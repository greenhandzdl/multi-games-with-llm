"""Metric primitives.

Text-level helpers live here because the calibration script needs them before any
game log exists. Event-log-based metrics (M1-M8 in the plan) are appended below as
they are implemented; every one is a pure function of a log so it can be recomputed
offline without touching the endpoint.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import os
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from . import roles
from .config import DRIFT_RATIO
from .events import Event, EventLog, Kind, seq_damage, torn_extent, voting_waves
from .info import percept_for
from .belief import build_belief
from .prompts.templates import EXAMPLE_EVENT_IDS

# --------------------------------------------------------------------------
# M5 primitives: template collapse
# --------------------------------------------------------------------------


def char_ngrams(text: str, n: int = 4) -> Counter:
    """Character n-gram multiset.

    Deliberately unsegmented: jieba would add a dependency and a failure mode
    (segmentation of colloquial werewolf speech is itself ambiguous) for no gain,
    since we only need similarity, not meaning.
    """
    t = "".join(text.split())
    if len(t) < n:
        return Counter([t] if t else [])
    return Counter(t[i : i + n] for i in range(len(t) - n + 1))


def jaccard(a: str, b: str, n: int = 4) -> float:
    """Multiset Jaccard (intersection over union of counts) of char n-grams.

    0 = no shared n-grams, 1 = identical. This is `collapse_round`'s pairwise unit;
    the M3 gate threshold is 0.35 on its within-round mean.
    """
    ga, gb = char_ngrams(a, n), char_ngrams(b, n)
    if not ga and not gb:
        return 1.0
    inter = sum((ga & gb).values())
    union = sum((ga | gb).values())
    return inter / union if union else 1.0


def collapse_round(speeches: list[str], n: int = 4) -> float:
    """Mean pairwise char-4-gram Jaccard within one round. M3 secondary criterion."""
    if len(speeches) < 2:
        return 0.0
    vals = [jaccard(a, b, n) for a, b in combinations(speeches, 2)]
    return sum(vals) / len(vals)


def opening_distinct_rate(speeches: list[str], prefix_chars: int = 8) -> float:
    """Fraction of distinct first-8-character openings. 1.0 = all differ."""
    if not speeches:
        return 1.0
    openings = {"".join(s.split())[:prefix_chars] for s in speeches if s.strip()}
    return len(openings) / len([s for s in speeches if s.strip()])


def shared_substring_rate(speeches: list[str], min_len: int = 6) -> float:
    """dup_exact6_rate: fraction of speeches sharing a >=min_len literal run with another.

    Catches verbatim recycling, which n-gram Jaccard can dilute away in long text.
    """
    norm = ["".join(s.split()) for s in speeches]
    grams: dict[str, int] = Counter()
    for t in norm:
        for i in range(len(t) - min_len + 1):
            grams[t[i : i + min_len]] += 1
    repeated = {g for g, c in grams.items() if c >= 2}
    hits = sum(1 for t in norm if any(g in t for g in repeated))
    return hits / len(norm) if norm else 0.0


def template_top_fragments(speeches: list[str], min_len: int = 6, min_count: int = 3) -> list[tuple[str, int]]:
    """The literal runs that recur across the batch — '我先听听大家的发言' detection.

    Returns **maximal** runs shared by >= min_count speeches, longest first. Feed the top
    entries back into prompt region C4 as an anti-repetition blacklist (plan §7 item 3).
    """
    norm = ["".join(s.split()) for s in speeches]
    in_which: dict[str, set[int]] = {}
    for idx, t in enumerate(norm):
        for i in range(len(t) - min_len + 1):
            in_which.setdefault(t[i : i + min_len], set()).add(idx)
    # 每个 6 字窗口左右各长到"所有出现位置的邻居不再一致"为止。下面那段"丢掉被包含的较短
    # 片段"从来没能把窗口合成一句话：所有候选都正好 `min_len` 长，长度相同就谁也包含不了谁。
    # 实测（10:22:33Z，修前）三句话共享十个字，回到 C4 的是
    # 「我先听听大家；先听听大家的；听听大家的发；听大家的发言」——四个滑窗把四个名额全花光，
    # 第二个模板一个字都没进提示词，而 `docs/metrics.md` 写着这个指标"专盯『我先听听大家的
    # 发言』这类 ≥6 字重复片段"。
    runs = {_grow(g, norm) for g, v in in_which.items() if len(v) >= min_count}
    frags = [(g, sum(1 for t in norm if g in t)) for g in runs]
    frags.sort(key=lambda kv: (-kv[1], -len(kv[0])))
    kept: list[tuple[str, int]] = []
    for g, c in frags:
        if not any(g in k for k, kc in kept if kc >= c):
            kept.append((g, c))
    return kept


def _grow(g: str, norm: list[str]) -> str:
    """Extend `g` while **every** occurrence shares the same neighbour, both directions.

    Growth can't loosen the threshold: the extended run appears in exactly the speeches the
    seed did. It stops at a text's edge rather than guessing a neighbour from one context.
    """
    while True:
        spots = [(t, j) for t in norm for j in _at(t, g)]
        right = {t[j + len(g)] if j + len(g) < len(t) else None for t, j in spots}
        if len(right) == 1 and None not in right:
            g += right.pop()
            continue
        left = {t[j - 1] if j else None for t, j in spots}
        if len(left) == 1 and None not in left:
            g = left.pop() + g
            continue
        return g


def _at(t: str, g: str) -> list[int]:
    return [i for i in range(len(t) - len(g) + 1) if t.startswith(g, i)]


SEAT_REF = "0123456789一二三四五六七八九两号位"


def mentions_seat(text: str) -> bool:
    """Does the utterance point at a concrete player?

    Passivity is behavioural, not lexical: an agent that never names anyone is not
    acting, whatever its wording looks like.
    """
    t = "".join(text.split())
    if any(ch.isdigit() and "号" in t for ch in t):
        return True
    return any(c in t for c in "一二三四五六七八九两") and ("号" in t or "位" in t)


def passivity_rate(turns: list[tuple[str, str]]) -> float:
    """M3 PRIMARY criterion.

    Input: (act, speech) pairs for a round. A turn is passive when the engine-assigned
    act was listen/align AND the speech names no player.

    Why this and not collapse_round is primary: raising temperature alone already buys
    8/8 distinct openings on this endpoint (measured), so a similarity-based gate passes
    while the game is still dead — every agent says 'let me hear others first'.
    """
    if not turns:
        return 0.0
    passive = sum(
        1 for act, speech in turns if act in ("listen", "align") and not mentions_seat(speech)
    )
    return passive / len(turns)


def shannon_entropy(counts: Iterable[int]) -> float:
    tot = sum(counts)
    if tot <= 0:
        return 0.0
    return -sum((c / tot) * math.log2(c / tot) for c in counts if c) + 0.0  # kills the -0.0


# ============================================================================= log-shaped
# M1-M8. Every one of these is a pure function of an already-loaded event list, which is the
# whole point: "recompute the number, never re-run the game" (plan §2 principle 5). A metric
# that needed the endpoint to recompute would die the next time somebody restarts the shared
# box (R7), and this project's only artifact is the log.

DECISION_KINDS = frozenset({Kind.SPEECH, Kind.VOTE, Kind.NIGHT_ACTION, Kind.WOLF_CHAT,
                            Kind.LAST_WORDS})
#: Terminals that belong in a win-rate denominator. An aborted game is not a lost game, and a
#: draw is not either — mixing them in is how a batch of outages reads as "the model played
#: passively".
DECISIVE = frozenset({"good_win", "wolf_win"})


@dataclass
class Game:
    """One log file, loaded: (events, manifest). The unit M1 aggregates over."""

    path: Path
    meta: dict[str, Any]
    events: list[Event]
    #: The lines at the end of the file that were not JSON. Never empty by accident, and never
    #: printed: `terminal` reads `unfinished` when the cut ate the `GAME_OVER` line, so a reader
    #: who cannot see the cut reads a shortened denominator as "this batch never got that far".
    torn_tail: tuple[str, ...] = ()

    @property
    def game_id(self) -> str:
        return str(self.meta.get("game_id") or self.path.stem)

    @property
    def _over(self) -> Event | None:
        """The terminating event, or None for a log that never got one. Four readers below,
        one scan: `terminal` / `winner` / the degraded pair all read the same record, and a
        second copy of "last GAME_OVER" is how they start disagreeing about which record it is."""
        return next((e for e in reversed(self.events) if e.kind == Kind.GAME_OVER), None)

    @property
    def terminal(self) -> str:
        over = self._over
        return over.payload["terminal"] if over else "unfinished"

    @property
    def winner(self) -> str | None:
        over = self._over
        return over.payload.get("winner") if over else None

    @property
    def degraded_game(self) -> bool | None:
        """plan §143: "这局的动作有多少是引擎替座位做的主"。`None` 表示**没记录**，不是"没退化"。

        The field was added after the first batches were played, and a log is a permanent
        artifact — so absence has to survive as absence. Read it as `False` and every batch
        from before the field ships reports "0 局退化" on bytes nobody ever touched, which is
        precisely the good-news-shaped failure this verdict exists to prevent.
        """
        over = self._over
        return over.payload.get("degraded_game") if over else None

    @property
    def degraded_threshold(self) -> int | None:
        """The threshold that was in force when the game wrote its verdict.

        Kept next to the verdict rather than re-read from `Config()`: a single log file carries
        only `config_hash`, so a checker that recomputed the rule today would be applying a
        different rule and calling it a reproduction.
        """
        over = self._over
        return over.payload.get("degraded_threshold") if over else None

    @property
    def days(self) -> int:
        return max((e.day for e in self.events), default=0)

    @property
    def seq_damage(self) -> dict[str, int]:
        """缺号 / 重号 / 顺序倒挂 —— 原样转交 `events.seq_damage`，不在这里重算。

        `replay` 从 #54 起就会说这句话，`audit` 印同一个 dict，批次侧一直没有读数：一局编号破损
        的日志静默进配对表、进分母、进 bootstrap。挂到 `Game` 上而不是在 `batch` 里再写一只手，
        是为了让批次的数和转录那句措辞出自同一次算术。
        """
        return seq_damage(self.events)

    @property
    def torn_extent(self) -> dict[str, int]:
        """末行被砍掉多少（几行、几字节）—— 转交 `events.torn_extent`，不在这里重算。

        和 `seq_damage` 同一个理由：`torn_notice` 的那句话、`audit` 的那一格、批次报告里的数必须是
        同一次算术。这里只有 `torn_tail` 的原文（引擎不让它往结论里走），量从那只共用的手取。
        """
        return torn_extent(self.torn_tail)

    @property
    def is_synthetic(self) -> bool:
        """A stand-in table can never enter a paired corpus (§十一 for mock, §十五 for human), so
        the log has to be able to answer that question without someone remembering which runs were real."""
        return sorted(self.meta.get("actor_kinds") or []) != ["llm"]


def read_game(path: str | os.PathLike[str]) -> Game:
    events, meta, torn = EventLog.read_split(Path(path))
    return Game(path=Path(path), meta=meta, events=events, torn_tail=tuple(torn))


def read_dir(out_dir: str | Path, *, pattern: str = "*.jsonl") -> list[Game]:
    """Every trajectory in a folder, skipping the prompt dumps that share it.

    `--dry-run` writes `g<seed>.prompts.jsonl` next to the very games it played to reach those
    states, so one `--out` directory holds two kinds of JSONL and only one of them is a
    trajectory — a prompt dump has no `visibility`, no `seq`, nothing a loader expects.
    """
    return [read_game(p) for p in sorted(Path(out_dir).glob(pattern))
            if not p.name.endswith(".prompts.jsonl")]


# -------------------------------------------------------------------------- shared slices
def decisions(events: list[Event]) -> list[Event]:
    """Turns an actor took, as opposed to facts the engine asserted.

    The denominator for M2 and M3: a refusal of an engine-written event is not a model
    failure, and counting the two together would make the rate a function of how many
    notices the judge read out.
    """
    return [e for e in events if e.kind in DECISION_KINDS and e.payload.get("meta")]


def speeches(events: list[Event], day: int | None = None) -> list[Event]:
    return [e for e in events if e.kind == Kind.SPEECH
            and (day is None or e.day == day)]


def roles_of(events: list[Event]) -> dict[int, str]:
    return {e.actor: e.payload["role"] for e in events if e.kind == Kind.DEAL and e.actor}


ROLE_TEAM: dict[str, str] = {
    r.id: r.team for board in roles.BOARDS.values() for r, _n in board.composition
}


def teams_of(events: list[Event]) -> dict[int, str]:
    """seat -> "wolf" | "villager" | "god", resolved through the declared boards.

    Not ``role == "wolf"``: if 狼王 ever joins a board (plan §13 keeps it off by default, not
    off forever) a string comparison would file it under the good team, and every faction-split
    metric would then err in exactly one direction — the kind of wrong that stays green.
    """
    return {seat: ROLE_TEAM[role] for seat, role in roles_of(events).items()}


def _meta(e: Event) -> dict[str, Any]:
    return e.payload.get("meta") or {}


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, int(math.ceil(q * len(vals))) - 1))
    return vals[idx]


def wilson_ci(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% interval for a binomial proportion, at the batch sizes this project actually has.

    Wilson and not the textbook ``p ± 1.96·sd``: at n=40 with p near 0 or 1 the normal
    approximation goes outside [0,1], which is exactly the region a 40-game overnight batch
    lives in.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) / denom), min(1.0, (centre + half) / denom))


# ---------------------------------------------------------------------------------------- M1
def seat_death_day(events: list[Event], seat: int) -> int | None:
    for e in events:
        if e.kind == Kind.DEATH and e.payload.get("seat") == seat:
            return e.day
    return None


def m1_win_rate(games: list[Game]) -> dict[str, Any]:
    """Faction win rate over decisive games only, with the interval this batch can support.

    The `descriptive` flag is not decoration: at 40 games the half-width is ±14.8pp at p=½
    (`wilson_ci(20, 40)`, pinned by `tests/test_report_stats.py`) on this
    project's throughput (~10 games/h), so a win rate here cannot carry a conclusion, and the
    per-utterance metrics exist to be the ones that do (plan §8 M1, R2).

    Synthetic tables (`actor_kinds != ["llm"]`) are dropped from the denominator rather than
    counted and annotated. A field named `n_synthetic_excluded` next to a win rate computed
    over those same games would be a label, not a guard, and the plan's rule is that a
    stand-in table never enters an evaluation corpus — a mock 9-seat script wins its faction
    by authorship, so a rate over it measures the author.
    """
    synthetic = [g for g in games if g.is_synthetic]
    usable = [g for g in games if not g.is_synthetic]
    decisive = [g for g in usable if g.terminal in DECISIVE]
    excluded = Counter(g.terminal for g in usable if g.terminal not in DECISIVE)
    good = sum(1 for g in decisive if g.winner == "good")
    lo, hi = wilson_ci(good, len(decisive))
    strat: dict[str, dict[str, Any]] = {}
    for label, pred in (("seer_died_night_1", lambda g: seat_death_day(g.events, seer_seat(g.events)) == 1),
                        ("seer_survived_night_1", lambda g: seat_death_day(g.events, seer_seat(g.events)) != 1)):
        sub = [g for g in decisive if pred(g)]
        k = sum(1 for g in sub if g.winner == "good")
        strat[label] = {"n": len(sub), "good_rate": round(k / len(sub), 3) if sub else None}
    note = "胜率在此样本量下仅作描述；b+c<6 时两配置对比会自动打『统计力不足』（plan §8）。"
    if usable and not decisive:
        note = f"本批 {len(usable)} 局无一决出阵营胜负（见 excluded），分母为空，胜率为 None 而非 0。"
    elif synthetic and not usable:
        note = (f"本批 {len(synthetic)} 局全为替身桌（actor_kinds={seat_kinds(synthetic)}），"
                f"依据 {synthetic_basis(synthetic)}：不入评测语料，胜率不产出，不是 0%。")
    return {
        "n_games": len(games), "n_decisive": len(decisive), "excluded": dict(excluded),
        "good_win_rate": round(good / len(decisive), 3) if decisive else None,
        "wilson95": [round(lo, 3), round(hi, 3)],
        "mean_days": round(mean([g.days for g in decisive]), 2) if decisive else None,
        "by_role_survival": _role_survival(decisive),
        "stratified": strat,
        "n_synthetic_excluded": len(synthetic),
        "descriptive": True,
        "note": note,
    }


def _role_survival(games: list[Game]) -> dict[str, float]:
    """Share of games in which each role's seat was never killed.

    Reported instead of "win rate by role", which on this board is not a different number per
    role: a wolf and its two teammates always win or lose together, so a per-role win rate
    would be the faction rate printed three times.
    """
    alive: dict[str, list[int]] = {}
    for g in games:
        dead = {e.payload["seat"] for e in g.events if e.kind == Kind.DEATH}
        for seat, role in roles_of(g.events).items():
            alive.setdefault(role, []).append(0 if seat in dead else 1)
    return {r: round(mean(v), 3) for r, v in sorted(alive.items())}


def seer_seat(events: list[Event]) -> int | None:
    return next((s for s, r in roles_of(events).items() if r == "seer"), None)


# ---------------------------------------------------------------------------------------- M2
def m2_illegal_rate(events: list[Event]) -> dict[str, Any]:
    """Illegal-action rate, both 口径s, because they answer different questions.

    first-attempt is the model's competence; final is what the transcript actually contains
    after the gate and the engine's default action. Only the first can find a regression, and
    only the second says whether the game was still the model's own.
    """
    turns = decisions(events)
    first = [e for e in turns if e.attempts and e.attempts[0].get("violations")]
    final = [e for e in turns if _meta(e).get("violations") or _meta(e).get("fallback")]
    role = roles_of(events)
    by: dict[str, list[int]] = {}
    for e in turns:
        refused = 1 if (e.attempts and e.attempts[0].get("violations")) else 0
        key = f"{e.phase}/{role.get(e.actor, 'engine') if e.actor is not None else 'engine'}"
        by.setdefault(key, []).append(refused)
    n = len(turns)
    return {
        "n_turns": n,
        "first_attempt": round(len(first) / n, 4) if n else None,
        "final": round(len(final) / n, 4) if n else None,
        "by_phase_role": {k: round(mean(v), 3) for k, v in sorted(by.items())},
        "kinds": dict(Counter(_vclass(x) for e in first for x in e.attempts[0]["violations"])),
    }


def _vclass(violation: str) -> str:
    """`invented_event_ids:['e9999'] (只能 …)` → `invented_event_ids`.

    The gate's messages carry the legal set so the retry prompt can be specific; a histogram
    over whole messages would have one bucket per refusal and answer nothing.
    """
    return violation.split(" (")[0].split(":")[0]


# ---------------------------------------------------------------------------------------- M3
def m3_gate_pressure(events: list[Event]) -> dict[str, Any]:
    """Refusals, retries, fallbacks and which repair rung actually caught the output.

    The rung histogram is the answer to a question the plan could only guess at: JSON-clean
    at 100% was measured on *short* prompts (plan §8 M3a). `rung == -1` means the decision did
    not come from a model at all (a stand-in seat, or the engine's default action), so it is
    counted apart rather than as "parse failed".
    """
    turns = decisions(events)
    rungs = Counter()
    for e in turns:
        for a in e.attempts:
            rungs[f"attempt:{a.get('rung', '?')}"] += 1
        rungs[f"published:{_meta(e).get('rung', '?')}"] += 1
    n = len(turns)
    return {
        "n_turns": n,
        "refused_turns": sum(1 for e in turns if e.attempts),
        "retry_rate": round(sum(1 for e in turns if e.attempts) / n, 4) if n else None,
        "fallback_rate": round(sum(_meta(e).get("fallback", 0) for e in turns) / n, 4) if n else None,
        "timed_out": sum(_meta(e).get("timed_out", 0) for e in turns),
        "context_overflows": sum(_meta(e).get("context_overflow", 0) for e in turns),
        "shrinks": sum(_meta(e).get("shrink", 0) for e in turns),
        "parse_failures": sum(1 for e in turns for a in e.attempts if a.get("failure")),
        "repair_rungs": dict(rungs),
    }


def assignment_compliance(events: list[Event]) -> dict[str, Any]:
    """How often a seat did the act the judge assigned it — over the turns that had an assignment.

    `speech_acts` in the audit answers "what did this table do"; plan §7's first defence needs the
    pair: "did they only ever say *listen* at the turns they were told to accuse". The denominator
    cannot come from `kinds`, because 没指派 / 指派了且听了 / 指派了没听 all look like "no such
    violation code" there — only the request record knows which of the three it was.

    `recorded` (turns whose request carries the key at all) is kept apart from `turns` (those with
    a non-null value) so a pre-`#68` log reads as "nobody measured this" rather than as "this
    batch never assigned anyone", which are different claims about different things.
    """
    turns = decisions(events)
    seen = [e for e in turns if "assigned_act" in e.request]
    asked = [e for e in seen if e.request.get("assigned_act")]
    n = len(asked)

    def _not_refused_for_the_assignment(e: Event) -> bool:
        return not any(str(v).startswith("act_not_as_assigned")
                       for a in e.attempts for v in (a.get("violations") or ()))

    return {
        "recorded": len(seen),
        "turns": n,
        "by_assigned": dict(Counter(str(e.request.get("assigned_act")) for e in asked)),
        "obeyed_first_try": round(sum(1 for e in asked if _not_refused_for_the_assignment(e)) / n, 4)
        if n else None,
        "obeyed_final": round(sum(1 for e in asked
                                  if e.payload.get("act") == e.request.get("assigned_act")) / n, 4)
        if n else None,
    }


#: plan §十 M3★ 的预注册判据，五行一字不改地抄自那一行。写在这里而不是散在报告模板里，
#: 是为了让"改阈值"是一个能被 grep 到的动作——两处各存一份时，总有一份先被改，
#: 而报告不会为这种分歧变红（它只会安静地用新阈值判旧数字）。
M3_GATE: dict[str, tuple[str, float]] = {
    "passivity_rate": ("<", 0.4),          # 主判据
    "collapse_round": ("<", 0.35),
    "opening_distinct_rate": (">", 0.8),
    "latency_p95_s": ("<", 20.0),
    "context_overflows": ("<=", 0),
}

_OPS = {"<": operator.lt, ">": operator.gt, "<=": operator.le,
        ">=": operator.ge, "==": operator.eq}


def m3_gate_verdict(games: list[Game]) -> dict[str, Any]:
    """Say PASS / FAIL / NOT_EVALUABLE about a batch, against the five pre-registered criteria.

    Until now the thresholds were only *printed* (`m5_style_collapse` shipped a `gate` dict of
    the same numbers), which left the comparison to a human at 1 a.m. This function is the
    difference between a report and a verdict — and plan §十二's acceptance criterion #3 asks for
    "全部达标或有明确失败记录", so a FAIL has to be an artifact too.

    Three aggregation decisions, none of which is obvious from the per-game views:

    * Denominators are pooled, not averaged. `passivity_rate` is a mean over turns, so the batch
      number is `sum(passive)/sum(turns)` over all turns; a mean of per-game means would let a
      3-turn game count as much as a 90-turn one.
    * Rounds are pooled but never merged: `speech_rounds` is per game, because grouping by day
      across games would compare 号1's day-1 opener with 号7's and call the result a round.
    * A criterion with nothing behind it returns `ok=None`, and `ok=None` is NOT_EVALUABLE rather
      than a pass. This is the load-bearing one: `passivity_rate([])` is 0.0 by definition (a
      round always has speech), and 0.0 < 0.4 reads as "the gate passed" for a batch that contains
      no speech at all. The same trap sits in the clock, in two shapes: a transport whose
      result never measured the wall clock carries exactly 0.0 s (`TransportResult.latency_s`'s
      default), and a stand-in seat never
      reaches a transport at all, so its log has no `response` field to read. Either way a
      batch would otherwise "pass" latency on a timer that never ran — no endpoint answers nine
      seats in zero seconds.

    A measured FAIL outranks an unmeasurable criterion: hiding a red number behind a missing
    reading is how a batch gets the benefit of the doubt.
    """
    usable = [g for g in games if not g.is_synthetic]
    rounds = [r for g in usable for r in speech_rounds(g.events)]
    turns = [(e.payload.get("act", ""), e.payload["text"]) for r in rounds for e in r]
    calls = [e for g in usable for e in decisions(g.events)]
    lats = [float(e.response["latency_s"]) for e in calls
            if e.response.get("latency_s") is not None]
    overflows = sum(_meta(e).get("context_overflow", 0) for e in calls)

    def crit(key: str, value: float | int | None, n: int) -> dict[str, Any]:
        op, thr = M3_GATE[key]
        return {"value": value, "comparator": op, "threshold": thr, "n": n,
                "ok": None if value is None else _OPS[op](value, thr)}

    # 0.0 s for *every* call is not a fast endpoint, it is an absent clock.
    clock_real = bool(lats) and max(lats) > 0.0
    criteria = {
        "passivity_rate": crit("passivity_rate",
                               round(passivity_rate(turns), 4) if turns else None, len(turns)),
        "collapse_round": crit("collapse_round",
                               round(mean([collapse_round([e.payload["text"] for e in r])
                                           for r in rounds]), 4) if rounds else None, len(rounds)),
        "opening_distinct_rate": crit(
            "opening_distinct_rate",
            round(mean([opening_distinct_rate([e.payload["text"] for e in r])
                        for r in rounds]), 4) if rounds else None, len(rounds)),
        "latency_p95_s": crit("latency_p95_s",
                              round(_pct(lats, 0.95), 2) if clock_real else None, len(lats)),
        "context_overflows": crit("context_overflows",
                                  overflows if calls else None, len(calls)),
    }
    failed = [k for k, c in criteria.items() if c["ok"] is False]
    missing = [k for k, c in criteria.items() if c["ok"] is None]
    if failed:
        verdict = "FAIL"
    elif missing:
        verdict = "NOT_EVALUABLE"
    else:
        verdict = "PASS"

    if not games:
        note = "没有可判定的局：分母为空，主判据不给 0.0（`passivity_rate([])` 的定义值不等于通过）。"
    elif not usable:
        note = (f"本批 {len(games)} 局全为替身桌（actor_kinds={seat_kinds(games)}），"
                f"依据 {synthetic_basis(games)}：不入评测语料，闸门不给判定，不是给通过。")
    elif not rounds:
        note = "可用局里一条发言都没有：四条风格判据没有分母。"
    elif verdict == "NOT_EVALUABLE":
        why = ("时钟全程为 0.0 秒（替身 transport 的默认读数），延迟判据无读数"
               if missing == ["latency_p95_s"] and not clock_real
               else f"以下判据缺读数：{'、'.join(missing)}")
        note = f"其余判据已算出，但闸门整体不给判定：{why}。"
    elif verdict == "PASS":
        note = ("五条预注册判据全部达标（plan §十 M3★）。样本量见各条 n，"
                "措辞类判据按轮、行为与延迟按次。")
    else:
        note = ("未达标：" + "、".join(
            f"{k}={criteria[k]['value']} 需 {criteria[k]['comparator']} {criteria[k]['threshold']}"
            for k in failed) + "。失败也是记录，不改阈值来让它消失。")
    return {
        "verdict": verdict, "criteria": criteria, "failed": failed,
        "n_games": len(games), "n_games_usable": len(usable),
        "n_synthetic_excluded": len(games) - len(usable),
        "n_rounds": len(rounds), "n_turns": len(turns), "n_calls": len(calls),
        "gate": {f"{k}{op}": thr for k, (op, thr) in M3_GATE.items()},
        "note": note,
    }


def _objected_ids(e: Event) -> set[str]:
    """Every event id the gate objected to on this turn, from whichever place it said so.

    Two places, because the turn that got refused publishes a *corrected* record: the ids in
    the refused attempt survive only in `attempts[]`, so reading `citation_stats` alone would
    report zero bad citations for exactly the behaviour worth counting.
    """
    stats = _meta(e).get("citation_stats", {}) or {}
    ids = set(stats.get("invented") or []) | set(stats.get("not_visible") or [])
    for a in e.attempts:
        for v in a.get("violations") or []:
            head = str(v).split(" (")[0]
            _, _, listed = head.partition(":")
            try:
                ids |= set(ast.literal_eval(listed))
            except (ValueError, SyntaxError):
                continue
    return ids


# ---------------------------------------------------------------------------------------- M4
def m4_hallucination_rates(events: list[Event]) -> dict[str, Any]:
    """Four sub-rates, and the invented-citation one reads the *rejected* attempts.

    Counting only published events would let the gate eat the evidence it exists to record:
    a seat that invented `e9999`, got refused, and then cited something real published a clean
    record and the batch would report zero hallucination for a model that lied on every turn
    (plan §8 M4).

    `example_copy_turns` splits that numerator rather than replacing it: region A shows a worked
    example carrying its own `[eNNN]` anchors, and a model that quotes those numbers copied the
    prompt instead of inventing evidence. Same gate refusal, different fix — a prompt clause for
    one, a model problem for the other — so the count has to survive in the log even though the
    headline rate deliberately keeps both in.

    `self_contradiction_rate` is the good team's speech↔ballot mismatch, and only theirs: for
    a wolf, saying one thing and voting another is the game, so folding both teams into one
    number would score good play as incoherence — the same trap as M6's split (R12).
    """
    sp = [e for e in speeches(events) if e.payload.get("text")]
    n = len(sp)
    if not n:
        return {"n_speech": 0}

    objected = [ids for e in sp if (ids := _objected_ids(e))]
    uncited = sum(1 for e in sp if _meta(e).get("citation_stats", {}).get("uncited"))
    bad_cite = len(objected)
    # Objection ids that *all* come from region A's worked example. A seat that copied `[e97]`
    # out of the prompt imitated the prompt; a seat that produced `e9999` fabricated evidence.
    # Only the second is a model failure, and the one-line fix for the first is a prompt edit —
    # so the batch report has to be able to tell the user which of the two it is looking at.
    example_copy = sum(1 for ids in objected if ids <= EXAMPLE_EVENT_IDS)
    impossible = sum(1 for e in sp
                     if any(str(f).startswith("impossible_percept") for f in _meta(e).get("flags", [])))
    teams = teams_of(events)
    ballots = sorted([(e.actor, e.day, e.seq, e.payload.get("target")) for e in events
                      if e.kind == Kind.VOTE and e.actor is not None])
    claims = [(e.actor, e.day, e.seq, e.payload.get("target")) for e in sp
              if e.payload.get("target") is not None and teams.get(e.actor) != "wolf"]
    # Paired with the *next* ballot that seat cast, not "a" ballot: a PK revote is a switch
    # made after hearing the two accused speak, and comparing it to a speech from before the
    # PK would score a changed mind as incoherence. Same for the reverse direction — a PK
    # speech is answered by the revote, which is the only ballot after it.
    contradicted = 0
    paired = 0
    for seat, day, seq, target in claims:
        after = [b for b in ballots if b[0] == seat and b[1] == day and b[2] > seq]
        if not after:
            continue
        paired += 1
        if after[0][3] is not None and after[0][3] != target:
            contradicted += 1
    return {
        "n_speech": n,
        "uncited_claim_rate": round(uncited / n, 4),
        "wrong_cite_rate": round(bad_cite / n, 4),
        "example_copy_turns": example_copy,
        "impossible_percept_rate": round(impossible / n, 4),
        "self_contradiction_rate": round(contradicted / paired, 4) if paired else None,
        "n_self_contradiction": contradicted,
        "n_self_contradiction_denominator": paired,
        "note": "wrong_cite 的分子含被闸门拒绝的尝试；example_copy_turns 是其中全部编号都抄自 "
                "region A 示例的那部分，是提示词模仿而非编造证据；self_contradiction 只算好人，"
                "且只与发言之后的那张票配对。",
    }


# ---------------------------------------------------------------------------------------- M5
def speech_rounds(events: list[Event]) -> list[list[Event]]:
    """Contiguous runs of speech in one phase.

    Grouped by phase, not by day: a PK round is its own round, and averaging its two
    utterances into the eight that preceded it would report one collapsed round as a mild
    improvement in the day's mean.
    """
    out: list[list[Event]] = []
    for e in events:
        if e.kind != Kind.SPEECH:
            continue
        if out and out[-1][-1].phase == e.phase and out[-1][-1].day == e.day:
            out[-1].append(e)
        else:
            out.append([e])
    return out


def m5_style_collapse(events: list[Event]) -> dict[str, Any]:
    """Template collapse per round, with `passivity_rate` as the headline (plan §8 M3 primary).

    `collapse_round` alone can pass while the game is dead, because raising temperature buys
    8/8 distinct openings on this endpoint without buying any behaviour at all — measured
    twice. Passivity is the number that cannot be fooled by wording.
    """
    rounds = speech_rounds(events)
    per = []
    for r in rounds:
        texts = [e.payload["text"] for e in r]
        turns = [(e.payload.get("act", ""), e.payload["text"]) for e in r]
        frags = template_top_fragments(texts)
        per.append({
            "day": r[0].day, "phase": r[0].phase, "n": len(r),
            "collapse_round": round(collapse_round(texts), 4),
            "dup_exact6_rate": round(shared_substring_rate(texts), 4),
            "opening_distinct_rate": round(opening_distinct_rate(texts), 4),
            "passivity_rate": round(passivity_rate(turns), 4),
            "template_top1": frags[0][0] if frags else "",
            "template_top1_share": round(frags[0][1] / len(r), 4) if frags else 0.0,
        })
    m = lambda k: round(mean([p[k] for p in per]), 4) if per else None  # noqa: E731
    return {
        "n_rounds": len(rounds),
        "collapse_round_mean": m("collapse_round"),
        "dup_exact6_mean": m("dup_exact6_rate"),
        "opening_distinct_mean": m("opening_distinct_rate"),
        "passivity_mean": m("passivity_rate"),
        "worst_round": max(per, key=lambda p: p["collapse_round"], default=None),
        "rounds": per,
        # 视图只报它按轮算得出的那三条，且数字取自 `M3_GATE`：阈值抄第二份，迟早有一处先被改。
        "gate": {f"{k}{op}": thr for k, (op, thr) in M3_GATE.items()
                 if k in ("passivity_rate", "collapse_round", "opening_distinct_rate")},
    }


# ---------------------------------------------------------------------------------------- M6
def m6_belief_action(events: list[Event], *, k: int = 2) -> dict[str, Any]:
    """Stated belief against the action taken, split by faction, plus the parrot check.

    `consistency_good` high and `divergence_wolf` high are both good play; `divergence` for
    the good team and `consistency` for the wolves are both bad play, which is why one number
    over both teams would point a fix at the wrong thing (plan §8 M6).

    `stated_vs_engine_agreement` is reported alongside, not merged in: if it approaches 1.0
    the sentence above is measuring a copy of the card we handed the model, and the M6 numbers
    beside it are then worthless (R12).
    """
    teams = teams_of(events)
    hit: dict[str, list[int]] = {"good": [], "wolf": []}
    parrot: list[float] = []
    for e in decisions(events):
        if e.actor is None:
            continue
        stated = [d["seat"] for d in (e.payload.get("belief") or {}).get("suspects", [])[:k]]
        target = e.payload.get("target")
        if not stated or target is None:
            continue
        side = "wolf" if teams.get(e.actor) == "wolf" else "good"
        hit[side].append(1 if target in stated else 0)
        visible = percept_for(e.actor, events, at_seq=e.seq - 1).events
        engine = build_belief(e.actor, list(visible)).top_suspects(k)
        if engine:
            parrot.append(len(set(stated) & set(engine)) / k)
    return {
        "k": k,
        "consistency_good": round(mean(hit["good"]), 4) if hit["good"] else None,
        "n_good": len(hit["good"]),
        "divergence_wolf": round(1 - mean(hit["wolf"]), 4) if hit["wolf"] else None,
        "n_wolf": len(hit["wolf"]),
        "stated_vs_engine_agreement": round(mean(parrot), 4) if parrot else None,
        "n_agreement_sample": len(parrot),
        "interpret": "好人 consistency 高=玩得好；狼 divergence 高=玩得好；"
                     "stated_vs_engine_agreement→1.0 则上面两个都是假的。",
    }


# ---------------------------------------------------------------------------------------- M7
#: The three fitted numbers other code is allowed to quote — §7 of `docs/calibration.md`, the
#: only legal source of a latency constant anywhere in this project.
#: Written once, on purpose: `calibrate.py`'s missing-constant list, the sidecar's own block,
#: and the usability rule below all read this tuple. Copied to a second place, adding a fourth
#: constant would update one of them and drift would quietly stop accounting for it.
CALIBRATION_KEYS = ("D_decode_tok_s", "per_call_fixed_overhead_s",
                    "P_prefill_tok_s_best_observed")


def fitted_constant(value: Any) -> bool:
    """A fitted constant is usable only as a positive number. `None` was never measured; 0 or a
    negative would make the term it divides into either infinite or nonsense, and an
    arithmetic error that survives into a verdict is worse than a missing number.

    Public and imported by `calibrate.py`: the report's §0 "本节尚不构成常数来源" line and this
    module's `usable` verdict are the same sentence written twice, so they read the same ruler.
    A negative `per_call_fixed_overhead_s` — what a zero-latency server produces — used to pass
    the report's `is None` check while this one refused the file.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def declared_models(features: Any) -> list[str]:
    """The model ids the endpoint advertised about itself, or [] when that probe never answered.
    Read out of the same `models_endpoint` block the calibration report prints in §1, so this is
    not a second source of truth — it is the only place this listing is parsed, and both the
    report's §0 line and `load_calibration`'s refusal go through it."""
    if not isinstance(features, dict):
        return []
    body = (features.get("models_endpoint") or {}).get("body")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    return [m["id"] for m in data if isinstance(m, dict) and m.get("id")]


def model_denial(model: Any, declared: Any) -> str | None:
    """The clause for "this endpoint's own `/v1/models` listing does not contain the model these
    numbers were asked of" — the only directly citable evidence of R7 (somebody restarted the box
    on other weights) that a machine can read. Public and imported by `calibrate.py`: the report's
    §0 line and this module's refusal are one sentence written twice, and the last time that
    happened a negative overhead passed one ruler and not the other.

    An absent or empty listing is *no evidence*, not a contradiction: refusing on it would leave
    `audit` unable to read constants from every run whose probe failed, which is the "there is no
    number" illness wearing the "this number must not be used" one.
    """
    if not model or not isinstance(declared, list) or not declared:
        return None
    if model in declared:
        return None
    return (f"端点在 /v1/models 里不承认 model={model}（它列出的是 "
            f"{'、'.join(str(m) for m in declared[:5])}）：这份常数可能来自别的权重")


def load_calibration(path: str | os.PathLike[str], *,
                     expect_model: str | None = None) -> dict[str, Any]:
    """Read a calibration run's sidecar and say honestly whether its constants may be used.

    Never raises. `wolf audit` has to print *why* it declined rather than a traceback, and a
    half-finished run — the file exists, and it is still no authority — is the ordinary case on
    a shared box, not the edge case.

    The refusal is per-file, not per-value: the constants are used together or not at all,
    because the model is `fixed + pt/P + ct/D` and dropping one term does not make the
    prediction vaguer, it biases it in a known direction.
    """
    src = Path(path)
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"source": str(src), "model": None, "ran_utc": None, "constants": None,
                "usable": False, "note": f"{src} 不存在：未标定，drift 自检不做而不是猜一个"}
    except (OSError, ValueError) as e:
        return {"source": str(src), "model": None, "ran_utc": None, "constants": None,
                "usable": False,
                "note": f"{src} 无法解析（{type(e).__name__}）：未标定，不去猜它缺了哪一项"}
    if not isinstance(raw, dict):
        return {"source": str(src), "model": None, "ran_utc": None, "constants": None,
                "usable": False, "note": f"{src} 解析出来不是一个对象：未标定"}

    model, ran_utc = raw.get("model"), raw.get("ran_utc")
    constants: dict[str, Any] | None = None
    # The path is stated once, at the head of the note; the reasons below are clauses, not
    # sentences, because this whole string is what `audit` prints as `drift_note`.
    issues: list[str] = []
    block = raw.get("constants")
    if not isinstance(block, dict):
        # 仓库里现存的那份 sidecar 就是这一类：一次跑断在半路的体检，文件里从来没有常数块。
        # 对它说"某个键为空"会把人支到一个不存在的问题上。
        issues.append("没有 constants 块（这份 sidecar 早于该字段，或那次体检在写出常数之前就"
                      "断了）：重跑 scripts/calibrate.py")
    else:
        missing = [k for k in CALIBRATION_KEYS if not fitted_constant(block.get(k))]
        if missing:
            issues.append(f"constants 里 {'、'.join(missing)} 没有可用的值（未测得或不为正）")
        else:
            constants = {k: block[k] for k in CALIBRATION_KEYS}
    # The recorded listing is the source; `features` is the same run's raw probe, read only for
    # sidecars written before the field existed. Without that fallback the report's §0 line and
    # this refusal would disagree on exactly those files — same run, two rulers again.
    declared = raw.get("model_declared") or declared_models(raw.get("features"))
    denial = model_denial(model, declared)
    if denial:
        if constants is not None:
            constants = None
        issues.append(denial)
    if constants is not None and expect_model:
        if not model:
            issues.append(f"未记录 model，无法确认它与本批日志（{expect_model}）同端点："
                          "常数按可用处理，出处存疑")
        elif model != expect_model:
            constants = None
            issues.append(f"拟合于 model={model}，本批日志是 model={expect_model}："
                          "换了端点之后的常数不能用来判 drift")
    note = f"{src}（model={model or '未知'}，实测于 {ran_utc or '未记录'}）"
    if issues:
        note += "；" + "；".join(issues)
    return {"source": str(src), "model": model, "ran_utc": ran_utc,
            "constants": constants, "usable": constants is not None, "note": note}


