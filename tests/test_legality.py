"""rung 4：引擎什么时候有权拒绝一个动作，以及拒绝不了的时候要留下什么。

两级严重性是设计而不是偏好（plan §2 原则 1）：夜里和投票是硬闸门（重试一次，代价 ~40
token），白天发言只标记不阻断（否则一大半轮次被打断，而且"昨晚我好像听见动静"这句话
本来就该被说出来——引擎的工作是把这句话记成谎言，不是闭掉它的嘴）。

来源标注是刻意的：plan §11 要"4 条真原句 + 8 条构造变体"。真端点样本要 M2 才回采得到
（`tests/fixtures/` 现在还是空的），所以现在只有 2026-09-20 温度扫描里村民那句
"昨晚我听到了狼叫"是真句。其余全部标明 constructed，别让一条用例看起来像实测。
"""

from __future__ import annotations

import pytest

from wolfengine import info, legality, roles, schema, state
from wolfengine.events import Event, Kind
from wolfengine.legality import check_action, default_action, impossible_percept


def _ev(seq: int, kind: str, *, visibility="all", actor=None, **payload) -> Event:
    return Event(seq=seq, kind=kind, day=1, phase="day_speech", visibility=visibility,
                 actor=actor, payload=payload)


VILLAGER_TURN = info.percept_for(4, [
    _ev(1, Kind.SPEECH, actor=1, text="我觉得3号有问题", act="accuse", target=3),
    _ev(2, Kind.DEATH, seat=3, cause="exiled"),
], at_seq=2)

SPEECH_LEGAL = state.LegalSet(
    acts=("accuse", "defend", "align", "probe", "pivot", "listen"),
    targets=frozenset({1, 2, 3, 5}), assigned_act="accuse")


def act(**kw):
    return schema.Action.model_validate({"act": "accuse", **kw})


# ---------------------------------------------------------------- 幻觉私有信息
@pytest.mark.parametrize("speech,role,expect", [
    # real: 2026-09-20 calibration on this endpoint, a villager seat produced exactly this
    ("昨晚我听到了狼叫，肯定有狼。", "villager", True),
    # constructed variants, each stressing one clause of the dictionary
    ("夜里我隐约听到有人倒下了。", "villager", True),
    ("半夜我闻到了一点不对劲。", "villager", True),
    ("昨晚我注意到5号一直没说话。", "villager", True),
    ("3号说他昨晚听到了动静。", "villager", False),        # 转述，不是第一人称
    ("白天我听到投票的声音很整齐。", "villager", False),      # 感知动词但没有夜指词
    ("今晚我要是女巫我就毒2号。", "villager", False),         # 今晚 + 我，但没有感知动词
    ("昨晚我听到了狼叫。", "seer", True),                   # 预言家夜里睁眼，但"听到狼叫"仍不可能
    ("我昨晚验了3号，他是狼。", "seer", False),              # 自己的夜间行动，不是ambient感知
], ids=[
    "real-villager-wolfhowl", "constructed-heard-collapse", "constructed-smelled",
    "constructed-noticed-silence", "constructed-reported-speech", "constructed-daytime",
    "constructed-hypothetical", "constructed-seer-heard", "constructed-seer-own-check"])
def test_impossible_percept_flags_only_private_night_perception(speech, role, expect):
    got = impossible_percept(speech, role=role)
    assert bool(got) is expect, f"{speech!r} as {role} -> {got!r}"


def test_the_hypothetical_witch_line_is_not_a_hallucination():
    """A wolf *pretending* to be the witch is the game, not a bug: the dictionary must key on
    what the *speaker's* role could perceive, and a villager claiming a night fact is already
    covered above. This case exists so nobody "improves" the flag into a lie detector."""
    assert impossible_percept("我是女巫，昨晚我救了7号。", role="villager") is None


def test_a_known_false_positive_is_recorded_as_one():
    """"注意到昨晚的票型" 会被词典标成不可能感知——票型是公开信息。

    这条断言的是**当前会误报**，不是"应该误报"。M4 给 `impossible_percept` 加宾语检查
    （感知动词 + 公开宾语 → 不算，不进分子）时，这条会失败，届时把它删掉。
    宁可现在钉住一个已知缺陷，也不要报告里写"词典零误报"。
    """
    assert impossible_percept("我注意到昨晚的票型很奇怪。", role="villager") is not None


# ------------------------------------------------------------------------ 硬闸门
def night(acts, targets, **kw):
    return state.LegalSet(acts=tuple(acts), targets=frozenset(targets), **kw)


def test_a_hard_phase_refuses_an_illegal_target_and_renames_the_option_space():
    legal = night(("kill",), {1, 2, 3})
    v = check_action(schema.Action(act="kill", target=9), legal=legal,
                     percept=VILLAGER_TURN, phase=state.Phase.NIGHT_WOLF)
    assert not v.ok and "target_not_legal:9" in v.reason
    assert "只能 [1, 2, 3]" in v.reason, "the retry prompt must hand back the legal list"


