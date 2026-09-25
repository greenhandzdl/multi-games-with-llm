"""`wolf run --human 3`：把 `#122` 那一只座位接进命令行（`#123`）。

`#122` 之后，那一席会读一行字、会问第二遍、会把"这个人走了"落进日志——但**没有任何生产代码构造
`HumanActor`**。所以"AI 与人类博弈"这件事在演示现场还不可达：它是测试里的一个对象，不是一条命令。
这一片补的就是这条路，而它要证的三件事各自不同：

* **名册只有一个构造点**。`--mock` 桌和真桌的两份名册都已经从 `cfg.seat_count` 来，人类座位不该是
  第三份"顺手把 3 号换掉"的实现——一个谓词两个读者就是这个仓库里缺陷的形状。
* **命令不对在落盘之前说**。`--human 10`、两个 `--human`、`--dry-run --human` 都是命令写错，不是
  引擎拒答。`#58`/`#59` 给 `--games 0` 和 `--seat 10` 立过这个次序：先拒，再碰磁盘，否则留下一个
  空目录或一份假 prompt 转储，比崩溃更贵。
* **整局的证据要在 CLI 上**。`test_human_seat.py` 那十条走的是 `agent.take_turn`；一局打完之后
  `actor_kinds` 里有没有 `human`、那个人说的话在不在日志里、他离开之后剩下的回合还认不认得出是
  谁答的——这三格以前没有现场。
* **读侧认领这一局（`#125`）**。上面那些日志被写出来之后，还没有一条用例把它读回去。于是写侧的
  归一化（`game.open_log` 的 `sorted(set(...))`）和读侧的谓词（`metrics.is_synthetic` 的
  `sorted(...) != ["llm"]`）之间没有对过账，而 `SYNTHETIC_CLAUSE["human"]` 那句 §十五 只被**手填**
  页眉的用例引过（`test_synthetic_attribution.py` 的 `_table`/`_rehumanise`）——手填的页眉证明不了
  引擎写的就是那个形状。这一片把那一局真日志读回三个出口。

不发一次请求：这些用例全在 `--mock` 桌上跑（真人那一席本来就不经过端点）。
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from wolfengine import cli, metrics
from wolfengine.events import EventLog, Kind
from wolfengine.game import DRAW_DAY_LIMIT

SEED = 7
MARK = "观望 我在听他们互相咬"


@pytest.fixture
def keyboard(monkeypatch):
    """一台会答话的键盘：先按脚本回答，剧本用完就是 EOF（= 这个人离开了）。

    打的是 `builtins.input` 而不是 `human.Console`：被测的正是"真 Console 那只手有没有被叫到"，
    换成替身屏幕就变成测我自己的夹具。`Console.read` 把 `EOFError` 收成 `None`，所以这里 raising
    才是真路径而不是替身行为。
    """

    def install(lines: list[str]):
        calls: list[str] = []

        def fake_input(prompt: str = "") -> str:
            calls.append(prompt)
            if not lines:
                raise EOFError
            return lines.pop(0)

        monkeypatch.setattr(builtins, "input", fake_input)
        return calls

    return install


def _log(out):
    paths = sorted(Path(out).glob(f"*_g{SEED:08d}.jsonl"))
    assert len(paths) == 1, f"一局该只有一个日志，实得 {paths}"
    return EventLog.read_records(paths[0])


def _asked(events):
    """3 号那一席被问过的回合（引擎替他答的那些也算——它们同样有 `actor`，也占了那一席）。"""
    return [e for e in events if e.actor == 3 and e.kind in metrics.DECISION_KINDS]


def _terminal(events):
    over = [e for e in events if e.kind == Kind.GAME_OVER]
    assert len(over) == 1, f"一局该有一条终局记录，实得 {len(over)} 条"
    return over[0].payload["terminal"]


# ------------------------------------------------------------------ 名册与落盘
def test_only_one_place_in_the_cli_puts_a_person_in_a_chair():
    """`HumanActor(` 在 `src/` 里恰好出现一次，且那一次在 `cli.py`：换座位不许有第二份实现。

    判据从磁盘上取（和 `test_wiring` 那几把尺同一手法），所以以后往 `batch` 里接真人会立刻红——
    而那一处该不该红，是 §十五 的另一条决定（含真人的局不进配对语料），不是这条管的事。
    """
    import ast

    hits = []
    for f in sorted(Path("src/wolfengine").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "HumanActor"):
                hits.append(f.name)
    assert hits == ["cli.py"], f"名册的构造点不该只有这一处：{hits}"


def test_a_person_at_the_table_is_asked_and_their_words_are_in_the_log(tmp_path, keyboard):
    calls = keyboard([MARK] * 12)
    out = tmp_path / "one"
    rc = cli.main(["run", "--mock", "--human", "3", "--seed", str(SEED),
                   "--quiet", "--out", str(out)])
    assert rc == 0, "一局里坐着一个人在打字，不能成为引擎拒绝出结论的理由"
    events, meta = _log(out)
    assert meta["actor_kinds"] == ["human", "mock"], meta["actor_kinds"]
    assert calls, "一局打完，那个人一次都没被问过"
    mine = _asked(events)
    assert mine, "3 号一轮都没被问过，那一席没上桌"
    # 只要求"至少一次原话进账，且进的是 3 号自己的格子"：真人那一席的 act 会被同一只硬闸门评，
    # 被判官指派换掉时 `_only_the_label_was_refused` 会留下他的句子（`fallback=1`），而夜间回合留
    # 不下——那不是这一片要证的（CLI 有没有把这个人接上桌），硬钉"每一回合都算他说的"就成了假账。
    # 但这句话**必须挂在 3 号身上**：`actor_kinds` 只说"这桌有一个人在打字"，把椅子挪到 2 号它照样
    # 是 `['human','mock']`，而替身答的回合 `meta.rung` 同样是 -1（`#122` 那格决定的）。变异
    # `/tmp/mut123.py` 的 K2 量的正是这一格，第一版这里扫的是全部事件，所以它是绿的。
    assert any(MARK.split(" ", 1)[1] in str(e.payload) for e in mine), (
        "3 号那一席没有一句是他说的话：那一席是引擎或替身在答")
    assert all(e.payload["meta"]["rung"] == -1 for e in mine), (
        "那一席被要求吐过 JSON：真人的回合不该走模型的解析梯子")


def test_the_person_who_leaves_leaves_a_record_and_the_game_still_ends(tmp_path, keyboard):
    """键盘一上手就是 EOF：这个人没坐进来。局要打完，而每一回合都要说清是谁答的。"""
    calls = keyboard([])
    out = tmp_path / "gone"
    rc = cli.main(["run", "--mock", "--human", "3", "--seed", str(SEED),
                   "--quiet", "--out", str(out)])
    assert rc in (0, 1), rc
    events, meta = _log(out)
    assert meta["actor_kinds"] == ["human", "mock"], meta["actor_kinds"]
    assert _terminal(events) in metrics.DECISIVE | {DRAW_DAY_LIMIT}, _terminal(events)
    assert calls, "输入已经关了也要真的问过——没问过就是名册里根本没有这一席"
    mine = _asked(events)
    assert mine, "3 号一轮都没答，那一席根本没上桌"
    for e in mine:
        assert e.result["fallback"] == 1, f"{e.kind} 那一回合没说是引擎替他答的"
        assert "human_input_closed" in str(e.attempts), (
            f"{e.kind}：代答的理由没落盘，这一格就成了模型弃答")


# ------------------------------------------------------------------ 三种命令写错
def test_a_seat_outside_the_table_is_refused_before_the_disk_is_touched(tmp_path, capsys,
                                                                       keyboard):
    keyboard([MARK] * 12)
    out = tmp_path / "range"
    rc = cli.main(["run", "--mock", "--human", "10", "--seed", str(SEED),
                   "--quiet", "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 2, f"越界的座位号拿到了 {rc}：那既是 rc 0 的表错，也是 rc 1 的引擎拒答"
    assert "名册" in err or "seat_count" in err, err
    assert not out.exists(), f"命令本身不对，却在磁盘上留下了 {out}"
    # 正面对照：合法的名册边界不能被同一句拒绝挡掉，否则"越界"这条判据可以永远返回拒绝。
    assert cli.main(["run", "--mock", "--human", "9", "--seed", str(SEED),
                     "--quiet", "--out", str(out)]) == 0
    assert out.exists()


def test_two_people_on_one_keyboard_are_refused(tmp_path, capsys, keyboard):
    keyboard([MARK] * 12)
    out = tmp_path / "two"
    rc = cli.main(["run", "--mock", "--human", "3", "--human", "7", "--seed", str(SEED),
                   "--quiet", "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 2, (
        "两个 --human 里后写的那个赢，等于『哪一席坐着人』由 argparse 的写入顺序决定")
    assert "一个键盘" in err or "stdin" in err, err
    assert not out.exists(), f"命令本身不对，却在磁盘上留下了 {out}"


def test_the_prompt_dump_refuses_to_promise_a_seat_a_person_is_in(tmp_path, capsys,
                                                                  no_network, keyboard):
    """`--dry-run` 的产物是"每一席的 prompt"，而真人那一席没有 prompt 可 dump。

    不拒的话，`g*.prompts.jsonl` 会替 3 号印一份模型 prompt——那份文件的全部用途就是数"模型会读到
    什么"，里面混着一席从来不由模型答的座位，数出来的就是假账（`test_cli.py` 里那句"九席都有 prompt"
    正是拿这个形状当判据的）。
    """
    keyboard([MARK] * 12)
    out = tmp_path / "dry"
    rc = cli.main(["run", "--dry-run", "--human", "3", "--seed", str(SEED),
                   "--out", str(out)])
    assert rc == 2, capsys.readouterr().err
    assert not out.exists(), f"命令本身不对，却在磁盘上留下了 {out}"
    assert no_network == [], "被拒绝的命令仍然够到了端点"


# ------------------------------------------------------------------ 读侧认领这一局（#125）
#
# 上面几条读的是**磁盘上那一格**。这一节把这局真日志交回给读侧，为的是让两头对一次账：
# `test_synthetic_attribution.py` 把"替身桌被拒时引用哪一条"钉得很死，可它的页眉是**手填**的
# （`_table` 造 dict，`_rehumanise` 就地改 jsonl 的第一行）。手填的页眉证不到引擎写的那一格长什么
# 样：`game.open_log` 写的是 `sorted(set(kinds))`，`metrics.is_synthetic` 读的是
# `sorted(...) != ["llm"]`，`seat_kinds` 读的是 set 摊平——三处任何一处改法不同，只有真日志会红。
ROSTER = "桌边坐着一个真人"
HUMAN_CLAUSE = "plan §十五（含真人座位的局永远不得进入配对评测语料）"


def _played(tmp_path, keyboard, name, *, human="3", seed=SEED):
    """CLI 真打的一局，返回**日志路径**——读侧要的是文件，不是已经解析过的结果。"""
    keyboard([MARK] * 40)
    out = tmp_path / name
    args = ["run", "--mock", "--seed", str(seed), "--quiet", "--out", str(out)]
    if human:
        args += ["--human", human]
    assert cli.main(args) == 0, f"{name}：一局没打完，读侧就没有东西可读"
    paths = sorted(out.glob(f"*_g{seed:08d}.jsonl"))
    assert len(paths) == 1, f"一局该只有一个日志，实得 {paths}"
    return paths[0]


def test_a_real_human_seat_game_is_refused_by_the_clause_about_humans(tmp_path, keyboard):
    """写侧落的那一格，读侧要认得出来，并且引用的是**管人的那一条**。

    这张桌上同时还有八席替身，所以两条条款都该在场、各带自己管的种类。只钉"§十五 在"是不够的：
    一句写死"含 mock/human"的措辞也能过（`#89` 的红就是这样来的），所以种类也按实印的那串比。
    """
    g = metrics.read_game(_played(tmp_path, keyboard, "read"))
    assert g.meta["actor_kinds"] == ["human", "mock"], g.meta["actor_kinds"]
    assert g.is_synthetic, "引擎自己写的页眉，读侧没认出来是替身桌"
    note = metrics.m1_win_rate([g])["note"]
    assert HUMAN_CLAUSE in note, note
    assert "plan §十一" in note, f"八席替身没被引用它们那条：{note}"
    assert "actor_kinds=['human', 'mock']" in note, note
    assert metrics.m1_win_rate([g])["good_win_rate"] is None, "被拒的局仍然产出了胜率"
    gate = metrics.m3_gate_verdict([g])["note"]
    assert HUMAN_CLAUSE in gate, f"两个出口说的是两件事：{gate}"


def test_the_same_game_with_no_person_cites_only_the_clause_about_stands(tmp_path, keyboard):
    """一行之差的对照：桌边没有人的同一局，只许引 §十一。

    少了这一半，上一条就成了空判据——一句"两种条款都印"的措辞也能把它喂绿。
    """
    g = metrics.read_game(_played(tmp_path, keyboard, "no-human", human=None))
    assert g.meta["actor_kinds"] == ["mock"], g.meta["actor_kinds"]
    assert g.is_synthetic, "替身桌照样该被拒——被拒的理由不是「有没有人」"
    note = metrics.m1_win_rate([g])["note"]
    assert "plan §十一（mock 只会自证，这几项不许省）" in note, note
    assert "§十五" not in note and "§15" not in note, f"没人的桌被说成了有人的桌：{note}"


def test_two_games_on_one_read_share_a_seat_kind_list_without_repeating_it(tmp_path, keyboard):
    """批次侧那句话数的是**跨局摊平**的种类，而那一摊以前只有手填页眉在读。

    `metrics.seat_kinds` 是一个 set 推导。把 `sorted({...})` 换成 `sorted([...])` 时，上面那两条单局
    用例一条都不会红——`game.open_log` 已经在每一局的页眉里去过一次重了，所以那是一具**等价变异**
    （`/tmp/mut125.py` 的 W3 量的正是这件事：它红 2 条，不是我预期的 3 条）。只有把两局真日志放到同
    一张桌上，才问得到"读者看到的那张种类表里 mock 出现一次还是两次"。
    """
    games = [metrics.read_game(_played(tmp_path, keyboard, f"pair-{i}", seed=11 + i,
                                       human="3" if i == 0 else None)) for i in (0, 1)]
    assert [g.meta["actor_kinds"] for g in games] == [["human", "mock"], ["mock"]]
    note = metrics.m1_win_rate(games)["note"]
    assert "本批 2 局全为替身桌" in note, note
    assert "actor_kinds=['human', 'mock']" in note, (
        f"两局共用的种类被数了两遍，那一格就不再是'桌上坐着谁'：{note}")


@pytest.mark.parametrize("which", ["replay", "watch", "export"])
def test_every_screen_of_this_game_says_a_person_sat_at_the_table(tmp_path, keyboard, capsys,
                                                                  which):
    """三个给人看的出口以前**一个字都没提**（2026-09-25T12:04Z 实测：拿含真人的日志逐屏读过，
    `replay`、`watch --once`、`export` 的 HTML 里连 "human" 这串都没出现）。

    为什么这一格值得单独一句，而不是并进 `actor_kinds` 那个读数：拿给人看的三屏都在说"这局不可
    复现，因为端点没有确定性"——桌边坐着一个从不经过端点的人时，那句话的主语根本不在这张桌上。
    批次那一侧不受影响：`batch` 的参数表里没有 `--human`（本节最后一条），它产不出这种局。
    """
    log = _played(tmp_path, keyboard, f"screen-{which}")
    if which == "export":
        page = tmp_path / "page.html"
        assert cli.main(["export", str(log), "--out", str(page)]) == 0
        text = page.read_text(encoding="utf-8")
    else:
        assert cli.main([which, str(log)] + (["--once"] if which == "watch" else [])) == 0
        text = capsys.readouterr().out
    assert ROSTER in text, f"{which} 上没有那一席是谁答的：{text[:300]!r}"
    assert "human" in text, f"{which} 只说了'有个'却说不出的种类：{text[:300]!r}"
    if which == "export":
        # 页眉那一串 `⚠` 说的是"这个文件坏了"。桌边坐着一个人什么也没坏，把它标成损坏等于让
        # 读者去找一个不存在的坏处。
        assert f"⚠ {ROSTER}" not in text, "页面把'桌边有人'印成了文件损坏"


def test_the_same_three_screens_stay_quiet_when_nobody_sat_there(tmp_path, keyboard, capsys):
    """反向对照：同一份夹具、桌边没有人的那一局，三屏都不许印出那句话。

    少了这一半，`roster_notice` 改成"无条件返回那句话"照样全绿——而它对替身桌是假话（没有哪一席
    是不经过端点的人答的），读者会以为自己看的是一局人机。实测（2026-09-25T12:18Z，同一个
    `--seed 7`）：`--human 3` 的日志在 replay/watch/export 上各印 1 次，纯替身日志印 0 次。
    """
    log = _played(tmp_path, keyboard, "no-person", human=None)
    page = tmp_path / "page.html"
    assert cli.main(["export", str(log), "--out", str(page)]) == 0
    assert ROSTER not in page.read_text(encoding="utf-8")
    assert cli.main(["replay", str(log)]) == 0
    assert ROSTER not in capsys.readouterr().out
    assert cli.main(["watch", str(log), "--once"]) == 0
    assert ROSTER not in capsys.readouterr().out


def test_the_roster_sentence_has_one_owner_and_reaches_three_screens():
    """同一句话不许长两种措辞：判据住在 `events.py`，三个出口各自去够它那只手。

    与 `meta_notice`/`empty_notice` 同一个待遇（`test_log_recovery.py` 里那三条 `..._has_one_owner`）。
    """
    hits = sorted(p.name for p in Path("src").rglob("*.py")
                  if ROSTER in p.read_text(encoding="utf-8"))
    assert hits == ["events.py"], f"这句话被抄到了别处：{hits}"
    for mod in ("cli.py", "render_html.py", "render_live.py"):
        src = (Path("src/wolfengine") / mod).read_text(encoding="utf-8")
        assert "roster_notice" in src, f"{mod} 没走那只手，它印的是自己那份措辞"


def test_the_batch_side_still_cannot_claim_a_person_at_its_table():
    """`--human` 只挂在 `run` 上：批次那张桌构造不出一席真人，所以 §十五 在批次侧是一句**结构**上
    成立的话，而不是一条要有人记得加的过滤。

    判据从 parser 上取，不从磁盘上 grep：`_rehumanise` 那一类夹具改的是文件，改不了命令行；而
    "批次的产物就是配对语料"这句一旦哪天 `batch` 也接了真人，就得有人在那里补一条真的剔除。
    """
    subparsers = cli.build_parser()._subparsers._group_actions[0].choices
    assert {"run", "batch", "compare", "gate"} <= set(subparsers), sorted(subparsers)
    flags = {name: {opt for a in sp._actions for opt in a.option_strings}
             for name, sp in subparsers.items()}
    assert "--human" in flags["run"], "run 上的 --human 没了，那这一族的用例全在测空气"
    for name, opts in sorted(flags.items()):
        if name != "run":
            assert "--human" not in opts, (
                f"{name} 也接了 --human：批次侧那句结构性结论作废，"
                f"§十五 在那里需要一个真的过滤器（并把这条用例改成断言它）")
