"""The only tests that drive `HttpTransport` — i.e. the code that talks to the endpoint.

Until this file, no test had ever constructed a request. `--mock` and `--dry-run` games go
through `MockActor`, which never touches a transport, and the scripted `MockTransport` that
used to sit at the bottom of `transport.py` was never instantiated by anything either — `#85`
deleted it, because a second offline twin that nobody drives is how a chain stays untested
while looking covered. So the whole real half of the chain — request building,
response parsing, and the error classification that decides whether a game *continues* or
*aborts* — was code the first live call would run for the first time.

These tests keep `httpx.MockTransport` in place of a socket: the bytes come from a fixture
instead of the wire, but every line of `HttpTransport.chat` executes. The distinction that
matters most here is not "did it parse" but *whose fault is it*:

  no response ever arrived   -> the endpoint is unhealthy -> `aborted_endpoint`
  a response, but a bad one  -> this turn's problem        -> retry / fallback / skip

Getting that backwards is how a server outage ends up in the corpus as model passivity.
"""

from __future__ import annotations

import httpx

from wolfengine.config import Config
from wolfengine.transport import HttpTransport

# Not a key, and shaped so it could never be mistaken for one in a log or a secret scan.
PLACEHOLDER = "PLACEHOLDER-NOT-A-KEY"

