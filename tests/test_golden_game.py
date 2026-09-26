"""A hand-authored transcript, asserted event for event.

`--mock` games are synthesised: nobody authored them, so they can only answer "did the
state machine survive". This file answers "did it do the *right* thing", which needs an
author. The script below is a complete 2-day game chosen to walk every branch of the
orchestration layer that a plan-critical claim rests on:

  night 1   a wolf proposal + a *different* wolf's knife + the witch's save + the seer's check
  day 1     nine speeches (one private citation, one invented citation, one impossible
            percept, one pass), a tally, an exile and its 遗言
  night 2   the proposal rotates, the knife finds the hunter, the witch poisons, the seer
            checks again
  day 2     a three–three tie, a PK round with its own speeches and revote, an exile, and
            the hunter's queued shot ending the game

Everything is positional: a seat answers from its own list, in order, and running out is
silence rather than a guess. That is the point. If the engine changes who it asks, or how
often, the scripted seats fall out of step and this file fails — which is the only way to
tell "the rules were refined" apart from "the rules silently moved".

The numbers pinned at the bottom were *measured from this fixture*, not derived. They exist
so a later change to `metrics.py` shows up here as a diff rather than as a quiet shift in a
batch report. Both halves are pinned: the text-level helpers and the log-shaped M1–M8
functions, each with a reverse control where the assertion could otherwise be satisfied by a
metric that never fires.
"""

from __future__ import annotations

import asyncio
import math
import statistics
from pathlib import Path

import pytest

from wolfengine import compress, game, metrics, phases
from wolfengine.actors import MockActor
from wolfengine.config import Config
from wolfengine.events import Event, EventLog, Kind, empty_notice, meta_notice
from wolfengine.schema import Action, Belief, Suspect

SEED = 7
# seed 7 deals: wolves 1/2/4, witch 5, villagers 3/6/8, seer 7, hunter 9.
WOLVES = (1, 2, 4)
GOOD = (3, 5, 6, 7, 8, 9)


def b(speaker: int, *seats: int) -> Belief:
    """Stated belief with a `why` that names its own author.

    The string is deliberately attributable (see
    test_no_seat_ever_reads_another_seats_private_ranking) so a leak of
    one seat's private ranking into another seat's prompt cannot be confused with a match
    that was always public.
    """
    return Belief(suspects=[Suspect(seat=s, why=f"{speaker}号私下判{s}号") for s in seats])


def A(act: str, target: int | None = None, speech: str = "", *,
      evidence: list[str] | None = None, belief: Belief | None = None,
      potion: str | None = None) -> Action:
    return Action(act=act, target=target, speech=speech, evidence=evidence or [],
                  belief=belief, potion=potion)  # type: ignore[arg-type]


SCRIPTS: dict[int, list[Action]] = {
    # ------------------------------------------------------------------ 1号 狼
    1: [
        A("discuss", 3, "刀3号，他发言太像神牌。"),                        # night 1 proposal
        A("defend", 7, "7号拿一句含糊话压人，什么都不肯说清，这更像在躲。",
          evidence=["e25"], belief=b(1, 7, 3)),                            # day 1
        A("vote", 8),                                                       # day 1 ballot
        A("last_words", speech="票是谁带的，今天之后大家会记得。"),          # exiled
    ],
    # ------------------------------------------------------------------ 2号 狼
    2: [
        A("kill", 3),                                                       # night 1 knife
        A("pivot", 3, "本来我盯着别人，现在改看3号，他连态度都不给。",
          belief=b(2, 7, 5)),        # the one designed belief/action divergence
        A("vote", 8),
        A("kill", 9),                                                       # night 2 knife
        A("accuse", 3, "3号昨天躲了一天，今天还在躲。", evidence=["e19"], belief=b(2, 3)),
        A("vote", 3),                                                       # day 2, first ballot
        A("accuse", 3, "我没什么可补充的，票给他。", belief=b(2, 3)),         # PK speech
        A("vote", 3),                                                       # PK revote
    ],
    # ------------------------------------------------------------------ 3号 民
    3: [
        A("listen", speech="我先不点名，听一圈再说。"),                       # the one passive turn
        A("vote", 1),
        A("accuse", 2, "2号今天急着给我定性，就是他。", evidence=["e24"], belief=b(3, 2, 6)),
        A("vote", 2),
        A("accuse", 2, "我昨天听了一圈，今天不听了。", belief=b(3, 2)),        # PK speech
        A("vote", 2),
        A("last_words", speech="票型会记住今天。"),
    ],
    # ------------------------------------------------------------------ 4号 狼
    4: [
        # Quotes 6号's claim without repeating a perception verb: "夜里+听见" inside the
        # speaker's own first-person window is the dictionary's known false positive, and
        # this transcript is meant to contain exactly one impossible percept.
        A("defend", 3, "6号那套“昨晚有声音”的说法站不住，我倒觉得3号开口太慢。",
          belief=b(4, 3, 9)),
        A("vote", 8),
        A("discuss", 9, "刀9号，他票得太快了。"),                            # night 2 proposal
    ],
    # ------------------------------------------------------------------ 5号 女巫
    5: [
        A("save", potion="save"),                                           # night 1
        A("defend", speech="昨夜那一步我不后悔，今天看票。", belief=b(5, 1, 2)),
        A("vote", 1),
        A("poison", 4, potion="poison"),                                    # night 2
        A("accuse", 2, "昨晚少了一个人，今天2号却最安静。", belief=b(5, 2, 3)),
        A("vote", 2),
        A("vote", 3),                                                       # PK: 2号的话是空话
    ],
    # ------------------------------------------------------------------ 6号 民
    6: [
        A("accuse", 1, "昨晚我听见狼叫了，方向就在1号那边。", belief=b(6, 1)),  # impossible percept
        A("vote", 1),
        A("align", 3, "我跟2号，3号昨天躲了一天，今天还在躲。", belief=b(6, 3, 2)),
        A("vote", 3),
        A("vote", 3),                                                       # PK revote
    ],
    # ------------------------------------------------------------------ 7号 预言家
    7: [
        A("check", 1),                                                      # night 1
        A("accuse", 1, "我有一手确定的信息：1号是狼，票给他。",
          evidence=["e17"], belief=b(7, 1, 4)),                             # private citation
        A("vote", 1),
        A("check", 2),                                                      # night 2
        A("accuse", 2, "我这手信息还是准的，2号就是狼。",
          evidence=["e48"], belief=b(7, 2, 6)),                             # private citation
        A("vote", 2),
        A("vote", 2),                                                       # PK revote
    ],
    # ------------------------------------------------------------------ 8号 民
    8: [
        # Attempt 1 is refused by the citation gate (hard even in speech), attempt 2 is the
        # same claim with no fabricated evidence. Two script entries, one event.
        A("accuse", 1, "1号今天的话术和昨天完全相反。", evidence=["e9999"]),
        A("accuse", 1, "1号今天的话术和昨天完全相反。", belief=b(8, 1, 2)),
        A("vote", 1),
        A("pivot", 3, "我改看3号了，7号那手昨天已经错过一次。", belief=b(8, 3)),
        A("vote", 3),
        A("vote", 3),                                                       # PK revote
    ],
    # ------------------------------------------------------------------ 9号 猎人
    9: [
        A("probe", 2, "2号从刚才到现在一句实在话都没有，先给个态度。", belief=b(9, 2, 4)),
        A("vote", 1),
        A("shoot", 2),                                                      # queued from night 2
    ],
}

