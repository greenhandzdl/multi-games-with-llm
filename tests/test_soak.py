"""M4's mock criterion, kept as a check rather than as a claim: `--mock` 100 局 0 崩溃.

One scripted game proves the engine does the authored thing; a hundred synthetic games prove
it does *some* thing for every deal. The interesting failures are the rare ones — a board
configuration only seed 61 reaches, a tie that only happens on day 4 — and those need volume,
not authorship. So this file deliberately does not assert which seat wins.

What it does assert is the shape of a healthy batch, measured from the run this command
produces: every game decisive, no fallback, no timeout, both factions represented. A batch
where one side wins 100/100 is not "the wolves are strong", it is the rules or the stand-in
policy having collapsed into one branch — the same class of finding as the template collapse
this project exists to measure, just in the engine rather than in the model.

The distribution below is measured, and is printed by nothing else. It exists so that a later
change to the rules shifts it as a question here rather than as a quietly different baseline.
100 games from seeds 1..100: wolf_win 74 / good_win 26, days 2-5 (median 3), events 44-118,
fallback/retry/timeout/overflow zero in every game. The wolf advantage is a property of the
stand-in policy — a scripted seat votes on stated suspicion and never investigates — not a
finding about models, and it is the baseline real seats get compared against.
"""

from __future__ import annotations

import contextlib
import io
import re

import pytest

from wolfengine import cli
from wolfengine.events import EventLog, Kind

GAMES = 100
FIRST_SEED = 1

SUMMARY = re.compile(
    r"\[(g\d{8})\] (\S+) winner=(\S+) day=(\d+) events=(\d+) "
    r"fallback=(\d+) retry=(\d+) timeout=(\d+) overflow=(\d+)"
)


def _soak(out_dir):
    """Run the batch the way the CLI would, and return (rc, stdout) without a print."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(["run", "--mock", "--seed", str(FIRST_SEED), "--games", str(GAMES),
                       "--quiet", "--out", str(out_dir)])
    return rc, buf.getvalue()


def _rows(out: str):
    return [m.groups() for m in SUMMARY.finditer(out)]


@pytest.fixture(scope="module")
def soak(tmp_path_factory):
    """One batch, four questions. A hundred games costs about two seconds, and the four
    assertions below all read the same run — re-playing it per test would multiply the cost
    of this file without adding one more table that could have failed."""
    out = tmp_path_factory.mktemp("soak")
    rc, text = _soak(out)
    return rc, text, out


def test_a_hundred_mock_games_all_finish(soak):
    rc, out, out_dir = soak
    assert rc == 0, "the batch returned non-zero"
    rows = _rows(out)
    assert len(rows) == GAMES, "a game printed no summary line — it died mid-run"
    assert len({r[0] for r in rows}) == GAMES, "two games share an id"
    assert len(list(out_dir.glob("*.jsonl"))) == GAMES, "a game wrote no log"


def test_no_game_needed_the_engine_to_play_for_it(soak):
    """Zero fallbacks is the load-bearing half of the criterion.

    A mock seat cannot produce an illegal action by accident, so a fallback here means the
    legality gate rejected something the orchestration layer itself assembled — an engine bug
    wearing a data-quality costume, and 100 games find the seeds that trigger it.
    """
    _rc, out, _dir = soak
    bad = [(g, kind, fb, rt, to, ov)
           for g, kind, _w, _d, _e, fb, rt, to, ov in _rows(out)
           if any(int(x) for x in (fb, rt, to, ov))]
    assert not bad, f"{len(bad)} games needed gate intervention: {bad[:5]}"
    assert all(kind in ("good_win", "wolf_win") for _g, kind, *_ in _rows(out)), \
        "a synthetic table stalled into a draw — check the 弃票 escalation (plan R10)"


def test_the_batch_produces_games_for_both_sides(soak):
    _rc, out, _dir = soak
    rows = _rows(out)
    winners = {r[2] for r in rows}
    assert winners == {"wolf", "good"}, f"one faction won every game: {winners}"
    days = [int(r[3]) for r in rows]
    assert max(days) <= 5, "屠边 should resolve inside 5 days; a longer game is a rules bug"
    assert min(days) >= 2, "day 1 cannot end a 9-seat 屠边 game with 6 seats alive"


def test_every_log_agrees_with_the_summary_line_it_was_reported_by(soak):
    """The summary and the file must agree, or `wolf audit` reports a batch that never happened.

    Cheapest full check that also covers the file's own bookkeeping: reload each log, re-read
    its terminal event, and confirm `seq` really is the record count — a renderer that trusts
    `seq` cannot then be quietly wrong about a truncated file.
    """
    _rc, out, out_dir = soak
    by_id = {r[0]: r for r in _rows(out)}
    for path in sorted(out_dir.glob("*.jsonl")):
        events, meta = EventLog.read_records(path)
        game_id = re.search(r"_(g\d{8})\.jsonl$", path.name).group(1)
        over = events[-1]
        assert over.kind == Kind.GAME_OVER, f"{path.name} ends on {over.kind}"
        assert (over.payload["terminal"], over.payload["winner"]) == (by_id[game_id][1],
                                                                      by_id[game_id][2])
        assert int(by_id[game_id][4]) == len(events), "the summary lied about the event count"
        assert meta["game_id"] == game_id
        assert meta["actor_kinds"] == ["mock"], "a synthetic table must declare itself"
        assert over.seq == len(events), "seq is not the record count; readers count wrong"
