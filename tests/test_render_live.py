"""The live half of "能拿给人看" (plan §9): a terminal view that tails a JSONL.

Two properties make this file different from `test_render_html.py`, and both come from the
fact that the game is *writing* while this is *reading*:

* **The viewer is read-only.** The append-only log is the only artifact the project has; a
  display that rewrites or truncates it is data loss dressed up as a UI. Asserted by hashing
  the file around a watch, not by trusting intent.
* **A torn last line is normal, not an error.** `run` appends line-by-line, so `tail` will
  occasionally read a half-written JSON object. Raising there kills the demo mid-game.

The UI-layer canary is here too — no private event text in the spectator frame, and no stated
`belief` slot either (it rides inside a public speech payload, so visibility cannot catch it).
Because a canary that asserts *absence* can be satisfied by an accident, each absence test has a
presence twin on the god frame, plus a no-wrap test: if the console chopped a leaking line in
half, "the text is not in the frame" would pass for the wrong reason.

`watch` is the interactive loop and is deliberately thin. The frame it draws is tested through
the pure `handle_key` / `tail_events` plus test-side `live_frame.frame_text`; the loop pins the three
things only a loop can get wrong — the viewer's own I/O rules above, *when* it redraws, and what
happens when there is no keyboard at all (a poll with no key, and a stdin that is closed).
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

from wolfengine import render_live
from wolfengine.config import Config
from wolfengine.compress import render_line
from wolfengine.events import EventLog, Kind, LogDamage

from live_frame import frame_text

sys.path.insert(0, str(Path(__file__).parent))
import test_golden_game as G  # noqa: E402


@pytest.fixture(scope="module")
def gold(tmp_path_factory):
    out = tmp_path_factory.mktemp("livegold")
    res = G.play_authored(out)
    events, meta = EventLog.read_records(res.path)
    return res, events, meta


def _private_lines(events) -> list[str]:
    """Every private event as the renderer would print it — stronger than payload text, since
    `seer_result` carries no `text` at all and only has something to leak once rendered."""
    return [render_line(e) for e in events if e.visibility != "all"]


# ------------------------------------------------------------------ the viewer's own I/O rules
def test_a_torn_last_line_is_skipped_not_fatal(tmp_path, gold):
    """`run` is appending while `watch` reads, so the tail of the file is sometimes half a
    JSON object. The frame must show what is complete and wait, not crash the demo."""
    _, events, _ = gold
    log = tmp_path / "g.jsonl"
    log.write_text(gold[0].path.read_text(encoding="utf-8") + '{"seq": 999, "kin', encoding="utf-8")
    got = render_live.tail_events(log)
    assert [e.seq for e in got] == [e.seq for e in events]


def test_a_broken_line_in_the_middle_is_not_skipped(tmp_path, gold):
    """The tolerance above is only for the tail, and the difference is the whole point: a
    damaged line in the middle is a missing event, and a viewer that quietly drew a frame
    around it would be showing a game that was not played — while a corrupted *end* is just
    tomorrow's poll waiting for the writer to finish."""
    _, events, _ = gold
    lines = gold[0].path.read_text(encoding="utf-8").splitlines()
    lines[len(lines) // 2] = '{"seq": 404, "ki'
    log = tmp_path / "broken.jsonl"
    log.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(LogDamage):
        render_live.tail_events(log)


def test_tail_events_resumes_after_the_last_seq_seen(tmp_path, gold):
    """Live tailing re-reads the file each poll. `after` is what keeps that O(new) instead of
    O(file), and a boundary that drops or repeats one event is a frame with a hole in it."""
    _, events, _ = gold
    first = render_live.tail_events(gold[0].path)
    again = render_live.tail_events(gold[0].path, after=first[-1].seq)
    assert again == []
    mid = render_live.tail_events(gold[0].path, after=first[10].seq)
    assert [e.seq for e in mid] == [e.seq for e in events[11:]]


def test_watching_does_not_modify_the_log(tmp_path, gold):
    """The log is the artifact. A viewer that rewrote it would destroy the thing it is
    displaying, and the audit trail would no longer be about the game that was played."""
    before = hashlib.sha256(gold[0].path.read_bytes()).hexdigest()
    code = render_live.watch(gold[0].path, one_shot=True)
    assert code == 0
    assert hashlib.sha256(gold[0].path.read_bytes()).hexdigest() == before


def test_no_live_path_touches_the_endpoint(gold, monkeypatch):
    """M6's gate is that the demo survives the endpoint being offline (plan §10) — asserted by
    making any call fatal rather than by observing none."""
    import httpx

    from wolfengine.transport import HttpTransport

    async def boom(self, *a, **kw):
        raise AssertionError("直播调用了端点")

    monkeypatch.setattr(HttpTransport, "chat", boom)
    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    monkeypatch.setattr(httpx.AsyncClient, "request", boom)
    monkeypatch.delenv(Config().api_key_env, raising=False)
    _, events, meta = gold
    frame_text(events, meta, god=True)
    render_live.watch(gold[0].path, one_shot=True, god=True)


# ---------------------------------------------------------------------- the two leak directions
def test_spectator_frame_carries_no_private_event(gold):
    _, events, meta = gold
    spectator = frame_text(events, meta)
    god = frame_text(events, meta, god=True)
    lines = _private_lines(events)
    assert len(lines) == 22, f"the fixture's private channel changed: {len(lines)}"
    for line in lines:
        assert line not in spectator, f"private event shown to spectators: {line[:30]}"
        assert line in god, f"god view is hiding a private event: {line[:30]}"


def test_spectator_frame_does_not_print_the_stated_belief_slot(gold):
    """The leak no visibility check can catch: 心里想 travels inside a public speech payload."""
    _, events, meta = gold
    spectator = frame_text(events, meta)
    god = frame_text(events, meta, god=True)
    whys = sorted({d["why"] for e in events
                   for d in (e.payload.get("belief") or {}).get("suspects", [])})
    assert len(whys) == 24
    for why in whys:
        assert why not in spectator, f"stated belief leaked: {why}"
    assert whys[0] in god, "the panel belongs in god view, not nowhere"


def test_a_long_line_reaches_the_frame_unbroken(tmp_path):
    """The absence tests above are only meaningful if the console is not wrapping: a leaked
    sentence chopped across two lines would pass them while leaking."""
    log = tmp_path / "x.jsonl"
    el = EventLog(log, meta={"actor_kinds": ["llm"], "game_id": "x", "deal_seed": 1})
    el.append(Kind.GAME_START, day=1, phase="day_speech", seats=[1])
    long_text = "这句话一共超过了终端宽度，用来证明画面没有把行折断。" * 4
    el.append(Kind.SPEECH, day=1, phase="day_speech", actor=1, text=long_text,
              act="accuse", target=None, result={"ok": True, "flags": [], "fallback": 0})
    events, meta = EventLog.read_records(log)
    assert long_text in frame_text(events, meta)


def test_markup_in_model_text_is_printed_literally(tmp_path):
    """A terminal renderer has the same trust problem as the HTML one, in a different syntax:
    the transcript is full of `[eNNN]` anchors, and a model that writes `[bold]…[/]` — or a
    wolf that writes `[/]` to break the next nine lines — must be *quoted*, not obeyed."""
    log = tmp_path / "x.jsonl"
    el = EventLog(log, meta={"actor_kinds": ["llm"], "game_id": "x", "deal_seed": 1})
    el.append(Kind.GAME_START, day=1, phase="day_speech", seats=[1])
    el.append(Kind.SPEECH, day=1, phase="day_speech", actor=1,
              text="[bold]我不是发言，我是排版指令[/]", act="accuse", target=None,
              result={"ok": True, "flags": [], "fallback": 0})
    events, meta = EventLog.read_records(log)
    frame = frame_text(events, meta)
    assert "[bold]我不是发言，我是排版指令[/]" in frame


# --------------------------------------------------------------------------- one seat's view
def test_revealing_a_seat_shows_that_seat_and_nobody_else(gold):
    """`1`-`9` hands the keyboard to one seat (and `enter` walks to the next one). The isolation
    property, through the UI: 7号's verdicts are 7号's, and the wolf team's chat is the team's,
    not the witch's."""
    _, events, meta = gold
    seer_verdict = "[e17] 法官（私发）：你查验的1号是狼人。"
    wolf_chat = "[e12] 狼队私聊 1号（指 3号）：刀3号，他发言太像神牌。"
    witch_notice = "[e14] 法官（私发）：今晚3号倒在了狼刀下。"

    assert seer_verdict in frame_text(events, meta, reveal_seat=7)
    assert wolf_chat in frame_text(events, meta, reveal_seat=1)
    assert witch_notice in frame_text(events, meta, reveal_seat=5)

    others = "".join(frame_text(events, meta, reveal_seat=s) for s in (1, 5, 9))
    assert seer_verdict not in others
    assert wolf_chat not in frame_text(events, meta, reveal_seat=5)
    assert witch_notice not in frame_text(events, meta, reveal_seat=9)
    plain = frame_text(events, meta)
    assert seer_verdict not in plain and wolf_chat not in plain


def test_reveal_names_the_seat_it_belongs_to(gold):
    """A private block with no owner on screen is indistinguishable from a leak — the viewer
    needs to see whose head they are reading."""
    _, events, meta = gold
    assert "7号视角" in frame_text(events, meta, reveal_seat=7)


def test_the_spectator_pointer_never_names_a_private_actor(gold):
    """The payload is not the only channel. Labelling 1号 as the one acting in the wolves'
    night slot tells the audience who the wolves are while showing them no private *text* — a
    canary that greps rendered lines cannot see it, so this reads the pointer line instead.

    The reverse control is the public case: a pointer that named nobody would pass the first
    half without proving anything.
    """
    _, events, meta = gold
    night = next(l for l in frame_text(
        [e for e in events if e.seq <= 13], meta).splitlines() if "正在：" in l)
    assert "狼队行动" in night
    assert not re.search(r"\d+号", night), night
    day = next(l for l in frame_text(
        [e for e in events if e.seq <= 27], meta).splitlines() if "正在：" in l)
    assert "发言 8号" in day, day


def test_the_pointer_itself_names_an_actor_only_when_it_may(gold):
    """The guard inside `_pointer` is invisible through a frame — the spectator event list is
    already filtered, so the pointer never *receives* a private event. That is exactly why both
    halves are pinned: the frame test above catches the call site being wrong (pointer built
    from the whole log, which names the wolves while showing them nothing), and this one catches
    the guard being dropped, which is the version that leaks the first time a caller passes an
    unfiltered list."""
    _, events, _ = gold
    private = next(e for e in events if e.kind == Kind.NIGHT_ACTION and e.visibility != "all")
    speech = next(e for e in events if e.kind == Kind.SPEECH)
    named = f"{private.actor}号"
    assert named in render_live._pointer(private, god=True)
    assert named not in render_live._pointer(private, god=False)
    assert f"{speech.actor}号" in render_live._pointer(speech, god=False)


# ------------------------------------------------------------------------ what a frame is for
def test_the_frame_points_at_the_phase_being_played(gold):
    """The pointer is the live view's whole reason to exist next to `replay`: where are we,
    and whose turn is it. Rebuilt from the tail of the event list, not from a phase field
    someone has to remember to update."""
    _, events, meta = gold
    speech = frame_text([e for e in events if e.seq <= 27], meta)
    assert "第1天" in speech and "正在：发言 8号" in speech, speech[:400]
    night = frame_text([e for e in events if e.seq <= 13], meta)
    assert "正在：狼队行动" in night, night[:400]


def test_the_tally_bar_reports_the_counts_in_the_log(gold):
    """Bar widths are decoration; the numbers beside them are the claim. Both come from the
    `vote_result` payload rather than a recount, so the frame and `audit` cannot disagree."""
    _, events, meta = gold
    frame = frame_text(events, meta)
    assert "1号 6票" in frame and "8号 3票" in frame
    assert "3号 4票" in frame and "2号 2票" in frame


def test_the_board_shows_who_is_still_sitting(gold):
    """9 cards, dead ones marked — the one panel a spectator and a colleague at the same screen
    both need, and the reason the frame is not just `tail -f`."""
    _, events, meta = gold
    frame = frame_text(events, meta)
    for s in range(1, 10):
        assert f"{s}号" in frame
    assert frame.count("✕") == 5, "five deaths in this game, marked once each on the board"
    assert "✕" not in frame_text([e for e in events if e.seq <= 20], meta)


def test_roles_stay_hidden_until_the_game_actually_ends(gold):
    """The terminal reveal is the payoff of the format, and it is gated on the `game_over`
    event — not on the day counter, not on the renderer's mood. Before it, a frame that named
    the roles would spoil the game it is streaming."""
    _, events, meta = gold
    before = [e for e in events if e.kind != Kind.GAME_OVER]
    assert "身份公开" not in frame_text(before, meta)
    assert "身份公开" not in frame_text(before, meta, god=True)
    assert "身份公开" in frame_text(events, meta)
    assert "7号 预言家" in frame_text(events, meta)


def test_the_frame_is_the_same_text_for_the_same_log(gold):
    """Two polls of an unchanged file must not redraw into a different document — a frame that
    reads the clock flickers, and flicker is what makes a long game unwatchable."""
    _, events, meta = gold
    a = frame_text(events, meta)
    b = frame_text(events, meta)
    assert a == b
    assert not re.search(r"\b20\d\d-\d\d-\d\d \d\d:\d\d", a)


def test_the_frame_never_prints_the_endpoint_host(gold):
    """base_url is a LAN address. It belongs in config, not in a screen that gets photographed
    and posted."""
    _, events, meta = gold
    doc = frame_text(events, meta, god=True)
    assert "100.87.65.60" not in doc and "13000" not in doc
    probe = next("\n".join(m["content"] for m in e.request["messages"])
                 for e in events if e.request.get("messages"))
    assert probe[200:280] not in doc


# --------------------------------------------------------------------------- the key handling
def test_g_toggles_the_god_view_and_q_quits(gold):
    """Keys change *what is shown*, never what is read: toggling must not re-derive anything,
    which is why the state carries flags and not a filter function."""
    st = render_live.State()
    st = render_live.handle_key(st, "g")
    assert st.god and not st.quit
    st = render_live.handle_key(st, "g")
    assert not st.god
    assert render_live.handle_key(st, "q").quit


def test_digits_pick_the_seat_to_reveal_and_esc_clears(gold):
    st = render_live.handle_key(render_live.State(), "7")
    assert st.reveal_seat == 7
    assert render_live.handle_key(st, "12").reveal_seat == 7, (
        "there is no twelfth seat; an out-of-range key is ignored, not clamped onto whoever is "
        "closest — landing in the head next to yours is the same bug class as showing a private "
        "event")
    assert render_live.handle_key(st, "escape").reveal_seat is None


def test_a_poll_that_returned_no_key_is_not_a_key_press(gold):
    """`_read_key` has to return something when the presenter typed nothing, and that something
    must not be a character the key map also understands. It used to be `""`, which `strip()`
    also produces for Enter and which `handle_key` read as esc — so the panel closed itself one
    poll (0.4 s) after being opened. Only an explicit esc may retract a seat."""
    st = render_live.State(reveal_seat=7)
    assert render_live.handle_key(st, None) == st, "no key arrived; the frame must not change"
    assert render_live.handle_key(st, "") == st
    assert render_live.handle_key(st, "\r").reveal_seat != 7, (
        "Enter is a keystroke with a job of its own, not a synonym for esc")


def test_enter_steps_through_the_seats_one_at_a_time(gold):
    """plan §9 item 4: 终局逐 seat reveal, 演示者控速. Seat by seat is the reveal节奏 — the
    roster line `draw` prints at the end gives the audience all nine heads at once, which is the
    same information with none of the suspense. Enter from "nobody" starts at 1 and it wraps,
    because the presenter needs to get back to 1号 without counting keypresses."""
    st = render_live.State()
    got = []
    for _ in range(11):
        st = render_live.handle_key(st, "\r")
        got.append(st.reveal_seat)
    assert got == [1, 2, 3, 4, 5, 6, 7, 8, 9, 1, 2]
    assert render_live.handle_key(render_live.State(reveal_seat=4), "\n").reveal_seat == 5, (
        "terminals send CR or LF depending on mode; both are Enter")


def test_the_reveal_panel_stays_open_until_a_key_actually_closes_it(gold, monkeypatch):
    """The loop half of the same property, through the frames a presenter would see: three
    quiet polls must not redraw, and the seat that was picked has to still be on screen."""
    keys = iter(["", "", "", "q"])
    monkeypatch.setattr(render_live, "_read_key", lambda timeout: next(keys))
    con = _Recorder()
    assert render_live.watch(gold[0].path, console=con, poll=0, reveal_seat=7) == 0
    assert len(con.frames) == 1, [f[:1] for f in con.frames]
    assert any("7号视角" in t for t in con.frames[0]), "the panel was retracted by a no-op poll"


class _StillPolling(Exception):
    """Raised by the fake key reader instead of letting a spinning loop hang the suite."""


def test_a_closed_keyboard_stops_polling_and_leaves_when_the_log_is_done(gold, monkeypatch):
    """`wolf watch` under a pipe, in CI, or behind `nohup` has no keyboard: `select` reports
    stdin readable forever and `read(1)` returns `""` forever. Treated as "a key was pressed",
    that is a hot loop at 100 % CPU that never exits — it used to keep tailing a finished game.
    Once EOF is seen the reader is retired; the tail continues, and the loop ends on the event
    that ends a game."""
    calls: list[float] = []

    def fake(timeout):
        if len(calls) >= 50:
            raise _StillPolling(f"still reading a closed stdin after {len(calls)} polls")
        calls.append(timeout)
        return "eof"

    monkeypatch.setattr(render_live, "_read_key", fake)
    try:
        code = render_live.watch(gold[0].path, console=_Recorder(), poll=0)
    except _StillPolling:
        code = None
    assert code == 0, f"did not exit; `_read_key` was called {len(calls)} times"
    assert len(calls) <= 2, f"closed stdin polled {len(calls)} times"


def test_the_reader_reports_a_closed_stdin_as_itself(gold, monkeypatch):
    """`_read_key` is the only place that meets the real terminal, and the loop above is tested
    against a stand-in — so without this, the mapping the whole exit path rests on (`read(1)`
    returning `""` is not a keystroke, and `\x1b` is Esc) has no test at all. Both halves go
    through `select`/`sys.stdin` patched at the module, not around them."""
    import io
    import select as _select

    monkeypatch.setattr(_select, "select", lambda r, w, t, timeout: (r, [], []))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert render_live._read_key(0) == "eof", "EOF must be distinguishable from a keystroke"
    monkeypatch.setattr(sys, "stdin", io.StringIO("\x1b"))
    assert render_live._read_key(0) == "escape", "the physical Esc key still has to retract"
    monkeypatch.setattr(sys, "stdin", io.StringIO("7"))
    assert render_live._read_key(0) == "7"


def test_watch_one_shot_prints_a_frame_without_waiting_for_keys(gold, capsys):
    """`--once` is what CI and the docs screenshot use, and it is the only proof the loop and
    the renderer are wired to the same function."""
    assert render_live.watch(gold[0].path, one_shot=True) == 0
    out = capsys.readouterr().out
    assert "第1天" in out and "本局不可复现" in out
    assert "[e17]" not in out


class _Recorder:
    """Console stand-in that keeps frames apart, because the property under test is about
    *when* a frame is drawn and *which* view it shows — both invisible to a single buffer."""

    def __init__(self):
        self.frames: list[list[str]] = []

    def clear(self):
        self.frames.append([])

    def print(self, *args, **kw):
        self.frames[-1].extend(str(a) for a in args if isinstance(a, str))


def test_a_key_press_redraws_even_when_the_log_stopped_growing(gold, monkeypatch):
    """The demo case is a finished file, and there the loop never gets a fresh event — so if a
    redraw is only triggered by new data, `g` is a dead key exactly when a presenter is pressing
    it in front of people."""
    keys = iter(["g", "q"])
    monkeypatch.setattr(render_live, "_read_key", lambda timeout: next(keys))
    con = _Recorder()
    assert render_live.watch(gold[0].path, console=con, poll=0) == 0
    assert len(con.frames) >= 2, "按键没有触发重绘"
    assert any("视角：观众" in t for t in con.frames[0])
    assert any("视角：上帝" in t for t in con.frames[-1]), (
        "redrew, but not the new state — the toggle never reached the frame")


def test_an_ignored_key_does_not_redraw(gold, monkeypatch):
    """The other half of the same property: redraw on *change*, not on input. A viewer that
    repaints every poll flickers, and flicker is what makes a long game unwatchable. Pinned
    after the fact, and proved by the mutation `st != before` → `True`."""
    keys = iter(["", "", "x", "q"])
    monkeypatch.setattr(render_live, "_read_key", lambda timeout: next(keys))
    con = _Recorder()
    render_live.watch(gold[0].path, console=con, poll=0)
    assert len(con.frames) == 1, [f[:1] for f in con.frames]