# (seq, kind, actor) for the whole game — the shape of it, without a single word of
# dialogue. Deliberately excludes payloads: wording edits must not move this list, while
# any change to who-acts-when does.
GOLDEN_KEYS = [
    "1 game_start None",
    "2 deal 1",
    "3 deal 2",
    "4 deal 3",
    "5 deal 4",
    "6 deal 5",
    "7 deal 6",
    "8 deal 7",
    "9 deal 8",
    "10 deal 9",
    "11 phase None",
    "12 wolf_chat 1",
    "13 night_action 2",
    "14 notice None",
    "15 night_action 5",
    "16 night_action 7",
    "17 seer_result 7",
    "18 phase None",
    "19 speech 3",
    "20 speech 9",
    "21 speech 6",
    "22 speech 4",
    "23 speech 5",
    "24 speech 2",
    "25 speech 7",
    "26 speech 1",
    "27 speech 8",
    "28 vote 1",
    "29 vote 2",
    "30 vote 3",
    "31 vote 4",
    "32 vote 5",
    "33 vote 6",
    "34 vote 7",
    "35 vote 8",
    "36 vote 9",
    "37 vote_result None",
    "38 death None",
    "39 last_words 1",
    "40 phase None",
    "41 wolf_chat 4",
    "42 night_action 2",
    "43 notice None",
    "44 night_action 5",
    "45 night_action 7",
    "46 death None",
    "47 death None",
    "48 seer_result 7",
    "49 phase None",
    "50 speech 5",
    "51 speech 7",
    "52 speech 8",
    "53 speech 3",
    "54 speech 2",
    "55 speech 6",
    "56 vote 2",
    "57 vote 3",
    "58 vote 5",
    "59 vote 6",
    "60 vote 7",
    "61 vote 8",
    "62 vote_result None",
    "63 phase None",
    "64 speech 2",
    "65 speech 3",
    "66 vote 2",
    "67 vote 3",
    "68 vote 5",
    "69 vote 6",
    "70 vote 7",
    "71 vote 8",
    "72 vote_result None",
    "73 death None",
    "74 last_words 3",
    "75 night_action 9",
    "76 death None",
    "77 game_over None",
]


def play_authored(out_dir, *, cfg: Config | None = None):
    """Play the authored transcript with the authored assignments.

    `assign_speech_acts` draws acts from the persona weights; this script *authored* its
    acts, and the gate now checks the answer against the assignment. Left to the draw, every
    speech turn would spend its retry and the numbers pinned below would be sampling results
    rather than designed ones. So the fixture supplies its own assignment table — the same
    entry each seat is about to answer with. The tripwire still fires: if the engine ever
    changes who it asks or how often, the next entry is the wrong shape, the gate refuses an
    assigned `vote` in a speech round, and `test_no_script_entry_was_wasted` goes red.

    The random assignment itself is covered where it is generated — `test_rules` (every
    assignable act is one the gate grants) and `test_agent_turns` (enforcement and salvage).
    """
    cfg = cfg or Config()
    actors = {s: MockActor(s, script=list(v)) for s, v in SCRIPTS.items()}
    mp = pytest.MonkeyPatch()
    mp.setattr(phases, "assign_speech_acts", _authored_assignment(actors))
    try:
        return asyncio.run(game.play(cfg=cfg, deal_seed=SEED, out_dir=out_dir, actors=actors))
    finally:
        mp.undo()


def _authored_assignment(actors):
    def assign(state, speakers, personas, beliefs, rng, cfg, legal):
        out = {}
        for seat in speakers:
            actor = actors[seat]
            nxt = actor.script[actor.i] if actor.i < len(actor.script) else None
            out[seat] = (nxt.act, nxt.target) if nxt is not None else ("listen", None)
        return out
    return assign


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    out = tmp_path_factory.mktemp("golden")
    cfg = Config()
    res = play_authored(out, cfg=cfg)
    events, meta = EventLog.read_records(res.path)
    return cfg, res, events, meta


def _key(events) -> list[str]:
    return [f"{e.seq} {e.kind} {e.actor}" for e in events]


def _ev(events, seq: int):
    return next(e for e in events if e.seq == seq)


def _speeches(events, day: int) -> list:
    return [e for e in events if e.kind == Kind.SPEECH and e.day == day]


def _said(events, day: int) -> list[tuple[str, str]]:
    return [(e.payload["act"], e.payload["text"]) for e in _speeches(events, day)]


# --------------------------------------------------------------------------- the shape
def test_the_state_machine_walks_the_authored_path(golden):
    _, res, events, _ = golden
    assert res.terminal == "good_win" and res.winner == "good"
    assert res.days == 2 and res.events == 77
    # The only retry in the game is the designed one (e27). A fallback here would mean a
    # scripted seat was asked for something its author did not foresee.
    assert (res.fallbacks, res.timeouts, res.context_overflows, res.shrinks) == (0, 0, 0, 0)
    assert res.retries == 1
    assert _key(events) == GOLDEN_KEYS


def test_no_script_entry_was_wasted(golden):
    """A seat with a left-over script entry was asked fewer times than the author thought;
    an exhausted seat (see `res.fallbacks` above) was asked more. Both mean the schedule
    moved, and the pinned key list would only catch it if some *other* seat shifted too."""
    _, _, events, _ = golden
    asked: dict[int, int] = {}
    for e in events:
        if e.actor is not None and e.kind in (Kind.SPEECH, Kind.VOTE, Kind.NIGHT_ACTION,
                                              Kind.WOLF_CHAT, Kind.LAST_WORDS):
            asked[e.actor] = asked.get(e.actor, 0) + 1
    assert asked == {1: 4, 2: 8, 3: 7, 4: 3, 5: 7, 6: 5, 7: 7, 8: 5, 9: 3}
    # Seat 8 is the only author whose entry count exceeds its event count, and by exactly
    # one: the attempt the citation gate refused. Any other seat out of step by one means a
    # retry appeared or vanished somewhere it was not designed.
    assert {s: len(v) - asked[s] for s, v in SCRIPTS.items()} == \
        {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0, 8: 1, 9: 0}


def test_the_wolves_do_not_all_vote_for_the_same_knife(golden):
    """The proposer and the killer are different seats, and the pair rotates by day
    (plan §12 R11): three wolves each choosing alone is what makes 狼队胜率 meaningless."""
    _, _, events, _ = golden
    chat = [e for e in events if e.kind == Kind.WOLF_CHAT]
    kill = [e for e in events if e.kind == Kind.NIGHT_ACTION
            and e.payload.get("action") == "kill"]
    assert [(e.actor, e.payload["target"]) for e in chat] == [(1, 3), (4, 9)]
    assert [(e.actor, e.payload["target"]) for e in kill] == [(2, 3), (2, 9)]
    assert all(c.actor != k.actor for c, k in zip(chat, kill))


def test_the_witch_is_told_the_knife_and_the_knife_is_not_published(golden):
    """`notice` exists so the witch need not be told by a public death. If the dawn
    announcement were the carrier, every seat would learn who the wolves chose."""
    _, _, events, _ = golden
    n1, n2 = _ev(events, 14), _ev(events, 43)
    assert (n1.kind, n1.visibility, n1.payload["about"]) == (Kind.NOTICE, frozenset({5}), 3)
    assert n2.payload["about"] == 9
    dawn = [e for e in events if e.kind == Kind.PHASE and "天亮了" in e.payload["text"]]
    assert len(dawn) == 2 and all(e.visibility == "all" for e in dawn)
    # Night 1 ended with the save, so no death event may exist between dusk and dawn.
    assert not [e for e in events if e.kind == Kind.DEATH and 17 < e.seq < 19]


def test_the_save_is_recorded_as_a_potion_not_as_a_resurrection(golden):
    """The engine decrements the antidote off `act == "save"`, and 3号 must still be alive
    to be asked for a ballot — the save has to reach the living set, not just the text."""
    _, _, events, _ = golden
    save = _ev(events, 15)
    assert (save.payload["act"], save.payload["target"]) == ("save", None)
    assert "potion" not in save.payload, "`#114` 起这一格只由 act 说一遍"
    assert save.payload["meta"]["citation_stats"] is not None
    assert [e.actor for e in events if e.kind == Kind.VOTE and e.day == 1] == list(range(1, 10))


# ------------------------------------------------------------------------ the gates
def test_a_private_citation_passes_and_a_public_one_does_not_become_illegal(golden):
    """e17 is the seer's own result: visible to seat 7 and to nobody else, and the gate
    accepts it. This is the whole reason the citation cell holds event ids, not prose."""
    _, _, events, _ = golden
    claim = _ev(events, 25)
    stats = claim.payload["meta"]["citation_stats"]
    assert stats["valid"] == ["e17"] and stats["invented"] == [] and stats["not_visible"] == []
    assert _ev(events, 17).visibility == frozenset({7})