# plan §5 那张预算表的每一格，对上日志里 `region_tokens` 的每一格。`B` 故意不在表里：它是
# B1+B2，给总数再配一把尺子等于给同一批字节立第二个更软权威。
#
# 五格里只有一格后面有刀（`C2`：装配器会削主张卡），另外四格是**警报尺**——plan §5 的牺牲顺序
# ⑤⑥ 明写私有信息和 A 永不压缩，C1 是反塌缩 P1/P3 的载体，C4 是 act 闸门的出口，那四格超了
# 不许砍，只许在报告里红着。11:12:16Z 三局 mock 实测出厂峰值 71/57/328/111/115，都还没咬。
REGION_CAP_KEYS = {"A": "a_hard", "B1": "b1", "B2": "b2", "C": "c_total",
                   "B0": "b0", "C1": "c_persona", "C2": "c_belief",
                   "C3": "c_private", "C4": "c_task"}


def region_budget_check(g: Game) -> dict[str, Any]:
    """Region sizes measured against the caps recorded in the same file, cross-checked twice.

    `region_tokens` carries one cell per block plan §5 budgets; the numbers themselves are not in
    the log. The rulers travel in `meta.regions`, so one file can answer "was anything over
    budget" without anyone remembering which config produced it — and a `--set` override is covered
    for free, because what gets read is the arm's own meta, not the factory `Config()`.

    `b2_witness_agrees` is the second pass: `b2_over_cap` was written by `assemble` at send time
    from the budget it held, and `worst_over.B2` is subtracted here from the bytes that were sent.
    Two ways of answering one question in one file that nobody reconciled is how a forged or
    version-skewed log reads as clean.

    The `card_*` cells are the C knife's witness, and they are the odd ones out: a count of lines,
    with no cap to compare it against. The drafted belief card is nowhere in the log, so there is
    nothing here to cross-check it with — which is the whole reason the assembler had to write it
    down at send time. `region_tokens.C2` is measured after the cut and cannot miss one.
    """
    requests = [e.request for e in g.events if e.request]
    # Deliberately computed before the caps guard below: this count needs no ruler, so a log whose
    # `meta.regions` never landed still has a real answer to "was any card thinned".
    dropped = [int(r["card_claims_dropped"]) for r in requests
               if r.get("card_claims_dropped") is not None]
    card = {"card_prompts_thinned": None if not dropped else sum(1 for x in dropped if x > 0),
            "card_worst_claims_dropped": max(dropped, default=0) if dropped else None}
    caps = g.meta.get("regions")
    if not caps:
        # Absent field, not zero: "this log predates the caps" and "every prompt sat inside its
        # budget" are different claims, and only the first one is true of an old batch.
        return {"caps": None, "worst_over": None, "b2_witness_agrees": None, **card}
    peak: dict[str, int] = {}
    witness: list[bool] = []
    for req in requests:
        rt = req.get("region_tokens") or {}
        for region, cap_key in REGION_CAP_KEYS.items():
            if rt.get(region) is not None and caps.get(cap_key) is not None:
                peak[region] = max(peak.get(region, 0), int(rt[region]))
        recorded = req.get("b2_over_cap")
        if rt.get("B2") is not None and recorded is not None and caps.get("b2") is not None:
            witness.append(max(0, int(rt["B2"]) - int(caps["b2"])) == int(recorded))
    return {
        "caps": {k: caps.get(k) for k in REGION_CAP_KEYS.values()},
        "worst_over": {r: (max(0, peak[r] - int(caps[k])) if r in peak else None)
                       for r, k in REGION_CAP_KEYS.items()},
        "b2_witness_agrees": None if not witness else all(witness),
        **card,
    }


