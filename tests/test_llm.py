"""`llm.py`: the retry policy and the timeout book, tested for the first time.

The comment on the injected sleep at llm.py:129 already advertised a "retry test" that would assert on
backoff *values* instead of waiting through them. No such test existed, and neither did any
test for the distinction this module exists to make — between a call that should be retried,
a prompt that must be shortened, and an endpoint that should end the game.

`sleep` is injected, so the whole file runs in milliseconds; a fake transport records how
many times it was asked.
"""

from __future__ import annotations

import pytest

from wolfengine.config import Config
from wolfengine.llm import LLM, LatencyBook, ContextTooLong, EndpointUnavailable
from wolfengine.transport import TransportResult


class ScriptedTransport:
    """Stands where HttpTransport stands, answering from a list of prepared results."""

    def __init__(self, *results: TransportResult) -> None:
        self.results = list(results)
        self.requests: list[dict] = []

    async def chat(self, messages, *, model, temperature, max_tokens, timeout_s):
        self.requests.append({"messages": messages, "model": model, "temperature": temperature,
                              "max_tokens": max_tokens, "timeout_s": timeout_s})
        # The last entry repeats: a script of one failure means "fails forever", which is the
        # shape most of these tests are about.
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def ok(text: str = '{"act":"pass"}', **kw) -> TransportResult:
    return TransportResult(ok=True, text=text, prompt_tokens=900, completion_tokens=12,
                           finish_reason="stop", latency_s=kw.pop("latency_s", 1.0), **kw)


def bad(**kw) -> TransportResult:
    return TransportResult(ok=False, error=kw.pop("error", "boom"), **kw)


async def _sleep_recorder():
    seen: list[float] = []

    async def sleep(delay):
        seen.append(delay)

    return seen, sleep


# --------------------------------------------------------------------- the three failure kinds
async def test_a_context_too_long_answer_is_never_resent():
    """Plan §6: "400 绝不无脑重发（死循环）". The fix is fewer bytes, which this module cannot make."""
    t = ScriptedTransport(bad(status=400, is_length_error=True, error="maximum context length"))
    llm = LLM(t, Config())
    with pytest.raises(ContextTooLong):
        await llm.complete([{"role": "user", "content": "x"}], max_tokens=140, temperature=0.9,
                           phase_key="1:day_speech")
    assert len(t.requests) == 1, "resending the same bytes is a guaranteed second 400"


async def test_a_persistent_outage_raises_rather_than_becoming_a_fallback():
    t = ScriptedTransport(bad(status=502, is_upstream_error=True, error="upstream error"))
    seen, sleep = await _sleep_recorder()
    llm = LLM(t, Config(), sleep=sleep)
    with pytest.raises(EndpointUnavailable):
        await llm.complete([{"role": "user", "content": "x"}], max_tokens=140, temperature=0.9,
                           phase_key="1:day_speech")
    assert llm.calls == 3, "it does get its two retries first — a 502 is often transient"
    assert seen == [1.5, 3.0], "exponential, and asserted as values so no test ever sleeps"


async def test_a_transient_failure_that_recovers_is_recorded_as_one_call():
    """The counter that matters downstream: `attempts` is what separates "the model said this
    on the first try" from "this survived two retries" in the M3a distribution."""
    t = ScriptedTransport(bad(status=500, is_upstream_error=True), ok())
    _, sleep = await _sleep_recorder()
    llm = LLM(t, Config(), sleep=sleep)
    res = await llm.complete([{"role": "user", "content": "x"}], max_tokens=140, temperature=0.9,
                             phase_key="1:day_speech")
    assert (res.ok, res.attempts, res.text) == (True, 2, '{"act":"pass"}')
    assert llm.completion_tokens_spent == 12, "the success side of the same counter"


async def test_a_broken_answer_carries_its_reason_into_the_event():
    t = ScriptedTransport(bad(status=400, error="invalid_request_error: unsupported n=2"))
    _, sleep = await _sleep_recorder()
    llm = LLM(t, Config(), sleep=sleep)
    res = await llm.complete([{"role": "user", "content": "x"}], max_tokens=140, temperature=0.9,
                             phase_key="1:day_speech")
    assert not res.ok
    assert "n=2" in res.error
    # Empty text with no error would be indistinguishable from a seat that chose to say
    # nothing, which is exactly the confusion this module's three buckets prevent.
    assert res.text == ""


# ------------------------------------------------------------------------- what gets sent
async def test_every_call_carries_the_budget_and_the_temperature_it_was_given():
    t = ScriptedTransport(ok())
    llm = LLM(t, Config())
    await llm.complete([{"role": "user", "content": "x"}], max_tokens=140, temperature=1.05,
                       phase_key="4:day_speech")
    req = t.requests[0]
    assert (req["max_tokens"], req["temperature"]) == (140, 1.05)
    assert req["model"] == Config().model