def test_an_invented_citation_is_refused_and_the_refusal_is_kept(golden):
    """Plan §4: rejected raw output is *not* overwritten, because refused+passed pairs are
    the free preference data a later KTO run would want — and a re-run does not exist."""
    _, _, events, _ = golden
    published = _ev(events, 27)
    assert published.payload["meta"]["citation_stats"]["valid"] == [] and len(published.attempts) == 1
    rejected = published.attempts[0]
    assert rejected["violations"] == ["invented_event_ids:['e9999']"]
    assert '"e9999"' in rejected["raw"]
    assert published.payload["meta"]["attempt"] == 2
    assert published.payload["meta"]["citation_stats"]["invented"] == []


def test_the_hallucinated_percept_is_flagged_and_still_played(golden):
    """Soft gate: the lie is *evidence for M4*, and blocking it would delete the measurement
    along with the behaviour (plan §2 principle 1)."""
    _, _, events, _ = golden
    liar = _ev(events, 21)
    assert liar.payload["meta"]["flags"] == ["impossible_percept:昨晚+听见"]
    assert liar.payload["meta"]["violations"] == [] and liar.payload["meta"]["fallback"] == 0
    assert [e.payload["meta"]["flags"] for e in events if e.kind == Kind.SPEECH] \
        .count([]) == len(_speeches(events, 1)) + len(_speeches(events, 2)) - 1


def test_no_seat_ever_reads_another_seats_private_ranking(golden):
    """Stated belief travels inside the payload of a *public* speech event, so isolation
    here is a property of the renderer, not of visibility. Every prompt actually sent is on
    disk; this scans those bytes rather than a re-assembly that could differ from what ran.

    One-directional on purpose: nothing requires a seat's own earlier `why` to be fed back to
    it (it is not), so asserting equality here would fail for the wrong reason.
    """
    _, _, events, _ = golden
    thoughts: dict[int, list[str]] = {}
    for e in events:
        for sus in (e.payload.get("belief") or {}).get("suspects", []):
            thoughts.setdefault(e.actor, []).append(sus["why"])
    owners: dict[str, set[int]] = {}
    for seat, whys in thoughts.items():
        for why in whys:
            owners.setdefault(why, set()).add(seat)
    assert len(owners) == 24
    assert {w for w, s in owners.items() if len(s) > 1} == set(), \
        "a judgement string is shared by two seats, so a leak would not be attributable"

    prompts = [(e.actor, "\n".join(m["content"] for m in e.request["messages"]))
               for e in events if e.request.get("messages") and e.actor]
    assert len(prompts) > 40, "too few prompts on disk for this scan to mean anything"
    for observer, blob in prompts:
        for seat, whys in thoughts.items():
            if seat == observer:
                continue
            for why in whys:
                assert why not in blob, f"seat {seat}'s thought {why!r} reached seat {observer}"


def test_the_scan_would_have_found_a_public_utterance(golden):
    """Reverse control: the same scan over the same bytes *must* find text that is meant to
    be public. Without this, a broken `prompts` dict would make the test above pass by
    looking at nothing."""
    _, _, events, _ = golden
    liar = next(e for e in events if e.seq == 21)
    said = liar.payload["text"]
    others = {e.actor for e in events
              if e.request.get("messages") and e.actor and e.actor != liar.actor
              and said in "\n".join(m["content"] for m in e.request["messages"])}
    assert len(others) >= 5, f"{said!r} never appeared in anybody's prompt — the scan is dead"


# --------------------------------------------------------------------------- the votes
def test_a_tie_opens_a_pk_and_the_revote_is_its_own_ballot_wave(golden):
    """The bug this pins: the PK path arrived at `_ballots` with the phase still set to
    speech, so every `act="accuse"` mapped to 弃票 and the log said 全员弃票 three lines
    above three ballots that had targets on them."""
    _, _, events, _ = golden
    first, second = _ev(events, 62), _ev(events, 72)
    assert first.payload["tally"] == {"3": 3, "2": 3} and first.payload["exiled"] is None
    assert "平票，2、3号进入PK" in _ev(events, 63).payload["text"]
    assert second.payload["tally"] == {"3": 4, "2": 2} and second.payload["exiled"] == 3
    ballots = [_ev(events, s).payload for s in range(66, 72)]
    assert [b_["act"] for b_ in ballots] == ["vote"] * 6, "a PK ballot recorded as abstention"


def test_a_hunter_knifed_at_night_shoots_after_the_day_that_killed_him(golden):
    """House rule `hunter_shoots_on={wolf_kill,exiled}`, resolved by `run_hunter_shots` at
    the end of the day. The shot is the last act of the game and ends it."""
    _, _, events, _ = golden
    assert _ev(events, 47).payload == {"seat": 9, "cause": "wolf_kill"}
    shot = _ev(events, 75)
    assert (shot.actor, shot.payload["act"], shot.payload["target"]) == (9, "shoot", 2)
    assert shot.visibility == frozenset({9})
    # Whole-record pin: the terminating line is the artifact `audit` and the batch report read,
    # so its exact key set belongs here — `degraded_game` is the verdict and `degraded_threshold`
    # the rule it was judged under (plan §143). Adding or dropping a key here changes what a
    # reader of one file can conclude, so it has to make this go red.
    assert _ev(events, 77).payload == {"winner": "good", "terminal": "good_win",
                                       "degraded_game": False, "degraded_threshold": 12}


def test_the_death_is_stored_as_an_enum_not_as_a_chinese_sentence(golden):
    """死因在日志里只有一份：枚举 `cause`；人话由渲染侧现算。

    `phases.py` 过去在同一条 `t.say` 里写完 `cause` 再顺手写 `cause_zh=CAUSE_ZH[cause]`，"怎么死的"
    这句话于是有了两个写者。data/ 6 份日志的 37 条 DEATH 上两份今天说的是同一件事（23:33:03Z 现读：
    `cause_zh == CAUSE_ZH[cause]` 37/37），但结构允许它们分岔，而四个读者已经各说各话——回退值分别是
    "死亡"、空字符串、空字符串、英文枚举原文（见 `tests/test_wiring.py` 那一格）。删掉的是存着的那一份，
    留下的是带类型的那一份：这正是 `compress.py` 里 `VERDICT_ZH` 那句注释给预言家查验定过的判据。
    """
    _, _, events, _ = golden
    deaths = [e for e in events if e.kind == Kind.DEATH]
    assert len(deaths) == 5, f"金样本这一桌该有 5 条死亡，实际 {len(deaths)} 条"
    for e in deaths:
        assert "cause_zh" not in e.payload, f"e{e.seq} 又把译文抄进了日志"
    assert sorted(e.payload["cause"] for e in deaths) \
        == ["exiled", "exiled", "hunter_shot", "poison", "wolf_kill"], \
        "五条死亡得真的带着枚举值，上面那句才不是空的"
    assert "被狼刀" in compress.render_line(_ev(events, 47)), "译文仍在，只是由渲染侧现算"


# ---------------------------------------------------------------------------- the numbers
def test_m5_template_numbers_on_the_authored_transcript(golden):
    """Measured from this fixture, not derived: `3号`'s listen is the single passive turn
    (M3's primary criterion has a numerator to find), and 6号 recycles 2号's sentence, which
    is the behaviour `collapse_round` and `dup_exact6_rate` exist to catch."""
    _, _, events, _ = golden
    d1, d2 = _said(events, 1), _said(events, 2)
    assert len(d1) == 9 and len(d2) == 8, "PK speeches are day 2, so they belong to d2"
    assert metrics.passivity_rate(d1) == pytest.approx(1 / 9)
    assert metrics.passivity_rate(d2) == 0.0
    texts1 = [t for _, t in d1]
    assert metrics.collapse_round(texts1) == 0.0
    assert metrics.opening_distinct_rate(texts1) == 1.0
    assert metrics.shared_substring_rate(texts1) == 0.0
    assert metrics.template_top_fragments(texts1) == []
    # Day 2 holds the planted duplication: 6号 says 2号's clause verbatim.
    texts2 = [t for _, t in d2]
    assert metrics.collapse_round(texts2) == pytest.approx(0.02521, abs=1e-4)
    assert metrics.shared_substring_rate(texts2) == pytest.approx(0.25)
    assert "3号昨天躲了一天，今天还在躲。" in "".join(texts2)
    assert metrics.opening_distinct_rate(texts2) == 1.0


