"""遗言的两道闸：每人只有一段（从日志推），且只给今天早上倒下的人。

`/tmp/mut73.py` 复核出的 P11/P12 是这一对。它们看起来像"互为冗余、拆一具没有差别"——
`run_last_words` 的 docstring 说去重集合是从日志推的而不是 `Death` 上的标记，`run_day` 又说
只把 `d.night == state.day` 的座位递进去。先把这个判断驳掉：两具各自有一条钉得死的用例，只
是钉法不同。

* ①P12：`already = {e.actor for e in log.all() if e.kind == Kind.LAST_WORDS}` 清空后，同一张
  座位被两次递进 `run_last_words` 就会得到两段遗言。`Death` 是冻结的，挂标记会当场抛异常，
  所以"说过没有"这件事的唯一来源就是日志——这句理由得有一条用例读它。
* ②P11：`d.night == t.state.day` 那半句删掉后，`state.deaths` 里任何一条**当年没说上话**的
  夜晚死亡都会在后面某一天被重新递进去。日志里没有她的 LAST_WORDS 事件时，去重集合挡不住她。

第②条要翻 `house.last_words_night_death_later`（出厂为 False，见 `roles.HouseRules`）才能
到达，而 CLI 没有这个开关——这跟 P6/P9 一样要在报告里披露为"没有入口能走到"。仍然钉：这条
支路是房规的一部分，翻它的人不该先撞上"编排层根本不看它"。
"""

from __future__ import annotations

import dataclasses
import random

from wolfengine import game, phases
from wolfengine.agent import Agent
from wolfengine.actors import MockActor
from wolfengine.config import Config
from wolfengine.events import Kind
from wolfengine.state import Death

SEED = 7


def _table(tmp_path, *, name: str = "lw.jsonl"):
    """一桌停在白天的现场，座位上全是会照指派答题的合成替身（这一文件只管"问不问"）。"""
    cfg = Config()
    state, personas, _ = game.build_game(cfg, SEED, f"g{SEED:08d}")
    log = game.open_log(cfg, state, tmp_path / name, ("mock",), SEED, "20260922T145400Z")
    agent = Agent(cfg=cfg, state=state, log=log, personas=personas,
                  known_ids=lambda: {f"e{e.seq}" for e in log.all()})
    actors = {s: MockActor(s, synthesize=True) for s in state.seats}
    table = phases.Table(cfg=cfg, state=state, log=log, agent=agent, actors=actors,
                         rng=random.Random(SEED))
    return cfg, state, log, table, actors


def _kill(state, seat: int, *, night: int, cause: str) -> None:
    state.seats[seat].alive = False
    state.deaths.append(Death(seat=seat, day=night, night=night, cause=cause))


def _house(state, **kw) -> None:
    """翻一条房规，其余原样。

    `HouseRules` 是冻结的，`GameState.house` 又是只读属性（它返回 `board.house`），所以改房规
    只能换整张 board——`test_rules.py` 里翻房规走的也是这条路。
    """
    state.board = dataclasses.replace(
        state.board, house=dataclasses.replace(state.board.house, **kw))


def _spoken(log, seat: int | None = None) -> list[int]:
    return [e.actor for e in log.all()
            if e.kind == Kind.LAST_WORDS and (seat is None or e.actor == seat)]


# ------------------------------------------------------------------------ ① 每人只有一段
async def test_a_seat_asked_twice_still_gets_one_set_of_last_words(tmp_path):
    """`run_last_words` 对同一张座位是幂等的，因为"说过没有"的唯一读者是日志。

    手术刀 P12：把 `already` 换成空集。两次调用之间桌面没有任何状态变化，能挡住第二段的
    只有日志里那一条她自己的遗言。
    """
    _cfg, state, log, table, _actors = _table(tmp_path)
    _kill(state, 3, night=1, cause="wolf_kill")
    _kill(state, 6, night=1, cause="poison")

    await phases.run_last_words(table, [3])
    assert _spoken(log, 3) == [3], "第一次就没问到，那后面的断言都是空转"
    await phases.run_last_words(table, [3])
    assert len(_spoken(log, 3)) == 1, f"3 号说了 {len(_spoken(log, 3))} 段遗言"
    # 反向对照：去重不是"只让第一张座位说话"——没说过的那位还是得问到。
    await phases.run_last_words(table, [3, 6])
    assert _spoken(log) == [3, 6], _spoken(log)


# -------------------------------------------------------------------- ② 只给今天早上的人
async def test_last_words_belong_to_tonight_not_to_an_older_undelivered_death(tmp_path):
    """第 2 天的清晨，没人替第 1 夜补一场迟到的遗言。

    夹具是手工摆的：6 号第 1 夜死于狼刀、当天没说上话（日志里没有她的 LAST_WORDS 事件），
    3 号第 2 夜死于毒。`run_day` 必须只把 3 号递进去。手术刀 P11：删掉 `d.night ==
    t.state.day`——那时 6 号会被重新问一次，而去重集合里没有她。
    """
    _cfg, state, log, table, _actors = _table(tmp_path, name="older.jsonl")
    _house(state, last_words_night_death_later=True)
    assert state.house.last_words_night_death_later is True, "房规没翻过来，这条测的是出厂支路"
    state.day = 2
    _kill(state, 6, night=1, cause="wolf_kill")
    _kill(state, 3, night=2, cause="poison")
    assert 6 not in state.alive_seats and 3 not in state.alive_seats

    await phases.run_day(table)

    assert len(_spoken(log, 3)) == 1, f"今天早上倒下的人没说上话：{_spoken(log)}"
    assert not _spoken(log, 6), f"第 1 夜没说的话在第 2 天补上了：{_spoken(log)}"
