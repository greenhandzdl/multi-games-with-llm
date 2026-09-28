"""Cross-module wiring: the tests that would have caught the split-brain.

Written after a smoke run found defects no single-module test could see — three modules
had invented three names for the same event (`vote` / `vote_cast` / `vote_result`), two
modules each owned a chronicle renderer and only one was called, the belief card could
render "存活：号。" with nothing in it, and a soft-gate helper was named wrongly enough to
raise NameError on the hottest path in the game.

Every test here pushes data through *two or more* modules. That is the point:
test_rules and test_schema stayed green the whole time those seams were broken.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import random
import re
from pathlib import Path

import pytest

from wolfengine import (assemble, belief, compress, events, info, legality, persona,
                        render_html, render_live, roles, rules, schema, state)
from wolfengine.config import INERT_FIELDS, INERT_LEAVES, Config, RegionBudget
from wolfengine.events import Event, EventLog, Kind, seats
from declared_kinds import KINDS


# --------------------------------------------------------------------------- helpers
def ev(seq: int, kind: str, *, day: int = 1, phase: str = "day_speech",
       visibility="all", actor: int | None = None, **payload: object) -> Event:
    """Payload is spelled as kwargs deliberately: a `payload=` argument would land inside
    **payload and nest the dict one level down, which is how two tests here first lied."""
    return Event(seq=seq, kind=kind, day=day, phase=phase, visibility=visibility,
                 payload=dict(payload), actor=actor)


def nine_seat_log(tmp_path) -> tuple[EventLog, state.GameState, dict[int, str]]:
    """A real deal through the real rules, then a plausible day-1 history."""
    rng = random.Random(7)
    board = roles.board_for(9)
    deal = rules.deal(board, rng)
    gs = state.GameState(
        board=board, game_id="w", deal_seed=7,
        seats={s: state.SeatState(seat=s, role=deal[s]) for s in deal},
        witch_seat=next(s for s, r in deal.items() if r == "witch"),
        seer_seat=next(s for s, r in deal.items() if r == "seer"),
        hunter_seat=next(s for s, r in deal.items() if r == "hunter"),
        phase=state.Phase.DAY_SPEECH,
        speech_order=rules.speech_order(rng, sorted(deal)),
    )
    log = EventLog(tmp_path / "w.jsonl")
    wolves = [s for s, r in deal.items() if r == "wolf"]
    log.append(Kind.GAME_START, day=1, phase="night_wolf", seats=sorted(deal))
    for s, r in sorted(deal.items()):
        log.append(Kind.DEAL, day=1, phase="night_wolf", visibility=seats(s), actor=s,
                   role=r, teammates=[x for x in wolves if x != s] if r == "wolf" else [])
    log.append(Kind.SPEECH, day=1, phase="day_speech", actor=1,
               text="3号发言太顺了，我怀疑他。", act="accuse", target=3)
    log.append(Kind.WOLF_CHAT, day=1, phase="night_wolf", visibility=seats(*wolves), actor=wolves[0],
               text="今晚刀6号。")
    log.append(Kind.SEER_RESULT, day=1, phase="night_seer", visibility=seats(gs.seer_seat),
               actor=gs.seer_seat, target=5, verdict="wolf")
    log.append(Kind.VOTE, day=1, phase="day_vote", actor=1, target=3)
    log.append(Kind.VOTE_RESULT, day=1, phase="day_vote", tally={"3": 1})
    return log, gs, deal


ALL_KIND_EVENTS = [
    ev(1, Kind.GAME_START, seats=[1, 2, 3]),
    ev(2, Kind.DEAL, visibility=seats(1), actor=1, role="wolf", teammates=[2]),
    ev(3, Kind.PHASE, text="天黑了。"),
    ev(4, Kind.SPEECH, actor=1, text="我是预言家。", act="accuse", target=2),
    ev(5, Kind.LAST_WORDS, actor=1, text="你们会后悔的。"),
    ev(6, Kind.NIGHT_ACTION, visibility=seats(1), actor=1, act="kill", action="kill", target=3),
    ev(7, Kind.WOLF_CHAT, visibility=seats(1), actor=1, text="刀3号。"),
    ev(8, Kind.SEER_RESULT, visibility=seats(2), actor=2, target=3, verdict="wolf"),
    ev(9, Kind.VOTE, actor=1, target=3),
    ev(10, Kind.VOTE, actor=2, target=None),
    ev(11, Kind.VOTE_RESULT, tally={"3": 1, "1": 1}),
    ev(12, Kind.DEATH, seat=3, cause="wolf_kill"),
    ev(13, Kind.COMPACTION, summary="第1天概要", window=8),
    ev(14, Kind.GAME_OVER, winner="wolf", terminal="wolf_win"),
    ev(15, Kind.NOTICE, visibility=seats(4), text="今晚7号倒在了狼刀下。", about=7),
]


# --------------------------------------------------------------- 1. one renderer, total
@pytest.mark.parametrize("e", ALL_KIND_EVENTS, ids=lambda e: e.kind)
def test_every_declared_kind_has_a_renderer(e):
    """A declared kind that falls through is invisible in tests and loud in the game."""
    line = compress.render_line(e)
    assert "未渲染事件" not in line, f"{e.kind} is declared but not rendered"
    assert "{" not in line, f"{e.kind} leaked a raw payload: {line}"


def test_undeclared_kind_hits_the_tripwire():
    line = compress.render_line(ev(1, "something_new", x=1))
    assert "未渲染事件 something_new" in line
    assert "'x': 1" not in line, "the fallback must not dump a payload into a prompt"


def test_the_declared_vocabulary_is_what_the_modules_agree_on():
    """Pinned to a count on purpose: adding a kind without a renderer breaks here.

    14 since Kind.NOTICE (the moderator telling one seat something, which is how the
    witch learns where the knife fell); the number is the test, not the list, so an
    accidental rename that keeps the count honest still has to be written down.
    """
    assert len(KINDS) == 14, sorted(KINDS)
    assert {e.kind for e in ALL_KIND_EVENTS} == set(KINDS)


def test_every_declared_kind_has_an_emitter():
    """渲染侧的守卫在上面；这条补齐另一半：词表里每个名字都得真有个地方把它落盘。

    `compaction` 就是漏网的那一个——`Kind` 里有、`render_line` 有分支、`audit` 有计数器，
    而 src/ 里没有任何一处写它，于是 `kinds["compaction"]` 永远读成 0，一份"这局从没折叠"的
    假话。名字按发射点收集：`log.append(Kind.<名字>)`、`t.say(Kind.<名字>)`、`kind=Kind.<名字>`。

    用 `in EMIT_NAMES` 而不是"出现在任意调用的实参里"：`kinds.get(Kind.COMPACTION, 0)` 也是
    实参，读侧的引用会把自己伪装成写侧。
    """
    EMIT_NAMES = {"append", "say", "ask"}
    declared = {n for n in dir(Kind) if not n.startswith("_") and n.isupper()}

    def kind_name(node):
        return (node.attr if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "Kind" and node.attr in declared else None)

    emitted = set()
    for f in sorted(Path("src/wolfengine").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            emitted |= {kind_name(a) for a in node.args if fname in EMIT_NAMES and kind_name(a)}
            emitted |= {kind_name(kw.value) for kw in node.keywords
                        if kw.arg and kw.arg.startswith("kind") and kind_name(kw.value)}
    assert declared <= emitted, f"声明了却没人写：{sorted(declared - emitted)}"


def _kind_comparisons(path: Path) -> tuple[list[tuple], list[tuple]]:
    """(object-form, raw-form) literal comparisons against an event kind, as (file, line, literal).

    Two tiers because the two spellings mean different things. `e.kind` is a loaded `Event`, so
    comparing it to a string invents a second name for a declared fact — exactly the split-brain
    this file exists to prevent, and the reason src/ has been banned from it for a while.
    `rec["kind"]` is a dict from `json.loads`: that test reads the file *as a file* on purpose so
    it doesn't share an implementation with `metrics`, and a literal there is the wire contract.
    A literal is still only legal if it names a declared kind, so a rename fails loudly instead of
    turning the filter into a no-op.

    Parsed from the AST rather than by regex because the regex would flag its own source line —
    the pattern `kind\\s*[!=]=` lives in this file's text.
    """
    obj, raw = [], []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            continue
        left, right = node.left, node.comparators[0]
        for a, b in ((left, right), (right, left)):
            if not (isinstance(b, ast.Constant) and isinstance(b.value, str)):
                continue
            spot = (path.name, node.lineno, b.value)
            if isinstance(a, ast.Attribute) and a.attr == "kind":
                obj.append(spot)
            elif (isinstance(a, ast.Subscript) and isinstance(a.slice, ast.Constant)
                    and a.slice.value == "kind"):
                raw.append(spot)
    return obj, raw


def _undeclared_kinds(spots: list[tuple]) -> list[tuple]:
    """原始形式里那些没在 `Kind` 里声明过的字面量。

    单独成函数是为了让对照用例**调用**这条规则而不是复算它：第一版把判定写在守卫体内，控制用例
    于是自己写了一遍 `[s[2] for s in raw if s[2] not in KINDS]`——结果"把判定改瞎"那具变异活了下来
    (两遍实现里瞎掉的那遍没人看)。
    """
    return [s for s in spots if s[2] not in KINDS]


def test_no_module_compares_an_event_kind_to_a_bare_string():
    """The split-brain this file exists to prevent was a string literal compared in each
    of three modules. `Kind` exists so the only legal spelling is the declared one.

    Scope is the whole repo, not just src/: a wrong literal in src/ fails as a broken game, while
    the same literal in a test fails as a *passing* test — the filter silently matches everything
    or nothing. `tests/` had five of these (`compaction`, `game_over`) until the ban was widened.
    """
    banned, wire = [], []
    for f in sorted(Path("src/wolfengine").rglob("*.py")) + sorted(Path("tests").glob("*.py")):
        o, r = _kind_comparisons(f)
        banned += o
        wire += r
    undeclared = _undeclared_kinds(wire)
    assert not banned, f"用 Kind.X，别写字面量：{banned}"
    assert not undeclared, f"读原始 JSONL 可以写字面量，但必须是声明过的 kind：{undeclared}"
    # 两档都得真的扫到东西，否则"第二档一条违规都没有"可能只是第二档没跑。
    assert wire, "没扫到任何 `rec[kind] == …` 形式：范围或解析坏了，第二档闸门是空转的"


def test_the_kind_guard_sees_both_tiers_on_a_synthetic_file(tmp_path):
    """正面对照：字面量在对象形式和原始形式里各红一次，否则改瞎哪一档都看不出来。

    真实语料是干净的，所以这一条不读仓库文件——它往 tmp_path 写一小段代码，钉的是"检测能力在"。
    """
    probe = tmp_path / "probe_kind.py"
    probe.write_text(
        "def a(e):\n"
        '    return e.kind == "compaction"\n'
        "def b(rec):\n"
        '    return rec["kind"] == "vote_cast"\n'
        "def c(rec):\n"
        '    return rec["kind"] == "speech"\n', encoding="utf-8")
    obj, raw = _kind_comparisons(probe)
    assert [s[2] for s in obj] == ["compaction"], obj
    assert sorted(s[2] for s in raw) == ["speech", "vote_cast"], raw
    assert [s[2] for s in _undeclared_kinds(raw)] == ["vote_cast"], (
        "`vote_cast` 是那次 split-brain 里发明的假名字，必须被判未声明；"
        "`speech` 是声明过的，不该跟着一起红")


def test_there_is_exactly_one_chronicle_renderer():
    owners = [f.name for f in sorted(Path("src/wolfengine").rglob("*.py"))
              if "def render_line" in f.read_text(encoding="utf-8")]
    assert owners == ["compress.py"], owners


VIEWING_RULES: dict[str, tuple[str, list[str]]] = {
    # rule -> (the one module allowed to `def` it, the modules that must import it from there)
    "shown_events": ("render_html", ["render_live"]),
    "event_flags": ("render_html", ["render_live"]),
    "markers": ("render_html", ["render_live"]),
    "mind_pairs": ("render_html", ["render_live"]),
    "roles_by_seat": ("render_html", ["render_live"]),
    "role_zh": ("render_html", ["render_live"]),
    "game_over_event": ("render_html", ["render_live"]),
    "voting_waves": ("events", ["render_html", "metrics"]),
}


def _imported_from(module: str, owner: str) -> set[str]:
    """Names `module.py` imports out of `owner.py`.

    The substring check this replaces passed on a docstring mention, so a view could stop
    using the shared rule and keep the test green by leaving the word in a comment.
    """
    text = Path(f"src/wolfengine/{module}.py").read_text(encoding="utf-8")
    return {a.name for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.ImportFrom) and node.module == owner and node.level == 1
            for a in node.names}


@pytest.mark.parametrize("rule", sorted(VIEWING_RULES))
def test_the_two_views_share_one_definition_of_each_viewing_rule(rule):
    """M6's split-brain risk: a live frame and a 复盘 HTML that each decide what a spectator may
    see are two audience-sized truths about one file, and only one of them gets looked at when
    somebody reports "the shared file leaked". Same shape as the render_line pin above."""
    owner, users = VIEWING_RULES[rule]
    owners = [f.name for f in sorted(Path("src/wolfengine").rglob("*.py"))
              if f"def {rule}(" in f.read_text(encoding="utf-8")]
    assert owners == [f"{owner}.py"], owners
    for f in users:
        assert rule in _imported_from(f, owner), (
            f"{f}.py 必须从 {owner}.py 导入 {rule}，而不是在旁边另写一份")


def test_the_torn_tail_bound_has_one_owner_and_every_reader_calls_it():
    """How many unparsable lines a reader may wave past is a rule about the **file**, and four
    modules read that file: the live tail, the transcript and `audit` (cli), the 复盘 HTML, and
    every metric built on `read_game`. Each owning its own `while the last line fails: pop()` is
    how one truncated batch ends up with four opinions about whether the game finished.

    The notice sentence has the same shape one layer up: two printers, one counter. It is pinned
    as an import rather than a substring because a docstring mention used to satisfy the old
    version of this check (see `_imported_from`).
    """
    for def_ in ("def split_torn_tail(", "def torn_notice("):
        owners = [f.name for f in sorted(Path("src/wolfengine").rglob("*.py"))
                  if def_ in f.read_text(encoding="utf-8")]
        assert owners == ["events.py"], f"{def_} 的第二份实现在 {owners}"
    for reader in ("cli.py", "metrics.py", "render_html.py", "render_live.py"):
        assert "read_split(" in Path("src/wolfengine", reader).read_text(encoding="utf-8"), (
            f"{reader} 自己在解析日志行，没有走 events 的那一份")
    for printer in ("cli", "render_html"):
        assert "torn_notice" in _imported_from(printer, "events"), (
            f"{printer}.py 在自己重写那句截断通知")


def _defs_named(src: str, name: str) -> list[ast.FunctionDef]:
    """Every `def <name>` in this source, module-level or a method."""
    return [n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == name]


def _code(fn: ast.FunctionDef) -> str:
    """A function body with docstrings stripped — so "this property is one call" can be checked
    by comparing text, and rewriting the docstring cannot turn the check into a no-op."""
    return "\n".join(ast.unparse(s) for s in fn.body
                     if not (isinstance(s, ast.Expr)
                             and isinstance(s.value, ast.Constant)
                             and isinstance(s.value.value, str)))


def test_the_truncation_extent_has_one_arithmetic_and_three_readers():
    """#56 让批次成为"末行被砍了多少"的第三个读者（`torn_notice` 那句话、`audit` 那一格、报告里那
    一节）。在接进来之前，这两个数被数了**两遍**：句子自己数一次，`cli.py` 的 dict 再数一次。两份
    "看起来一样"的算术正是这一路一直在拆的东西（#27/#28 的区域预算、#48 的 manifest 判据）。

    三条腿各管一种错法，最后一条是这一族特有的：行为用例钉不住"抄一份恰好也算对的算术"，所以要
    按**谁能碰到被砍的原文**来查。`sum(len(...) for ...)` 这种形状本身在四个文件里都有（各数各的
    东西，第一次写这条腿时就误伤过），所以判据换成数**次数**：`torn_tail` 这个字段全仓库只许被读到
    两处，各一处——`metrics.py` 里那只转交的手、`cli.py` 里那句 `is not` 的空判断。批次碰不到原文，
    就只能走 `Game.torn_extent`。
    """
    arithmetic = [n for n in ast.parse(Path("src/wolfengine/events.py").read_text(encoding="utf-8")).body
                  if isinstance(n, ast.FunctionDef) and n.name == "torn_extent"]
    assert len(arithmetic) == 1, "数行数/字节数那只手在 events.py 里被写了不止一份"

    prop = _defs_named(Path("src/wolfengine/metrics.py").read_text(encoding="utf-8"), "torn_extent")
    assert len(prop) == 1 and any(ast.unparse(d) == "property" for d in prop[0].decorator_list)
    assert _code(prop[0]) == "return torn_extent(self.torn_tail)", (
        f"Game.torn_extent 不只是一次转交：{_code(prop[0])}")

    reads = {p.name: sum(1 for x in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                         if isinstance(x, ast.Attribute) and x.attr == "torn_tail")
             for p in sorted(Path("src/wolfengine").rglob("*.py"))}
    touched = {k: v for k, v in reads.items() if v}
    assert touched == {"cli.py": 1, "metrics.py": 1}, (
        f"碰到被砍原文的地方变了：{touched}——多一处就多一套数法"
        "（#56 之前 `cli.py` 正是第二处：那句 `is not` 之外还自己数了一遍行数和字节数）")
    assert _defs_named(Path("src/wolfengine/batch.py").read_text(encoding="utf-8"), "torn_extent") == []
    assert "torn_extent" in _imported_from("metrics", "events")
    assert "torn_extent" not in _imported_from("batch", "events"), (
        "batch.py 绕开 Game 自己去数，批次读数和转录读数就有两套算法了")


def test_the_batch_reads_the_numbering_arithmetic_instead_of_recounting_it():
    """#55 给批次侧补了一个编号破损读数。那条行为用例只在一个 fixture 上比过"相等"，而一份恰好
    也算对了缺号与重号的第二实现照样能绿——所以判据得是结构性的，且不能只是一句子串搜索：
    `Game.seq_damage` 自己就叫这个名字，`"def seq_damage(" in text` 会把它当成第二套算术（第一版
    这条用例就是这么红的）。

    三腿各管一种错法：
    * 全仓库只有 `events.py` 里有**算术**（`Game` 上那个 property 不算，它的身体必须是一次调用，
      逐字对得上），别处不许再 `def` 一个；
    * `metrics.py` 从 `events` **导入**这个名字（子串不算——一个 docstring 提到它就能骗过，
      见 `_imported_from` 的来历）；
    * `batch.py` 不导入它：批次要经 `Game` 拿数，否则 #54 刚拆掉的"四个出口各有一套算法"就在
      机器侧原地重建。
    """
    arithmetic = [n for n in ast.parse(Path("src/wolfengine/events.py").read_text(encoding="utf-8")).body
                  if isinstance(n, ast.FunctionDef) and n.name == "seq_damage"]
    assert len(arithmetic) == 1, "计数那只手在 events.py 里被写了不止一份"
    prop = _defs_named(Path("src/wolfengine/metrics.py").read_text(encoding="utf-8"), "seq_damage")
    assert len(prop) == 1 and any(ast.unparse(d) == "property" for d in prop[0].decorator_list)
    assert _code(prop[0]) == "return seq_damage(self.events)", (
        f"Game.seq_damage 不只是一次转交：{_code(prop[0])}")
    elsewhere = [p.name for p in sorted(Path("src/wolfengine").rglob("*.py"))
                 if p.name not in ("events.py", "metrics.py")
                 and _defs_named(p.read_text(encoding="utf-8"), "seq_damage")]
    assert elsewhere == [], f"编号计数被抄到了第二处：{elsewhere}"
    assert "seq_damage" in _imported_from("metrics", "events")
    assert "seq_damage" not in _imported_from("batch", "events"), (
        "batch.py 绕开 Game 自己去数，批次读数和转录读数就有两套算法了")


def _literal_alias(src: str, name: str) -> set[str]:
    """模块级 `name = Literal[...]` 的取值集合。

    从源码里读，是为了不在测试里抄第二份阵营表——抄一份就等于把"键空间只有一处定义"这条判据
    自己违反掉（#80 要钉的就是这个）。
    """
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
                and isinstance(node.value, ast.Subscript)
                and ast.unparse(node.value.value) == "Literal"):
            sl = node.value.slice
            elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
            return {e.value for e in elts if isinstance(e, ast.Constant)}
    raise AssertionError(f"{name} 不是模块级的 Literal 别名，去 roles.py 里看它变成什么了")


def test_the_win_check_names_its_key_space_as_teams_and_has_no_role_twin():
    """#80：`winner_for` 读的键是**阵营**，参数却叫 `alive_roles`、docstring 说"which roles
    remain alive"，旁边还站着一个按职业建键的 `alive_role_counts`。

    三条腿各管一种错法，都不靠子串搜索：
    * 键空间：函数体里从那个计数参数上取的每个字符串键，必须是 `roles.Team` 的取值之一。
      这一腿今天就是绿的，它管的是**将来**——有人往屠边判据里加一句 `alive.get("seer", 0)`。
    * 名字与注解：那个参数得叫 team、注解得写 `Counter[Team]`，docstring 得提到阵营。今天红。
    * 孪生：`rules.py` 里不许再有"用 `role_of` 建 Counter"这个**形状**（不是"那个名字不许多一处"，
      换个名重抄一遍照样是把整局当场判狼赢的入口留着）。今天红。

    为什么名字值得一条守卫：`Counter` 按职业建键时压根没有 "god" 这个键，`get("god", 0)` 永远是
    0，屠边在第一次 `check_win` 就判狼赢——`tests/test_rules.py` 里那格把后果钉成了读数。
    """
    src = Path("src/wolfengine/rules.py").read_text(encoding="utf-8")
    teams = _literal_alias(Path("src/wolfengine/roles.py").read_text(encoding="utf-8"), "Team")
    assert teams == {"wolf", "villager", "god"}, f"阵营键空间变了：{sorted(teams)}"

    fn = _defs_named(src, "winner_for")[0]
    counter = fn.args.args[1]
    keys = set()
    for node in ast.walk(fn):
        taken = None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.func.value is not None
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == counter.arg and node.args):
            taken = node.args[0]
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
              and node.value.id == counter.arg):
            taken = node.slice
        if isinstance(taken, ast.Constant) and isinstance(taken.value, str):
            keys.add(taken.value)
    assert keys and keys <= teams, f"胜负判据读了键空间外的键：{sorted(keys - teams)}"

    assert "team" in counter.arg and "role" not in counter.arg, (
        f"参数名替调用方撒了谎：它叫 {counter.arg}，键空间却是阵营")
    assert ast.unparse(counter.annotation) == "Counter[Team]", (
        f"注解没写出键空间：{ast.unparse(counter.annotation)}——类型检查器就此帮不上忙")
    doc = ast.get_docstring(fn) or ""
    assert "阵营" in doc or "team" in doc.lower(), (
        "docstring 还在说'哪些职业活着'，而下一个人会照它接线")

    twins = [f.name for f in _defs_named(src, "alive_role_counts")] + [
        n.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)
        and any(isinstance(c, ast.Call) and ast.unparse(c.func) == "Counter"
                and "role_of" in ast.unparse(c) for c in ast.walk(n))]
    assert twins == [], f"rules.py 里还有按职业建键的计数函数：{sorted(set(twins))}"

    call = [n for n in ast.walk(_defs_named(src, "check_win")[0])
            if isinstance(n, ast.Call) and ast.unparse(n.func) == "winner_for"]
    assert len(call) == 1 and len(call[0].args) == 2, "check_win 不再是一次两参数的调用，下面那条白写"
    fed = ast.unparse(call[0].args[1])
    assert fed == "alive_team_counts(state)", (
        f"check_win 喂给胜负判据的是 {fed}，键空间对不对没人钉")


def _py_refs(def_roots: tuple[str, ...], ref_roots: tuple[str, ...]):
    """(每个标识符在 `ref_roots` 里被**引用**的次数, `def_roots` 里出现过的 def 名字集合)。

    引用只数 AST 的真引用：`Name`/`Attribute`/装饰器，外加**等于该名字的字符串常量**——后者是为了
    不误伤 `getattr(mod, "x")` 与 `__all__` 这类按名字派发（它们是真读者）。docstring 里的提及
    **不算**：#32 那一族的教训就是"注释留着词、代码早就不用了"照样能骗过子串搜索。

    "字符串算不算读者"是有代价的：`#155` 那条生产链判据把 `__all__` 与字典键排除在外，只认
    `getattr(obj, "名字")` 那种派发；两边的差额由
    `test_the_export_list_is_not_a_caller_and_the_only_name_it_would_have_saved_is_declared`
    逐名点名，不靠推测。
    """
    refs: dict[str, int] = {}
    defined: set[str] = set()

    def bump(key: str) -> None:
        refs[key] = refs.get(key, 0) + 1

    def scan(root: str, own: bool) -> None:
        for f in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and own:
                    defined.add(node.name)
                elif isinstance(node, ast.Attribute):
                    bump(node.attr)
                elif isinstance(node, ast.Name):
                    bump(node.id)
                elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and node.value.isidentifier()):
                    bump(node.value)

    for root in def_roots:
        scan(root, own=True)
    for root in ref_roots:
        scan(root, own=False)
    return refs, defined


def test_no_named_helper_in_the_engine_is_left_without_a_reader():
    """`#81`：`src/wolfengine` 下曾有八个"除定义行外没人点它名"的函数，其中两个的 docstring 还在
    认领一个不存在的读者——`state.kill_target_last_night` 写着"Read by the witch prompt only"，
    而女巫看到的是 `phases.py` 里那条 `Kind.NOTICE`；`belief.mean_agreement` 的"Feeds M6"更是把
    M6 指到了别人身上（`metrics.m6_belief_action` 自己在原地算另一个判据）。

    判据取"零读者"这个形状本身，不取那份清单：清单要第二个读者才不作弊，而"引用次数 == 0"不需要。
    范围两端都钉：被定义的一侧只数引擎（`src/`），读者那一侧数全树（`src/`+`tests/`+`scripts/`）——
    第一版只数了 `src/`，于是十八个名字里一半其实住在测试里，那是我的尺错了不是它们的读者没了。
    限界也要写出来——这条**只数名字**，一个签名与调用都对、却从没被接进产物链的函数它看不见（那是
    #74/#75 那一族走的事），它管的是"树上挂着八把没人拿的扳手"这一种腐烂。定义面**只数 `def`**：
    模块级 `class` 整个不在扫面里，而 `x.Foo` 这种属性读数还会替一个从不构造的同名类付账——那是 #85。
    """
    refs, defined = _py_refs(("src/wolfengine",), ("src", "tests", "scripts"))
    dead = sorted(n for n in defined
                  if refs.get(n, 0) == 0 and not n.startswith("__") and n != "main")
    assert dead == [], f"这些具名函数零读者（要么接上，要么删掉，别留着认领假读者）：{dead}"


DISPATCH = ("getattr", "hasattr", "setattr")
SPAN = re.compile(r"`([^`\n]*)`")
COMMAND_HEAD = re.compile(r"^(?:\.venv/bin/)?(?:python(?:\.\d+)? -c |wolf )")

# 生产链不点它名、唯一读者是手册里一条真命令的那些名字。每个都要能在被点名的那本手册里逐字敲出来。
MANUAL_EXITS = {"read_dir": "docs/metrics.md"}


def _manual_command_names() -> dict[str, list[str]]:
    """(标识符 -> `docs/*.md` + `README.md` 里把它写进一条**可粘贴命令**的那几行)。

    只认反引号里以 `python -c` 或 `wolf ` 开头的片段：手册的复现行是用户对产品下的单，而散文里提到
    一个函数名不是（`#142` 收过一批"看着像命令其实没有"的出处）。
    """
    out: dict[str, list[str]] = {}
    for f in sorted(Path("docs").glob("*.md")) + [Path("README.md")]:
        for no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for span in SPAN.findall(line):
                if not COMMAND_HEAD.search(span):
                    continue
                for tok in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", span):
                    out.setdefault(tok, []).append(f"{f}:{no}")
    return out


def _entry_point_names() -> set[str]:
    """`[project.scripts]` 里 `模块:名字` 的那个名字——它是读者，只是形状不是 Python 里的点名。"""
    import tomllib

    with open("pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    return {str(v).rsplit(":", 1)[-1] for v in data.get("project", {}).get("scripts", {}).values()}


def _module_defs_and_production_reads(
    src_roots: tuple[str, ...] = ("src",),
    read_roots: tuple[str, ...] = ("src", "scripts"),
):
    """(src 的**模块级** def 名字 -> 落点, 生产链读者计数, src 的模块级常量名 -> 落点)。

    读者只算两种：`Name`/`Attribute` 上的真名字，以及 `getattr(obj, "名字")` 里那一格字符串（按名字
    派发）。`__all__` 的一行字符串与 `row["键名"]` 都不算——出口清单不是调用者，键名与同名函数也是两
    回事（`#132`）。范围只到模块级：方法/property 用同一口径 22:28Z 重量是 **12 处**零生产读者，不是
    21:53Z 记的六处（那一趟的脚本已在提交后删净，无法复查它少在哪一维，所以这里只说重数出来的那一版），
    逐条处置另开 `#156`——每一处要先读它的孪生与金样本，不是一片能收的账。

    第三格 `#160` 收进来：模块级**赋值左边**的名字。这一族从 `#81` 起只认 `def`、`#156` 认方法、
    `#159` 认类、`#83` 认 import，`X = (...)` 这一形一直没进过名册。
    读者那一张表现在**扣掉了模块级赋值的左端**：常量的落点行自己就是一个 `ast.Name`，不扣的话每一具
    常量都自带一个读者，探测永不发光。两把尺在同一棵树上的差是实测过的（01:08:24Z）：不扣报 **0 处**
    零读者，扣了报 **3 处**（`events.KINDS`、`metrics.SEAT_REF`、`persona.SPEECH_ACTS`）。函数不受
    这一格影响——`def` 的名字不是 `ast.Name`，所以 `#155` 那条判据的读数一格没动。
    """
    readers: dict[str, int] = {}
    written: dict[str, int] = {}
    defs: dict[str, list[str]] = {}
    consts: dict[str, list[str]] = {}

    def bind(target: ast.expr, node: ast.stmt, f: Path) -> None:
        """模块级赋值的左端：记一次落点，也记一次"它不是读者"。"""
        if isinstance(target, (ast.Tuple, ast.List)):
            names = [e for e in target.elts if isinstance(e, ast.Name)]
        elif isinstance(target, ast.Name):
            names = [target]
        else:
            return
        for t in names:
            written[t.id] = written.get(t.id, 0) + 1
            consts.setdefault(t.id, []).append(f"{f}:{node.lineno}")

    def count(tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                key = node.attr
            elif isinstance(node, ast.Name):
                key = node.id
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in DISPATCH and len(node.args) > 1
                  and isinstance(node.args[1], ast.Constant)
                  and isinstance(node.args[1].value, str)):
                key = node.args[1].value
            else:
                continue
            readers[key] = readers.get(key, 0) + 1

    for root in read_roots:
        for f in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            if root in src_roots:
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defs.setdefault(node.name, []).append(f"{f}:{node.lineno}")
                    elif isinstance(node, ast.Assign):
                        for t in node.targets:
                            bind(t, node, f)
                    elif isinstance(node, ast.AnnAssign):
                        bind(node.target, node, f)
            count(tree)
    return defs, {k: v - written.get(k, 0) for k, v in readers.items()}, consts


def test_every_module_level_engine_helper_is_called_by_the_product_or_is_a_manual_exit():
    """`#155`：`#81` 那条闸门的读者名册里有 `tests/`，于是"只有自己的用例在养它"的生产码它能放行。

    现测三处（21:53Z）：`report.cluster_bootstrap_rate`（三个调用全在 `test_report_stats.py`，产物链
    只走 `_diff` 那一支）、`render_live.frame_text`（三十七处点名都在测试里，`watch()` 直接调 `draw`）、
    `metrics.read_dir`（src 里只剩 `__all__` 那一行，CLI 的目录入口是 `batch.read_arm`）。前两处已按
    "要么接上、要么删掉"处置完：`cluster_bootstrap_rate` 删了，连带它那条理论 deff 锚的断言一起没
    （`test_report_stats.py` 页眉记着这个缺口）；`frame_text` 搬进 `tests/live_frame.py`——读者只有测试
    的东西就住在测试侧。第三处的读者是手册的复现命令行，那是用户对产品下的单，所以走
    `MANUAL_EXITS` 点名豁免——豁免的两侧由另一条判据钉，它自己绿不代表没牙（见那条的正文）。

    范围与 `#81` 那条不同不是复制：那条回答"整棵树里有没有人点过这个名"，这一条回答"产品自己会不会
    走到它"。两把刀量过这个分别（22:11Z）：src 里加一个"只有测试在养"的 helper 时**只红这一条**，
    `#81` 那条不红；同一个 helper 连测试都不点它名时两条一起红。
    """
    defs, readers, _ = _module_defs_and_production_reads()
    manual = _manual_command_names()
    entries = _entry_point_names()
    unexplained = []
    for name, places in sorted(defs.items()):
        if name.startswith("__") or name in entries or readers.get(name, 0):
            continue
        doc = MANUAL_EXITS.get(name)
        if doc and any(p.startswith(f"{doc}:") for p in manual.get(name, [])):
            continue
        unexplained.append(f"{name}（落点 {', '.join(places)}；手册命令 {len(manual.get(name, []))} 处）")
    assert unexplained == [], (
        f"这些模块级 helper 生产链里没人调用：{unexplained}——要么接上，要么删掉，"
        f"要么把它写进 MANUAL_EXITS 并在手册里留下一条真能敲的命令")


def test_the_export_list_is_not_a_caller_and_the_only_name_it_would_have_saved_is_declared():
    """`__all__` 与字典键替谁付了账，要逐名点名——这一条就是那笔账的名单。

    两边都比：宽松口径（任何等于名字的字符串常量都算读者，`#81` 那条的读法，但把它的 `tests/` 那一半
    摘掉——这一条只管"字符串算不算读者"，掺进范围问题就分不清是谁的牙）与严格口径（只认派发用的字符串）。
    差额必须恰好等于 `MANUAL_EXITS`，多一个名字说明有人把豁免藏进出口清单，少一个名字说明那处豁免
    已经不需要了（接上了、或那行 `__all__` 被摘了）——两种都是这一条该红。
    本条落笔即绿：它的牙由电池里"把 `read_dir` 接进 `cli.py`"和"摘掉 `__all__` 那一行"两具刀量，
    不靠它自己绿着充数。
    """
    defs, strict, _ = _module_defs_and_production_reads()
    loose, _ = _py_refs(("src/wolfengine",), ("src", "scripts"))
    string_paid = sorted(n for n in defs if strict.get(n, 0) == 0 and loose.get(n, 0) > 0)
    assert string_paid == sorted(MANUAL_EXITS), (
        f"只靠字符串常量才被算成有读者的是 {string_paid}，点名豁免的是 {sorted(MANUAL_EXITS)}"
        f"——两边不一致就是口径漂移了")


def _unreached_consts(
    src_roots: tuple[str, ...] = ("src",),
    read_roots: tuple[str, ...] = ("src", "scripts"),
) -> tuple[dict[str, list[str]], list[str]]:
    """常量层的探测本体：(名册, 零生产读者的名字)。两条判据共用这一具，夹具不许另写一份。"""
    _, readers, consts = _module_defs_and_production_reads(src_roots, read_roots)
    unreached = sorted(n for n, places in consts.items()
                       if not n.startswith("__") and readers.get(n, 0) <= 0)
    return consts, unreached


CONST_EXITS: dict[str, str] = {}


def test_no_module_level_engine_constant_is_left_without_a_reader():
    """`#160`：`#81`→`#159` 那一族数过函数、方法、类、import，唯独没数过**模块级赋值左边的名字**。

    这一格不是补一个"更完整"的名单，是收一处真话：`metrics.SEAT_REF` 与 `persona.SPEECH_ACTS` 都从
    初始提交 `aa0d646` 活到今天，中间那一整轮死名清理（`#81` 九处死函数、`#83` 导入、`#85` 同名类、
    `#155` 生产链、`#156` 方法层、`#159` 类层）一次都没看过它们。落笔时现测 3 处零读者（01:08:24Z），
    逐条处置见归档那一片。

    名册规模先钉住：`consts` 是这一条唯一"扫到了东西"的证据，收集坏了它会空着，而空名册上的"零读者
    名单为空"是真话——所以地板取 80（01:26:57Z 现测 94 个名字、96 条落点，含双下划线那一格；同一趟
    另写了一份不共用这把尺的 ast 复算，两侧名字集相同）。

    双下划线那一格豁免着的真零读者名字，01:33:36Z 实测是两具（`__all__`、`__version__`）：前者是出口
    清单，`#155` 已经定过性——清单不是调用者，豁免是对的。后者那一具 `#161` 删掉了（它是 `pyproject.toml`
    那句 `version` 的第二份抄本，两格今天相等而没有任何东西在核对，且零生产读者），所以这一格现在豁免的
    只有 `__all__`。版本号"只住一处"这件事不再靠这段散文兜着，由下面那两条用例钉着。
    """
    consts, unreached = _unreached_consts()
    assert len(consts) >= 80, f"模块级常量只数到 {len(consts)} 个，多半是收集坏了"
    extra = sorted(set(unreached) - set(CONST_EXITS))
    stale = sorted(set(CONST_EXITS) - set(unreached))
    assert not extra and not stale, (
        f"这些模块级常量生产链里没人读：{extra}（落点见名册）；"
        f"这些点名豁免已经不需要了：{stale}——接上了或删掉了就把它从 CONST_EXITS 摘掉。"
        f"要么接上、要么删掉，别留着让它装作产品的一部分")


def _version_homes(corpus: dict[str, str]) -> list[str]:
    """哪些文件自己给版本号做了一次赋值——`__version__` 与 `VERSION` 两种写法都算抄第二份。"""
    home = re.compile(r"""^[A-Za-z_]*version[A-Za-z_]*\s*=\s*["']""", re.M | re.I)
    return sorted(name for name, text in corpus.items() if home.search(text))