def test_m4_citation_rates_have_the_designed_numerators(golden):
    """Denominator = the speech turns only. Counting ballots as uncited claims would make
    `uncited_claim_rate` a function of how many people voted."""
    _, _, events, _ = golden
    sp = [e for e in events if e.kind == Kind.SPEECH]
    stats = [e.payload["meta"]["citation_stats"] for e in sp]
    assert len(sp) == 17
    assert sum(1 for s in stats if s["uncited"]) == 12
    assert [e.seq for e, s in zip(sp, stats) if s["valid"]] == [25, 26, 51, 53, 54]
    # Zero, because the one invention in this game never reached a published event: the gate
    # refused it and the corrected attempt is what got written (see test_an_invented_citation).
    assert sum(1 for s in stats if s["invented"]) == 0
    assert sum(1 for e in sp if e.payload["meta"]["flags"]) == 1  # the planted hallucination


def test_m8_strategy_proxies_are_readable_from_the_log(golden):
    """The no-statistical-power-needed signals (plan §8 M8): these are what say "the agents
    are playing" rather than "the engine did not crash"."""
    _, _, events, _ = golden
    deaths = [(e.payload["seat"], e.payload["cause"], e.day)
              for e in events if e.kind == Kind.DEATH]
    assert deaths == [(1, "exiled", 1), (4, "poison", 2), (9, "wolf_kill", 2),
                      (3, "exiled", 2), (2, "hunter_shot", 2)]
    assert [e.actor for e in events if e.kind == Kind.SEER_RESULT] == [7, 7]
    assert [e.payload["verdict"] for e in events if e.kind == Kind.SEER_RESULT] == ["wolf", "wolf"]
    tallies = [e.payload["tally"] for e in events if e.kind == Kind.VOTE_RESULT]
    ent = [metrics.shannon_entropy(list(t.values())) for t in tallies]
    assert ent == [pytest.approx(0.918296), 1.0, pytest.approx(0.918296)]
    assert statistics.mean(ent) == pytest.approx(0.945531)


def test_a_unanimous_wave_reports_no_entropy_rather_than_negative_entropy():
    """All nine votes on one seat is zero disagreement, not *less than* zero. `-0.0` compares
    equal to `0.0`, so only the sign bit tells them apart — and `audit` prints the JSON, where
    `-0.0` reads as a bug in the metric to the person the report is written for."""
    assert math.copysign(1.0, metrics.shannon_entropy([9])) == 1.0, repr(metrics.shannon_entropy([9]))


def test_the_belief_action_gap_is_the_one_designed_case(golden):
    """M6's split by team (plan §8): for the good team `target ∈ stated top-2` is skill; for
    a wolf *failing* it is skill. Averaging the two would call 2号's bluff bad play.
    Pinned as facts here; the scoring function itself lands with M5."""
    _, _, events, _ = golden
    def hit(e) -> bool:
        top2 = [d["seat"] for d in e.payload["belief"]["suspects"][:2]]
        return e.payload["target"] in top2

    wolves = [e for e in _speeches(events, 1) + _speeches(events, 2)
              if e.actor in WOLVES and e.payload["belief"]]
    good = [e for e in _speeches(events, 1) + _speeches(events, 2)
            if e.actor in GOOD and e.payload["belief"] and e.payload["target"] is not None]
    assert [(e.seq, hit(e)) for e in wolves] \
        == [(22, True), (24, False), (26, True), (54, True), (64, True)]
    assert all(hit(e) for e in good), "the good team's stated ranking contradicted its own ballot"
    assert len(good) == 10


def test_the_manifest_declares_this_a_synthetic_table(golden):
    """Pairing corpora must be able to exclude a stand-in table by reading the file, not by
    remembering which runs were real. Recording `actor_kinds` in the header is plan §15's
    implementation clause; the rule that keeps a mock table out is §11."""
    _, _, _, meta = golden
    assert meta["actor_kinds"] == ["mock"] and meta["deal_seed"] == SEED
    assert meta["reproducible"] is False
    assert meta["contract_version"] and meta["rules_version"] and meta["compress_version"]


# ------------------------------------------------------- M1–M8 as functions of a loaded log
#
# Everything below takes the *file*, not the running game: a metric that needs the engine
# alive to be computed cannot survive R7 (the endpoint is someone else's and gets restarted).

def _deal(seat: int, role: str) -> Event:
    return Event(seq=0, kind=Kind.DEAL, day=1, phase="deal", visibility=frozenset({seat}),
                 payload={"seat": seat, "role": role}, actor=seat)


def _night(seat: int, day: int, *, act: str, action: str,
           target: int | None, potion: str | None = None) -> Event:
    """A night action with both `act` (what the seat chose) and `action` (the legality class
    the turn was played under) — the pair m8 has to read the first of and does not."""
    return Event(seq=0, kind=Kind.NIGHT_ACTION, day=day, phase=f"night_{seat}",
                 visibility=frozenset({seat}),
                 payload={"act": act, "action": action, "target": target, "potion": potion,
                          "meta": {"attempt": 1, "violations": [], "flags": [], "fallback": 0}},
                 actor=seat)


def _call(seat: int, day: int, phase: str, *, lat: float, ct: int, pt: int,
          finish: str = "stop", transport_attempts: int = 1,
          asked: int | None = None) -> Event:
    request: dict = {"total_tokens_est": pt}
    if asked is not None:
        # What the actor billed the call at (`Config.token_budget_for`), i.e. the `max_tokens`
        # that went on the wire. Absent on a hand-built log on purpose: that is the shape of an
        # older transcript, and `m7` must not turn "not recorded" into "asked for nothing".
        request["max_tokens"] = asked
    return Event(seq=0, kind=Kind.VOTE, day=day, phase=phase, visibility="all",
                 payload={"act": "vote", "target": 1, "meta": {"attempt": 1}},
                 actor=seat,
                 request=request,
                 response={"latency_s": lat, "completion_tokens": ct, "prompt_tokens": pt,
                           "finish_reason": finish, "attempts": transport_attempts})


def test_the_log_reads_back_as_a_game_with_no_engine_involved(golden):
    """`Game` is the unit M1 aggregates over, so its projections have to come off disk alone."""
    _, res, events, _ = golden
    g = metrics.read_game(res.path)
    assert g.path == res.path and g.game_id == res.game_id
    assert (g.terminal, g.winner, g.days) == ("good_win", "good", 2)
    assert g.is_synthetic is True
    assert len(metrics.read_dir(res.path.parent)) == 1
    assert metrics.roles_of(g.events) == {1: "wolf", 2: "wolf", 3: "villager", 4: "wolf",
                                          5: "witch", 6: "villager", 7: "seer", 8: "villager",
                                          9: "hunter"}
    # Team, not role: witch/seer/hunter are three roles on one team, and every faction-split
    # metric would triple-count them as three factions if this map were the role strings.
    assert metrics.teams_of(g.events) == {1: "wolf", 2: "wolf", 3: "villager", 4: "wolf",
                                          5: "god", 6: "villager", 7: "god", 8: "villager",
                                          9: "god"}
    assert metrics.seer_seat(g.events) == 7
    assert [metrics.seat_death_day(g.events, s) for s in (9, 7, 3)] == [2, None, 2]
    assert len(events) == len(g.events) == 77
    assert len(metrics.decisions(g.events)) == 49
    assert len(metrics.speeches(g.events)) == 17


