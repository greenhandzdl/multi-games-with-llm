"""夜晚那两个"不问了"的分支：女巫两瓶都用完了、预言家无可查了。

`/tmp/mut73.py` 在整套 779 条上复核出的幸存者里，P7/P8 是这一对。两条都不是抽象洁癖：

* **P7：`if witch is None or not t.state.is_alive(witch) or t.state.witch.both_used`** 少掉最后
  一支，女巫每夜多被问一次——那一问的合法集是 `acts=("pass",)、consumables=()`（见
  `rules.legal_actions` 的 NIGHT_WITCH 分支）：一个手里没药的人被问"救谁或毒谁"，答"救"会被
  闸门退回，退回预算用完后由引擎替她 `pass`。多烧一次调用，还往日志里写一条"她自己弃了药"。
* **P8：`return out.action.target if out.action.act == "check" else None`** 少掉 `act` 那半句，
  一个 `{"act":"pass","target":3}` 就成了"查了 3 号"。这条在 `rules.resolve_night` 里有兜底
  （`already_checked`），所以变异活下来的表现不是假报告，而是 `t.blocked` 里多出一条记录——
  两条断言都得写：只断"没有 SEER_RESULT"会放过一个靠兜底而不是靠接线的答案。

两条各配一个反向对照，免得"根本没人被问"本身变成通过条件。
"""

from __future__ import annotations

import random

from wolfengine import game, phases
from wolfengine.agent import Agent
from wolfengine.actors import MockActor, Proposal, TurnContext
from wolfengine.config import Config
from wolfengine.events import Kind
from wolfengine.schema import Action
from wolfengine.state import Phase

SEED = 7
# seed 7 的牌桌与 test_golden_game.py 同一张：狼 1/2/4，女巫 5，预言家 7，猎人 9。
WITCH, SEER = 5, 7
NIGHT = {1: Action(act="discuss", target=3, evidence=[]),
         2: Action(act="kill", target=3, evidence=[])}


def _a(act: str, target: int | None = None, **kw) -> Action:
    return Action(act=act, target=target, evidence=[], **kw)


class NightActor(MockActor):
    """指定相位照脚本，其余照 `MockActor` 的合成策略。

    一次性弹出：同一相位被问第二次（闸门退回后重问）就走合成策略——那时"她这一夜被问了几次"
    由断言直接读事件条数，不由脚本兜着。
    """

    def __init__(self, seat: int, by_phase: dict[Phase, Action]) -> None:
        super().__init__(seat, synthesize=True)
        self.by_phase = dict(by_phase)

    async def act(self, ctx: TurnContext) -> Proposal:
        action = self.by_phase.pop(ctx.phase, None)
        if action is None:
            return await super().act(ctx)
        self.turns.append(ctx)
        return Proposal(action=action, raw=action.model_dump_json())


def _table(tmp_path, by_seat: dict[int, dict[Phase, Action]], *, name: str = "n.jsonl"):
    """一桌停在第 1 天夜里的现场：真实发牌、真实 agent，四个夜里角色各拿一份脚本。"""
    cfg = Config()
    state, personas, _ = game.build_game(cfg, SEED, f"g{SEED:08d}")
    log = game.open_log(cfg, state, tmp_path / name, ("mock",), SEED, "20260922T145100Z")
    agent = Agent(cfg=cfg, state=state, log=log, personas=personas,
                  known_ids=lambda: {f"e{e.seq}" for e in log.all()})
    actors = {s: NightActor(s, dict(by_seat.get(s, {}))) for s in state.seats}
    table = phases.Table(cfg=cfg, state=state, log=log, agent=agent, actors=actors,
                         rng=random.Random(SEED))
    return cfg, state, log, table, actors


def _night_turns_for(log, seat: int) -> int:
    return sum(1 for e in log.all()
               if e.kind == Kind.NIGHT_ACTION and e.actor == seat and e.payload.get("act"))


# ------------------------------------------------------------------------- ① 女巫的两瓶药
async def test_a_witch_out_of_both_potions_is_not_asked_again(tmp_path):
    """两瓶都用完 = 那一夜她根本没有决定要做：不问是省一次调用，也是不写假弃药。"""
    script = {**NIGHT, WITCH: {Phase.NIGHT_WITCH: _a("save", potion="save")},
                SEER: {Phase.NIGHT_SEER: _a("check", 6)}}
    _cfg, state, log, table, _actors = _table(tmp_path, script, name="used.jsonl")
    state.witch.save_left = 0
    state.witch.poison_left = 0

    await phases.run_night(table)

    assert _night_turns_for(log, WITCH) == 0, "两瓶药都空了，女巫还是被问了一夜"
    assert table.blocked == [], table.blocked
    assert any(e.kind == Kind.WOLF_CHAT for e in log.all()), "狼队没被问，上面的 0 是空转"


