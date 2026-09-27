"""前缀缓存到底有没有被复用，现在是每一批都有的读数，不是 2026-09-20 那一次体检的回忆。

plan §5 整段区域几何（A/B/C 三段、字节稳定、同波共享前缀）存在的理由就是让端点省掉 prefill。
而这件事的证据只有一个地方有：`usage.prompt_tokens_details.cached_tokens`。在 #70 之前，
`transport.py` 的白名单只留三个平铺计数，这一支整块丢掉，于是链条上没有任何一处读得出来——
唯一知道过一次的地方是体检脚本，而它那次的结论又被过宽的 redact 遮成了 `<elided>`
（`docs/calibration.md` 第 0 节点名了那 7 格）。落盘的 `Event.response["usage"]` 因此在
#70 之前只有一个读者：`tests/test_no_secrets.py`，它证明的是"密钥没漏进去"，不是"缓存命中了"。

这一页全部离线可验：判的是"0 与缺席"的算术与分母的配对，端点此刻开着还是关着都不影响。
"""
from __future__ import annotations

from wolfengine import metrics
from wolfengine.events import Event, Kind


_MISSING = object()


def _call(seq: int, phase: str = "day_speech", *, cached: object = _MISSING,
          prompt: object = None, latency: float = 3.0) -> Event:
    usage: dict[str, object] = {}
    if cached is not _MISSING:
        usage["cached_tokens"] = cached
    if prompt is not None:
        usage["prompt_tokens"] = prompt
    response: dict[str, object] = {"latency_s": latency, "completion_tokens": 40}
    if usage:
        response["usage"] = usage
    if prompt is not None:
        response["prompt_tokens"] = prompt
    return Event(seq=seq, kind=Kind.SPEECH, day=1, phase=phase, visibility="all", actor=seq,
                 payload={"seat": seq, "text": "我指控下一位。", "act": "accuse",
                          "meta": {"rung": 0, "fallback": 0}},
                 request={"total_tokens_est": 900}, response=response)


def test_the_reuse_ratio_divides_cached_by_the_same_calls_prompt_tokens():
    """分子分母必须来自同一批调用：报了 cached 的那些才算分母，没报的一格都不许进。

    和 `m7_cost_profile` 的 `asked`/`used` 是同一条纪律（`metrics.py:1328` 那一对 `append` 写进的是同
    一件事）：把没量过的那 900 tok 塞进分母，命中率就凭空掉一截，而那一截会被读成"前缀没立住"。
    """
    ev = [_call(1, cached=1000, prompt=2000), _call(2, cached=500, prompt=1000),
          _call(3, prompt=900)]
    out = metrics.prefix_cache_reuse(ev)
    assert out["calls"] == 3 and out["reported"] == 2 and out["silent"] == 1, out
    assert out["cached_tokens"] == 1500 and out["prompt_tokens"] == 3000, out
    assert out["reuse_ratio"] == 0.5, out


def test_never_measured_and_measured_zero_are_not_the_same_answer():
    """`reuse_ratio: null` 说的是"这批端点一个字都没说"，`0.0` 说的是"说了，一次都没复用"。

    把前者写成后者，§5 的经济性就拿一个从不存在过的读数去算折扣——这一族的判据在本仓库已经是
    第四次用了（`assignment_compliance` 的 `recorded`、`compactions` 的两格、`region_budget_check`
    的 caps），每一格都是单独重判的，因为"继承别处结论"正是这些格子逐个变脏的方式。
    """
    silent = metrics.prefix_cache_reuse([_call(1, prompt=2000), _call(2, prompt=1800)])
    assert silent["reported"] == 0 and silent["silent"] == 2, silent
    assert silent["reuse_ratio"] is None and silent["cached_tokens"] is None, silent

    zero = metrics.prefix_cache_reuse([_call(1, cached=0, prompt=2000)])
    assert zero["reported"] == 1 and zero["reuse_ratio"] == 0.0, zero
    assert zero["cached_tokens"] == 0, "显式的 0 被当成了缺席"


def test_a_cached_count_with_no_prompt_count_is_left_out_of_both_sides():
    """只报了分子的那次调用不能进账：分母为空时比值是 `null`，不是 `inf` 也不是 1.0。

    端点可以只回 `prompt_tokens_details` 而把顶层 `prompt_tokens` 省掉（体检那次就见过只回一半的
    形状）。这种调用算进分子会让"一次都没复用"读出一个非零的命中率，算进分母则凭空抬高；所以它
    们被单独数在 `unpairable` 那一格里，而不是悄悄蒸发。
    """
    out = metrics.prefix_cache_reuse([_call(1, cached=800), _call(2, cached=200)])
    assert out["reported"] == 2 and out["unpairable"] == 2, out
    assert out["cached_tokens"] is None and out["reuse_ratio"] is None, out


def test_the_per_phase_split_locates_the_wave_that_lost_its_prefix():
    """整批一个比值会藏住"是哪一段的波次身份断了"，所以读数按相切开。

    §5 的前缀是按波共享的：同一相的九个座位被问同一份 A+B 字节。day_vote 那一相如果命中率为
    0.0 而 day_speech 是 1.0，问题出在投票那一段的装配上——总比值只会把这两件事平均成一个数。
    """
    ev = [_call(1, "day_speech", cached=2000, prompt=2000),
          _call(2, "day_speech", cached=1000, prompt=1000),
          _call(3, "day_vote", cached=0, prompt=900), _call(4, "day_vote", prompt=900)]
    out = metrics.prefix_cache_reuse(ev)
    by = out["by_phase"]
    assert by["day_speech"]["reuse_ratio"] == 1.0, by
    assert by["day_vote"]["reuse_ratio"] == 0.0, by
    assert by["day_vote"] == {"reported": 1, "silent": 1, "reuse_ratio": 0.0}, by


def test_the_cache_denominator_is_the_same_batch_of_calls_m7_bills():
    """两处 `calls` 说的必须是同一句话：只算**记过延迟**的那些决定。

    这一格的动机不是设计出来的，是变异跑出来的：`/tmp/mut70.py`（13:34:34Z）在把
    `prefix_cache_reuse` 的延迟过滤摘掉时，全套 0 红——四个既有断言要么不看 `calls` 的绝对值，
    要么两格一起动。而 `m7_cost_profile` 里逐字相同的那一行才是这批调度的账面分母：两处各自
    过滤一遍，就是"一个判定两份实现"，改一处忘另一处时 `m7_cost.n_calls` 与 `prefix_cache.calls`
    会安静地分家，而这两个数并排印在同一份 audit 里。
    """
    ev = [_call(1, cached=1000, prompt=2000), _call(2, prompt=1000),
          _call(3, prompt=900, latency=None)]
    cache = metrics.prefix_cache_reuse(ev)
    cost = metrics.m7_cost_profile(ev)
    assert cache["calls"] == cost["n_calls"] == 2, (cache, cost)
    assert cache["silent"] == 1, "没延迟那一格不许进分母，也不许被数成'端点没说'"
