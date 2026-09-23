"""Reading a log that stopped mid-write.

`run` appends one line per event and closes the file each time, so a process killed while it
was writing leaves a **half-written last line**. That is the ordinary shape of an interrupted
batch — the endpoint has been refusing mid-game, and someone has to open the files afterwards.
Until now only the live viewer tolerated it (`render_live.tail_events` popped trailing lines in
a loop of its own), while every offline reader (`wolf audit`, `replay`, the 复盘 HTML,
`metrics.read_dir`) died with a raw `JSONDecodeError` traceback naming a *column* and no file.

The split this file pins is the one that makes the tolerance safe:

* **one** unparsable line at the very end is a torn tail → drop it, keep the complete prefix, and
  *say so* in whatever the reader prints. Silent is not acceptable, because the last event of a
  finished game is `GAME_OVER`: dropping it turns `wolf_win` into `unfinished`, and a reader who
  cannot see the cut reads a shortened denominator as "this batch never got that far".
* **two** unparsable lines, or any unparsable line with a good line after it, is damage. That is
  not one interrupted write, and a tolerance wide enough to swallow it would be a tolerance wide
  enough to swallow a missing day.
* The dropped bytes are counted, never echoed: the half-written line can be a `wolf_chat`, so
  printing "what couldn't be parsed" would leak private text into a spectator-facing output.
* A JSON object that is not an event at all is a different *kind of file*, and the message says so.
  `--dry-run` leaves its prompt dumps in the same folder as the games; handed one, every reader used
  to die at `d["visibility"]` with a `KeyError` that had already lost the file and the line.
* A file whose `seq` numbering has holes, twins, or a stretch running backwards is **one game whose
  bytes were edited**, and gets a sentence. A file carrying **two** opening records is two games in
  one path — the writer refuses to produce that, and the reader agrees.

The live tail sharing this read is pinned once behaviourally below and once structurally in
`test_wiring.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wolfengine import batch, cli, events, metrics, render_html, render_live
from wolfengine.events import EventLog, Kind, seats

SECRET = "狼队密语：今晚刀7号，别让任何人听见这句话"


def _table_log(path: Path) -> None:
    """A three-event log whose **last** line is a private wolf chat, ending before GAME_OVER.

    Deliberately unfinished: the interesting question is what a reader says when the record that
    would have settled the game is the one that got cut.
    """
    log = EventLog(path, meta={"game_id": "g", "deal_seed": 7, "actor_kinds": ["llm"],
                               "config_hash": "h"})
    log.write_meta()
    log.append(Kind.GAME_START, day=1, phase="night_wolf", seats=[1, 2, 3])
    log.append(Kind.SPEECH, day=1, phase="day_speech", actor=1, text="3号发言太顺了。")
    log.append(Kind.WOLF_CHAT, day=1, phase="night_wolf", visibility=seats(1, 2), actor=1,
               text=SECRET)


def _tear_last_line(path: Path) -> str:
    """Drop the closing brace off the final line in place, returning the bytes left behind.

    Cut *after* the payload text rather than mid-way: a tolerance test that tears before the
    secret would pass "don't echo the tail" for the wrong reason.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    cut = lines[-1][: lines[-1].rindex("}")]
    path.write_text("\n".join(lines[:-1] + [cut]), encoding="utf-8")
    return cut


# ---------------------------------------------------------------------- the tail, tolerated
def test_a_torn_last_line_reads_as_the_complete_prefix(tmp_path):
    """The reader gets the events that *are* there and no exception — this is the call every
    offline command makes, and it used to crash before printing anything."""
    path = tmp_path / "g.jsonl"
    _table_log(path)
    events_, meta = EventLog.read_records(path)
    assert [e.seq for e in events_] == [1, 2, 3]
    assert meta["game_id"] == "g"
    _tear_last_line(path)
    events_, meta = EventLog.read_records(path)
    assert [e.seq for e in events_] == [1, 2], "半行不是事件，但前面的整行一条都不能少"
    assert meta["game_id"] == "g", "manifest 在第一行，撕裂永远碰不到它"


def test_read_split_names_the_lines_it_had_to_drop(tmp_path):
    """`read_records` hides the tail; `read_split` is the leg that can report it. Both have to
    exist — a caller that only wants events must not be forced to invent a third return value."""
    path = tmp_path / "g.jsonl"
    _table_log(path)
    got, meta, torn = EventLog.read_split(path)
    assert ([e.seq for e in got], meta["game_id"], torn) == ([1, 2, 3], "g", [])
    cut = _tear_last_line(path)
    got, _meta, torn = EventLog.read_split(path)
    assert [e.seq for e in got] == [1, 2]
    assert torn == [cut], torn


def test_a_file_cut_before_its_manifest_reads_empty_not_fatal(tmp_path):
    """A handful of bytes is not a game, and it must not be a crash either: `read_dir` over a
    folder of interrupted runs has to walk past it and report it, not die on the first file."""
    path = tmp_path / "g.jsonl"
    path.write_text('{"seq":0,"meta"', encoding="utf-8")
    got, meta, torn = EventLog.read_split(path)
    assert (got, meta) == ([], {})
    assert len(torn) == 1


def test_a_trailing_blank_line_is_not_reported_as_a_cut(tmp_path):
    """`json.loads("")` fails, so without the blank skip an extra newline at the end of a
    hand-copied file becomes "1 行截断" — and a notice that fires on nothing is a notice people
    learn to ignore. The parser already skips blank lines anywhere in the file; the tail may not
    invent a damage category the middle doesn't have."""
    path = tmp_path / "g.jsonl"
    _table_log(path)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    got, _meta, torn = EventLog.read_split(path)
    assert ([e.seq for e in got], torn) == ([1, 2, 3], [])


