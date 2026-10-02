"""The single-file 复盘: one artifact you can send somebody, with nothing running behind it.

Plan §9 makes the HTML the shareable half of "能拿给人看" — it must open with the endpoint
offline, on a machine where nobody runs python. That makes these tests the UI-layer version of
the canary. `visibility` is already proved correct in `test_info_isolation.py`, so what is under
test here is the renderer's own judgement about *what a spectator is shown*. Two leaks are
possible and only one is caught by the event field:

  1. a private event (wolf chat, seer result, 夜间行动) reaching spectator output — a visibility
     bug, which `info.py` should make unrepresentable;
  2. a *public* event's private-by-intent contents reaching it — the stated `belief` slot rides
     inside a public speech payload, so no visibility check can see it.

Number 2 is the reason this file exists.

The API asserted below is the wished-for one: `render(events, meta, god=...) -> str` and
`write_report(log_path, out_path, god=...) -> Path`. Marker vocabulary is pinned here and used
verbatim by the renderer: `〔拒绝N次〕`, `〔不可能感知〕`, `〔发言超长〕`, `〔引擎代打〕`,
`本局不可复现`, and the god-only `心里想` / `嘴上说` panel. The first three come from
`event_flags()`/`markers()`; 〔发言超长〕 has no case in the authored transcript, so it is pinned
on hand-fed flags instead (see `test_the_over_long_marker_needs_its_own_flag_and_prints_no_length`).
"""

from __future__ import annotations

import dataclasses
import html as html_mod
import json
import re
import sys
from pathlib import Path

import pytest

from wolfengine import belief, render_html
from wolfengine.events import PUBLIC, Event, EventLog, Kind

sys.path.insert(0, str(Path(__file__).parent))
import test_golden_game as G  # the authored transcript, reused as the fixture


@pytest.fixture(scope="module")
def gold(tmp_path_factory):
    out = tmp_path_factory.mktemp("htmlgold")
    res = G.play_authored(out)
    events, meta = EventLog.read_records(res.path)
    return res, events, meta


@pytest.fixture(scope="module")
def pair(gold):
    """Both renderings of one file, so every "spectator must not show X" has a matching "god
    view must show X" — otherwise a renderer that prints nothing at all passes."""
    _, events, meta = gold
    return (render_html.render(events, meta, god=False),
            render_html.render(events, meta, god=True))


def _private_prose(events) -> list[str]:
    return [e.payload["text"] for e in events
            if e.visibility != "all" and len(str(e.payload.get("text") or "")) > 6]


# ------------------------------------------------------------------ the two leak directions
def test_spectator_output_carries_no_private_event(pair, gold):
    _, events, _ = gold
    spectator, god = pair
    samples = _private_prose(events)
    # Pinned as a set, not a minimum: `>= 5` would silently pass a fixture that lost a scene,
    # and a *sixth* string appearing is also news worth failing on.
    assert set(samples) == {
        "刀3号，他发言太像神牌。",
        "今晚3号倒在了狼刀下。",
        "刀9号，他票得太快了。",
        "今晚9号倒在了狼刀下。",
    }, samples
    for s in samples:
        assert html_mod.escape(s) not in spectator, f"private text leaked to spectators: {s[:24]}"
        assert html_mod.escape(s) in god, f"god view is hiding a private event: {s[:24]}"


def test_spectator_output_does_not_print_the_stated_belief_slot(pair, gold):
    """The leak `visibility` cannot catch: 心里想 lives inside a *public* speech payload.

    An audience that hears the ranking has no game left to watch, and a colleague who sees it
    beside the speech cannot tell whether the engine wrote the model's head or the model did.
    """
    _, events, _ = gold
    spectator, god = pair
    whys = sorted({d["why"] for e in events
                   for d in (e.payload.get("belief") or {}).get("suspects", [])})
    assert len(whys) == 24, "these attributable strings are the probe; if they merge, so does a leak"
    for why in whys:
        assert html_mod.escape(why) not in spectator, f"stated belief leaked: {why}"
    assert html_mod.escape(whys[0]) in god, "the panel belongs in god view, not nowhere"


def test_neither_mode_prints_the_prompt_that_was_sent(gold, pair):
    """`request.messages` is the whole assembled prompt — region A verbatim, the persona card,
    and every event that seat could see. It is on disk for `--dry-run` and `audit`, and it is
    not the game: pasting it into a shareable file would leak per-seat views into a public one.
    """
    _, events, _ = gold
    probe = next("\n".join(m["content"] for m in e.request["messages"])
                 for e in events if e.request.get("messages"))
    marker = probe[200:280]
    assert len(marker) == 80 and all(marker not in doc for doc in pair)