def timed_decisions(events: list[Event]) -> list[Event]:
    """The batch of calls both cost and cache arithmetic use as its denominator.

    Two readers, one predicate: `m7_cost_profile` bills this batch and `prefix_cache_reuse` divides
    by it. Written out twice it was a drift waiting to happen — a filter added on one side would
    make `m7_cost.n_calls` and `prefix_cache.calls` disagree while both sit in the same audit
    object — and the mutation run of #70 (13:34:34Z) showed the duplicated line had no reader at
    all: dropping the timing filter left the whole suite green.

    A decision the log never timed is not a call: an un-timed record came out of a repair path or
    an aborted send, so it has neither a wall clock nor a usage block to divide.

    The second conjunct is redundant *today*, and said so on purpose: the only writer of a
    `response` block is `agent.take_turn`, which appends one of exactly those five kinds. It stays
    because that is an invariant of the current call sites, not of the log format — when a sixth
    kind starts carrying an answer, `decisions()` is the half that keeps the bill honest.
    """
    return [e for e in decisions(events) if e.response.get("latency_s") is not None]


def m7_cost_profile(events: list[Event], *, constants: dict[str, Any] | None = None,
                    calibration_note: str | None = None) -> dict:
    """Token and wall-clock cost per phase, plus the drift self-check.

    `fill_rate` is the other half of the wall-clock story: `wolf run --dry-run` prints a game's
    completion budget as a sum of `max_tokens`, which is an upper bound, and the per-game estimate
    is multiplied out of that bound. Once a game has actually run, the log carries what was asked
    (`request.max_tokens`, one value per phase from `Config.token_budget_for`) and what came back
    (`response.completion_tokens`), so the bound becomes an expectation instead of a number someone
    divides by hand. It is a ratio over the calls that recorded a budget — `asked_calls` against
    `n_calls` says how much of the game that covers — and None when no call recorded one, because
    0.0 would be a claim that the model answered with nothing.

    The check is deliberately unusable rather than optimistic: `constants` comes from
    `load_calibration()`, and until the re-run measures the decode term every prediction would
    be a guess wearing the calibration file's authority. `calibration_note` is printed verbatim
    as the reason there is no drift, because "why not" is a fact about a file only the code that
    just read it can know — two places each composing their own excuse is how one starts saying
    "missing D" while the other says "no such file".
    """
    calls = timed_decisions(events)
    by: dict[str, dict[str, list[float]]] = {}
    for e in calls:
        d = by.setdefault(e.phase, {"lat": [], "ct": [], "pt": [], "asked": [], "used": [],
                                    "cut": []})
        d["lat"].append(float(e.response["latency_s"]))
        ct = float(e.response.get("completion_tokens") or 0)
        d["ct"].append(ct)
        d["pt"].append(float(e.request.get("total_tokens_est") or 0))
        d["cut"].append(1.0 if e.response.get("finish_reason") == "length" else 0.0)
        asked = e.request.get("max_tokens")
        if asked is not None:
            # Both sides of the ratio come from the same call or neither: a call whose budget
            # was never written down contributes nothing to the denominator and, importantly,
            # nothing to the numerator either — an uncounted 40 tokens would otherwise be
            # subtracted from the calls that were counted.
            d["asked"].append(float(asked))
            d["used"].append(ct)

    def rate(d: dict[str, list[float]]) -> float | None:
        return None if not sum(d["asked"]) else round(sum(d["used"]) / sum(d["asked"]), 4)

    profile = {
        phase: {"n": len(d["lat"]), "latency_sum": round(sum(d["lat"]), 2),
                "lat_p50": round(_pct(d["lat"], 0.50), 2),
                "lat_p95": round(_pct(d["lat"], 0.95), 2), "lat_max": round(max(d["lat"]), 2),
                "completion_sum": int(sum(d["ct"])), "prompt_est_sum": int(sum(d["pt"])),
                "asked_sum": int(sum(d["asked"])), "asked_n": len(d["asked"]), "fill_rate": rate(d),
                "truncated": int(sum(d["cut"]))}
        for phase, d in sorted(by.items())
    }
    asked_all = [x for d in by.values() for x in d["asked"]]
    used_all = [x for d in by.values() for x in d["used"]]
    out: dict[str, Any] = {
        "by_phase": profile,
        "n_calls": len(calls),
        "completion_total": int(sum(float(e.response.get("completion_tokens") or 0) for e in calls)),
        "asked_total": int(sum(asked_all)),
        "asked_calls": len(asked_all),
        "fill_rate": None if not sum(asked_all) else round(sum(used_all) / sum(asked_all), 4),
        "latency_total_s": round(sum(float(e.response["latency_s"]) for e in calls), 1),
        "truncations": sum(1 for e in calls if e.response.get("finish_reason") == "length"),
        "transport_retries": sum(int(e.response.get("attempts") or 1) - 1 for e in calls),
    }
    if calls and out["fill_rate"] is None:
        # Same rule as `drift_note`: "why is this cell empty" is a fact about the log only the
        # code that just read it knows, so it travels with the reading instead of being
        # re-composed in prose somewhere else.
        out["fill_rate_note"] = (
            f"{len(calls)} 次调用里没有一次记下 `max_tokens`：兑现率不做外推，"
            "0.0 会被读成\"模型一个 token 也没用完\"，而这里唯一的事实是这份日志没记问过多少")
    if not constants:
        out["drift"] = None
        out["drift_note"] = calibration_note or (
            "未标定：没有传入 constants（docs/calibration.md §7 的三个拟合值尚缺），"
            "不做预测而不是猜一个")
        return out
    unusable = [k for k in CALIBRATION_KEYS if not fitted_constant(constants.get(k))]
    if unusable:
        out["drift"] = None
        out["drift_note"] = calibration_note or (
            f"constants 里 {'、'.join(unusable)} 没有可用的值：不用半份常数。尤其不能把缺失的 "
            "per_call_fixed_overhead_s 当 0——那会让每一次预测都偏短、每个比值都偏大，"
            "自检于是报出一个假的 suspect")
        return out
    d_tok, p_tok = constants["D_decode_tok_s"], constants["P_prefill_tok_s_best_observed"]
    overhead = constants["per_call_fixed_overhead_s"]
    # Per call, not per phase: concurrency makes a phase's wall clock meaningless, while a
    # call's own latency is directly comparable to the fitted T_call (plan §8 M7).
    ratios: dict[str, list[float]] = {}
    for e in calls:
        pt = float(e.response.get("prompt_tokens") or e.request.get("total_tokens_est") or 0)
        ct = float(e.response.get("completion_tokens") or 0)
        predicted = overhead + pt / p_tok + ct / d_tok
        if predicted > 0:
            ratios.setdefault(e.phase, []).append(float(e.response["latency_s"]) / predicted)
    all_r = [r for v in ratios.values() for r in v]
    # `any()` over an empty list is False, so the two-way version of this verdict reported
    # "ok" for a batch with no calls at all — a green light from nothing, which is the same
    # mistake M3's gate had to be talked out of.
    verdict = ("not_evaluable" if not all_r else
               "suspect" if any(r > DRIFT_RATIO or r < 1 / DRIFT_RATIO for r in all_r) else "ok")
    out["drift"] = {
        "measured_over_predicted_by_phase": {k: round(mean(v), 2) for k, v in sorted(ratios.items())},
        "worst": round(max(all_r), 2) if all_r else None,
        "best": round(min(all_r), 2) if all_r else None,
        "n_calls_scored": len(all_r),
        "threshold": DRIFT_RATIO,
        "verdict": verdict,
        "constants": {k: constants[k] for k in CALIBRATION_KEYS},
        "note": ("没有一次调用可比：常数齐了而分母是空的，这不叫通过" if not all_r else
                 f"偏离 >{DRIFT_RATIO}× 说明端点变了或常数过期（plan §8 M7）；这不是模型变笨了。"),
    }
    return out