async def test_a_failed_call_consumes_no_completion_tokens():
    """The game's token budget is spent from this counter; counting a failure's phantom
    tokens would make `--max-game-tokens` fire early and blame the model."""
    t = ScriptedTransport(bad(status=500, is_upstream_error=True))
    _, sleep = await _sleep_recorder()
    llm = LLM(t, Config(), sleep=sleep)
    with pytest.raises(EndpointUnavailable):
        await llm.complete([{"role": "user", "content": "x"}], max_tokens=140, temperature=0.9,
                           phase_key="1:night_wolf")
    assert llm.completion_tokens_spent == 0


# ------------------------------------------------------------------------- the timeout book
def test_a_cold_phase_gets_the_floor_not_three_times_nothing():
    cfg = Config()
    assert LatencyBook().timeout_for("1:day_speech", cfg) == cfg.llm_timeout_floor_s


def test_the_timeout_is_three_times_the_median_but_never_below_the_floor():
    cfg = Config(llm_timeout_floor_s=45.0, llm_timeout_median_mult=3.0)
    book = LatencyBook()
    for s in (1.0, 2.0, 100.0):  # median, not mean: one 100s outlier must not widen the window
        book.note("1:day_speech", s)
    assert book.median("1:day_speech") == 2.0
    assert book.timeout_for("1:day_speech", cfg) == 45.0  # 3x2=6 < floor
    for s in (20.0, 30.0, 40.0):
        book.note("2:day_speech", s)
    assert book.timeout_for("2:day_speech", cfg) == 90.0


def test_a_zero_second_sample_is_not_a_measurement():
    """A broken timer would otherwise make every future call time out at the floor times
    nothing, i.e. kill healthy calls — the failure this whole class exists to avoid."""
    book = LatencyBook()
    book.note("1:day_speech", 0.0)
    assert book.median("1:day_speech") is None


def test_the_book_answers_for_one_seat_without_mixing_seats():
    book = LatencyBook()
    book.note("3:day_speech", 5.0)
    book.note("7:day_speech", 80.0)
    assert book.median_prefix("3:day_speech") == 5.0
    assert book.median_prefix("7:day_speech") == 80.0
    assert book.median_prefix("9:day_speech") is None


# --------------------------------------------------- the deployed key name reaching what hits disk
ALT_ENV = "WOLF_OTHER_KEY_ENV"
ALT = "OTHERSENTINELNOTKEY"


async def test_the_usage_dict_written_to_disk_erases_a_renamed_deployers_value(monkeypatch):
    """`as_response_dict()` 落盘那一格今天只按出厂名擦值，而它的调用方手上就有 config。

    `usage_from()` 按白名单放行**值**（类型写的是 `dict[str, Any]`），端点把回显的密钥放进
    `prompt_tokens` 那一格时，键名不在 `REDACT_PATTERNS` 里，只有值那一半能拦住它——而拦住它
    需要知道这次部署把键放在哪个名字下。这里故意让 `total_tokens` 保持原样：正控制是"计数还是要
    记进日志"，清空 usage 也能让明文消失，那种绿不算证人。
    """
    monkeypatch.setenv(ALT_ENV, ALT)
    t = ScriptedTransport(ok(raw_usage={"prompt_tokens": f"Bearer {ALT}", "total_tokens": 7}))
    llm = LLM(t, Config(api_key_env=ALT_ENV))

    call = await llm.complete([{"role": "user", "content": "x"}], max_tokens=140,
                              temperature=0.9, phase_key="3:discuss")
    usage = call.as_response_dict(ALT_ENV)["usage"]
    assert usage["total_tokens"] == 7, "正控制：白名单里的计数不许靠清空整格变绿"
    assert ALT not in repr(usage), f"改名部署的明文进了落盘那一格：{usage!r}"
    assert "<elided>" in usage["prompt_tokens"]


@pytest.mark.parametrize("length_error", [False, True], ids=["retry-then-error", "raise-too-long"])
async def test_both_error_arms_erase_a_renamed_deployers_value(monkeypatch, length_error):
    """`complete()` 里两份 `redact(res.error)` 各擦一遍，改名部署下各自会静默漏。

    两形的差别不是"擦得干不干净"而是**谁拿到那句**：一形进 `CallResult.error`（落进事件的
    `response`），另一形进 `ContextTooLong` 的异常文本（沿调用栈往上被打印）。把联结只补在其中
    一处，另一处照旧只认出厂名——所以两形各自要一条，共用一个 parametrize 是因为断言完全同形。
    """
    monkeypatch.setenv(ALT_ENV, ALT)
    kw = {"status": 400, "is_length_error": True} if length_error else {"status": 502}
    t = ScriptedTransport(bad(error=f"upstream saw {ALT}", **kw))
    llm = LLM(t, Config(api_key_env=ALT_ENV))

    if length_error:
        with pytest.raises(ContextTooLong) as got:
            await llm.complete([{"role": "user", "content": "x"}], max_tokens=140,
                               temperature=0.9, phase_key="3:discuss")
        seen = str(got.value)
    else:
        call = await llm.complete([{"role": "user", "content": "x"}], max_tokens=140,
                                  temperature=0.9, phase_key="3:discuss")
        seen = call.error

    assert seen, "正控制：那句原因还是要记的，不许靠擦成空串变绿"
    assert ALT not in seen, f"这一支的擦值没跟着 api_key_env：{seen!r}"
    assert "<elided>" in seen