# --------------------------------------------------------------------- the bound, enforced
def test_only_one_torn_line_is_a_tail_two_is_damage(tmp_path):
    """The tolerance is exactly one line wide, and this is the test that says so.

    Without it `while the last line fails: pop()` walks the whole file backwards, and "the last
    two lines are junk" (a damaged log) becomes "nothing after the first event happened".
    """
    path = tmp_path / "g.jsonl"
    _table_log(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-2] + ['{"seq": 3, "ki', '{"seq": 4, "ki']),
                    encoding="utf-8")
    with pytest.raises(events.LogDamage):
        EventLog.read_split(path)


def test_a_damaged_line_mid_file_says_which_line(tmp_path):
    """`Expecting ':' delimiter: line 1 column 122 (char 121)` names no file and no place in the
    file. A reader holding a 500-line log needs the line number, not the column of one line."""
    path = tmp_path / "g.jsonl"
    _table_log(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[2] = '{"seq": 999, "kind": "spe'
    path.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(events.LogDamage) as e:
        EventLog.read_split(path)
    assert "第 3 行" in str(e.value), str(e.value)


def test_a_line_that_is_json_but_not_a_record_is_damage_too(tmp_path):
    """`123` parses as JSON and then dies inside the parser with an `AttributeError` about `.get`.

    The point of catching damage is to name the place, so a nameless crash on the same line is
    the same defect wearing a different hat.
    """
    path = tmp_path / "g.jsonl"
    _table_log(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = "123"
    path.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(events.LogDamage) as e:
        EventLog.read_split(path)
    assert "第 2 行" in str(e.value), str(e.value)


def test_the_damage_message_names_the_file_being_read(tmp_path):
    """`read_dir` walks a whole folder, and "line 2 is broken" without a filename is a hunt."""
    _table_log(tmp_path / "good.jsonl")
    bad = tmp_path / "bad.jsonl"
    _table_log(bad)
    lines = bad.read_text(encoding="utf-8").splitlines()
    lines[1] = "{} not json"
    bad.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(events.LogDamage) as e:
        metrics.read_dir(tmp_path)
    assert "bad.jsonl" in str(e.value), str(e.value)


# ----------------------------------------------------------- the wrong *kind* of file
DUMP_KEYS = {"seat": 1, "phase": "day_speech", "attempt": 1, "total_tokens": 1489,
             "region_tokens": {"A": 912}, "over_ceiling": False, "shrink": 0,
             "messages": [{"role": "system", "content": "…"}]}


def test_a_line_without_the_event_keys_says_which_ones_are_missing(tmp_path):
    """`--dry-run` leaves `g<seed>.prompts.jsonl` in the very folder holding the games it was
    dumped from, and tab-completion finds it there. Handing one to a log reader used to die at
    `d["visibility"]` with `KeyError: 'visibility'` — true, and useless: no file, no line, no word
    about the file being a different kind of thing.

    The fixture's keys are copied from a real dump (the first line of
    `data/g00000021.prompts.jsonl`). The assertion does not depend on that list still being
    current — any object without the event keys takes this path — so it checks one present key,
    not the whole list.
    """
    path = tmp_path / "g00000021.prompts.jsonl"
    path.write_text(json.dumps(DUMP_KEYS, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(events.LogDamage) as e:
        EventLog.read_split(path)
    msg = str(e.value)
    assert "第 1 行" in msg, msg
    assert "visibility" in msg and "seq" in msg, f"没报缺哪些键：{msg}"
    assert "messages" in msg, f"只报了缺的键，读者认不出这是哪个文件：{msg}"


@pytest.mark.parametrize("which", ["audit", "replay", "export", "watch"])
def test_every_file_reading_command_reports_a_wrong_kind_file_instead_of_crashing(which,
                                                                                  capsys,
                                                                                  tmp_path):
    """#47 put the file name, the line number and the missing keys into the message. What a person
    actually sees at the terminal is still a Python traceback: `main()` is `return ns.func(ns)` with
    no handler, so `LogDamage` — the one *expected* error class in the repo with no handler anywhere
    — escapes and the sentence ends up as the last line under fifteen lines of stack.

    Reproduced off the real artifacts at 2026-09-22T01:29Z: `data/` holds three games and two
    `--dry-run` dumps, and `audit data/g00000021.prompts.jsonl` exits 1 with a traceback. The repo
    already has the other shape for the same kind of mistake — `cmd_run`/`cmd_batch` turn
    `ConfigError`/`BadOverride` into one `配置错误：…` line and rc 2, `cmd_compare` does it for a
    folder with no manifest.

    All four exits are named one at a time, not "a call to the CLI": a handler in `main()` covers
    every command, while a per-command `try` is exactly the second-owner mistake this file exists to
    punish. `export` additionally must not leave an HTML file behind — an artifact written from an
    input nobody can read is worse than the crash it replaced.
    """
    path = tmp_path / "g7.prompts.jsonl"
    path.write_text(json.dumps(DUMP_KEYS, ensure_ascii=False) + "\n", encoding="utf-8")
    argv = {"audit": ["audit", str(path)],
            "replay": ["replay", str(path)],
            "export": ["export", str(path), "-o", str(tmp_path / "out.html")],
            "watch": ["watch", "--once", str(path)]}[which]
    assert cli.main(argv) == 2, f"{which} 没有把这条错误接成一句报告"
    captured = capsys.readouterr()
    err = captured.err
    assert "g7.prompts.jsonl" in err, f"{which} 的报告里没说清楚是哪个文件：{err!r}"
    assert "seq" in err and "visibility" in err, \
        f"{which} 把 #47 那句换成了泛泛一句话：{err!r}"
    assert "Traceback" not in err, f"{which} 印的是默认崩溃：{err[:160]!r}"
    assert not (tmp_path / "out.html").exists(), f"{which} 在读不动的输入上还是落了盘"


def test_exactly_one_place_turns_log_damage_into_an_exit_code():
    """上一条四个参数全绿，说明 `main()` 那一个 handler 覆盖了每个读文件的命令；它同时也说明
    "每人自己接一遍"这条变体是行为等价的、任何用例都杀不动——所以这一句得钉成结构断言。

    这是本文件一直在付的那种债的反面：`#46` 那轮四处各写一份弹循环，正是"边界处理散到每个出口"
    长出来的形状。`events.py:325` 那一处不算，它是把消息包上文件名的那只手，不在终端这一侧。
    """
    body = Path("src/wolfengine/cli.py").read_text(encoding="utf-8")
    assert body.count("except LogDamage") == 1, (
        "cli.py 里接住 `LogDamage` 的地方不止一处：某个命令自己接了一遍，"
        "下一次改报错措辞就只改一处")
    main_src = body.split("def main(", 1)[1]
    assert "except LogDamage" in main_src.split("\n\n", 1)[0], (
        "唯一那一处不在 `main()` 里，那就是某个出口自己的私货")


def _argv(which: str, path: Path, out: Path) -> list[str]:
    return {"audit": ["audit", str(path)],
            "replay": ["replay", str(path)],
            "export": ["export", str(path), "-o", str(out)],
            "watch": ["watch", "--once", str(path)]}[which]


@pytest.mark.parametrize("which", ["audit", "replay", "export", "watch"])
def test_a_path_that_is_not_there_is_a_wrong_command_not_a_refusal(which, capsys, tmp_path):
    """`#50` 把 `LogDamage` 接成了一行报告，接的却是"文件在、读不动"那一半。文件名打错一个字符走的
    是另一条异常（`FileNotFoundError`），实测（2026-09-22T01:48:52Z）四个出口一律 traceback + rc 1。

    rc 1 在本仓库不是空着的：`cli.py` 的 docstring 写着 0=有结论、1=引擎拒绝、2=命令本身不对。于是
    "你把路径打错了"和"这批数据不可比"在脚本眼里同一个码，而后者是要人去查 manifest 的。
    """
    missing = tmp_path / "g00000099.jsonl"
    assert cli.main(_argv(which, missing, tmp_path / "out.html")) == 2, f"{which} 把打错的路径记成了拒绝"
    err = capsys.readouterr().err
    assert "g00000099.jsonl" in err, f"{which} 没说是哪个路径：{err!r}"
    assert "Traceback" not in err, f"{which} 印的是默认崩溃：{err[:160]!r}"


@pytest.mark.parametrize("which", ["audit", "replay", "export", "watch"])
def test_a_directory_handed_to_a_reader_says_so_in_one_line(which, capsys, tmp_path):
    """同一个洞的第二种形状：`wolf audit data/`（少打了个文件名）抛 `IsADirectoryError`，既不是
    `FileNotFoundError` 也不是 `LogDamage`。按类名逐个列举的 handler 会放过它——所以这一条单独存在，
    它钉的是"OS 层面的读写失败一律走同一句话"，不是"这两个 errno 都修了"。
    """
    folder = tmp_path / "logs"
    folder.mkdir()
    assert cli.main(_argv(which, folder, tmp_path / "out.html")) == 2, f"{which} 把目录记成了拒绝"
    err = capsys.readouterr().err
    assert "logs" in err, f"{which} 没说是哪个路径：{err!r}"
    assert "Traceback" not in err, f"{which} 印的是默认崩溃：{err[:160]!r}"


def test_export_names_the_path_it_could_not_write(tmp_path, capsys):
    """写侧也在同一条腿上，而它更容易被漏掉：读得到了、算得出来，最后一步 `open(out, "w")` 砸了。

    把 `-o` 指向一个目录（`export 局.jsonl -o 结果目录`）在真终端里是 `IsADirectoryError` + rc 1。
    报错必须说出**输出**那个路径——只说"出错了"等于让人重猜一遍自己敲了什么。
    """
    log = _clean_log(tmp_path / "g7.jsonl")
    out_dir = tmp_path / "results"
    out_dir.mkdir()
    assert cli.main(["export", str(log), "-o", str(out_dir)]) == 2
    err = capsys.readouterr().err
    assert "results" in err, f"报告里没提写不下去的那个路径：{err!r}"
    assert "Traceback" not in err, f"印的是默认崩溃：{err[:160]!r}"


def test_exactly_one_place_turns_an_unusable_path_into_an_exit_code():
    """为什么这条也要钉成结构断言，`test_exactly_one_place_turns_log_damage_into_an_exit_code` 说过一遍：
    每个出口自己接一遍在行为上和 `main()` 接一次无法区分，九条参数全绿也分辨不出谁在兜底。
    """
    body = Path("src/wolfengine/cli.py").read_text(encoding="utf-8")
    assert body.count("except OSError") == 1, (
        "cli.py 里接住 `OSError` 的地方不止一处：路径失败这句报告会长成四个版本")
    assert "except OSError" in body.split("def main(", 1)[1].split("\n\n", 1)[0], (
        "唯一那一处不在 `main()` 里")


def test_a_renamed_dump_stops_the_batch_reader_naming_the_folder_entry(tmp_path):
    """The prose boundary this file's sibling claim draws — "skip by *name*, and a renamed dump is
    caught by the content rule instead" — was asserted for `watch` and `audit` but not for the
    reader that does the skipping by name.

    `batch.read_arm` walks a folder and drops `*.prompts.jsonl` before parsing. That skip is only
    the quiet half of the defence: change the name and the same object has to come back as a
    `LogDamage` naming the file, or a batch loader would be one `mv` away from treating a dump as
    a trajectory.
    """
    (tmp_path / "g7.jsonl").write_text(json.dumps(DUMP_KEYS, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    with pytest.raises(events.LogDamage) as e:
        batch.read_arm(tmp_path)
    assert "g7.jsonl" in str(e.value), str(e.value)


def test_each_key_the_loader_subscripts_has_a_line_that_demands_it(tmp_path):
    """Every element of `EVENT_KEYS` is one `d["…"]` subscript below, so dropping one from the guard
    has to bring that `KeyError` back — for that key, and for no other.

    The list here is spelled out rather than read from `events`: iterate the production list and a
    guard that lost `visibility` silently stops testing `visibility`. The last assertion is the
    completeness check for the other direction.
    """
    src = tmp_path / "table.jsonl"
    _table_log(src)
    lines = src.read_text(encoding="utf-8").splitlines()
    for drop in ("seq", "kind", "day", "phase", "visibility"):
        rec = json.loads(lines[1])
        assert drop in rec, f"夹具自己就不含 {drop}，这一轮等于什么都没测"
        del rec[drop]
        path = tmp_path / f"g-{drop}.jsonl"
        path.write_text("\n".join([lines[0], json.dumps(rec, ensure_ascii=False)]) + "\n",
                        encoding="utf-8")
        with pytest.raises(events.LogDamage) as e:
            EventLog.read_split(path)
        assert drop in str(e.value), f"缺 {drop} 的那一句里没有 {drop}：{e.value}"
        assert "第 2 行" in str(e.value), str(e.value)
    assert set(events.EVENT_KEYS) == {"seq", "kind", "day", "phase", "visibility"}, \
        "键表和这条用例脱钩了：有一处下标不在被点名的名单里"


# ------------------------------------------------------------------ the tail, made visible
def _audit(path, capsys) -> dict:
    assert cli.main(["audit", str(path)]) == 0
    return json.loads(capsys.readouterr().out)


def test_audit_reports_a_torn_tail_without_printing_its_text(tmp_path, capsys):
    """`audit` is the machine-readable face of one log. It has to say the file was cut — and say
    it as a count, because the cut line is a wolf chat and its bytes are not for the audience."""
    path = tmp_path / "g.jsonl"
    _table_log(path)
    cut = _tear_last_line(path)
    stats = _audit(path, capsys)
    assert SECRET in cut, "样本要真的把密文留在被丢掉的字节里，否则下面那条断言是空转的"
    assert stats["torn_tail"] == {"lines": 1, "chars": len(cut)}, stats["torn_tail"]
    out = capsys.readouterr().out
    assert SECRET not in out and cut not in out, "把丢掉的字节印出来就是漏题"
    assert stats["terminal"] == "unfinished", "少了 GAME_OVER 就是没打完，不许猜一个赢家"


def test_a_clean_log_reports_no_torn_tail(tmp_path, capsys):
    """反向对照：`torn_tail` 若是永远亮着的灯，上面那条断言就什么都没钉。"""
    path = tmp_path / "g.jsonl"
    _table_log(path)
    assert not EventLog.read_split(path)[2]
    assert _audit(path, capsys)["torn_tail"] is None


def test_the_truncation_extent_is_counted_once_for_every_reader(tmp_path, capsys):
    """"末 N 行、M 字节"这两个数今天被数了两遍：`torn_notice` 的句子自己数，`audit` 的 dict 在
    `cli.py` 里再数一遍。批次侧要做第三个读者（#56），而"两份看起来一样的算术"正是这一路一直在
    拆的东西（#27/#28 的区域预算、#48 的 manifest 判据）。先收成一只手，句子和 dict 都从它取数。

    下面那条 `audit` 的腿比的不是"数对不对"——那个
    `test_audit_reports_a_torn_tail_without_printing_its_text` 已经钉过了——它比的是**句子和 dict
    走的是不是同一次算术**：一句印在终端上、一个藏在 json 里，今天没有一条断言读过它们俩对不对得上。
    """
    assert events.torn_extent([]) == {"lines": 0, "chars": 0}
    assert events.torn_extent(["abc", "d"]) == {"lines": 2, "chars": 4}

    path = tmp_path / "g.jsonl"
    _table_log(path)
    cut = _tear_last_line(path)
    extent = events.torn_extent(EventLog.read_split(path)[2])
    assert extent == {"lines": 1, "chars": len(cut)}, extent
    assert f"末 {extent['lines']} 行、{extent['chars']} 字节" in events.torn_notice([cut])
    assert _audit(path, capsys)["torn_tail"] == extent, (
        "audit 印的那两个数和句子背后的那两个数不是同一次算术")


def test_a_chronicle_handed_to_a_human_says_it_was_cut(tmp_path):
    """The transcript is the artifact a colleague reads. Its last line must not just stop: a
    spectator cannot tell "the game ended here" apart from "the file ends here"."""
    path = tmp_path / "g.jsonl"
    _table_log(path)
    _tear_last_line(path)
    lines = cli.render_chronicle_file(path)
    assert "截断" in lines[-1] and "1 行" in lines[-1], lines[-1]
    assert SECRET not in "\n".join(lines)
    seat_view = cli.render_chronicle_file(path, as_seat=1)
    assert "截断" in seat_view[-1], "标记不能只挂在上帝视角上，座位视角同样是读完就下结论的那一个"


def _clean_log(path: Path) -> Path:
    _table_log(path)
    return path


def _html_report(path: Path, out: Path) -> str:
    render_html.write_report(path, out)
    return out.read_text(encoding="utf-8")


def test_the_html_page_says_the_file_was_cut(tmp_path):
    """The 复盘 HTML is the artifact that leaves the building, and it went through the same read.

    Loud-then-silent is the bad trade: before the tolerance existed this page crashed on a torn
    file, and after a naive tolerance it would render a chronicle that simply stops — which reads
    as a game that stopped there. So the page carries the same notice the terminal does, as a
    count, with none of the dropped bytes in it.
    """
    path = tmp_path / "g.jsonl"
    _table_log(path)
    _tear_last_line(path)
    html = _html_report(path, tmp_path / "cut.html")
    assert "截断" in html and "1 行" in html, html[-400:]
    assert SECRET not in html
    # 同一处截断在两个产物里必须是同一句话：各写一句就会有一天两边数字不一样，而没人会同时打开两边。
    # containment 不够——D50 让 cli 另写一句更短的话，它正好是另一句的前缀，`in` 照样绿。
    # 所以两边都钉：这句话在页里，且页里那句和它一字不差。
    notice = cli.render_chronicle_file(path)[-1].strip("〔〕")
    assert notice in html, "两个渲染器对同一处截断写了两句话"
    in_page = re.search(r"⚠ ([^<]*)", html)
    assert in_page and in_page.group(1) == notice, (
        f"页眉那句和转录那句不是一句话：{in_page and in_page.group(1)!r} != {notice!r}")
    clean = _html_report(_clean_log(tmp_path / "clean.jsonl"), tmp_path / "clean.html")
    assert "截断" not in clean, "通知永远亮着就等于没有通知"


def test_the_live_tail_drops_no_more_than_the_offline_reader_does(tmp_path):
    """One rule, two readers. The live viewer used to own its own pop-loop, which was *wider* than
    anything offline: it walked past several junk lines while `audit` would have refused the file.
    A frame drawn from a log nobody else can read is the split-brain this repo keeps paying for."""
    path = tmp_path / "g.jsonl"
    _table_log(path)
    assert [e.seq for e in render_live.tail_events(path)] == [1, 2, 3]
    _tear_last_line(path)
    assert [e.seq for e in render_live.tail_events(path)] == [1, 2]
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines + ['{"seq": 9, "ki']), encoding="utf-8")
    with pytest.raises(events.LogDamage):
        render_live.tail_events(path)


# ------------------------------------------------------------------ 没有 manifest 的三种出口
NOTICE = "没有开局记录"


def test_replay_on_a_file_with_no_records_says_why_it_has_nothing_to_show(capsys, tmp_path):
    """`audit 空文件` 老实印出 `events: 0`，`replay 空文件` 却一个字都不印、还返回 0——三个给人看的
    出口里，只有机器那个说实话。实测（2026-09-22T02:06:44Z）：空文件、`/dev/null` 都是静默 rc 0。

    对着终端的人分不开"这一局没有可看的"和"我给错了东西"，而本文件前几节立的规矩正是"沉默是对它的
    错误回答"。`read_split` 早就把这种文件判成"读得动、事件 0 行"（`test_a_file_cut_before_its_manifest_
    reads_empty_not_fatal`），缺的只是把这件事说给人听的那一句。
    """
    path = tmp_path / "g9.jsonl"
    path.write_text("", encoding="utf-8")
    assert cli.main(["replay", str(path)]) == 0, "读得动的空文件不是失败，别改退出码"
    out = capsys.readouterr().out
    assert NOTICE in out, f"转录一个字都没印：{out!r}"
    assert "哪一局" in out, f"通知没说清缺的是哪一格：{out!r}"


def test_a_named_game_still_prints_no_such_notice(tmp_path, capsys):
    """反向：通知永远亮着就等于没有通知（`test_the_html_page_says_the_file_was_cut` 的同一条规矩）。
    """
    path = _clean_log(tmp_path / "g8.jsonl")
    assert cli.main(["replay", str(path)]) == 0
    assert NOTICE not in capsys.readouterr().out


def test_the_exported_page_says_the_same_about_a_file_with_no_manifest(tmp_path):
    """复盘 HTML 是要发出去的东西：拿到一份空文件导出的页面，收的人也应该知道没有东西可看。"""
    empty = tmp_path / "e.jsonl"
    empty.write_text("", encoding="utf-8")
    page = _html_report(empty, tmp_path / "e.html")
    assert NOTICE in page, "页面把空文件当成了一局正常结束的牌局"


def test_the_live_header_fills_the_name_slot_instead_of_printing_a_blank():
    """`watch --once 空文件` 的页眉是 `狼人杀直播  · 第1天`——中间那格空着。#48 刚记过这件事的形状：
    "少的那一格恰好是'这一屏说的是哪一局'"。那次的原因是页眉自己解析第一行，这次是根本没有那一行。
    """
    frame = render_live.frame_text([], {}, god=False)
    assert NOTICE in frame, f"页眉空着而没有任何一句话解释：{frame[:200]!r}"
    assert "狼人杀直播  ·" not in frame, f"名字那一格还是个空洞：{frame[:120]!r}"


def test_the_no_manifest_sentence_has_one_owner():
    """三个出口同一句话，判据住在 `events.py`，和 `torn_notice` 一个待遇：两处各写一遍就会长成两种
    措辞，而"这一屏说的是哪一局"这种话一旦不一致，读者就得分辨哪个出口是真的。
    """
    hits = sorted(p.name for p in Path("src").rglob("*.py")
                  if NOTICE in p.read_text(encoding="utf-8"))
    assert hits == ["events.py"], f"这句话被抄到了别处：{hits}"
    for mod in ("cli.py", "render_html.py", "render_live.py"):
        src = (Path("src/wolfengine") / mod).read_text(encoding="utf-8")
        assert "meta_notice" in src, f"{mod} 没走那只手，它印的是自己那份措辞"


# ------------------------------------------------------------------ 只有开局记录的那一种空
BLANK = "只有开局记录"


def _stub_log(path: Path, *, with_events: int = 0) -> Path:
    """A file that reached the manifest and no further — the shape a run leaves behind when the
    endpoint dies before the first event.

    Not a hand-edited fantasy: `write_meta()` lands on disk at the start of a game, so a killed
    run leaves exactly these bytes. `with_events=1` is the same helper one step further along,
    which is what a negative control needs to differ from the tested case by one line.
    """
    log = EventLog(path, meta={"game_id": "g-stub", "deal_seed": 7,
                               "actor_kinds": ["llm"], "config_hash": "h"})
    log.write_meta()
    for i in range(with_events):
        log.append(Kind.SPEECH, day=1, phase="day_speech", actor=1, text=f"{i}号发言。")
    return path


def test_a_file_that_only_reached_the_opening_record_says_so(tmp_path, capsys):
    """实测（2026-09-22T02:51:43Z）：`replay` 对着只有 manifest 的文件印出**一个空行**、退出码 0，
    页面写"第0天结束 · 未结束"。`#52` 那句在这里不会说话——`game_id` 明明在，缺的是后面的每一条。

    这一格和上一格是同一类错的两个原因：读得动，但没得看，而对着终端的人分不开"这一局没记下来"和
    "我看的东西不对"。退出码仍然不是 2：命令没错，文件也没坏，这就是这份文件的结论。
    """
    path = _stub_log(tmp_path / "stub.jsonl")
    assert cli.main(["replay", str(path)]) == 0
    printed = capsys.readouterr().out
    assert BLANK in printed, f"只有开局记录的文件印出来的是：{printed!r}"
    assert "一条事件都没有" in printed, f"没说清缺的是什么：{printed!r}"
    assert NOTICE not in printed, "开局记录明明在，不能同时说它没有"


def test_an_empty_file_gets_one_sentence_not_two(tmp_path, capsys):
    """两句"没得看"各管一种文件，不能同时亮：同时亮就等于读者要先自己判断哪一句是真的。

    现在（修前）它就是绿的，因为第二句还不存在——它钉的是"加了第二句之后不许回头把第一句也带上"，
    反向那一半由上面那条的最后一行钉住。
    """
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert cli.main(["replay", str(path)]) == 0
    printed = capsys.readouterr().out
    assert NOTICE in printed and BLANK not in printed, printed


def test_a_game_with_one_event_prints_neither_notice(tmp_path, capsys):
    """反向对照：有事件可看的时候两句都不许出现（`with_events=1` 与上面那条只差一行）。"""
    path = _stub_log(tmp_path / "one.jsonl", with_events=1)
    assert cli.main(["replay", str(path)]) == 0
    printed = capsys.readouterr().out
    assert BLANK not in printed and NOTICE not in printed, printed


def test_a_frame_with_nothing_visible_does_not_claim_the_file_is_empty(tmp_path):
    """这一句读的是**文件里**的事件数，不是这一屏被允许看的事件数。

    一局只留下一条狼队密语的日志，观众屏是空的——可"这一局只有开局记录"对它是一句假话：文件里有
    事件，只是不许给这个视角看。两种空是两句话，混一次就少一句；`draw` 里那句注释（`events`, not
    `evs`）如果没有断言读它，就是一条假装存在的分支。
    """
    path = tmp_path / "priv.jsonl"
    log = EventLog(path, meta={"game_id": "g-priv", "deal_seed": 7,
                               "actor_kinds": ["llm"], "config_hash": "h"})
    log.write_meta()
    log.append(Kind.WOLF_CHAT, day=1, phase="night_wolf", visibility=seats(1, 2), actor=1,
               text=SECRET)
    evs, meta, _ = EventLog.read_split(path)
    assert evs and meta.get("game_id"), "夹具坏了：这条日志既不是空的也没有开局记录"
    spectator = render_live.frame_text(evs, meta, god=False)
    assert BLANK not in spectator, "观众屏空白不等于这局没记下来"
    assert SECRET not in spectator, "顺手钉住：这一屏仍然不许漏私有文本"
    assert BLANK not in render_live.frame_text(evs, meta, god=True)


def test_the_exported_page_says_the_game_recorded_nothing(tmp_path):
    """要发出去的东西更不能拿"第0天结束 · 未结束"糊过去：那句读起来像一局打完了、平局。"""
    page = _html_report(_stub_log(tmp_path / "s.jsonl"), tmp_path / "s.html")
    assert BLANK in page, "页面把一条事件都没有的文件当成了一局正常结束的牌局"


def test_the_live_frame_says_the_game_recorded_nothing():
    """直播页眉有名字（`g-stub`），所以那一格不是空的——空的是整块板面，而没人解释。"""
    frame = render_live.frame_text([], {"game_id": "g-stub"}, god=False)
    assert BLANK in frame, f"一屏空白而没有任何一句话解释：{frame[:200]!r}"


def test_the_recorded_nothing_sentence_has_one_owner():
    """和前两句同一个待遇：一只手写，三个出口调。"""
    hits = sorted(p.name for p in Path("src").rglob("*.py")
                  if BLANK in p.read_text(encoding="utf-8"))
    assert hits == ["events.py"], f"这句话被抄到了别处：{hits}"
    for mod in ("cli.py", "render_html.py", "render_live.py"):
        src = (Path("src/wolfengine") / mod).read_text(encoding="utf-8")
        assert "empty_notice" in src, f"{mod} 没走那只手"


# ------------------------------------------------- 编号破损：这份文件到底是不是一局
BROKEN = "这份日志的编号不是连续递增的"
TWO_GAMES = "两局"


def _lines_of(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _edited_log(tmp_path: Path, order: list[int]) -> Path:
    """A written three-event log, re-saved with its lines reordered or dropped by hand.

    Line 0 is the manifest, lines 1..3 are seq 1..3. Not a fantasy shape: a `sed -i` on a copy, a
    truncated middle, a partial restore. The writer cannot produce these (`_next_seq` lives in
    memory, `write_meta` refuses a non-empty file), which is precisely why nothing downstream was
    watching for them.
    """
    path = tmp_path / "edited.jsonl"
    _table_log(path)
    lines = _lines_of(path)
    path.write_text("".join(lines[i] + "\n" for i in order), encoding="utf-8")
    return path


def _cat_of_two_games(tmp_path: Path) -> Path:
    """`cat g.jsonl h.jsonl > both.jsonl` — the second file's manifest is line 5."""
    first = tmp_path / "a.jsonl"
    _table_log(first)
    second = tmp_path / "b.jsonl"
    log = EventLog(second, meta={"game_id": "g-two", "deal_seed": 8,
                                 "actor_kinds": ["llm"], "config_hash": "h2"})
    log.write_meta()
    log.append(Kind.GAME_START, day=1, phase="night_wolf", seats=[4, 5, 6])
    both = tmp_path / "both.jsonl"
    both.write_text("".join(l + "\n" for l in _lines_of(first) + _lines_of(second)),
                    encoding="utf-8")
    return both


@pytest.mark.parametrize("order,words", [
    ([0, 1, 3], "缺号 1 处"),
    ([0, 1, 2, 2, 3], "重号 1 个"),
    ([0, 3, 2, 1], "顺序倒挂 2 处"),
])
def test_a_log_whose_numbering_was_broken_by_hand_says_so(tmp_path, capsys, order, words):
    """实测（2026-09-22T03:20:16Z，/tmp/probe_seq.py）：缺号、重号、倒序三种文件在 `replay` 上
    一个字都不多说，`audit` 也没有任何编号读数——只有 `kinds` 那一格偶尔露出马脚。

    对着终端的人看见 `[e1] [e3] [e4]` 会以为这一局就没有 e2；看见 `[e2] [e2]` 分不出哪个引用是对的，
    而本项目的引用记法就是 `eNNN`。退出码仍然不是 2：每一行都读得动，缺的是一句说明。
    """
    path = _edited_log(tmp_path, order)
    assert cli.main(["replay", str(path)]) == 0
    printed = capsys.readouterr().out
    assert BROKEN in printed, f"编号破损的文件印出来的是：{printed!r}"
    # 整句相等，不是"含这段"：只钉片段的话，多报一项、少报一项都照样绿，那句话的分量就没了。
    assert cli.render_chronicle_file(path)[-1] == f"〔{BROKEN}：{words}〕"


def test_a_clean_log_prints_no_numbering_sentence(tmp_path, capsys):
    """反向对照：句子永远亮着就等于没有句子，上面三条断言也就都是空的。"""
    path = tmp_path / "clean.jsonl"
    _table_log(path)
    assert cli.main(["replay", str(path)]) == 0
    printed = capsys.readouterr().out
    assert BROKEN not in printed and "缺号" not in printed, printed


def test_an_empty_event_list_has_no_numbering_to_break():
    """`docs/views.md` 说直播那一屏的两句"构造上不会同时亮"，靠的就是这一格：`empty_notice` 要
    `events` 为空，而 `seq_damage` 对空列表三个计数全为 0——所以同一份 `events` 不可能同时喂出两句。

    这句话住在 `seq_damage` 里那个 `if seqs else 0`：没有它，`min([])` 直接抛 `ValueError`，
    而在写这条用例之前，没有任何一条断言把空列表喂给过 `seq_damage`。
    """
    assert events.seq_damage([]) == {"gaps": 0, "duplicates": 0, "out_of_order": 0}
    assert events.seq_notice([]) == ""


def test_audit_counts_the_numbering_damage_it_is_about_to_describe(tmp_path, capsys):
    """机器那一面也得说：`audit` 现在只印 `events: 3`，把"3 条里少了一条"读成"这局就只有 3 条"。

    三个计数由同一只手算，`replay` 那句就是它的格式化结果——两处各写各的，迟早一边说缺 1 处、
    另一边说缺 2 处。
    """
    stats = _audit(_edited_log(tmp_path, [0, 1, 3]), capsys)
    assert stats["seq_damage"] == {"gaps": 1, "duplicates": 0, "out_of_order": 0}, stats
    clean = _audit(_clean_log(tmp_path / "clean.jsonl"), capsys)
    assert clean["seq_damage"] == {"gaps": 0, "duplicates": 0, "out_of_order": 0}, clean


def test_the_exported_page_and_the_transcript_say_the_same_about_broken_numbering(tmp_path):
    """要发出去的复盘页不能只写"第1天结束"：编号破损同样进页眉，且必须和转录一字不差。"""
    path = _edited_log(tmp_path, [0, 3, 2, 1])
    page = _html_report(path, tmp_path / "broken.html")
    notice = cli.render_chronicle_file(path)[-1].strip("〔〕")
    assert BROKEN in notice and "顺序倒挂" in notice, notice
    assert notice in page, "两个渲染器对同一份编号写了两句话"
    in_page = re.search(r"⚠ ([^<]*)", page)
    assert in_page and in_page.group(1) == notice, in_page and in_page.group(1)


def test_the_live_frame_says_the_numbering_is_broken(tmp_path):
    """直播那一屏读的是同一个文件、同一只手算出来的数，不该是第三个沉默的出口。"""
    path = _edited_log(tmp_path, [0, 1, 2, 2, 3])
    evs, meta, _ = EventLog.read_split(path)
    assert "重号" in render_live.frame_text(evs, meta, god=True)


def test_the_numbering_sentence_and_the_cut_sentence_both_appear(tmp_path, capsys):
    """编号那句管的是"这不是一局写出来的"，和前三句（都是"没得看"）说的不是同一个毛病，所以它可以
    和它们同时亮——README 与 `docs/views.md` 都写了这句话，写出来就得有断言读它。

    顺序也是内容：`render_html` 里那句注释说"页面 ⚠ 的顺序就是转录追加的顺序"，两处的顺序被打乱时
    页面仍然带着同一句话，只有**同时**出现两句的输入能读出区别（2026-09-22T03:48:28Z 实测：
    一份既有缺号、末行又撕裂的文件，转录末尾先是编号那句、再是截断那句，`audit` 两格各报各的数）。
    """
    path = tmp_path / "both.jsonl"
    _table_log(path)                       # manifest + seq 1,2,3，末行是私有狼聊
    lines = _lines_of(path)
    kept = "".join(l + "\n" for l in lines[:2] + lines[3:])
    path.write_text(kept + '{"seq": 4, "kind": "vote", "da', encoding="utf-8")
    assert cli.main(["replay", str(path)]) == 0
    tail = capsys.readouterr().out.splitlines()[-2:]
    assert tail[0] == f"〔{BROKEN}：缺号 1 处〕", tail
    assert "截断" in tail[1], tail
    printed = "\n".join(tail)
    assert NOTICE not in printed and BLANK not in printed, "有事件可看时两句『没得看』都不许出现"
    page = _html_report(path, tmp_path / "both.html")
    assert f"⚠ {tail[0].strip('〔〕')} · ⚠ {tail[1].strip('〔〕')}" in page, \
        "页面页眉里两个 ⚠ 的顺序和转录追加的顺序不一样，或者两句里没有那一句"


def test_a_cut_file_that_never_got_past_the_opening_record_says_both(tmp_path, capsys):
    """"没得看"的两句里，只有前两句互斥（认不出局号 / 这局没记下来）；截断那句讲的是**文件末尾**，
    它可以叠在任何一句上。`docs/views.md` 早期把三句一起写成"不会同时亮"，那是超出断言的话——
    2026-09-22T04:00:41Z 实测的就是这一格：manifest + 一条写了一半的行，转录先说"这一局只有开局
    记录"、再说"日志在这里截断"，页面页眉里两个 ⚠ 同序，退出码 0。

    两句都在是对的：文件里确实一条事件都没读成，而**为什么**没有（末尾被砍）是另一件要说的事情。
    编号那句在这里不说话——一条完整事件都没有，就无所谓缺号。
    """
    path = _stub_log(tmp_path / "cut_empty.jsonl")   # 开局记录 + 后面一条完整事件都没有
    path.write_text(path.read_text(encoding="utf-8") + '{"seq": 1, "kind": "speech", "da',
                    encoding="utf-8")
    assert cli.main(["replay", str(path)]) == 0
    tail = capsys.readouterr().out.splitlines()[-2:]
    assert BLANK in tail[0] and NOTICE not in tail[0], tail
    assert "截断" in tail[1], tail
    assert BROKEN not in "\n".join(tail), "一条事件都没有的文件，编号那句没有可报的"
    page = _html_report(path, tmp_path / "cut_empty.html")
    assert f"⚠ {tail[0].strip('〔〕')} · ⚠ {tail[1].strip('〔〕')}" in page, \
        "页面页眉里两个 ⚠ 的顺序和转录追加的顺序不一样"


def test_a_file_with_two_opening_records_is_not_a_game(tmp_path):
    """`write_meta` 早就对第二次调用说不，理由写在它自己的 docstring 里：两局共享一个文件 id 会把
    两套编号不可逆地交错。读取侧今天同意同一个判断——它此前不是不同意，而是**没看见**：后一条
    manifest 直接覆盖前一条。

    这条不是洁癖，是实测：`cat` 两局日志（`/tmp/probe_seq_F.jsonl`，2026-09-22T03:20:16Z）之后，
    导出页眉只报 `g-two`，而文件前两条事件属于 `g-probe`——页面对"这是哪一局"撒了谎。
    """
    both = _cat_of_two_games(tmp_path)
    with pytest.raises(events.LogDamage) as e:
        EventLog.read_split(both)
    assert "第 5 行" in str(e.value), str(e.value)
    assert TWO_GAMES in str(e.value), f"没说清这是两局拼的：{e.value}"


def test_a_file_of_two_games_refuses_every_command_that_reads_it(tmp_path, capsys):
    """Refusal, not a notice: there is no single game in this file to show, and picking the last
    manifest's name for it is a false claim rather than a missing one."""
    both = _cat_of_two_games(tmp_path)
    out = tmp_path / "both.html"
    assert cli.main(["export", str(both), "-o", str(out)]) == 2
    assert not out.exists(), "拒绝之后还在原子上留一份页眉写着第二局局号的文件"
    err = capsys.readouterr().err
    assert "日志读不下去" in err and "Traceback" not in err, f"报告里出现了 traceback：{err!r}"


def test_the_broken_numbering_sentence_has_one_owner():
    """和前三句同一个待遇：一只手写计数，三个出口和 audit 各自去说。"""
    hits = sorted(p.name for p in Path("src").rglob("*.py")
                  if BROKEN in p.read_text(encoding="utf-8"))
    assert hits == ["events.py"], f"这句话被抄到了别处：{hits}"
    for mod in ("cli.py", "render_html.py", "render_live.py"):
        src = (Path("src/wolfengine") / mod).read_text(encoding="utf-8")
        assert "seq_notice" in src, f"{mod} 没走那只手"
    assert "seq_damage" in (Path("src/wolfengine/cli.py")).read_text(encoding="utf-8"), \
        "audit 那一面自己数了一套"