# --------------------------------------------------------------------------- the god extras
def test_god_view_adds_the_head_to_head_panel(pair):
    _, god = pair
    spectator = pair[0]
    assert "心里想" in god and "嘴上说" in god
    assert "心里想" not in spectator and "嘴上说" not in spectator


def test_god_curves_are_the_engine_belief_recomputed_independently(pair, gold):
    """`data-suspicion` must equal `belief.build_belief` at each day boundary — the same call
    that builds the C2 card — not the model's declared ranking and not a re-derivation."""
    _, events, _ = gold
    spectator, god = pair
    assert "data-suspicion" not in spectator, (
        "a shared file may not carry what each seat privately knows: 7号's curve is pushed by "
        "its own seer verdicts, so publishing the curves publishes the seer's checks")
    blocks = re.findall(r'data-suspicion="([^"]*)"', god)
    assert len(blocks) == 9
    days = sorted({e.day for e in events})
    for seat, raw in zip(range(1, 10), blocks):
        got = json.loads(html_mod.unescape(raw))
        want = []
        for day in days:
            upto = max(e.seq for e in events if e.day <= day)
            st = belief.build_belief(seat, [e for e in events
                                            if e.seq <= upto and e.visible_to(seat)])
            want.append({str(s): st.score(s) for s in st.top_suspects(3)})
        assert got == want, f"seat {seat}'s curve is not the engine's"


def test_the_curve_is_folded_from_each_seat_view_not_the_loaded_log(gold, monkeypatch):
    """The renderer's own `visible_to` filter is an equivalent mutant on this fixture, so it is
    pinned where it can be seen — at the call.

    `build_belief` re-guards the only private kind that moves numbers (`SEER_RESULT and
    observer == actor`, belief.py), so handing it the whole log changes no byte of output today.
    It would change them the moment a new private kind joins the consumed list, and then the
    god-view ledger would be quietly handed to every seat card.
    """
    _, events, meta = gold
    handed: dict[int, tuple[int, ...]] = {}
    real = belief.build_belief

    def spy(observer, evs):
        handed[observer] = tuple(e.seq for e in evs)
        return real(observer, evs)

    monkeypatch.setattr(render_html, "build_belief", spy)
    render_html.render(events, meta, god=True)
    assert set(handed) == set(range(1, 10)), "every seat's card must be built from its own view"
    for seat, seqs in handed.items():
        assert seqs == tuple(e.seq for e in events if e.visible_to(seat)), f"seat {seat}"



# ------------------------------------------------------------------------ the game's shape
def _waves(events) -> list[list]:
    """Voting waves, split the only way the log allows: `_ballots` writes a whole wave and then
    one `vote_result`, so a `vote_result` is the wave boundary. Day 2 of this transcript is a
    tie followed by a PK revote, which means the same seat casts two ballots in one day."""
    waves: list[list] = []
    cur: list = []
    for e in events:
        if e.kind == Kind.VOTE:
            cur.append(e)
        elif e.kind == Kind.VOTE_RESULT and cur:
            waves.append(cur)
            cur = []
    return waves + [cur] if cur else waves


def test_the_ballot_marks_are_the_ballots_in_the_log(pair, gold):
    """Mark count and per-wave counts recomputed from the events, so a matrix that drops a wave
    (or double-counts a PK revote) cannot agree with both."""
    _, events, _ = gold
    spectator = pair[0]
    marks = re.findall(r'<td class="ballot" data-voter="(\d+)" data-target="(-?\d+)">', spectator)
    votes = [e for e in events if e.kind == Kind.VOTE]
    assert len(marks) == len(votes) == 21
    assert marks == [(str(e.actor), str(-1 if e.payload["target"] is None
                                       else e.payload["target"])) for e in votes]
    tables = re.findall(r'<table class="ballot-matrix" data-day="(\d+)" data-wave="(\d+)">',
                        spectator)
    assert tables == [("1", "1"), ("2", "1"), ("2", "2")], \
        "一轮一张表；摊平成一张清单会把 2 号的两次票挤在同一格上"