def prefix_cache_reuse(events: list[Event]) -> dict[str, Any]:
    """Was the shared prefix reused? The one field that answers it is `usage.cached_tokens`.

    plan §5's whole region geometry exists to make a server skip prefill work, and until now the
    only place that value was visible was a single calibration run — which its own redaction then
    blanked (`docs/calibration.md` §0 names the seven cells). `transport.usage_from()` carries the
    number out of the response and into `Event.response["usage"]`; everything here is arithmetic
    over the calls that recorded it.

    A call that recorded nothing stays out of **both** sides of the ratio and is counted in
    `silent`, because "this endpoint never answered" and "this endpoint answered: nothing was
    reused" are different claims, and only the second one is evidence about §5's economics. A call
    that answered one side but not the other is `unpairable` rather than quietly vanished.
    """
    calls = timed_decisions(events)

    def _cached(e: Event) -> int | None:
        u = e.response.get("usage") or {}
        return None if u.get("cached_tokens") is None else int(u["cached_tokens"])

    def _pair(e: Event) -> tuple[int, int] | None:
        prompt = e.response.get("prompt_tokens")
        cached = _cached(e)
        return None if cached is None or prompt is None else (cached, int(prompt))

    reported_all = [e for e in calls if _cached(e) is not None]
    by_phase: dict[str, Any] = {}
    pairs_all: list[tuple[int, int]] = []
    for phase in sorted({e.phase for e in calls}):
        mine = [e for e in calls if e.phase == phase]
        reported = [e for e in mine if _cached(e) is not None]
        pairs = [p for p in map(_pair, reported) if p is not None]
        pairs_all += pairs
        by_phase[phase] = {
            "reported": len(reported), "silent": len(mine) - len(reported),
            "reuse_ratio": round(sum(c for c, _ in pairs) / sum(p for _, p in pairs), 4)
            if pairs else None}

    cached_sum, prompt_sum = sum(c for c, _ in pairs_all), sum(p for _, p in pairs_all)
    return {
        "calls": len(calls),
        "reported": len(reported_all),
        "silent": len(calls) - len(reported_all),
        "unpairable": len(reported_all) - len(pairs_all),
        "cached_tokens": cached_sum if pairs_all else None,
        "prompt_tokens": prompt_sum if pairs_all else None,
        "reuse_ratio": round(cached_sum / prompt_sum, 4) if pairs_all else None,
        "by_phase": by_phase,
    }


