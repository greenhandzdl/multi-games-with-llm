"""替身桌被剔除时，报告说出的**依据**必须真是那条依据。

`#87` 的方法（读一遍产物，找语义错而不是计数错）在 `wolf compare` 的输出上撞到第二类：数字
全对 —— 3 局 × 2 臂、`actor_kinds=['mock']`、`win` 清空 —— 句子里的出处是假的。2026-09-23
实测（`/tmp/m88batch2`）`comparison.md` 印的是：

    合成桌（含 mock 座位）只验证管线，不产出结论：plan §十五 规定它永不进评测语料。

而 plan §十五 第 358 行的原话是「**含真人座位的局**永远不得进入配对评测语料」，管的是人；
mock 的约束在 §十一 第 287 行「必须真端点（mock 只会自证，这几项不许省）」。读者照着印出来的
那一格去找，只会找到一条关于玩家的规定，然后得出恰恰相反的结论：替身 transport 没人管。

同一句假出处在三个出口各写了一遍（`compare` 的拒绝语、`m1_win_rate` 的 note、
`m3_gate_verdict` 的 note），外加两处文档 —— 与 `#87` 的「一个谓词两处写」同形，所以修法也
同类：一张表、一个函数，三个出口共用。

还有一句假话在同一行里：note 硬编码「actor_kinds 含 mock/human」，而批次顶层的
`actor_kinds` 只有 `["mock"]`/`["llm"]` 两种取值（`batch.py:207`），真人座位只活在局文件的
页眉里。于是「含真人的那一批」被拒时，印出来的种类恰恰是 `['llm']` —— 句子自相矛盾，而计数
仍然全对。这里的判据因此是**从触发拒绝的那些局身上取种类**，不是从 manifest。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from wolfengine import batch, metrics
from wolfengine.config import Config


def _table(kinds: list[str] | None, game_id: str = "g1") -> metrics.Game:
    """一张只带页眉的桌：三条被拒路径都不读事件，读的是 `actor_kinds` 那一格。

    种类为 None 时连那一格都不写 —— 现实里只有被人手改过的日志会这样，而
    `is_synthetic` 对它是**关门**（不等于 `["llm"]`），所以措辞必须跟着诚实。
    """
    meta: dict[str, object] = {"game_id": game_id}
    if kinds is not None:
        meta["actor_kinds"] = kinds
    return metrics.Game(path=Path(f"/tmp/{game_id}.jsonl"), meta=meta, events=[])


# ------------------------------------------------------------------ 局级：种类 → 条款
def test_a_mock_table_cites_the_clause_that_rules_on_mock():
    """mock 不进结论出自 §十一，不是 §十五：把两者混起来，读者在 §十五 找不到任何关于
    替身 transport 的规定，就会以为这条剔除是随手加的。"""
    out = metrics.m1_win_rate([_table(["mock"], "g1"), _table(["mock"], "g2")])
    note = out["note"]
    assert out["good_win_rate"] is None, out
    assert "§十一" in note, note
    assert "§十五" not in note and "§15" not in note, note
    # 种类也要照实印：旧措辞写死"含 mock/human"，一批全是 mock 的桌子被拒时被告知桌上还有
    # 真人，读者就会去找那条关于人的条款 —— 与假出处是同一个错。
    assert "actor_kinds=['mock']" in note and "human" not in note, note


def test_a_human_seat_table_cites_the_clause_that_rules_on_humans():
    """同一批换成真人坐在 9 号位：这一次 §十五 才是依据，而 §十一 那句"mock 只会自证"
    对着一桌人类说就是假的。两条条款各管一种座位，替换不得只换一半。"""
    out = metrics.m1_win_rate([_table(["llm"] * 8 + ["human"], "g1")])
    note = out["note"]
    assert "plan §十五（含真人座位的局永远不得进入配对评测语料）" in note, note
    assert "§十一" not in note and "mock" not in note, note
    assert "actor_kinds=['human', 'llm']" in note, note


def test_both_seat_kinds_quote_both_clauses():
    """一臂全 mock、一臂含真人：两句话都要在场，且各自带着自己管的种类。"""
    out = metrics.m1_win_rate([_table(["mock"], "g1"), _table(["human"], "g2")])
    note = out["note"]
    assert "§十一" in note and "§十五" in note, note
    assert "mock" in note and "human" in note, note


def test_an_unregistered_table_borrows_no_clause():
    """`actor_kinds` 缺格仍然被拒（关门），但没有一条条款点名"没登记"这种座位。
    此时只能说自己缺读数，不能挑一条最近的借来 —— 借来的出处比没有出处更贵。

    两种写法都要查：文档里 §十五 与 §15 混用过，只盯一种的断言会对着另一种的假出处放行。
    """
    out = metrics.m1_win_rate([_table(None, "g1")])
    note = out["note"]
    assert "§十一" not in note and "§十五" not in note, note
    assert "§11" not in note and "§15" not in note, note
    assert out["n_synthetic_excluded"] == 1, out


# ------------------------------------------------------------------ 三个出口共用一张表
def test_the_gate_and_the_win_rate_cite_the_same_words():
    """同一件事在两个出口里必须是同一串字。这里钉的是措辞本身：谁把其中一处改写成
    另一套说法，这一条就红 —— 因为读者会从两份报告里读到两个不同的出处。"""
    games = [_table(["mock"], "g1"), _table(["mock"], "g2")]
    m1 = metrics.m1_win_rate(games)["note"]
    gate = metrics.m3_gate_verdict(games)["note"]
    assert "plan §十一（mock 只会自证，这几项不许省）" in m1, m1
    assert "plan §十一（mock 只会自证，这几项不许省）" in gate, gate
    assert "§十五" not in m1 + gate, (m1, gate)


# ------------------------------------------------------------------------- 批次出口
def _mock_batch(tmp_path):
    arms = [batch.Arm("A", Config(), overrides=()),
            batch.Arm("B", batch.apply_overrides(Config(), {"temperature": 0.6}),
                      overrides=("temperature",))]
    asyncio.run(batch.run_batch(arms, games=1, seed0=5, out_dir=tmp_path, mock=True,
                                canary=batch.NO_CANARY))
    return arms


def _rehumanise(tmp_path, kinds: list[str]) -> None:
    """把局文件页眉的 `actor_kinds` 就地改掉，manifest 那一格**留着不动**。

    留着才是要点：批次顶层的 `actor_kinds` 写不出真人（`batch.py:207` 只有 mock/llm 两种
    取值），所以这一格一旦成了依据的来源，印出来的句子就会说"本批 actor_kinds=['mock']"
    来解释一次由真人座位引起的拒绝。
    """
    for p in sorted(tmp_path.glob("*/*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        head = json.loads(lines[0])
        head["meta"]["actor_kinds"] = kinds
        lines[0] = json.dumps(head, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    man_path = tmp_path / "run_manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    assert man["actor_kinds"] == ["mock"], "改的就是『manifest 与局文件不一致』这个前提"


def test_compare_names_the_seats_that_actually_played(tmp_path):
    """一次拒绝要说清是谁在桌边，且依据与种类同源：mock 批引 §十一，把局文件改成真人批之后
    引 §十五 并印出 human —— 而 manifest 那一格全程是 `["mock"]`。"""
    _mock_batch(tmp_path)
    md = batch.compare(tmp_path, axis=("temperature",))["markdown"]
    assert "SYNTHETIC_TABLE" in md
    assert "§十一" in md and "§十五" not in md, md

    _rehumanise(tmp_path, ["llm"] * 8 + ["human"])
    md = batch.compare(tmp_path, axis=("temperature",))["markdown"]
    assert "§十五" in md and "§十一" not in md, md
    assert "human" in md, md
