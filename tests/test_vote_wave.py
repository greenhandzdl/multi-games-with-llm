"""一次投票波次的三件事，全部由 `/tmp/mut73.py` 的存活变异体点名（14:41:29Z 全量确认）。

`phases.run_vote` 的 docstring 把"密封"写得很硬：票写进日志、每个座位的世界切在开局那一刻、
连投两轮的人要被记住。这三句里只有一句有读者——`test_info_isolation.py` 钉的是
`info.percept_for` 会按 `at_seq` 过滤，`test_prefix_stability.py` 钉的是"同一波座位拿到同样的
字节"，而这两条合起来仍然放得过"调用方根本没把 `as_of` 传下来"（14:37Z 读到的
`info.percept_for`：`at_seq=None` 就切在日志末行）。所以这里三处接线各钉一处：

* ①`run_vote` 把开局 seq 传给了每一个座位——手术刀 P3：把 `as_of=opened_at` 改成
  `as_of=None`，或者把 `opened_at` 换成一个大常数，第二波起前面的票就会渗进后面座位的世界。
* ②PK 复投是**第二次**切，不是沿用第一次——同一个座位在两波里拿到两个不同的世界，
  而两波各自内部一致。
* ③弃票计数有人读：写进 `state.abstain_streak` 的那个数字，最终变成下一轮发言里的一行提示。
  手术刀 P5：把 `run_vote` 里的累加改成恒 0，`forced_nominate` 就永不触发，而装配器里
  那句"你连续两轮没有实质表态了"在全仓库零断言（14:41Z grep 只有 src 命中）。

不钉的那条：`_ballots` 把 `act != "vote"` 算成弃票（变异 P1）是**等价变异**，测不出来也不需要
测——DAY_VOTE 在 `legality.HARD_PHASES` 里，闸门只放 `legal.acts | {pass}` 过（P1 的可达性
论证见报告，`test_agent_turns.py` 里已经有一条用例钉住"回到调用方的 act 一定在合法集里"）。
"""

from __future__ import annotations

import random

from wolfengine import game, info, phases
from wolfengine.agent import Agent
from wolfengine.actors import MockActor, Proposal, TurnContext
from wolfengine.config import Config
from wolfengine.events import Kind
from wolfengine.schema import Action
from wolfengine.state import Phase

SEED = 7
ALL_SEATS = list(range(1, 10))


class Scripted(MockActor):
    """票照脚本，其余轮次照 `MockActor` 的合成策略。

    只接管 DAY_VOTE，因为发言轮上有一道 `assigned_act` 硬闸门：照本宣科读不到指派，一被拒就
    多吃掉一格脚本，"第 N 格是不是第 N 波的那张票"就对不上了。合成策略本来就是负责读指派的那条路。
    """

    def __init__(self, seat: int, ballots: list[Action]) -> None:
        super().__init__(seat, synthesize=True)
        self.ballots = list(ballots)

    async def act(self, ctx: TurnContext) -> Proposal:
        if ctx.phase is Phase.DAY_VOTE and self.ballots:
            self.turns.append(ctx)
            action = self.ballots.pop(0)
            return Proposal(action=action, raw=action.model_dump_json())
        return await super().act(ctx)


def _table(tmp_path, ballots: dict[int, list[Action]]):
    """一桌停在第 1 天白天的现场：真实发牌、真实 agent，座位上是要投第几票就说第几票的替身。"""
    cfg = Config()
    state, personas, _ = game.build_game(cfg, SEED, f"g{SEED:08d}")
    log = game.open_log(cfg, state, tmp_path / "t.jsonl", ("mock",), SEED, "20260922T144500Z")
    agent = Agent(cfg=cfg, state=state, log=log, personas=personas,
                  known_ids=lambda: {f"e{e.seq}" for e in log.all()})
    actors = {s: Scripted(s, list(ballots.get(s, []))) for s in state.seats}
    table = phases.Table(cfg=cfg, state=state, log=log, agent=agent, actors=actors,
                         rng=random.Random(SEED))
    return cfg, state, log, table, actors


def _vote(who: int) -> Action:
    return Action(act="vote", target=who, evidence=[])


def _pass() -> Action:
    return Action(act="pass", evidence=[])


def _waves(actors: dict[int, Scripted], n: int) -> list[list[TurnContext]]:
    """每个座位被问着投票的那一串上下文，按波次对齐：第 k 项是第 k+1 波。

    波次只能从座位的记录里读，不能从日志里——日志有票，但没有"这张票落笔时看见了什么"。
    这里要求九个座位各自恰好被问 n 次：多一次就是有人在波次中被闸门退回后重问，那时
    "第 k 项属于同一波"这个前提已经不成立，让它先响而不是让断言悄悄变松。
    """
    per_seat = {s: [t for t in a.turns if t.phase is Phase.DAY_VOTE]
                for s, a in actors.items()}
    bad = {s: len(v) for s, v in per_seat.items() if len(v) != n}
    assert not bad, f"期望每座被问 {n} 次，实际 {bad}"
    return [[per_seat[s][k] for s in sorted(per_seat)] for k in range(n)]