def test_the_version_string_has_exactly_one_home_in_the_repo():
    """`#161`：`__init__.py` 顶上曾有 `__version__ = "0.1.0"`，那是 `pyproject.toml` 那句的第二份抄本。

    两格当时相等，而没有任何东西在核对——`#160` 的 K3 摘掉双下划线豁免时它现形，零生产读者。这一条钉的
    是"只住一处"，不是"出厂版本必须等于 0.1.0"：那个值归 `pyproject.toml` 自己管，本包不复制它。
    """
    corpus = {str(p): p.read_text(encoding="utf-8")
              for p in sorted(Path("src/wolfengine").rglob("*.py"))}
    assert len(corpus) >= 10, f"src 只数到 {len(corpus)} 个模块，多半是扫面坏了"
    homes = _version_homes(corpus)
    assert not homes, f"版本字符串在 src/ 里有第二份：{homes}，而出厂口径只该住在 pyproject.toml"


def test_the_version_scanner_fires_on_a_second_copy_and_not_on_prose():
    """判据两侧都有读者：两种抄法都报，注释、属性读取与函数定义都不报。

    删掉 `__version__` 之后真语料上是零命中，所以"这条闸门有用"只能由这一格喂假数据来证——不然它
    可以是一把永远不开火的枪。
    """
    hits = _version_homes({
        "a/__init__.py": '__version__ = "0.1.0"\n',
        "b/m.py": "VERSION = '2'\n",
        "c/m.py": '# __version__ = "0.1.0" 这一句是注释\n',
        "d/m.py": "v = cfg.version\n",
        "e/m.py": "def get_version() -> str:\n    return '1'\n",
    })
    assert hits == ["a/__init__.py", "b/m.py"], hits