def test_the_matrix_is_one_row_per_voter_and_one_column_per_balloted_seat(pair, gold):
    """A 9-seat 票型 read off a two-column list means counting down eight rows and holding them
    in your head, which is the one thing a file you *send* to somebody should not ask for. The
    columns are the seats that actually received a ballot in that wave, plus 弃票 only when
    somebody abstained: nine columns for eight votes would be a grid of blanks, and an
    always-there 弃票 column would say "nobody abstained" in a shape nobody can distinguish from
    an empty cell.
    """
    _, events, _ = gold
    spectator = pair[0]
    first = spectator.split('<table class="ballot-matrix"')[1].split("</table>")[0]
    wave = _waves(events)[0]
    cands = sorted({e.payload["target"] for e in wave if e.payload["target"] is not None})
    got_abstention = any(e.payload["target"] is None for e in wave)

    assert re.findall(r'<th class="cand" data-seat="(\d+)">', first) == [str(s) for s in cands]
    assert ('<th class="abstain">' in first) is got_abstention
    assert re.findall(r'<th class="voter" data-seat="(\d+)"', first) \
        == [str(e.actor) for e in wave], "行=投票人，按落票顺序"
    cols = len(cands) + int(got_abstention)
    assert first.count("<td") == len(wave) * cols, "每行都要铺满列，否则'谁没投谁'读不出来"
    assert first.count("<td></td>") == len(wave) * cols - len(wave), \
        "没投的格子必须是空的：任何占位符都会让'没人投他'和'投了但看不见'长成同一个样子"


def _ballot(seq: int, actor: int, target: int | None) -> Event:
    return Event(seq=seq, kind=Kind.VOTE, day=3, phase="day_vote",
                 visibility=PUBLIC, payload={"target": target}, actor=actor)


def test_the_abstain_column_appears_exactly_when_somebody_abstained():
    """The whole reason this is a unit test on `_matrix` and not a claim about the transcript:
    the authored game has 21 ballots and **zero** abstentions, so on that data deleting the
    column entirely is invisible — and 弃票 is precisely the outcome `abstain_streak` exists to
    count, i.e. the thing that makes a mock-vs-real table differ. Both directions pinned.
    """
    with_abstention = render_html._matrix(
        3, 1, [_ballot(1, 2, 5), _ballot(2, 3, None), _ballot(3, 5, None)])
    without = render_html._matrix(3, 1, [_ballot(1, 2, 5), _ballot(2, 3, 5)])

    assert '<th class="abstain">弃票</th>' in with_abstention
    assert re.findall(r'data-voter="(\d+)" data-target="(-?\d+)"', with_abstention) \
        == [("2", "5"), ("3", "-1"), ("5", "-1")], "两张弃票各占一行，不在同一格里合并"
    assert with_abstention.count("<td") == 3 * 2, "两列：5号 与 弃票"
    assert "abstain" not in without
    assert 'data-target="-1"' not in without


def test_the_rows_are_in_the_order_the_ballots_were_written():
    """Also unreachable from a full game: `_ballots` gathers the wave in voter order, and voter
    order is seat order, so "the renderer re-sorts the rows" changes nothing on any real log —
    while being exactly the kind of quiet second opinion a render layer should not hold. The
    log's order is the engine's; the grid must repeat it, not fix it.
    """
    out = render_html._matrix(3, 1, [_ballot(1, 7, 4), _ballot(2, 2, 4), _ballot(3, 9, None)])
    assert re.findall(r'<th class="voter" data-seat="(\d+)"', out) == ["7", "2", "9"]
    assert re.findall(r'<td class="ballot" data-voter="(\d+)"', out) == ["7", "2", "9"]


def test_the_day_one_columns_add_up_to_the_tally(pair):
    spectator, _ = pair
    cells = dict(re.findall(r'<td class="tally" data-seat="(\d+)">(\d+)</td>', spectator))
    assert [cells.get(s) for s in ("1", "8")] == ["6", "3"], cells
    assert spectator.count('class="tally-row"') == 3, "day1 exile, day2 tie, PK exile"


def test_the_death_axis_is_in_order_with_causes(pair):
    spectator, _ = pair
    assert re.findall(r'data-death="(\d+):([^"]+)"', spectator) \
        == [("1", "exiled"), ("4", "poison"), ("9", "wolf_kill"), ("3", "exiled"),
            ("2", "hunter_shot")]


def test_every_shown_event_line_keeps_its_own_anchor(pair):
    """The `[eNNN]` the model was shown and the anchor a viewer clicks are one id. Two numbering
    schemes in one artifact is how a demo stops being checkable against the log."""
    spectator, _ = pair
    assert 'id="e19"' in spectator and "[e19]" in spectator
    assert "第1天" in spectator and "第2天" in spectator