def test_m1_refuses_to_score_a_stand_in_table(golden):
    """Plan §11's rule has to bite inside the metric, not only inside `compare`: a mock script
    wins its faction by authorship, so a win rate over it measures the author."""
    _, res, _, _ = golden
    g = metrics.read_game(res.path)
    out = metrics.m1_win_rate([g])
    assert (out["n_games"], out["n_decisive"], out["n_synthetic_excluded"]) == (1, 0, 1)
    assert out["good_win_rate"] is None and out["by_role_survival"] == {}
    assert "替身桌" in out["note"]
    # Reverse control: relabel the same bytes as a real table and the numbers must appear —
    # otherwise `None` above could be a metric that never computes anything.
    real = metrics.Game(path=g.path, meta={"actor_kinds": ["llm"]}, events=g.events)
    out = metrics.m1_win_rate([real])
    assert out["good_win_rate"] == 1.0 and out["n_decisive"] == 1
    assert out["wilson95"] == pytest.approx([0.207, 1.0])
    assert out["mean_days"] == 2 and out["n_synthetic_excluded"] == 0
    # 1 game out of a 9-seat board: the interval must span nearly the whole range, which is
    # the machine-readable version of "this number cannot carry a conclusion" (R2).
    assert out["wilson95"][1] - out["wilson95"][0] > 0.7
    assert out["stratified"]["seer_survived_night_1"]["n"] == 1
    assert out["stratified"]["seer_died_night_1"] == {"n": 0, "good_rate": None}
    assert out["descriptive"] is True


def test_m1_keeps_an_unfinished_game_out_of_the_denominator(golden):
    """`aborted` games are counted separately rather than folded into a win rate (plan §8 M1):
    dropping them silently would favour whichever side the timeout happened to hit."""
    _, res, events, _ = golden
    half = metrics.Game(path=res.path, meta={"actor_kinds": ["llm"]}, events=events[:-1])
    out = metrics.m1_win_rate([half, metrics.read_game(res.path)])
    assert out["excluded"] == {"unfinished": 1}
    assert (out["n_games"], out["n_decisive"], out["n_synthetic_excluded"]) == (2, 0, 1)
    assert out["good_win_rate"] is None and "分母为空" in out["note"]


#: 两种"文件在、局不在"的形状，与 `tests/test_m3_gate.py` 的 `#99` 那一节同形：这里钉的是**胜率**
#: 这一层的第二个读者（`m1_win_rate` 的 `n_games` 和 `comparison.md` 的「局数」那一格）。
_NO_TABLE = metrics.Game(path=Path("/tmp/no-table.jsonl"), meta={}, events=[])
#: 页眉登记的是真端点：`is_synthetic` 这道门**放行**它，所以它会溜进 `usable` 并把 `excluded`
#: 顶出一格 "unfinished": 1 —— 一个从来没打过牌的字节，被说成"打完了没分出胜负"。
_OPENED_ONLY_LLM = metrics.Game(path=Path("/tmp/opened-only-llm.jsonl"),
                                meta={"game_id": "opened-only-llm", "actor_kinds": ["llm"]},
                                events=[])


def test_m1_counts_a_file_with_no_game_in_the_files_not_in_the_games(golden):
    """一份 0 字节的日志和一份只写了开局记录的日志，都不许挪动胜率里的任何一格数字。

    18:40:38Z 拿真日志目录实测（两局真的 + 一份只有页眉的 `["llm"]` + 一份 0 字节）：闸门那侧
    #99 之后读作 `n_games=2 / n_files=4`，胜率这一侧仍然报 `n_games: 4`、`excluded:
    {"unfinished": 1}`。`n_decisive` 走的是 `DECISIVE` 筛过的分母，所以胜率本身没错，错的是「本批 N
    局」这一格把两份没有事件的字节说成了局 —— 一句假话在两个出口各写了一遍，就是 `#89` 那一族。
    """
    _, res, _, _ = golden
    real = metrics.Game(path=res.path, meta={"actor_kinds": ["llm"]},
                        events=metrics.read_game(res.path).events)
    base = metrics.m1_win_rate([real])
    assert (base["n_games"], base["n_decisive"], base["good_win_rate"]) == (1, 1, 1.0), base
    out = metrics.m1_win_rate([real, _NO_TABLE, _OPENED_ONLY_LLM])
    assert out["n_games"] == 1, "没有事件的字节被数进了局的分母"
    assert out["n_files"] == 3 and out["hollow"]["n"] == 2, out
    assert out["excluded"] == base["excluded"], "空文件被算成『打完了没分出胜负』"
    assert out["n_synthetic_excluded"] == base["n_synthetic_excluded"], "剔除数被没有局的文件顶偏"
    for key in ("good_win_rate", "wilson95", "mean_days", "by_role_survival", "stratified"):
        assert out[key] == base[key], f"{key} 不该因为多了两个空字节而变动"
    # 少了哪几份必须说得出是谁：`hollow` 的措辞从 `events.py` 那一只手取，这里一个字都不重写。
    assert meta_notice({}) in out["hollow"]["note"], out["hollow"]
    assert empty_notice([], {"game_id": "opened-only-llm"}) in out["hollow"]["note"], out["hollow"]


def test_m1_over_a_folder_of_such_files_says_there_is_no_game():
    """全是空文件时，胜率要说"没有一局"，不能说"本批 2 局全为替身桌"，也不能沿用那句默认的"仅作描述"。

    后两者都是关于**打牌的人**的假话：这些文件连座位表都没有（`is_synthetic` 是关门，反过来读成
    "登记成了替身"就是无中生有一个座位）。默认那句 note 硬写着"胜率在此样本量下仅作描述"，读者会
    以为有一批局被采样了 —— 与 `#99` 在闸门那侧拦下的是同一句话。
    """
    out = metrics.m1_win_rate([_NO_TABLE, _OPENED_ONLY_LLM])
    assert out["n_games"] == 0 and out["n_decisive"] == 0, out
    assert out["good_win_rate"] is None and out["wilson95"] == [0.0, 1.0], out
    assert out["hollow"]["n"] == 2 and out["n_files"] == 2, out
    assert "替身桌" not in out["note"], out["note"]
    assert "样本量" not in out["note"], out["note"]
    assert "没有一局" in out["note"], out["note"]


def test_a_log_from_before_the_verdict_reads_as_unrecorded_not_as_healthy(golden):
    """`degraded_game` 缺失要读成 `None`（没记录），不能读成 `False`（没退化）。

    这一格是后加的：手上永远可能有字段落地之前跑的批次。把缺失误读成"没问题"，一份旧批次就会
    在没有任何人改动过它的情况下突然变成"0 局退化"——而这条判定的全部意义就是别让退化局混进
    结论，所以它失效时必须以"不知道"的形式失效，而不是以好消息的形式。
    """
    _, res, _, _ = golden
    g = metrics.read_game(res.path)
    assert g.degraded_game is False, "金样本是新跑的日志：它必须真的带着这一格"
    over = [e for e in g.events if e.kind == metrics.Kind.GAME_OVER][-1]
    del over.payload["degraded_game"]
    assert g.degraded_game is None, "缺字段被读成了「没退化」"
    # 没有 GAME_OVER 的半局同理：`unfinished` 不是"没退化"，是"没记完"。
    half = metrics.Game(path=res.path, meta=g.meta, events=g.events[:-1])
    assert half.degraded_game is None and half.terminal == "unfinished"


def test_m2_separates_what_the_model_tried_from_what_the_transcript_contains(golden):
    """first-attempt is the only口径 that can find a regression; final is the only one that
    says whether the played game was still the model's (plan §8 M2)."""
    _, res, _, _ = golden
    out = metrics.m2_illegal_rate(metrics.read_game(res.path).events)
    assert out["n_turns"] == 49
    assert out["first_attempt"] == pytest.approx(1 / 49, abs=5e-5)
    assert out["final"] == 0.0 and out["kinds"] == {"invented_event_ids": 1}
    # The refusal is 8号's, and 8号 is a villager — so the bucket is non-zero for a reason a
    # reader can check against the transcript rather than against a hash of the log.
    assert out["by_phase_role"]["day_speech/villager"] == pytest.approx(1 / 6, abs=5e-4)
    assert sum(v for k, v in out["by_phase_role"].items() if k != "day_speech/villager") == 0