def test_a_soft_phase_flags_the_same_violation_instead_of_refusing_it():
    legal = state.LegalSet(acts=("accuse",), targets=frozenset({1, 2}))
    v = check_action(schema.Action(act="accuse", target=7, speech="7号就是狼"), legal=legal,
                     percept=VILLAGER_TURN, phase=state.Phase.DAY_SPEECH)
    assert v.ok, "speech must never be blocked"
    assert any("target_not_legal:7" in f for f in v.flags), v.flags


def test_an_invented_citation_id_is_refused_even_in_speech():
    """The citation is addressed to the engine, not to the other players, so refusing it
    costs nothing and stops the transcript from recording a lie as evidence.

    `agent.py` always supplies `known_ids` (it holds the log), so the empty-set case below
    is not what runs in a game — it is what runs if someone forgets to wire it, and the
    failure direction there is "call everything invented", which is the loud one.
    """
    legal = state.LegalSet(acts=("accuse",), targets=frozenset({1}))
    v = check_action(act(target=1, evidence=["e999"]), legal=legal, percept=VILLAGER_TURN,
                     phase=state.Phase.DAY_SPEECH, known_ids=frozenset({"e1", "e2"}))
    assert not v.ok and v.citation_stats["invented"] == ["e999"]


def test_without_known_ids_an_unknown_id_is_only_flagged():
    legal = state.LegalSet(acts=("accuse",), targets=frozenset({1}))
    v = check_action(act(target=1, evidence=["e999"]), legal=legal, percept=VILLAGER_TURN,
                     phase=state.Phase.DAY_SPEECH)
    assert v.ok, "known_ids=None cannot tell a lie from a leak, so it must not refuse"
    assert v.citation_stats["not_visible"] == ["e999"]


def test_a_citation_to_someone_elses_private_event_is_a_leak_not_a_lie():
    """`invented` and `not_visible` are different failures with different owners: one is the
    model, the other is our isolation layer. Without `known_ids` the two collapse together."""
    legal = state.LegalSet(acts=("accuse",), targets=frozenset({1}), assigned_act="accuse")
    a = act(evidence=["e77"])
    v = check_action(a, legal=legal, percept=VILLAGER_TURN, phase=state.Phase.DAY_VOTE,
                     known_ids=frozenset({"e1", "e2", "e77"}))
    assert v.citation_stats["not_visible"] == ["e77"] and not v.citation_stats["invented"]
    v2 = check_action(a, legal=legal, percept=VILLAGER_TURN, phase=state.Phase.DAY_VOTE)
    assert v2.citation_stats["invented"] == [], "without known_ids a real event id must not " \
                                                "be called a fabrication"


def test_malformed_citations_are_counted_separately():
    legal = state.LegalSet(acts=("accuse",), targets=frozenset({1}))
    v = check_action(act(evidence=["第三条", "e1"]), legal=legal, percept=VILLAGER_TURN,
                     phase=state.Phase.DAY_SPEECH)
    assert v.citation_stats["malformed"] == ["第三条"]
    assert v.citation_stats["valid"] == ["e1"]


def test_abstaining_is_never_a_violation_when_the_rules_allow_it():
    legal = state.LegalSet(acts=("vote",), targets=frozenset({1, 2}), allow_pass=True)
    assert check_action(schema.Action(act="pass"), legal=legal, percept=VILLAGER_TURN,
                        phase=state.Phase.DAY_VOTE).ok


def test_a_used_up_potion_is_refused_by_name():
    legal = state.LegalSet(acts=("save", "poison"), targets=frozenset({2}),
                           consumables={"poison": 1})
    v = check_action(schema.Action(act="save", potion="save"), legal=legal,
                     percept=VILLAGER_TURN, phase=state.Phase.NIGHT_WITCH)
    assert not v.ok and "potion_unavailable:save" in v.reason


def test_potion_and_act_must_agree():
    legal = state.LegalSet(acts=("save", "poison"), targets=frozenset({2}),
                           consumables={"save": 1, "poison": 1})
    v = check_action(schema.Action(act="poison", target=2, potion="save"), legal=legal,
                     percept=VILLAGER_TURN, phase=state.Phase.NIGHT_WITCH)
    assert not v.ok and "potion_act_mismatch" in v.reason


def test_a_targetless_act_may_carry_no_target():
    legal = state.LegalSet(acts=("defend",), targets=frozenset({2, 3}))
    assert check_action(schema.Action(act="defend", target=3, speech="我是好人"), legal=legal,
                        percept=VILLAGER_TURN, phase=state.Phase.DAY_SPEECH).ok


def test_an_act_that_requires_a_target_but_gives_none_is_refused():
    legal = state.LegalSet(acts=("accuse",), targets=frozenset({2}))
    v = check_action(schema.Action(act="accuse", speech="有人装得太极其容易"), legal=legal,
                     percept=VILLAGER_TURN, phase=state.Phase.DAY_VOTE)
    assert not v.ok and "target_required_for:accuse" in v.reason