# ---------------------------------------------------------------- ① 一波票一个世界
async def test_every_voter_is_asked_about_the_world_at_the_moment_the_phase_opened(tmp_path):
    """密封投票的接线点在这里：`run_vote` 传下来的那个 seq，不是过滤函数会不会过滤。

    反向对照一起写：波次结束后同样的日志必须让同样的座位看见全部九张票，否则"切在世界开局"
    和"切坏了"在断言里长得一样。
    """
    ballots = {s: ([_vote(8)] if s != 8 else [_vote(1)]) for s in ALL_SEATS}
    _cfg, _state, log, table, actors = _table(tmp_path, ballots)
    opened = log.last_seq

    await phases.run_vote(table)

    wave = _waves(actors, 1)[0]
    for ctx in wave:
        assert ctx.percept.at_seq == opened, \
            f"{ctx.seat}号的世界切在 e{ctx.percept.at_seq}，而这一波开在 e{opened}"
        seen = ctx.percept.by_kind(Kind.VOTE)
        assert not seen, f"{ctx.seat}号在自己落笔前就看见了 {len(seen)} 张票：密封失效"
    # ...and the cut is not a blank world: the same seats, read after the wave, are told all.
    after = info.percept_for(9, log.all())
    assert len(after.by_kind(Kind.VOTE)) == len(ALL_SEATS), \
        f"票写完了却只有 {len(after.by_kind(Kind.VOTE))} 张公开"


# ---------------------------------------------------------------- ② 复投是第二次切
async def test_the_pk_revote_gets_its_own_cut_after_the_first_tally_goes_public(tmp_path):
    """两波票各自密封、互不相同：第一波的票对复投公开，复投自己的票对复投不公开。

    这一条钉的是"沿用第一次的 `as_of`"——那种改法只红不出错：九个人两波都问到了，票型也
    对，只是复投的人被禁止知道刚刚平了什么票，而 PK 发言的全部信息量就是那张票型。
    """
    first = {1: _vote(3), 2: _vote(3), 4: _vote(3),
             6: _vote(5), 7: _vote(5), 8: _vote(5),
             3: _pass(), 5: _pass(), 9: _pass()}
    second = {s: _vote(3 if s in (1, 2, 4, 5, 9) else 5) for s in ALL_SEATS}
    ballots = {s: [first[s], second[s]] for s in ALL_SEATS}
    _cfg, state, log, table, actors = _table(tmp_path, ballots)

    await phases.run_vote(table)

    wave1, wave2 = _waves(actors, 2)
    assert len({c.percept.at_seq for c in wave1}) == 1, "第一波内部就没有对齐到同一个时点"
    cut1 = wave1[0].percept.at_seq
    cut2 = wave2[0].percept.at_seq
    assert cut2 > cut1, f"复投沿用了第一波的时点（{cut1} → {cut2}），票型没进任何人的世界"
    tally_at = max(e.seq for e in log.all() if e.kind == Kind.VOTE_RESULT and e.seq <= cut2)
    assert any(e.seq == tally_at for e in wave2[0].percept.events), \
        f"复投的人看不见 e{tally_at} 那张票型——PK 发言就成了无的放矢"
    for ctx in wave2:
        seen = [e for e in ctx.percept.by_kind(Kind.VOTE) if e.seq > cut2]
        assert not seen, f"{ctx.seat}号在复投落笔前看见了 {len(seen)} 张复投票"
    assert state.abstain_streak[9] == 1 and not state.is_alive(3), \
        "脚本没造出'平票 → 复投 → 3 号出局'的形状，上面几条都是空转"


# ---------------------------------------------------------------- ③ 弃票计数有人读
async def test_two_waves_of_abstention_is_a_streak_and_a_vote_breaks_it(tmp_path):
    """`abstain_streak` 的写侧：弃票 +1、真投票清零，两波都写在 `run_vote` 里。

    手术刀 P5 的另一半（`+ 1` 那支留着、重置那支删掉）也会被这条抓住：第三波 9 号投了票，
    计数却还挂在 2 上。
    """
    ballots = {s: [_pass(), _pass(), _vote(8) if s != 8 else _vote(1)] for s in ALL_SEATS}
    _cfg, _state, _log, table, actors = _table(tmp_path, ballots)

    await phases.run_vote(table)
    assert table.state.abstain_streak[9] == 1, table.state.abstain_streak
    await phases.run_vote(table)
    assert table.state.abstain_streak[9] == 2, table.state.abstain_streak
    await phases.run_vote(table)
    assert table.state.abstain_streak[9] == 0, table.state.abstain_streak
    assert all(a.turns for a in actors.values()), "有座位一波都没被问到，上面三条是空转"


async def test_the_repeat_abstainer_is_told_in_her_next_prompt_and_her_neighbour_is_not(tmp_path):
    """连投两轮的代价要走到发言提示那一行，而不是停在 `GameState` 的一个字典里。

    装配器里那句提示在全仓库零断言（14:41Z grep 只有 src 命中），而它是 plan §7
    反塌缩里唯一针对"一直不出手"的压力。对照座位同样必要：只断言 9 号拿到压力，等于只证明
    提示行会无条件出现。
    """
    ballots = {s: [_pass(), _pass()] for s in ALL_SEATS}
    ballots[3] = [_pass(), _vote(8)]
    _cfg, state, _log, table, _actors = _table(tmp_path, ballots)
    await phases.run_vote(table)
    await phases.run_vote(table)
    assert state.abstain_streak[9] == 2 and state.abstain_streak[3] == 0, state.abstain_streak

    marked = [9, 3]
    await phases.run_speeches(table, speakers=marked)
    seen = {s: [t for t in _actors[s].turns if t.phase is Phase.DAY_SPEECH][-1] for s in marked}
    assert seen[9].legal.reason_if_empty == "forced_nominate", seen[9].legal
    assert "必须点名一个座位" in seen[9].prompt.messages[2]["content"], \
        seen[9].prompt.messages[2]["content"][-400:]
    assert seen[3].legal.reason_if_empty != "forced_nominate", "刚投过票的人也被当成一直在躲"
    assert "必须点名一个座位" not in seen[3].prompt.messages[2]["content"], \
        "那句提示断言了一次没发生的连躲，没被标记的人不该读到它"
