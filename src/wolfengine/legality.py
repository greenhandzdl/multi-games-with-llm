"""Legality gate: rung 4, and the only place where the engine refuses a model's output.

Two severities, and the split is the design (plan §2 principle 1):

* **hard** — night actions and votes. A structured payload with one legal target set;
  refusing costs a retry of ~40 tokens and prevents a broken game.
* **soft** — daytime speech. Flagged, logged, never blocked. Blocking speech would
  interrupt a large share of turns to enforce something the audience cannot verify, and
  a smooth-talking wolf *should* be able to say "昨晚我好像听见动静" — the engine's job is
  to record that as a lie, not to silence it.

Softness covers the *sentence*. Two fields are addressed to the engine rather than to the
table and stay hard even in a speech turn: a citation id that does not exist, and a speech
act the engine assigned and the seat did not take. Re-asking either costs no words.

The citation check is what makes hallucinated private evidence cheap to detect: `evidence`
must name engine event ids, so "did the player invent something they cannot know" becomes
set membership instead of a second model judging the first one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .info import Percept
from .schema import Action
from .state import LegalSet, Phase

HARD_PHASES = frozenset({Phase.NIGHT_WOLF, Phase.NIGHT_WITCH, Phase.NIGHT_SEER,
                         Phase.DAY_VOTE, Phase.HUNTER_SHOT})

TARGETLESS_ACTS = frozenset({"pass", "last_words", "discuss", "defend", "listen", "save"})

FIRST_PERSON = ("我", "本人", "我自己", "俺", "咱")
PERCEPT_VERBS = ("听到", "听见", "看到", "看见", "察觉", "发现", "梦到", "闻到", "感觉到", "注意到", "瞅见")
NIGHT_WORDS = ("昨晚", "夜里", "半夜", "晚上", "夜里头", "昨天夜里", "今晚")

# Seats with no night action cannot legitimately perceive anything at night. The seer and
# the witch may report *their own* action, but not an ambient sound — so "听到狼叫" is
# impossible for every role, which is the actual bug class this dictionary exists for.
NIGHT_ACTORS = frozenset({"wolf", "witch", "seer"})

SPEECH_SOFT_LIMIT = 140


@dataclass
class Verdict:
    ok: bool = True
    violations: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # soft: recorded, never retried
    citation_stats: dict[str, object] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return "; ".join(self.violations)


def check_action(
    action: Action | None,
    *,
    legal: LegalSet,
    percept: Percept,
    phase: Phase,
    role: str = "",
    hard: bool | None = None,
    known_ids: frozenset[str] | None = None,
) -> Verdict:
    v = Verdict()
    if action is None:
        v.ok = False
        v.violations.append("no_action_parsed")
        return v
    strict = HARD_PHASES.__contains__(phase) if hard is None else hard

    # `pass` is legal by flag, not by membership: rules.py grants 弃票 through `allow_pass`
    # on votes, and a gate that only read `acts` would refuse a legal abstention, burn the
    # retry, and end the day with the engine voting on a seat's behalf.
    allowed = set(legal.acts) | ({"pass"} if legal.allow_pass else set())

    if action.act not in allowed:
        _add(v, strict, f"act_not_allowed:{action.act} (只能 {sorted(allowed)})")

    if legal.assigned_act and action.act != legal.assigned_act:
        # Hard even in a soft phase, on the same argument as an invented citation: this field
        # is addressed to the engine, not to the other players. Refusing it silences nobody —
        # the re-ask leaves the wording entirely the model's — and without it the required
        # speech act (plan §7 item 1) is a suggestion, so `passivity_rate` would be measuring
        # the model's own default behaviour instead of behaviour under assignment pressure.
        # `test_every_act_the_assigner_can_draw_is_one_speech_may_answer_with` is what keeps
        # this satisfiable: an assigned act the rules never grant would be an unwinnable turn.
        _add(v, True, f"act_not_as_assigned:{action.act} (法官指派 {legal.assigned_act})")

    if action.target is not None:
        # A number is a claim about the table whoever typed it. `agent.py` writes `target` for
        # every decision, targetless acts included, so a seat the judge never granted would
        # reach the log wearing the same shape as one it did.
        if action.target not in legal.targets:
            _add(v, strict, f"target_not_legal:{action.target} (只能 {sorted(legal.targets)})")
    elif action.act not in TARGETLESS_ACTS and action.act in allowed:
        _add(v, strict, f"target_required_for:{action.act}")

    if action.potion and action.potion != action.act:
        _add(v, strict, f"potion_act_mismatch:{action.potion}!={action.act}")
    if action.potion and action.potion not in legal.consumables:
        _add(v, strict, f"potion_unavailable:{action.potion} (剩 {list(legal.consumables)})")

    cite = _check_citations(action, percept, known_ids=known_ids)
    v.citation_stats = cite
    # 编造事件编号 is hard even in speech: the citation is addressed to the engine, not to
    # the other players, so refusing it costs nothing and stops the transcript lying.
    if cite["invented"]:
        _add(v, True, f"invented_event_ids:{sorted(cite['invented'])[:5]}")
    if strict and cite["not_visible"]:
        _add(v, True, f"cited_events_not_visible_to_seat:{sorted(cite['not_visible'])[:5]}")

    if action.speech:
        imp = impossible_percept(action.speech, role=role)
        if imp:
            # Soft by design: it is evidence of the thing M4 counts, and blocking it would
            # delete the measurement along with the behaviour.
            v.flags.append(f"impossible_percept:{imp}")
        if len(action.speech) > SPEECH_SOFT_LIMIT:
            v.flags.append(f"speech_too_long:{len(action.speech)}")
        if not action.speech.strip():
            v.flags.append("speech_empty")
    return v


def _add(v: Verdict, strict: bool, msg: str) -> None:
    (v.violations if strict else v.flags).append(msg)
    if strict:
        v.ok = False


def _check_citations(
    action: Action, percept: Percept, *, known_ids: frozenset[str] | None = None
) -> dict[str, object]:
    """Three buckets, because "no citation", "citation from a place you can't see" and
    "citation to an event that never happened" are three different failures.

    `not_visible` should be unreachable — a Percept cannot hold an invisible event — so a
    nonzero count there means an isolation bug, not a model bug. `invented` is the model
    lying about its evidence, and is the one M4 counts.

    Telling those two apart needs the set of ids that exist in the *whole log*, which a
    single seat's percept deliberately does not have. So `known_ids` comes from the
    caller (agent.py, which holds the log). Without it the gate cannot tell a lie from a
    leak, and reports everything as not_visible — which is why it is a parameter rather
    than a default guess: the metric must not silently change meaning by caller.
    """
    have = percept.id_set
    cited = {str(c).strip().lower() for c in action.evidence if str(c).strip()}
    valid = cited & have
    malformed = {c for c in cited - have if not re.fullmatch(r"e\d+", c)}
    unknown = cited - have - malformed
    if known_ids is None:
        not_visible, invented = unknown, malformed
    else:
        not_visible = {c for c in unknown if c in known_ids}
        invented = (unknown - not_visible) | malformed
    return {
        "cited": sorted(cited),
        "valid": sorted(valid),
        "not_visible": sorted(not_visible),
        "invented": sorted(invented),
        "malformed": sorted(malformed),
        "uncited": not cited,
    }


def impossible_percept(speech: str, *, role: str) -> str | None:
    """Flag a first-person night perception the speaker's role could not have had.

    Deliberately lexical rather than semantic (plan §13 rejects LLM-as-judge here): it can
    be unit-tested, it costs nothing, and it errs toward missing cases, not inventing them.
    M4 reports the rate as a lower bound and says so.
    """
    for nw in NIGHT_WORDS:
        if nw not in speech:
            continue
        seg = speech[max(0, speech.find(nw) - 24): speech.find(nw) + 24]
        if not any(fp in seg for fp in FIRST_PERSON):
            continue
        for vb in PERCEPT_VERBS:
            if vb in seg:
                if role in NIGHT_ACTORS and vb in ("听到", "听见", "闻到", "瞅见", "看到", "看见"):
                    return f"{nw}+{vb}"
                if role not in NIGHT_ACTORS:
                    return f"{nw}+{vb}"
    return None


def default_action(legal: LegalSet, belief_top: Iterable[int]) -> Action:
    """Engine's own choice after a failed retry: keep the game moving, mark the book.

    The rules differ per act because the *cost of a wrong default* differs:

    * `kill` must still happen. A wolf turn that silently passes hands the good team a free
      night, and the audience cannot tell an engine pass from a model pass.
    * `vote` falls back to abstaining, never to a fabricated ballot. The tally is a public
      fact that every other seat folds into its belief; inventing one would corrupt nine
      players' reasoning to spare the game a slow day. An abstention is at least honest
      about itself, and `persona.py` escalates the pressure on repeat abstainers anyway.
    * speech takes the act the engine *assigned* this turn, so the fallback cannot quietly
      become the most passive option and inflate `passivity_rate` by construction.

    Always returns something inside `legal`, which is the point: a default the gate then
    refuses would log a violation for an action the engine itself chose.
    """
    tops = [t for t in belief_top if t in legal.targets]
    ranked = sorted(legal.targets)

    def pick() -> int | None:
        return tops[0] if tops else (ranked[0] if ranked else None)

    def may_pass() -> bool:
        return legal.allow_pass or "pass" in legal.acts

    if "kill" in legal.acts:
        return Action(act="kill", target=pick(), speech="", evidence=[])
    if "check" in legal.acts and legal.targets:
        return Action(act="check", target=pick(), speech="", evidence=[])
    if may_pass():
        return Action(act="pass", speech="", evidence=[])
    if legal.assigned_act in legal.acts:
        act = str(legal.assigned_act)
        tgt = pick() if act not in TARGETLESS_ACTS else None
        return Action(act=act, target=tgt, speech="", evidence=[])
    for act in legal.acts:
        if act in TARGETLESS_ACTS:
            return Action(act=act, speech="", evidence=[])
    if legal.acts:
        return Action(act=legal.acts[0], target=pick(), speech="", evidence=[])
    return Action(act="pass", speech="", evidence=[])