def test_m3_locates_the_single_retry_and_says_which_rung_cleared_it(golden):
    _, res, _, _ = golden
    out = metrics.m3_gate_pressure(metrics.read_game(res.path).events)
    assert (out["n_turns"], out["refused_turns"]) == (49, 1)
    assert out["retry_rate"] == pytest.approx(1 / 49, abs=5e-5)
    assert (out["fallback_rate"], out["timed_out"], out["context_overflows"],
            out["shrinks"], out["parse_failures"]) == (0.0, 0, 0, 0, 0)
    # Rung -1 is "no repair ladder ran" — true of a scripted table. On a real run this map is
    # the M3a answer to "does the strict-JSON rate hold at 5k tokens", so it is pinned as a
    # shape, not as a number to be copy-forwarded from here.
    assert out["repair_rungs"] == {"published:-1": 49, "attempt:-1": 1}


def test_m4_counts_a_claim_against_the_ballot_that_followed_it(golden):
    """The pairing rule is the whole difference between a finding and an artifact.

    5号 accuses 2号 on day 2 and then votes 3号 in the PK revote — a mind changed by 2号's
    empty PK speech, which is play. Pairing a claim with the *last* ballot of the day flags
    that too, and would report 0.2 on this transcript; the rule below pairs with the next
    ballot after the speech and reports 0.1, leaving exactly the designed case: 9号's probe
    named 2号 and its ballot went to 1号.
    """
    _, res, _, _ = golden
    out = metrics.m4_hallucination_rates(metrics.read_game(res.path).events)
    assert out["n_speech"] == 17
    assert out["uncited_claim_rate"] == pytest.approx(12 / 17, abs=5e-5)
    assert out["wrong_cite_rate"] == pytest.approx(1 / 17, abs=5e-5)
    assert out["impossible_percept_rate"] == pytest.approx(1 / 17, abs=5e-5)
    # 10, not 17: only a good-team claim that has a later ballot in the same seat's own day
    # can be contradicted. Wolves are excluded because their divergence is the product.
    assert out["n_self_contradiction"] == 1 and out["n_self_contradiction_denominator"] == 10
    assert out["self_contradiction_rate"] == pytest.approx(1 / 10, abs=5e-5)


def _cited(seq: int, seat: int, *, attempts: tuple[str, ...] = (),
           invented: tuple[str, ...] = (), not_visible: tuple[str, ...] = ()) -> Event:
    """A speech turn whose citation the gate objected to, in the two shapes it objects in."""
    stats = {"cited": list(invented) + list(not_visible), "valid": [],
             "not_visible": list(not_visible), "invented": list(invented),
             "malformed": [], "uncited": False}
    refused = ({"violations": [f"invented_event_ids:{list(attempts)}"]},) if attempts else ()
    return Event(seq=seq, kind=Kind.SPEECH, day=1, phase="day_speech", visibility="all",
                 actor=seat,
                 payload={"text": f"{seat}号：5号的问题不用查也知道。", "act": "accuse", "target": 5,
                          "meta": {"citation_stats": stats, "violations": [], "flags": []}},
                 attempts=refused)


def test_a_citation_copied_out_of_the_prompt_example_is_named_separately(golden):
    """Region A shows a worked example carrying its own `[eNNN]` numbers, and a weak model
    copies them. That is prompt imitation, not fabricated evidence, and the two have to be
    told apart before either number goes into a report: `wrong_cite_rate` stays the honest
    headline, and this key says how much of it the prompt itself taught.
    """
    from wolfengine.prompts.templates import EXAMPLE_EVENT_IDS

    assert {"e88", "e97", "e132"} <= EXAMPLE_EVENT_IDS, \
        "the example's own ids must be listed where the example is written"

    events = metrics.read_game(golden[1].path).events
    assert metrics.m4_hallucination_rates(events)["example_copy_turns"] == 0, (
        "the golden game invented e9999, which no prompt ever showed a seat")

    mixed = [_cited(1, 5, attempts=("e97", "e132")),        # both from the example
             _cited(2, 6, attempts=("e9999",)),             # genuinely invented
             _cited(3, 7, not_visible=("e88",)),            # copied, and invisible anyway
             _cited(4, 8, attempts=("e97", "e9999"))]       # mixed: not a clean copy
    out = metrics.m4_hallucination_rates(mixed)
    assert out["example_copy_turns"] == 2, out
    assert out["wrong_cite_rate"] == pytest.approx(4 / 4, abs=1e-9), \
        "the headline must not shrink because a diagnosis was added"


def test_m5_treats_a_pk_round_as_its_own_round(golden):
    """A PK is a second speaking wave in the same day. Merging it into the day would compare
    6 short replies against each other and report a collapse the table did not have."""
    _, res, _, _ = golden
    events = metrics.read_game(res.path).events
    assert [len(r) for r in metrics.speech_rounds(events)] == [9, 6, 2]
    out = metrics.m5_style_collapse(events)
    assert out["n_rounds"] == 3
    assert [(r["day"], r["phase"], r["n"]) for r in out["rounds"]] \
        == [(1, "day_speech", 9), (2, "day_speech", 6), (2, "day_pk_speech", 2)]
    assert out["worst_round"]["day"] == 2 and out["worst_round"]["phase"] == "day_speech"
    assert out["passivity_pooled"] == pytest.approx(1 / 17, abs=5e-5)  # 3号's listen, /17 turns
    assert out["collapse_round_mean"] == pytest.approx(0.0157, abs=5e-4)
    assert out["opening_distinct_mean"] == 1.0
    # The planted duplication: 6号 says 2号's clause verbatim inside the day-2 round.
    assert out["rounds"][1]["dup_exact6_rate"] == pytest.approx(1 / 3, abs=5e-4)
    # 批级那一格今天就有读者（`#104`）：`#102` 电池里"取错列"那具变异当时无人读，换成
    # `collapse_round_mean` 的列这条就红。这一局的三轮全都有 ≥2 人开口，所以它是**数值**读者、
    # 不是地板读者——地板的读者在 `test_m3_gate.py` 的 `#104` 那一节。
    assert out["dup_exact6_mean"] == pytest.approx(0.1111, abs=5e-4)
    assert out["gate"] == {"passivity_rate<": 0.4, "collapse_round<": 0.35,
                           "opening_distinct_rate>": 0.8}


def test_m6_reports_the_parrot_rate_beside_the_two_it_guard(golden):
    """M6 split by team (plan §8), plus R12's self-check in the same dict.

    `stated_vs_engine_agreement` is 0.32, not ≈1: the engine's ranking and the model's own are
    genuinely different objects here. Had they converged, both other numbers would be junk
    and this is the only place that would show it.
    """
    _, res, _, _ = golden
    events = metrics.read_game(res.path).events
    out = metrics.m6_belief_action(events)
    assert (out["k"], out["n_good"], out["n_wolf"]) == (2, 10, 5)
    assert out["consistency_good"] == 1.0
    assert out["divergence_wolf"] == pytest.approx(0.2)  # 2号's day-1 pivot: good play, not a bug
    assert out["stated_vs_engine_agreement"] == pytest.approx(0.3214, abs=5e-4)
    assert out["n_agreement_sample"] == 14
    assert out["stated_vs_engine_agreement"] < 0.9, "M6 is now measuring the card, not the play"
    # k=1 gives the same answer here: no good seat in this transcript named a second suspect and
    # then voted for the first. Recorded so nobody later assumes k=2 (pre-registered in plan §8)
    # is what flattered `consistency_good` on this fixture. It is not, on this fixture.
    assert metrics.m6_belief_action(events, k=1)["consistency_good"] == 1.0


def test_m7_invents_no_latency_for_a_table_that_never_called(golden):
    """Mock logs carry no `response`, so an honest profile is empty. A zero filled in would
    look like "the endpoint is fast" in exactly the report a reviewer trusts."""
    _, res, _, _ = golden
    out = metrics.m7_cost_profile(metrics.read_game(res.path).events)
    assert out["by_phase"] == {} and out["n_calls"] == 0
    assert out["completion_total"] == 0 and out["latency_total_s"] == 0
    assert out["drift"] is None and "未标定" in out["drift_note"]


