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

不发一次请求：这六条全在 `--mock` 桌上跑（真人那一席本来就不经过端点）。
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
