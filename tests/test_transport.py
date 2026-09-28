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
import pytest

from wolfengine.config import Config
from wolfengine.transport import HttpTransport

# Not a key, and shaped so it could never be mistaken for one in a log or a secret scan.
PLACEHOLDER = "PLACEHOLDER-NOT-A-KEY"

ANSWER = {
    "choices": [{"message": {"content": '{"act":"pass"}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1234, "completion_tokens": 5, "total_tokens": 1239,
              "system_fingerprint": "fp-should-not-be-persisted"},
}


def _transport(handler, monkeypatch, *, cfg: Config | None = None) -> HttpTransport:
    monkeypatch.setenv(Config().api_key_env, PLACEHOLDER)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpTransport(cfg or Config(), client=client)


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


# ---------------------------------------------------------------- 谁先超时：建连 vs 席位 deadline
def _peek(handler, box: list):
    """把每一次请求真正交给 httpx 的那四个数抄下来。

    `request.extensions["timeout"]` 是唯一能离线看见这件事的地方：`httpx.MockTransport` 不建连，
    所以"连接被黑洞吞掉"没法在这儿演（那是 `test_loopback_endpoint.py` 那一层要真 socket 的理由）。
    但这一轮的缺陷不是"没演出来"，是**发出去的那个对象长什么样**——四个阶段共用一个数，于是
    黑洞里 connect 撞不上自己的上限，席位 deadline 先响。那是接线，接线看得见。
    """
    def h(request: httpx.Request):
        box.append(dict(request.extensions["timeout"]))
        return handler(request)
    return h


async def test_the_connect_phase_gets_its_own_bound_below_the_seat_deadline(monkeypatch):
    """一个数发下去，connect 和 read 就是同一个上限，于是永远是席位 deadline 先判这一回合。

    实测形状（2026-09-25T01:44:20Z，端点黑洞——SYN 无应答，不是 `ConnectError` 那种立刻被拒）：
    13 个回合**全部**记成 `timeout_after_45s`、`result.ok: true`、`fallback: 1`，一局打到第 2 天
    用了 585s、整局预计 ~37 分钟。`agent._ask` 的 deadline 取 `llm_timeout_floor_s`=45s
    （`actors.py:131`），`llm.py:146` 又把同一个 45 当作 `timeout_s` 交给 transport，
    `transport.py` 改前那一行用裸 float 传下去 = 四个阶段都是 45。于是本文件上面那条
    `test_an_unreachable_endpoint_is_the_endpoints_fault_not_the_models` 所承诺的分类根本到不了：
    `asyncio.wait_for` 与 httpx 的 connect 超时同时响，抢先进入 `except` 的是前者，
    `EndpointUnavailable`（`llm.py:178`）与 `aborted_endpoint`（`game.py:211`）在这形状下不可达。

    7.5 是故意挑的：它既不是 45 的因数也不是任何一个"忘了改"能碰巧写出来的数。钉的是**接线**
    （connect 单独取新字段），不是那个字段的取值——取值由下一条管。
    """
    seen: list[dict] = []
    t = _transport(_peek(lambda request: httpx.Response(200, json=ANSWER), seen), monkeypatch,
                   cfg=Config(connect_timeout_s=7.5))
    res = await _chat(t, timeout_s=45.0)
    assert res.ok
    assert seen == [{"connect": 7.5, "read": 45.0, "write": 45.0, "pool": 45.0}], (
        f"发出去的超时对象：{seen}——connect 若还是 45，黑洞里的端点就被记成一次慢回答")


def test_the_default_numbers_let_the_endpoints_verdict_win_the_race():
    """上一条钉"分了"，这一条钉"分得够开"：默认配置下端点的判决必须**赶在**席位 deadline 之前。

    `llm.py` 的重试预算是 `max_retries_per_call + 1` 次尝试加两段退避；每一次尝试现在最多花
    `connect_timeout_s`（黑洞形状），所以端点判决的最坏时刻是 `3×connect + (1.5 + 3)`。
    它必须小于冷启动的席位 deadline `llm_timeout_floor_s`，否则这一轮修的东西只是把 race 挪了个
    位置。这两个数分别从 `Config` 和 `LLM.__init__` 的签名上读，因为**两侧都会漂移**：
    退避常数是 `llm.py` 的默认参数，不在 `Config` 里，写死在断言里就是一条会腐烂的散文。
    （现值实测：3×5.0 + 4.5 = 19.5 < 45。）
    """
    import inspect

    from wolfengine.llm import LLM

    cfg = Config()
    params = inspect.signature(LLM.__init__).parameters
    attempts = params["max_retries_per_call"].default + 1
    base = params["backoff_base"].default
    backoffs = sum(base * 2 ** a for a in range(attempts - 1))
    worst = attempts * cfg.connect_timeout_s + backoffs
    assert worst < cfg.llm_timeout_floor_s, (
        f"{attempts} 次建连 × {cfg.connect_timeout_s}s + 退避 {backoffs}s = {worst}s，"
        f"席位 deadline 只有 {cfg.llm_timeout_floor_s}s：这局会被记成慢回答而不是端点故障")


async def test_the_read_budget_is_unchanged_by_the_connect_bound(monkeypatch):
    """反方向：一个真答得很慢的端点还是**不能**被 5 秒掐掉。

    把 connect 收紧的同时把 read 也收紧，就等于把"模型在思考"重新归类成"这一回合超时"——那是
    上面 `test_a_stalled_read_stays_one_turns_problem` 守着的另一半。这一条钉的是新字段**只**动
    connect 那一格。
    """
    seen: list[dict] = []
    t = _transport(_peek(lambda request: httpx.Response(200, json=ANSWER), seen), monkeypatch,
                   cfg=Config(connect_timeout_s=7.5))
    await _chat(t, timeout_s=31.0)
    assert seen[0]["read"] == 31.0 and seen[0]["connect"] == 7.5


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


async def test_the_scrubber_follows_whatever_env_var_the_config_names(monkeypatch):
    """`require_key()` reads the configured name; the value scrubber must read the same one.

    `transport.py` used to hard-code a pair of names, so a deployment that moves the key to a
    third name still sent it as `Bearer`, still got the endpoint's echo back, and scrubbed
    nothing: the literal was only ever looked up for the two names the list already knew. The
    alternate sentinel below is deliberately *not* the value the fixture puts under the default
    name, and does not contain it as a substring, so this cannot go green by accident.
    """
    alt = "OTHERSENTINELNOTKEY"
    monkeypatch.setenv("WOLF_OTHER_KEY_ENV", alt)

    def echo(request: httpx.Request):
        return httpx.Response(400, text=f"bad Authorization: {request.headers['Authorization']}")

    t = _transport(echo, monkeypatch, cfg=Config(api_key_env="WOLF_OTHER_KEY_ENV"))
    res = await _chat(t)
    assert (res.ok, res.status) == (False, 400)
    assert alt not in res.error, f"擦值没跟着 api_key_env，键的明文进了日志：{res.error!r}"
    assert "<elided>" in res.error


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.ConnectError],
                         ids=["slow-read", "never-connected"])
