"""替身桌那四条 stand-in 出口，和模型席失败的那一条：每格一个真的座位、一张真的卡片。

执行普查说这五格在离线套件里从没落到过。`_synthesize` 的四条出口此前只在 `--mock` 整局里
被走过，而整局用例是子进程跑的——子进程里的证人跨不过普查的 trace 窗口；`LlmActor.act` 的
失败出口则从来没有离线用例直接调用过它，端点那本用的是桩 transport，接不到这一层。

这一本走真装配：`info.percept_for` → `belief.build_belief` → `assemble.assemble` →
`actors.TurnContext`，与 `test_info_isolation.py` 给的是同一个上下文形状——手搓一个空
prompt 也能让这些分支跑到，但那就不再是"这张桌子上的这个座位会看到的东西"。断言只挑那些
**只有这一格成立才说得通**的事实：某个 act 从哪个出口出来、某个 belief 是不是 None。整局
"经过"这些格子不等于有人读过它，经过与读过是两件事。
"""

from __future__ import annotations

import asyncio
import random

from wolfengine import actors, assemble, belief, info, persona, state
from wolfengine.config import Config
from wolfengine.events import Event, Kind, PUBLIC, seats
from wolfengine.llm import CallResult

CFG = Config()


def _other(seat: int) -> int:
    return 3 if seat != 3 else 4


def _ctx(seat: int, legal: state.LegalSet):
    """What this seat is really handed at this moment: its own deal plus one public accusation.

    The accusation carries `meta` because `belief.build_belief` only counts a speech as a
    decision when the model that made it is recorded — a belief built from a meta-less log is
    empty, and an empty belief makes `pick` come out of the rng instead of the ranking, which
    would let these tests pass without the seat having read anything.
    """
    other = _other(seat)
    evs = (
        Event(seq=1, kind=Kind.DEAL, day=1, phase="day_speech", visibility=seats(seat),
              actor=seat, payload={"role": "wolf", "teammates": [other]}),
        Event(seq=2, kind=Kind.SPEECH, day=1, phase="day_speech", visibility=PUBLIC,
              actor=other,
              payload={"meta": {"model": "witness"}, "text": "我怀疑他",
                       "act": "accuse", "target": seat}),
    )
    percept = info.percept_for(seat, evs, at_seq=evs[-1].seq)
    bs = belief.build_belief(seat, percept.events)
    p = assemble.assemble(
        cfg=CFG, percept=percept, seat_role=percept.role(),
        persona=persona.PersonaParams(), belief=bs, legal=legal,
        phase=state.Phase.DAY_SPEECH)
    return actors.TurnContext(
        seat=seat, role=percept.role(), phase=state.Phase.DAY_SPEECH,
        percept=percept, legal=legal, persona=persona.PersonaParams(), prompt=p, belief=bs)


def _synth(seat: int, legal: state.LegalSet, seed: int):
    mock = actors.MockActor(seat, synthesize=True, rng=random.Random(seed))
    return asyncio.run(mock.act(_ctx(seat, legal)))


def test_a_dying_seat_with_no_way_out_of_speaking_uses_its_words():
    """遗言席没有"过"这个选项时，替身必须留下话，而不是留一个空回合。"""
    legal = state.LegalSet(acts=("last_words",), targets=frozenset({2, 4}), allow_pass=False)
    out = _synth(1, legal, 11)
    assert out.action.act == "last_words"
    assert out.action.speech


def test_a_round_whose_only_verb_is_pass_produces_no_speech():
    """引擎把"过"当成唯一可给的动词时，替身不能自己发明一个动词。"""
    legal = state.LegalSet(acts=("pass",), targets=frozenset({2, 4}), allow_pass=True,
                           assigned_act="pass")
    out = _synth(1, legal, 12)
    assert out.action.act == "pass"
    assert out.action.speech == ""


def test_a_repeat_abstainer_forced_to_nominate_stops_defending_itself():
    """连弃两轮的压力是把 defend 改写成 accuse，不是往 persona 上贴一个临时标记。"""
    legal = state.LegalSet(acts=("defend", "accuse"), targets=frozenset({2, 4}),
                           assigned_act="defend", reason_if_empty="forced_nominate")
    out = _synth(1, legal, 13)
    assert out.action.act == "accuse"
    assert out.action.target in {2, 4}


def test_a_seat_with_nobody_left_to_rank_states_no_belief():
    """没有别的座位可点名时，说出来的 belief 是"没有"，而不是一个空的 suspects 清单。"""
    legal = state.LegalSet(acts=("listen",), targets=frozenset())
    out = _synth(1, legal, 14)
    assert out.action.act == "listen"
    assert out.action.belief is None


class _RefusingLlm:
    """`complete` 带着错误回来——端点拒答时 transport 就是这个形状。"""

    def __init__(self) -> None:
        self.turns: list[list[dict[str, str]]] = []

    async def complete(self, messages, **kwargs) -> CallResult:
        self.turns.append(messages)
        return CallResult(text="", error="transport_connect_failed")


def test_a_refused_call_is_recorded_as_a_failure_and_not_as_an_action():
    """拒答不能塌成"模型决定过"：failure 有名字、action 为空、发出去的那份参数仍在。"""
    llm = _RefusingLlm()
    seat = actors.LlmActor(llm, CFG, 1)
    legal = state.LegalSet(acts=("listen",), targets=frozenset({2}))
    out = asyncio.run(seat.act(_ctx(1, legal)))
    assert out.failure == "transport_connect_failed"
    assert out.action is None
    assert out.request_meta["model"] == CFG.model
    assert llm.turns == [_ctx(1, legal).prompt.messages]
