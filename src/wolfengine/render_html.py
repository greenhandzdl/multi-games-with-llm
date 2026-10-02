"""The shareable half of "能拿给人看" (plan §9): one self-contained HTML file per game.

Chosen over a web server because 可分享性 *is* the requirement — a single file gets sent to
somebody who has none of this installed. So: no JS, no external asset, no host address, no
font. `string.Template` carries the shell, and every byte of model text goes through
`html.escape`, because the transcript is untrusted input being opened by a third party.

Two modes, one renderer: `god=False` shows what an audience at the table could hear, `god=True`
adds the roles, the private channel, and the 心里想/嘴上说 panel. The spectator half is the
UI-layer canary — see tests/test_render_html.py. In particular the stated `belief` slot is held
back in *both* the visibility sense and the intent sense: it travels inside a public speech
payload, so nothing but this module's own rule keeps it out of spectator output.

The one exception is the 终局 reveal: `身份公开` prints the whole roster in *both* modes, and the
only thing authorising it is the presence of a `game_over` row (`game_over_event`). A finished
game has no spoilers left, so the gate is time, not mode — which is why the check lives in the
renderer rather than in `visibility`.

Nothing here reads a model or a socket. `render` takes events already loaded from the log, which
is what makes the demo survive the endpoint being offline tomorrow (R7).
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from string import Template
from typing import Any, Iterable

from . import roles
from .belief import build_belief
from .compress import cause_text, render_line
from .events import (Event, EventLog, Kind, empty_notice, meta_notice, roster_notice,
                     seq_notice, torn_notice, voting_waves)
from .info import eid

IMPOSSIBLE = "不可能感知"
TOO_LONG = "发言超长"
REFUSED = "拒绝"
FALLBACK = "引擎代打"

_TOPK = 3  # what the seat's own card ranks; the curve is the card, not a second opinion

_SHELL = Template("""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$title</title>
<style>
body{font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     margin:0 auto;padding:24px;max-width:52em;color:#1c1c1c;background:#fbfaf8}
h1{font-size:1.3em;margin:0 0 .2em}h2{font-size:1.05em;margin:1.6em 0 .4em;
   border-bottom:2px solid #d8d2c4;padding-bottom:.2em}
.meta{color:#6b6455;font-size:.86em;margin-bottom:1.2em}
.board{display:grid;grid-template-columns:repeat(auto-fit,minmax(9.5em,1fr));gap:6px;margin:0 0 1em}
.seat{border:1px solid #ddd6c6;border-radius:6px;padding:6px 8px;background:#fff;font-size:.86em}
.seat.dead{opacity:.45;text-decoration:line-through}
.chip{float:right;font-size:.8em;color:#7a5c00;background:#fff3d6;border-radius:3px;padding:0 4px}
ul.chron{list-style:none;margin:0;padding:0}
ul.chron li{padding:1px 0 1px .5em;border-left:3px solid transparent}
ul.chron li.speech{border-left-color:#8aa6c1}ul.chron li.vote{border-left-color:#c1b08a}
li.death{background:#fdefef}li.flagged{background:#fdf3e6}ul.chron li.last_words{border-left-color:#8f5fa8;background:#f6eefa;font-style:italic}
.mind{display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:.88em}
.mind section{border:1px solid #ddd6c6;border-radius:6px;padding:6px 10px;background:#fff}
.mind h3{font-size:1em;margin:.2em 0}
.mind ol{margin:.2em 0 .2em 1.2em;padding:0}
table{border-collapse:collapse;font-size:.86em;margin:.4em 0 1em}
th,td{border:1px solid #ddd6c6;padding:2px 7px;text-align:center}
.axis{display:flex;flex-wrap:wrap;gap:6px;font-size:.86em}
.axis span{border:1px solid #ddd6c6;border-radius:12px;padding:1px 8px;background:#fff}
footer{margin-top:2em;padding-top:1em;border-top:1px solid #ddd6c6;
       color:#6b6455;font-size:.82em}
</style></head><body>
<h1>$title</h1>
<p class="meta">$meta</p>
<main>$body</main>
<footer>$footer</footer>
</body></html>
""")


def _esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


def shown_events(events: Iterable[Event], *, god: bool) -> list[Event]:
    """The only visibility decision this module makes, and the only one it may make.

    Spectator mode is `visibility == "all"`, not "everything minus the interesting parts": a
    new private kind is then absent from the shared file by default, and shows up only once
    someone decides it should be in the god view.
    """
    evs = list(events)
    return evs if god else [e for e in evs if e.visibility == "all"]


def event_flags(e: Event) -> list[str]:
    """The flags worth showing a viewer: the gate's own verdict plus anything the payload
    carries. Two places hold them because the gate can flag before the payload is built — and
    the same flag lands in both, so they are merged here rather than printed twice from two
    call sites that each have to remember to dedupe."""
    both = list(e.result.get("flags") or []) + [
        str(f) for f in (e.payload.get("meta") or {}).get("flags", [])]
    return list(dict.fromkeys(both))


def markers(e: Event) -> str:
    """The bracketed hints, as text: no renderer here emits markup, so the live view and this
    one share the exact vocabulary rather than two near-copies of it."""
    flags = event_flags(e)
    out = [f"〔{FALLBACK}〕"] if e.result.get("fallback") else []
    if len(e.attempts) > 0:
        out.append(f"〔{_esc(REFUSED)}{len(e.attempts)}次〕")
    for f in flags:
        if f.startswith("impossible_percept"):
            out.append(f"〔{IMPOSSIBLE}〕")
        elif f.startswith("speech_too_long"):
            out.append(f"〔{TOO_LONG}〕")
    return " " + " ".join(out) if out else ""


def _line(e: Event) -> str:
    text = render_line(e)
    cls = e.kind if e.kind in ("speech", "vote", "death", "last_words") else ""
    if event_flags(e):
        cls = (cls + " flagged").strip()
    return (f'<li id="{_esc(eid(e.seq))}" class="{_esc(cls)}">'
            f'{_esc(text)}{markers(e)}</li>')


def roles_by_seat(events: list[Event]) -> dict[int, str]:
    """seat -> role. The seat of a deal row is its `actor`: the payload carries `role` and
    `teammates` only, because the row is addressed to one seat and not to the room."""
    return {e.actor: str(e.payload.get("role", "?")) for e in events
            if e.kind == Kind.DEAL and e.actor is not None}


def seats_of(events: list[Event]) -> list[int]:
    """The roster, from the public 开局 announcement — the deal rows are per-seat and absent
    from a spectator's event list, which is why the board cannot read them."""
    gs = next((e for e in events if e.kind == Kind.GAME_START), None)
    if gs is not None and gs.payload.get("seats"):
        return sorted(gs.payload["seats"])
    return sorted(roles_by_seat(events))


def role_zh(seat: int, ids: dict[int, str]) -> str:
    """The board's Chinese name for a seat's role id, falling back to the raw id.

    Shared with the live view because a seat called `seer` in one artifact and 预言家 in the
    other is the same renderer having drifted; the Chinese name is the one a reader of either
    expects. Unknown ids pass through rather than becoming `?`, since the log is the truth and a
    board that grew a role should show it, not hide it.
    """
    try:
        return roles.board_for().spec(ids[seat]).name_zh
    except (KeyError, AttributeError, ValueError):
        return ids.get(seat, "?")


def game_over_event(events: Iterable[Event]) -> Event | None:
    """The one gate a viewer's right to know a role depends on, for both renderers.

    The deal rows are private, so `roles_by_seat` needs the *loaded* log — but that is exactly
    the fact a spectator must not get before the end. Timing is therefore read from the log and
    never from the day counter: a game that ends mid-day still ends.
    """
    return next((e for e in events if e.kind == Kind.GAME_OVER), None)


def provenance(meta: dict[str, Any]) -> list[str]:
    """Which build wrote this file — the three version stamps the log carries, plus its board.

    A header line otherwise says only what happened in the game. These four answer what measured
    it, and they sit in the log's first line, which is exactly the line a person holding the
    rendered page (or looking at the live terminal) does not have. Empty for a log written before
    the stamps existed, so a view prints nothing rather than a row of `None`.
    """
    board = meta.get("board")
    return [v for v in (meta.get("contract_version"), meta.get("rules_version"),
                        meta.get("compress_version"), f"板{board}" if board else "") if v]


def provenance_line(meta: dict[str, Any]) -> str:
    """The four stamps as the one string a text screen prints — the join lives here, not at each site.

    Two screens fold them into a header sentence, the transcript prints them alone; if each wrote
    its own join, three orderings of the same four values is where it ends (`#174`'s three-times
    night order started the same way). Empty string for a log written before the stamps existed."""
    return " · ".join(provenance(meta))


def _board(events: list[Event], *, god: bool) -> str:
    """Nine cards, in seat order, showing only what any viewer may know: who is sitting where,
    and who is face down. The role chip is a god-view addition, not a board default."""
    seats = seats_of(events)
    roles = roles_by_seat(events)
    dead = {e.payload["seat"] for e in events if e.kind == Kind.DEATH}
    cards = []
    for s in seats:
        chip, curve = "", ""
        if god:
            chip = f'<span class="chip">{_esc(role_zh(s, roles))}</span>'
            curve = f' data-suspicion="{_esc(_suspicion(events, s))}"'
        cards.append(f'<div class="seat{" dead" if s in dead else ""}"{curve}>'
                     f'{chip}{s}号</div>')
    return f'<section class="board">{"".join(cards)}</section>'


def _suspicion(events: list[Event], seat: int) -> str:
    """Per-day snapshot of *that seat's* engine belief — the same `build_belief` call that fills
    region C2, replayed at every day boundary. Not the model's declared ranking: comparing the
    two is M6's job, and this curve is the engine's own ledger."""
    curve = []
    for day in sorted({e.day for e in events}):
        upto = max(e.seq for e in events if e.day <= day)
        visible = [e for e in events if e.seq <= upto and e.visible_to(seat)]
        st = build_belief(seat, visible)
        curve.append({str(s): st.score(s) for s in st.top_suspects(_TOPK)})
    return json.dumps(curve, ensure_ascii=False, separators=(",", ":"))


def _axis(events: list[Event]) -> str:
    cells = [f'<span data-death="{_esc(e.payload["seat"])}:{_esc(e.payload["cause"])}">'
             f'{e.payload["seat"]}号 {_esc(cause_text(e.payload["cause"]))}</span>'
             for e in events if e.kind == Kind.DEATH]
    return ('<h2>出局顺序</h2><div class="axis">' + "".join(cells) + "</div>") if cells else ""


def _matrix(day: int, wave: int, votes: list[Event]) -> str:
    """Rows are voters in the order their ballots landed, columns are the seats that received a
    ballot in this wave, plus 弃票 only if somebody abstained. A column for every alive seat would
    make an eight-vote wave a grid of mostly blanks and read as a finished table; an always-there
    弃票 column makes "nobody abstained" indistinguishable from an empty cell, which is the one
    thing this view has to keep apart."""
    cands = sorted({e.payload["target"] for e in votes if e.payload["target"] is not None})
    keys = [str(c) for c in cands]
    abstained = any(e.payload["target"] is None for e in votes)
    if abstained:
        keys.append("-1")
    head = "".join(f'<th class="cand" data-seat="{s}">{s}号</th>' for s in cands) \
        + ('<th class="abstain">弃票</th>' if abstained else "")
    rows = []
    for e in votes:
        target = str(-1 if e.payload["target"] is None else e.payload["target"])
        cells = "".join(
            f'<td class="ballot" data-voter="{e.actor}" data-target="{target}">●</td>'
            if k == target else "<td></td>" for k in keys)
        rows.append(f'<tr><th class="voter" data-seat="{e.actor}">{e.actor}号</th>{cells}</tr>')
    return (f'<table class="ballot-matrix" data-day="{day}" data-wave="{wave}">'
            f'<tr><th>投票人＼被投</th>{head}</tr>'
            f'{"".join(rows)}</table>')


def _tally(result: Event | None) -> str:
    """The numbers beside the picture, taken off the `vote_result` payload rather than recounted
    here — the same rule `render_live._bars` follows, so the two views cannot disagree about a
    tally. No result event means the wave never finished, and this prints nothing rather than a
    tally this renderer invented."""
    if result is None:
        return ""
    tally = result.payload.get("tally") or {}
    cells = "".join(f'<td class="tally" data-seat="{_esc(s)}">{_esc(n)}</td>'
                    for s, n in sorted(tally.items(), key=lambda kv: -kv[1]))
    heads = "".join(f"<th>{_esc(s)}号</th>" for s in sorted(tally, key=lambda k: -tally[k]))
    # `tally-row` marks the row carrying numbers, not the seat header, so counting the
    # class counts 票型 waves. Two rows sharing a class makes the marker say nothing.
    return ('<table><tr><th>座位</th>' + heads + '</tr>'
            '<tr class="tally-row"><th>票数</th>' + cells + '</tr></table>')


def _vote_blocks(day: int, events: list[Event]) -> str:
    """One block per voting wave — see `events.voting_waves` for why the wave, not the day, is
    the unit. Flattening a day into a single grid would put both of a PK revote's ballots in one
    row and make the revote invisible."""
    return "".join(f'<h3>第{day}天 第{i}轮投票</h3>{_matrix(day, i, votes)}{_tally(result)}'
                   for i, (votes, result) in enumerate(voting_waves(events), 1))


def mind_pairs(events: list[Event]) -> list[tuple[int, list[str], list[str]]]:
    """seat -> (what it claimed to think, what it said). One definition of the pair, because
    "which turns carry a stated belief" is a rule about the log and not a formatting choice;
    the live view and this one must agree on it or the two screens describe different games.

    God-view only in both renderers: see tests/test_render_html.py for the leak this guards.
    """
    pairs = []
    for seat in sorted({e.actor for e in events if e.actor}):
        turns = [e for e in events if e.kind in (Kind.SPEECH, Kind.LAST_WORDS) and e.actor == seat]
        thoughts = [f"{d['seat']}号 — {d['why']}"
                    for e in turns for d in (e.payload.get("belief") or {}).get("suspects", [])]
        if not thoughts:
            continue
        said = [str(e.payload.get("text", "")) for e in turns]
        pairs.append((seat, thoughts, said))
    return pairs


def _mind(events: list[Event]) -> str:
    """心里想 vs 嘴上说. The gap is the show: a wolf whose panel reads "怀疑3号" beside
    "3号是好人" is demonstrating the isolation rather than being told about it."""
    blocks = []
    for seat, thoughts, said in mind_pairs(events):
        blocks.append(f"<h3>{seat}号</h3><div class=\"mind\">"
                      f"<section><h3>心里想（模型自报）</h3>"
                      f"<ol>{''.join(f'<li>{_esc(t)}</li>' for t in thoughts)}</ol></section>"
                      f"<section><h3>嘴上说</h3>"
                      f"<ul>{''.join(f'<li>{_esc(t)}</li>' for t in said)}</ul></section></div>")
    return "".join(blocks)


def _reveal_section(events: list[Event]) -> str:
    """身份公开 — the whole roster, in *both* modes, once the log says the game ended.

    Read from the loaded events rather than the spectator-filtered ones on purpose: the deal rows
    that carry the roles are private, so the gate is the only thing letting a shareable file name
    a role. That makes it the UI-layer canary in tests/test_render_html.py — drop it and the
    spectator artifact becomes a spoiler with no other symptom.
    """
    if game_over_event(events) is None:
        return ""
    ids = roles_by_seat(events)
    cells = "".join(f'<span data-role="{_esc(ids.get(s, "?"))}">{s}号 {_esc(role_zh(s, ids))}</span>'
                    for s in seats_of(events))
    return '<h2>身份公开</h2><div class="axis">' + cells + "</div>"


def render(events: list[Event], meta: dict[str, Any], *, god: bool = False,
           torn: Iterable[str] = ()) -> str:
    evs = shown_events(events, god=god)
    by_day: dict[int, list[Event]] = {}
    for e in evs:
        by_day.setdefault(e.day, []).append(e)
    sections = [_board(evs, god=god), _axis(evs)]
    for day in sorted(by_day):
        body = "".join(_line(e) for e in by_day[day])
        sections.append(f'<h2>第{day}天</h2><ul class="chron">{body}</ul>'
                        + _vote_blocks(day, by_day[day]))
    sections.append(_reveal_section(events))
    if god:
        sections.append("<h2>心里想 / 嘴上说</h2>" + _mind(evs))

    over_ev = game_over_event(events)
    over = over_ev.payload if over_ev else {}
    # The body renders `evs` (what this view may show); the header counts `events` (what the file
    # holds). The one place the two lists meet, so it is the place the mistake was made: handed
    # `evs`, the audience page printed 私有事件0条 about a game with 26 of them.
    counts = _counts(events)
    title = f"狼人杀复盘 {_esc(meta.get('game_id', ''))}{'（上帝视角）' if god else ''}"
    # A page whose chronicle just stops reads as a game that stopped, so the notice goes in the
    # header line, next to the terminal it claims. Same sentence the terminal transcript prints,
    # from the same counter: two renderers, one number for one cut.
    # Same order the transcript appends them in, so "the page's ⚠ and the transcript's last line
    # are one sentence" stays true for the inputs that carry exactly one of the four.
    #
    # The roster sentence gets no ⚠: the four after it say the file is damaged, and a person at
    # seat 3 is not damage. It heads them because it is a claim about who answered, not a verdict
    # on these bytes. The ⚠ chain stays contiguous, so the ordering claim above still holds.
    # The four provenance values answer a different question than the rest of the line: not what
    # happened in this game but which build wrote this file. See `provenance`.
    roster = roster_notice(meta)
    meta_line = (f"第{max(by_day, default=0)}天结束 · {_esc(over.get('terminal', '未结束'))} · "
                 f"发言{counts['speech']}条 · 私有事件{counts['private']}条 · "
                 f"闸门拒绝{counts['refused']}次 · 视角：{'上帝' if god else '观众'}"
                 + "".join(f" · {_esc(v)}" for v in provenance(meta))
                 + (f" · {_esc(roster)}" if roster else "")
                 + "".join(f" · ⚠ {_esc(n)}"
                           for n in (meta_notice(meta), empty_notice(events, meta),
                                     seq_notice(events), torn_notice(torn)) if n))
    footer = ("本局不可复现：端点没有确定性（同请求同 seed 也会给出不同输出），seed 只决定发牌与"
              "座位序；本文件渲染的是当时落盘的日志，不是一次重跑。"
              + ("" if god else " 观众模式只含公开事件：模型的自报排序与私有频道均不在本文件中，"
                               "身份只在终局事件之后公开。"))
    return _SHELL.substitute(title=title, meta=meta_line, body="".join(sections), footer=footer)


def _counts(evs: list[Event]) -> dict[str, int]:
    return {
        "speech": sum(1 for e in evs if e.kind == Kind.SPEECH),
        "private": sum(1 for e in evs if e.visibility != "all"),
        "refused": sum(len(e.attempts) for e in evs),
    }


def write_report(log_path: str | os.PathLike[str], out_path: str | os.PathLike[str], *,
                 god: bool = False) -> Path:
    """Read the log, write one file. No other I/O, and deliberately no other mode."""
    events, meta, torn = EventLog.read_split(Path(log_path))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(events, meta, god=god, torn=torn), encoding="utf-8")
    return out
