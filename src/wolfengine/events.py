"""Append-only event log: the single source of truth.

A game is never re-run to be reproduced (the endpoint has no determinism), so this log
IS the artifact. Replay = rendering these records, never re-calling the model.

`visibility` is a field on the event, not a convention: a seat sees exactly the events
whose visibility contains it, and no other path exists (see info.py).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

Visibility = Literal["all"] | frozenset[int]  # "all" | the set of seats allowed to read it

PUBLIC: Visibility = "all"

# The manifest line carries no `kind` at all, and seq 0 is the only way to spot it: real
# events start at 1. A fake kind would be the same split-brain this module's `Kind` exists
# to prevent, one line earlier in the file.
META_SEQ = 0

# The keys `records_from_lines` below reads by subscript. Listing them up front is what lets a
# file of the wrong kind get one sentence naming its line, instead of a `KeyError` that has
# already lost which line and which file it came from.
EVENT_KEYS = ("seq", "kind", "day", "phase", "visibility")


def seats(*seats_: int) -> Visibility:
    return frozenset(seats_)


class LogDamage(ValueError):
    """A line the log reader was handed that it cannot read as an event.

    Two shapes, one handling: a line that is not JSON at all (and is not the torn tail), and a JSON
    object that turns out to be a *different kind of file* — `--dry-run` leaves its prompt dumps in
    the very folder holding the games, and they have keys of their own and no `seq`.

    A `ValueError` because that is what "this text is not JSON" already is (`json.JSONDecodeError`
    subclasses it), so a caller written to tolerate one still catches the other. The point of the
    new name is its message: `JSONDecodeError` reports a column inside one line and a bare
    `KeyError` reports a field name, and neither says which file or which line — no help in a
    folder full of look-alike names.
    """


class Kind:
    """The event vocabulary. Declared once because three modules once invented three
    names for the same fact (`vote` / `vote_cast` / `vote_result`) and every consumer
    silently stopped matching: beliefs never moved, folded days lost their tallies, and
    the prompt fell back to dumping raw dicts. A kind listed here must be renderable
    (see test_wiring.py::test_every_declared_kind_has_a_renderer) *and* written by
    something (see test_every_declared_kind_has_an_emitter): the half that was missing
    was a vocabulary entry with a renderer and a counter and no emitter, which reads as
    "this never happened" forever.

    Payload shapes are documented per kind here rather than in a design doc, because a
    shape that drifts is that failure wearing a new hat — `test_payload_shape.py` checks.
    """

    GAME_START = "game_start"    # all      {"seats": [int]}
    DEAL = "deal"                # own seat {"role": str, "teammates": [int]}
    PHASE = "phase"              # all      {"text": str}
    SPEECH = "speech"            # all      {"text": str, "act": str, "target": int|null, "evidence": [str], "belief": dict|null, "meta": dict}
    NIGHT_ACTION = "night_action"  # acting seats {"action": "kill|save|poison", "target": int|null, "text": str, "act": str, "evidence": [str], "belief": dict|null, "meta": dict, "_idem": str}
    WOLF_CHAT = "wolf_chat"      # wolves   {"text": str, "act": str, "target": int|null, "evidence": [str], "belief": dict|null, "meta": dict}
    SEER_RESULT = "seer_result"  # seer     {"target": int, "verdict": "wolf|good"}
    NOTICE = "notice"            # given    {"text": str, "about": int|null}  法官只对个别座位说的话（女巫见刀口）
    VOTE = "vote"                # all      {"target": int|null, "text": str, "act": str, "evidence": [str], "belief": dict|null, "meta": dict, "_idem": str}  appended when the tally opens
    VOTE_RESULT = "vote_result"  # all      {"tally": {seat: n}, "exiled": int|null, "abstained": int, "pending_pk": bool}
    DEATH = "death"              # all      {"seat": int, "cause": str}
    LAST_WORDS = "last_words"    # all      {"text": str, "act": str, "target": int|null, "evidence": [str], "belief": dict|null, "meta": dict}
    COMPACTION = "compaction"    # all      {"summary": str, "window": int, "folded_days": [int], "_idem": str}
                                 #          one per distinct fold state, written by agent.py
                                 #          from compress.fold_body() (plan §83)
    GAME_OVER = "game_over"      # all      {"winner": str|null（平局与三种 aborted_*）, "terminal": str,
                                 #             "degraded_game": bool, "degraded_threshold": int}


KINDS: frozenset[str] = frozenset(
    v for k, v in vars(Kind).items() if not k.startswith("_") and isinstance(v, str)
)


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str                      # a Kind value; see Kind's payload table
    day: int
    phase: str
    visibility: Visibility
    payload: dict[str, Any]
    actor: int | None = None
    t_wall: float = 0.0
    # provenance of the model call that produced this event, when there was one
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    attempts: tuple[dict[str, Any], ...] = ()  # rejected raw outputs, kept for future preference pairs
    result: dict[str, Any] = field(default_factory=dict)

    def visible_to(self, seat: int) -> bool:
        return self.visibility == PUBLIC or (
            isinstance(self.visibility, frozenset) and seat in self.visibility
        )

    def to_json(self) -> str:
        d: dict[str, Any] = {
            "seq": self.seq,
            "kind": self.kind,
            "day": self.day,
            "phase": self.phase,
            "actor": self.actor,
            "t_wall": self.t_wall,
            "visibility": (
                "all" if self.visibility == PUBLIC else sorted(self.visibility)  # type: ignore[arg-type]
            ),
            "payload": self.payload,
            "request": self.request,
            "response": self.response,
            "attempts": [dict(a) for a in self.attempts],
            "result": self.result,
        }
        # Whitelist serialisation: nothing not named above can ever reach disk.
        # In particular there is no code path that writes headers or the api key.
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def voting_waves(events: Iterable[Event]) -> list[tuple[list[Event], "Event | None"]]:
    """Ballots grouped into the waves that were actually asked for.

    `phases._ballots` writes a whole voting wave and then exactly one `vote_result`, so the
    tally is the boundary — and the **day is not**: the tie-break path revotes into a PK round,
    putting two ballots from the same seat into one day. Anything that counts per day merges
    those two into one cell and makes the revote invisible.

    A trailing run of ballots with no tally is still returned, paired with `None`: a game that
    ended mid-vote is a fact about the log, and dropping its tail would read as "those seats
    were never asked".
    """
    waves: list[tuple[list[Event], Event | None]] = []
    cur: list[Event] = []
    for e in events:
        if e.kind == Kind.VOTE:
            cur.append(e)
        elif e.kind == Kind.VOTE_RESULT and cur:
            waves.append((cur, e))
            cur = []
    if cur:
        waves.append((cur, None))
    return waves


def torn_extent(torn: Iterable[str]) -> dict[str, int]:
    """被砍掉的末行有多少：几行、几字节。这两个数只在这里数一次。

    句子（`torn_notice`）、机读面（`audit` 的 `torn_tail`）和批次报告（#56）都要它，而字节本身不能
    跟着一起走：被砍的半行可能是 `wolf_chat`。所以对外只给量、不给内容。
    """
    lines = list(torn)
    return {"lines": len(lines), "chars": sum(len(l) for l in lines)}


def torn_notice(torn: Iterable[str]) -> str:
    """One sentence saying the file was cut, for whichever reader shows a human the transcript.

    A count and nothing else. The dropped bytes can be the middle of a `wolf_chat`, and the whole
    point of the visibility field is that nobody gets to read those on the way to the exit — a
    notice that quoted what it couldn't parse would be the leak.

    Owned here so the terminal transcript and the 复盘 HTML cannot drift into describing the same
    cut with two different numbers (`render_html` escapes it, `cli` prints it, neither counts).
    The counting itself lives in `torn_extent`, which is the same answer `audit` prints as JSON.
    """
    lines = list(torn)
    if not lines:
        return ""
    d = torn_extent(lines)
    return (f"日志在这里截断：末 {d['lines']} 行、{d['chars']} 字节"
            "没能读成事件，这一局在这一行之后没有记录")


def meta_notice(meta: dict[str, Any]) -> str:
    """One sentence for whichever reader is about to show a game it cannot name.

    A file with no manifest line still *reads* (`read_split` returns `{}` on purpose, because a
    parser that only accepts its own output cannot check that output), and each human exit then
    renders the blank as if a game had been played: `replay` prints nothing at all and exits 0,
    the live header prints `狼人杀直播  · 第1天` with an empty name slot, the 复盘 page claims
    "第0天结束 · 未结束". `audit` is the only exit that says it plainly, because there `events: 0`
    is a field. Owned here so the three cannot grow three wordings of one fact.
    """
    if meta.get("game_id"):
        return ""
    return "这个文件没有开局记录（manifest 那一行）：说不出它是哪一局"


def empty_notice(events: list[Any], meta: dict[str, Any]) -> str:
    """One sentence for the other way a reader gets nothing: the manifest is there, nothing is.

    `write_meta()` lands before the first game fact does, so a run killed while its first call was
    in flight leaves precisely these bytes — the ordinary shape of an interrupted batch on an
    endpoint that has been refusing. Every human exit then renders a whole game that happened to
    have no events in it: a named live header, an empty board, a page reading "第0天结束 · 未结束",
    and a transcript that is one blank line with exit code 0. `audit` again says it plainly
    (`events: 0`, `days: 0`), which is the asymmetry this family of sentences keeps closing.

    Quiet when there is something to show, and quiet when there is no manifest at all: the two
    emptiness sentences describe two different files and must never appear together.
    """
    if events or not meta.get("game_id"):
        return ""
    return "这一局只有开局记录：后面一条事件都没有"


DAMAGE_WORDS = (("gaps", "缺号", "处"), ("duplicates", "重号", "个"),
                ("out_of_order", "顺序倒挂", "处"))


def seq_damage(events: list[Any]) -> dict[str, int]:
    """How far the `seq` numbers sit from 1,2,3… in the order the file holds them.

    Counted, never repaired. Sorting the events would make a broken file print a clean
    chronicle whose anchors run backwards, and a reader checking `[e3] → [e2]` against the file
    could no longer tell whether the file was repaired or the renderer was lying. The numbers are
    also independent of each other on purpose: a hand-deleted middle line and a hand-duplicated
    one are different accidents, and one "broken: yes/no" flag would hide which.
    """
    seqs = [e.seq for e in events]
    tally: dict[int, int] = {}
    for s in seqs:
        tally[s] = tally.get(s, 0) + 1
    return {
        "gaps": len(set(range(min(seqs), max(seqs) + 1)) - set(seqs)) if seqs else 0,
        "duplicates": sum(1 for n in tally.values() if n > 1),
        "out_of_order": sum(1 for a, b in zip(seqs, seqs[1:]) if b < a),
    }


def seq_notice(events: list[Any]) -> str:
    """One sentence naming which of those three counts is non-zero, or "" when the numbering is clean.

    `eNNN` is how every artifact in this project refers to an event — the transcript, the belief
    report, a citation in `comparison.md`. A file with a hole, a twin, or a backwards stretch
    makes those addresses quietly ambiguous or absent, which is a claim about the file, not about
    the game. `audit` prints the same dict this formats, so there is one arithmetic.
    """
    damage = seq_damage(events)
    parts = [f"{label} {damage[key]} {unit}" for key, label, unit in DAMAGE_WORDS
             if damage[key]]
    if not parts:
        return ""
    return "这份日志的编号不是连续递增的：" + "、".join(parts)


def split_torn_tail(lines: list[str]) -> tuple[list[str], list[str]]:
    """(the lines to parse, the one line dropped) — the bound is the whole design.

    `append` writes one line and closes the file, so the only interruption a writer can leave
    behind is a **single** half-written line at the end: one `write()` of one object, killed
    halfway. Two unparsable lines are not that, and neither is one with a good line after it — so
    this drops at most one line and lets the second one surface as ordinary damage from
    `records_from_lines`, with its line number. The old live viewer's `while the last line fails:
    pop()` had no bound, which is how "the tail is junk" turned into "the game stopped after
    event 1": a tolerance that widens itself silently reports a short game, not a broken file.

    A blank line at the end is neither damage nor a torn tail; it is nothing. The writer never
    emits one (every record carries its own `\\n`), and a reader that called it torn would flag
    every hand-written fixture that forgot one.
    """
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    torn: list[str] = []
    if out:
        try:
            json.loads(out[-1])
        except json.JSONDecodeError:
            torn.append(out.pop())
    return out, torn


class EventLog:
    """One JSONL file per game. Append-only."""

    def __init__(self, path: str | os.PathLike[str], meta: dict[str, Any] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[Event] = []
        self._next_seq = 1
        self.meta = meta or {}

    def __len__(self) -> int:
        return len(self._events)

    @property
    def last_seq(self) -> int:
        return self._next_seq - 1

    def append(
        self,
        kind: str,
        *,
        day: int,
        phase: str,
        visibility: Visibility = PUBLIC,
        actor: int | None = None,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        attempts: Iterable[dict[str, Any]] = (),
        result: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> Event:
        """Append one record, including the model call that produced it.

        Deliberately one-phase: the LLM call has already returned by the time a game
        fact exists, so there is nothing to bind later. That keeps the file strictly
        append-only — no rewrites — which is what makes it a usable source of truth
        for offline replay and for future preference-pair export.

        `idempotency_key` guards a genuine failure mode, not a theoretical one: a call
        that timed out client-side may still have completed server-side and been
        recorded by a retry path, which would double-write an irreversible fact (a
        kill, a vote). `agent.py` passes `f"{phase}:{day}:{seat}:{as_of}"` for those two kinds.
        """
        if idempotency_key is not None:
            for e in reversed(self._events):
                if e.payload.get("_idem") == idempotency_key:
                    return e
        payload = dict(fields.pop("payload", None) or {})
        payload.update(fields)
        if idempotency_key is not None:
            payload["_idem"] = idempotency_key
        ev = Event(
            seq=self._next_seq,
            kind=kind,
            day=day,
            phase=phase,
            visibility=visibility,
            actor=actor,
            t_wall=time.time(),
            payload=payload,
            request=request or {},
            response=response or {},
            attempts=tuple(attempts),
            result=result or {},
        )
        self._events.append(ev)
        self._next_seq += 1
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(ev.to_json() + "\n")
        return ev

    def all(self) -> list[Event]:
        return list(self._events)

    def write_meta(self) -> None:
        """Establish the file with its manifest as the first line.

        Append-only has to cover this line too or the discipline is decorative: called after
        events exist it would either overwrite them (`"w"`) or put a manifest behind the
        events, where `read()` is no longer allowed to relocate it. Refusing is also the only
        thing that stops two games sharing one file id, which would interleave two `seq`
        numberings irrecoverably.
        """
        if self._events:
            raise RuntimeError("write_meta() must come before the first append()")
        if self.path.exists() and self.path.stat().st_size > 0:
            raise FileExistsError(f"{self.path} already holds a game log; a game id is not reusable")
        with self.path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"seq": META_SEQ, "meta": self.meta},
                                ensure_ascii=False, separators=(",", ":")) + "\n")

    def for_seat(self, seat: int) -> list[Event]:
        return [e for e in self._events if e.visible_to(seat)]

    @staticmethod
    def read(path: str | os.PathLike[str]) -> list[Event]:
        events, _ = EventLog.read_records(path)
        return events

    @staticmethod
    def read_records(path: str | os.PathLike[str]) -> tuple[list[Event], dict[str, Any]]:
        events, meta, _torn = EventLog.read_split(path)
        return events, meta

    @staticmethod
    def read_split(path: str | os.PathLike[str]) -> tuple[list[Event], dict[str, Any], list[str]]:
        """(events, meta, the tail that was dropped).

        A file with no manifest line reads as `{}` rather than raising: fixtures are written by
        hand, and a parser that only accepts its own output cannot be used to check that output.

        The tail is reported, never echoed. A caller that prints what it dropped would put the
        bytes of a half-written `wolf_chat` in front of whoever is reading the output — the shape
        of leak `render_live`'s canaries exist to catch — so this hands over the lines and every
        printer decides what a *count* of them means. `read_records` is the same read for callers
        that have nowhere to put the third leg.
        """
        text = Path(path).read_text(encoding="utf-8")
        usable, torn = split_torn_tail(text.splitlines())
        try:
            events, meta = EventLog.records_from_lines(usable)
        except LogDamage as e:
            raise LogDamage(f"{path}: {e}") from e
        return events, meta, torn

    @staticmethod
    def records_from_lines(lines: Iterable[str]) -> tuple[list[Event], dict[str, Any]]:
        """The parse, split from the file read so a live tail can share `read_split`'s format
        without owning a second copy of it."""
        out: list[Event] = []
        meta: dict[str, Any] = {}
        manifests = 0
        for no, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                # `e.msg`, not `str(e)`: the latter starts with its own "line 1 column 210", which
                # reads as a second, contradicting line number next to the real one.
                raise LogDamage(f"第 {no} 行不是合法 JSON（{e.msg}，行内第 {e.colno} 列）") from e
            if not isinstance(d, dict):
                raise LogDamage(f"第 {no} 行是一条 JSON {type(d).__name__}，不是一条记录")
            if d.get("seq") == META_SEQ:
                manifests += 1
                if manifests > 1:
                    # The writer already refused this: `write_meta` raises once any event exists,
                    # because two games in one path interleave two `seq` numberings irrecoverably.
                    # Until now the reader disagreed by silence — the later manifest overwrote the
                    # earlier one, so `cat a.jsonl b.jsonl > both.jsonl` exported a page whose
                    # header named `b`'s game id above `a`'s events.
                    raise LogDamage(f"第 {no} 行是第 {manifests} 条开局记录：一个文件里混进了两局，"
                                    f"两套编号交错之后还原不成一局日志")
                meta = dict(d.get("meta", {}))
                continue
            if missing := [k for k in EVENT_KEYS if k not in d]:
                raise LogDamage(f"第 {no} 行没有一条事件必有的 {'/'.join(missing)}"
                                f"（它有的键是 {'/'.join(sorted(d))}），这不是一局日志")
            vis = d["visibility"]
            out.append(
                Event(
                    seq=d["seq"],
                    kind=d["kind"],
                    day=d["day"],
                    phase=d["phase"],
                    actor=d.get("actor"),
                    t_wall=d.get("t_wall", 0.0),
                    visibility=PUBLIC if vis == "all" else frozenset(vis),
                    payload=d.get("payload", {}),
                    request=d.get("request", {}),
                    response=d.get("response", {}),
                    attempts=tuple(d.get("attempts", ())),
                    result=d.get("result", {}),
                )
            )
        return out, meta


def roster_notice(meta: dict[str, Any]) -> str:
    """One sentence for whoever is about to call a transcript "a game between the models".

    `actor_kinds` is the only place a game records *who* sat at its table, and no human exit read
    it: measured 2026-09-25T12:04Z against a log with a person at seat 3, `replay`,
    `watch --once` and the 复盘 page contained neither 「真人」 nor the string "human" — the only
    un-reproducibility they announced was the endpoint's, whose words are not on that table at all.
    The clause that excludes such a game from the paired corpus (`metrics.SYNTHETIC_CLAUSE`) is
    deliberately not quoted here: a screen reader wants to know whose words these are, and that
    sentence already has exactly one owner on the batch side.

    Owned here, with `meta_notice`/`empty_notice`/`seq_notice`/`torn_notice`, so the three exits
    cannot grow three wordings of one fact. Placed at the end of the module rather than next to
    that family because the `idempotency_key` early return at `events.py:332` and the outer
    `LogDamage` wrapper at `events.py:410` are pointed at by line number in ``docs/iterations.md``
    (8 such cites lived in README when this sentence was written; `#135` moved that prose, 0 left).
    """
    kinds = meta.get("actor_kinds") or []
    if "human" not in kinds:
        return ""
    return (f"桌边坐着一个真人（actor_kinds={kinds}）：他答的那几席不经过端点，"
            "这一屏不是九个模型自玩的那一局")
