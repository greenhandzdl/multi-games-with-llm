"""白昼那三枚"名册空了就不问"的守卫：把它们走到，并说清哪一枚走不得。

`#245` 剩下的四格里有三格住在 `phases.py` 的白昼链上（`_wolves` 没狼、`run_day` 胜负已定、
`run_vote` 没投票人）。这一片不能靠"整套跑绿"来交代，因为四格里有一格**任何刀都不会红**——
先把那格量出来，再给余下三格配证人。量法是把守卫换成 `pass` 的模块副本（`/tmp/probe248.py`，
同包加载所以 `from . import rules` 照旧解析）和原件跑同一个现场，逐字比日志摘要：

| 格 | 拆掉守卫之后 | 定性 |
|---|---|---|
| `_wolves` 的空狼队 | `wolves[(day - 1) % len(wolves)]` 除零，整夜崩 | 有人读，配证人 |
| `run_day` 的胜负已定 | 多写三条事件：已经结束的局又发言、又投票 | 有人读，配证人 |
| `run_vote` 的空名册 | 多写一条事件，且 `tally_votes` 收到空票箱 | 有人读，配证人 |
| `run_speeches` 的没人发言 | 摘要一字不差、事件数一字不差 | **等价变异**，不配证人 |

`run_speeches` 那一格不写断言是刻意的：守卫之后的每一条语句都是在遍历 `speakers`，空表时
整段本身就是空转，写一条"什么都不发生"的断言只会给普查造出一个假的执行人。它的结构理由
（生产链上唯一两个调用点给的都不是空表：`run_day` 递的是活着的人，PK 那一支递的是平票席）
和量的结果一起登在归档里。

三枚证人都走真牌桌：真发牌、真 `Agent`、真 `MockActor`，只把座位的 `alive` 翻成 False 来摆出
空名册。夹具是 `tests/test_night_guards.py` 那一张桌，因为"没人"这件事必须由真名册算出来——
`alive()` 读的是 `state.seats[*].alive`，手搓一个空列表就把被审的那一层换成了自己的夹具。
"""

from __future__ import annotations

import random

from wolfengine import game, phases
from wolfengine.agent import Agent
from wolfengine.actors import MockActor
from wolfengine.config import Config
from wolfengine.events import Kind

SEED = 7
# seed 7 的名册与 test_golden_game.py 同一张：狼 1/2/4，女巫 5，预言家 7，猎人 9，平民 3/6/8。
WOLVES = (1, 2, 4)
VILLAGERS = (3, 5, 6, 7, 8, 9)


def _table(tmp_path, dead: tuple[int, ...], *, name: str):
    cfg = Config()
    state, personas, _ = game.build_game(cfg, SEED, f"g{SEED:08d}")
    log = game.open_log(cfg, state, tmp_path / name, ("mock",), SEED, "20260922T145100Z")
    agent = Agent(cfg=cfg, state=state, log=log, personas=personas,
                  known_ids=lambda: {f"e{e.seq}" for e in log.all()})
    actors = {s: MockActor(s, synthesize=True) for s in state.seats}
    for seat in dead:
        state.seats[seat].alive = False
    return phases.Table(cfg=cfg, state=state, log=log, agent=agent, actors=actors,
                        rng=random.Random(SEED))


def _killed_by_wolf(t) -> bool:
    return any(e.kind == Kind.NIGHT_ACTION and e.payload.get("act") == "kill"
               for e in t.log.all())


# ------------------------------------------------------------------------- ① 狼队没人了
async def test_a_night_with_no_wolves_left_asks_no_one_for_a_knife(tmp_path):
    """狼全死 = 那一夜根本没有刀：不问，也不许因为"没人"而算下标算到崩。"""
    t = _table(tmp_path, WOLVES, name="nowolves.jsonl")

    await phases.run_night(t)

    assert not _killed_by_wolf(t), "狼队空了还是落下一把刀"
    assert t.blocked == [], t.blocked
    assert any(e.kind == Kind.PHASE for e in t.log.all()), "整夜根本没跑，上面那条是空转"


async def test_a_night_with_wolves_left_does_ask_for_a_knife(tmp_path):
    """上一条的反向对照：不许是"从来不问狼队"，否则两条一起绿。"""
    t = _table(tmp_path, (3,), name="wolves.jsonl")

    await phases.run_night(t)

    assert _killed_by_wolf(t), "狼还活着，那一把刀却没被问过"


# ------------------------------------------------------------------------- ② 胜负已定的天亮
async def test_a_decided_dawn_announces_but_does_not_open_the_floor(tmp_path):
    """天亮那句要照发，但已经结束局不再发言、不再投票——守卫拆了就多三条事件。"""
    t = _table(tmp_path, VILLAGERS, name="decided.jsonl")
    opened = len(t.log.all())

    await phases.run_day(t)

    kinds = [e.kind for e in t.log.all()]
    assert Kind.PHASE in kinds, "天亮了那句没发，那这一桌没走到守卫"
    assert Kind.SPEECH not in kinds, "胜负已定还是开了一轮发言"
    assert len(kinds) - opened == 1, kinds[opened:]


async def test_an_undecided_dawn_does_open_the_floor(tmp_path):
    """同一段代码在没结束的局上必须发发言：钉住上一条的 `not in` 不是因为 run_day 什么都不做。"""
    t = _table(tmp_path, (3,), name="undecided.jsonl")

    await phases.run_day(t)

    assert any(e.kind == Kind.SPEECH for e in t.log.all())


# ------------------------------------------------------------------------- ③ 没人可投票
async def test_a_ballot_with_no_voters_returns_before_the_boxes_open(tmp_path):
    """一张空名册不开发票：`_ballots` 的并行波一次都不该被起。"""
    t = _table(tmp_path, tuple(range(1, 10)), name="novoters.jsonl")
    before = len(t.log.all())

    assert await phases.run_vote(t) is None
    assert len(t.log.all()) == before, "没人投票还是往日志里写了结算"


async def test_a_ballot_with_voters_writes_the_settlement(tmp_path):
    """上一条的反向对照：有人投票时必须多写出东西来，否则"什么也不写"这条判据是空的。"""
    t = _table(tmp_path, (3,), name="voters.jsonl")
    before = len(t.log.all())

    await phases.run_vote(t)

    assert len(t.log.all()) > before