def test_m7_would_have_seen_a_real_call_and_a_drifted_one():
    """Reverse control for the test above, on a hand-built log: two calls, same predicted
    shape, one taking twice as long as the fitted model says."""
    calls = [_call(1, 1, "day_vote", lat=3.0, ct=100, pt=1000),
             _call(2, 1, "day_vote", lat=6.0, ct=100, pt=1000, transport_attempts=2)]
    consts = {"D_decode_tok_s": 100.0, "P_prefill_tok_s_best_observed": 1000.0,
              "per_call_fixed_overhead_s": 1.0}
    out = metrics.m7_cost_profile(calls, constants=consts)
    assert out["n_calls"] == 2 and out["completion_total"] == 200
    assert out["latency_total_s"] == 9.0 and out["transport_retries"] == 1
    # The exact-dict form is the point of this assertion: a key added to the profile has to be
    # noticed here. `asked_*`/`fill_rate`/`truncated` landed on 2026-09-22 with the budget-vs-used
    # reading, and this fixture's calls carry no `max_tokens` — so the honest value is None, and
    # it is pinned as None to keep "not recorded" from drifting into 0.0 later.
    assert out["by_phase"]["day_vote"] == {
        "n": 2, "latency_sum": 9.0, "lat_p50": 3.0, "lat_p95": 6.0, "lat_max": 6.0,
        "completion_sum": 200, "prompt_est_sum": 2000,
        "asked_sum": 0, "asked_n": 0, "fill_rate": None, "truncated": 0}
    assert out["asked_total"] == 0 and out["asked_calls"] == 0 and out["fill_rate"] is None
    # 1 + 1000/1000 + 100/100 = 3.0 predicted: the first call is exactly on model, the second
    # is 2× — past the 1.5× tripwire that means "the endpoint moved", not "the model is slow".
    d = out["drift"]
    assert d["verdict"] == "suspect" and d["worst"] == pytest.approx(2.0)
    assert d["best"] == pytest.approx(1.0) and d["threshold"] == 1.5
    assert metrics.m7_cost_profile(calls)["drift"] is None, "prediction without constants"


def test_m7_reports_the_share_of_the_asked_budget_the_model_actually_used():
    """The census prints what a game is *billed* at; this is how much of that was spent.

    `--dry-run`'s completion budget and the per-game wall clock are multiplied out of
    `max_tokens`, i.e. an upper bound. Once a game has actually run, the log carries both sides
    — `request.max_tokens` and the transport's `completion_tokens` — and only the ratio turns the
    bound into an expectation. Without it the plan's estimate can only be checked by hand, once,
    by someone remembering to divide two numbers that live in different files.
    """
    calls = [_call(1, 1, "day_speech", lat=2.0, ct=70, pt=900, asked=140),
             # 71, not 70: every ratio in this fixture would otherwise be exact (0.5, 1.0), and an
             # exact quotient cannot tell a rounded reading from a raw float. K5 in
             # `/tmp/mut_fillrate.py` survived on precisely that hole.
             _call(2, 1, "day_speech", lat=2.0, ct=71, pt=900, asked=140),
             _call(3, 1, "day_vote", lat=1.0, ct=60, pt=900, asked=60, finish="length")]
    out = metrics.m7_cost_profile(calls)
    assert out["asked_total"] == 340 and out["completion_total"] == 201
    assert out["fill_rate"] == 0.5912, "rounded like every other rate in this module"
    # The ratio covers only the calls that recorded a budget, and says how many that was: a log
    # where 2 of 54 calls remembered to write `max_tokens` must not print a whole-game number.
    assert out["asked_calls"] == 3 and out["n_calls"] == 3
    speech, vote = out["by_phase"]["day_speech"], out["by_phase"]["day_vote"]
    assert (speech["asked_sum"], speech["asked_n"], speech["fill_rate"]) == (280, 2, 0.5036)
    # The pair that keeps `fill_rate == 1.0` from meaning two different things: the vote filled
    # its budget *and* was cut off, so zero headroom there is a ceiling problem, while a phase
    # at 1.0 with `truncated == 0` really did answer at exactly the length it was asked for.
    assert (vote["asked_sum"], vote["fill_rate"], vote["truncated"]) == (60, 1.0, 1)
    assert speech["truncated"] == 0 and out["truncations"] == 1


def test_m7_invents_no_fill_rate_for_a_log_that_never_asked():
    """No `max_tokens` in the log gives None, never 0.0.

    0.0 is a behaviour finding — "the endpoint answered with nothing" — and it would land in the
    same column of the same report as a measured one. The log here simply did not record what was
    asked, which is a fact about the transcript, not about the model.
    """
    out = metrics.m7_cost_profile([_call(1, 1, "day_vote", lat=1.0, ct=40, pt=900)])
    assert out["asked_total"] == 0 and out["asked_calls"] == 0 and out["fill_rate"] is None
    assert out["by_phase"]["day_vote"]["fill_rate"] is None
    assert out["completion_total"] == 40, "the measured half still reads; only the ratio is withheld"
    assert "没记" in out["fill_rate_note"], "null without a reason is the same prose problem"
    # A call that recorded a budget but nothing else must not be counted into the denominator.
    half = [_call(1, 1, "day_vote", lat=1.0, ct=40, pt=900, asked=60),
            _call(2, 1, "day_vote", lat=1.0, ct=40, pt=900)]
    out = metrics.m7_cost_profile(half)
    assert (out["asked_total"], out["asked_calls"], out["n_calls"]) == (60, 1, 2)
    assert out["fill_rate"] == 0.6667, "the ratio is over the matched pair only"


def test_a_turn_without_bookkeeping_is_not_a_turn():
    """`decisions()` requires `payload.meta`, and no log the engine writes today produces a
    decision-kind event without it — so dropping the clause would change no number, and a
    mutation proves nothing about it. Pinned directly instead: a rate's denominator must not
    include an event that cannot answer for the fields the numerator reads."""
    bare = Event(seq=0, kind=Kind.VOTE, day=1, phase="day_vote", visibility="all",
                 payload={"act": "vote", "target": 1}, actor=1)
    assert metrics.decisions([bare]) == []
    assert [e.actor for e in metrics.decisions([bare, _call(2, 1, "day_vote", lat=1.0, ct=1, pt=1)])] == [2]


def test_m8_finds_the_save_that_the_legality_field_hides(golden):
    """The witch's `action` is `save_or_poison` for both potions; only `act` says which.

    Filtering on the wrong one returns 0 saves — a number that reads as "she sat on her
    medicine" and would have been believed, since nothing else in the report contradicts it.
    """
    _, res, _, _ = golden
    out = metrics.m8_strategy_proxies(metrics.read_game(res.path).events)
    assert out["witch_saves"] == 1 and out["n_knives"] == 2
    assert (out["seer_seat"], out["seer_death_day"]) == (7, None)
    assert out["seer_nights_knifed"] == 0 and out["witch_save_rate_on_seer"] is None
    # 刀查杀: the wolves knifed 9号 while the good team's top suspect was 3号.
    assert out["wolf_kill_hit_rate"] == 0.0 and out["n_knives_scored"] == 1
    assert out["vote_split_entropy"] == [pytest.approx(0.9183), 1.0, pytest.approx(0.9183)]
    assert out["deaths"][0] == (1, "exiled", 1)


def _ballot(seq: int, voter: int, target: int | None, day: int = 1) -> Event:
    return Event(seq=seq, kind=Kind.VOTE, day=day, phase="day_vote", visibility="all",
                 payload={"act": "vote" if target is not None else "pass", "target": target},
                 actor=voter)


def _wave(seq: int, tally: dict[str, int], exiled: int | None, day: int = 1) -> Event:
    return Event(seq=seq, kind=Kind.VOTE_RESULT, day=day, phase="day_vote", visibility="all",
                 payload={"tally": tally, "exiled": exiled})