def test_the_gate_is_visible_not_silently_applied(pair, gold):
    """8号's invented citation was refused and corrected; 6号's hallucinated perception was
    flagged and played. A clean transcript of a messy game hides the two most interesting facts
    about it (plan §8 M2/M4's 口径 split, in the UI)."""
    _, events, _ = gold
    spectator = pair[0]
    line27 = re.search(r'<li[^>]*>\[e27\](.*?)</li>', spectator, re.S).group(0)
    assert "〔拒绝1次〕" in line27
    line21 = re.search(r'<li[^>]*>\[e21\](.*?)</li>', spectator, re.S).group(0)
    assert "〔不可能感知〕" in line21
    assert line21.count("〔不可能感知〕") == 1, (
        "the same flag is carried in result.flags and payload.meta.flags; printing both reads "
        "as two problems with one sentence, and a viewer counts markers, not sources")
    assert spectator.count("〔拒绝1次〕") == 1, "one retry in this game; more means the gate moved"
    assert "〔引擎代打〕" not in spectator and len([e for e in events if e.result.get("fallback")]) == 0


def _flagged_speech(flags: list[str]) -> Event:
    """One speech turn with `flags` left in **both** places the gate can write them."""
    return Event(seq=90, kind=Kind.SPEECH, day=1, phase="day_speech", visibility=PUBLIC,
                 actor=1, payload={"text": "先听大家说。", "meta": {"flags": flags}},
                 result={"ok": True, "fallback": 0, "flags": flags})


def test_the_over_long_marker_needs_its_own_flag_and_prints_no_length():
    """词表里第四个标记 `〔发言超长〕` 此前只有一句散文：渲染分支写了它，全工程没有一条用例给过
    它一次 True 或一次 False。authored 转录 17 条发言全部短于 `SPEECH_SOFT_LIMIT`，实测 0 条
    `speech_too_long` 标记（2026-09-21：`_flagged_speech` 之前的检查脚本），所以"把这条渲染分支
    整个删掉"在那份日志上是**等价变异**——与 `弃票` 那一列同一困境，载体换成直接喂 `markers()`。

    正反两侧都要有：只断言"文档里不该有"会让一个永不印任何东西的渲染器通过。
    """
    got = render_html.markers(_flagged_speech(["speech_too_long:188"]))
    assert "〔发言超长〕" in got, got
    assert got.count("〔发言超长〕") == 1, (
        "同一个 flag 同时躺在 result.flags 与 payload.meta.flags 里，印两遍是在虚报问题个数")
    assert "188" not in got, "字符数是引擎内部量，别印进一份要转发给别人的文件"

    assert "〔发言超长〕" not in render_html.markers(_flagged_speech(["speech_empty"]))
    assert render_html.markers(_flagged_speech([])) == ""


def test_roles_are_revealed_in_both_modes_once_the_game_ends(gold):
    """The 终局 reveal is the payoff of the format, and the shareable file is where an audience
    actually reads it — so it is gated on the `game_over` event, not on the mode. Before that
    event exists, neither mode may name a role."""
    _, events, meta = gold
    before = [e for e in events if e.kind != Kind.GAME_OVER]
    for god in (False, True):
        assert "身份公开" not in render_html.render(before, meta, god=god)
    for god in (False, True):
        doc = render_html.render(events, meta, god=god)
        assert "身份公开" in doc, f"mode god={god} ends without the reveal"
        assert "7号 预言家" in doc


def test_the_chips_are_in_the_same_language_as_the_live_view(pair, gold):
    """The role labels a viewer reads come from the board definition, not from the log: `wolf` in
    one artifact and 狼人 in the other is two renderers drifting apart, and the one a Chinese
    audience reads should not be the database key.

    Scoped to the two label lists rather than the whole document, because two other places carry
    the id on purpose: `data-role` is a machine hook beside the Chinese text (like `data-death`),
    and the deal line keeps the schema id because it *is* the sentence the seat was told — the
    same `render_line` the prompt was assembled from.
    """
    _, events, _ = gold
    spectator, god = pair
    chips = re.findall(r'<span class="chip">([^<]+)</span>', god)
    revealed = re.findall(r'<span data-role="[^"]*">\d+号 ([^<]+)</span>', god)
    assert chips == revealed and len(chips) == 9, (chips, revealed)
    assert set(chips) == {"狼人", "平民", "预言家", "女巫", "猎人"}, chips
    assert re.findall(r'<span data-role="[^"]*">\d+号 ([^<]+)</span>', spectator) == chips, (
        "both modes show the same words, only at different times")


def test_an_unknown_role_id_is_printed_not_swallowed():
    """The fallback matters because the log is the truth: a board that grows a role should show
    the id it used rather than a `?` that reads as "this seat had no role"."""
    assert render_html.role_zh(1, {1: "guard"}) == "guard"
    assert render_html.role_zh(2, {2: "seer"}) == "预言家"


# --------------------------------------------------------------------------- the artifact
def test_the_file_is_self_contained(pair):
    """No JS, no external asset, no host: the point is that it opens on a plane, and that a
    shared file does not disclose where the endpoint is."""
    spectator, god = pair
    doc = spectator + god
    assert "<script" not in doc and "<link" not in doc
    assert not re.search(r'\b(src|href)="(https?:)?//', doc)
    assert "100.87.65.60" not in doc


