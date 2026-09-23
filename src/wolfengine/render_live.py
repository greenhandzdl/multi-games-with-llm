"""The live half of "能拿给人看" (plan §9): a terminal view of a game as it is played.

`watch` opens the JSONL the game process is *still appending to*, which is the one place in this
project that reads a file being written. Two consequences shape the module:

* **Torn tail tolerated.** A half-written JSON object at the end of the file is the normal case,
  not corruption, so `tail_events` drops it and waits for the next poll. It does not skip a
  broken line in the middle — that would be a different failure, and silent is the wrong answer
  to it.
* **Nothing is ever written back.** The log is the artifact the metrics, the 复盘 and the batch
  comparisons are computed from; a viewer that rewrote it would be destroying its own subject.

There is one renderer per concern, shared on purpose: which events a spectator may see, which
markers a turn carries, and which turns count as "having stated a belief" are imported from
`render_html` rather than re-derived here. Two definitions of those would mean the live screen
and the file you send a colleague describe different games.

The leak this module has to keep is the one `visibility` cannot see. A stated `belief` slot rides
inside a public speech payload, and an actor field on a private event names a wolf to the
audience without showing a word of private text — so the spectator frame filters events *and*
declines to name actors it has no public reason to name.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
from typing import Any, Iterator

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .compress import render_line
from .events import Event, EventLog, Kind, empty_notice, meta_notice, seq_notice
from .render_html import (event_flags, game_over_event, markers, mind_pairs, role_zh,
                          roles_by_seat, seats_of, shown_events)

DEAD = "✕"
REVEAL = "{seat}号视角（仅本席可见）"

_PHASE_ZH = {
    "night_wolf": "狼队行动",
    "night_witch": "女巫行动",
    "night_seer": "预言家验人",
    "day_speech": "发言",
    "day_vote": "投票",
    "day_pk_speech": "PK 发言",
    "last_words": "遗言",
    "hunter_shot": "猎人开枪",
    "over": "终局",
}

HELP = "g 上帝视角 · 1-9 看某席 · enter 下一席 · esc 收回 · q 退出"


# ------------------------------------------------------------------------------------ the read
def _read(path: str | Path) -> tuple[list[Event], dict[str, Any]]:
    """The one place this module opens a game log. Both legs come out of `read_split`: asking it
    for the events and then finding the manifest by another route is how the header and the
    metrics ended up with two rules for which line is the manifest — `read_split` keys on
    `seq == 0` and skips blank lines, a first-line reader does neither.
    """
    events, meta, _torn = EventLog.read_split(path)
    return events, meta


def tail_events(path: str | Path, *, after: int = 0) -> list[Event]:
    """Every complete event in the file with `seq > after`.

    Goes through `EventLog.read_split` rather than reading the file its own way: a frame drawn
    from a log no offline reader can open is a demo that works and a record that doesn't. That
    means the live view inherits the one-line torn-tail bound too — several unparsable lines is
    damage, and the viewer saying so (loudly, with a line number) beats quietly drawing a frame
    with a day missing.
    """
    events, _meta = _read(path)
    return [e for e in events if e.seq > after]


# ----------------------------------------------------------------------------------- the state
@dataclass(frozen=True)
class State:
    """Flags, not closures: a key press may change *what is shown* and must never change what
    is read or re-derived. Anything richer here would put game logic in the viewer."""

    god: bool = False
    reveal_seat: int | None = None
    quit: bool = False


def handle_key(st: State, key: str | None) -> State:
    """`None` is "no key arrived this poll", and it must not be expressible as a keystroke.
    It used to be `""`, which is also what `strip()` turns Enter into and what the key map read
    as esc — so a panel the presenter opened with `7` closed itself 0.4 s later, and `Enter`
    retracted the seat it was supposed to walk to. Hence: Enter is matched on the raw key,
    before any stripping, and an unrecognised key changes nothing."""
    if key is None:
        return st
    if key in ("\r", "\n"):             # CR from a cbreak terminal, LF from a canonical one
        return replace(st, reveal_seat=1 if st.reveal_seat in (None, 9) else st.reveal_seat + 1)
    k = key.strip().lower()
    if k in ("q", "quit", "ctrl-c", "\x03"):
        return replace(st, quit=True)
    if k == "g":
        return replace(st, god=not st.god)
    if k in ("escape", "esc"):
        return replace(st, reveal_seat=None)
    if k.isdigit() and 1 <= int(k) <= 9:
        # Out of range is ignored rather than clamped: landing on somebody else's head because
        # you mistyped is the same class of bug as showing a private event.
        return replace(st, reveal_seat=int(k))
    return st


# -------------------------------------------------------------------------------- the drawing
def _pointer(last: Event | None, *, god: bool) -> str:
    """`正在：…`. The actor is named only when the event being pointed at is public (see
    tests/test_render_live.py): `1号 狼队行动` would hand the audience the wolf list while
    showing them no private text at all."""
    if last is None:
        return "正在：等待开局"
    label = _PHASE_ZH.get(last.phase, last.phase)
    if last.actor is not None and (god or last.visibility == "all"):
        return f"正在：{label} {last.actor}号"
    return f"正在：{label}"


def _board(events: list[Event], *, god: bool) -> Table:
    """Nine cards in seat order, marked dead, with the role chip only for the god view. The
    roster comes from the public 开局 line because a spectator's event list has no deal rows.

    Cells are `Text`, not `str`: rich parses markup inside a table cell, and a seat card is
    assembled from log content.
    """
    dead = {e.payload["seat"] for e in events if e.kind == Kind.DEATH}
    ids = roles_by_seat(events)
    table = Table(box=None, padding=(0, 1), show_header=False)
    for row in (range(1, 6), range(6, 10)):
        cells = []
        for s in row:
            label = f"{s}号 {role_zh(s, ids)}" if god else f"{s}号"
            cells.append(Text(label + (DEAD if s in dead else ""),
                              style="red" if s in dead else ""))
        table.add_row(*cells)
    return table


def _bars(events: list[Event]) -> list[str]:
    """票数 read straight off the `vote_result` payload — the same numbers `audit` counts, not a
    recount here. The block is the picture; the digits are the claim."""
    out = []
    for e in events:
        if e.kind != Kind.VOTE_RESULT:
            continue
        tally = e.payload.get("tally") or {}
        cells = "  ".join(f"{s}号 {n}票 {'█' * int(n)}"
                          for s, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))
        out.append(f"第{e.day}天票型 {cells}")
    return out


def _reveal(events: list[Event], seat: int) -> list[str]:
    private = [e for e in events if e.visible_to(seat) and e.visibility != "all"]
    return [render_line(e) + markers(e) for e in private]


def draw(console: Console, events: list[Event], meta: dict[str, Any], *,
         god: bool = False, reveal_seat: int | None = None) -> None:
    """One frame. Everything below it is formatting; the three decisions that matter —
    which events, which markers, which belief pairs — are render_html's.

    `markup=False` on every line, including this module's own headings. Rich reads `[...]` as a
    style tag, and the transcript is built out of `[eNNN]` anchors and whatever a model typed —
    so the safe rule is that nothing in this view is ever *obeyed*, only printed. Colour comes
    from `style=`, which takes a name this module chose rather than a name the log supplied.
    """
    def line(text: str, *, style: str = "") -> None:
        console.print(text, markup=False, style=style or None)

    evs = shown_events(events, god=god)
    over = game_over_event(events)
    by_day: dict[int, list[Event]] = {}
    for e in evs:
        by_day.setdefault(e.day, []).append(e)

    line(f"狼人杀直播 {meta.get('game_id', '') or '—'} · 第{evs[-1].day if evs else 1}天 · "
         f"视角：{'上帝' if god else '观众'}", style="bold")
    unnamed = meta_notice(meta)
    if unnamed:
        line(f"〔{unnamed}〕")
    # `events`, not `evs`: these sentences are about what the file holds, not about what this view
    # is allowed to show. A seat's own frame can legitimately be empty while the file is full.
    for notice in (empty_notice(events, meta), seq_notice(events)):
        if notice:
            line(f"〔{notice}〕")
    line(_pointer(evs[-1] if evs else None, god=god))
    console.print(_board(evs, god=god))
    for bar in _bars(evs):
        line(bar)

    for day in sorted(by_day):
        line(f"── 第{day}天 ──", style="bold")
        for e in by_day[day]:
            line(render_line(e) + markers(e),
                 style="red" if e.kind == Kind.DEATH else ("yellow" if event_flags(e) else ""))

    if reveal_seat is not None:
        line(f"── {REVEAL.format(seat=reveal_seat)} ──", style="bold")
        for text in _reveal(events, reveal_seat):
            line(text, style="dim")

    if over is not None:
        ids = roles_by_seat(events)
        line("身份公开：" + " ".join(f"{s}号 {role_zh(s, ids)}" for s in seats_of(events)),
             style="bold")
        line(f"终局：{over.payload.get('terminal', '')} · "
             f"胜方：{over.payload.get('winner') or '无'}")
    elif god:
        line("（身份尚未公开，上帝视角从发牌事件读取角色）", style="italic")

    if god:
        line("── 心里想 / 嘴上说 ──", style="bold")
        for seat, thoughts, said in mind_pairs(evs):
            line(f"{seat}号 心里想：" + "；".join(thoughts), style="cyan")
            line(f"{seat}号 嘴上说：" + "；".join(said))

    line(f"本局不可复现：端点没有确定性，本画面读的是已落盘的日志 {meta.get('game_id', '')}"
         f" · {HELP}", style="dim")


def frame_text(events: list[Event], meta: dict[str, Any], *, god: bool = False,
               reveal_seat: int | None = None, width: int = 100) -> str:
    """The frame as plain text — `soft_wrap` so no line is ever broken. Without it a long
    sentence would be chopped across two lines, and every "this text is not on screen"
    assertion in the test suite would pass for the wrong reason."""
    buf = StringIO()
    draw(Console(file=buf, soft_wrap=True, width=width, color_system=None,
                 force_terminal=False, highlight=False),
         events, meta, god=god, reveal_seat=reveal_seat)
    return buf.getvalue()


# ---------------------------------------------------------------------------------- the loop
@contextmanager
def _cbreak() -> Iterator[None]:
    """Raw-ish keystrokes when there is a terminal to read them from; a no-op when stdin is a
    pipe, which is how `watch --once` and the CI run reach this code."""
    fd = None
    old = None
    try:
        import termios
        import tty

        if sys.stdin.isatty():
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd)
    except (ImportError, OSError, ValueError):  # pragma: no cover - platform dependent
        fd = None
    try:
        yield
    finally:  # pragma: no cover - platform dependent
        if fd is not None and old is not None:
            import termios

            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key(timeout: float) -> str | None:
    """`None` when nothing was typed, the literal `"eof"` when there never will be: a pipe,
    `/dev/null` or a detached run leaves `select` reporting stdin readable forever with `read(1)`
    returning `""`, which is a closed keyboard rather than a key."""
    import select

    data, _, _ = select.select([sys.stdin], [], [], timeout)
    if not data:
        return None
    ch = sys.stdin.read(1)
    if ch == "":
        return "eof"
    return "escape" if ch == "\x1b" else ch


def watch(path: str | Path, *, god: bool = False, reveal_seat: int | None = None,
          one_shot: bool = False, poll: float = 0.4, console: Console | None = None) -> int:
    """Tail one game. `one_shot` prints a single frame and returns — the mode CI, a docs
    screenshot and any `ssh host wolf watch … | head` use, and the only one under test here.

    The loop below is deliberately the thinnest thing that could work: read keys, read new
    events, redraw. It calls no model and writes no file, which is what makes it safe to leave
    running next to a game that is costing money per turn.
    """
    con = console or Console()
    st = State(god=god, reveal_seat=reveal_seat)
    events, meta = _read(path)

    def frame() -> None:
        con.clear()
        draw(con, events, meta, god=st.god, reveal_seat=st.reveal_seat)

    frame()
    if one_shot:
        return 0
    seen = events[-1].seq if events else 0
    keyboard = True
    try:
        with _cbreak():
            while not st.quit:
                before = st
                if keyboard:
                    key = _read_key(poll)
                    if key == "eof":
                        # Retire the reader rather than treat a closed stdin as an endless
                        # stream of blank keystrokes: that is a loop at 100 % CPU which, with
                        # every keystroke ignored, never reaches a quit key and never exits.
                        keyboard = False
                    else:
                        st = handle_key(st, key)
                        if st.quit:
                            break
                else:
                    time.sleep(poll)
                fresh = tail_events(path, after=seen)
                if fresh:
                    events += fresh
                    seen = fresh[-1].seq
                # A key press redraws on its own. Waiting only for new events makes `g` a dead
                # key on a finished log, which is the file a demo is usually run against.
                if fresh or st != before:
                    frame()
                if not keyboard and game_over_event(events) is not None:
                    return 0
    except KeyboardInterrupt:  # pragma: no cover
        return 0
    return 0