# ---------------------------------------------------------------- 指派动作
def test_ignoring_the_assigned_act_is_refused_even_though_speech_is_soft():
    """plan §4 rung4 与 §7 第 1 条：`act` 是写给引擎的字段，不是说给同桌听的话。

    两级严重性管的是发言内容——"昨晚我好像听见动静"必须说得出口，引擎只负责把它记成谎言。
    标签不是内容：拒一次只多花一次重问，重问之后模型照样自由组织语言。此前这里连比较都
    没有，required speech act 整个反塌缩机制是空转的，M3 的 `passivity_rate` 量的就只是
    模型自己的选择偏差，而不是指派生效之后的行为。
    """
    v = check_action(schema.Action(act="listen", speech="我先听听大家怎么说。"),
                     legal=SPEECH_LEGAL, percept=VILLAGER_TURN, phase=state.Phase.DAY_SPEECH)
    assert not v.ok
    assert any(x.startswith("act_not_as_assigned") for x in v.violations), v.reason


def test_a_speech_turn_that_takes_the_assigned_act_raises_nothing():
    """反向：闸门不能把服从指派也判成违规，否则每一次发言都要烧掉一次重问。"""
    v = check_action(act(target=3, speech="3号从刚才那轮开始就一直躲，我要听他解释。"),
                     legal=SPEECH_LEGAL, percept=VILLAGER_TURN, phase=state.Phase.DAY_SPEECH)
    assert v.ok, v.reason


# ---------------------------------------------------------------- 引擎代打的动作
CASES = [
    ("wolf night", night(("kill",), {2, 3, 4}), state.Phase.NIGHT_WOLF),
    ("seer night", night(("check",), {2, 3, 4}), state.Phase.NIGHT_SEER),
    ("witch", state.LegalSet(acts=("save", "poison"), targets=frozenset({2}),
                             consumables={"save": 1, "poison": 1}, allow_pass=True),
     state.Phase.NIGHT_WITCH),
    ("vote", state.LegalSet(acts=("vote",), targets=frozenset({2, 3}), allow_pass=True),
     state.Phase.DAY_VOTE),
    ("speech", SPEECH_LEGAL, state.Phase.DAY_SPEECH),
    ("last words", state.LegalSet(acts=("last_words",), allow_pass=True),
     state.Phase.LAST_WORDS),
    ("hunter", night(("shoot",), {2, 3}, allow_pass=True), state.Phase.HUNTER_SHOT),
    ("empty", state.LegalSet(reason_if_empty="dead"), state.Phase.DAY_SPEECH),
]


@pytest.mark.parametrize("name,legal,phase", CASES, ids=[c[0] for c in CASES])
def test_the_default_action_is_itself_legal(name, legal, phase):
    """A fallback the gate then refuses would log an engine choice as a model violation."""
    d = default_action(legal, [3, 2])
    v = check_action(d, legal=legal, percept=VILLAGER_TURN, phase=phase, role="villager")
    assert v.ok, f"{name}: engine default {d} refused: {v.reason}"


def test_the_wolves_always_knife_on_a_fallback():
    """A silent wolf turn is a free night for the good team, and the audience cannot tell an
    engine pass from a model pass — so the default must still be a kill."""
    d = default_action(night(("kill",), {5, 6}), [])
    assert d.act == "kill" and d.target == 5


def test_the_engine_never_invents_a_ballot():
    """The tally is a public fact nine seats reason over. Corrupting it to spare the game a
    slow day is worse than a slow day; an honest abstention is the floor."""
    legal = state.LegalSet(acts=("vote",), targets=frozenset({2, 3}), allow_pass=True)
    assert default_action(legal, [2]).act == "pass"


def test_a_speech_fallback_keeps_the_assigned_act_so_passivity_is_not_built_in():
    legal = state.LegalSet(acts=("accuse", "listen"), targets=frozenset({2, 3}),
                           assigned_act="accuse")
    d = default_action(legal, [3])
    assert d.act == "accuse" and d.target == 3


def test_the_engine_default_cites_nothing_it_cannot_show():
    """`evidence=[]` is a *statistic* (uncited_claim_rate), never a violation. A fallback
    that invented ids would make M4 count the engine as the worst hallucinator at the table."""
    d = default_action(SPEECH_LEGAL, [1])
    assert d.evidence == []
    assert d.speech == ""


def test_roles_module_agrees_with_the_gates_targetless_list():
    """`discuss` is wolf-chat prose: it needs no target, and if it were missing from
    TARGETLESS_ACTS every wolf discussion would burn its one retry and end in a fallback."""
    assert "discuss" in legality.TARGETLESS_ACTS
    legal = state.LegalSet(acts=("discuss",), targets=frozenset({4, 5}))
    assert check_action(schema.Action(act="discuss", speech="刀4号"), legal=legal,
                        percept=VILLAGER_TURN, phase=state.Phase.NIGHT_WOLF).ok
    assert roles.BOARD_9.spec("wolf").night_slot == 1