async def test_a_witch_with_one_potion_left_is_still_asked(tmp_path):
    """上一条的反向对照：守卫不能是"从来不问女巫"，否则两条一起绿。"""
    script = {**NIGHT, WITCH: {Phase.NIGHT_WITCH: _a("save", potion="save")},
                SEER: {Phase.NIGHT_SEER: _a("check", 6)}}
    _cfg, state, log, table, _actors = _table(tmp_path, script, name="one_left.jsonl")
    state.witch.save_left = 1
    state.witch.poison_left = 0

    res = await phases.run_night(table)

    assert _night_turns_for(log, WITCH) == 1
    assert res.used_save and res.peace, res
    assert any(e.kind == Kind.NOTICE for e in log.all()), "问了却没告诉她倒了谁"


async def test_a_self_inconsistent_potion_is_kept_as_the_refused_answer_not_as_a_fact(tmp_path):
    """`{"act":"save","potion":"poison"}` 被闸门退回，日志里留下的是**被退回的那份原文**。

    `#114` 之前 `_write` 还把 `action.potion` 抄进 payload，于是"她用了哪瓶"有两个写者：一个是
    `act`，一个是刚被 `potion_act_mismatch` 校验过、按定义不可能和 `act` 不同的那一格。真日志上
    存着的 7 格 potion 与 act 逐字节相同（00:48:53Z 数的），所以这份抄写从来没有第二个读者。
    删掉它不丢证据：被拒的答复整条留在 `attempts` 里，那才是模型自相矛盾的唯一证物。
    """
    script = {**NIGHT, WITCH: {Phase.NIGHT_WITCH: _a("save", potion="poison")},
              SEER: {Phase.NIGHT_SEER: _a("check", 6)}}
    _cfg, _state, log, table, _actors = _table(tmp_path, script, name="mismatch.jsonl")

    await phases.run_night(table)

    witch = [e for e in log.all() if e.kind == Kind.NIGHT_ACTION and e.actor == WITCH]
    assert len(witch) == 1, witch
    assert "potion" not in witch[0].payload, "act 之外不该有第二支笔记下她用了哪一瓶"
    assert witch[0].payload["act"] == "save", witch[0].payload["act"]
    assert [a["raw"] for a in witch[0].attempts] == [
        '{"act":"save","target":null,"speech":"","evidence":[],"belief":null,"potion":"poison"}'], \
        witch[0].attempts
    assert table.blocked == [], "退回由 attempts 作证，不是由 blocked 作证——两格不是一回事"


# --------------------------------------------------------------------- ② 预言家的"无可查"
async def test_a_seer_with_nothing_left_to_check_reports_no_check(tmp_path):
    """`{"act":"pass","target":3}` 不是"查了 3 号"：既不发布结果，也不留下 blocked 记录。

    夹具把所有活人都记成已查，于是她这一轮的合法集只剩 `pass`（`allow_pass=not unchecked`）。
    带一个旧号码的弃票是小模型在这一轮最常见的一种形状。
    """
    script = {**NIGHT, WITCH: {Phase.NIGHT_WITCH: _a("pass")},
                SEER: {Phase.NIGHT_SEER: _a("pass", 3)}}
    _cfg, state, log, table, _actors = _table(tmp_path, script, name="all_checked.jsonl")
    state.witch.save_left = state.witch.poison_left = 0
    state.seer_results = {s: "good" for s in state.alive_seats if s != SEER}

    res = await phases.run_night(table)

    assert res.seer_report is None, res
    assert not any(e.kind == Kind.SEER_RESULT for e in log.all()), "无可查的一轮查出了结果"
    assert table.blocked == [], f"接线漏了，是 `rules` 的兜底在替它挡：{table.blocked}"


async def test_a_real_check_still_reaches_the_seer_channel(tmp_path):
    """上一条的反向对照：`_seer` 不能退化成"永远返回 None"，否则上面三条一起绿。"""
    script = {**NIGHT, WITCH: {Phase.NIGHT_WITCH: _a("pass")},
                SEER: {Phase.NIGHT_SEER: _a("check", 6)}}
    _cfg, _state, log, table, _actors = _table(tmp_path, script, name="checked.jsonl")

    res = await phases.run_night(table)

    assert res.seer_report is not None and res.seer_report[0] == 6, res
    reported = [e for e in log.all() if e.kind == Kind.SEER_RESULT]
    assert len(reported) == 1 and reported[0].payload["target"] == 6, reported
    assert reported[0].actor == SEER, reported[0]
    assert table.blocked == [], table.blocked