def test_the_footer_declares_the_game_unrepeatable_once(pair, gold):
    _, events, meta = gold
    for god in (False, True):
        doc = render_html.render(events, meta, god=god)
        assert doc.count("本局不可复现") == 1
        assert "seed" in doc and doc.index("本局不可复现") > doc.index("</main>")


def test_model_text_is_escaped_not_interpreted(tmp_path):
    """The 复盘 is handed to other people and the speech is untrusted input: opening the file
    must not execute the transcript. Escaping is not redaction — the text stays readable."""
    log = tmp_path / "x.jsonl"
    el = EventLog(log, meta={"actor_kinds": ["llm"], "game_id": "x", "deal_seed": 1})
    el.append(Kind.GAME_START, day=1, phase="night_wolf", seats=[1])
    el.append(Kind.SPEECH, day=1, phase="day_speech", actor=1,
              text='<img src=x onerror="alert(1)">', act="accuse", target=None,
              result={"ok": True, "fallback": 0, "flags": []})
    events, meta = EventLog.read_records(log)
    doc = render_html.render(events, meta, god=False)
    assert "<img src=x" not in doc and "&lt;img" in doc and "onerror" in doc


def test_rendering_the_same_file_twice_gives_the_same_bytes(gold, pair):
    """Byte-stability is the rule that keeps region A+B cached (plan §5); a renderer that reads
    the clock makes two openings of one file two documents."""
    _, events, meta = gold
    assert render_html.render(events, meta, god=False) == pair[0]
    assert "生成时间" not in pair[0] and not re.search(r"\b20\d\d-\d\d-\d\d \d\d:\d\d", pair[0])


def test_write_report_lands_one_file_without_touching_the_network(gold, tmp_path, monkeypatch):
    """M6's criterion is that the demo survives the endpoint being down (plan §10). Asserted by
    making any call fatal, not by observing none.

    替身名单从 `conftest` 那一个循环装：这里以前只写了 `post` 一个动词，是那三份抄本里最窄的一
    份（`#222`）。
    """
    from conftest import block_the_endpoint

    block_the_endpoint(monkeypatch, "复盘渲染")
    res, _, _ = gold
    out = tmp_path / "review.html"
    assert render_html.write_report(res.path, out) == out and out.exists()
    assert "第1天" in out.read_text(encoding="utf-8")


# ------------------------------------------------- the header line states facts about the file
def _meta_line(doc: str) -> str:
    """The one line that carries the counts, pulled out so a failure prints the sentence rather
    than the whole page."""
    line = re.search(r"第\d+天结束[^\n<]*", doc)
    assert line, doc[:400]
    return line.group(0)


def test_the_audience_page_counts_the_private_events_the_file_holds(pair, gold):
    """`docs/views.md` 的「读的是文件里的事件数，不是这一屏被允许看的事件数」同一条规则的另一半。

    Found by reading a shipped artifact, not the code: the audience page of a real log printed
    `私有事件0条` while the same file on disk holds 26 non-public records, and the footer of that
    very page says 私有频道…均不在本文件中 — a document contradicting itself two lines apart.
    The number is a claim about the game (a game with no wolf chat and no seer check is a
    collapsed game, which is the one thing this project is watching for); which of those events
    this view is allowed to *show* is a claim about the view, and it lives in the footer.
    """
    _, events, _ = gold
    private = sum(1 for e in events if e.visibility != "all")
    assert private == 22, "夹具动了：下面钉的那格要跟着改，不是让它自己变成恒真"
    spectator, god = pair
    assert f"私有事件{private}条" in spectator, _meta_line(spectator)
    assert f"私有事件{private}条" in god, _meta_line(god)


def test_the_two_pages_print_one_number_for_each_of_the_three_counts(pair):
    """Two views of one file may differ in 视角 and in what they render, not in what they count.

    Pinned as a triple rather than one field because the bug was in the argument (`_counts(evs)`
    over the filtered list): any count whose kinds are partly private can drift, and 闸门拒绝 is
    the one that drifts silently — `refused_turns` in `audit` counts wolf-chat and night-action
    turns too (`metrics.DECISION_KINDS`), so the page and the machine readout split on the
    refusal that happened in a private channel.
    """
    spectator, god = pair
    pat = r"发言(\d+)条 · 私有事件(\d+)条 · 闸门拒绝(\d+)次"
    ms, mg = re.findall(pat, spectator), re.findall(pat, god)
    assert len(ms) == len(mg) == 1, (_meta_line(spectator), _meta_line(god))
    assert ms == mg, f"one file, two numbers: {_meta_line(spectator)} vs {_meta_line(god)}"