def test_no_module_level_test_helper_is_left_without_a_caller_or_an_injector():
    """`#165`：引擎那一面被 `#81`/`#155`/`#156`/`#159`/`#160` 扫过，`tests/`+`scripts/` 自己这一面没有过。

    03:28:01Z 现测：46 个文件、312 处模块级 helper 定义（`conftest.py` 算在内）里零读者 **0 处**，其中
    17 具是 `@pytest.fixture`（名字去重后 14 个）——它们全靠"被某个函数按参数名注入"这一条口径活着，
    而这一条正是这条尺比引擎那一面多出来的一维：pytest 注入不留任何 AST 上的点名。所以删掉一条用例、
    把它独占的那个 fixture 变成孤悬（17 具里有 8 具只有这一个读者），pytest 一句警告都不给，只有这一格会红。

    限界两条，与 `#155` 同族：它只数名字，所以**互相调用的一簇死代码**各自都有读者、它看不见；它也不问
    "被调用的那一条用例还跑不跑"（`skip`/`xfail` 那一族是另一回事）。按名字数还会被重名蒙一次：一具 helper
    与别处一个同名变量撞了，注入那一支会替它付账——这与 `#85` 在类层抓到过的那一形同族。现测有 4 处这样撞的
    （`test_golden_game.py: b`、`test_legality.py: act`/`night`、`test_wiring.py: _prompt`），四具都另有
    真读者，所以严格口径在这里零代价；判据把放行限定在 fixture 上，这一形就没法替一具死 helper 付账。
    312 处定义里 18 个名字被抄了 44 处（多出来 26 处），名册按 `"文件: 名字"` 建键正是为了不让这 26 处互相顶替。

    地板取 250（现测 312 处定义、46 个文件）：名册空掉的"零处零读者"是真话，所以扫面坏了必须先红。
    """
    trees = {str(f): ast.parse(f.read_text(encoding="utf-8"))
             for root in ("tests", "scripts") for f in sorted(Path(root).rglob("*.py"))}
    roster, dead = _unreached_test_helpers(trees)
    assert len(roster) >= 250, f"名册只数到 {len(roster)} 具，多半是扫面坏了"
    assert dead == [], f"这些测试侧/脚本侧的模块级 helper 没人调用、也没人按名字注入：{dead}"


def _unreached_test_helpers(trees: dict[str, ast.Module]) -> tuple[list[str], list[str]]:
    """(名册, 零读者名单)，两边都是 `"文件: 名字"` 的排序表。

    读者四种，都从 AST 上导出来、没有一个名字是念出来的：被调用（`Name` 或 `Attribute` 位上的函数名）、
    被当实参递出去、被当装饰器点名，以及 **fixture 被某个函数按参数名注入**。最后这一种只给
    `@pytest.fixture` 装饰着的那些名字放行——别的名字撞上某个参数名只是重名，把它算成读者就是 `#132`
    那个"按键名数读者"的老错（同名的局部变量会替一具死 helper 付账）。
    """
    helpers: dict[str, bool] = {}
    for file, tree in trees.items():
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test_"):
                dec = [ast.unparse(d) for d in node.decorator_list]
                is_fixture = any(d == "fixture" or d.startswith("pytest.fixture") for d in dec)
                helpers[f"{file}: {node.name}"] = is_fixture

    called: set[str] = set()
    injected: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
                if name:
                    called.add(name)
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    if isinstance(arg, ast.Name):
                        called.add(arg.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    if isinstance(d, ast.Name):
                        called.add(d.id)
                    elif isinstance(d, ast.Attribute):
                        called.add(d.attr)
                    elif isinstance(d, ast.Call) and isinstance(d.func, (ast.Name, ast.Attribute)):
                        called.add(d.func.id if isinstance(d.func, ast.Name) else d.func.attr)
                for a in list(node.args.args) + list(node.args.kwonlyargs):
                    injected.add(a.arg)

    dead = [key for key, is_fixture in helpers.items()
            if key.rsplit(": ", 1)[1] not in called and not (is_fixture and key.rsplit(": ", 1)[1] in injected)]
    return sorted(helpers), sorted(dead)


def test_the_helper_probe_counts_injection_and_decoration_as_readers():
    """`#165` 的夹具：零读者名单在喂假数据的七档上两侧都要对——尤其"没人注入的 fixture"必须报。

    真语料上是零命中（03:28:01Z 现测：`tests/`+`scripts/` 里 312 处模块级、非 `test_` 的 helper 定义，
    按"被调用 / 被当实参递出 / 被装饰器点名 / 被某个函数当参数名注入"四种读法全都有人读），所以这条
    闸门有没有牙只能由这一格证。`airtight` 那一形在这里是 `alpha`：只被注入一处——把它那个用例删掉，
    fixture 就成孤悬，而 pytest 对此一个字的警告都不会给。

    七档是一档一维，各自都是真语料里**独占数最少或为零**的那一维：装饰器点名那一支在真语料上撑 0 具、
    经属性调用那一支撑 12 具但独占 0 具——所以这两支有没有牙，除了这一格没有第二个证人。

    `f/shadow.py` 问的是注入那一维**能走多远**：一具没被 `@pytest.fixture` 装饰的 helper，名字撞上某个
    参数名，算不算被读了？这一格要求它算死。放行非 fixture 的注入，就是 `#132` 那个"按键名数读者"的老错
    ——同名参数会替一具没人调的函数付账。
    """
    corpus = {
        "f/injected.py": "@pytest.fixture\ndef alpha():\n    return 1\n\ndef test_uses(alpha):\n    assert alpha\n",
        "f/orphan.py": "@pytest.fixture\ndef lonely():\n    return 1\n",
        "f/helper.py": "def never():\n    return 1\n",
        "f/decorated.py": "def deco(fn):\n    return fn\n\n@deco\ndef test_real():\n    pass\n",
        "f/passed_as_arg.py": "def target():\n    return 1\n\ndef test_caller():\n    hand(target)\n",
        "f/via_attr.py": "def got():\n    return 1\n\ndef test_attr():\n    holder().got()\n",
        "f/shadow.py": "def alpha():\n    return 2\n",
    }
    trees = {name: ast.parse(src) for name, src in corpus.items()}
    roster, dead = _unreached_test_helpers(trees)
    assert dead == ["f/helper.py: never", "f/orphan.py: lonely", "f/shadow.py: alpha"], dead
    assert len(roster) == 7, f"名册数到 {roster}，这一格的夹具面应该有七具非 test_ 定义"


def test_no_test_side_helper_is_reachable_only_from_dead_code():
    """`#166`：把 `#165` 自己写下的第一条限界变成断言——只被死代码点名的那一批。

    03:53:14Z 现测：同一批 46 个文件里 314 具 helper（`#165` 在 03:28:01Z 量到 312，多的两具就是这一片
    自己新写的判据），从入口（每条模块级用例，加上每个文件的顶层语句本身）做传递闭包之后，
    **到不了的 0 具**。名字口径说"都有读者"，可达性口径也说"都有活读者"，两把尺今天 agreeing；这一格要钉
    的是下一簇长出来的那一刻：删掉一条用例而它调的那具 helper 还互相调用着，`#165` 会整簇放行（读者名单
    非空），只有这一格会红。

    入口把"模块级语句本身"算进去不是宽纵：pytest 收集一个文件就会执行它的顶层，`BOARD = _board()` 这一行
    是真的会跑的代码。第一次探针没建这个结点，于是四处**假死**（`_board`、`_blob`、`A`、
    `looks_like_gateway_error`）——它们的调用点全在顶层，量出来是 4 具到不了，逐条 grep 后一句都站不住。
    """
    trees = {str(f): ast.parse(f.read_text(encoding="utf-8"))
             for root in ("tests", "scripts") for f in sorted(Path(root).rglob("*.py"))}
    roster, unreachable = _unreachable_test_helpers(trees)
    assert len(roster) >= 250, f"名册只数到 {len(roster)} 具，多半是扫面坏了"
    assert unreachable == [], f"这些 helper 只有死代码会调到，从任何入口都到不了：{unreachable}"


def _unreachable_test_helpers(trees: dict[str, ast.Module]) -> tuple[list[str], list[str]]:
    """(名册, 从任何入口都到不了的 helper)，两边都是 `"文件: 名字"` 的排序表。

    读者集合与 `#165` 逐字相同（被调用 / 被当实参递出 / 被装饰器点名 / 被某个函数按参数名注入），差别只在
    **读者的读者**也要算：一具 helper 只有被"到不了的具"点名，它自己也到不了。注入这一维在这里必须降成
    一条从**请求方**出发的边——`#165` 把它当全局豁免（名字出现在任何参数表里就放行），所以一具只被死簇
    请求的 fixture 在那边是活的。按裸名字解析，跨文件的同名会互相顶替，因此这只会漏报、不会误报。

    入口两种：模块级的 `test_*` 定义，和每个文件的顶层语句本身（`"文件: <module>"` 那个结点）。后者不是
    宽纵——pytest 收集一个文件就执行它的顶层。`03:52:43Z` 现测：曾有的第三种入口（脚本
    `if __name__ == "__main__"` 那块点到的具）**删掉后两个语料逐字不变**，因为那块本来就是顶层语句、
    已经挂在 `<module>` 结点的边上了——一条没有读者的判据分支，被自己的电池（K3 全绿）当场抓了回来。
    类体与被调用的方法不在结点集里（与 `#165` 同口径：只数模块级的 `def`）。
    """
    edges: dict[str, set[str]] = {}
    by_name: dict[str, list[str]] = {}
    entries: set[str] = set()
    for file, mod in trees.items():
        top = f"{file}: <module>"
        edges.setdefault(top, set())
        entries.add(top)
        for stmt in mod.body:
            key = top
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.col_offset == 0:
                key = f"{file}: {stmt.name}"
                by_name.setdefault(stmt.name, []).append(key)
                edges.setdefault(key, set())
                if stmt.name.startswith("test_"):
                    entries.add(key)
            edges[key] |= _names_read(stmt)
    live = set(entries)
    frontier = sorted(entries)
    while frontier:
        for name in edges.get(frontier.pop(), ()):
            for key in by_name.get(name, ()):
                if key not in live:
                    live.add(key)
                    frontier.append(key)
    helpers = {k for k in edges
               if not k.endswith(": <module>") and not k.rsplit(": ", 1)[1].startswith("test_")}
    return sorted(helpers), sorted(helpers - live)


def _names_read(node: ast.AST) -> set[str]:
    """一个结点里被"读"到的裸名字：调用位、实参位、装饰器位、参数名（注入），与 `#165` 一致。"""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            out.add(fn.attr if isinstance(fn, ast.Attribute) else fn.id)
            for arg in list(sub.args) + [k.value for k in sub.keywords]:
                if isinstance(arg, ast.Name):
                    out.add(arg.id)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in sub.decorator_list:
                out.add(d.func.id if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                        else d.func.attr if isinstance(d, ast.Call) else
                        d.id if isinstance(d, ast.Name) else d.attr)
            for a in list(sub.args.args) + list(sub.args.posonlyargs) + list(sub.args.kwonlyargs):
                out.add(a.arg)
    return out


def test_the_reachability_probe_names_a_cluster_the_name_probe_forgives():
    """`#166` 的夹具：同一棵树喂两把尺，宽的那把必须报出窄的那把放行的那一形。

    真语料上两把都是 0 处，所以这格是这条判据唯一的牙。夹具里 `helper_a`/`helper_b` 互为读者、
    `only_by_dead` 只被这个死簇按参数名注入，三具在 `#165` 的口径下全都有读者——`helper_b` 有人调、
    fixture 有人注入，只有 `lonely` 和那条"从别处 import 进来却没调用"的 `helper_a` 露出来。可达性口径
    把它们连根报掉。`s/tool.py` 那一档证明的是**顶层语句算入口**这一维：`entry_from_main` 只被
    `if __name__ == "__main__"` 那块调到，而那块就是文件自己的顶层代码——撤掉 `<module>` 入口的那具刀
    （K1）会让这两具和真语料上那四处假死一起回到名单里。
    """
    corpus = {
        "t/live.py": "@pytest.fixture\ndef only_by_dead():\n    return 1\n"
                     "\ndef helper_a(x):\n    return helper_b(x)\n"
                     "\ndef helper_b(only_by_dead, x):\n    return only_by_dead + x\n"
                     "\ndef lonely():\n    return 3\n"
                     "\ndef test_real():\n    return 1\n",
        "t/entry.py": "from live import helper_a\n\nMODULE_LEVEL = 5\n"
                      "\ndef test_reads_module_level():\n    return MODULE_LEVEL\n",
        "s/tool.py": "def entry_from_main():\n    return unused_tool()\n"
                     "\ndef unused_tool():\n    return 2\n"
                     '\nif __name__ == "__main__":\n    entry_from_main()\n',
    }
    trees = {name: ast.parse(src) for name, src in corpus.items()}
    roster, unreachable = _unreachable_test_helpers(trees)
    assert unreachable == ["t/live.py: helper_a", "t/live.py: helper_b",
                           "t/live.py: lonely", "t/live.py: only_by_dead"], unreachable
    assert len(roster) == 6, f"名册数到 {roster}，这一格的夹具面应该有六具非 test_ 定义"
    assert _unreached_test_helpers(trees)[1] == ["t/live.py: helper_a", "t/live.py: lonely"], (
        "窄的那把尺今天报了四具——它已经不窄了，这条断言的对照面要重新量")


def test_the_constant_probe_counts_a_definition_itself_as_no_reader(tmp_path):
    """夹具：定义那一行自己不是读者，只有测试在养的名字也不算生产读者。

    这一条钉的是判据里最容易坏的一格。`X = (...)` 的左端在 ast 里就是一个 `ast.Name`，跟真正的读取
    同形，所以不扣定义的话每一具常量都自带一个读者：同一棵树、同一把尺，不扣报 0 处、扣了报 3 处
    （01:08:24Z 在真语料上量的那一对）。把减法拆掉的那具刀要能红这一条，也要能红真语料那一条。

    `ONLY_TEST` 的读者住在 `outside/`，而 `read_roots` 里没有它——这一格复现的是 `#155` 的决定：
    "只有自己的用例在养它"不算产品读者。`orphan` 那一具函数同时被数，是拿来证明第三格没有改动
    `#155` 那条判据读的那一张表（函数名不是 `ast.Name`，扣不到它）。
    """
    pkg = tmp_path / "pkg"
    scripts = tmp_path / "script_side"
    outside = tmp_path / "outside"
    for d in (pkg, scripts, outside):
        d.mkdir()
    (pkg / "prod.py").write_text(
        "USED = 1\nDEAD = 2\nONLY_TEST = 3\n\n\ndef show():\n    return USED\n\n\n"
        "def orphan():\n    return 0\n", encoding="utf-8")
    (pkg / "consumer.py").write_text(
        "from prod import USED\nprint(USED)\n", encoding="utf-8")
    (scripts / "run.py").write_text("import prod\nprint(prod.show())\n", encoding="utf-8")
    (outside / "only_test_user.py").write_text("import prod\nassert prod.ONLY_TEST\n",
                                               encoding="utf-8")

    _, readers, _ = _module_defs_and_production_reads(
        src_roots=(str(pkg),), read_roots=(str(pkg), str(scripts)))
    roster, unreached = _unreached_consts((str(pkg),), (str(pkg), str(scripts)))

    assert sorted(roster) == ["DEAD", "ONLY_TEST", "USED"], sorted(roster)
    assert readers["USED"] >= 1 and readers["show"] >= 1
    assert readers["DEAD"] == 0 and readers["ONLY_TEST"] == 0
    assert readers.get("orphan", 0) == 0
    assert unreached == ["DEAD", "ONLY_TEST"], unreached


def _class_defs_and_reads():
    """(类体内每个 `def`/property 的 `文件::类.名字` -> (生产链读数, 测试侧读数, 落点行))。`#156` 的尺。

    与 `_module_defs_and_production_reads` 的分别有两层。**什么算读者**沿用那一版：`ast.Attribute` 上的
    真名字，加 `getattr(obj, "名字")` 那一格字面量（`batch.py` 的 `axis_fields` 靠这一格活着，而那两处
    写的是三参数的 `getattr(obj, "名字", None)`——本尺的第一版只认两参数，就把这两个名字判成了零读者）。
    **键的形状**换了：按 `文件::类.名字` 数，不按裸名字——`as_dict` 曾在
    `GameResult` 与 `PublicState` 上各有一具，一个有测试读者、一个连读者都没有，按名字数会把这两件事
    混成一个数（`#85` 的"同名替付账"在类内那一侧的同一形）。
    """
    prod: dict[str, int] = {}
    tests: dict[str, int] = {}

    def tally(tree: ast.AST, bucket: dict[str, int]) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                key = node.attr
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in DISPATCH and len(node.args) > 1
                  and isinstance(node.args[1], ast.Constant)
                  and isinstance(node.args[1].value, str)):
                key = node.args[1].value
            else:
                continue
            bucket[key] = bucket.get(key, 0) + 1

    defs: dict[str, list[int]] = {}
    for root, bucket in (("src", prod), ("scripts", prod), ("tests", tests)):
        for f in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            tally(tree, bucket)
            if root != "src":
                continue
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                for node in cls.body:
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if node.name.startswith("__"):
                        continue
                    defs.setdefault(f"{f.name}::{cls.name}.{node.name}", []).append(node.lineno)
    return {k: (prod.get(k.rsplit(".", 1)[1], 0), tests.get(k.rsplit(".", 1)[1], 0), v)
            for k, v in defs.items()}


