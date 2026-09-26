"""文档里"读者会照抄的东西"必须还能用——用例名、命令行、条数、行号、出厂值、收集数都在扫描范围内。

`docs/*.md` 和 `README.md` 用 `` `test_名字` `` 的形式给每个断言指认"是哪条用例在钉它"，又印了一
批可以直接敲的 `wolf …` 命令，还写了"某个测试文件有几条用例"、"某个源码里的东西在第几行"和"某个配置键
出厂是多少"。这六类串都是读者复核时的入口：写完一轮重构、改个用例名、插一行代码、给某个参数换个叫法、
把某个默认值调一格，文档不会报错，只会留下一个点不到的入口。这一片的缺陷是实测抓到的几条——

* `comparison.md` 点名 `test_each_arm_gets_its_own_gate_verdict` 时漏了后半截；
* `metrics.md` 那句"`wolf run` 没有 `--set`"是**反向**主张，任何"扫有没有过期参数"的机制都看不见
  它（它扫不到不存在的东西），所以另用一张表钉；
* `README.md` 给 `test_calibrate_rehearsal.py` 写的条数少一条（那个文件长了读侧对账，注释没跟着数）；
* `metrics.md` 两处把实验臂压到的值写成 `c_total=250`，和出厂值 `c_total=1450` 同一个形状、两句都
  是真话，而机器分不出"这句说的哪一个"——散文改成"`A.regions.c_total` 压到 250 的那一臂"，裸的
  `key=数字` 从此只报出厂值。
* `README.md` 两处把同一个 `except LogDamage` 指到 `cli.py` 的两个不同号上，而**两个号都是错的**（真实
  那一行在它们下面 3 行）：当时的行号判据取 ±2 行窗口、标识符取整段，于是"这段里有个词在那五行里出现过"
  总能成立。13:48:25Z 实测那两处连同"注释里提到另一个文件名"的一起全绿；判据收成整行、并且同段点过名的
  文件词干不算证据之后，三处都报得出来（`#72`）。

范围钉在这里（`docs/*.md` + `README.md`，对照 `tests/*.py` 的 `def`、`cli.build_parser()` 的活参数、
`tests/*.py` 的模块级 `def test_*` 计数、`src/**.py` 的 AST 字面量）：

* 对照 **AST 里的函数名**而不是 `pytest --collect-only` 的输出。一是 subprocess 让测试不再离线
  自足；二是 addopts 已经带 `-q`，再叠一个 `-q` 会把 collect 输出压成每文件计数，一个"36 条全部
  MISSING"的假警报就是这么来的（见 views.md 里那条 harness 教训）。
* 只收 `def test_*` / `async def test_*` 和测试模块名，不往名字集里灌字符串。文档现在不点名带
  `[参数]` 后缀的用例，而正则也停在 `[` 之前，所以基础函数名足够。
* 只认 `test_` 形状，不认一般标识符。文档里的 `` `region_budget_check` `` 这类引用有真价值，但
  "明确不做"清单里也写着将来才有的函数名，那类引用现在必然报红，报红三次就会被整体关掉。

写文档由此多了五条约束，都是这条扫描连 `README.md` 一起扫的直接后果（README 的"测试"一节把这话
也说给了人看）：

* 讲历史时不能把**错名字**写成代码串。补这个闸门时抓到的第一条缺陷就是它自己那篇文档写漏了后半截，
  而"演示这个错名字长什么样"会立刻让闸门变红——错的样子只能描述，不能贴出来。
* 占位符不能写成 ASCII 形状（`test_xxx` 算一次点空），要写 `test_名字` 这种正则接不住的形式。
* 条数必须**绑在测试模块名上**才算主张，而 `条` 后面不能再接词；同一行那句"套件共 N 条用例（…）"
  里，总数只和它自己括号里的枚举对账。参数化模块不在核对范围内（模块级 `def` 数不等于收集数）。
* 裸的 `` `key=数字` `` 从此是一句**出厂值**主张：键要在 `src/` 里存在、数要就是代码里那个字面量。
  实验臂压到的值不能用这个形状写，把键名和数字分开（"把 `A.regions.c_total` 压到 250"）；日志字段
  （`fallback` 这类只以字符串键存在、值不是字面量的名字）可以写，扫描器认它存在但不核数。
* `` `文件名.py:NNN` `` 从此是一句**今天还对**的主张：被点那一行上必须找得到这句话点过的名字。闸门
  分不出"引用"和"复述一个已经漂走的旧号"（它只认形状，不认语境），所以复述不许用这个形状——写成
  "`cli.py` 的 616 行"这种正则接不住的样子。本轮补这一族时自己撞上过一次：把两个历史号照形状写进了
  README，报红的是那段历史叙述、不是代码，改写法即可。

"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

from wolfengine import cli

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]
TEST_FILES = sorted((ROOT / "tests").glob("test_*.py"))
# 允许文档跨行写代码串（CommonMark 会把换行渲染成空格），所以按"整篇"而不是按行取 token。
CITED = re.compile(r"\btest_[A-Za-z0-9_]+")


def _defined_names() -> set[str]:
    """Every name a reader could actually point at: test functions and test modules."""
    names = {f.stem for f in TEST_FILES}
    for f in TEST_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # async 也要收：`test_live_path` / `test_transport` / `test_llm` / `test_agent_turns`
                # 里 39 条用例是协程，只走 FunctionDef 会把它们整批报成文档过期——第一次跑就是这么
                # 误报了一条真实存在的用例名。
                if node.name.startswith("test_"):
                    names.add(node.name)
    return names


def _stale(bodies: dict[str, str], defined: set[str]) -> dict[str, list[str]]:
    """The check itself, over caller-supplied text: which cited names resolve to nothing.

    A module-level `assert not stale` can't be distinguished from a broken scanner, so the
    blindness has to be provable on synthetic input — see the probe case below.
    """
    out: dict[str, list[str]] = {}
    for doc, body in bodies.items():
        bad = [t for t in sorted(set(CITED.findall(body))) if t not in defined]
        if bad:
            out[doc] = bad
    return out


def test_every_test_named_in_the_docs_resolves():
    stale = _stale({f.name: f.read_text(encoding="utf-8") for f in DOCS}, _defined_names())
    assert not stale, (
        "文档点名的用例在 tests/ 里不存在（改名或被删了），读者按它复核会点空："
        f"{stale}"
    )


def test_a_renamed_case_is_reported_rather_than_waved_through():
    """正面对照：给守卫一份点了假名字的文档，它必须点出来。

    只跑上面那条断言不足以证明这个闸门有用——把 `_stale` 改瞎（永远返回 `{}`），`assert not stale`
    照样绿。这一条不读真实语料，所以它不会被文档变好而削弱：它钉的是"检测能力在"。
    """
    defined = _defined_names()
    assert defined, "名字集是空的，下面的断言就成了自证"
    gone = sorted(n for n in defined if n.startswith("test_"))[0] + "_renamed_away"
    assert gone not in defined
    hit = _stale({"probe.md": f"这条由 `{gone}` 钉住\n"}, defined)
    assert hit == {"probe.md": [gone]}, hit
    # 反向：同一份文本换成真名字，必须干净——否则上一行的红只是"什么都报"。
    assert _stale({"probe.md": f"这条由 `{sorted(n for n in defined if n.startswith('test_'))[0]}` 钉住\n"},
                  defined) == {}


def test_the_guard_itself_can_fail():
    """防"守卫因为找不到文件而永远绿"：docs 和 tests 都必须真的扫到东西。

    一条 `assert not missing` 在 `DOCS` 为空时也成立——那正是 `git init` 之前 `git grep` 返回 0
    的那类假绿。这里断言的是扫到的规模，不是某个具体名字，所以它不会随文档增删而失效。
    """
    assert len(DOCS) >= 5, DOCS
    assert len(TEST_FILES) >= 20, TEST_FILES
    defined = _defined_names()
    cited = {t for body in (f.read_text(encoding="utf-8") for f in DOCS)
             for t in CITED.findall(body)}
    assert len(cited) >= 30, f"只扫到 {len(cited)} 个引用，说明正则或范围坏了"
    assert len(cited & defined) / len(cited) > 0.8, (
        f"命中率 {len(cited & defined)}/{len(cited)}：多半是名字收集方式坏了，不是文档全错了")


# ------------------------------------------------------------- CLI 命令引用
SUBCOMMANDS = ("run", "replay", "audit", "export", "watch", "batch", "compare", "gate")
WOLF = re.compile(r"\bwolf\s+(?:" + "|".join(SUBCOMMANDS) + r")\b")
FLAG = re.compile(r"(?<![\w-])(-{1,2}[A-Za-z][\w-]*)")


def _command_texts(md: str) -> list[str]:
    """Every command the doc shows a reader typing: fenced blocks plus inline code spans.

    Prose is excluded on purpose, and not for tidiness. `metrics.md` says "`wolf run` 没有
    `--set`" and "出厂预算下的 `--mock` 桌" — a line-based scan of the word `wolf` reads both of
    those as commands and reports two stale flags that don't exist. Code spans are also exactly
    the set of things a reader copy-pastes.
    """
    fenced = [b.group(1) for b in re.finditer(r"```[^\n]*\n(.*?)```", md, re.S)]
    outside = re.sub(r"```[^\n]*\n.*?```", "\n", md, flags=re.S)
    spans = [s for s in re.findall(r"`([^`\n]+)`", outside) if WOLF.search(s)]
    return fenced + spans


def _cited_flags(md: str) -> list[tuple[str, str]]:
    """(subcommand, flag) pairs, with one subcommand governing until the next `wolf` appears.

    Continuation lines (`... --configs A,B \\` + newline + `--set ...`) need the subcommand to
    carry over; text after `&&` belongs to the *next* command, so splitting on the next `wolf`
    rather than on the line break is what makes `wolf batch … && wolf audit …` read correctly.
    """
    pairs: list[tuple[str, str]] = []
    split = re.compile(r"(?=\bwolf\s+(?:" + "|".join(SUBCOMMANDS) + r")\b)")
    for text in _command_texts(md):
        for chunk in split.split(text):
            m = re.match(r"\bwolf\s+(" + "|".join(SUBCOMMANDS) + r")\b", chunk)
            if not m:
                continue
            typed = chunk.split("#", 1)[0]     # `# …` 是文档给人看的注释，不会被敲进终端
            typed = typed.split("|", 1)[0]     # 管道之后是 jq/grep 的参数，不是 wolf 的
            pairs += [(m.group(1), f) for f in FLAG.findall(typed)]
    return pairs


def _cli_surface() -> dict[str, set[str]]:
    """{子命令: 它接受的参数串}，从 `build_parser()` 的活对象上读，不是从 `cli.py` 的源码上猜。

    走 `_actions` 收 `option_strings`，短写法 `-o` 才算得进来（`export` 和 `compare` 都是
    `-o/--out`，README 里就写着"不写 -o 就落在日志旁边"）；按引号里的字面量扫 `cli.py` 只看得见
    `--x` 形状，那种第二处实现会漏掉文档里真在用的一条。
    """
    ap = cli.build_parser()
    subs = next(a for a in ap._actions if isinstance(a, argparse._SubParsersAction))  # noqa: SLF001
    return {name: {opt for act in p._actions for opt in act.option_strings}  # noqa: SLF001
            for name, p in subs.choices.items()}


def _stale_flags(cited: list[tuple[str, str]],
                 surface: dict[str, set[str]]) -> list[tuple[str, str]]:
    """The check itself, so blindness is provable on synthetic input like `_stale` above."""
    return sorted({(s, f) for s, f in cited
                   if s not in surface or f not in surface.get(s, set())})


def test_every_flag_the_docs_show_is_offered_by_that_subcommand():
    surface = _cli_surface()
    cited = [(s, f) for doc in DOCS for s, f in _cited_flags(doc.read_text(encoding="utf-8"))]
    stale = _stale_flags(cited, surface)
    assert not stale, f"文档让读者敲一个 CLI 不认的参数（改名或删过）：{stale}"


def test_the_negative_flag_claims_in_the_docs_are_negatives():
    """"`compare` 这一侧没有 `--set`"（`metrics.md`）也是一条主张：将来给它加上，这句就成了假话。

    正面引用由上一条管，反向引用没有别的机制能管——扫不到"不存在的参数"，只能把话抄成表。
    这张表在 2026-09-24 换过一行：`run` 原先也在那句限界里（"没有 `--set`"），加上 `--set` 的那天
    它红了一次，指名要改文档，所以 `metrics.md` 与 `README.md` 里的引文一起跟着搬了家。反向主张
    会随代码追上而变假，而"某参数不存在"扫不出来——只能靠这一张表。
    """
    surface = _cli_surface()
    assert "run" in surface and "batch" in surface and "compare" in surface, surface.keys()
    assert "--set" in surface["run"], "run 不再认 --set 了，`metrics.md` 那句『两处都能进』要改"
    assert "--set" in surface["batch"]
    assert "--set" not in surface["compare"], "compare 现在认 --set 了，`metrics.md` 那句反向主张要改"


def test_the_flag_scanner_sees_a_stale_flag_when_there_is_one():
    """正面对照：一条只报"没找到问题"的扫描器，和一条什么都没扫的扫描器无法区分。

    合成文本里埋一个假参数和一个"围栏之外的抱怨"，前者必须红、后者必须不被当成命令。
    """
    surface = _cli_surface()
    probe = ("打一批：`wolf batch --configs A --frobnicate 1` 然后\n\n"
             "```\nwolf batch --out /tmp/x --configs A,B \\\n  --set B.temperature=0.6\n"
             "wolf audit /tmp/x/A/*.jsonl | jq '.kinds'\n"
             "wolf audit \"$LOG\" --calibration c.json | grep -A7 '\"calibration\"'\n"
             "wolf compare /tmp/x --axis temperature   # 退出码：0 结论 / 1 拒绝 --not-a-flag\n```\n\n"
             "`wolf compare` 没有 `--set`，别照抄。\n")
    cited = _cited_flags(probe)
    assert ("batch", "--frobnicate") in cited, cited
    assert ("batch", "--set") in cited, f"续行没接上同一个子命令：{cited}"
    assert ("audit", "--calibration") in cited, cited
    assert ("audit", "-A7") not in cited, f"管道之后的 grep 参数被算到了 wolf 头上：{cited}"
    assert ("compare", "--not-a-flag") not in cited, f"# 注释被当成要敲的参数了：{cited}"
    assert ("compare", "--axis") in cited, cited
    assert ("compare", "--set") not in cited, "把散文当命令扫了（`wolf compare` 没有 `--set` 那一句）"
    assert ("audit", "--frobnicate") not in cited, "参数被算到了下一条命令头上"
    assert _stale_flags(cited, surface) == [("batch", "--frobnicate")], _stale_flags(cited, surface)


def test_the_flag_scanner_actually_scans_the_docs():
    cited = [(s, f) for doc in DOCS for s, f in _cited_flags(doc.read_text(encoding="utf-8"))]
    assert len(cited) >= 25, f"只扫到 {len(cited)} 个参数引用，多半是围栏或正则坏了"
    assert len({s for s, _ in cited}) >= 5, cited


def test_the_scanner_covers_every_subcommand_the_parser_offers():
    """`SUBCOMMANDS` 一旦落后于 `build_parser()`，新命令在文档里的参数就扫不到，而且永远绿。

    上面两条扫描用例判的是"扫到的对不对"，扫不到的那一片不会自己报警。这条守卫在建起来的那一秒
    正好有用：给 CLI 加一个子命令而忘了把名字进这张表，文档里写 `wolf gate --frobnicate 1`
    没有任何东西会红。
    """
    surface = set(_cli_surface())
    assert set(SUBCOMMANDS) == surface, (
        f"文档里只 {sorted(set(SUBCOMMANDS))} 会被扫，CLI 实际给的是 {sorted(surface)}")


# ------------------------------------------------------------- 用例计数引用
# 两种写法都算"把条数绑在了模块名上"：`test_x.py`（14 条 …`、围栏里 `pytest tests/x.py # 14 条`，
# 以及枚举 `（test_a.py 23 + test_b.py 29）` 里裸着的数。
# 落点判据是一条否定式而非字符表：`条` 后面还接得上词，就是在数别的东西（语料里的 `24 条自报文本`、
# `22 条渲染行`、`610 条里`），数用例的那一句到这里就收住了。否定式不会随文档换个说法就漏——
# 白名单每加一种新写法都得改一次表，而"后面是词"这个判据一次覆盖全部。
MINIMAL = re.compile(r"(test_[a-z0-9_]+)\.py`?[^\n]{0,30}?(\d+) 条(?=\W|$)")
BARE = re.compile(r"(test_[a-z0-9_]+)\.py`?\s+(\d+)(?=\s*(?:\+|、|[）)]|$))")


def _case_claims(bodies: dict[str, str]) -> list[tuple[str, int, str, int]]:
    """(文档, 行号, 模块名, 文档写的条数)——同一行里模块名后面出现的"N 条"或枚举裸数。"""
    out = []
    for doc, body in bodies.items():
        for no, line in enumerate(body.splitlines(), 1):
            out += [(doc, no, m.group(1), int(m.group(2)))
                    for pat in (MINIMAL, BARE) for m in pat.finditer(line)]
    return out


TOTAL = re.compile(r"(\d+) 条用例（([^（）]*)）")


def _case_totals(bodies: dict[str, str]) -> list[tuple[str, int, int, int]]:
    """(文档, 行号, 写出的总数, 括号里枚举相加) —— 只报对不上的那些。

    Enumerations are `（`a.py` 23 + `b.py` 29）`: only digits separated by `+` count as members, so
    a parenthetical that isn't an enumeration (a file name, a clause) can't be mistaken for one.
    """
    out = []
    for doc, body in bodies.items():
        for no, line in enumerate(body.splitlines(), 1):
            for m in TOTAL.finditer(line):
                if "+" not in m.group(2):
                    continue
                total = sum(int(n) for n in re.findall(r"\d+", m.group(2)))
                if total != int(m.group(1)):
                    out.append((doc, no, int(m.group(1)), total))
    return out


def _def_counts() -> dict[str, int]:
    """{模块名: 模块级 `def test_*` 的条数}，与 `_defined_names` 同一套取法（不引 subprocess）。"""
    counts: dict[str, int] = {}
    for f in TEST_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        counts[f.stem] = sum(1 for n in tree.body
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and n.name.startswith("test_"))
    return counts


COLLECTED = re.compile(r"跑起来 (\d+) 个用例")
MODULE_NEAR = re.compile(r"(test_[a-z0-9_]+)\.py")


def _collected_claims(bodies: dict[str, str]) -> list[tuple[str, int, str | None, int]]:
    """(文档, 行号, 模块名, 文档写的收集数)——名字取那句话**上方**最近的一个测试模块。

    扫之前先把换行折成空格：Markdown 会在渲染时把换行当空格，"跑起来 57" 落在行尾、"个用例"落在
    下一行行首就是同一句话，按物理行扫会**整个看不见**它（不是报错，是安静地少一格）。归属窗口取
    ±2 行；"上方"是判据的一半：
    只往上看，才不会出现"下一句的名字替上一句背书"。一句没有主人的 `mod=None` 记成没主人并被报红
    ——和行号那一族一样，没人能核对的主张就是缺陷。
    """
    out = []
    for doc, body in bodies.items():
        flat = body.replace("\n", " ")
        for m in COLLECTED.finditer(flat):
            head = body[:m.start()]
            mods = MODULE_NEAR.findall("\n".join(head.split("\n")[-3:]))
            out.append((doc, head.count("\n") + 1, mods[-1] if mods else None,
                        int(m.group(1))))
    return out


def _collected_counts() -> dict[str, int]:
    """{模块名: pytest 收集到的条数}。参数化展开后的条数不在 AST 里，只能把套件收集一遍。

    `-o addopts=` 是必需的：`pyproject.toml` 的 addopts 自带一个 `-q`，命令行再给一个就把 nodeid
    清单压成"每文件计数"两行——那只手感和 README〈测试〉一节记的那格是同一个坑。
    """
    r = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider",
                        "--collect-only", "-q"], cwd=ROOT, capture_output=True, text=True)
    counts: dict[str, int] = {}
    for line in r.stdout.splitlines():
        m = re.match(r"tests/([a-z0-9_]+)\.py::", line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    assert r.returncode == 0 and len(counts) >= 15, f"收集这一步没跑起来：{r.stdout[-300:]}"
    return counts


def test_a_case_count_written_next_to_a_module_name_matches_that_module():
    """文档说"`test_x.py` 14 条"就是一句可核对的话，说错就是要人白跑一次。

    `test_calibrate_rehearsal.py` 从 14 条长到 15 条（`#38` 那轮加了一条读侧对账），README 里那句
    注释没跟着改——这类腐烂是静默的：没有任何一条已存在的守卫数过"每文件几条"。
    """
    counts = _def_counts()
    claims = _case_claims({f.name: f.read_text(encoding="utf-8") for f in DOCS})
    assert len(claims) >= 4, f"只扫到 {len(claims)} 处计数，多半是正则或范围坏了：{claims}"
    bad = [(doc, no, mod, n) for doc, no, mod, n in claims if counts.get(mod) != n]
    assert not bad, f"文档写的条数和模块里的用例数不符：{bad}"


def test_the_collected_count_scanner_fires_on_the_wrong_one_and_names_the_ownerless():
    """"跑起来 N 个用例"是这一族计数的另一半，四格都要它分得开：对的、错的、名字在上一行的、
    整句话没点名模块的。第三格是真实语料的形状（Markdown 折行把模块名留在了上一行），第四格是
    "没名字可核对"的主张——它和行号那一族的处理一样：报，不放过。
    """
    counts = {"test_cli": 56, "test_log_recovery": 57}
    # Sections are split by blank lines: a claim's owner is the nearest module name above it
    # within ±2 lines, and probes that can see each other's names would not test that rule.
    probe = ("`tests/test_cli.py`（现 47 条、跑起来 56 个用例，三条各参数化为 4/6/2 个）\n"
             "\n"
             "`tests/test_cli.py` 跑起来 55 个用例\n"
             "\n"
             "另一半住在 `tests/test_log_recovery.py`（现 46 条，\n"
             "其中四条是参数化的，跑起来 57 个用例）\n"
             "\n"
             "有个文件跑起来 99 个用例，句子里没点名字\n")
    claims = _collected_claims({"probe.md": probe})
    assert len(claims) == 4, claims
    bad = [(no, mod, n) for _, no, mod, n in claims if counts.get(mod) != n]
    assert bad == [(3, "test_cli", 55), (8, None, 99)], bad


def test_a_collected_claim_takes_the_nearest_name_above_and_never_one_from_below():
    """`只看上方` 和 `两个名字里取最近的那个` 是判据的另一半，各钉一格。

    README〈测试〉一节真就长这个形状：`跑起来 57 个用例` 那一行的上方两行里同时出现了
    `test_cli.py` 和 `test_log_recovery.py`（还有一格的名字只在下一行）。取错方向或取错远近
    都会把一句写对了的话报红——或者更糟：把 57 记到别人头上，于是两句错话互相抵消成绿色。
    """
    probe = ("`tests/test_cli.py` 那格钉的是只有开局记录的文件上的座位视图。\n"
             "`tests/test_wiring.py` 那格钉的是 manifest 只有一个读者。\n"
             "`tests/test_log_recovery.py`（现 46 条，\n"
             "其中四条是参数化的，跑起来 57 个用例）\n"
             "\n"
             "还有一格跑起来 12 个用例，它的名字只在下一行：\n"
             "`tests/test_wiring.py`\n")
    claims = _collected_claims({"probe.md": probe})
    assert [(no, mod, n) for _, no, mod, n in claims] == [
        (4, "test_log_recovery", 57), (6, None, 12)], claims


def test_a_claim_folded_in_the_middle_of_the_number_is_still_a_claim():
    """`跑起来 57` 落在行尾、`个用例` 落在下一行行首——按行扫的那只手会**整个看不见**这一格。

    本轮写〈收集数〉那一节时自己就差点写出这么一行（先按行扫再谈归属，折行把主张切成了半句，
    闸门连"没主人"都不会报，因为压根没扫到）。修法是先把整篇的换行折成空格再扫，行号报的是这句话
    **开头**所在那一行。
    """
    probe = ("另一半住在 `tests/test_log_recovery.py`（现 46 条，\n"
             "跑起来 57\n"
             "个用例）\n")
    claims = _collected_claims({"probe.md": probe})
    assert [(no, mod, n) for _, no, mod, n in claims] == [(2, "test_log_recovery", 57)], claims


def test_a_suite_total_is_not_a_collected_claim():
    """`跑起来 ` 那个前缀是有读者的：它把"某个文件收集了几条"和"整套一共几条"分开。

    文档里两种话都在说（〈收集数〉那一节写着整套的模块数与用例总数，〈测试〉开头也写着套件总数），
    而只有前一种能对着某个模块核。正则放宽成 `N 个用例` 就会把后一种也收进来，收到的是没有主人的
    数字——一条常误报的守卫三次之内就会被关掉，所以这一格要在合成语料里钉住，不能靠真实文档里那句
    话碰巧没被扫到。
    """
    probe = ("这一套一共 707 个用例，不是哪个文件的数。\n"
             "另一句：`tests/test_cli.py`（跑起来 56 个用例）\n")
    claims = _collected_claims({"probe.md": probe})
    assert [(no, mod, n) for _, no, mod, n in claims] == [(2, "test_cli", 56)], claims


def test_a_collected_count_written_in_the_docs_matches_what_pytest_collects():
    """文档说"`test_x.py` 跑起来 N 个用例"，N 是收集数（参数化会展开），和条数是两句真话。

    这一族的腐烂是本轮改文档时现出来的：同一份 `test_log_recovery.py` 在 README 里一处写 57、一处
    写 56，而"条数闸门"只管模块级 `def` 数，两句都不归它管。收集数只能问 pytest——参数化用的是
    `@pytest.mark.parametrize` 的字面量列表，从 AST 里数不出展开后的条数。
    """
    counts = _collected_counts()
    claims = _collected_claims({f.name: f.read_text(encoding="utf-8") for f in DOCS})
    assert len(claims) >= 4, f"只扫到 {len(claims)} 处收集数，多半是正则或范围坏了：{claims}"
    bad = [(doc, no, mod, n, counts.get(mod))
           for doc, no, mod, n in claims if counts.get(mod) != n]
    assert not bad, f"文档写的收集数和 pytest 数出来的不符（末列是真数）：{bad}"


def test_the_count_scanner_counts_cases_not_whatever_else_the_line_counts():
    """绑到模块名还不够：同一行里"24 条自报文本"也长这个形状，而它数的不是用例。

    这份语料里真有三个这样的邻居：README 的〈信息隔离〉一节写着"把 24 条自报文本逐条断言"，
    `docs/views.md` 把同一件事写了两遍（24 条自报文本、22 条渲染行）。这里原先记的是行号
    （`views.md:52`、`:53`），而 `.md:NN` 不在行号闸门的形状里——它只认 `.py:NN`——所以那种号
    没人核，写出来就是等着烂：这两格在补〈行号〉那套闸门之前就已经各往下漂了九行。
    把它们误当成用例数会让守卫在文档**没写错**的时候报红，
    而一条常报红的守卫三次之内就会被关掉——所以错的样子必须在合成文本里钉住，不能靠"真实语料
    刚好绿"。反方向同一条用例里一起钉：真的写错数必须被点出来，否则"什么都没报"和"扫不到"无法区分。
    """
    defined = {"test_probe": 15}
    probe = ("重跑：`pytest tests/test_probe.py`（14 条，两秒内）\n"
             "所以 `tests/test_probe.py` 把 24 条自报文本逐条断言\n"
             "`tests/test_probe.py` 对 22 条渲染行做同一件事\n"
             "610 条里有两条在真实时间里等完了退避：`test_probe.py` 的端点不可用局\n"
             "渲染层 52 条用例（`test_probe.py` 23 + `test_probe.py` 29）里，每条都被验过\n"
             "`tests/test_probe.py` 里 15 条\n")
    claims = _case_claims({"probe.md": probe})
    assert [n for _, _, _, n in claims] == [14, 23, 29, 15], claims
    # 上面第一行是"写错的数"：模块里 15 条、文档写 14，守卫必须抓到它（真实语料那条红的就是它）。
    stale = [(doc, no, mod, n) for doc, no, mod, n in claims if defined.get(mod) != n]
    assert len(stale) == 3, f"三处都该红（14 对 15、23 对 15、29 对 15），实到 {stale}"


def test_a_stated_total_has_to_add_up_to_its_own_enumeration():
    """"渲染层 52 条用例（`a.py` 23 + `b.py` 29）"里，总数是第三句独立的主张。

    逐文件那两个数由 `_case_claims` 对 AST 核，就算它们都对，总数仍可以写错——而它错了不会让
    任何一条已存在的断言变红（23 与 29 各自仍然精确）。这一条补的就是这个缝：总数只和它自己
    括号里的枚举比，不引新的知识进来。
    """
    probe = ("渲染层 53 条用例（`test_a.py` 23 + `test_b.py` 29）里，每条都被验过\n"
             "渲染层 52 条用例（`test_a.py` 23 + `test_b.py` 29）里，每条都被验过\n"
             "610 条里有两条在真实时间里等完了退避\n")
    assert _case_totals({"probe.md": probe}) == [("probe.md", 1, 53, 52)]
    real = {f.name: f.read_text(encoding="utf-8") for f in DOCS}
    totals = _case_totals(real)
    assert totals == [], f"文档写的用例总数和它自己列的枚举加不起来：{totals}"


# --------------------------------------------------------------- 出厂值引用
# 文档里 `` `c_total=1450` ``、`` `max_days=6` `` 这种写法是"代码里的这个键出厂是这个数"。
# 和用例名、命令行参数一样，它是读者会照抄的东西：改一次 `Config` 的默认值，散文不会报错，
# 只会留下一句读起来仍然通顺、但已经和代码不符的话。而这一类尤其便宜——数字就在 `` ` `` 里，
# 字面量就在 `src/` 的 AST 里，两边都是本地就能取的，不需要端点。
VALUE_CLAIM = re.compile(r"`([a-z][a-z0-9_]{2,})=(\d+)`")
SRC_FILES = sorted(Path(cli.__file__).resolve().parent.rglob("*.py"))


def _value_claims(bodies: dict[str, str]) -> list[tuple[str, int, str, int]]:
    """(文档, 行号, 键名, 文档写的数)——只取"反引号里 key=整数"这一种形状。"""
    out = []
    for doc, body in bodies.items():
        for no, line in enumerate(body.splitlines(), 1):
            out += [(doc, no, m.group(1), int(m.group(2))) for m in VALUE_CLAIM.finditer(line)]
    return out


def _src_int_literals() -> dict[str, set[int]]:
    """{键名: 代码里绑到这个键上的整数字面量}，三种绑法都收。

    * 赋值目标：模块级常量与 dataclass 字段（`max_days: int = 6`）；
    * 关键字参数的默认值（`def f(..., min_window=4)`）；
    * 位置参数的默认值（`def f(days=6)`，默认值从签名右侧对齐，直接 zip 会错位）。

    收 `set` 而不是单个值是刻意的：同一个键在代码里可以有几处不同的字面量（出厂值之外还有
    分支里的常数），那种键的主张只能核到"文档写的数是其中之一"，核不动就放行——放行的代价是
    少核一处，比误伤一条常红的守卫便宜。
    """
    out: dict[str, set[int]] = {}

    def add(name: str, value: int) -> None:
        out.setdefault(name.lower(), set()).add(value)

    for f in SRC_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant) \
                            and isinstance(node.value.value, int):
                        add(t.id, node.value.value)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, int):
                    add(node.target.id, node.value.value)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                for arg, d in zip(a.kwonlyargs, a.kw_defaults):
                    if isinstance(d, ast.Constant) and isinstance(d.value, int):
                        add(arg.arg, d.value)
                pos = [*a.posonlyargs, *a.args]
                off = len(pos) - len(a.defaults)
                for i, d in enumerate(a.defaults):
                    if isinstance(d, ast.Constant) and isinstance(d.value, int) and off + i >= 0:
                        add(pos[off + i].arg, d.value)
    return out


def _src_names() -> set[str]:
    """`src/` 里真实出现过的名字：赋值目标与参数名，外加被当键用的字符串常量。

    存在的判据要比"绑过整数字面量"宽：日志字段（如 `fallback`）的值是条件表达式或运行时算出来的，
    AST 里核不出一个数，但它在代码里确实是个名字——文档提它不算点空，只是这一闸管不着。反过来，
    一个**从 `src/` 里消失**的键名必须报红，否则"改个字段名"就能让整条主张悄悄漂走。
    """
    out: set[str] = set()
    for f in SRC_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.arg)):
                out.add((node.id if isinstance(node, ast.Name) else node.arg).lower())
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.fullmatch(r"[a-z][a-z0-9_]{2,}", node.value.strip()):
                    out.add(node.value.strip().lower())
    return out


def _stale_values(claims, ints: dict[str, set[int]], names: set[str]) -> list[tuple]:
    """(文档, 行号, 键, 文档写的数, 为什么不符)——名字不存在，或数不在代码的字面量里。"""
    out = []
    for doc, no, key, val in claims:
        if key not in names:
            out.append((doc, no, key, val, "src/ 里没有这个名字"))
        elif key in ints and val not in ints[key]:
            out.append((doc, no, key, val, f"src/ 里是 {sorted(ints[key])}"))
    return out


def test_a_stated_default_value_matches_the_literal_in_src():
    """文档写 `key=数字` 就是可核对的主张：键要在 `src/` 里存在，数要就是代码里那个数。"""
    ints, names = _src_int_literals(), _src_names()
    claims = _value_claims({f.name: f.read_text(encoding="utf-8") for f in DOCS})
    assert len(claims) >= 10, f"只扫到 {len(claims)} 处出厂值引用：{claims}"
    checked = sum(1 for _, _, k, _ in claims if k in ints)
    assert checked >= 8, f"只有 {checked} 处主张真的核到了字面量，扫描范围多半坏了"
    bad = _stale_values(claims, ints, names)
    assert not bad, f"文档写死的出厂值和 src/ 里的字面量不符：{bad}"


def test_the_value_scanner_reads_claims_and_not_every_equal_sign():
    """`=` 在散文里出现的理由比"报一个出厂值"多得多，判据必须只认那一种。

    合成语料里八句主张 + 两行"长得像但不是"：真主张各有归属（对得上、对不上、键存在但核不到数），
    而 `>=`、小数、大写、过短的键名都不该被算成主张——被算上就会在文档没写错的时候报红。
    三句"核得到/核不到"是分别给取数的三条支路写的，删掉任何一条都有一种主张会掉进"点空"那一支：
    `min_len` 只能从**位置参数默认值**取到（签名右对齐，取错一位就记到前一个参数头上），
    `speech_soft_limit` 只能从**模块级赋值**取到，`b2_worst_over_tokens` 只能从**字符串键**取到。
    """
    ints, names = _src_int_literals(), _src_names()
    probe = ("出厂 `c_total=1450`，C 区还有余量\n"
             "那一臂的 `c_total=250` 已经把主张砍光\n"
             "地板是 `min_window=4` 条\n"
             "只记 `fallback=1` 这一格\n"
             "`frobnicate_cap=7` 是个压根不存在的键\n"
             "碎片门槛 `min_len=6` 个字符\n"
             "日志那一格 `b2_worst_over_tokens=9001`\n"
             "发言软上限 `speech_soft_limit=140` 字\n"
             "判据 `passivity_rate >= 0.35` 与 `warmup>=100` 数的是阈值不是出厂值\n"
             "`a=1` 太短，`C_TOTAL=9` 是大写，`temperature=0.7` 是小数\n")
    claims = _value_claims({"probe.md": probe})
    assert [k for _, _, k, _ in claims] == [
        "c_total", "c_total", "min_window", "fallback", "frobnicate_cap", "min_len",
        "b2_worst_over_tokens", "speech_soft_limit"], claims
    bad = _stale_values(claims, ints, names)
    assert [(d, n, k, why) for d, n, k, _, why in bad] == [
        ("probe.md", 2, "c_total", f"src/ 里是 {sorted(ints['c_total'])}"),
        ("probe.md", 5, "frobnicate_cap", "src/ 里没有这个名字")], bad
    # 位置默认值那一支真的把 6 记在了 `min_len` 身上，而不是记给了签名里第一个参数。
    assert ints.get("min_len") == {6}, sorted(ints.get("min_len", set()))
    assert ints.get("speech_soft_limit") == {140}, sorted(ints.get("speech_soft_limit", set()))
    # 日志字段：名字在代码里（只以字符串键出现），值不是字面量，所以这一闸不置可否——不能报红。
    assert "b2_worst_over_tokens" in names and "b2_worst_over_tokens" not in ints
    assert "fallback" in names and "fallback" not in ints
    # 最后两行都不是主张，一条都不该被扫到。
    assert all(no < 9 for _, no, _, _ in claims), claims


LINE_CITE = re.compile(r"([\w.-]+\.py):(\d+)")
# 只有 ≥4 个字符的 ASCII 标识符才算"这句话点到了某个名字"；文件自身的词干（`cli`、`test_cli`）
# 和路径片段拿去比对永远会命中，那不是证据。
STOP = {"src", "docs", "tests", "scripts", "python", "readme", "wolfengine", "self", "args",
        "return", "none", "true", "false", "str", "int", "bool", "path", "prompts"}
PY_CITED = re.compile(r"([\w./-]+)\.py")
SENT_END = re.compile(r"[。！？；;]")
ASCII_TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
QUOTED = re.compile(r"'([^'\n]{1,40})'|\"([^\"\n]{1,40})\"")
BACKTICK = re.compile(r"`([^`\n]{1,60})`")
PATHISH = re.compile(r"\.py$")
CJK = re.compile(r"[぀-ヿ一-鿿]")


def _cited_sentence(block: list[str], idx: int, col: int) -> str:
    """The citation's own sentence, and nothing else from its paragraph.

    Bounded by the nearest blank line (a Markdown paragraph break) and by the nearest 句末标点 on
    either side. Lines are joined with a single space so a wrapped sentence stays one sentence,
    while two identifiers on neighbouring lines can't fuse into a third one.
    """
    start = idx
    while start > 0 and block[start - 1].strip():
        start -= 1
    end = idx
    while end + 1 < len(block) and block[end + 1].strip():
        end += 1
    joined = " ".join(block[start:end + 1])
    at = sum(len(b) + 1 for b in block[start:idx]) + col
    lo = max([0] + [s.end() for s in SENT_END.finditer(joined[:at])])
    hi = min([s.end() for s in SENT_END.finditer(joined) if s.end() > at], default=len(joined))
    return joined[lo:hi]


def _quoted_literals(sentence: str) -> set[str]:
    lits = set()
    for a, b in QUOTED.findall(sentence):
        lit = (a or b).strip()
        if CJK.search(lit) or len(lit) >= 3:
            lits.add(lit)
    return lits


def _evidence(sentence: str) -> set[str]:
    """Names this sentence offers as evidence: ASCII identifiers plus quoted literals.

    The literal half is not decoration — a sentence like "那格改成 `or '无'`" names a Chinese
    string, and a rule that only sees ASCII would call that "没写出可核对的名字" (`#108`).
    """
    return set(ASCII_TOK.findall(sentence)) | _quoted_literals(sentence)


def _strong_names(sentence: str) -> set[str]:
    """这句话里**被当作名字写出来**的那批：反引号里的内容，加引号里的字面量。

    注释行只认这一类（`#108` 的第二半）。源码注释里满是英文散文词，句子里任何一个 ASCII 词都可能
    碰巧落在被指那一行上：22:17:28Z 逐 token 核出，README 指 `DECISIVE` 的那个号写着 251（它上面那
    行注释，定义在 253），而 251 里有 "a batch of outages"，句子里又写着"batch 的分母"，于是错号靠
    `batch` 绿着。22:18:41Z 量这一族的范围：整份语料 107 处里落在注释行上的共 4 处，3 处反引号里
    就写着那行出现的名字，规则加上去代价为零，逮到的正是剩下那一处。

    路径形状的 span（`src/wolfengine/metrics.py` 的 251 行）剔掉：它指的是文件不是行，留着反而能给注释行里
    抄了同一条路径的那一行背书。
    """
    names = {s.strip() for s in BACKTICK.findall(sentence)} | _quoted_literals(sentence)
    # `metrics.DECISIVE` 在源码里写的是 `DECISIVE`——把模块前缀剥掉，但只剥"名字.名字"这种形状：
    # 路径 `src/wolfengine/metrics.py` 的尾段是 "py"，收下它等于给任何含 "py" 的注释放行。
    names |= {n.rsplit(".", 1)[-1] for n in names
              if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", n)}
    return {n for n in names if n and ".py:" not in n and not PATHISH.search(n)}


def _line_citations(bodies: dict[str, str]) -> list[tuple[str, int, str, int, set[str], set[str]]]:
    """(文档, 行号, 被点的文件, 句里写的号, 句里的标识符, 句里**写成名字**的那些)。

    Identifiers come from the citation's **own sentence** (`_cited_sentence`), not from the ±2-line
    paragraph. The paragraph was measured to be too loose twice over: 21:44:40Z a `metrics.py`
    pointer that had drifted 3 lines stayed green because some other word in the paragraph sat on
    that line, and 22:10:17Z the whole corpus (107 pointers, all green under the old rule) turned up
    **9** that only passed on a neighbour sentence's name or on a word the sentence never wrote as a
    name. Wrapping is still handled: a sentence may span several physical lines.

    凡是这句话里点过名的 `.py`，它的词干都不算证据（`PY_CITED` 那一减法）：`events.py` 的 410 与
    `cli.py` 的 623 写在同一句里时，`events` 这个词会给两个号同时背书，而它对其中一个才是真名字。
    代价实测为零——13:51:02Z 数整份语料 50 处引用，去掉这一格会多放行 1 处（`assemble.py` 的 201 行
    靠 `persona` 站着，`persona` 不是这一段点名的文件），去掉后它仍然绿。
    """
    out = []
    for doc, body in bodies.items():
        lines = body.splitlines()
        for no, line in enumerate(lines, 1):
            for m in LINE_CITE.finditer(line):
                block = lines[max(0, no - 3):no + 2]
                sentence = _cited_sentence(block, no - 1 - max(0, no - 3), m.start())
                stems = {Path(p).stem for p in PY_CITED.findall(sentence)}
                drop = stems | {Path(m.group(1)).stem, m.group(1)} | STOP
                out.append((doc, no, m.group(1), int(m.group(2)),
                            _evidence(sentence) - drop, _strong_names(sentence) - drop))
    return out


def _resolve_source(name: str) -> Path | None:
    for cand in (ROOT / name, ROOT / "src/wolfengine" / name, ROOT / "src/wolfengine/prompts" / name,
                 ROOT / "tests" / name, ROOT / "scripts" / name):
        if cand.exists():
            return cand
    return None


def _bad_line_cites(cites) -> list[tuple[str, int, str, str]]:
    """The check itself, over caller-supplied citations: which numbers no longer sit on the line
    the sentence names. A citation with no identifier beside it is reported too — an unchecked
    pointer is the rot this gate exists to catch, and the fix is to write the name."""
    bad = []
    for doc, no, target, line_no, toks, strong in cites:
        path = _resolve_source(target)
        if path is None:
            bad.append((doc, no, f"{target}:{line_no}", "src/tests/scripts 里没有这个文件"))
            continue
        rows = path.read_text(encoding="utf-8").splitlines()
        if line_no > len(rows):
            bad.append((doc, no, f"{target}:{line_no}", f"超出文件长度（{len(rows)} 行）"))
            continue
        if not toks:
            bad.append((doc, no, f"{target}:{line_no}", "这句话没写出可核对的名字"))
            continue
        # 窗口就是被点名的那一行，不含上下邻行。取 ±2 时"这一段的某个词在那五行里出现过"就能通过，
        # 而一段话通常有 5~11 个标识符、源文件那五行总有一行提到别的名字——13:48:25Z 实测：真实语料
        # 里 `cli.py` 的 620 与 624 都是这么绿的，而它们想指的 `except LogDamage` 在 623 行。
        window = rows[line_no - 1]
        # 注释行只接受**写成名字**的证据：那里的英文散文词太多，随便一个词都能给错号背书
        # （`#108`：`DECISIVE` 的号指着它上面的注释，靠句里的 `batch` 绿着）。
        pool = strong if window.lstrip().startswith("#") else toks
        if not any(t in window for t in pool):
            # 措辞要自带归因：一次插行造成的"顶偏"和"这句话本来指点别的东西"不是同一件事，前者
            # 改文档里的号就完事，后者要改句子。变异电池把本闸门当证人时读的就是这一格（`#77`）。
            head = ("号落在注释行上，而这句话没把被指的东西写成名字（散文词不算）"
                    if pool is strong else "那一行没有")
            shown = sorted(pool)[:5]
            occ = [i for i, r in enumerate(rows, 1) if any(t in r for t in pool)]
            if not occ:
                bad.append((doc, no, f"{target}:{line_no}", f"{head}：{shown}，整个 {target} 里也找不到"))
                continue
            want = min(occ, key=lambda i: abs(i - line_no))
            bad.append((doc, no, f"{target}:{line_no}",
                        f"{head}：{shown}；它在第 {want} 行（差 {want - line_no:+d} 行）"))
    return bad


def test_the_line_citation_probe_fires_on_a_number_that_moved_and_only_on_that():
    """两向都要证：一个故意写错的号必须红，一个就地写对的号必须绿。

    第一行抄的是 `cli.py` 里 `_games_error` 的真号，第二行把它挪后 40 行——那里是别的东西。
    第三行点一个不存在的文件，第四行的句子只有中文，机器无从核对，所以也算红。
    """
    rows = (ROOT / "src/wolfengine/cli.py").read_text(encoding="utf-8").splitlines()
    n = next(i for i, r in enumerate(rows, 1) if "_games_error" in r)
    sections = [f"地板谓词 `cli._games_error`（`cli.py:{n}`）",
                f"地板谓词 `cli._games_error`（`cli.py:{n + 40}`）",
                f"`nosuchfile.py:{n}` 在那儿",
                f"行号在这里：`cli.py:{n + 40}`，这句话什么都没点"]
    # Two blank lines between the sections: a citation's paragraph is ±2 lines, and probes whose
    # sections can see each other would let the good tokens leak into the lines meant to fail.
    cites = _line_citations({"probe.md": "\n\n\n".join(sections)})
    assert len(cites) == 4, cites
    bad = _bad_line_cites(cites)
    assert len(bad) == 3, bad
    assert bad[0][2].endswith(str(n + 40)) and "那一行" in bad[0][3], bad[0]
    assert "没有这个文件" in bad[1][3], bad[1]
    assert "没写出可核对的名字" in bad[2][3], bad[2]


def test_a_number_one_line_off_is_a_bad_pointer_and_a_file_name_is_not_evidence():
    """两道闸门都收不进"错三行"和"隔壁那句话的文件名"，是本轮实测出来的，不是设想。

    实测（13:48:25Z）：`#50`/`#57` 那两句当时写的是 `cli.py` 的 616 与 620，而它们指的
    `except LogDamage` 在 623 行——**两个都绿**。原因是窗口取 ±2（共 5 行）且标识符取整段（±2 行），
    于是"这一段里有个词在那五行里出现过"就算通过。同一句里还点着 `events.py` 的 410，那个 `events`
    因此成了一个万能标识符：13:51:02Z 实测 `cli.py` 的 624（一句提到 `events.py` 的注释）也能替
    623 行背书。所以这里钉两格：

    * 号错三行必须红（`cli.py:{n-3}`：那是 `parse_args` 那一行，与本句无关）。这一格今天的宽窗口
      也报得出，它钉的是"别把 unrelated 行当对的"。
    * 号错两行落在**下面那行注释**上（`cli.py:{n+2}`）只有把窗口收到整行才报得出：宽窗口 ±2 时
      623 行就在 `{n}` 的窗口里，错号照样绿。这一格钉的是窗口。
    * 号错一行落在提到 `events.py` 的那句注释上（`cli.py:{n+1}`）只有把"文件名不算证据"接上才报得
      出：整行 624 里唯一命中的词就是 `events`。这一格钉的是标识符的来源。
    """
    rows = (ROOT / "src/wolfengine/cli.py").read_text(encoding="utf-8").splitlines()
    n = next(i for i, r in enumerate(rows, 1) if "except LogDamage" in r)
    # 前提自己也要站着：这几行确实没有本句的标识符，否则下面的红是"测试写坏了"而不是"闸门变严了"。
    assert not any(t in rows[n - 4] for t in ("LogDamage", "except")), rows[n - 4]
    assert not any(t in rows[n - 2] for t in ("LogDamage", "except")), rows[n - 2]
    assert "events" in rows[n] and not any(t in rows[n] for t in ("LogDamage", "except")), rows[n]
    assert not any(t in rows[n + 1] for t in ("LogDamage", "except")), rows[n + 1]
    ev = next(i for i, r in enumerate(
        (ROOT / "src/wolfengine/events.py").read_text(encoding="utf-8").splitlines(), 1)
        if "class LogDamage" in r)

    def probe(number: int) -> list[tuple]:
        body = (f"损坏的行由 `events.py:{ev}` 定义的 `LogDamage` 抛出，`cli.py:{number}` 的 except 接住它。\n"
                "\n\n")
        cites = _line_citations({"probe.md": body})
        assert len(cites) == 2, cites
        return [b for b in _bad_line_cites(cites) if b[2].startswith("cli.py")]

    assert probe(n) == [], f"对的那个号被报红了：{probe(n)}"
    assert probe(n - 3), "号错三行还绿着——窗口或标识符取得太宽"
    assert probe(n - 1), "窗口只要往下多伸一行，指错一行的号就靠邻居那行真代码绿了"
    assert probe(n + 2), "窗口取到 ±2：错两行的号靠隔壁那行真代码通过了"
    assert probe(n + 1), "隔壁那句的文件名替一个错号背了书"
    # 越界那一格补的是**读者**而不是行为：`line_no > len(rows)` 这条分支 13:58:40Z 之前没有任何断言
    # 走过（全套 777 条里没有一处引用超出文件长度），所以它当时是一支假装存在的分支。
    out = probe(len(rows) + 5)
    assert len(out) == 1 and "超出文件长度" in out[0][3], out


def test_a_name_from_the_neighbouring_sentence_is_not_evidence_for_this_pointer():
    """`#108`：段里**隔壁那句**的名字不许替这个号背书——取词范围必须是引用所在的那一句。

    两回现世：21:44:40Z，`metrics.py` 第二次被插行之后 `truncated_call` 那个号已经偏了 3 行，闸门
    照样绿（同段里有别的词落在那五行上）；22:10:17Z 拿整份语料量收成的代价——107 处 `文件.py:行号`
    引用在旧判据下**全绿**，把取词从 ±2 行收到同一句话新报 9 处（README 的 804/1473/1533/1862/2932/
    3029/3254/3310/3542），其中 2 处是号本身错了（`_hollow_files` 写在 487 而定义在 507），7 处号没错、
    只是句子里没把被指的东西写出名字来。`#72` 把窗口从 ±2 收到整行是同一族的第一半：窗口窄了，可
    标识符仍然取自整段，于是"这句话旁边"实际是"这段话里"。
    """
    rows = (ROOT / "src/wolfengine/cli.py").read_text(encoding="utf-8").splitlines()
    n = next(i for i, r in enumerate(rows, 1) if "_games_error" in r)
    m = next(i for i, r in enumerate(rows, 1) if r.startswith("def main("))
    assert abs(m - n) > 3, (n, m)
    # 前提自己也要站着，否则红是"测试写坏了"而不是"闸门变严了"。
    assert "main" not in rows[n - 1] and "_games_error" not in rows[m - 1], (rows[n - 1], rows[m - 1])

    def probe(number: int) -> list[tuple]:
        body = (f"`main` 是整个 CLI 的入口。\n"
                f"地板谓词 `cli._games_error` 落在 `cli.py:{number}`。\n")
        cites = _line_citations({"probe.md": body})
        assert len(cites) == 1, cites
        return _bad_line_cites(cites)

    assert probe(n) == [], f"对的那个号被报红了：{probe(n)}"
    out = probe(m)
    assert len(out) == 1, (
        f"`cli.py:{m}` 那一行只有 `main`，而本句点的是 `_games_error`——它却绿了，"
        "说明取词范围还在整段")
    # `_games_error` 在 cli.py 里有定义 + 两处读者，所以"它想去哪一行"取最近的那一处（`#77` 的措辞）。
    occ = [i for i, r in enumerate(rows, 1) if "_games_error" in r]
    want = min(occ, key=lambda i: abs(i - m))
    assert len(occ) > 1, occ
    assert "_games_error" in out[0][3] and f"第 {want} 行" in out[0][3], out[0]


def test_a_literal_named_in_the_same_sentence_is_evidence_too():
    """收紧取词范围时，句子里点的是**字面量**而不是标识符的那类主张不能被冤枉。

    形状是真的：README 当时把 `render_live.py` 的 222 行那格改成 `or '无'`，被指的东西就是一个中文字面量，
    只认 ASCII 标识符的判据会把这句正确主张说成"这句话没写出可核对的名字"。但收益要按量出来的说：
    22:15:16Z 数整份语料 107 处引用，**只靠字面量站着的是 0 处**，所以这一半今天在真语料上换不来任何
    新读数，它的牙长在构造的夹具上——和 `#105`、`#107` 一样，这一条得写清楚。它仍须装上，是因为
    `#108` 的注释行那一半把"是否写成名字"当判据，不认引号里的字面量，那种形状就是下一个被冤枉的句子。
    """
    rows = (ROOT / "src/wolfengine/render_live.py").read_text(encoding="utf-8").splitlines()
    n = next(i for i, r in enumerate(rows, 1) if "'无'" in r)
    m = next(i for i, r in enumerate(rows, 1) if r.startswith("def _read("))
    assert "'无'" not in rows[m - 1], rows[m - 1]

    def probe(number: int) -> list[tuple]:
        body = f"终局那一格改成 `or '无'`（`render_live.py:{number}`）。\n"
        cites = _line_citations({"probe.md": body})
        assert len(cites) == 1, cites
        return _bad_line_cites(cites)

    assert probe(n) == [], f"句子里写着被指的那个字面量，却被当成没写名字：{probe(n)}"
    out = probe(m)
    assert len(out) == 1, f"错号还绿着：{out}"


def test_a_pointer_at_a_comment_line_needs_a_name_written_on_that_line():
    """`#108` 的第二半：号落在**注释行**上时，句子里的散文词不算证据。

    收紧到同一句话之后，语料里仍有一处错号是绿的，22:17:28Z 逐 token 核出来：README 指 `DECISIVE`
    写的是 `metrics.py` 的 251，那是它上面那行注释，定义在 253——而 251 里正好有英文散文词 "a batch of
    outages"，句子里又写着"batch 的分母"，于是 `batch` 替一个错号背了书。22:18:41Z 量这一族的范围：
    整份语料 107 处引用里落在注释行上一共 4 处，其中 3 处（`config.py` 的 77、`agent.py` 的 376、
    `cli.py` 的 352）反引号里就写着那行出现的名字，只有 `DECISIVE` 这一处没有。所以这条规则的代价实测
    为零，而它逮到的正是那唯一一处真错号。
    """
    rows = (ROOT / "src/wolfengine/metrics.py").read_text(encoding="utf-8").splitlines()
    n = next(i for i, r in enumerate(rows, 1) if r.startswith("DECISIVE = frozenset"))
    c = n - 2
    assert rows[c - 1].lstrip().startswith("#"), rows[c - 1]
    assert "DECISIVE" not in rows[c - 1], rows[c - 1]

    def probe(number: int) -> list[tuple]:
        body = (f"胜率的分母是拿 `DECISIVE` 筛的（`src/wolfengine/metrics.py:{number}`，"
                f"batch 那一侧也读它）。\n")
        cites = _line_citations({"probe.md": body})
        assert len(cites) == 1, cites
        return _bad_line_cites(cites)

    assert probe(n) == [], f"对的那个号被报红了：{probe(n)}"
    out = probe(c)
    assert len(out) == 1, (
        f"`metrics.py:{c}` 是 `DECISIVE` 上面的注释行，句里那个散文词 `batch` 又替它背了书——"
        "错号还绿着")
    assert "注释" in out[0][3], f"红了但没说清是注释行这一族：{out[0][3]}"

    # 反过来：注释行上真写着名字的形状不许被冤枉（README 指 `config.py` 那段注释就是这一族）。
    crows = (ROOT / "src/wolfengine/config.py").read_text(encoding="utf-8").splitlines()
    k = next(i for i, r in enumerate(crows, 1) if r.lstrip().startswith("#") and "c_belief" in r)
    body = f"这条分工写在 `src/wolfengine/config.py:{k}` 那段注释里（只有 `c_belief` 后面真有一刀）。\n"
    cites = _line_citations({"probe.md": body})
    assert len(cites) == 1, cites
    assert _bad_line_cites(cites) == [], f"注释行里写着名字，仍被当成没写：{_bad_line_cites(cites)}"


def test_a_moved_number_names_the_line_it_wanted_and_a_missing_name_says_so():
    """`#77`：这一条闸门把两件事报成同一句话，于是变异电池把它当成了行为证人。

    实测在 `#75` 的表上：两具"before 就红"的红来自行号闸门被插行顶偏——号后面挪了一行，句子点的
    名字还在原地等着，那种红只需要改文档里的号，**没有读过任何行为**。可它和"这句话指错了东西"
    共用一句措辞（`那一行没有一个 [...]`），于是记账时分不开。

    修法是让措辞自己带上归因，且不引入我拍的阈值：名字还在文件里就说在第几行、差多少；名字整个
    文件都没有就直说找不到。所以这里钉两格，**红/绿的判据一条没动**（错号仍旧红、对号仍旧绿），
    变的只是那句话点不点得清自己属于哪一类。
    """
    rows = (ROOT / "src/wolfengine/cli.py").read_text(encoding="utf-8").splitlines()
    n = next(i for i, r in enumerate(rows, 1) if "_games_error" in r)

    def probe(number: int, name: str = "_games_error") -> list[tuple]:
        cites = _line_citations(
            {"probe.md": f"地板谓词 `cli.{name}`（`cli.py:{number}`）\n\n\n"})
        assert len(cites) == 1, cites
        return _bad_line_cites(cites)

    assert probe(n) == [], f"对的那个号被报红了：{probe(n)}"
    moved_number = n + 40
    moved = probe(moved_number)
    assert len(moved) == 1, moved
    assert "那一行" in moved[0][3] and "差" in moved[0][3], moved[0]
    # `_games_error` 在 `cli.py` 里不止一处（定义 + 两处读者），所以"它想去哪一行"取最近的那一处。
    occ = [i for i, r in enumerate(rows, 1) if "_games_error" in r]
    want = min(occ, key=lambda i: abs(i - moved_number))
    assert len(occ) > 1, f"这一格的前提是这个名字在文件里有多处，否则测不出'最近'：{occ}"
    assert f"第 {want} 行" in moved[0][3], f"没说清它想去第几行：{moved[0][3]}"
    assert f"差 {want - moved_number:+d} 行" in moved[0][3], f"差多少没带符号：{moved[0][3]}"

    gone = probe(n + 1, "_no_such_predicate_anywhere_in_cli")
    assert len(gone) == 1, gone
    assert "找不到" in gone[0][3] and "差" not in gone[0][3], gone[0][3]


def _line_cite_corpus() -> dict[str, str]:
    """闸门读的语料：markdown 文档 + `tests/` 与 `scripts/` 下的 .py。

    `#126` 把 .py 收进来：测试文件的 docstring 里写的是同一个形状 `文件.py:号`，读者照样照着号去看
    **现在**那一行，号漂了就误导。文档没有豁免名单，代码也没有。
    """
    files = [*DOCS, *sorted((ROOT / "tests").glob("*.py")),
             *sorted((ROOT / "scripts").glob("*.py"))]
    return {f.name: f.read_text(encoding="utf-8") for f in files}


def test_a_line_number_written_in_the_docs_still_points_at_the_thing_named_beside_it():
    """README 里那个 `cli.py` 的 88 号是一次插入就烂掉的主张：号还在、文件还在，只有指的语句不在那儿了
    （`#123` 往 `cli.py` 插了四处之后，它漂到了 95）。

    这一族的腐烂有过实测记录——README 自己就写过"一个早就漂走的 `test_live_path.py` 行号"，
    而 `#59` 这轮往 `cli.py` 里插进一个函数之后，同文件下游 15 处行号引用（今天数：点 `cli.py` 的 23 处
    里号在插入点下游的那些）全部后移。名字有闸门
    （`test_a_name_cited_in_the_docs_...` 那几条），行号没有，所以这一条来补：号不许只靠"看着像
    对"活着。

    扫描面自 `#126` 起含 `tests/` 与 `scripts/` 的 .py（组成由下一条证人钉住）：08:52:44Z 数全语料
    190 处引用，其中 .py 侧 21 处。地板取 150——低于 markdown 那一半，所以它只管"正则或范围坏了"。
    """
    cites = _line_citations(_line_cite_corpus())
    assert len(cites) >= 150, f"只扫到 {len(cites)} 处行号引用，多半是正则或范围坏了"
    bad = _bad_line_cites(cites)
    assert not bad, f"文档与代码注释里的行号引用指错了地方：{bad}"


def test_the_line_citation_gate_reads_the_python_files_that_cite_line_numbers():
    """这一族从 `#77` 起只读 markdown，而测试文件的 docstring 里写的是同一个形状。

    `#126` 量的代价（2026-09-26T08:43:59Z，把扫描面扩到 `tests/*.py` + `scripts/*.py` 后拿闸门
    自己的判据跑一遍）：44 个 .py 里扫到 38 处引用，报红 30 处。一条豁免名单都不给——本文件开头
    第 43 行早就写着约定：`文件.py:号` 这个形状**只**表示"照这个号去看现在那一行"，复述旧号要写成
    正则接不住的样子，所以历史叙述改写法，不改闸门。
    """
    corpus = _line_cite_corpus()
    py = {f.name for d in ("tests", "scripts") for f in (ROOT / d).glob("*.py")}
    assert py, "tests/ 与 scripts/ 里一个 .py 都没有，多半是路径坏了"
    missing = py - set(corpus)
    assert not missing, f"行号闸门不读这些文件（里面写的号漂了没人报）：{sorted(missing)}"
