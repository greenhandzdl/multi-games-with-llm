"""Engine belief: the ledger, the 对跳 inference, and the decay.

The claim here that deserves the most suspicion is "`_apply_contested_seer_claims` is what
makes the ledger worth having". It was dead code for most of this session: it keyed off an
event kind nothing ever emits, so two seats could both jump as seer and neither would be
discounted. These tests are what make that claim checkable.
"""

from __future__ import annotations

from wolfengine import belief
from wolfengine.events import Event, Kind, seats

DAY = {"day": 1, "phase": "day_speech"}


def sp(seq: int, actor: int, text: str, *, act: str = "accuse", target: int | None = None, day: int = 1) -> Event:
    return Event(seq=seq, kind=Kind.SPEECH, day=day, phase="day_speech", visibility="all",
                 actor=actor, payload={"text": text, "act": act, "target": target})


# ------------------------------------------------------------------ claimed_role dictionary
def test_a_first_person_jump_is_a_role_claim():
    assert belief.claimed_role("我是预言家，昨晚验的5号。") == "seer"
    assert belief.claimed_role("本人女巫，解药已经用掉了。") == "witch"
    assert belief.claimed_role("我是猎人，谁动我我跟谁走。") == "hunter"


def test_reporting_someone_elses_claim_is_not_a_claim_of_your_own():
    """The failure this guard exists for: a villager repeating "1号说他是预言家" must not
    put the real seer into a contested state."""
    assert belief.claimed_role("1号说他是预言家。") is None
    assert belief.claimed_role("跳预言家的那个肯定是狼。") is None


def test_a_role_word_in_another_clause_does_not_count():
    """First person and role word have to share a clause, or 我相信5号，他是预言家 would
    make seat 1 the claimant."""
    assert belief.claimed_role("我相信5号，他说他是预言家。") is None


def test_denying_a_role_is_not_claiming_it():
    """Not handled, and recorded as a known limit rather than a solved case: the deny
    sentence costs a credibility bump, the false 对跳 costs the ledger's meaning."""
    assert belief.claimed_role("我不是预言家，别乱扣帽子。") == "seer"  # documented miss


# ------------------------------------------------------------------- the ledger from events
def test_speech_weight_follows_the_assigned_act_not_the_words():
    """An accuse with a target moves suspicion; a listen moves nothing, however long it is."""
    log_events = [
        Event(seq=1, kind=Kind.GAME_START, day=1, phase="night_wolf", visibility="all", payload={"seats": [1, 2, 3]}),
        sp(2, 2, "先听大家说。", act="listen"),
        sp(3, 3, "我怀疑1号。", act="accuse", target=1),
    ]
    st = belief.build_belief(1, log_events)
    assert st.suspicion.get(2, 0.0) == 0.0
    assert st.suspicion[1] > 0.0


def test_two_seer_jumps_contest_each_other():
    evs = [
        Event(seq=1, kind=Kind.GAME_START, day=1, phase="night_wolf", visibility="all", payload={"seats": [1, 2, 3, 4]}),
        sp(2, 2, "我是预言家。"),
        sp(3, 3, "我才是预言家。"),
    ]
    st = belief.build_belief(1, evs)
    contested = [c for c in st.claims if c.label == "contested_seer"]
    assert contested and contested[0].note == "2,3"
    assert st.credibility[2] < 1.0 and st.credibility[3] < 1.0


def test_one_seer_jump_is_not_contested():
    evs = [
        Event(seq=1, kind=Kind.GAME_START, day=1, phase="night_wolf", visibility="all", payload={"seats": [1, 2, 3]}),
        sp(2, 2, "我是预言家。"),
    ]
    st = belief.build_belief(1, evs)
    assert not any(c.label == "contested_seer" for c in st.claims)
    assert st.credibility[2] > 1.0, "an uncontested seer jump should raise credibility, not lower it"


def test_a_contested_seers_accusation_weighs_less_than_the_same_uncontested_one():
    """The whole point of credibility: identical words, and the discount is what separates
    a lone jump from two seats jumping on each other."""
    start = Event(seq=1, kind=Kind.GAME_START, day=1, phase="night_wolf", visibility="all",
                  payload={"seats": [1, 2, 3, 5]})
    alone = belief.build_belief(1, [start, sp(2, 2, "我是预言家。"), sp(4, 2, "5号是狼。", act="accuse", target=5)])
    rival = belief.build_belief(1, [start, sp(2, 2, "我是预言家。"), sp(3, 3, "我才是预言家。"),
                                    sp(4, 2, "5号是狼。", act="accuse", target=5)])
    assert alone.suspicion[5] > 0
    assert rival.suspicion[5] < alone.suspicion[5], (rival.suspicion[5], alone.suspicion[5])
    assert abs(rival.suspicion[5] - alone.suspicion[5] / 2) < 1e-9, "对跳 halves, exactly once"


def test_suspicion_decays_with_age_but_not_to_zero():
    evs = [Event(seq=1, kind=Kind.GAME_START, day=1, phase="night_wolf", visibility="all", payload={"seats": [1, 2, 3]})]
    evs += [sp(2, 2, "我怀疑3号。", act="accuse", target=3, day=1)]
    evs += [Event(seq=i, kind=Kind.PHASE, day=d, phase="day_speech", visibility="all", payload={"text": ""})
            for d, i in ((2, 3), (3, 4))]
    st = belief.build_belief(1, evs)
    assert st.day == 3
    assert 0 < st.suspicion[3] < 0.7 * belief.ACT_WEIGHT["accuse"] * 3


def test_dead_seats_leave_the_ranking():
    """A fallback that nominates a corpse is not a legal action, so the ranking must not
    offer one — the engine's default pick reads this same list."""
    evs = [
        Event(seq=1, kind=Kind.GAME_START, day=1, phase="night_wolf", visibility="all", payload={"seats": [1, 2, 3]}),
        sp(2, 2, "我怀疑3号。", act="accuse", target=3),
        Event(seq=3, kind=Kind.DEATH, day=1, phase="day_vote", visibility="all",
              payload={"seat": 3, "cause": "exiled"}),
    ]
    st = belief.build_belief(1, evs)
    assert 3 not in st.suspicion
    assert st.dead == (3,)


def test_the_observer_is_never_its_own_suspect_from_its_own_words():
    evs = [
        Event(seq=1, kind=Kind.GAME_START, day=1, phase="night_wolf", visibility="all", payload={"seats": [1, 2]}),
        sp(2, 1, "我怀疑我自己。", act="accuse", target=1),
    ]
    st = belief.build_belief(1, evs)
    assert not [c for c in st.claims if c.actor == 1 and c.label == "accuse"]


def test_the_card_holds_facts_and_no_advice():
    st = belief.BeliefState(observer=1, day=2, alive=(1, 2, 3),
                           claims=[belief.Claim(4, 1, 2, "accuse", 3, "票型可疑")],
                           suspicion={3: 2.0})
    card = belief.render_card(st)
    assert "票型可疑" in card and "3号" in card
    for advice in ("怀疑", "投", "应该") :
        assert advice not in card.replace("可疑", ""), (advice, card)