def test_the_header_of_each_page_names_the_stamps_the_log_carries(pair, gold):
    """`#178`/`#179` 把四格接进机器出口 `audit`，给人看的这一页还答不出同一句话。

    页眉那一条念的是"这一局发生了什么"（天数、终局、三个计数、视角、名册、破损），没有一格说
    "这份文件是哪一版规则写的、坐在哪张板上"。四格从开局就落在文件第一行里，而拿到一个 HTML
    文件的人手边没有那个文件第一行——`docs/views.md` 说这页是**当时落盘日志的渲染**，那就得连
    量过它的那几把尺子一起印。
    """
    _, _, meta = gold
    spectator, god = pair
    for page in (spectator, god):
        for key in ("contract_version", "rules_version", "compress_version"):
            assert meta[key] in _meta_line(page), _meta_line(page)
        assert f"板{meta['board']}" in _meta_line(page), _meta_line(page)


def test_a_log_with_no_manifest_line_prints_no_invented_provenance(gold, tmp_path):
    """四格是"这份文件是谁写的"，所以它们只能从文件里来；文件没写的时候这一格必须整段缺席。

    现场把金样本的第一行（开局记录）撕掉，读侧给的是 `meta == {}` 加一句 ⚠「这个文件没有开局
    记录」。页眉同一条既说"没有开局记录"、又印出三个版本号，就是本页自相矛盾——而 `#90` 钉的
    是同一页两行互相打架的那一类。
    """
    res, _, _ = gold
    body = res.path.read_text(encoding="utf-8").splitlines()[1:]
    bare = tmp_path / "no-manifest.jsonl"
    bare.write_text("\n".join(body) + "\n", encoding="utf-8")
    evs, meta, torn = EventLog.read_split(bare)
    assert meta == {}, "读侧换了口径：这一具就不再是无开局记录那一形，得另找文件"
    line = _meta_line(render_html.render(evs, meta, torn=torn))
    assert "没有开局记录" in line, line
    assert "None" not in line and "板" not in line, line


def test_a_refusal_in_a_private_channel_is_counted_by_both_pages_but_shown_by_one(gold):
    """The header counts it in both modes; only the god page gets to show the line.

    The split is the whole point of the fix, so both halves are asserted here: an earlier version
    of this case only pinned the header and would also have passed if the audience page started
    printing the wolf chat.

    The private turn is given **two** rejected outputs while the fixture's public speech carries
    one, because 页眉 says 次: a page counting rejected *outputs* says 3 and a page counting
    *turns that were refused* says 2, and with one attempt each those two readings coincide.
    That coincidence is how this case first survived its own knife (K4, round 1).
    """
    _, events, meta = gold
    chat = next(e for e in events if e.kind == Kind.WOLF_CHAT)
    assert chat.visibility != "all", "夹具里的狼聊必须是私有的，否则这一条没在钉任何东西"
    twice = ({"violations": ["wrong_cite"]}, {"violations": ["not_legitimate"]})
    injected = [dataclasses.replace(e, attempts=twice) if e is chat else e for e in events]
    docs = {god: render_html.render(injected, meta, god=god) for god in (False, True)}
    for doc in docs.values():
        assert "闸门拒绝3次" in doc, _meta_line(doc)
    # 1 speech was refused once and is public; the wolf chat was refused twice and is not.
    assert docs[False].count("〔拒绝1次〕") == 1, "观众页把私有频道那一行显示了进去"
    assert docs[False].count("〔拒绝2次〕") == 0, "私有频道的标记漏进了观众页"
    assert docs[True].count("〔拒绝1次〕") == 1 and docs[True].count("〔拒绝2次〕") == 1, (
        "上帝页两条都要在：〔拒绝N次〕数的是这一轮的被拒次数，不是这一轮被拒了几回")


def test_the_audience_footer_owns_the_which_ones_shown_claim(pair):
    """分工：页眉回答"有多少"（关于文件，两个视图同一个数），页脚回答"这一页给你看了哪些"。

    The header deliberately does not repeat the second half — one predicate, one source — so the
    page is only honest while that sentence exists, and nothing read it before this case. Remove
    it and a page counting 22 private events starts implying it contains them.
    """
    spectator, god = pair
    assert spectator.count("观众模式只含公开事件") == 1, spectator[-400:]
    assert "私有频道" in spectator and "不在本文件中" in spectator
    assert "观众模式只含公开事件" not in god, "上帝页上这句话是假话"