def _cited_path_exists(text: str) -> bool:
    """处置那句话里是否点了一个真实存在的文件——按名字找，不要求写全路径。

    找的是"这句话有落点"，不是"路径写法对"：`tests/test_rules.py` 与 `test_rules.py` 都算。
    """
    for p in re.findall(r"[\w/.-]+\.(?:py|md)", text):
        name = Path(p).name
        if Path(p).exists():
            return True
        if any(f.name == name for root in ("src", "tests", "scripts", "docs")
               for f in Path(root).rglob(name)):
            return True
    return False


# 方法/property 层的零生产读者名册：每一格都要写出处置和它落在哪一个文件上（`#156`）。
# 处置词只有五个：删 / 搬 / 接 / 留 / 待判。"留"是给真有产物理由的（`MANUAL_EXITS` 那一形），
# 而连测试都不点它名的那一格不许写"留"。
METHOD_TRIAGE: dict[str, str] = {
    "info.py::Percept.by_kind":
        "待判：七处读者全在 tests/test_info_isolation.py 与 tests/test_vote_wave.py；孪生 `tail` 是有生产"
        "读者的（人那一屏在 human.py 里就调它），`window` 那一格的名字被 plan.window 顶着——按名字数读数"
        "分不出是哪一具，见下面限界那句",
    "roles.py::Board.team_counts":
        "待判：唯一读者是 tests/test_rules.py 那句 3/3/3 的板面核对，胜负判据读的是别处的 team 键空间"
        "（`#80` 那一族），先确认它不是那判据的第二份实现",
    "legality.py::Verdict.reason":
        "待判：src/ 里那几处只读 violations 那个列表，把它们拼成一句的只有这个 property，而它唯一的"
        "读者是 tests/test_legality.py 的断言——拼句要不要成为产物的一部分是决定，不是清理",
    "rules.py::NightResolution.dead_seats":
        "待判：产物链读的是 `deaths` 那张表本身（`src/wolfengine/phases.py` 夜里结算那一支就是逐条 "
        "`for d in res.deaths`），把表压成座位号只有测试在用——23:42:16Z 现测 src+scripts 零处、"
        "tests/test_rules.py 十处，其中一处专门断言这两者一致。这一格是 `#159` 删掉 `PublicState` "
        "时**级联**长出来的：同名的 `GameState.dead_seats` 原本唯一的生产读者就坐在被删的 `public_state()`"
        " 体内，那一具删了它便彻底没人读，同一趟删净（名册里不留它，因为树上已经没有这个 def）",
    "state.py::GameState.teammates_of":
        "待判：docs/iterations.md 里 `#133` 那一节写明它与 `Percept.teammates()` 不是同一个判据（按"
        "『存活过滤 vs 念发牌那一刻的名册』），卡片印的是后者；两具哪一具该活下来要先定，不是删得掉的",
}
# 限界：测试侧的读数按**名字**数，不按 def 数。同名两具（`as_dict` 曾住在 `GameResult` 与 `PublicState`
# 各一具）里究竟哪一具有读者这条判不出来——所以『零读者』那一格只敢在**连名字都没人点**时强制『删/搬』，
# 名字被点过但可能不是点它这一具时，只能由处置那一格自己写清落点。两具都没有读者（22:39Z 现测：
# `GameResult.as_dict` 的 `vars(self)` 那行没有任何调用者），已分别随 `#156` 与 `#159` 删掉。


def test_the_method_layer_names_every_zero_production_reader_and_each_carries_a_disposition():
    """`#156`：`#155` 那条只数模块级 `def`，方法 / property 那一层它整个看不见。

    删之前那棵树（HEAD `9aa078b`）上现测（22:36Z）：99 个类内 `def`、86 个名字，按 `文件::类.名字` 数
    生产链零读者的 13 具——比 `#155` 限界里那句『六处』多出一倍，那一趟的脚本没留读者；22:28Z 重数的
    是 12 个**名字**，13 具与 12 名的差就是 `as_dict` 住在两具类里。这一片删掉谁都不读的 7 具，剩下的
    由本条自己数（23:04Z：92 具、80 名、零生产读者 6 具）。这一条不要它们都"有读者"，只要**每一具都有
    一条登记过的处置**：名册与 `METHOD_TRIAGE` 的键必须两侧相等
    （多一格＝新长出来的没登记，少一格＝登记的那个名字已经不在这棵树上了），每一格的落点路径必须
    存在，而**生产链和测试里都没有读者**的那几具不许标"留"——那正是 `#155` 量的第一种腐烂，只是
    住在类里。
    """
    roster = _class_defs_and_reads()
    dead = {k: v for k, v in roster.items() if v[0] == 0}
    assert sorted(dead) == sorted(METHOD_TRIAGE), (
        f"零生产读者的方法与登记的名册不是一份：只在树上 {sorted(set(dead) - set(METHOD_TRIAGE))}，"
        f"只在名册里 {sorted(set(METHOD_TRIAGE) - set(dead))}")
    nowhere, sloppy = [], []
    for name, (reads_p, reads_t, places) in sorted(dead.items()):
        verdict = METHOD_TRIAGE[name]
        if reads_p == 0 and reads_t == 0 and not verdict.startswith(("删", "搬")):
            nowhere.append(f"{name}（落点 {places}）")
        if not _cited_path_exists(verdict):
            sloppy.append(f"{name}：{verdict}")
    assert not nowhere, f"这些方法生产链和测试都不读它，不能登记成『留』：{nowhere}"
    assert not sloppy, f"这些格的处置没写出落点（或落点那个文件不存在）：{sloppy}"


def _field_defs_and_reads(
    src_roots: tuple[str, ...] = ("src",),
    read_roots: tuple[str, ...] = ("src", "scripts"),
    test_roots: tuple[str, ...] = ("tests",),
) -> tuple[dict[str, tuple[int, int, list[int]]], list[str]]:
    """字段层的探测本体：(名册 `文件::类.字段` -> (生产读数, 测试读数, 落点行), 零读者字段)。
    名册与夹具两条判据共用这一具，夹具不许另写一份尺（`#153`）。

    与 `#156` 那把方法层的尺差三格，每一格都是这一层特有的：

    * **落盘回读算读者**：`row["名字"]` 与 `meta.get("名字")`。dataclass 整份 `asdict()` 出去、
      读侧再按键取回，是字段最常见的活法，方法层没有这一形。
    * **写盘不算读者**：dict 字面量里的键、构造时的 `名字=v` 关键字、类体里那行注解的左端——
      同 `#160` 那条"定义自己不是读者"，字段这一层还要多扣两种写。
    * **src 自己点名惰性的那两张表算读者**：`config.INERT_FIELDS` 与 `INERT_LEAVES`（嵌套那半边
      写成 `tokens.warn`，取末段）。从源码 import 而不是在这里抄第二份名单。
    """
    inert = {*(INERT_FIELDS), *(leaf.rsplit(".", 1)[-1] for leaf in INERT_LEAVES)}
    readers: dict[str, int] = {}
    test_readers: dict[str, int] = {}
    defs: dict[str, list[int]] = {}

    def bump(bucket: dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1

    def count_reads(tree: ast.AST, bucket: dict[str, int]) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if isinstance(node.ctx, ast.Load):
                    bump(bucket, node.attr)
            elif isinstance(node, ast.Call):
                fn = node.func
                if (isinstance(fn, ast.Name) and fn.id in DISPATCH and len(node.args) > 1
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, str)):
                    bump(bucket, node.args[1].value)
                elif (isinstance(fn, ast.Attribute) and fn.attr == "get" and node.args
                      and isinstance(node.args[0], ast.Constant)
                      and isinstance(node.args[0].value, str)):
                    bump(bucket, node.args[0].value)
            elif (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                  and isinstance(node.slice.value, str)):
                bump(bucket, node.slice.value)

    def py_files(root: str):
        for f in sorted(Path(root).rglob("*.py")):
            if "__pycache__" not in f.parts:
                yield f, ast.parse(f.read_text(encoding="utf-8"))

    for root in read_roots:
        for f, tree in py_files(root):
            count_reads(tree, readers)
    for root in test_roots:
        for f, tree in py_files(root):
            if root not in read_roots:
                count_reads(tree, test_readers)
    for root in src_roots:
        for f, tree in py_files(root):
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                for node in cls.body:
                    if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                            and not node.target.id.startswith("__")):
                        defs.setdefault(f"{f.name}::{cls.name}.{node.target.id}", []).append(
                            node.lineno)

    roster = {k: (readers.get(k.rsplit(".", 1)[1], 0) + (1 if k.rsplit(".", 1)[1] in inert else 0),
                  test_readers.get(k.rsplit(".", 1)[1], 0), v) for k, v in defs.items()}
    return roster, sorted(k for k, v in roster.items() if v[0] == 0)


# 字段层的零读者名册：每一格都要写处置和落点（`#172`）。处置词沿用 `#156` 那五个：
# 删 / 搬 / 接 / 留 / 待判。
FIELD_TRIAGE: dict[str, str] = {
    "config.py::RegionBudget.a_hard":
        "留：这六格由 src/wolfengine/metrics.py 的 `REGION_CAP_KEYS` 按**算出来的键**回读（那张表把 "
        "'A' 映到 'a_hard'，`src/wolfengine/game.py` 里 `asdict(regions)` 整份落进 meta）——本尺只认"
        "字面量下标，看不见这一形，所以它是**登记**下来的留，不是证出来的，见限界第二条",
    "config.py::RegionBudget.b0": "留：同 `a_hard`，`REGION_CAP_KEYS` 的 'B0' 那一格，见 src/wolfengine/metrics.py",
    "config.py::RegionBudget.b1": "留：同 `a_hard`，`REGION_CAP_KEYS` 的 'B1' 那一格，见 src/wolfengine/metrics.py",
    "config.py::RegionBudget.c_persona":
        "留：同 `a_hard`，`REGION_CAP_KEYS` 的 'C1' 那一格；`src/wolfengine/config.py` 的注释另写明它"
        "改的是 audit 的超额判定、不动发出去的字节",
    "config.py::RegionBudget.c_private": "留：同 `a_hard`，`REGION_CAP_KEYS` 的 'C3' 那一格，见 src/wolfengine/metrics.py",
    "config.py::RegionBudget.c_task": "留：同 `a_hard`，`REGION_CAP_KEYS` 的 'C4' 那一格，见 src/wolfengine/metrics.py",
    "rules.py::VoteResult.top_seats":
        "待判：`src/wolfengine/rules.py` 里由 `tied_seats` 写入，全仓库零读者，而并列这件事已经由 "
        "`tied_seats` 那张表本身落盘——先确认这格不是第二份抄本，见 src/wolfengine/rules.py",
    "rules.py::NightResolution.peace":
        "待判：这一格已经有票了——#88 问的是『平安夜要不要在公开产物里留痕』，那是处置变更不是清理，"
        "见 src/wolfengine/rules.py 与 docs/iterations.md 里 `#88` 那一节",
    "state.py::GameState.hunter_seat":
        "待判：`src/wolfengine/game.py` 发牌时算出并写进状态，之后没人读（猎人那一枪走的是别的路径，"
        "见 src/wolfengine/phases.py）——接进产物还是删掉是决定，见 src/wolfengine/state.py",
    "state.py::LegalSet.assigned_target":
        "待判：`src/wolfengine/phases.py` 里 `replace(..., assigned_act=act, assigned_target=target)` "
        "成对写，`#68` 只给 `assigned_act` 补了读者（服从率），target 那一半零读者，见 src/wolfengine/state.py",
    "assemble.py::Prompt.legal_acts":
        "待判：`src/wolfengine/assemble.py` 里 `Prompt` 构造时写入，而落盘那格走的是同文件 "
        "`payload_for_log` 的白名单、不含它；`src/wolfengine/schema.py` 里同名的那一格是**函数参数**"
        "不是这一具（`#156` 记过的同名替付账搬到字段层），见 src/wolfengine/assemble.py",
    "assemble.py::Prompt.legal_targets":
        "待判：同 `legal_acts`，成对写入、零读者、不在落盘白名单里，见 src/wolfengine/assemble.py",
    "batch.py::BatchResult.rows":
        "待判：落盘那一格 `\"rows\"` 写的是同函数里的**局部变量**（`src/wolfengine/batch.py` 里 "
        "`\"n_logs\": len(rows), \"rows\": rows`），字段这一份是它的第二份抄本且没人回读，见 src/wolfengine/batch.py",
    "info.py::Percept.at_seq":
        "待判：`src/wolfengine/metrics.py` 与 `src/wolfengine/agent.py` 里那两处 `at_seq=` 都是 "
        "`percept_for` 的**入参**（定义在 `src/wolfengine/info.py`），不是这格的读者——同名替付账，见 info.py",
    "schema.py::ParseOutcome.raw_used":
        "待判：`src/wolfengine/schema.py` 里两处写入（`raw_used=cand`）、零读者——它是『最后发出去的是"
        "哪一个候选』的出处格，要不要进产物链由 `#114` 那张载荷普查说了算，见 src/wolfengine/schema.py",
}
FIELD_VERDICTS = ("删", "搬", "接", "留", "待判")


def test_the_field_layer_names_every_zero_reader_field_and_each_carries_a_disposition():
    """`#172`：`#81`→`#166` 那一族数过函数、方法、类、导入、模块级常量，唯独没数过**类体里
    带注解的字段**。本条自己数：296 格字段、15 格零生产读者（其中 5 格连测试也不点它名，那是
    下面九格"待判"里的五格）。`#172` 那份不共用这把尺的复算当时报 24 格（08:14:58Z 那棵树比
    现在多两格），多的 7 格正是这具尺多认的那两形：`row["名字"]` 式的落盘回读与 src 自己那两张
    INERT 表。

    本条不要它们都"有读者"，只要**每一格都有一条登记过的处置**：名册与本条数的零读者两侧相等
    （多一格＝新长出来没登记，少一格＝登记的那格已不在树上）、处置词必须是那五个之一、落点路径
    必须存在。词表这一格是必要的：`#156` 的规矩里"生产链和测试都不读的不许标留"在那一层能立，
    是因为方法层没有"按算出来的键回读"这一形；这一层有（见上面那六格 `RegionBudget`），所以
    先把"必须表态"钉住，哪一格表态错了由下面那条夹具与限界第二条管。
    """
    roster, dead = _field_defs_and_reads()
    assert len(roster) >= 250, f"字段只数到 {len(roster)} 格，多半是收集坏了"
    assert sorted(dead) == sorted(FIELD_TRIAGE), (
        f"零读者的字段与登记的名册不是一份：只在树上 {sorted(set(dead) - set(FIELD_TRIAGE))}，"
        f"只在名册里 {sorted(set(FIELD_TRIAGE) - set(dead))}")
    vague = [f"{k}：{FIELD_TRIAGE[k][:24]}" for k in dead
             if not FIELD_TRIAGE[k].startswith(FIELD_VERDICTS)]
    assert not vague, f"这些格的处置没以『删/搬/接/留/待判』开头：{vague}"
    sloppy = [f"{k}：{FIELD_TRIAGE[k]}" for k in dead if not _cited_path_exists(FIELD_TRIAGE[k])]
    assert not sloppy, f"这些格的处置没写出落点（或落点那个文件不存在）：{sloppy}"


