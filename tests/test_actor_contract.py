"""plan §15 的三条"上桌前必须从编排层清掉的隐含假设"：代码里都实现了，仓库里 0 条测试。

三条都不是抽象洁癖。这三条立起来的时候 `HumanActor.act()` 还是一句 `NotImplementedError`（直到
`#122` 才变成坐得下人的座位），所以任何一条被后来的重构悄悄改回去，都要等到真有人坐进那个座位时
才炸——而那时全仓库没有替身能复现。这里用最小替身把三条各自钉住：

* ①超时按 actor 取，不按阶段、也不拿配置兜底：`timeout_for()` 返回 `None` 的座位不能被地板值判死。
* ②并发度表接受"此刻能答的座位"，且 `blocking` 的座位要剔出去——它正在被逐个等，不是一个 worker
  名额；不剔的话真人发言时其余八座被同一个 semaphore 卡住，正是 §15 说要防的东西。
* ③墙钟只对全模型桌生效：一个人在想的局不叫 stall，杀掉它等于把"这个人为什么坐在这儿"扔掉。

第①条有个诚实边界：`asyncio.wait_for(coro, None)` 本身就是无限等，所以"删掉 `agent.py` 里
`if limit is None` 那一支"是**等价变异**，这一条测不出来、也不需要测。它钉的是"截止时间不从
`Config` 里来"——能抓住的是 `actor.timeout_for(...) or cfg.llm_timeout_floor_s` 这种"顺手兜个底"，
而那恰好是把 §15 第 1 条改回去的最自然写法。

最后一条不是 §15 的假设，是**契约本体**（`HumanActor` 自己那四个声明）。`#85` 之前全仓库没有一个
命名 `HumanActor` 的读者，"一期只留契约"这句话因此只活在 docstring 里。`#122` 把 `act()` 实现了
之后，这一条更吃重而不是更轻松：那四个声明现在是**生产路径**读的输入（`wave_size`、墙钟闸门、
`to_thread` 里那次阻塞读），而它自己那一侧的行为住在 `tests/test_human_seat.py`。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import random
from typing import get_args

import pytest

from wolfengine import game, phases, rules
from wolfengine.agent import Agent
from wolfengine.actors import ActorKind, HumanActor, MockActor, Proposal
from wolfengine.config import Config
from wolfengine.events import Kind, seats
from wolfengine.schema import Action
from wolfengine.state import Phase

SEED = 7


class _Seat:
    """一个把"像谁"和"多久答"各自说清的座位。

    `kind` / `blocking` / `limit` / `sleep_s` 四件事互相独立，因为 §15 的三条假设各自只动其中一件：
    绑成一个"真人替身"就只剩"像不像人"可测，那条规则本身反而测不到。
    """

    def __init__(self, *, kind: str = "human", blocking: bool = False,
                 limit: float | None = None, sleep_s: float = 0.05, target: int = 1) -> None:
        self.kind = kind
        self.blocking = blocking
        self.limit = limit
        self.sleep_s = sleep_s
        self.target = target
        self.calls = 0

    def timeout_for(self, phase: Phase) -> float | None:
        return self.limit

    async def act(self, ctx) -> Proposal:
        self.calls += 1
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        return Proposal(action=Action(act="kill", target=self.target),
                        raw='{"act":"kill"}', rung=0,
                        response={"latency_s": self.sleep_s, "attempts": 1})


def _table(tmp_path, *, cfg: Config | None = None, blocking: set[int] = frozenset(),
           prefix: int = 0):
    """一桌夜晚 1 的完整现场：状态、日志、agent，和九个各自声明 blocking 的座位。"""
    cfg = cfg or Config()
    state, personas, _ = game.build_game(cfg, SEED, f"g{SEED:08d}")
    log = game.open_log(cfg, state, tmp_path / "t.jsonl", ("llm",), SEED, "20260921T000000Z")
    agent = Agent(cfg=cfg, state=state, log=log, personas=personas,
                  known_ids=lambda: {f"e{e.seq}" for e in log.all()})
    agent.last_prefix_tokens = prefix
    actors = {s: _Seat(blocking=s in blocking) for s in state.seats}
    table = phases.Table(cfg=cfg, state=state, log=log, agent=agent, actors=actors,
                         rng=random.Random(SEED))
    wolf = next(s for s in state.seats if state.team_of(s) == "wolf")
    return cfg, state, log, agent, table, wolf


async def _one_night_turn(tmp_path, *, limit: float | None, sleep_s: float):
    """让狼座位单独答一次夜晚刀人，返回 (agent, log, outcome)。"""
    cfg, state, log, agent, _, wolf = _table(
        tmp_path, cfg=dataclasses.replace(Config(), llm_timeout_floor_s=0.01))
    legal = rules.legal_actions(state, wolf)
    seat = _Seat(limit=limit, sleep_s=sleep_s, target=sorted(legal.targets)[0])
    outcome = await agent.take_turn(seat=wolf, actor=seat, legal=legal,
                                    phase=Phase.NIGHT_WOLF, kind=Kind.NIGHT_ACTION,
                                    visibility=seats(wolf))
    return agent, log, seat, outcome


# --------------------------------------------------------------- ① 超时按 actor 取
async def test_a_seat_that_declares_no_deadline_is_not_given_one_by_the_floor(tmp_path):
    """§15 第 1 条：`None` 是"永远等"，不能被 `llm_timeout_floor_s` 当成"没填"。"""
    agent, log, seat, outcome = await _one_night_turn(tmp_path, limit=None, sleep_s=0.05)

    assert seat.calls == 1, "座位根本没被问，那下面几条都是空转"
    assert not outcome.timed_out and agent.timeouts == 0
    assert not outcome.fell_back
    assert log.all()[-1].kind == Kind.NIGHT_ACTION, "答案没落盘，那上面两条只是什么都没发生"


async def test_the_same_slowness_with_a_number_does_cut_the_turn(tmp_path):
    """同一个 50ms、同一张桌，唯一的差别是座位报了一个数：这一条必须红在超时上。

    没有它，上一条分不清"`None` 被尊重"和"这条路径根本没跑到"。
    """
    agent, _, seat, outcome = await _one_night_turn(tmp_path, limit=0.01, sleep_s=0.05)

    assert seat.calls == 1
    assert outcome.timed_out and agent.timeouts == 1


# --------------------------------------------------------------- ② 并发度按能答的座位算
def test_the_seat_being_waited_on_is_not_a_worker_slot(tmp_path):
    _, state, _, _, table, _ = _table(tmp_path, blocking={3})
    everyone = list(state.seats)
    assert table.wave_size(everyone) == len(everyone) - 1
    assert table.wave_size([3]) == 1, "只剩被等的那一座时也不能给出 0：Semaphore(0) 会锁死这一波"


def test_a_wave_of_eight_waiting_seats_still_has_exactly_one_worker(tmp_path):
    _, state, _, _, table, _ = _table(tmp_path, blocking={1, 2, 3, 4, 5, 6, 7, 8})
    assert table.wave_size(list(state.seats)) == 1


def test_the_wave_is_capped_by_the_prefix_ladder_not_by_the_seat_count(tmp_path):
    """剔掉 blocking 之后还要过一遍静态表：k 随前缀长度降档，这是 §六 那张表的本体。"""
    _, state, _, agent, table, _ = _table(tmp_path, prefix=2500)
    assert agent.last_prefix_tokens == 2500
    assert table.wave_size(list(state.seats)) == 6
    assert table.wave_size([1, 2]) == 2, "座位比表少时按座位数，不能被表抬高"


# --------------------------------------------------------------- ③ 墙钟只对全模型桌生效
class _AsModel(MockActor):
    """按 mock 剧本答题、但**声明**自己是模型的座位：只为把 `play()` 的墙钟闸门打开。"""

    kind = "llm"  # type: ignore[assignment]


class _AsHuman(MockActor):
    kind = "human"  # type: ignore[assignment]
    blocking = True


def _nine(seat_cls) -> dict[int, MockActor]:
    return {s: seat_cls(s, synthesize=True, rng=random.Random(SEED * 10 + s))
            for s in range(1, 10)}


def _play(tmp_path, actors: dict) -> game.GameResult:
    """同一副牌、`wallclock_limit_s=0`：只要闸门开着，第一个日间检查必然落下。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return asyncio.run(game.play(cfg=Config(), deal_seed=SEED, out_dir=tmp_path,
                                     actors=actors, wallclock_limit_s=0.0))