ANSWER = {
    "choices": [{"message": {"content": '{"act":"pass"}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1234, "completion_tokens": 5, "total_tokens": 1239,
              "system_fingerprint": "fp-should-not-be-persisted"},
}


def _transport(handler, monkeypatch) -> HttpTransport:
    monkeypatch.setenv(Config().api_key_env, PLACEHOLDER)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpTransport(Config(), client=client)


async def _chat(t: HttpTransport, **kw):
    return await t.chat([{"role": "user", "content": "发言"}], model="m", temperature=0.9,
                        max_tokens=140, timeout_s=kw.pop("timeout_s", 45.0), **kw)


# --------------------------------------------------------------------------- the happy path
async def test_a_successful_answer_is_parsed_into_the_fields_metrics_reads(monkeypatch):
    t = _transport(lambda request: httpx.Response(200, json=ANSWER), monkeypatch)
    res = await _chat(t)
    assert res.ok and res.status == 200
    assert (res.text, res.prompt_tokens, res.completion_tokens, res.finish_reason) == (
        '{"act":"pass"}', 1234, 5, "stop")
    # The whitelist is the reason `Event.response` can be written to disk at all: a field
    # added upstream (fingerprints, server logs) must not reach the corpus by accident.
    assert set(res.raw_usage) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    assert res.error == ""


async def test_the_outgoing_request_is_the_whole_contract_and_nothing_else(monkeypatch):
    seen: list[httpx.Request] = []
    t = _transport(lambda r: seen.append(r) or httpx.Response(200, json=ANSWER), monkeypatch)
    await _chat(t)
    req = seen[0]
    assert req.url.path.endswith("/chat/completions")
    import json as _json
    body = _json.loads(req.content)
    # `seed`, `stop` and `response_format` are deliberately absent: the endpoint ignores or
    # rejects them (plan §1), so sending them would be a claim about determinism we cannot keep.
    assert set(body) == {"model", "messages", "temperature", "max_tokens"}
    assert req.headers["authorization"] == f"Bearer {PLACEHOLDER}"


# --------------------------------------------------------------------- whose fault is it?
async def test_an_unreachable_endpoint_is_the_endpoints_fault_not_the_models(monkeypatch):
    """`nc`-level failure is the *most likely* first live experience, so this is not academic.

    If a connection failure were reported as an ordinary bad answer, `LLM.complete` would
    hand back `CallResult(error=...)`, `agent.take_turn` would treat it as unrepairable, and
    the engine would quietly write a legal default action for all 9 seats × every phase — a
    complete game with `terminal="good_win"`/`"wolf_win"` whose every decision came from
    `legality.default_action`. M2/M5 would then measure the outage and call it passivity.
    """
    def boom(request: httpx.Request):
        raise httpx.ConnectError("connection refused")

    t = _transport(boom, monkeypatch)
    res = await _chat(t)
    assert not res.ok
    assert res.is_upstream_error, "no response arrived; only the endpoint can be at fault"
    assert not res.is_length_error


async def test_a_stalled_read_stays_one_turns_problem(monkeypatch):
    """The other side of the same line: the model was reached and is thinking too long.

    That must not abort a game — `agent._ask` has a per-seat deadline precisely so one slow
    seat gets skipped instead of holding the table. Over-classifying here is as expensive as
    under-classifying, so the two cases are pinned by separate tests.
    """
    def slow(request: httpx.Request):
        raise httpx.ReadTimeout("timed out")

    t = _transport(slow, monkeypatch)
    res = await _chat(t)
    assert not res.ok and not res.is_upstream_error
    assert "ReadTimeout" in res.error


async def test_a_5xx_and_a_gateway_html_page_are_both_unhealthy(monkeypatch):
    for response in (httpx.Response(502, json={"error": {"message": "upstream error"}}),
                     httpx.Response(500, text="<html><body>502 Bad Gateway</body></html>")):
        t = _transport(lambda request, r=response: r, monkeypatch)
        res = await _chat(t)
        assert not res.ok and res.is_upstream_error, response.status_code


async def test_a_400_about_length_is_an_order_to_compress_not_to_resend(monkeypatch):
    t = _transport(lambda request: httpx.Response(
        400, json={"error": {"message": "This model's maximum context length is 20000 tokens"}}),
        monkeypatch)
    res = await _chat(t)
    assert res.is_length_error and not res.is_upstream_error


async def test_length_wording_echoed_by_a_non_400_is_not_an_order_to_compress(monkeypatch):
    """The 400 in that predicate is load-bearing: a gateway that forwards the upstream's text
    under its own 502 would otherwise send the assembler into a shrink loop against a box that
    is simply down. Compaction is the remedy for a prompt that is too long, not for a server
    that is not there.
    """
    t = _transport(lambda request: httpx.Response(
        502, json={"error": {"message": "upstream said: maximum context length exceeded"}}),
        monkeypatch)
    res = await _chat(t)
    assert not res.is_length_error and res.is_upstream_error


async def test_a_refusal_that_would_come_back_identically_is_not_this_turns_problem(monkeypatch):
    """Everything a non-200 that is neither a length order nor a *time-dependent* status.

    A refused credential or a wrong path repeats identically for all nine seats in every phase,
    so a game that continues past one is a transcript of `default_action` — 401 is not a
    hypothetical here: the last time this endpoint answered at all it answered 401
    (2026-09-21T18:21:44Z, an unauthenticated `GET /v1/models`). `422` is in the loop because the
    rule is default-deny: a status nobody has seen yet must land on the "not model behaviour"
    side, which is the half that an allowlist would have gotten wrong.
    """
    for code in (401, 403, 404, 405, 410, 422):
        t = _transport(lambda request, c=code: httpx.Response(
            c, json={"error": {"message": "Invalid API key"}}), monkeypatch)
        res = await _chat(t)
        assert not res.ok and res.is_upstream_error, code
        assert not res.is_length_error, code


async def test_a_rate_limit_and_a_deadline_stay_this_turns_problem(monkeypatch):
    """The other side of the same line, pinned so widening cannot swallow it: 408 and 429 say
    different things at different moments, so waiting is the remedy and one skipped seat is not a
    lie about the endpoint. What a persistent limit costs belongs to the batch-level
    `fallback_rate` gate, not to this predicate."""
    for code in (408, 429):
        t = _transport(lambda request, c=code: httpx.Response(
            c, json={"error": {"message": "Too Many Requests"}}), monkeypatch)
        res = await _chat(t)
        assert not res.ok and not res.is_upstream_error, code
        assert res.status == code


async def test_a_200_that_is_not_json_or_has_no_choices_fails_loudly(monkeypatch):
    for make in (lambda: httpx.Response(200, text="not json at all"),
                 lambda: httpx.Response(200, json={"usage": {}})):
        t = _transport(lambda request: make(), monkeypatch)
        res = await _chat(t)
        assert not res.ok, "an empty answer must not be parsed as a seat that chose to pass"


# --------------------------------------------------------------------------------- the key
async def test_the_key_never_survives_into_the_error_text(monkeypatch):
    """httpx embeds the request — headers included — in some exception messages.

    The value here is what the test is really about: the assertion holds only while the
    scrubber reads the environment at the moment of the failure, which is the same mechanism
    `Config.require_key()` uses.
    """
    def boom(request: httpx.Request):
        raise httpx.ConnectError(f"could not connect with Authorization: Bearer {PLACEHOLDER}")

    t = _transport(boom, monkeypatch)
    res = await _chat(t)
    assert PLACEHOLDER not in res.error
    # The scheme name may stay (it is not the secret); what must not survive is the material
    # after it. This is the assertion that fails if the scrubber stops reading the environment.
    assert "<elided>" in res.error and f"Bearer {PLACEHOLDER}" not in res.error


# ----------------------------------------------------------------------- the cache evidence
CACHED = {**ANSWER, "usage": {**ANSWER["usage"],
                              "prompt_tokens_details": {"cached_tokens": 1152}}}


async def test_the_nested_cached_token_count_survives_the_usage_whitelist(monkeypatch):
    """plan §5 的整段几何存在的理由是让端点复用前缀，而"复用没复用"在日志里读不出来。

    直接读数只有一个地方有：`usage.prompt_tokens_details.cached_tokens`。白名单以前只留三个平铺
    计数，这一支整块被丢掉，于是每一局、每一批都不知道前缀有没有落地——唯一知道过一次的地方是
    体检脚本，而它那次的答案还被过宽的 redact 遮成了 `<elided>`（`docs/calibration.md` 第 0 节）。
    摊平成 `cached_tokens` 是要让读数不必再懂 OpenAI 的嵌套形状；放行这一支不等于放行整支，
    `system_fingerprint` 那类字段仍然进不来。
    """
    t = _transport(lambda request: httpx.Response(200, json=CACHED), monkeypatch)
    res = await _chat(t)
    assert res.raw_usage["cached_tokens"] == 1152
    assert set(res.raw_usage) == {"prompt_tokens", "completion_tokens", "total_tokens",
                                 "cached_tokens"}, res.raw_usage


async def test_zero_and_silence_are_two_different_answers_in_the_usage_block(monkeypatch):
    """`cached_tokens: 0` 是端点说"没复用"，字段缺席是端点没说——日志必须分得开这两句话。

    把缺席写成 0 会让下一份报告把"我们没量"报成"这批桌前缀命中率为零"，而 §5 的经济性恰好是
    靠这个比值算的。所以这里两档各自造一次：显式 0 落 0，没有那一支就整个键都不写。
    """
    zero = {**ANSWER, "usage": {**ANSWER["usage"], "prompt_tokens_details": {"cached_tokens": 0}}}
    got_zero = await _chat(_transport(lambda r: httpx.Response(200, json=zero), monkeypatch))
    assert got_zero.raw_usage["cached_tokens"] == 0

    got_plain = await _chat(_transport(lambda r: httpx.Response(200, json=ANSWER), monkeypatch))
    assert "cached_tokens" not in got_plain.raw_usage, "缺席被写成了 0"

    junk = {**ANSWER, "usage": {**ANSWER["usage"], "prompt_tokens_details": "unavailable"}}
    got_junk = await _chat(_transport(lambda r: httpx.Response(200, json=junk), monkeypatch))
    assert "cached_tokens" not in got_junk.raw_usage, "一整个坏掉的分支不算一次读数"