def test_the_field_probe_reads_a_disk_key_readback_but_not_a_dict_literal(tmp_path):
    """夹具：回读那一格算读者，写盘那一格不算，src 点名惰性的那一格豁免——三向都要红得起来。

    `via_attr` 被 `b.via_attr` 读、`via_key` 被 `row["via_key"]` 读、`via_get` 被
    `b.__dict__.get("via_get")` 读、`never` 被**三参数** `getattr(b, "never", None)` 读（`#156`
    那一课：只认两参数的 getattr 会把这格判成零读者），四格都不许进名册；`dict_key_only`
    只作为 dict 字面量的键出现过（那是写盘不是回读），必须进来。`enable_sheriff` 谁都不读，
    但它坐在 `config.INERT_FIELDS` 里、由 src 自己背书，不许被本尺判成缺陷——这一格是
    "豁免不是漏判"的正控制。
    """
    pkg = tmp_path / "pkg"
    scripts = tmp_path / "script_side"
    for d in (pkg, scripts):
        d.mkdir()
    (pkg / "board.py").write_text(
        "from dataclasses import dataclass\n\n\n@dataclass\nclass Board:\n"
        "    via_attr: int\n    via_key: int\n    via_get: int\n"
        "    dict_key_only: int\n    never: int\n    enable_sheriff: int\n\n\n"
        "def show(b: Board) -> int:\n    return b.via_attr\n", encoding="utf-8")
    (scripts / "run.py").write_text(
        "from pkg import board\n\nb = board.Board(1, 2, 3, 4, 5, 6)\n"
        'row = {"via_key": 0, "dict_key_only": 7}\n'
        'print(board.show(b), row["via_key"], b.__dict__.get("via_get"), '
        'getattr(b, "never", None))\n', encoding="utf-8")

    roster, dead = _field_defs_and_reads((str(pkg),), (str(pkg), str(scripts)), ())
    assert sorted(roster) == [
        "board.py::Board.dict_key_only", "board.py::Board.enable_sheriff",
        "board.py::Board.never", "board.py::Board.via_attr", "board.py::Board.via_get",
        "board.py::Board.via_key"], sorted(roster)
    assert dead == ["board.py::Board.dict_key_only"], dead


def _own_module_names() -> set[str]:
    """`x.Foo` 的根名里，哪些算"我们自己的模块"。

    从磁盘上取，不抄名单：新增一个模块不需要改这条判据，而第三方库永远进不来（仓库里没有叫
    `httpx.py` 的文件）。
    """
    names: set[str] = set()
    for f in Path("src/wolfengine").rglob("*.py"):
        names.add(f.stem)              # 模块自己的文件名
        names.add(f.parts[1])          # 包名：wolfengine
        names.update(f.parts[2:-1])    # 子包目录名：prompts
    return names


def _classes_no_reader_reaches():
    """引擎里"没有任何读者够得着"的模块级类，连同它们一起不可达的成员名。`#85` 的判据本体。

    与 `_py_refs` 的分别只在**什么算读者**，而这个分别是这条判据存在的全部理由：`_py_refs` 把
    `x.Foo` 记成 `Foo` 的一次引用，于是第三方的同名成员替引擎里那个从不构造的类付了账——
    `httpx.MockTransport(handler)`（三处测试在用，那是 httpx 自己的类）把 `transport.MockTransport`
    喂活了。这里只认两种读者：裸 `Name`，和根名是本包模块的 `Attribute`（`batch.run_batch()` 那种
    走模块对象的真调用）。字符串常量仍算读者，与 `_py_refs` 同一套理由（`getattr` / `__all__`）。
    类**体内**指向自己名字的读数要减掉：`def clone(self) -> Foo` 是一个自指注解，不是有人拿它。

    这条**一个豁免都没开**——不跳 `__init__.py`，也不放过 dunder 名。那两个 `__init__.py` 今天一个
    类都没有，跳过它们是一个不需要证人的洞（`#83` 那三处豁免各自有证人，是因为它们真的在挡东西）。
    """
    own = _own_module_names()
    reads: dict[str, int] = {}

    def bump(name: str) -> None:
        reads[name] = reads.get(name, 0) + 1

    for root in ("src", "tests", "scripts"):
        for f in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Attribute):
                    head = node.value
                    while isinstance(head, ast.Attribute):
                        head = head.value
                    if isinstance(head, ast.Name) and head.id in own:
                        bump(node.attr)
                elif isinstance(node, ast.Name):
                    bump(node.id)
                elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and node.value.isidentifier()):
                    bump(node.value)

    dead = []
    for f in sorted(Path("src/wolfengine").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        for node in ast.parse(f.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.ClassDef):
                continue
            inside = sum(1 for n in ast.walk(node)
                         if isinstance(n, ast.Name) and n.id == node.name)
            if reads.get(node.name, 0) - inside > 0:
                continue
            members = sorted({s.name for s in node.body
                              if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))})
            dead.append(f"{f}:{node.lineno} class {node.name}（成员 {members}）")
    return sorted(dead)


def test_no_class_in_the_engine_is_kept_alive_by_a_namesake():
    """`#85`：上一条闸门只数 `def`，所以**类整个住在它的盲区里**，而类还有第二种死法。

    第一次跑这条时它是红的，名单两项（扫描面是全引擎的模块级类），两具的死法不同：
    - `transport.MockTransport` 的"读者"是 `httpx.MockTransport(handler)`（三处），那是 httpx 的
      同名类——**按名字全局匹配的尺替它付了账**。它的 docstring 还认领一件不存在的事："the fixture
      source for `test_golden_game.py`"，而那局金样本是手写的。删。
    - `actors.HumanActor` 连同名属性都没有，纯粹是 `#81` 不数类。它是 plan §15 留的上桌契约，
      这件事只活在 docstring 和 `docs/views.md` 里——**一句只活在散文里的主张就是个缺陷**，所以
      修法是给它真读者（`tests/test_actor_contract.py` 的 ④ 把它构造出来，钉住它自己声明的四个值），
      不是给闸门加一条"看着像桩就放过"的豁免。`#122` 把 `act()` 从 `NotImplementedError` 变成了
      真座位，钉那句拒绝的那半条随之删除——留着的这半条仍然要它能构造。

    限界也写在这里：这条仍然按名字匹配，一个方法名与活的兄弟同名时它不区分（`chat` 就是），它靠的是
    "类不可达则成员一起不可达"这一层；至于"签名与调用都对、但从没接进产物链"，那是 #74/#75 那一族。
    """
    dead = _classes_no_reader_reaches()
    assert dead == [], (
        "这些引擎类没有任何读者够得着（第三方同名属性不算读者），要么接上要么删："
        f"{dead}")


DECLARATION_ONLY_BASES = ("Enum", "IntEnum", "StrEnum", "ReprEnum", "Flag", "IntFlag",
                          "Protocol", "ABC", "Generic", "Exception", "BaseException")


def _class_constructions():
    """每个 src 模块级类的构造证据，连同"这一格归因到哪一形"。`#159` 的尺。

    与 `#85` 的分别是这条存在的全部理由，两条都要留着：那条问**有没有人点这个名**（`tests/` 里的
    点名也算，一根裸 `Name` 就活），这一条问**产物链走不走得到那一次构造**。`PublicState` 正是穿过
    那条却卡在构造这一环的——23:32Z 现测：全树 62 具模块级类里只有它一具，全部构造点都坐在
    `#156` 名册里零生产读者的成员体内。

    构造口径四种，少认一种就造出假缺陷（00:00:43Z 用本函数自己的归因现测：把"①构造点在生产链上"以外
    全算成缺陷是 6 格，6 格全是形状；`PublicState` 还在的时候是 7 格里 1 格真、6 格形状）：
      ① 显式 `Cls(...)`——callee 是 `Name`，或 `Attribute` 且根名是本包模块（`#85` 的同名替付账
        在这一层同样成立，所以 `Attribute` callee 必须过 `_own_module_names()`，第三方同名不算）
      ② dunder 隐式——构造点坐在 `__init__` / `__post_init__` 这类双下划线成员体内，Python 自己会走到
      ③ `field(default_factory=Cls)`——父 dataclass 构造时隐式实例化，那一格根本不长成 Call
      ④ 按设计不实例化——base 里有 Enum / Protocol / ABC / Generic / Exception，或整具类体一个函数都没有
        （`events.py::Kind` 是常量名册，走成员访问）

    形状有两份来源的那一格要认下来：`config.py::RegionBudget` 既没有 Call 也没有方法，③与④的"无方法"
    那一支同时解释着它——拆掉③那把刀只报出 `WitchState` 一具（00:12Z 现测），对 `RegionBudget` 它是等价
    变异。③仍然要留：`WitchState` 有 property，只有③解释得了它。

    限界，两条，都写在这里而不是藏在代码里：**只往外找一层**——构造点的宿主有没有生产读者用的是
    `#156`/`#155` 那两份名册，不是调用图闭包，所以"死函数里套死函数"这条看不见（套在更外面那层的
    宿主只要被人点过名就放行）；**嵌套闭包不是宿主**——`_functions_of` 会给出内层函数，它不在名册里
    时这一条继续向外层找，一直找不到就按模块体（import 期执行，算活）。
    """
    roster = _class_defs_and_reads()
    mod_defs, mod_readers, _ = _module_defs_and_production_reads()
    entries = _entry_point_names()
    own = _own_module_names()
    site_trees = _parsed_trees(("src", "scripts"))
    src_trees = _parsed_trees(("src",))

    def callee(node):
        """这次 Call 在被构造的是哪一具类；第三方同名（根名不在本包里）不算。"""
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            head = func.value
            while isinstance(head, ast.Attribute):
                head = head.value
            if isinstance(head, ast.Name) and head.id in own:
                return func.attr
        return None

    def host(path, defs, lineno):
        """ lineno 所在的最内层函数（按 AST 跨度认，不认"行号上最近的前一个 def"），一路向外直到模块体。

        按行号就近取宿主是这一具的第一版，K3 那具负控制把它抓出来了：模块体里、写在最后一个 `def`
        **之后**的一次构造，被算给了那个 `def` 的体内——于是"import 期执行＝算活"那一支根本没机会开口。
        """
        inside = [d for d in defs if d[0].lineno <= lineno <= d[0].end_lineno]
        for fn, cls, _ in sorted(inside, key=lambda t: (t[0].end_lineno - t[0].lineno, -t[0].lineno)):
            if fn.name.startswith("__"):
                return ("dunder", fn.name)
            if cls is not None:
                key = f"{Path(path).name}::{cls}.{fn.name}"
                if key in roster:
                    return ("method", key)
            elif fn.name in mod_defs:
                return ("module-fn", fn.name)
        return ("import", None)

    calls: dict[str, list[tuple]] = {}
    factory: dict[str, list[tuple]] = {}
    for path, tree in site_trees.items():
        defs = _functions_of(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = callee(node)
                if name:
                    kind, key = host(path, defs, node.lineno)
                    calls.setdefault(name, []).append((f"{path}:{node.lineno}", kind, key))
            elif isinstance(node, ast.keyword) and node.arg == "default_factory":
                ref = node.value
                ref = ref.id if isinstance(ref, ast.Name) else None
                if ref:
                    factory.setdefault(ref, []).append(f"{path}:{node.lineno}")

    out: dict[str, dict] = {}
    for path, tree in src_trees.items():
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.startswith("__"):
                continue
            bases = []
            for b in node.bases:
                while isinstance(b, ast.Subscript):
                    b = b.value
                bases.append(b.id if isinstance(b, ast.Name) else
                             (b.attr if isinstance(b, ast.Attribute) else ""))
            methods = [s for s in node.body
                       if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
            where = f"{path}:{node.lineno}"
            shape, live, dead_sites = "", [], []
            for site, kind, key in calls.get(node.name, []):
                if kind in ("import", "dunder"):
                    live.append(f"{site}（{kind}）")
                elif kind == "module-fn":
                    (live if (key in entries or mod_readers.get(key, 0)) else dead_sites).append(
                        f"{site} 坐在 {key}()")
                else:
                    (live if roster[key][0] else dead_sites).append(f"{site} 坐在 {key}")
            if live:
                shape = "①构造点在生产链上"
            elif dead_sites:
                shape = "①有构造点但产物链走不到"
            elif factory.get(node.name):
                shape = f"③field(default_factory)：{factory[node.name]}"
            elif any(b in DECLARATION_ONLY_BASES for b in bases):
                shape = f"④按设计不实例化（base {bases}）"
            elif not methods:
                shape = "④常量名册，无一具方法（走成员访问）"
            out[f"{Path(path).name}::{node.name}"] = {
                "where": where, "shape": shape, "live": live, "dead": dead_sites,
                "has_method": bool(methods), "bases": bases,
            }
    return out


def test_no_engine_class_lives_only_on_a_construction_the_product_never_reaches():
    """`#159`：类层此前只有 `#85` 那一把尺，而它问的是"点名"不是"构造"——`PublicState` 从它眼底过去了。

    第一跑（23:36Z，删之前）是红的，名单恰好一具：`state.py::PublicState` 唯一一处
    `PublicState(...)` 坐在 `GameState.public_state()` 体内，而那一具在 `#156` 名册里是零生产读者
    ——也就是整具类在产物链上不可达。同趟另六具"一处 Call 构造都没有"的类全部由形状解释（`Actor`
    与 `LLMTransport` 是 Protocol、`Phase` 是 Enum、`Kind` 是无方法的常量名册、`RegionBudget` 与
    `WitchState` 走 `field(default_factory=...)`），一条手工豁免都没开。

    这一条不许"登记个处置就放过"：解释必须机械可查（四种口径都是从 AST 上取的），所以唯一的出路是
    `#85` 那两选一——接进产物链，或者删掉。`PublicState` 走的是后者，理由与代价记在 `docs/iterations.md`
    对应那一节（`#157` 的页眉假话是同一次删的）。
    """
    cells = _class_constructions()
    unexplained = {k: v for k, v in cells.items()
                   if v["shape"] == "①有构造点但产物链走不到" or v["shape"] == ""}
    shaped = {k: v["shape"] for k, v in sorted(cells.items()) if k not in unexplained}
    report = [f"{k}（{v['where']}；{v['dead']}）" for k, v in sorted(unexplained.items())]
    assert not unexplained, (
        f"这些引擎类的构造点产物链走不到，或根本没有构造点又没有形状解释：{report}\n"
        f"同趟有解释的 {len(shaped)} 格，各自归因：{shaped}")


def _is_a_declaration_only(fn) -> bool:
    """去掉 docstring 之后，函数体只剩 `...`/`pass`/`raise`：它是**声明**，不是实现。

    `raise` 也算声明，因为"这里刻意没做，去别处做"（测试里那些整个 body 只有一个 `raise` 的回调
    替身 `boom` / `dead`）和"签名在这、实现待定"（Protocol 桩）是同一种东西：body 里本来就不该有读者。
    """
    body = [s for s in fn.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                    and isinstance(s.value.value, str))]
    return bool(body) and all(
        isinstance(s, (ast.Pass, ast.Raise))
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
            and s.value.value is Ellipsis)
        for s in body)


def _parameters_of(fn) -> list[str]:
    """这个签名**要求**调用方递进来的名字。接收者不算（`self` 是语言塞的，不是选的设计），
    `*args`/`**kwargs` 今天全引擎零处，不为其写规则。
    """
    a = fn.args
    return [p.arg for p in [*a.posonlyargs, *a.args, *a.kwonlyargs]
            if p.arg not in ("self", "cls")]


def _parsed_trees(roots: tuple[str, ...]) -> dict[str, "ast.Module"]:
    """把 `roots` 下每个 `.py` 解析一次，键是相对路径字符串。

    只解析一遍是必需的，不是省时间：豁免的来源和被判据扫的那个函数常常不在同一个 root 里
    （`tests/` 里 `class Chorus(MockActor)` 的父类住在 `src/wolfengine`），两边必须看同一批 AST。
    """
    out: dict[str, ast.Module] = {}
    for root in roots:
        for f in sorted(Path(root).rglob("*.py")):
            if "__pycache__" not in f.parts:
                out[str(f)] = ast.parse(f.read_text(encoding="utf-8"))
    return out


def _functions_of(tree):
    """模块里的每个函数，连同它**所属的类名**（没有就 None）与**嵌套深度**（模块级是 0）。"""
    out: list[tuple] = []

    def walk(node, cls: str | None, depth: int) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((ch, cls, depth))
                walk(ch, cls, depth + 1)
            elif isinstance(ch, ast.ClassDef):
                walk(ch, ch.name, depth)

    walk(tree, None, 0)
    return out