def test_the_wallclock_does_kill_a_table_of_nothing_but_models(tmp_path):
    """正向对照：墙钟不是装饰品。缺了这条，下一条的"没被判死"分不清是规则还是死代码。"""
    res = _play(tmp_path, _nine(_AsModel))
    assert res.terminal == "aborted_wallclock", (
        f"limit=0 的全模型桌没有被判死（{res.terminal}）：闸门本身就是空的，"
        f"那么下一条的通过什么也说明不了")


def test_one_seat_of_flesh_takes_the_wallclock_off_the_game(tmp_path):
    """§15 第 3 条：3 号换成一个"人在想"的座位，同一张桌、同一个 0 秒上限就不该再被杀。"""
    actors = _nine(_AsModel)
    actors[3] = _AsHuman(3, synthesize=True, rng=random.Random(SEED * 10 + 3))
    res = _play(tmp_path, actors)
    assert res.terminal != "aborted_wallclock", res.terminal


# --------------------------------------------------------------- ④ 契约本体：那四个声明
def test_the_reserved_seat_declares_the_shape_the_orchestrator_reads():
    """`#85`：把"一期只留契约"从一句 docstring 变成一条断言。

    在这一条之前，`HumanActor` 全树零读者——`tests/test_wiring.py` 的类闸门因此需要一条"看着像桩
    就放过"的豁免，而豁免是要有证人的洞。构造它、把编排层真的会去读的四个属性钉住，就有了一个
    命名它的读者，而契约也不再依赖有人记得读那段话。`timeout_for()` 返回 `None` 是第 1 条的**前提**：
    上面那两条用本地替身 `_Seat` 测的是"编排层拿到 `None` 怎么办"，这一条测的是"这个座位声明的是
    `None`"——两件事各有一个读者，缺了后者，前者用的那个 `None` 是谁给的没人说得出。
    """
    seat = HumanActor(3)
    assert seat.seat == 3, "构造器连座位都不认，那上桌时它是谁"
    assert seat.kind in get_args(ActorKind), (
        "`kind` 不在声明过的名单里：它会经 `game.py` 写进页眉的 `actor_kinds`，而名单只有一个来源")
    assert seat.blocking is True, (
        "`blocking` 是 `wave_size` 与墙钟闸门的输入：改成 False 就是把 §15 第 2 条改回去")
    assert seat.timeout_for(Phase.NIGHT_WOLF) is None, (
        "这个座位的截止时间是『永远』不是『没填』；给它一个数，第 1 条的替身就成了唯一说法")


def test_play_stops_when_the_table_has_neither_actors_nor_a_transport(tmp_path):
    """`play()` mints its own LLM actors only for a table it builds from a transport; with
    neither argument there is no table at all. The refusal has to be one sentence at the door:
    opening a log first would leave a game file with a name and nothing in it, which the read
    side has its own rules to explain. Hence the path one level below `tmp_path` — the fixture's
    own directory exists either way, so emptiness there proves nothing about who created it."""
    out = tmp_path / "g-none"
    with contextlib.redirect_stdout(io.StringIO()):
        with pytest.raises(ValueError) as why:
            asyncio.run(game.play(cfg=Config(), deal_seed=SEED, out_dir=out))
    assert str(why.value) == "play() needs either `actors` or a `transport`", str(why.value)
    assert not out.exists(), f"门口就该停，目录却已经被摊开了：{out}"
