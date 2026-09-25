"""房规的两个开关，编排层到底看不看：`tie_break` 与 `hunter_shoots_on`。

`/tmp/mut73.py` 复核出的 P6/P9 是这一对。两具各自删掉一个 `and` 后面的房规读取：

* **P6：`if res.tied and res.pk_seats and t.state.house.tie_break == "pk_once_then_nobody":`** 少掉
  最后一支，`tie_break="nobody"` 的桌子照样进 PK——那条房规的全部含义就是"平票不重来"。
* **P9：`and "exiled" in t.state.house.hunter_shoots_on`** 少掉，猎人被投票出局时永远排队开枪，
  而"被票出去不能开枪"是一桌房规里最常见的一种村规。

**诚实边界（报告里也要这么写）**：这两条开关目前没有入口能翻——CLI 不接受房规参数，出厂桌
一直是 `pk_once_then_nobody` + `{wolf_kill, exiled}`（见 `roles.HouseRules`），`test_rules.py`
只把它们喂到规则层。所以这两条用例钉的是"编排层确实读了它"，不是"产品今天能选它"；先钉住的
理由是翻开关的人不该先撞上"读了也没用"。夹具沿用 `test_vote_wave.py` 的那张桌和那种替身。
"""

from __future__ import annotations

import dataclasses

from wolfengine import compress, phases
from wolfengine.events import Kind
from wolfengine.state import Phase

from test_vote_wave import ALL_SEATS, _pass, _table, _vote


def _house(state, **kw) -> None:
    """翻一条房规，其余原样（`HouseRules` 冻结、`GameState.house` 只读，只能换整张 board）。"""
    state.board = dataclasses.replace(
        state.board, house=dataclasses.replace(state.board.house, **kw))


def _tied_ballots() -> dict[int, list]:
    """三比三的票型加三张弃票：平票是真造出来了，不是靠断言假设。"""
    return {s: [(_vote(3) if s in (1, 2, 4) else _vote(5) if s in (6, 7, 8) else _pass())]
            for s in ALL_SEATS}


# ---------------------------------------------------------------------- ① tie_break=nobody
async def test_a_house_that_says_nobody_on_a_tie_never_opens_a_pk(tmp_path):
    """`tie_break="nobody"`：平票就散会，没有第二轮，也没有人出局。

    手术刀 P6：删掉那一支 `and`。复投波一开，每个座位会被问第二次票、日志里会多一条
    VOTE_RESULT——两条断言都指向"这一桌被改成了别的房规"。
    """
    _cfg, state, log, table, actors = _table(tmp_path, _tied_ballots())
    _house(state, tie_break="nobody")

    await phases.run_vote(table)

    asked = [len([t for t in a.turns if t.phase is Phase.DAY_VOTE]) for a in actors.values()]
    assert asked == [1] * len(ALL_SEATS), f"平票后又开了一轮复投：每座被问 {asked} 次"
    assert sum(1 for e in log.all() if e.kind == Kind.VOTE_RESULT) == 1
    assert state.alive_seats and len(state.alive_seats) == len(ALL_SEATS), \
        f" {[s for s in ALL_SEATS if not state.is_alive(s)]} 在这张不该死人的桌上出局了"
    # 反向对照：平票本身确实发生了，否则上面三条只是"没人投票"的副产品。而这张桌上"无人出局"
    # 就是**结论**（没有 PK 会来收回它），所以钉那一行的整句而不是钉"平票"两个字——
    # `tests/test_judge_wording.py` ①钉的是另一张桌上这句不许出现。
    published = [e for e in log.all() if e.kind == Kind.VOTE_RESULT][-1]
    assert published.payload["pending_pk"] is False, published.payload
    assert compress.render_line(published).endswith("平票，无人出局。"), compress.render_line(published)


# -------------------------------------------------------------------- ② hunter_shoots_on
async def test_a_hunter_exiled_by_a_house_that_only_allows_night_deaths_keeps_his_gun(tmp_path):
    """`hunter_shoots_on={wolf_kill}`：被票出去的人不排队开枪。

    手术刀 P9：删掉那一支 `and`。枪是 irreversible 的一步——排进 `pending_shots` 之后
    `run_hunter_shots` 一定会把它打出去，所以这条断言看的是队列本身，不是有没有开枪。
    """
    ballots = {s: [_vote(9)] for s in ALL_SEATS if s != 9}
    ballots[9] = [_vote(1)]
    _cfg, state, _log, table, _actors = _table(tmp_path, ballots)
    assert state.role_of(9) == "hunter", "这张桌的 9 号不是猎人，下面两条都是空转"
    _house(state, hunter_shoots_on=frozenset({"wolf_kill"}))

    await phases.run_vote(table)

    assert not state.is_alive(9)
    assert state.pending_shots == [], f"房规说不许，枪还是排进了队列：{state.pending_shots}"