def _signatures_imposed_from_outside(trees: dict):
    """两类"这个签名不是写函数的人自己选的"，豁免只认这两个来源，都是从盘上取的。

    1. **契约桩**：某个只签名的函数（Protocol 方法、`raise NotImplementedError` 的占位）声明的
       `(函数名, 参数名)` 对。鸭子类型没有 base 列表，`MockActor` 从来没写过 `(MockActor, "phase")`
       这种关系，所以这一类只能按名字配对——它的限界就是它的形状（见 #85 的同名 laundering）。
    2. **继承**：`(父类名, 方法名, 参数名)`。子类改的是行为不是签名，删一个不收的参数会当场把
       父类的调用点打死。
    """
    stub_pairs: set[tuple[str, str]] = set()
    parents: dict[str, list[str]] = {}
    inherited: set[tuple[str, str, str]] = set()
    for tree in trees.values():
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for base in cls.bases:
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                if name:
                    parents.setdefault(cls.name, []).append(name)
            for meth in [n for n in cls.body
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                for p in _parameters_of(meth):
                    inherited.add((cls.name, meth.name, p))
                if _is_a_declaration_only(meth):
                    stub_pairs |= {(meth.name, p) for p in _parameters_of(meth)}
        for fn, _cls, _depth in _functions_of(tree):
            if _cls is None and _is_a_declaration_only(fn):
                stub_pairs |= {(fn.name, p) for p in _parameters_of(fn)}
    return stub_pairs, parents, inherited


def _declared_by_an_ancestor(cls: str, meth: str, param: str, parents: dict, inherited: set) -> bool:
    """`cls` 的任一祖先（含第三方基类名，按名字匹配）在同名方法里收了这个参数。"""
    stack = list(parents.get(cls, ()))
    seen: set[str] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if (cur, meth, param) in inherited:
            return True
        stack += parents.get(cur, [])
    return False


def _params_declared_but_never_read(roots: tuple[str, ...] = ("src/wolfengine",),
                                    *, framework_called: bool = False) -> list[str]:
    """`#86` 的判据本体：签名要求你递、body 从不看的东西。

    "读过"只认 AST 里的 `Name`/Load——包括嵌套函数与 f-string 里的（尺按形状粗，这是**故意**的：
    闭包确实拿到了那个值，硬要区分调用栈深度换来的只是把真读者误判成谎言）。docstring 里提到
    参数名**不算**读过，与 `#81` 同一套理由：注释留着词、代码早不用了，子串搜索骗得过去。

    `framework_called` 是**给测试侧用的**第三种豁免：函数名以 `test_` 开头（pytest 按名字从夹具
    注册表里取参数）或它嵌在另一个函数里（它是递给被测代码的回调，参数表由对面那一步决定）。
    引擎侧永远不传这个开关——引擎的函数是引擎自己调的。
    """
    trees = _parsed_trees(("src", "tests", "scripts"))
    stub_pairs, parents, inherited = _signatures_imposed_from_outside(trees)
    hits: list[str] = []
    for path, tree in sorted(trees.items()):
        if not any(path == r or path.startswith(r + "/") for r in roots):
            continue
        for fn, cls, depth in _functions_of(tree):
            reads = {n.id for n in ast.walk(fn)
                     if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            for p in _parameters_of(fn):
                if p in reads or (fn.name, p) in stub_pairs:
                    continue
                if cls and _declared_by_an_ancestor(cls, fn.name, p, parents, inherited):
                    continue
                if framework_called and (fn.name.startswith("test_") or depth > 0):
                    continue
                hits.append(f"{path}:{fn.lineno} {cls + '.' if cls else ''}{fn.name}({p})")
    return sorted(hits)


def test_no_engine_function_declares_a_parameter_nobody_reads():
    """`#86`：`#81` 数函数的名字、`#83`/`#84` 数导入的名字、`#85` 数类的名字——**签名本身**没人管。

    于是"声明了却从不读的入参"活到了现在，第一次跑这条是红的，五处，五处都在替调用方编一个
    不存在的约定：
    - `compress.plan_fold(budget=...)`：六处调用点老老实实递进 `cfg.tokens`，而决定折叠多少的是
      `b2_cap` 和 `est`。它的 docstring 通篇在讲"预算杠杆"，读代码的人会以为改 `TokenBudget`
      能改变折叠深度——不能，那条路径上没有任何东西读它。
    - `agent._write(legal=...)`：写日志的函数收下了合法动作集，又没往日志里写过一个字。
    - `cli._llm_actors(cfg, seed, transport)`：`make_actors` 用 seed 抽人格，这个孪生签名不用，
      于是"活 Actor 也按 seed 变化"看着成立，实则换 seed 换不出任何差别。
    - `rules.resolve_tie(state, first, second)`： house rule 是"复投再平就没人出局"，只看 `second`
      就够；`first` 是留着的。
    - `persona._top_accuser(b, state, seat)`：最吵的指控者是信念状态的性质，与 `GameState` 无关。

    五处一律**删参数**而不是"想办法读一下"：读一次就把它写进日志/产物，那是在给一个没被要求的
    字段找读者（`#85` 那条"接上 vs 删掉"的取舍在这里的答案是删——没有一个下游指标需要它）。

    豁免见 `_signatures_imposed_from_outside`，两条，都在说同一件事：**这个签名不是写函数的人选的**。
    契约桩（`Actor`/`LLMTransport` 那些只有签名的方法）声明的 `(函数名, 参数名)` 对放过了 Protocol
    桩自己的五个参数，也放过了它们的鸭子类型实现（`MockActor.timeout_for(phase)`、
    `HumanActor.act(ctx)`——它们没写 base，所以只能按名字配对）；父类声明过的参数放过了真继承
    （引擎今天用不上这一条：`parents` 里只有异常类、`str,Enum` 和 pydantic 模型，没有一个父类
    与引擎方法同名）。连 `_` 前缀都**没有**豁免：全引擎今天零个下划线参数，开了只是把扫面变小
    （`#85` 的教训）。

    限界两条，写在这里而不是藏在实现里：豁免按**名字**配对，所以一个与契约同名的普通方法可以
    借它藏一个真死的参数（`timeout_for` 就是这种名字）；而"参数被读了但读到的值没用"（传进去
    又原样返回）这条尺看不见。**扫面只有引擎**是范围决定，不是遗漏，旁边那条用例钉住它。
    """
    dead = _params_declared_but_never_read()
    assert dead == [], (
        "这些入参被签名要求、却从没被函数体读过（要么接上，要么删掉，"
        "别留着让调用方以为它有用）："
        f"{dead}")


def test_the_test_side_is_out_of_that_scope_by_a_derivation_not_a_list():
    """`#86` 的第二格：`#84` 把未用导入的扫面从引擎加宽到了 `tests/`+`scripts/`，这条**没有**，
    所以那个范围决定必须自己交证人。

    加宽之后现场是：未开框架豁免时满仓库的夹具参数都成了"谎言"（数量见 `raw`），开了以后只剩零处——
    而"只剩零处"不是因为我把名单念了一遍，是因为三条豁免把每一处都归到了某个**形状**：
    - `test_` 开头：pytest 按名字从夹具注册表取参数，`test_x(key)` 收 `key` 是要那个 env 变量被设上，
      不是要读它。这一半在 `raw` 里占大头（断言在下面，别让它悄悄变成零）。
    - 嵌在另一个函数里：那是递给被测代码的回调（httpx 的 `handler(request)`、批跑探针的
      `canary(prompt)`），参数表由对面那一步决定。
    - 父类收了这个参数：`tests` 里的 `Chorus._line(act, target)` 覆盖 `actors.MockActor._line`，
      改行为不改签名。这一具是"继承"那条豁免的**唯一**证人，而它在 `raw` 里已经看不见（`raw`
      也带着这条豁免），所以这条用例直接查那个谓词，不查名单。

    `scripts/` 在这一条扫面里零处，不需要任何照顾——它与 `tests/` 同一条尺，靠的是断言而不是范围。
    """
    raw = _params_declared_but_never_read(("tests", "scripts"))
    assert raw, "tests/ 里如果一处都没了，框架豁免就该删掉、把范围真的加宽"
    assert any(" test_" in h for h in raw), "pytest 那一半没人证了：豁免成了空转的洞"
    assert any(not h.split(" ")[1].split("(")[0].startswith("test_") for h in raw), \
        "回调/覆盖那一半没人证了，同上"
    trees = _parsed_trees(("src", "tests", "scripts"))
    _stub, parents, inherited = _signatures_imposed_from_outside(trees)
    assert _declared_by_an_ancestor("Chorus", "_line", "act", parents, inherited), \
        "继承豁免的唯一证人不见了（`Chorus._line` 的 `act` 是父类收的）：那条豁免变成没有形状的洞"
    left = _params_declared_but_never_read(("tests", "scripts"), framework_called=True)
    assert left == [], (
        "测试侧这些地方既不是夹具、也不是回调、也没有父类收着这个参数——那就是真的签名谎言："
        f"{left}")
    assert _params_declared_but_never_read(("scripts",), framework_called=True) == []


def _unread_imports(roots: tuple[str, ...]) -> list[str]:
    """导入名在本文件里从没被点过的地方。`#83` 的判据本体，两条用例共用一把尺。

    三处豁免的理由写在 `#83` 那条用例的 docstring 里，这里只说形状：`__future__` 跳过、
    `__init__.py` 整文件跳过、只出现在字符串注解里的名字算读者；函数体内的延迟导入不豁免。
    """
    dead: list[str] = []
    for root in roots:
        for f in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in f.parts or f.name == "__init__.py":
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            imported: dict[str, int] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module == "__future__":
                        continue
                    for a in node.names:
                        if a.name != "*":
                            imported[a.asname or a.name.split(".")[0]] = node.lineno
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        imported[a.asname or a.name.split(".")[0]] = node.lineno
            used: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    head = node.value.split(".")[0]
                    if head.isidentifier():
                        used.add(head)
            dead += [f"{f}:{ln} {name}"
                     for name, ln in imported.items() if name not in used]
    return sorted(dead)


def test_no_import_in_the_engine_is_left_unread():
    """`#83`：上一条闸门只数 `def`，而 `#81` 删字段删出来的腐烂正好落在它看不见的地方——导入。

    06:25:43Z 现测四处：`batch.py` 的 `asyncio`、`events.py` 的 `asdict`、`info.py` 与 `persona.py`
    的 `field`。它们没有一处会把局跑坏，代价是另一种：读代码的人拿导入行当"这个模块依赖什么"的
    说明，于是一个不再依赖 `asyncio` 的模块继续声称自己依赖它。这跟"零读者函数"是同一种腐烂，只是
    挂在 import 上，所以判据也照同一套写法来：AST 数真引用，不拿子串搜索冒充。

    **这一条只管引擎侧**，而这不是偷懒：函数那条判据要"读者侧比定义侧宽"，因为一个 helper 可以被
    测试正当使用；导入没有这种跨文件读者——**本文件不点它的名，它对这行代码就是死的**，别的文件
    再导入一次是另一件事。所以两把尺的范围不同，测试与脚本侧由旁边那条 `#84` 用例管。

    三处豁免都有证人（见 `/tmp/mut83.py` 的 I2/I5/I7），限界也写在这里：它只认"整串就是一个
    标识符"的字符串注解，带运算符的那种（`"A | None"`）它不解析——今天全仓库没有这种写法。
    """
    dead = _unread_imports(("src/wolfengine",))
    assert not dead, f"这些导入在本文件里从没被点过名（要么接上，要么删掉）：{dead}"


def test_no_import_left_unread_in_the_test_side_either():
    """`#84`：同一把尺量 `tests/` 与 `scripts/`，因为 `#83` 删掉四处之后没有任何东西阻止再烂一次。

    06:48:17Z 现测十五处，全在 `tests/` 里（`scripts/` 是零），形状分两类：一类是**整行的模块
    清单被掏空**（`from wolfengine import belief, info, roles, rules, state` 里只剩 `belief` 还有人
    点），一类是**单行导入整个没人读**。测试文件的导入行同样是这份文件对读者说的话——"这个行为
    要靠这些模块演出来"，说错了和被删掉的那四处是同一种谎。

    第十五处不是腐烂，是**这把尺的盲点**：`tests/test_batch_live.py` 从 `test_live_path` 再导出的
    `key` 是一个 pytest 夹具，读者按名字在模块命名空间里找它，AST 里永不会有一次点名。修法是把夹具
    搬进 `tests/conftest.py`（框架给跨模块夹具留的地址），**不是**给闸门开豁免——豁免是要有证人的洞，
    搬走是把洞填上。所以将来谁再把夹具按名字再导出，这条会红，而修法还是同一句：搬进 conftest。
    `# noqa` 在这把尺上一个字都不算（`/tmp/mut84.py` 的 T3 钉这一点），它挡不住任何东西。
    """
    dead = _unread_imports(("tests", "scripts"))
    assert not dead, f"测试与脚本里这些导入从没被点过名：{dead}"


def test_the_persona_card_ships_every_number_the_sampler_draws():
    """`#81` 的第二格：`PersonaParams` 抽四个数，`render_persona_card` 只印三个。

    漏掉的是 `verbosity`：`sample_persona` 为它花了一次 `rng.uniform`，值落进 dataclass 之后再
    没有被任何一处读过（06:04Z 现查 `grep -rn "verbosity" src tests scripts docs`，命中的只有定义
    行与抽样行）。它既不改变发出去的字节，也不改变任何权重，而类 docstring 写着"Four numbers and
    a style tag"——那句话对 dataclass 成立、对产品不成立。

    判据不写死"四"这个数，而是从 dataclass 的字段表上取全部 `float`：以后再加一个数而忘了印，
    红的会是那一个的名字。取值走 `dataclasses.replace` 逐个注入，不在测试里抄第二份字段名单——
    抄来的名单会在加字段时 `TypeError`，那是一条没有信息量的红。
    """
    numbers = [f.name for f in dataclasses.fields(persona.PersonaParams) if f.type == "float"]
    assert len(numbers) >= 2, f"没从 dataclass 上取到数字段，字段注解的形状变了：{numbers}"
    p = persona.PersonaParams()
    for i, name in enumerate(numbers):
        p = dataclasses.replace(p, **{name: round(0.11 + 0.13 * i, 2)})
    card = persona.render_persona_card(p)
    missing = [name for name in numbers if f"{getattr(p, name):.2f}" not in card]
    assert not missing, f"这些数抽出来了却没进那张卡，座位上读不到自己的人格：{missing}"


def test_the_live_header_shows_the_manifest_the_shared_reader_found(tmp_path, capsys):
    """Which line of a file is the manifest has one answer: the one with `seq == 0`, and a reader
    may skip blank lines to find it — `events.py` says that out loud, because hand-written
    fixtures have them. The live view took *line 1* instead and swallowed the parse error, so a log
    that begins with a blank line lost its `game_id` from the header while every offline reader
    still reported it: two opinions about one file, and the silent one was the screen somebody
    presents from.

    The structural leg is what keeps the first leg honest. A second parser that happens to agree
    on this fixture would pass the frame assertion, so the rule has to be that no second parser
    opens the file at all.
    """
    path = tmp_path / "w.jsonl"
    log = EventLog(path, meta={"game_id": "g-header-7", "model": "stub"})
    log.write_meta()
    log.append(Kind.GAME_START, day=1, phase="night_wolf", seats=[1, 2, 3])
    log.append(Kind.SPEECH, day=1, phase="day_speech", actor=1, text="3号发言太顺了。",
               act="accuse", target=3)
    path.write_text("\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    loaded, meta = EventLog.read_records(path)
    assert meta.get("game_id") == "g-header-7", "共用读取器自己也找不到，这条用例就没在测分裂"
    assert len(loaded) == 2

    assert render_live.watch(path, one_shot=True) == 0
    out = capsys.readouterr().out
    named = [ln for ln in out.splitlines() if "狼人杀直播" in ln or "已落盘的日志" in ln]
    assert len(named) == 2, f"一帧里有两处报出这局的名字，实测 {len(named)} 处：{named}"
    for ln in named:
        assert "g-header-7" in ln, f"离线读取器认得这局的名字，这一处不认得：{ln}"

    live_src = Path("src/wolfengine/render_live.py").read_text(encoding="utf-8")
    assert "read_text(" not in live_src, (
        "render_live 又自己去开日志文件了：哪一行是 manifest 的判据只该有一份")


def test_abstention_does_not_render_as_a_fake_seat():
    """`→{target or '弃票'}号` produced the string 弃票号, i.e. a seat that does not exist."""
    assert compress.render_line(ev(9, Kind.VOTE, actor=4, target=2)) == "[e9] 投票：4号→2号"
    assert compress.render_line(ev(9, Kind.VOTE, actor=4, target=None)) == "[e9] 投票：4号弃票"


def test_night_action_renders_the_chosen_act_not_the_task_label():
    """A mock game printed `女巫（夜间行动）：save_or_poison→None`.

    `payload["action"]` is what the phase *asked for*, and nothing in the product reads it (it is
    the one name on #115's exemption table) — as a record of what happened it is useless, and
    `→None` on top of that. The chronicle is the model's only memory of the night, so it has to
    say 用解药.
    """
    assert compress.render_line(ev(7, Kind.NIGHT_ACTION, visibility=seats(5), actor=5,
                                  act="save", action="save_or_poison")) \
        == "[e7] 5号（夜间行动）：用解药。"
    assert "save_or_poison" not in compress.render_line(
        ev(7, Kind.NIGHT_ACTION, visibility=seats(5), actor=5, act="poison",
           action="save_or_poison", target=2))
    assert compress.render_line(ev(8, Kind.NIGHT_ACTION, visibility=seats(5), actor=5,
                                   act="pass", action="save_or_poison")) \
        == "[e8] 5号（夜间行动）：没有行动。"


def test_silence_is_rendered_as_silence_not_as_an_empty_line():
    """`4号（遗言）：` with nothing after it reads as a renderer bug; the seat chose not to
    speak, and that choice is itself information for the next day's reads."""
    assert compress.render_line(ev(9, Kind.LAST_WORDS, actor=4, text="")) == "[e9] 4号（遗言）：（沉默）"
    assert compress.render_line(ev(9, Kind.SPEECH, actor=4, text="   ")) == "[e9] 4号：（沉默）"
    assert compress.render_line(ev(9, Kind.WOLF_CHAT, visibility=seats(1, 2), actor=1,
                                  text="")) == "[e9] 狼队私聊 1号：（沉默）"


def test_the_wolf_chat_line_shows_the_seat_being_point_at():
    """形状表给 `wolf_chat` 声明了 `target`，而渲染层只印文本——那一格落盘之后没有读者。

    写侧是真的在写：`agent.py` 给每种决策都带 `target`，所以真人打「讨论 5 今晚刀他」时那个 5
    进了日志、又谁都不读（实测 16:29Z：`compress.py` 的狼聊分支只用 `_said(p)`）。一条没人读的
    指向有两种坏法：要么它是死字段（该从表里删），要么它在骗读表的人（该印出来）。狼队私聊里
    "指向谁"是提案的一部分，所以这一片选了印出来——同一行文本在模型看得见的那条梯子上也变，
    不需要第二条取数据的路。
    """
    assert compress.render_line(ev(9, Kind.WOLF_CHAT, visibility=seats(1, 2), actor=1,
                                   text="今晚动手。", act="discuss", target=5)) \
        == "[e9] 狼队私聊 1号（指 5号）：今晚动手。"
    # 反向：没带指向的一行不能被凭空补一个"指"字。
    assert compress.render_line(ev(9, Kind.WOLF_CHAT, visibility=seats(1, 2), actor=1,
                                   text="今晚动手。", act="discuss", target=None)) \
        == "[e9] 狼队私聊 1号：今晚动手。"


def test_the_deal_line_says_who_is_on_your_team():
    """形状表给 `deal` 声明了 `teammates`，写侧也在写（`game.py:131`），渲染层却只印到身份为止。

    这一格和 `#130` 那一格是同一种坏法：落盘了、没人念。区别在于这里的代价是牌桌上的一个事实——
    狼不知道自己跟谁一伙。实测 12 局 mock（`/tmp/probe133.py`，17:02Z）：74 次问狼里 12 次
    percept 里认不出任何队友，而且**每局都恰好一次**，就是 `phases.py:137` 那个先开口的 proposer，
    他在任何一条私聊落盘之前就得决定今晚刀谁。补这一行只改一处：`--god` 时间线、直播、复盘 HTML
    和真人屏上的局况四台机器共用 `render_line`，写一次就全都念得到。**模型 prompt 不在这四条出口
    里**：`chronicle()` 只留公开事件，C 段那块私有信息又明写着 `e.kind != Kind.DEAL`，那一格是
    `#134`，不在这一片里顺手。
    """
    assert compress.render_line(ev(2, Kind.DEAL, visibility=seats(1), actor=1,
                                   role="wolf", teammates=[4, 2])) \
        == "[e2] 法官（私发）：你的身份是 wolf，队友是 2号、4号。"
    # 反向两腿：平民没有名册，旧日志干脆没有这一格——两种都不能多出"队友"两个字。
    assert compress.render_line(ev(2, Kind.DEAL, visibility=seats(3), actor=3,
                                   role="villager")) \
        == "[e2] 法官（私发）：你的身份是 villager。"
    assert compress.render_line(ev(2, Kind.DEAL, visibility=seats(6), actor=6,
                                   role="villager", teammates=[])) \
        == "[e2] 法官（私发）：你的身份是 villager。"


def test_the_wave_splitter_has_one_owner_and_both_readers_call_it():
    """Where a voting wave *ends* is a rule about the log, and two modules need it: the metric
    that counts 弃票 per wave, and the 复盘 that prints one grid per wave. Two definitions drift,
    and the drift is invisible until an audit says "3 waves" next to a page that printed 2 —
    the same split-brain this file was written for, one layer down.
    """
    owners = [f.name for f in sorted(Path("src/wolfengine").rglob("*.py"))
              if "def voting_waves(" in f.read_text(encoding="utf-8")]
    assert owners == ["events.py"], owners
    for reader in ("metrics.py", "render_html.py"):
        assert "voting_waves(" in Path("src/wolfengine", reader).read_text(encoding="utf-8"), (
            f"{reader} must call the shared splitter, not reimplement it nearby")


def test_a_wave_closes_at_its_tally_and_an_untallied_tail_is_still_a_wave():
    evs = [ev(1, Kind.VOTE, actor=1, target=2), ev(2, Kind.VOTE, actor=2, target=None),
           ev(3, Kind.VOTE_RESULT, tally={"2": 1}),
           ev(4, Kind.VOTE, actor=3, target=2),
           ev(5, Kind.SPEECH, actor=4, text="……")]
    waves = events.voting_waves(evs)
    assert [(len(v), None if r is None else r.seq) for v, r in waves] == [(2, 3), (1, None)]
    assert [e.actor for e in waves[0][0]] == [1, 2], "波内保持落票顺序"
    assert events.voting_waves([ev(1, Kind.SPEECH, actor=1, text="x")]) == []
    # A tally with no ballots before it is not a wave with zero voters in it: the denominator
    # of every per-wave rate is the length of that list, so an empty one is a ZeroDivisionError
    # on somebody else's read of this log.
    assert events.voting_waves([ev(1, Kind.VOTE_RESULT, tally={})]) == []


def test_the_settlement_sentence_has_one_author_and_four_shapes():
    """票型、弃票人数、结论子句三截话全部由 typed 字段算出来：四种形状各一句，逐字钉住。

    手术刀：把 `_vote_summary` 退回"只拼票型"的那一份——它今天就是这个形状，而负载里存着的
    那整句（`phases._tally_text` 写的）比它多两截，于是真日志上 30 次结算有 30 次两份说法不
    一致（00:03:32Z 现读）。这一条四句全红，红在文案本身，不红在我新造的短语上。

    第三形状是这条存在的原因：「并列，且后面还有一波复投」今天没有任何字段承载，只活在那句
    人话里——一句话能带着一个字段带不了的事实，就是双写的现场。
    """
    exile = compress.render_line(ev(11, Kind.VOTE_RESULT, tally={"3": 4, "1": 2},
                                    exiled=3, abstained=1))
    assert exile == f"[{info.eid(11)}] 法官：票型：1号2票、3号4票。弃票1人。3号被投票出局。", exile
    tie = compress.render_line(ev(12, Kind.VOTE_RESULT, tally={"3": 1, "9": 1},
                                  exiled=None, abstained=1))
    assert tie.endswith("票型：3号1票、9号1票。弃票1人。平票，无人出局。"), tie
    pk = compress.render_line(ev(13, Kind.VOTE_RESULT, tally={"3": 1, "9": 1},
                                 exiled=None, abstained=1, pending_pk=True))
    assert pk.endswith("票型：3号1票、9号1票。弃票1人。票数并列，先不定人。"), pk
    all_pass = compress.render_line(ev(14, Kind.VOTE_RESULT, tally={}, exiled=None, abstained=9))
    assert all_pass.endswith("法官：全员弃票，无人出局。"), all_pass


def test_an_unknown_abstention_count_is_not_rendered_as_zero():
    """没有 `abstained` 就不许印「弃票0人」：0 是一个断言，不是空缺。

    与 `#92` 那条「null 不是 0」同一条判据，落在结算文案这一格。旧日志带着整句走另一个分支，
    所以这条只管"typed 字段缺"的那一支——缺了就少说一截，不许补一个反方向的数。
    """
    line = compress.render_line(ev(15, Kind.VOTE_RESULT, tally={"3": 4}, exiled=3))
    assert "弃票" not in line, line
    assert "3号4票" in line and "3号被投票出局" in line, line


def test_every_real_settlement_renders_the_very_sentence_it_was_written_with():
    """从 data/ 八份日志抄出来的 30 次真结算逐条重放：拆掉双写之后，模型读到的那一行一字不变。

    夹具的 typed 字段一律**不从那句文案里解析**（`abstained` 来自日志自己的 `abstainers` 名单
    或这一波里的空白票，`pending_pk` 来自同一天后面是否还有第二个结算），所以这条证的不是我
    的解析能往返，而是新的唯一作者复现了旧的整句。21 个不同的句子、11 个不同的结论子句都在里面。

    限界两条：① `全员弃票，无人出局。` 那一支在真日志里没有证人（30 次结算的票型都非空），它只有
    上面那条合成用例；② 有一行标了 `prefixed_prose` 并从等价里排除——那份文案写于"并列先不定人"
    这个区分出现之前，而同一份日志紧接着就开了复投波，也就是说那句话当时就与自己的下文矛盾。
    """
    doc = json.loads((Path(__file__).parent / "fixtures" / "vote_settlements.json")
                     .read_text(encoding="utf-8"))
    rows = doc["rows"]
    assert len(rows) == 30, len(rows)
    prefixed = [r["src"] for r in rows if r["prefixed_prose"]]
    assert prefixed == ["20260920T184536Z_g00000007.jsonl#seq94"], prefixed
    bad = []
    for i, r in enumerate(rows):
        if r["prefixed_prose"]:
            continue
        line = compress.render_line(ev(20 + i, Kind.VOTE_RESULT, tally=r["tally"], exiled=r["exiled"],
                                       abstained=r["abstained"], pending_pk=r["pending_pk"]))
        if not line.endswith("法官：" + r["stored"]):
            bad.append((r["src"], r["stored"], line))
    assert not bad, f"{len(bad)} 条真结算的句子换了字：{bad[:3]}"



def test_one_death_says_the_same_sentence_to_every_reader_of_it():
    """同一条 DEATH 在三个读者嘴里必须是同一句话，未知死因是这一条的压力测试。

    `compress.py` 上面几行早就给过判据（`VERDICT_ZH` 那句注释）：游戏事实以枚举进日志，译文留在
    渲染侧——预言家查验照做了，死因没有：`phases.py` 在写 `cause` 的同时把 `CAUSE_ZH[cause]` 也
    存进负载，于是"怎么死的"这句话有两个写者。今天两份说的是同一件事（data/ 6 份、37 条 DEATH，
    `cause_zh == CAUSE_ZH[cause]` 37/37，23:33:03Z 现读），但四个读者各有各的回退：时间线现算
    （未知→"死亡"）、B0 状态卡现算（未知→空字符串）、复盘 HTML 和提示词读**存着的那一份**
    （缺失→分别为空字符串和英文枚举原文）。表里加第五种死法、或读一条没写过 `cause_zh` 的旧日志，
    这三句就会分岔——而其中一句是要进模型提示词的。
    """
    dead = ev(12, Kind.DEATH, seat=5, cause="old_witch_curse", day=2)
    line = compress.render_line(dead)
    phrase = re.search(r"出局（(.*?)）。", line).group(1)
    assert phrase, f"时间线没给出这句死法，下面的比较是空的：{line}"
    axis = render_html._axis([dead])
    card = assemble._status_card(info.Percept(seat=1, at_seq=12, events=(dead,)), (dead,))
    fold = compress.day_fold_lines(2, [dead])
    assert phrase in axis, f"复盘 HTML 说的不是这一句（{phrase}）：{axis}"
    assert phrase in card, f"进提示词的状态卡说的不是这一句（{phrase}）：{card}"
    assert phrase in fold, f"折叠出的当日摘要说的不是这一句（{phrase}）：{fold}"
    assert "old_witch_curse" not in card, f"英文枚举泄进了中文提示词：{card}"


def test_render_line_is_byte_stable_across_calls():
    for e in ALL_KIND_EVENTS:
        assert compress.render_line(e) == compress.render_line(e)


def test_render_line_ignores_wall_clock():
    """A t_wall in a rendered line would rewrite region B every turn and void the prefix
    cache; the field exists on the event and must never reach the prompt."""
    import time
    late = Event(seq=4, kind=Kind.SPEECH, day=1, phase="day_speech", visibility="all",
                 payload={"text": "我是预言家。", "act": "accuse", "target": 2},
                 actor=1, t_wall=time.time())
    assert compress.render_line(late) == compress.render_line(ALL_KIND_EVENTS[3])


# -------------------------------------------------------------------- 2. estimator bias
def test_fullwidth_punctuation_counts_as_a_token_not_a_quarter():
    """The old classifier used a CJK *ideograph* range, so 。 and 、 were priced at 0.25.

    Chinese prose is ~8% sentence-final punctuation, and the estimator's only job is to
    not under-promise an endpoint that hard-rejects near 20k.
    """
    punct = "。。" * 50
    assert compress.estimate_tokens(punct) >= 90, compress.estimate_tokens(punct)
    assert compress.estimate_tokens("a" * 100) < 40


def test_estimator_errs_high_against_the_measured_ratio():
    """calibration.md measured zh at 0.8053 tokens/char; the estimator must overshoot."""
    sample = "昨晚3号说他自己查杀了5号，今天票型却是2号，我觉得他很可疑。"
    assert compress.estimate_tokens(sample) >= 0.8053 * len(sample)


# --------------------------------------------------------------------- 3. the belief card
def test_belief_card_never_renders_an_empty_alive_list():
    """`存活：号。` was a real output: alive came only from a game_start event and nothing
    said what to do when there wasn't one. Omit the line rather than assert an empty table."""
    st = belief.BeliefState(observer=1, day=2)
    assert "存活：号" not in belief.render_card(st)
    st.alive = (1, 2, 3)
    assert "存活：1、2、3号" in belief.render_card(st)


def test_alive_is_seeded_from_the_opening_seat_list(tmp_path):
    log, gs, deal = nine_seat_log(tmp_path)
    assert belief.build_belief(1, log.all()).alive == tuple(sorted(deal))


def test_claims_render_with_something_after_the_seat(tmp_path):
    """A speech carrying no `act` used to print "- [e4] 1号 " and then nothing."""
    log, gs, deal = nine_seat_log(tmp_path)
    st = belief.build_belief(2, log.all())
    lines = [l for l in belief.render_card(st).splitlines() if l.startswith("- [")]
    assert lines
    for l in lines:
        assert l.split("号 ", 1)[1].strip(), l


def test_seer_result_reaches_only_the_seat(tmp_path):
    log, gs, deal = nine_seat_log(tmp_path)
    seer = belief.build_belief(gs.seer_seat, log.all())
    outsider = belief.build_belief(next(s for s, r in deal.items() if r == "villager"), log.all())
    assert any(c.label == "seer_verdict" for c in seer.claims)
    assert not any(c.label == "seer_verdict" for c in outsider.claims)


def test_belief_card_states_no_ranking(tmp_path):
    """R12: a card that told the model whom to suspect would make M6 measure a copy."""
    log, gs, deal = nine_seat_log(tmp_path)
    st = belief.build_belief(gs.seer_seat, log.all())
    card = belief.render_card(st)
    for phrase in ("最可疑", "建议", "应该投", "结论"):
        assert phrase not in card, card


# ------------------------------------------------------------------------- 4. assembly
def _prompt(tmp_path, seat, *, assigned=None):
    log, gs, deal = nine_seat_log(tmp_path)
    legal = rules.legal_actions(gs, seat)
    if assigned:
        legal = state.LegalSet(acts=legal.acts, targets=legal.targets,
                               allow_pass=legal.allow_pass, assigned_act=assigned)
    p = info.percept_for(seat, log.all())
    pr = assemble.assemble(cfg=Config(), percept=p, seat_role=deal[seat],
                           persona=persona.sample_persona(random.Random(seat)),
                           belief=belief.build_belief(seat, log.all()),
                           legal=legal, phase=gs.phase)
    return pr, log, gs, deal


def test_c4_does_not_claim_an_assignment_that_was_never_made(tmp_path):
    pr, *_ = _prompt(tmp_path, 1)
    assert "指派你本轮的 act" not in pr.messages[2]["content"]
    pr2, *_ = _prompt(tmp_path, 1, assigned="accuse")
    assert "act = accuse" in pr2.messages[2]["content"]


def test_region_b_carries_every_public_event_id(tmp_path):
    """A working renderer is not a working region: B is built by plan_fold + chrono_bytes,
    and a window of 0 or an over-eager filter empties it without raising anything.

    The loop runs over `chronicle`, not over every public event: a COMPACTION marker is public and is
    deliberately *not* in B (it summarises this block, and its fresh seq would rewrite the
    cached prefix). That exception is pinned from the other side by
    `test_a_marker_is_public_but_never_becomes_chronicle`.
    """
    pr, log, gs, deal = _prompt(tmp_path, 1)
    b = pr.messages[1]["content"]
    for e in compress.chronicle(info.percept_for(1, log.all()).events):
        assert f"[{info.eid(e.seq)}]" in b, f"{e.kind} seq={e.seq} missing from region B"
    assert "{" not in b


def test_private_events_never_appear_in_region_b(tmp_path):
    for seat in range(1, 10):
        pr, *_ = _prompt(tmp_path, seat)
        b = pr.messages[1]["content"]
        assert "今晚刀6号" not in b, f"wolf chat leaked into B for seat {seat}"
        assert "你查验的" not in b, f"seer result leaked into B for seat {seat}"


def test_every_seat_shares_identical_region_a_and_b(tmp_path):
    """The prefix-cache argument in plan §5 is only true if these bytes match by seat."""
    heads = {"".join(m["content"] for m in _prompt(tmp_path, seat)[0].messages[:1])
             for seat in range(1, 10)}
    assert len(heads) == 1, "A+B differ between seats — there is no shared cached prefix"


FICTION = re.compile(r"虚构|不是本局|不属于本局")


def test_the_example_labels_its_own_ids_before_a_seat_can_copy_them(tmp_path):
    """M4's `example_copy_turns` counts seats that cited the worked example's numbers, and the
    cheap answer is a clause in the example itself. A disclaimer *after* the ids is one the
    model has already pasted past — region A is read top-down and the example is its last
    block — so this guard is positional: the warning must precede the first `[eNNN]`.

    Cross-module on purpose: the ids come from the text, the clause from the same file, and
    `assemble` is what proves both reach the model.
    """
    from wolfengine.prompts.templates import EXAMPLE_EVENT_IDS

    assert EXAMPLE_EVENT_IDS, "no ids to guard means the derived set went stale, not the prompt"
    b = _prompt(tmp_path, 1)[0].messages[0]["content"]
    start = b.index("【示例】")
    first_id = b.index("[e", start)  # CONTRACT spells `[eNNN]` too, and it sits earlier
    assert FICTION.search(b[start:first_id]), \
        "region A shows citable-looking ids without saying they are not from this game"


def test_prompt_stays_inside_the_ceiling_at_a_plausible_length(tmp_path):
    pr, *_ = _prompt(tmp_path, 1)
    assert not pr.over_ceiling
    assert pr.total_tokens < Config().tokens.absolute_ceiling


# ---------------------------------------------------- 4b. plan §5 的每一格都要有读数
SECTION_OF_HEADER = {"局况": "B0", "你的性格参数": "C1", "你目前掌握的事实": "C2",
                     "你的私有信息": "C3", "本轮任务": "C4"}


def _shipped_block(text: str, key: str) -> str | None:
    """从**发出去的那串字节**里抠出一格：从它的标题行起，到下一个 `== ` 标题行为止。

    这一格没出现时返回 None，而不是空串——`estimate_tokens("")` 是 1 不是 0（每段非空文本
    都 +1），拿空串当"没有这一块"会把 0 读成 1。

    和 `assemble.block_tokens` 是两条独立的路——这边按标题行找边界，那边按装配时用的
    `"\\n\\n"` 分块——所以两边走岔就会红：改了标题措辞、或者读数取自改动之前的草稿，
    拿同一个函数对同一份文本再算一遍是看不出来的（`#66` 修的就是"读数与名字不是一回事"）。
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"== {key}")), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("== ")),
               len(lines))
    body = lines[start:end]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body)


def test_every_sub_budget_plan_5_names_is_measured_from_the_shipped_bytes(tmp_path):
    """B0 和 C1–C4 在 plan §5 的预算表里各占一格，`RegionBudget` 里各有一个数，而装配器
    只报 A/B/C/B1/B2 五段——那五格既没人读（`#62` 因此在门口拒掉 `--set A.regions.b0`），
    也没人量，于是"C1 装不装得进 250"至今只是一句散文。

    这一条钉的是"数的是发出去的那段字节"：逐座位循环不是顺手，`C3` 只有狼和预言家有私有
    事件才有内容，而 `A`/`B`/`C` 三个总数已经在账上，任何一格算错了别的块都会露出来。
    """
    tok = Config().tokens

    def est(text: str, key: str) -> int:
        blk = _shipped_block(text, key)
        return compress.estimate_tokens(blk, tok) if blk is not None else 0

    seen_private = set()
    for seat in range(1, 10):
        pr, *_ = _prompt(tmp_path, seat)
        toks = pr.region_tokens
        assert {"B0", "C1", "C2", "C3", "C4"} <= set(toks), f"{seat}号缺格：{sorted(toks)}"
        assert toks["B0"] == est(pr.messages[1]["content"], "局况"), f"{seat}号 B0"
        for key, name in SECTION_OF_HEADER.items():
            if name == "B0":
                continue
            assert toks[name] == est(pr.messages[2]["content"], key), f"{seat}号 {name}"
        if toks["C3"]:
            seen_private.add(seat)
    assert seen_private, "fixture 空了：没有一个座位有私有事件，C3 那格就恒为 0"
    assert len(seen_private) < 9, "人人都有私有事件，那 C3=0 那一半就没被测过"


def test_an_unbudgeted_block_is_not_counted_into_the_neighbour_it_follows(tmp_path):
    """`== 上一轮被拒 ==` 在 plan §5 的表里没有格子，而它紧跟在 C4 后面。

    分块规则是"以 `== ` 开头就换东家，没认领的块谁也不进"。忘了这一条，被拒重问那一段会
    并进本轮任务的账上：`C4` 凭空涨一截，而涨的那一节内容恰恰是"上一轮模型说了什么"——
    重问那一臂的处理效应就这么进了反塌缩的预算表里。
    """
    log, gs, deal = nine_seat_log(tmp_path)
    seat = 1
    pr = assemble.assemble(cfg=Config(), percept=info.percept_for(seat, log.all()),
                           seat_role=deal[seat], persona=persona.sample_persona(random.Random(seat)),
                           belief=belief.build_belief(seat, log.all()),
                           legal=rules.legal_actions(gs, seat), phase=gs.phase,
                           retry_note="上一轮的 act=listen 不在合法动作里，换一个。")
    c = pr.messages[2]["content"]
    assert "== 上一轮被拒 ==" in c, "fixture 空了：被拒说明没进 prompt"
    toks = pr.region_tokens
    assert toks["C4"] == compress.estimate_tokens(_shipped_block(c, "本轮任务"), Config().tokens), toks
    tail = c[c.index("== 本轮任务 =="):]
    assert toks["C4"] < compress.estimate_tokens(tail, Config().tokens), (
        f"C4={toks['C4']} 而任务块到文末是 {compress.estimate_tokens(tail, Config().tokens)}："
        "被拒的那一段被算进了任务的预算")


def test_the_sub_budget_readings_are_zero_rather_than_absent_when_a_block_is_empty(tmp_path):
    """没私有事件的座位，`C3` 必须是 0 而不是"这一格不在"。

    缺键和读数为 0 是两件事：`region_budget_check` 对缺失的写法是 `None`（"没测到"），
    对 0 的写法是 0（"测到了，没有超"）。把没有私有信息说成没有读数，等于让一个坏消息
    躲在缺数据后面——`tests/test_m3_gate.py` 为同一件事钉过一条。
    """
    quiet = [s for s in range(1, 10) if _prompt(tmp_path, s)[0].region_tokens["C3"] == 0]
    assert quiet, "找不到一个没有私有事件的座位，这一条就没在钉任何东西"
    toks = _prompt(tmp_path, quiet[0])[0].region_tokens
    assert toks["C3"] == 0, toks


def test_the_c2_reading_is_the_card_that_shipped_not_the_card_that_was_drafted(tmp_path):
    """C 区那一刀改写主张卡，读数必须跟着变。

    如果 `C2` 是在拼装时顺手记下草稿的长度，上面两条用例都不会红（它们对的是同一份草稿
    和同一份文本），而账上就再也看不出"这一桌的 belief card 被削过"——被削掉的内容是
    处理效应，不是记账细节。这里拿未削的整卡长度去比，两个数必须不同。
    """
    _c, pr, card = _seer_card_over_cap(tmp_path, 400)
    tok = Config().tokens
    shipped = compress.estimate_tokens(
        _shipped_block(pr.messages[2]["content"], "你目前掌握的事实"), tok)
    assert pr.region_tokens["C2"] == shipped, pr.region_tokens
    drafted = compress.estimate_tokens(card, tok)
    assert shipped < drafted, f"刀没进账：C2={shipped} 而草稿={drafted}"
    assert pr.region_tokens["C"] <= 400, pr.region_tokens


# ------------------------------------------------------------------------------ 5. gates
def test_citation_gate_needs_the_full_log_to_tell_a_lie_from_a_leak(tmp_path):
    log, gs, deal = nine_seat_log(tmp_path)
    known = frozenset(info.eid(e.seq) for e in log.all())
    act = schema.Action(act="accuse", target=1, speech="3号有问题", evidence=["e999"])

    blind = legality.check_action(act, legal=rules.legal_actions(gs, 1),
                                  percept=info.percept_for(1, log.all()),
                                  phase=state.Phase.DAY_SPEECH, role="villager")
    assert blind.citation_stats["invented"] == [], "cannot tell without the log, so must not guess"
    assert blind.citation_stats["not_visible"] == ["e999"]

    sighted = legality.check_action(act, legal=rules.legal_actions(gs, 1),
                                    percept=info.percept_for(1, log.all()),
                                    phase=state.Phase.DAY_SPEECH, role="villager", known_ids=known)
    assert sighted.citation_stats["invented"] == ["e999"]
    assert sighted.citation_stats["not_visible"] == []


def test_a_real_event_the_seat_cannot_see_is_not_visible_not_invented(tmp_path):
    log, gs, deal = nine_seat_log(tmp_path)
    known = frozenset(info.eid(e.seq) for e in log.all())
    outsider = next(s for s, r in deal.items() if r == "villager")
    v = legality.check_action(
        schema.Action(act="accuse", target=1, speech="你们别搞错了", evidence=["e13"]),
        legal=rules.legal_actions(gs, outsider), percept=info.percept_for(outsider, log.all()),
        phase=state.Phase.DAY_SPEECH, role="villager", known_ids=known)
    assert v.citation_stats["not_visible"] == ["e13"], "the wolf chat exists; this seat may not read it"
    assert v.citation_stats["invented"] == []


def test_invented_id_is_a_hard_violation_even_in_speech(tmp_path):
    """The citation is addressed to the engine, so refusing a fake id costs the game nothing."""
    log, gs, deal = nine_seat_log(tmp_path)
    known = frozenset(info.eid(e.seq) for e in log.all())
    v = legality.check_action(
        schema.Action(act="probe", target=None, speech="我有证据", evidence=["e404"]),
        legal=rules.legal_actions(gs, 1), percept=info.percept_for(1, log.all()),
        phase=state.Phase.DAY_SPEECH, role="villager", known_ids=known)
    assert any("invented_event_ids" in x for x in v.violations), v.violations
    assert not v.ok


def test_the_impossible_percept_flag_fires_on_the_measured_failure(tmp_path):
    """The endpoint really said 昨晚我听到了狼叫 as a villager. This turns that anecdote
    into a regression, and keeps speech a soft gate so the signal survives being flagged."""
    log, gs, deal = nine_seat_log(tmp_path)
    v = legality.check_action(
        schema.Action(act="accuse", target=1, speech="昨晚我听到了狼叫，1号是狼", evidence=["e4"]),
        legal=rules.legal_actions(gs, 1), percept=info.percept_for(1, log.all()),
        phase=state.Phase.DAY_SPEECH, role="villager")
    assert any(f.startswith("impossible_percept") for f in v.flags), v.flags
    assert v.ok, "speech is a soft gate by design"


def test_a_wolf_is_not_flagged_for_night_perception(tmp_path):
    """It heard the kill because it made the kill. Flagging this would teach M4 to lie."""
    log, gs, deal = nine_seat_log(tmp_path)
    wolf = next(s for s, r in deal.items() if r == "wolf")
    v = legality.check_action(
        schema.Action(act="probe", target=None, speech="昨晚我们动的手，别问是谁", evidence=["e4"]),
        legal=rules.legal_actions(gs, wolf), percept=info.percept_for(wolf, log.all()),
        phase=state.Phase.DAY_SPEECH, role="wolf")
    assert not any(f.startswith("impossible_percept") for f in v.flags), v.flags


def test_the_over_long_flag_the_gate_writes_is_the_one_the_renderer_reads(tmp_path):
    """One fact, two spellings in two modules: `legality` appends `speech_too_long:{len}` and
    `render_html` maps a flag with exactly that prefix to 〔发言超长〕. Rename either side and
    nothing downstream notices — the audience gets a transcript in which every speech looks
    brief, which is the good news nobody measured.

    The threshold is read from the module that owns it instead of written as a literal here: a
    test hard-coding 141 keeps passing when someone moves it to 400.
    """
    log, gs, deal = nine_seat_log(tmp_path)
    legal, percept = rules.legal_actions(gs, 1), info.percept_for(1, log.all())

    def gate(speech: str) -> list[str]:
        v = legality.check_action(schema.Action(act="accuse", target=1, speech=speech,
                                                evidence=["e4"]),
                                  legal=legal, percept=percept,
                                  phase=state.Phase.DAY_SPEECH, role="villager")
        assert v.ok, "超长是软闸门：拒掉它等于把 M4 要量的行为连同证据一起删了"
        return [f for f in v.flags if f.startswith("speech_too_long")]

    exactly = "长" * legality.SPEECH_SOFT_LIMIT
    assert gate(exactly) == [], f"正好到线的发言被说成超长（限 {legality.SPEECH_SOFT_LIMIT}）"
    over = gate(exactly + "。")
    assert over, "刚过线一格就该留标记，否则这个闸门从不响"
    assert over[0] == f"speech_too_long:{len(exactly) + 1}", over

    marked = ev(90, Kind.SPEECH, actor=1, text=exactly + "。", meta={"flags": over})
    assert "〔发言超长〕" in render_html.markers(marked), (
        "闸门写下的拼写和渲染器读的拼写不是同一份：标记在日志里，屏幕上永远看不见")
    assert "〔发言超长〕" not in render_html.markers(
        ev(91, Kind.SPEECH, actor=1, text=exactly, meta={"flags": gate(exactly)}))


# ------------------------------------------------------------------------ 6. end to end
def _seer_card_over_cap(tmp_path, cap: int, n_extra: int = 12, belief_cap: int = 450):
    # A day-3 card with `n_extra` fresh accusations plus this seat's own check result. The tail
    # is the point: `render_card` puts the roster first and the newest claims last, so a trim
    # that keeps the head of the card cuts exactly what the rest of the module works to keep
    # (`claims[-14:]`, `priv[-8:]`) — and the check result is nowhere else: that night
    # sentence is visible to one seat only, so it never enters region B at all.
    log, gs, deal = nine_seat_log(tmp_path)
    seat = gs.seer_seat
    st = belief.build_belief(seat, log.all())
    extra = [belief.Claim(seq=100 + i, day=3, actor=seat + 1 + (i % 6), label="accuse",
                          target=1 + (i % 7), note=f"第{i}条理由，写得长一点好一眼看出哪几条被砍了")
             for i in range(n_extra)]
    own = belief.Claim(seq=900, day=3, actor=seat, label="seer_verdict", target=5, note="wolf")
    fat = belief.BeliefState(observer=seat, claims=list(st.claims) + extra + [own],
                             suspicion=st.suspicion, credibility=st.credibility, day=3,
                             alive=st.alive, dead=st.dead)
    card = belief.render_card(fat)
    if n_extra >= 12:
        assert len(card.splitlines()) > 8, "the fixture must overflow the old head-cut"
    pr = assemble.assemble(
        cfg=Config(regions=RegionBudget(c_total=cap, c_belief=belief_cap)),
        percept=info.percept_for(seat, log.all()),
        seat_role=deal[seat], persona=persona.sample_persona(random.Random(seat)),
        belief=fat, legal=rules.legal_actions(gs, seat), phase=state.Phase.DAY_SPEECH)
    return pr.messages[2]["content"], pr, card


def test_c_thinning_keeps_the_newest_claims_and_the_seer_record(tmp_path):
    c, pr, card = _seer_card_over_cap(tmp_path, 400)
    assert pr.region_tokens["C"] <= 400, (
        f"C 瘦完仍超预算 {pr.region_tokens['C']}：还能砍的时候就要继续砍，"
        "这条用例的预算高于那一桌的地板")
    assert "你自己的查验记录" in c, (
        "被砍掉的正是模型无处可查的那一样：夜里那句查验结果只发给这一个座位，"
        "公开编年史里从来没有第二份")
    assert "第11条理由" in c, "留下的必须是最近的指控，不是名册后面那几条最旧的"
    assert "第0条理由" not in c, (
        f"最旧的那条主张还占着预算：C={pr.region_tokens['C']}\n{c}")
    assert pr.card_claims_dropped == card.count("- [") - c.count("- ["), (
        f"刀的读数（{pr.card_claims_dropped}）与文本上少的行数不一致：那是一句没有对家的声明")


def test_c_thinning_stops_at_the_floor_without_eating_the_seer_record(tmp_path):
    c, pr, card = _seer_card_over_cap(tmp_path, 200)
    assert "你自己的查验记录" in c, "砍到地板以下时先该停手，而不是把查验记录也交出去"
    assert not any(ln.startswith("- [") for ln in c.splitlines()), (
        "预算连一条主张都装不下，就一条都不该留：留着的就是没生效的那一刀")
    assert "公开主张" not in c, (
        "主张行被砍空之后还留着表头，等于给模型一个指向空列表的标题")
    assert pr.card_claims_dropped == card.count("- ["), (
        "地板那一支也是动了刀，读数不能只记中间那条 `return`")


def test_c_under_cap_hands_over_the_whole_card(tmp_path):
    """没超预算就不许动刀。

    这一侧不是"顺手也测一下"：把守卫的尺子拿错成 `c_persona`（250）时，上面两条超预算的用例
    分辨不出来——它们只要求"砍到装得下"，用错尺子恰好也砍了。只有"本来就没超"这一格能证明
    守卫读的是 `c_total`。
    """
    c, pr, card = _seer_card_over_cap(tmp_path, 1450, n_extra=3)
    assert pr.region_tokens["C"] <= 1450, pr.region_tokens
    assert "第0条理由" in c and "第2条理由" in c, (
        f"C={pr.region_tokens['C']} 在上限以内却被动了刀：\n{c}")
    assert c.count("- [") == card.count("- ["), "整卡原样交接，一条主张都不该少"
    assert "公开主张" in c


def test_the_c2_ruler_bites_on_its_own_while_the_total_stays_shut(tmp_path):
    """`regions.c_belief` 必须是**单独**能下刀的一把尺子，不能只是 `c_total` 的陪衬。

    这一桌把 `c_total` 留在出厂的 1450，只把 C2 的上限压到 400：整段 C 在动刀前实测 672 tok
    （11:22:16Z），离 1450 差着一倍还多，所以"砍了"这个事实只有 `c_belief` 能解释。砍的幅度
    也要有数：每砍一条主张省 29 tok，草稿 459 → keep=13 是 430、keep=12 是 402、keep=11 是
    373（11:22:57Z 实测），所以最少的那一刀恰好落在 11 条——多砍一条就是另一种错（把预算当
    指标去凑整），少砍一条则根本没生效。`- 3` 是"草稿 14 条窗口减去被砍的 3 条"，不是魔数。
    """
    c, pr, card = _seer_card_over_cap(tmp_path, 1450, n_extra=20, belief_cap=400)
    assert pr.region_tokens["C2"] <= 400, pr.region_tokens["C2"]
    assert c.count("- [") == card.count("- [") - 3, (
        f"最少的一刀该剩 11 条主张：草稿 {card.count('- [')} 条、发出 {c.count('- [')} 条")
    assert pr.card_claims_dropped == 3, (
        "砍掉三条却不在日志里留数，读者只能从长度反推——而 `region_tokens` 记的是砍完以后的"
        "长度，草稿原本多长它一个字都不说")
    assert "第19条理由" in c and "第9条理由" in c, "留下的必须是最新的几条"
    assert "第8条理由" not in c, "被砍的必须是名册最旧的三条"
    assert "你自己的查验记录" in c, "C2 的地板与 C 的地板是同一条：查验记录不计数、也不被交出去"


def test_a_c_belief_loose_enough_to_hold_the_card_hands_it_over_whole(tmp_path):
    """反方向：`c_belief` 宽到装得下整张卡时，一个字都不许动。

    只有上面那一条的话，"任何让 C2 变小的改动都算通过"这个假判据混得过去——把守卫写成
    `if card_over_cap or True:` 也照样绿。这一格用同一份超预算草稿（20 条新主张、C 总量同样
    没超），只把尺子放宽到 9999，于是唯一能解释"14 条全在"的就是守卫真的在读 `c_belief`。
    """
    c, pr, card = _seer_card_over_cap(tmp_path, 1450, n_extra=20, belief_cap=9999)
    assert pr.region_tokens["C2"] == 459, pr.region_tokens
    assert pr.card_claims_dropped == 0, "没动刀却要写 0：这一格和 3 一样是给人读的数"
    assert c.count("- [") == card.count("- [") == 14, (
        f"尺子放宽了还动刀：发出 {c.count('- [')} 条 / 草稿 {card.count('- [')} 条")



def test_the_knife_count_survives_into_the_request_record(tmp_path):
    """装配器知道砍了几条不算数——只有 `Event.request` 里那一格才算，日志是唯一留存的产物。

    `agent` 把 `payload_for_log` 的返回整个写进日志，所以那张白名单就是唯一的出口：少加一行，
    `Prompt` 上的字段照样活得好好的、上面每一条断言照样绿，而落盘的文件里没有它——读到旧批次的
    人只能猜。这里用 `c_total=400` 那一桌，因为要记的必须是一个非零的数：拿出厂预算来测，
    "恒 0"和"根本没写"在断言里长得一样。
    """
    _, pr, _ = _seer_card_over_cap(tmp_path, 400)
    assert pr.card_claims_dropped > 0, "fixture 失效：这一格要有非零的刀可记"
    rec = assemble.payload_for_log(pr)
    assert rec["card_claims_dropped"] == pr.card_claims_dropped, sorted(rec)


def test_a_full_turn_round_trip_needs_no_endpoint(tmp_path):
    """deal → log → percept → belief → legal → assemble → parse → gate, all pure.

    This is the M1 claim in one test: the engine can run a turn without the model, which
    is what makes --mock and --dry-run honest rather than approximate.
    """
    log, gs, deal = nine_seat_log(tmp_path)
    seat = 1
    p = info.percept_for(seat, log.all())
    legal = rules.legal_actions(gs, seat)
    pr = assemble.assemble(cfg=Config(), percept=p, seat_role=deal[seat],
                           persona=persona.sample_persona(random.Random(seat)),
                           belief=belief.build_belief(seat, log.all()),
                           legal=legal, phase=gs.phase)
    raw = ('{"act":"accuse","target":3,"speech":"3号你昨晚那句话根本站不住。",'
           '"belief":{"suspects":[{"seat":3,"why":"逻辑太顺"}]},"evidence":["e11"]}')
    parsed = schema.parse_action(raw)
    assert parsed.action is not None, parsed.errors
    assert parsed.rung == 0
    v = legality.check_action(parsed.action, legal=legal, percept=p,
                              phase=state.Phase.DAY_SPEECH, role=deal[seat],
                              known_ids=frozenset(info.eid(e.seq) for e in log.all()))
    assert v.ok, (v.violations, parsed.errors)
    assert v.citation_stats["valid"] == ["e11"]
