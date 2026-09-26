"""Transport: the only module allowed to know the endpoint exists.

Everything above it takes a `LLMTransport`, which is what makes the whole pure core
testable offline and what makes `--mock` and `--dry-run` first-class rather than bolt-ons.

The key is read from the environment at call time via `Config.require_key()`; there is no
code path here that could serialise it, and `redact()` covers the one leak that does
happen in practice — an exception whose message embeds the request.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from .config import Config

REDACT_PATTERNS = ("authorization", "api_key", "apikey", "x-api-key", "secret", "password", "credential")

# The statuses whose answer depends on *when* you ask, so waiting is a remedy and one skipped
# seat says nothing about the endpoint. This list is short on purpose and the rule is
# default-deny: anything else the endpoint answers with — a refused key, a wrong path, a 422 we
# have never seen — repeats identically for all nine seats in every phase, so it is a fact about
# the credentials, the URL or our own request body, and must never be retried into a fallback
# action that gets read back as the model declining to act.
TIME_DEPENDENT_STATUSES = (408, 429)


def redact(obj: Any) -> Any:
    """Replace credential-shaped values, and the literal key inside any string.

    Applied to exception text as well as payloads: `httpx` will happily echo a request in
    a `RemoteProtocolError` message, and that text is what ends up in a log line.
    """
    if isinstance(obj, dict):
        return {
            k: ("<elided>" if any(t in str(k).lower() for t in REDACT_PATTERNS) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _scrub_string(obj)
    return obj


def _scrub_string(s: str, key_env: str = Config.api_key_env) -> str:
    """Erase the value of the variable the key is actually read from.

    The name comes from `Config`, not from a list of names written here: `require_key()` will
    read whatever `api_key_env` says, so a deployment that renames the variable used to send a
    key this scrubber never looked up — while the endpoint's echo of it went straight into
    `error`, and from there into the log. The default is the factory name because `redact()` has
    no config in hand; every call site that does have one passes it.
    """
    value = os.environ.get(key_env)
    if value and value in s:
        s = s.replace(value, "<elided>")
    return s


USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def usage_from(u: dict[str, Any]) -> dict[str, Any]:
    """The usage block the log is allowed to carry: three flat counts plus one flattened nested one.

    `cached_tokens` lives under `prompt_tokens_details` in the OpenAI shape and is the only direct
    evidence plan §5's whole region geometry exists to produce — whether the endpoint reused the
    prefix. Absent or a broken branch stays *absent*: writing 0 there would turn "this server never
    told us" into "the prefix was never reused", which is a claim §5's economics would then be
    computed from.
    """
    out = {k: v for k, v in u.items() if k in USAGE_KEYS}
    details = u.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        out["cached_tokens"] = details["cached_tokens"]
    return out


@dataclass
class TransportResult:
    ok: bool
    text: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    latency_s: float = 0.0
    status: int | None = None
    error: str = ""
    # 400 whose body mentions length/context: the one error the caller should answer by
    # compacting a layer rather than resending the same bytes forever.
    is_length_error: bool = False
    # Not a fact about the model: nothing came back, or the endpoint said it is unhealthy, or it
    # refused *us* (a key, a path, a body it will not parse) in a way the next seat would repeat.
    # The alternative is a full transcript where every one of the 9 seats was actually played by
    # `legality.default_action`, so this flag is the difference between data and an outage.
    is_upstream_error: bool = False
    raw_usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMTransport(Protocol):
    async def chat(self, messages: list[dict[str, str]], *, model: str, temperature: float,
                   max_tokens: int, timeout_s: float) -> TransportResult: ...


class HttpTransport:
    def __init__(self, cfg: Config, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self._own_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(180.0))

    async def aclose(self) -> None:
        if self._own_client:
            await self.client.aclose()

    async def chat(self, messages, *, model, temperature, max_tokens, timeout_s) -> TransportResult:
        key = self.cfg.require_key()  # raises ConfigError before any network use
        body = {"model": model, "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens}
        t0 = time.perf_counter()
        try:
            r = await self.client.post(f"{self.cfg.base_url}/chat/completions", json=body,
                                       headers={"Authorization": f"Bearer {key}",
                                                "Content-Type": "application/json"},
                                       # Not a bare float: that would set connect == read == the
                                       # seat deadline, and a host that swallows SYNs would then be
                                       # classified by `agent._ask` as one slow turn, forever.
                                       timeout=httpx.Timeout(timeout_s,
                                                            connect=self.cfg.connect_timeout_s))
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            # Reached the model, it is answering too slowly. That is one turn's problem, and
            # the remedy already exists upstream: `agent._ask`'s per-seat deadline skips the
            # seat. Aborting a whole game over one slow read would discard 8 good seats.
            return TransportResult(ok=False, latency_s=time.perf_counter() - t0,
                                   error=_scrub_string(f"{type(e).__name__}: {e}", self.cfg.api_key_env)[:200])
        except Exception as e:  # noqa: BLE001
            # ConnectError / ConnectTimeout / any NetworkError / ProtocolError: nothing ever
            # came back, so this cannot be a fact about the model's behaviour. `llm.py` turns
            # the flag into `EndpointUnavailable` once its retries are spent and `game.py`
            # ends the game as `aborted_endpoint` — the alternative is a full transcript where
            # every one of the 9 seats was actually played by `legality.default_action`.
            return TransportResult(ok=False, latency_s=time.perf_counter() - t0,
                                   error=_scrub_string(f"{type(e).__name__}: {e}", self.cfg.api_key_env)[:200],
                                   is_upstream_error=True)
        dt = time.perf_counter() - t0
        if r.status_code != 200:
            text = r.text[:300]
            low = text.lower()
            length_order = r.status_code == 400 and any(
                w in low for w in ("length", "context", "maximum", "token"))
            return TransportResult(
                ok=False, latency_s=dt, status=r.status_code,
                # Not `text`: the erase has to run on the uncut body. A value straddling the
                # 300-char cut no longer matches the whole-string `replace`, and the head of it
                # would reach the log looking like nothing was leaked.
                error=_scrub_string(r.text, self.cfg.api_key_env)[:300],
                is_length_error=length_order,
                # Nothing else is left over: a gateway's HTML page or an "upstream error" body
                # arrives with a status that is neither of these two, so it lands here by default.
                # The body-shaped checks that used to be the whole rule are gone — they now only
                # matter on a 200, where the parse below cannot read them as an answer.
                is_upstream_error=not (length_order or r.status_code in TIME_DEPENDENT_STATUSES),
            )
        try:
            o = r.json()
        except Exception as e:  # noqa: BLE001
            return TransportResult(ok=False, latency_s=dt, status=r.status_code,
                                   error=_scrub_string(f"unparseable body: {e}",
                                                       self.cfg.api_key_env)[:200],
                                   is_upstream_error=True)
        u = o.get("usage") or {}
        try:
            choice = o["choices"][0]
        except (KeyError, IndexError):
            return TransportResult(ok=False, latency_s=dt, status=200, error="no choices")
        return TransportResult(
            ok=True,
            text=choice.get("message", {}).get("content") or "",
            prompt_tokens=u.get("prompt_tokens"),
            completion_tokens=u.get("completion_tokens"),
            finish_reason=choice.get("finish_reason"),
            latency_s=dt,
            status=200,
            raw_usage=usage_from(u),
        )