# ---------------------------------------------- 类名词表：CSS、产物、断言三向对账
# The three sets below have to agree in both directions, and each direction catches a
# different kind of lie. A rule with no emission is dead CSS; a class with neither rule nor
# assertion is decoration nobody can notice; and an assertion naming a class the renderer
# never emits launders a dead name into "有读者" — which would silently weaken the second one.
_QUOTE = chr(34)      # 两个都拼装：本文件的源码里因此没有连续的 class="<…>" 那一形
_CLASS_PREFIX = "class" + chr(61)
CLASS_ATTR = re.compile(_CLASS_PREFIX + _QUOTE + "([^" + _QUOTE + "]*)" + _QUOTE)
_WORD = re.compile(r"[A-Za-z][\w-]*")
_STYLE_BLOCK = re.compile(r"<style>(.*?)</style>", re.S)
_RULE_HEAD = re.compile(r"([^{}]+)\{[^{}]*\}")
_IN_SELECTOR = re.compile(r"\.([A-Za-z][\w-]*)")


def _class_names(markup: str) -> set[str]:
    """每一个类名。一个属性里两个名字算两个：`class="seat dead"` 的 dead 是单独一格。"""
    return {tok for attr in CLASS_ATTR.findall(markup) for tok in _WORD.findall(attr)}


def _rule_names(css_body: str) -> set[str]:
    """CSS 点过名的每一个类。按 `{}` 配对取选择器，不按物理行切——真块里 `li.speech{…}` 和
    `li.vote{…}` 挤在同一行上，按行切只会看见每行的前一半（普查第一版就是这么错的）。
    """
    return {m.group(1) for head in _RULE_HEAD.finditer(css_body)
            for m in _IN_SELECTOR.finditer(head.group(1))}


def _style_rule_names() -> set[str]:
    src = Path(render_html.__file__).read_text(encoding="utf-8")
    return _rule_names(_STYLE_BLOCK.search(src).group(1))


def _markup_readers() -> set[str]:
    """测试里写出来的每一个类名字面量——断言读过的类名才算有人读。"""
    out: set[str] = set()
    for f in sorted(Path(__file__).parent.glob("*.py")):
        out |= _class_names(f.read_text(encoding="utf-8"))
    return out


@pytest.fixture(scope="module")
def emitted(pair) -> set[str]:
    """产物里真的出现过的类名。

    样本是金样本的两个视图，不是 `data/` 下的日志——那些文件 gitignored，别的机器上一本都没有。
    现测（04:08:18Z 按本文件的谓词重数）：金样本两视图给 19 个名字，本机 13 本局日志（`data/` 里 16 个
    `.jsonl` 排除 3 本 `*.prompts.jsonl`，那不是局日志）重渲染后给同样 20 个，两边唯一对不上的是
    `abstain`（那一局 0 张弃票，日志里的那几局有），所以手工喂一格 `_matrix` 补上它。
    """
    out: set[str] = set()
    for doc in pair:
        out |= _class_names(doc)
    return out | _class_names(render_html._matrix(
        3, 1, [_ballot(1, 2, 5), _ballot(2, 3, None), _ballot(3, 5, None)]))


def test_no_css_rule_outlives_the_name_it_styles(emitted):
    """CSS 里有一条规则、产物里从来没有那个名字 —— 删掉它一整套测试都不会红，这正是它需要闸门的理由。

    现测唯一一处是 `.tag`：`src/` 和 `scripts/` 里没有一处把 tag 写进 class，所以那条灰字规则从
    写下来那天起就没上色过任何东西。
    """
    dead = _style_rule_names() - emitted
    assert dead == set(), f"这些规则在 CSS 里，产物里从来没出现过：{sorted(dead)}"


def test_every_class_the_renderer_emits_is_styled_or_asserted_on(emitted):
    """反方向：一个类名既没有 CSS 规则、也没有一条测试读它，那它就是纯装饰。

    「留给以后的人上色」这个理由在**这个**产物上不成立：单文件 HTML，不带外部样式表，也没有第二个
    消费者。现测两处这样的一格——两张表头行 `ballot-head`/`tally-head`，边框是 `table` 和 `th,td`
    两条元素规则给的，那个类名本身没人读，所以属性一起删掉。第三处 `last_words` 处置**不同**：它不是
    装饰，而是能说"这一行是临终遗言"的记号，`#209` 给它补了属于自己的颜色规则，它的读者是下面两条：
    一条数真产物上的行，一条钉 `_SHELL` 里那条规则在不在。
    """
    unobserved = emitted - _style_rule_names() - _markup_readers()
    assert unobserved == set(), f"零读者的类名：{sorted(unobserved)}"