# ---------------------------------------------------------------------------------------- M8
def m8_strategy_proxies(events: list[Event]) -> dict[str, Any]:
    """Do the agents look like they are playing? Readable from one game, no statistical power.

    These are the metrics that say "the game is alive" as opposed to "the engine did not
    crash", which makes them the right thing to watch while a batch is still running: a
    collapsed table shows up here in game one, long before any win-rate interval is tight
    enough to mean anything (plan §8 M8).
    """
    teams = teams_of(events)
    role = roles_of(events)
    seer = next((s for s, r in role.items() if r == "seer"), None)
    # `act`, not `action`: the latter is the legality class the turn was played under, and for
    # the witch both potions share it ("save_or_poison"), so filtering on it silently found
    # zero saves — a 0 that looked like "the witch never used her medicine", which is a claim
    # about play, not about a field name.
    knives = [e for e in events if e.kind == Kind.NIGHT_ACTION
              and e.payload.get("act") == "kill"]
    saves = [e for e in events if e.kind == Kind.NIGHT_ACTION
             and e.payload.get("act") == "save"]
    # Both per-wave lists come out of `voting_waves`, the same splitter the 复盘 grids use, so
    # they are the same length by construction rather than by luck. Entropy answers "how spread
    # was this vote", and to answer it the counts get normalised away — which makes 9 seats
    # naming one target and 3 naming it while 6 sat out the *same* number. `abstention_rate` and
    # `ballot_mandate` are the half of the ballot the entropy throws out: how many seats were
    # asked, and how many answered with a name.
    waves = voting_waves(events)
    tallied = [(v, r) for v, r in waves if r is not None]
    entropies = [shannon_entropy(list((r.payload.get("tally") or {}).values()))
                 for _, r in tallied]
    written = sum(len(v) for v, _ in waves)
    cast = sum(1 for v, _ in waves for e in v if e.payload.get("target") is not None)
    mandate = [round(max((r.payload.get("tally") or {"0": 0}).values()) / len(v), 4)
               for v, r in tallied]

    # Who the good team was most suspicious of, from stated beliefs before that night, and did
    # the knife land there. A wolf seat is a legitimate answer — it is the *expected* answer,
    # since 刀查杀 means killing the seat about to be lynched — so nothing here filters by
    # team except the source of the suspicion, which must not be the wolves' own ranking.
    # A pack that ignores that seat is not playing a strategy, whatever its win rate says,
    # and this is the only proxy that notices.
    hits = scored = 0
    for night in sorted({e.day for e in knives}):
        that_night = [e for e in knives if e.day == night]
        knifed_already = {e.payload.get("target") for e in knives if e.day < night}
        suspects: Counter = Counter()
        for s in speeches(events):
            if s.day >= night or teams.get(s.actor) == "wolf":
                continue
            for d in (s.payload.get("belief") or {}).get("suspects", [])[:1]:
                suspects[d["seat"]] += 1
        ranked = [seat for seat, _ in suspects.most_common() if seat not in knifed_already]
        if not ranked:
            continue
        for e in that_night:
            scored += 1
            hits += 1 if e.payload.get("target") == ranked[0] else 0
    # A save names no seat: the witch answers "someone was knifed", and the engine knows whom
    # (the `notice`). So the pairing is by night, and the question this answers is plan §8's —
    # not "did she use the bottle" but "did she use it when the seer was the one on the table".
    saved_nights = {s.day for s in saves}
    seer_nights = {k.day for k in knives if k.payload.get("target") == seer}
    return {
        "deaths": [(e.payload["seat"], e.payload["cause"], e.day)
                   for e in events if e.kind == Kind.DEATH],
        "seer_seat": seer,
        "seer_death_day": seat_death_day(events, seer) if seer else None,
        "n_knives": len(knives),
        "wolf_kill_hit_rate": round(hits / scored, 3) if scored else None,
        "n_knives_scored": scored,
        "witch_saves": len(saves),
        "seer_nights_knifed": len(seer_nights),
        "witch_save_rate_on_seer": (
            round(len(seer_nights & saved_nights) / len(seer_nights), 3)
            if seer_nights else None),
        "vote_split_entropy": [round(x, 4) for x in entropies],
        "vote_split_entropy_mean": round(mean(entropies), 4) if entropies else None,
        "abstention_rate": round((written - cast) / written, 4) if written else None,
        "ballot_mandate": mandate,
        "ballot_mandate_mean": round(mean(mandate), 4) if mandate else None,
    }


