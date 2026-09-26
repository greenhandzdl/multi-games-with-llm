"""Engine-maintained belief: what a seat's visible evidence actually supports.

Two beliefs exist on purpose and are never merged (plan §4):

* **engine belief** — this module. Derived from the seat's own visible events, by code.
* **stated belief** — what the model declares in its JSON `belief` slot.

Their comparison is the parrotty rate. If the card we inject *were* the stated belief,
M6 would measure its own copy and mean nothing, so `render_card` deliberately states
facts and claims and **never the ranking**. The ranking is still computed, because the
engine needs it for default actions and for scoring.

Rebuilt from the log every turn rather than updated incrementally: the log is small, and
a derived value cannot drift away from the transcript it is supposed to summarise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .events import Event, Kind
from .info import eid

# Per-day forgetting curve. 0.85 is a judgement call, not a measurement, so it lives in
# one place and is part of config_hash via `belief_version` in the caller.
DAY_DECAY = 0.85

ACT_WEIGHT = {
    "accuse": 0.7,
    "pivot": 0.4,
    "align": 0.5,
    "probe": 0.2,
    "defend": -0.1,
    "listen": 0.0,
}


@dataclass(frozen=True)
class Claim:
    """One entry in the ledger. `label` is deliberately not called `kind`: `kind` names an
    event type (see events.Kind) and this is a derived speech-act label. Two vocabularies
    sharing one field name is how the split-brain in test_wiring.py became possible."""

    seq: int
    day: int
    actor: int
    label: str           # accuse | defend | align | probe | pivot | listen | vote | role_claim | seer_verdict | contested_seer
    target: int | None
    note: str = ""


@dataclass
class BeliefState:
    observer: int
    claims: list[Claim] = field(default_factory=list)
    suspicion: dict[int, float] = field(default_factory=dict)
    credibility: dict[int, float] = field(default_factory=dict)
    day: int = 1
    alive: tuple[int, ...] = ()
    dead: tuple[int, ...] = ()

    def top_suspects(self, k: int = 3) -> list[int]:
        ranked = sorted(self.suspicion.items(), key=lambda kv: (-kv[1], kv[0]))
        return [s for s, v in ranked if v > 0][:k]

    def score(self, seat: int) -> float:
        return round(self.suspicion.get(seat, 0.0), 3)


def _decayed(day_of: int, now_day: int) -> float:
    return DAY_DECAY ** max(0, now_day - day_of)


def build_belief(observer: int, events: list[Event] | tuple[Event, ...]) -> BeliefState:
    """Fold a seat's visible events into a belief state.

    Only `vote`, `speech`, `seer_result` and `role_claim` move the numbers.
    Anything the seat cannot see is not in `events` at all (see info.percept_for), so
    there is no visibility check left to do here — which is the point.
    """
    evs = list(events)
    day = evs[-1].day if evs else 1
    st = BeliefState(observer=observer, day=day)
    seer_claimants: set[int] = set()
    contested: set[int] = set()

    for e in evs:
        if e.kind == Kind.DEAL:
            continue
        if e.kind == Kind.GAME_START:
            st.alive = tuple(e.payload.get("seats", ()))
            continue
        if e.kind == Kind.DEATH:
            s = e.payload.get("seat")
            if isinstance(s, int):
                st.alive = tuple(x for x in st.alive if x != s)
                st.dead += (s,)
            continue

        actor = e.actor
        payload = e.payload
        kind = e.kind

        if kind == Kind.SPEECH and actor is not None:
            act = str(payload.get("act", ""))
            target = payload.get("target")
            w = ACT_WEIGHT.get(act, 0.0) * st.credibility.get(actor, 1.0) * _decayed(e.day, day)
            if isinstance(target, int) and w:
                st.suspicion[target] = st.suspicion.get(target, 0.0) + w
            text = str(payload.get("text", ""))
            if actor != observer:
                st.claims.append(Claim(e.seq, e.day, actor, act, target if isinstance(target, int) else None,
                                       text[:40]))
                role = claimed_role(text)
                if role:
                    st.credibility[actor] = st.credibility.get(actor, 1.0) * (1.2 if role == "seer" else 1.0)
                    st.claims.append(Claim(e.seq, e.day, actor, "role_claim", None, role))
                    _contest_seers(st, seer_claimants, contested, actor, e.seq, e.day)

        elif kind == Kind.VOTE and actor is not None:
            target = payload.get("target")
            if isinstance(target, int):
                w = 1.0 * st.credibility.get(actor, 1.0) * _decayed(e.day, day)
                st.suspicion[target] = st.suspicion.get(target, 0.0) + w
                st.claims.append(Claim(e.seq, e.day, actor, "vote", target))

        elif kind == Kind.SEER_RESULT and observer == actor:
            target = payload.get("target")
            verdict = payload.get("verdict")
            if isinstance(target, int) and verdict == "wolf":
                st.suspicion[target] = st.suspicion.get(target, 0.0) + 3.0
            st.claims.append(Claim(e.seq, e.day, observer, "seer_verdict",
                                   target if isinstance(target, int) else None, str(verdict)))

    for s in (*st.dead,):
        st.suspicion.pop(s, None)  # a dead seat cannot be voted for; keeping it would skew fallback picks
    return st


ROLE_WORDS = {"预言家": "seer", "女巫": "witch", "猎人": "hunter"}
FIRST_PERSON = ("我", "本人", "在下")


def claimed_role(text: str) -> str | None:
    """Whom does this speaker say they are? Derived from the words, not from a model call.

    Narrow on purpose, and it must stay narrow: a *report* about someone else's claim
    ("1号说他是预言家") must not count, or the engine would mark the real seer contested
    on the strength of a villager repeating a lie. So it requires a first-person marker in
    the same clause as the role word. It will miss oblique self-claims ("昨晚那位睁眼的"),
    which is the correct direction to fail — a missed claim costs one credibility bump, a
    false one poisons 对跳, the most load-bearing inference in the ledger.
    """
    for clause in re.split(r"[，。！？；,.!?;\s]", text):
        if not any(w in clause for w in FIRST_PERSON):
            continue
        for word, role in ROLE_WORDS.items():
            if word in clause:
                return role
    return None


def _contest_seers(
    st: BeliefState, claimants: set[int], contested: set[int], actor: int, seq: int, day: int
) -> None:
    """对跳: the second living seat to jump as 预言家 halves *both* their credibility.

    The one inference worth hard-coding, because it is what the game turns on and it is
    mechanical — no language understanding, just two first-person jumps.

    It runs mid-pass, not as an epilogue, and that is the whole behaviour. Discounting at
    the end of the loop mutates a dictionary nobody reads again, so the earlier version of
    this function did nothing at all. Real players distrust a jump *after* it is rivalled:
    accusations made before the contest keep face value, accusations after it are halved.
    """
    claimants.add(actor)
    if len(claimants) < 2 or actor in contested:
        return
    for c in claimants:
        if c not in contested:
            contested.add(c)
            st.credibility[c] = st.credibility.get(c, 1.0) * 0.5
    st.claims.append(Claim(seq=seq, day=day, actor=st.observer, label="contested_seer",
                           target=None, note=",".join(str(c) for c in sorted(claimants))))


def render_card(st: BeliefState, max_claims: int | None = None) -> str:
    """Region C2. Facts and claims, no ranking, no advice.

    The forbidden move is writing "所以你最该怀疑5号": the model would then agree with us
    on cue and stated_vs_engine_agreement would measure the copy, not the reasoning
    (plan §12 R12). It gets the ledger and draws its own conclusion.

    `max_claims` is the lever region C pulls when the card itself is what pushes C past
    `regions.c_total`. It keeps the **newest** k lines, the same end of the list the default
    window keeps; `0` means "no accusations", which also drops the table header rather than
    pointing it at an empty list. The seat's own check result is not part of that count — it
    is the one line region B can never carry, so it survives every setting of the lever.
    """
    lines = [f"第{st.day}天。"]
    if st.alive:
        lines.append(f"存活：{'、'.join(str(s) for s in st.alive)}号。")
    if st.dead:
        lines.append("已出局：" + "、".join(f"{s}号" for s in st.dead) + "。")
    claims = [c for c in st.claims if c.actor != st.observer]
    if max_claims is not None:
        claims = claims[-max_claims:] if max_claims else []
    if claims:
        lines.append("公开主张（按发生顺序）：")
        for c in claims[-14:]:
            tag = eid(c.seq) if c.seq else "推断"
            arrow = f"→{c.target}号" if c.target is not None else ""
            label = c.label or "发言"
            # The note survives the fold: once region B compresses that day away, this
            # 40-char trace is the only remaining evidence of *why* the seat pointed.
            note = f"：{c.note}" if c.note else ""
            lines.append(f"- [{tag}] {c.actor}号 {label}{arrow}{note}")
    own = [c for c in st.claims if c.actor == st.observer and c.label == "seer_verdict"]
    if own:
        lines.append("你自己的查验记录：" + "；".join(f"{c.target}号={c.note}" for c in own))
    return "\n".join(lines)
