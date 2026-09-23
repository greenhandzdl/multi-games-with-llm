"""Layered compaction of the public chronicle (region B). Pure: no LLM, no I/O.

Summaries here are *template-generated from event payloads*, not model-written. That is a
deliberate limit, not an oversight: a summarising call would put a nondeterministic
component in the middle of the cached prefix, cost completion tokens to save prompt
tokens, and make `test_purity` moot. The engine knows who accused whom and how the votes
fell; it does not need a model to tell it.

Only public events (visibility="all") are eligible. Private material lives in region C
and is never compressed (plan §5 sacrifice order item ⑤) — and keeping it out of B is
also what lets every seat share the same B bytes, which is where the prefix-cache win
comes from.

This module *builds* the fold's record but cannot write it: appending is I/O, so
`agent.Agent` is the one that turns `fold_body()`'s output into a `Kind.COMPACTION` event.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import TokenBudget
from .events import Event, Kind
from .info import eid


@dataclass(frozen=True)
class FoldPlan:
    """How much of the chronicle stays verbatim, and which days were folded away."""

    window: int
    folded_days: tuple[int, ...]
    rounds: int

    @property
    def folded(self) -> bool:
        return bool(self.folded_days)


CAUSE_ZH = {"wolf_kill": "被狼刀", "poison": "被毒", "exiled": "被投票出局", "hunter_shot": "被猎人带走"}
# The seer's report is a game fact stored as an enum; the prompt is Chinese prose, so the
# translation belongs in the renderer rather than in the log, where it would lose its type.
VERDICT_ZH = {"wolf": "狼人", "good": "好人", "unknown": "无法确定"}
TEAM_ZH = {"wolf": "狼人", "good": "好人"}


def render_line(e: Event) -> str:
    """One event, one line, byte-stable forever. The only renderer in the codebase.

    No timestamps and no relative wording: a render function that reads the clock makes
    region B's bytes differ between turns for no reason, which silently voids every
    prefix-cache assumption and shows up only as unexplained latency drift.

    Every `Kind` gets an explicit branch. The fallback deliberately does **not** print the
    payload: a raw dict in the prompt is unreadable to the model, and dict repr is not
    byte-stable across a change of key insertion order. An unhandled kind should look
    broken to a human reading the transcript — the companion test asserts the table is
    total, so that branch is a tripwire, never a code path.
    """
    tag = eid(e.seq)
    p, k = e.payload, e.kind
    if k == Kind.SPEECH:
        return f"[{tag}] {e.actor}号：{_said(p)}"
    if k == Kind.LAST_WORDS:
        return f"[{tag}] {e.actor}号（遗言）：{_said(p)}"
    if k == Kind.DEATH:
        return f"[{tag}] 法官：{p.get('seat')}号出局（{CAUSE_ZH.get(str(p.get('cause')), '死亡')}）。"
    if k == Kind.VOTE:
        t = p.get("target")
        return f"[{tag}] 投票：{e.actor}号→{t}号" if t is not None else f"[{tag}] 投票：{e.actor}号弃票"
    if k == Kind.VOTE_RESULT:
        return f"[{tag}] 法官：{p.get('summary') or _vote_summary(p)}"
    if k == Kind.PHASE:
        return f"[{tag}] 法官：{p.get('text', '')}"
    if k == Kind.GAME_START:
        return f"[{tag}] 法官：开局座位 {'、'.join(str(s) for s in p.get('seats', ()))}号。"
    if k == Kind.COMPACTION:
        return f"[{tag}] 法官：[折叠] {p.get('summary', '')}"
    if k == Kind.GAME_OVER:
        camp = p.get("winner")
        if camp is None:
            return f"[{tag}] 法官：本局没有阵营获胜（{p.get('terminal', '')}）。"
        return f"[{tag}] 法官：{TEAM_ZH.get(str(camp), str(camp))}阵营获胜（{p.get('terminal', '')}）。"
    if k == Kind.DEAL:
        return f"[{tag}] 法官（私发）：你的身份是 {p.get('role')}。"
    if k == Kind.NIGHT_ACTION:
        return f"[{tag}] {e.actor}号（夜间行动）：{_night_text(p)}"
    if k == Kind.WOLF_CHAT:
        return f"[{tag}] 狼队私聊 {e.actor}号：{_said(p)}"
    if k == Kind.SEER_RESULT:
        verdict = VERDICT_ZH.get(str(p.get("verdict")), str(p.get("verdict")))
        return f"[{tag}] 法官（私发）：你查验的{p.get('target')}号是{verdict}。"
    if k == Kind.NOTICE:
        return f"[{tag}] 法官（私发）：{p.get('text', '')}"
    return f"[{tag}] （未渲染事件 {k}）"


NIGHT_ZH = {"kill": "刀", "check": "查验", "save": "用解药", "poison": "毒",
            "shoot": "开枪", "pass": "没有行动"}


def _said(p: dict) -> str:
    """An empty utterance is a choice the engine should *say*, not a blank line: a seat that
    declined to speak rendered as `4号（遗言）：` with nothing after it, which reads as a
    renderer bug to everyone but the log."""
    text = str(p.get("text", "")).strip()
    return text if text else "（沉默）"


def _night_text(p: dict) -> str:
    """Renders the act the actor *chose*, not the task label the phase was asked to run.

    `payload["action"]` is the prompt's task name ("save_or_poison"), which stays useful in
    C4; printing it here made the witch's turn read `save_or_poison→None` — a string that
    describes a request as if it were an answer.
    """
    act = str(p.get("act", ""))
    verb = NIGHT_ZH.get(act, act or "未知行动")
    tgt = p.get("target")
    if act == "pass" or tgt is None:
        return f"{verb}。"
    return f"{verb}{tgt}号。"


def _vote_summary(p: dict) -> str:
    """Derived from the tally rather than stored alongside it: a second copy of the truth
    in the log is a second thing that can disagree with the first."""
    tally = p.get("tally") or {}
    if not tally:
        return "无人被投票出局。"
    return "票型：" + "、".join(f"{s}号{n}票" for s, n in sorted(tally.items(), key=lambda kv: str(kv[0]))) + "。"


def day_fold_lines(day: int, events: list[Event] | tuple[Event, ...]) -> str:
    """A folded day: accusations, vote shape, who left. Bounded by construction.

    Counting accusations is the useful part — "票型分散" is a fact the engine can state
    exactly, where a model would have to re-derive it from 40 lines it can no longer see.
    """
    speeches = [e for e in events if e.kind == Kind.SPEECH]
    votes: dict[int, int] = {}
    for e in events:
        if e.kind == Kind.VOTE and isinstance(e.payload.get("target"), int):
            votes[e.payload["target"]] = votes.get(e.payload["target"], 0) + 1
    deaths = [f"{e.payload.get('seat')}号{CAUSE_ZH.get(str(e.payload.get('cause')), '')}"
              for e in events if e.kind == Kind.DEATH]
    acc: dict[int, int] = {}
    for e in speeches:
        t = e.payload.get("target")
        if e.payload.get("act") in ("accuse", "pivot", "align") and isinstance(t, int):
            acc[t] = acc.get(t, 0) + 1
    bits = [f"第{day}天：发言{len(speeches)}人。"]
    if acc:
        top = sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        bits.append("被指向最多：" + "、".join(f"{s}号×{n}" for s, n in top) + "。")
    if votes:
        bits.append("票型：" + "、".join(f"{s}号{n}票" for s, n in sorted(votes.items())) + "。")
    bits.append("出局：" + ("、".join(deaths) if deaths else "无人") + "。")
    return " ".join(bits)


def chronicle(percept_events: tuple[Event, ...]) -> tuple[Event, ...]:
    """The chronicle: what region B renders line by line.

    Named apart from `Percept.public` on purpose. A `Kind.COMPACTION` marker is public — every
    seat should be able to learn that the table was summarised — but it is not chronicle
    material. Two reasons, one cheap and one expensive: the marker's line is a summary *of*
    this block, so letting it in has a summary summarising itself; and it holds a fresh `seq`
    inserted into the middle of the timeline, which rewrites the bytes every seat had already
    cached (plan §5 sells the whole latency model on those bytes only ever appending).
    """
    return tuple(e for e in percept_events
                 if e.visibility == "all" and e.kind != Kind.COMPACTION)


WINDOW_BOUNDARIES = (8, 12, 18, 27, 40, 60, 90)


def boundary_for(n: int, cap: int) -> int:
    """The largest window the schedule allows for `n` events, never above `cap`.

    A fixed window means the front of region B moves one event per turn once history
    overflows it — and every such move rewrites the bytes after the cached prefix, so the
    cache is flushed on *every* utterance. Measured on a mock game: 16-event cap, front
    moved 33 times in 3 days. Geometric boundaries turn that into ~4 flushes per game,
    which is the whole reason plan §5 specifies 8→12→18→27 rather than "keep the last 16".
    """
    for b in WINDOW_BOUNDARIES:
        if n <= b:
            return min(b, cap)
    return min(WINDOW_BOUNDARIES[-1], cap)


def plan_fold(
    events: tuple[Event, ...],
    *,
    est,
    start_window: int = WINDOW_BOUNDARIES[-1],
    min_window: int = 4,
    b2_cap: int = 1500,
    shrink: int = 0,
) -> FoldPlan:
    """Pick the verbatim window by folding **whole days**, then let `shrink` cut it further.

    The front of B2 moves only at a day boundary. That is the whole point: a budget-derived
    fixed *count* of verbatim events slides one event forward on every utterance once history
    is longer than the window, and every such slide rewrites the bytes after the cached prefix.
    Measured on a mock game with `regions.b2=400` (54 calls): 2 declared compactions but 15
    head rewrites — `boundary_for`'s geometric ladder was holding the front still, and the
    halving loop undid it the moment the budget bound. Default budgets never reach that branch,
    which is why six prefix assertions were passing vacuously.

    So the lever that moves the front — `b2_cap`, measured with `est`, and nothing else: an
    earlier signature also demanded a whole `TokenBudget`, which no line below ever read, so
    changing a token constant looked like it would deepen the fold when it could not — now
    moves in days: fold the oldest verbatim day, re-check, repeat.
    Flights of flushes are bounded by the day cap (`Config.max_days`), which is the same ≤5
    plan §11 asks of compactions. If even the current day alone does not fit, B2 overshoots
    its soft region budget rather than sliding — the hard ceiling is `shrink`'s job, and
    `shrink` is counted and reported (`res.shrinks`), because a mid-day cut *is* a slide.

    `shrink` is the overflow lever (plan §5 牺牲顺序 ①): each pull halves whatever the day
    folding left, on the way out. It halves the *result* rather than the `start_window` cap
    because the cap is above the boundary for any chronicle under ~90 events, which left two
    of the three pulls inert and a 400-fix indistinguishable from a resend.
    """
    events = chronicle(events)
    n = len(events)
    verbatim_from = max(0, n - boundary_for(n, start_window))
    day_starts = [i for i in range(n) if i == 0 or events[i - 1].day != events[i].day]
    rounds = 0
    while True:
        folded = sorted({e.day for e in events[:verbatim_from]})
        size = est(chrono_bytes(events, folded, n - verbatim_from))
        ahead = [i for i in day_starts if i > verbatim_from]
        # The floor is a *day* floor: stop once what is left is smaller than `min_window`, and
        # let B2 overshoot its soft region budget. Folding away a two-line day to satisfy a cap
        # would leave the seat looking at nothing but summaries of the moment it is in.
        if size <= b2_cap or not ahead or n - ahead[0] < min_window:
            break
        verbatim_from = ahead[0]
        rounds += 1
    window = n - verbatim_from
    for _ in range(max(0, shrink)):
        window = max(min_window, window // 2)
    folded = sorted({e.day for e in events[:max(0, n - window)]})
    return FoldPlan(window=window, folded_days=tuple(folded), rounds=rounds)


def chrono_bytes(events: tuple[Event, ...], folded_days: tuple[int, ...], window: int) -> str:
    """B1 (folded days) then B2 (verbatim tail). Order is fixed so the prefix stays stable."""
    events = chronicle(events)
    out: list[str] = []
    if folded_days:
        out.append("== 已折叠 ==")
        out.append(fold_body(events, folded_days))
        out.append("== 最近发言 ==")
    for e in events[-window:] if window else ():
        out.append(render_line(e))
    return "\n".join(out)


def fold_body(events: tuple[Event, ...] | list[Event], folded_days: tuple[int, ...]) -> str:
    """The B1 block on its own, because a COMPACTION marker has to store exactly that.

    Split out of `chrono_bytes` rather than reimplemented at the write site: "summarise a day"
    is one predicate, and a second copy is a summary that can disagree with the prompt the
    model was actually sent — which is the only claim the marker is making.
    """
    events = chronicle(events)
    return "\n".join(day_fold_lines(d, [e for e in events if e.day == d]) for d in folded_days)


def estimate_tokens(text: str, b: TokenBudget | None = None) -> int:
    """Char-class estimator, calibrated on this endpoint (see TokenBudget comments).

    Deliberately not a SentencePiece port: shipping a copy of Gemma's tokenizer to save
    one HTTP round trip is the kind of certainty this project doesn't have, and the
    estimator only has to err high. `usage.prompt_tokens` from every real response is
    reaped back into the caller's calibration, so the number gets better with play.
    """
    b = b or TokenBudget()
    wide = sum(1 for ch in text if ord(ch) > 0x7F)
    return int(wide * b.tok_per_cjk + (len(text) - wide) * b.tok_per_ascii) + 1
