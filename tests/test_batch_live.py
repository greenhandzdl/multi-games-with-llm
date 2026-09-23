"""§5 买的那笔折扣在**臂**这一级读不读得出来：一条真链跑一整批。

`tests/test_live_path.py` 已经证明 `cached_tokens` 能从一根线上走到 `wolf audit` 那一格——但那是一
局。拿八十份日志的人要的是"这条臂有没有省下 prefill"，而**把各局比值平均**出来的那个数没有人问过：
一局多打了一次重试，它就按自己那一份比值投票，而不是按自己那一份分子分母进池子。这一页把
`batch.run_batch` 接在 `httpx.MockTransport` 上跑真链，只盯三件事：池内相除、`null` 与 `0` 的分别
在臂级还成不成立、以及这两格最后有没有走到 `comparison.md`。

桩线故意让"端点报没报"成为那根轴的函数：冷臂（`temperature_ladder=(0.3,)`，`config.py` 的
`temperature_for` 决定、`actors.py` 发上线的那一格）每次回答都带 `prompt_tokens_details`，热臂一个字
都不提。延迟不是测出来的，所以这一页不产出任何延迟主张。
"""

from __future__ import annotations

import asyncio
import json

import httpx

from wolfengine import batch
from wolfengine.config import Config
from wolfengine.transport import HttpTransport

from test_live_path import _user_text, oracle_answer

PROMPT_TOKENS = 1000
# 7 步循环 + 130 的步长：一局之内的比值各不相同，两局之间调用次数不同 ⇒ 各局平均值 != 池内比值。
CYCLE, STEP, BASE = 7, 130, 100

# 轴走 `temperature_ladder`：它是逐座那一格，一臂一档就能让整臂每次调用落在同一个温度上，
# 于是桩线"报不报 cached_tokens"是温度的函数。`Config.temperature` 现在也真上线了（`#76` 之后
# 梯子为空时由 `temperature_for` 取它当全局默认），当轴一样使得动，这里只是不需要两档。
AXIS = ("temperature_ladder",)


def _arms() -> list[batch.Arm]:
    return [batch.Arm(name, batch.apply_overrides(Config(), dict(o)), overrides=tuple(o))
            for name, o in {"A": {AXIS[0]: (0.3,)}, "B": {AXIS[0]: (0.9,)}}.items()]