#: 哪一种替身座位被计划里的哪一条挡在语料外面 —— 两个出口曾经说的是同一条。
#: §十五 点名的是**人**（真人不可 seed 控制，混进配对表直接污染 McNemar 的配对前提）；
#: mock 的约束在 §十一「必须真端点（mock 只会自证，这几项不许省）」。
SYNTHETIC_CLAUSE = {
    "mock": "plan §十一（mock 只会自证，这几项不许省）",
    "human": "plan §十五（含真人座位的局永远不得进入配对评测语料）",
}


def seat_kinds(games: Iterable[Game]) -> list[str]:
    """桌上坐着的种类，取自**这些局自己的页眉**。

    不取 `run_manifest.json` 那一格：批次顶层的 `actor_kinds` 只有 `["mock"]`/`["llm"]` 两种
    取值（`batch.py` 里由 `--mock` 推出来），它写不出真人座位。于是"含真人的那一批被拒"会印出
    `actor_kinds=['llm']` —— 计数全对、句子自相矛盾，正是 `#87` 那一类。
    """
    return sorted({k for g in games for k in (g.meta.get("actor_kinds") or [])})


def synthetic_basis(games: Iterable[Game]) -> str:
    """剔除一张替身桌时引用的那条款，按桌上真正坐着的种类挑。

    三个出口（`compare` 的拒绝语、`m1_win_rate` 的 note、`m3_gate_verdict` 的 note）各写过
    一遍同一句话，而那句把两种座位都推给了 §十五：读者照着印出来的出处去找，只会找到一条关于
    玩家的规定，然后得出恰恰相反的结论 —— 替身 transport 没人管。一张表就是为这个。

    没有对应条款的种类不借最近的来用：`actor_kinds` 缺格的日志照样被拒（`is_synthetic` 是
    关门），但它不属于 §十一/§十五 点名的任何一种，所以只能说"没登记"。
    """
    kinds = set(seat_kinds(games))
    named = [SYNTHETIC_CLAUSE[k] for k in ("mock", "human") if k in kinds]
    if not named:
        return "这份日志没登记是谁在桌边（`actor_kinds` 缺格），两种被点名的替身座位都不是它"
    return "、".join(named)


__all__ = [
    "char_ngrams",
    "jaccard",
    "collapse_round",
    "opening_distinct_rate",
    "shared_substring_rate",
    "template_top_fragments",
    "mentions_seat",
    "passivity_rate",
    "shannon_entropy",
    "Game",
    "read_game",
    "read_dir",
    "decisions",
    "speeches",
    "speech_rounds",
    "roles_of",
    "teams_of",
    "wilson_ci",
    "load_calibration",
    "CALIBRATION_KEYS",
    "m1_win_rate",
    "m2_illegal_rate",
    "m3_gate_pressure",
    "m3_gate_verdict",
    "M3_GATE",
    "m4_hallucination_rates",
    "m5_style_collapse",
    "m6_belief_action",
    "m7_cost_profile",
    "m8_strategy_proxies",
]