async def test_a_renamed_key_is_erased_by_whichever_except_arm_echoes_it(monkeypatch, exc):
    """两条 `except` 分支各自有一份擦值调用，所以各自要有证人。

    上面那条走的是"有响应、但响应是坏的"那一条分支；这里两条分别把异常丢进**超时**分支和
    **什么都没回来**分支——两处的 `error=` 是各写一遍的，把联结只补在其中一处，另一处照样在
    出厂名下擦值、对改过名字的部署静默失效。
    """
    alt = "OTHERSENTINELNOTKEY"
    monkeypatch.setenv("WOLF_OTHER_KEY_ENV", alt)

    def boom(request: httpx.Request):
        raise exc(f"while sending {request.headers['Authorization']}")

    t = _transport(boom, monkeypatch, cfg=Config(api_key_env="WOLF_OTHER_KEY_ENV"))
    res = await _chat(t)
    assert not res.ok
    assert alt not in res.error, f"这一分支没跟着 api_key_env：{res.error!r}"
    assert "<elided>" in res.error


async def test_a_key_straddling_the_body_cut_leaves_no_prefix_either(monkeypatch):
    """The cut used to run *before* the scrubber, so a straddling value left its head behind.

    `r.text[:300]` is applied to the raw body; a value that starts before offset 300 and ends
    after it no longer equals the string `replace` looks for, and the 10 characters that made it
    past the cut stayed in `error`. That is why the assertion is on a *prefix* of the value — the
    whole value is not in there anymore, which is exactly how the old code passed
    `test_the_key_never_survives_into_the_error_text`'s shape while still leaking half of it.
    """
    body = "x" * 290 + PLACEHOLDER + "tail"

    t = _transport(lambda request: httpx.Response(502, text=body), monkeypatch)
    res = await _chat(t)
    assert (res.ok, res.status) == (False, 502)
    assert "x" in res.error, "正控制：正文还是要记的，不许靠清空 error 变绿"
    assert PLACEHOLDER[:8] not in res.error, f"截断在擦除之前，键的前缀进了日志：{res.error!r}"


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