def test_no_test_names_a_class_the_renderer_never_emits(emitted):
    """第三条边：断言里写过的类名必须真的在产物里。上面那条把"被测试读过"记成一个读者，
    所以一处过期断言就能给死类名发通行证——这一条把那条边的口径也关住。
    """
    stale = _markup_readers() - emitted
    assert stale == set(), f"测试断言了产物里没有的类名：{sorted(stale)}"


def test_a_last_words_line_carries_a_hook_no_ordinary_speech_has(pair):
    """遗言那一格在 `#209` 之前**没有** CSS 规则，那个 class 属性是产物里唯一能分辨"这一行是临终遗言、
    不是普通发言"的记号——所以它不是装饰，删掉它这一桌的复盘页就把两种话说成同一种话。

    这一条就是上面那条零读者闸门所需的"读者"：它数的是真产物上的行，不是源码里的名字。现测作者局有
    两条 last_words 事件（39、74 号），两个视图里各两行。
    """
    spectator, god = pair
    for doc in (spectator, god):
        found = doc.count('class="last_words"')
        assert found == 2, f"遗言行的记号数不对：{found}（作者局有两条 last_words 事件）"


def test_a_last_words_line_gets_its_own_colour_rule():
    """#209 的定夺：遗言要有自己的颜色。上面那条只证明"产物里有一个能分辨的记号"，而那个记号住在
    DOM 属性里——把复盘页投给人看时，读者看不到属性，只看得到颜色，所以"能分辨"在这一页上其实不成立。
    判据取的是 `_SHELL` 里真有一条以 `last_words` 为选择器的规则这件事，不取任何像素。
    """
    assert "last_words" in _style_rule_names(), sorted(_style_rule_names())


def test_a_kind_colour_bar_rule_is_strong_enough_to_land():
    """一根分类色条的规则必须压得过那条 shorthand，否则它是看不见的墨。

    `ul.chron li{border-left:3px solid transparent}` 的特异度是 (0,1,2)，而 `li.speech{border-left-color:…}`
    只有 (0,1,1)——shorthand 里那个 `transparent` 每次都比分类色赢。2026-10-02 15:51Z 在真页面上量到
    computed `border-left-style:solid`、`border-left-width:3px` 都在，`border-left-color` 却是
    `rgba(0, 0, 0, 0)`，speech／vote／last_words 三处一起：条子的粗细一直在，颜色从来没上过。
    这一条不模拟整个 cascade，只钉这一处形状：给 `li` 设 `border-left-color` 的规则，选择器要写成
    `ul.chron li.<类名>`。反面那格（`border-left-color` 一次都不出现）由上面那句 `assert bars` 兜住，
    它不许这条判据在没有任何对象的情况下绿。
    """
    css = _STYLE_BLOCK.search(Path(render_html.__file__).read_text(encoding="utf-8")).group(1)
    bars = [sel.strip() for sel in re.findall(r"([^{}]*)\{[^{}]*border-left-color[^{}]*\}", css)]
    assert bars, "一条 `border-left-color` 规则都没有：这根条子的判据失去对象了"
    weak = [sel for sel in bars if not sel.startswith("ul.chron li.")]
    assert weak == [], f"这些分类色条会被 `ul.chron li` 的 shorthand 顶掉：{weak}"


def test_the_class_scanners_are_not_blind(emitted):
    """三条对账都靠这两个扫描器，所以单独钉它们的形状——这一条不读真实语料的新东西，
    它不会被产物变好而削弱：它钉的是"检测能力在"。每一行都对应普查真栽过或真险过的一种形状。
    """
    # 一行两条规则是真块的排法（62、63 两行各挤着两条），所以夹具也得按真文件那样带换行：
    # 只给一串不带换行的文本时，"按物理行锚定"那一类写法照样能过，电池第一版的 K4 就是这么落空的。
    assert _rule_names("a{color:red}\nli.speech{color:red}li.vote{color:blue}\nb.death{x:y}\n") \
        == {"speech", "vote", "death"}
    assert _rule_names(".seat.dead{opacity:.45}") == {"seat", "dead"}
    assert _class_names('<li class="seat dead">x</li>') == {"seat", "dead"}
    # 这两格是**从源码 grep 换成读产物**的理由：mind 只从转义引号里出来，
    # dead 是 f-string 拼在属性中间的（`class="seat{" dead...`），两种都会被文本扫描切成半个名字。
    assert "mind" in emitted and "dead" in emitted, sorted(emitted)
    assert len(emitted) >= 18 and len(_style_rule_names()) >= 11 and len(_markup_readers()) >= 6, \
        (sorted(emitted), sorted(_style_rule_names()), sorted(_markup_readers()))