def test_entropy_cannot_tell_a_unanimous_table_from_an_abstention_flood():
    """`vote_split_entropy` normalises the ballot counts away, and that is the right call for
    the thing it measures — how spread the votes are. Read alone, though, it says the same
    thing about two waves that mean the opposite: nine seats all naming 5号, and three seats
    naming 5号 while six sat out. Both are entropy 0.0.

    The distinction is not cosmetic. An exile carried by 3 of 9 is a table that barely voted,
    and a model going passive shows up *here* before it shows up in `passivity_rate` — which
    only ever counts 发言 (listen/align without a name), never a ballot.
    """
    all_in = [_ballot(i, i, 5) for i in range(1, 10)] + [_wave(10, {"5": 9}, 5)]
    flooded = [_ballot(i, i, 5) for i in range(1, 4)] \
        + [_ballot(i, i, None) for i in range(4, 10)] + [_wave(10, {"5": 3}, 5)]
    a = metrics.m8_strategy_proxies(all_in)
    b = metrics.m8_strategy_proxies(flooded)

    assert a["vote_split_entropy"] == b["vote_split_entropy"] == [0.0], \
        "这是前提不是结论：熵看不出来，所以才要另一串数"
    assert a["abstention_rate"] == 0.0
    assert b["abstention_rate"] == 0.6667, "六张弃票除以九张写出来的票，不是除以三张投出的"
    assert a["ballot_mandate"] == [1.0]
    assert b["ballot_mandate"] == [0.3333], "最高票 3 张 / 该轮应投 9 人"

    # 全员弃票 is plan §12 R10's draw warning, so it must be a number and not a gap.
    all_pass = [_ballot(i, i, None) for i in range(1, 10)] + [_wave(10, {}, None)]
    c = metrics.m8_strategy_proxies(all_pass)
    assert c["abstention_rate"] == 1.0 and c["ballot_mandate"] == [0.0]
    assert c["vote_split_entropy"] == [0.0], "熵在这里第三次沉默：满桌弃票和满桌同票一个数"
    # …and an empty log is not "zero abstention": no denominator, no number (the same
    # convention `m3_gate_verdict` refuses to score as a pass).
    assert metrics.m8_strategy_proxies([])["abstention_rate"] is None


def test_the_abstention_rate_prints_both_numbers_it_was_divided_from():
    """`abstention_rate` 是 (问到的票数 − 投出的票数) / 问到的票数，而那两个数过去只活在
    `m8_strategy_proxies` 的函数体里。读到 0.6667 的人只能选择相信分子和分母，而这一格的分母
    恰恰是全文件最容易被改动口径的一处（`#105`：分母是票数不是轮数）。

    同族的前一轮是 `#107`：一行有两个分母就把两个都印出来。这里印两个而不是一个，是因为
    "弃了几张" = asked − cast 只能由读的人自己减出来，减错的方向反过来就是"弃票率算错了"。
    """
    flooded = [_ballot(i, i, 5) for i in range(1, 4)] \
        + [_ballot(i, i, None) for i in range(4, 10)] + [_wave(10, {"5": 3}, 5)]
    out = metrics.m8_strategy_proxies(flooded)
    assert out["n_ballots_asked"] == 9 and out["n_ballots_cast"] == 3
    assert out["abstention_rate"] == round(
        (out["n_ballots_asked"] - out["n_ballots_cast"]) / out["n_ballots_asked"], 4)

    all_in = [_ballot(i, i, 5) for i in range(1, 10)] + [_wave(10, {"5": 9}, 5)]
    full = metrics.m8_strategy_proxies(all_in)
    assert (full["n_ballots_asked"], full["n_ballots_cast"]) == (9, 9)
    assert full["abstention_rate"] == 0.0

    # 空日志里两个计数各自是真的 0（问了 0 张、投出 0 张是可以核对的事实），
    # 而比率仍然是 None：没有分母就没有数，null ≠ 0（`#95`）。
    empty = metrics.m8_strategy_proxies([])
    assert empty["n_ballots_asked"] == 0 and empty["n_ballots_cast"] == 0
    assert empty["abstention_rate"] is None


def test_a_wave_that_never_settled_is_in_the_rate_and_out_of_the_per_wave_lists():
    """一轮有票没有结算记录：`abstention_rate` 的分母数它，`ballot_mandate` 和熵都不数它。

    这个不对称过去只在函数体里成立。读 audit JSON 的人看见逐轮那两串少一项、比率却照旧，
    没有任何东西告诉他两串数的分母不是同一批票。印出 `n_ballots_asked` 之后这一格变成可核对的：
    问到的票数比逐轮那两串覆盖的多，多出来的就是没结算那一轮。
    """
    unsettled = [_ballot(i, i, 5) for i in range(1, 4)] \
        + [_ballot(i, i, None) for i in range(4, 7)] \
        + [_wave(10, {"5": 3}, 5)] \
        + [_ballot(i, i, None) for i in range(7, 10)]
    out = metrics.m8_strategy_proxies(unsettled)
    assert len(out["ballot_mandate"]) == len(out["vote_split_entropy"]) == 1, "没结算那轮进不了逐轮串"
    assert out["n_ballots_asked"] == 9, "九张落盘的票都在分母里，包括没结算那轮的三张"
    assert out["n_ballots_cast"] == 3
    assert out["abstention_rate"] == round(6 / 9, 4)


def test_a_tally_with_no_ballots_behind_it_enters_neither_list():
    """`vote_split_entropy` used to walk the log hunting for `vote_result` on its own, which
    made the two per-wave lists agree only because no log so far contained a stray tally. Now
    that the wave has one owner, both lists read it: a settlement record with nobody behind it
    cannot add a number to one list and leave the other short.
    """
    out = metrics.m8_strategy_proxies([_wave(1, {}, None)])
    assert out["vote_split_entropy"] == [] and out["ballot_mandate"] == []
    assert out["vote_split_entropy_mean"] is None and out["abstention_rate"] is None


def test_the_golden_game_records_who_actually_voted(golden):
    """The same two numbers on the authored transcript, pinned because they must stay aligned
    with the entropy list — one entry per voting wave, PK revote included. Day 2 is the tie
    and its revote, so three waves, and the middle one exiled nobody: 0.5 is what "top of a
    tie" means when the denominator is the whole table.
    """
    _, res, _, _ = golden
    out = metrics.m8_strategy_proxies(metrics.read_game(res.path).events)
    assert out["abstention_rate"] == 0.0, "authored 转录 21 张票零弃票——mock 桌可不是这个数"
    assert out["ballot_mandate"] == [0.6667, 0.5, 0.6667]
    assert len(out["ballot_mandate"]) == len(out["vote_split_entropy"]) == 3


def test_m8_scores_the_witch_on_the_night_the_seer_was_knifed():
    """`witch_save_rate_on_seer` pairs by *night*, because a save names no seat — the engine
    knows the victim from the `notice`. Seer knifed twice, saved once: 0.5, and only a rule
    that looks at the right night can tell that apart from 1.0 or 0.0.
    """
    ev = [_deal(7, "seer"), _deal(5, "witch"), _deal(2, "wolf"),
          _night(2, 1, act="kill", action="kill", target=7),
          _night(5, 1, act="save", action="save_or_poison", target=None, potion="save"),
          _night(2, 2, act="kill", action="kill", target=7)]
    out = metrics.m8_strategy_proxies(ev)
    assert (out["witch_saves"], out["seer_nights_knifed"]) == (1, 2)
    assert out["witch_save_rate_on_seer"] == pytest.approx(0.5)
    # And a poison is not a save: same legality class, opposite answer. The rate must be a real
    # 0/1, not the None it returns when the seer was never knifed.
    ev2 = [_deal(7, "seer"), _deal(5, "witch"),
           _night(2, 1, act="kill", action="kill", target=7),
           _night(5, 1, act="poison", action="save_or_poison", target=2, potion="poison")]
    out2 = metrics.m8_strategy_proxies(ev2)
    assert out2["witch_saves"] == 0 and out2["seer_nights_knifed"] == 1
    assert out2["witch_save_rate_on_seer"] == 0.0

