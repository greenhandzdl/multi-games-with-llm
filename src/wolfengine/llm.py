"""One model call, with the retry policy the endpoint actually needs.

Sits between `transport` (bytes) and `actors` (a seat's turn). It deliberately knows
nothing about werewolf: no prompts, no legality, no event kinds. That is what lets
`phases.py` treat a 45-second timeout and a human taking two minutes as the same shape.

Three failure kinds are kept apart because each demands a different answer, and collapsing
them is how a dead endpoint ends up reported as a passive model:

  transient (a slow read, a rate limit)    -> back off and retry, at most twice
  context too long (400 + length)         -> do NOT resend; the caller must compact a layer
  anything else the endpoint answers with -> raise EndpointUnavailable, abort the game

Which statuses are "transient" and which are not is decided in one place,
`transport.TIME_DEPENDENT_STATUSES`, and decided by exclusion: a refusal this module cannot
answer by waiting or by compacting a layer is not a fact about the model either. This table
names the shape of each bucket rather than restating its members, because the last time a list
like this was written twice, a refusal passed one ruler and not the other.

The third is the one that matters for the data. Half of this session's calibration run
failed with `upstream error: do request failed`; had that been retried into a fallback
action, the fallback would have been recorded as model behaviour and M2/M5 would have
measured the outage.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from .config import Config
from .transport import LLMTransport, redact


class EndpointUnavailable(RuntimeError):
    """The endpoint is unhealthy. Surfaced to game.py as terminal="aborted", never as a
    behaviour finding, and never retried into a fallback action."""


class ContextTooLong(RuntimeError):
    """Raised *at* the caller so it can compact a layer and try once more. Resending the
    same bytes is a guaranteed second 400."""


@dataclass
class CallResult:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    latency_s: float = 0.0
    attempts: int = 1
    backoffs: tuple[float, ...] = ()
    error: str = ""
    raw_usage: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error

    def as_response_dict(self) -> dict[str, Any]:
        """What goes into `Event.response`. Whitelisted: latency and token counts, never
        headers, because this dict is written to disk."""
        return {
            "text": self.text,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "finish_reason": self.finish_reason,
            "latency_s": round(self.latency_s, 3),
            "attempts": self.attempts,
            "backoffs": list(self.backoffs),
            "usage": redact(self.raw_usage),
        }


class LatencyBook:
    """Per-phase median latency, which is the only sane source for a timeout.

    `Config.llm_timeout_median_mult` says a call may take 3x the phase median before it is
    declared dead. That cannot be a constant: the same prompt took 1.2s and 17.6s in two
    measured runs of this endpoint depending on what else was in flight. A fixed timeout
    either kills healthy calls under load or lets a hung one hold the whole game.
    """

    def __init__(self, window: int = 12) -> None:
        self._by_key: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window))

    def note(self, key: str, seconds: float) -> None:
        if seconds > 0:
            self._by_key[key].append(seconds)

    def median_prefix(self, prefix: str) -> float | None:
        """Median over every sample whose key starts with `prefix`.

        Keys are "seat:phase", so this answers "how long does seat 3 take when it is
        speaking" — which is what an LLM seat's timeout is defined against (plan §6), and
        which a flat key cannot answer without the caller rebuilding the book.
        """
        vals = [v for k, dq in self._by_key.items() if k.startswith(prefix) for v in dq]
        return median(vals) if vals else None

    def median(self, key: str) -> float | None:
        vals = list(self._by_key.get(key, ()))
        return median(vals) if vals else None

    def timeout_for(self, key: str, cfg: Config) -> float:
        """Floor first, then the median multiplier. Cold phases have no samples yet, and a
        cold-start timeout of `3 x nothing` would be zero."""
        m = self.median(key)
        if m is None:
            return cfg.llm_timeout_floor_s
        return max(cfg.llm_timeout_floor_s, m * cfg.llm_timeout_median_mult)


class LLM:
    """Retry/backoff wrapper. `max_retries_total` is a *game* budget and lives on the
    caller; this class only ever spends `max_retries_per_call` of it."""

    def __init__(self, transport: LLMTransport, cfg: Config, *,
                 latency: LatencyBook | None = None, max_retries_per_call: int = 2,
                 sleep=asyncio.sleep, backoff_base: float = 1.5) -> None:
        self.transport = transport
        self.cfg = cfg
        self.latency = latency or LatencyBook()
        self.max_retries_per_call = max_retries_per_call
        # Injectable so the retry test asserts on backoff *values* instead of waiting
        # through them. A scripted transport that answers `is_upstream_error=True` is what
        # makes this path reachable on demand; the real one only reaches it when the
        # endpoint actually faults.
        self._sleep = sleep
        self.backoff_base = backoff_base
        self.completion_tokens_spent = 0
        self.calls = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        phase_key: str,
    ) -> CallResult:
        timeout_s = self.latency.timeout_for(phase_key, self.cfg)
        backoffs: list[float] = []
        last_error = ""
        last_upstream = False
        for attempt in range(self.max_retries_per_call + 1):
            self.calls += 1
            res = await self.transport.chat(
                messages, model=self.cfg.model, temperature=temperature,
                max_tokens=max_tokens, timeout_s=timeout_s,
            )
            if res.ok:
                self.latency.note(phase_key, res.latency_s)
                self.completion_tokens_spent += int(res.completion_tokens or 0)
                return CallResult(
                    text=res.text, prompt_tokens=res.prompt_tokens,
                    completion_tokens=res.completion_tokens, finish_reason=res.finish_reason,
                    latency_s=res.latency_s, attempts=attempt + 1, backoffs=tuple(backoffs),
                    raw_usage=res.raw_usage,
                )
            if res.is_length_error:
                # Deliberately not retried here: the fix is fewer prompt bytes, which only
                # the assembler can produce. Retrying the same prompt is a 400 loop.
                raise ContextTooLong(redact(res.error)[:200])
            last_error = redact(res.error)[:200] or f"status {res.status}"
            last_upstream = res.is_upstream_error
            if attempt >= self.max_retries_per_call:
                break
            delay = self.backoff_base * (2 ** attempt)
            backoffs.append(delay)
            await self._sleep(delay)

        if last_upstream:
            raise EndpointUnavailable(last_error)
        return CallResult(text="", error=last_error, attempts=self.max_retries_per_call + 1,
                          backoffs=tuple(backoffs))