def _wire(seen: dict[str, list[tuple[int | None, int]]]):
    """Answers legally; reports `cached_tokens` only for the cold arm.

    `seen` records what the socket actually said, so the expected arithmetic is written down
    before the code under test touches it rather than read back out of it.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        cold = body["temperature"] < 0.5
        usage: dict = {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": 40,
                       "total_tokens": PROMPT_TOKENS + 40}
        if cold:
            cached = BASE + (len(seen["cold"]) % CYCLE) * STEP
            usage["prompt_tokens_details"] = {"cached_tokens": cached}
            seen["cold"].append((cached, PROMPT_TOKENS))
        else:
            seen["warm"].append((None, PROMPT_TOKENS))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": oracle_answer(_user_text(request))},
                         "finish_reason": "stop"}], "usage": usage})
    return handler


async def _compare(tmp_path, seen: dict, *, games: int) -> dict:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_wire(seen)))
    try:
        await batch.run_batch(_arms(), games=games, seed0=5, out_dir=tmp_path,
                              transport=HttpTransport(Config(), client=client))
    finally:
        await client.aclose()
    return batch.compare(tmp_path, axis=AXIS)


def _per_game_sums(arm_dir) -> list[tuple[int, int]]:
    """Per log file: (Σ cached, Σ prompt) over the calls that answered both, read off the raw JSONL.

    Deliberately not `metrics`: a test that recomputes the expected value with the function under
    test proves nothing. A row with no `usage` block is skipped, which is the same set the pooled
    cell divides over — `reported` counts rows that carried a cached count.
    """
    out: list[tuple[int, int]] = []
    for path in sorted(arm_dir.glob("*.jsonl")):
        pairs = []
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            usage = (row.get("response") or {}).get("usage") or {}
            if usage.get("cached_tokens") is not None:
                pairs.append((int(usage["cached_tokens"]), int(usage["prompt_tokens"])))
        if pairs:
            out.append((sum(c for c, _ in pairs), sum(p for _, p in pairs)))
    return out


def test_the_arm_divides_the_pool_instead_of_averaging_the_games(tmp_path, key):
    """生产改动让它红的方式：把臂级比值改成"各局比值取平均"，或把分子分母取自不同的调用集合。"""
    seen: dict = {"cold": [], "warm": []}
    out = asyncio.run(_compare(tmp_path, seen, games=2))
    assert out["verdict"] == "OK", out["why"] if "why" in out else ""
    cell = out["prefix_cache"]["A"]

    pooled_c = sum(c for c, _ in seen["cold"])
    pooled_p = sum(p for _, p in seen["cold"])
    per_game = _per_game_sums(tmp_path / "A")
    assert len(per_game) == 2, per_game
    assert (sum(c for c, _ in per_game), sum(p for _, p in per_game)) == (pooled_c, pooled_p)
    avg = sum(c / p for c, p in per_game) / len(per_game)
    assert round(pooled_c / pooled_p, 6) != round(avg, 6), \
        "夹具退化了：两种算法同值，这一条就没有分辨力了"

    assert cell["cached_tokens"] == pooled_c
    assert cell["prompt_tokens"] == pooled_p
    assert cell["reuse_ratio"] == round(pooled_c / pooled_p, 4)
    assert cell["reuse_ratio"] != round(avg, 4)


def test_the_arm_the_endpoint_never_answered_reads_null_not_zero(tmp_path, key):
    """`#70` 把"没问过它"与"回答了：一次都没复用"分开，这一条钉的是那个分处在臂级没掉。

    热臂有调用（`calls` > 0）却没有一次回答，所以比值必须是 `null`；写成 0.0 就是把端点的
    沉默报成模型的吝啬，而 §5 的折扣结论正是从这一格上读的。
    """
    seen: dict = {"cold": [], "warm": []}
    out = asyncio.run(_compare(tmp_path, seen, games=2))
    warm = out["prefix_cache"]["B"]
    assert warm["calls"] > 0 and warm["reported"] == 0 and warm["silent"] == warm["calls"]
    assert warm["cached_tokens"] is None and warm["prompt_tokens"] is None
    assert warm["reuse_ratio"] is None
    assert warm["n_games"] == warm["n_games_silent"] == 2
    assert out["prefix_cache"]["A"]["reuse_ratio"] is not None


def test_the_per_phase_split_at_arm_altitude_adds_up_over_every_game(tmp_path, key):
    """按相切开是为了指出"哪一波把前缀弄丢了"，臂级那一份必须覆盖整条臂而不是第一局。"""
    seen: dict = {"cold": [], "warm": []}
    out = asyncio.run(_compare(tmp_path, seen, games=2))
    a = out["prefix_cache"]["A"]
    assert len(a["by_phase"]) > 1, a["by_phase"]
    assert sum(v["reported"] for v in a["by_phase"].values()) == a["reported"]
    assert sum(v["silent"] for v in a["by_phase"].values()) == a["silent"]
    b = out["prefix_cache"]["B"]
    assert all(v["reported"] == 0 for v in b["by_phase"].values())


def test_the_comparison_markdown_has_a_row_for_each_arm(tmp_path, key):
    """读数进了 JSON 却到不了人，等于没测：两臂各一行，沉默那一臂印 `—` 而不是一个 0。"""
    seen: dict = {"cold": [], "warm": []}
    out = asyncio.run(_compare(tmp_path, seen, games=2))
    md = out["markdown"]
    assert "## 前缀缓存" in md
    # 只在**这一节里**找行：区域预算那一节也有一行 `| A | …`，整篇扫会把两张表读成一张。
    section = md.split("## 前缀缓存", 1)[1].split("\n## ", 1)[0]
    rows = {ln.split("|")[1].strip(): ln
            for ln in section.splitlines() if ln.startswith("| ")}
    assert set(rows) >= {"A", "B"}, sorted(rows)
    ratio = out["prefix_cache"]["A"]["reuse_ratio"]
    assert f"{ratio:.4f}" in rows["A"], rows["A"]
    # `—` 是这份报告里"没量到"的印法（与 `_cell`、`_region_md` 同一套），0.0000 是"量到了，是零"。
    assert "—" in rows["B"] and "0.0000" not in rows["B"], rows["B"]
    assert section.count("端点没报过") == 1, section
