"""文档里"读者会照抄的东西"必须还能用——用例名、命令行、条数、行号、出厂值、收集数、节标题指针、具数落点都在扫描范围内。

`docs/*.md` 和 `README.md` 用 `` `test_名字` `` 的形式给每个断言指认"是哪条用例在钉它"，又印了一
批可以直接敲的 `wolf …` 命令，还写了"某个测试文件有几条用例"、"某个源码里的东西在第几行"、"某个配置键
出厂是多少"和"细节在〈某一节〉"，能力清单里还按批写着"那一批跑了 N 具变异"。这八类串都是读者复核时的入口：
写完一轮重构、改个用例名、插一行代码、给某个参数换个叫法、把某个默认值调一格、把一节改了标题、
把一批电池的具数复述错，文档不会报错，只会留下一个点不到的入口。
这一片的缺陷是实测抓到的几条——

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
`tests/*.py` 的模块级 `def test_*` 计数、`src/**.py` 的 AST 字面量、README 能力清单的具数主张对照
`docs/*.md` 里同一条 bullet 点到的那一节）：

* 对照 **AST 里的函数名**而不是 `pytest --collect-only` 的输出。一是 subprocess 让测试不再离线
  自足；二是 addopts 已经带 `-q`，再叠一个 `-q` 会把 collect 输出压成每文件计数，一个"36 条全部
  MISSING"的假警报就是这么来的（见 views.md 里那条 harness 教训）。
* 只收 `def test_*` / `async def test_*` 和测试模块名，不往名字集里灌字符串。文档现在不点名带
  `[参数]` 后缀的用例，而正则也停在 `[` 之前，所以基础函数名足够。
* 只认 `test_` 形状，不认一般标识符。文档里的 `` `region_budget_check` `` 这类引用有真价值，但
  "明确不做"清单里也写着将来才有的函数名，那类引用现在必然报红，报红三次就会被整体关掉。
* 具数落点只认**同一条 bullet 里点到的那一节**，背书只认「N 具+名词」和「N 行的具名表」两种形状；
  跨 bullet 不算（读者照着有数的那一条查还是查不到）、裸的「N 具」不算（同一节里的裸数可能说的是
  另一批）、README 不给自己背书（拿手册查手册是自我背书）。这三条是声明的限界，各有一条合成用例钉着。

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


def _line_cite_files() -> list[Path]:
    """这一族读的文件清单：markdown 文档 + `tests/`、`scripts/`、`src/` 下的 .py。

    `#126` 收进 tests/scripts，`#139` 收进 src。名单单独成一个函数，是因为"扫了几棵"和"字典剩几个键"
    不是一件事——同名文件按名字建键会静默少一棵，那条守卫要看的是前者。
    """
    return [*DOCS, *sorted((ROOT / "tests").glob("*.py")),
            *sorted((ROOT / "scripts").glob("*.py")),
            *sorted((ROOT / "src").rglob("*.py"))]


def _line_cite_corpus() -> dict[str, str]:
    """闸门读的语料，键是**相对路径**。

    `#126` 把 .py 收进来：测试文件的 docstring 里写的是同一个形状 `文件.py:号`，读者照样照着号去看
    **现在**那一行，号漂了就误导。文档没有豁免名单，代码也没有。
    """
    return {str(f.relative_to(ROOT)): f.read_text(encoding="utf-8") for f in _line_cite_files()}


PLACE_PHRASE = re.compile(r"按行号引|按行号点|pointed at by line number|cited by line number|"
                          r"by line number in")
DOC_NAMED = re.compile(r"(?:docs/)?[\w.\-]+\.md|README")


def _placement_claims(corpus: dict[str, str]) -> list[tuple[str, int, list[str], str]]:
    """代码里"文档按号点着我，所以这段摆在这儿"那一类句子：(文件, 行号, 句里点名的出处, 原句)。

    措辞之外还要求句子里有**自指**（自己文件的 `名.py:` 或「本文件」），只扫 .py：散文里谈这件事的
    行是叙述，不是"我为什么写在这一行"。这句话自己也被同一条判据管着，所以本文件的夹具字符串都写成
    跨行相邻字面量——任何一格只要在一行里同时出现措辞与自指，它就会把自己算成第 4 条摆放理由。

    已知限制，不打算修：判据是**按行**的，措辞与自指被硬换行拆到两行时它看不见。要修就得把整段读成
    一句，而那样会把相邻两句接成一条假依据——代价比这一格漏掉的更常见。
    """
    out = []
    for path, text in sorted(corpus.items()):
        if not path.endswith(".py"):
            continue
        stem = Path(path).name
        for no, line in enumerate(text.splitlines(), 1):
            if not PLACE_PHRASE.search(line):
                continue
            if f"{stem}:" not in line and "本文件" not in line:
                continue
            out.append((path, no, sorted(set(DOC_NAMED.findall(line))), line.strip()))
    return out


def _claim_sources(named: list[str], path: str, corpus: dict[str, str]) -> list[str]:
    """这句话声称的出处落在语料的哪些键上：点名按名字找，泛指"文档"=除自己以外的全部。"""
    if not named:
        return [k for k in sorted(corpus) if k != path]
    return [k for k in sorted(corpus)
            if k != path and any(k == n or Path(k).name == n or k.endswith("/" + n)
                                 or (n == "README" and Path(k).name == "README.md") for n in named)]


def _bad_placements(corpus: dict[str, str]) -> list[tuple[str, int, list[str], str]]:
    """摆放理由落空的那些：它点名的每一个出处里，都已经没有一处指向本文件的号了。"""
    bad = []
    for path, no, named, line in _placement_claims(corpus):
        stem = Path(path).name
        for src in (named or ["文档"]):
            keys = _claim_sources([src] if named else [], path, corpus)
            hits = sum(1 for k in keys for m in LINE_CITE.finditer(corpus[k])
                       if Path(m.group(1)).name == stem)
            if not hits:
                bad.append((path, no, named, f"{src} 里已经没有任何一处指向 {stem} 的行号了：{line}"))
    return bad


def test_a_line_number_written_in_the_docs_still_points_at_the_thing_named_beside_it():
    """README 里那个 `cli.py` 的 88 号是一次插入就烂掉的主张：号还在、文件还在，只有指的语句不在那儿了
    （`#123` 往 `cli.py` 插了四处之后，它漂到了 95）。

    这一族的腐烂有过实测记录——README 自己就写过"一个早就漂走的 `test_live_path.py` 行号"，
    而 `#59` 这轮往 `cli.py` 里插进一个函数之后，同文件下游 15 处行号引用（今天数：点 `cli.py` 的 23 处
    里号在插入点下游的那些）全部后移。名字有闸门
    （`test_a_name_cited_in_the_docs_...` 那几条），行号没有，所以这一条来补：号不许只靠"看着像
    对"活着。

    扫描面自 `#126` 起含 `tests/` 与 `scripts/` 的 .py、自 `#139` 起再含 `src/` 的 .py（组成由下面
    那两条"读不读这些文件"的证人钉住，本条不钉组成）：08:52:44Z 数全语料 190 处引用、其中 .py 侧
    21 处，09:35:02Z 加宽后 194 处、.py 侧 23 处（src 侧那 2 处都在同一句注释里）。地板取 150——
    低于 markdown 那一半，所以它只管"正则或范围坏了"。
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
    py = {str(f.relative_to(ROOT)) for d in ("tests", "scripts") for f in (ROOT / d).glob("*.py")}
    assert py, "tests/ 与 scripts/ 里一个 .py 都没有，多半是路径坏了"
    missing = py - set(corpus)
    assert not missing, f"行号闸门不读这些文件（里面写的号漂了没人报）：{sorted(missing)}"


def test_the_line_citation_corpus_has_one_entry_per_file_it_scans():
    """键要是文件名，`__init__.py` 就让其中一棵**静默消失**：不报错，只是少读一个文件。

    危害是真的而不是假想的（09:29:11Z）：`src/wolfengine/__init__.py` 与 `src/wolfengine/prompts/__init__.py`
    都在 `#139` 扩进来的那 28 棵里，名字相同。今天这两棵都没有行号引用，所以按名字建键**不会**改变
    本片的判定——它改变的是"下一片往这些文件里写号时有没有人看着"。这条守卫钉的是字典规模等于扫描
    规模，电池里把键换回 `f.name` 的那具刀要能红它。
    """
    files = _line_cite_files()
    corpus = _line_cite_corpus()
    names = [f.name for f in files]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert dupes, "扫描面里已经没有同名文件了，这条守卫就成了空跑——它钉的正是同名那一格"
    assert len(corpus) == len(files), f"语料只剩 {len(corpus)} 条，扫了 {len(files)} 个文件"


def test_the_line_citation_gate_reads_the_product_files_that_cite_line_numbers():
    """`#126` 收了 `tests/` 与 `scripts/`，`src/` 是这一族唯一还没有读者的 .py。

    代价先量过（09:29:11Z，`/tmp/m139.py`：把 markdown + tests + scripts + src 一起喂给闸门自己的
    判据）：src 侧 28 个 .py 里只有一处注释写着这个形状，就是 `src/wolfengine/events.py` 末尾那段
    docstring，它同时点了两个号，**两处都报红**。票面那句"先得量有多少处注释里的号昨天就对不上"的
    答案是 2——不是 0，也不是几十处，所以这一层扩面是划算的。
    """
    corpus = _line_cite_corpus()
    src = {str(f.relative_to(ROOT)) for f in (ROOT / "src").rglob("*.py")}
    assert len(src) >= 20, f"src/ 里只数到 {len(src)} 个 .py，多半是路径坏了"
    missing = src - set(corpus)
    assert not missing, f"行号闸门不读这些产品文件（里面注释写的号漂了没人报）：{sorted(missing)}"


def _fixture_cite(stem: str, no: int) -> str:
    """夹具里的 `名.py:号`：这五个字符不能在**源文本**里以活形状出现。

    本文件自己就在行号闸门的语料里（`#126` 收的），所以照点形状写假文件名会被判成"src/tests/scripts
    里没有这个文件"——10:07:11Z 这一跑就是它红了三格。拼起来只在运行时成形状，判据不受影响。
    """
    return f"{stem}.py:{no}"


def test_a_fixture_that_claims_a_doc_backs_its_place_is_tested_against_that_doc():
    """`#139` 的缺席刀 K7 产出的这一格：把一句摆放理由改成已被证伪的那份，28 条证人当时全绿。

    判据要能分四种情况，所以夹具四句话各钉一格：点名了出处而那份里已经没有号（该报）、点名了出处
    且号还在（不该报）、只泛指"文档"而任意一处文档里还有号（不该报）、泛指而哪一份里都没有号（该报）。
    泛指那一格是 `cli.py` 里那两句的实际形状，点名那一格是 `events.py` 那一句的形状——它当初就是靠
    README 这个名字活着的；最后那一格是"泛指"这半判据唯一的反例，摘掉对泛指的支持时它和前三格都不红。

    第四格 `docs/other.md` 里**有**指向 `thing.py` 的号，但那不是那句话点名的出处。这一格钉的是"出处
    按名字算"——把它放宽成"语料里任意一处"时，该报的那条就报不出来了（10:12:05Z 实测：没有这一格时
    那具刀在夹具与真语料两侧都不红，是一具等价刀）。
    """
    corpus = {
        "src/wolfengine/thing.py": "x\ny\nz\nw\n    the `def thing` at "
                                   f"`{_fixture_cite('thing', 9)}` is pointed at"
                                   " by line number in README\n",
        "src/wolfengine/kept.py": f"    `def kept` at `{_fixture_cite('kept', 1)}` is pointed at"
                                  " by line number in ``docs/notes.md``\n",
        "src/wolfengine/generic.py": "    文档按行号" "引本文件下游的语句，所以它住在文件尾\n",
        "src/wolfengine/orphan.py": "    文档按行号" "点本文件里的一处号，可哪一份里都没有\n",
        "README.md": "这里一个号都没有\n",
        "docs/notes.md": f"见 `{_fixture_cite('kept', 1)}` 与 `{_fixture_cite('generic', 1)}`。\n",
        "docs/other.md": f"这一份里还留着 `{_fixture_cite('thing', 9)}`，可它不是被点名的那一份\n",
    }
    claims = _placement_claims(corpus)
    assert len(claims) == 4, f"夹具里该认出 4 条摆放理由，实际 {len(claims)}：{claims}"
    bad = _bad_placements(corpus)
    assert {Path(p).name for p, *_ in bad} == {"thing.py", "orphan.py"}, f"该报的是点名与泛指各一条：{bad}"
    thing = [b for b in bad if "thing.py" in b[0]]
    assert len(thing) == 1, f"点名那条要单独认得出：{bad}"
    # 出处必须点名在**引文之外**：报告尾巴上挂着原句，而原句里就写着 "README"——整串子串判据会被
    # 它蒙过去（10:2xZ 电池 K4 那一具就是这么活下来的：把 `{src}` 从报告里删掉，28 条证人全绿）。
    assert thing[0][3].split("：")[0].startswith("README"), \
        f"报告得点名它说过的出处，而不是让引文替它点名：{thing[0][3]}"


def test_a_placement_reason_in_the_product_code_still_has_the_citation_it_claims():
    """三句话拿"文档里按号点着它"当自己摆放位置的理由，而没有任何断言在读那个"还点着"。

    `#139` 量到的代价就是这一格的形状（09:29:39Z）：README 里原本有 8 处点了 `events.py`，`#135`
    把那段散文搬走之后归零，那句依据从那天起就是假的，闸门一路绿到有人**用眼睛**读它。地板取 3——
    10:03:43Z 实测全仓就这 3 条（`cli.py` 两处、`events.py` 一处），低于它就说明正则或扫法坏了。
    markdown 不在扫的范围里：那里同样有 2 行写着这类措辞（本文件 `#139` 那一节的叙述），但它们是
    **谈这件事**而不是**为自己摆在这句话而摆**，判据只收 .py 里带自指的那一类。
    """
    claims = _placement_claims(_line_cite_corpus())
    assert len(claims) >= 3, f"只扫到 {len(claims)} 条摆放理由，多半是判据或语料坏了"
    bad = _bad_placements(_line_cite_corpus())
    assert not bad, f"这些句子拿号当摆放理由，可它们点名的出处里已经没有号了：{bad}"


# 这个形状要拆开写：新判据扫的是 markdown，而本文件既要在夹具里拼出它、又要在正文里谈它，
# 整串留在源码里就会让"讲这一族的句子"变成这一族的第 33 条（`#140` 的自噬那一格的形状）。
VOLATILE = "/" + "tmp"
_ARTIFACT = re.compile(
    rf"{VOLATILE}/[^\s、，。）（`\"']*(?:\.(?:py|out|log)|/\*)(?![^\s、，。）（`\"'])")
ARCHIVE = "iterations.md"


def _artifact_paths(text: str) -> list[str]:
    """"某一轮跑出来、又被写进文档当复现出处"的临时工件。

    认 `.py`（电池脚本本身）、`.out` / `.log`（它印出来的台账）、以及 `/tmp/某目录/*`（宣称还留在
    那里的备份）。**不认**命令自己生成又自己读的临时目录——`--out /tmp/clipin` 和
    `/tmp/clipin/A/*.jsonl` 那两类是今天还能敲的命令行，不是指不到东西的出处，把它们算进来会让
    文档不敢再写"怎么复核"，而那正是这一片要保住的东西。
    """
    return _ARTIFACT.findall(text)


def _manual_pages() -> list[Path]:
    """给人照抄的那几本：`README.md` + `docs/*.md`，减去那本按日期追加的历史归档。"""
    return [f for f in DOCS if f.name != ARCHIVE]


def test_the_artifact_scanner_tells_a_dead_pointer_from_a_scratch_dir():
    """判据先要站在它自己划的那条线上：三类工件形状认得出，两类命令行形状放过。

    这条用例写完即绿（正则是在真语料上逐条验过才抄进来的，11:46:37Z），它的牙由电池还账：
    `#142` 的 K1 摘掉尾部那道负向预查——`/tmp/clipin/A/*.jsonl` 会当场变成一个"命中"，文档里
    五条 `wolf audit …` 全成假缺陷；K2 摘掉通配符那一支——`#142` 数到的 `mutbackups` 那一格
    会静默漏掉。两具都必须红这一条，别红在下一条。
    """
    hits = _artifact_paths(f"见 `{VOLATILE}/mut_x.py`、`{VOLATILE}/mut_x.out`、"
                           f"`{VOLATILE}/run.log` 和备份 `{VOLATILE}/mutbackups/*`")
    assert len(hits) == 4, f"四类工件形状该全认出来：{hits}"
    assert _artifact_paths(f"`wolf batch --out {VOLATILE}/clipin && "
                           f"wolf audit {VOLATILE}/clipin/A/*.jsonl`") == [], \
        "命令自己生成自己读的临时目录被当成了死出处：判据越界，文档将不敢再写复核命令"


def test_the_archive_is_excluded_only_while_it_says_its_scripts_are_gone():
    """那一本 136 条历史指针不被检查，前提是它自己写明那些脚本已经没了。

    豁免没有形状就是洞：`#142` 把归档挡在判据之外，理由不是"历史可以撒谎"，是"历史已经声明过
    它指的是当时跑过的东西"。这句话就是那个声明的唯一读者——它被删掉，下一句"归档里那条指针
    大概还能点"就没有任何东西反驳。逐字要求见 `_archive_declares_its_artifacts_gone`。
    """
    text = (ROOT / "docs" / ARCHIVE).read_text(encoding="utf-8")
    assert _artifact_paths(text), "归档里一条工件指针都没有，那这条豁免是空转的洞"
    assert _archive_declares_its_artifacts_gone(text), \
        f"`docs/{ARCHIVE}` 没有声明那些脚本是一次性工件：豁免失去了它的依据"


def _archive_declares_its_artifacts_gone(text: str) -> bool:
    """开头那一节里要有一句"这些脚本随重启消失、留下的是表里的账"。"""
    head = text[:2000]
    return (VOLATILE in head and "一次性" in head
            and any(k in head for k in ("重启", "消失", "不复存在")))


def test_the_manual_never_points_at_an_artifact_that_evaporates():
    """手册里 32 条"复现照某脚本"指到的东西全部不存在（11:43–11:46Z 逐条 `test -f` 量的）。

    `#142` 数到 README 20、`docs/comparison.md` 5、`docs/views.md` 5、`docs/metrics.md` 2，
    合计 32 条；README 那 17 个脚本名逐个验过全都不在。句式是「N 具变异照 某个脚本（…CAUGHT）」，
    而那一轮真正的证据是紧跟其后的那张表——账留下了，来路指不到。同族前例是 `#118`（注释里一条
    没实测过的出处）与 `#91`（"已入库"而那个目录是 gitignored）。
    """
    bad = [(f.name, p) for f in _manual_pages()
           for p in _artifact_paths(f.read_text(encoding="utf-8"))]
    assert not bad, f"这些复现出处指到了一次性工件：{bad}"


def test_the_artifact_scanner_scans_every_manual_page():
    """语料组成：手册==目录里的 markdown 全部减去那一本归档。

    把判据收窄（把某一本人册也排除掉）时，上一条用例**不红**——它断言的是"没有"，越收窄越像绿。
    这条钉的是"每一本都在被读"，`#126` 用的是同一招。地板取 4：现测手册 5 本（README + 四本参考
    文档），低于它说明 glob 或文件名变了。
    """
    manual = _manual_pages()
    assert len(manual) >= 4, f"手册语料只剩 {len(manual)} 本，多半是扫法坏了"
    assert len(manual) == len(DOCS) - 1, \
        f"被排除在判据之外的应当只有 {ARCHIVE} 一本：{[p.name for p in DOCS if p not in manual]}"
    assert ROOT / "README.md" in manual, "README 被排除了：那正是最需要这条判据的一本"


# --------------------------------------------------- 逐片变异账表不许留在手册里

def _table_body(lines: list[str], header_idx: int) -> str:
    """一张表的表体：分隔行之后、第一行不以竖线开头之前的那些行。"""
    body = []
    for row in lines[header_idx + 2:]:
        if not row.startswith("|"):
            break
        body.append(row)
    return "\n".join(body)


TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}")
# 分隔行有两种写法要认：`| --- | --- |` 与 `|---|---|`。前者是 `#143` 立判据时唯一见过的形状，
# 后者在 `docs/views.md` 里——竖线后面那个空格不是形状的一部分。
VERDICT_WORDS = "(?:CAUGHT|SURVIVED)"
# 判词这两个字面量只住这一处：下面的表体判据与散文判据（`_prose_verdicts` 一族）共用它。
ANY_VERDICT = re.compile(VERDICT_WORDS)
# 表体里只要出现判词就是这一轮的读数，不带具数也算：`| 退出删掉 | harness 判 `CAUGHT` |`
# 那一格既没有「5 具」也没有「5/5」，`_verdict_readings` 两个形状都读不到它。


def _tally_tables(text: str) -> list[int]:
    """具名变异账表的表头行号：表头写着「变异」，或者表体里报着判词的那张表。

    认表头而不是认整张表，因为这一族的正文里也会提到"某一具"——只有表格才是逐片取证的那份
    清单，而 `#135` 立的规矩管的正是清单：手册只回答"怎么跑、跑出来该看到什么、哪些事还答不了"。
    表头那两个字只是它的**一种**写法（`#169` 第二步）：一张"哪一具刀 → 哪条用例红了"的表把
    表头改叫「坏改动 / 谁变红了」就绕过旧判据，所以判据改成看表体报不报判决。
    """
    lines = text.splitlines()
    out: list[int] = []
    for i, ln in enumerate(lines):
        if not ln.startswith("| ") or i + 1 >= len(lines):
            continue
        if not TABLE_SEP.match(lines[i + 1].lstrip()):
            continue
        if ln.startswith("| 变异") or ANY_VERDICT.search(_table_body(lines, i)):
            out.append(i + 1)
    return out


def test_the_manual_carries_no_per_slice_mutation_tally():
    """手册里不许坐着"第 N 具变异红在哪条断言上"那种账表——那是归档的形状。

    `#135` 把逐片取证从 README 搬去 `docs/iterations.md` 时只搬走了散文，四张账表留了下来：
    D 表（文档引用闸门三十四具）、K 表（kind 字面量五具）、A·B·C 表（§十五上桌契约八具）、
    W 表（wilson_ci 九具）。它们既不是"怎么跑"也不是"该看到什么"，而 `#135` 那条规矩当时没有
    任何读者——搬家搬多干净全凭下一次记得。这条用例就是那个"下一次"。
    """
    bad = [(f.name, _tally_tables(f.read_text(encoding="utf-8"))) for f in _manual_pages()]
    assert not [site for site in bad if site[1]], \
        f"这些手册页里还有具名变异账表（行号见括号）：{[s for s in bad if s[1]]}"


def test_the_archive_is_where_a_moved_mutation_tally_lands():
    """判据得能看见那张表搬去了哪儿：归档里的账表只多不少，一条不许在移动中丢掉。

    上一条断言的是"没有"，把归档从语料里抹掉它一样绿——所以这一条钉"有"。地板取 20：现测归档
    22 张（`#143` 搬进四张之前），低于它说明扫法坏了或被搬的东西消失在了移动里。
    """
    tally = _tally_tables((ROOT / "docs" / ARCHIVE).read_text(encoding="utf-8"))
    assert len(tally) >= 20, f"归档里只剩 {len(tally)} 张账表，多半是扫法坏了或搬运丢了东西"


def test_a_line_naming_mutations_without_a_separator_is_not_a_tally():
    """判据认的是"表头 + 分隔行"这个形状，不是"这一行里出现了那两个字"。

    真实语料里 26 张表头每一张后面都跟着分隔行，所以只看这一族的账无法证明第二个条件在读——
    `#143` 的电池里那具"摘掉分隔行要求"的刀因此要靠这条合成用例才有读者。
    """
    assert _tally_tables("| 变异 | 红用例 |\n| --- | --- |\n| D1 … | … |") == [1]
    assert _tally_tables("| 变异 | 说明 |\n这一族还没跑电池，先把要列的东西说一句") == []
    assert _tally_tables("| 变异 | 红用例 |") == [], "表头后面什么都没有：不越界，也不算账表"


def test_a_tally_table_that_renamed_its_header_is_still_a_tally():
    """表头不写「变异」、分隔行写成 `|---|` 的账表，也得被认成账表。

    `#143` 立这条判据时认的是表头那两个字，于是它有两个盲区，而真实语料一次踩中两个：
    `docs/views.md` 那张 26 行的表表头叫「坏改动 / 谁变红了」、分隔行竖线后面没有空格，
    可它每一行都是"哪一具刀、哪条用例红了"——正是 `#135` 那条规矩要搬去归档的东西。
    """
    assert _tally_tables("| 变异 | 红用例 |\n|---|---|\n| D1 … | … |") == [1], \
        "分隔行写成 `|---|`（竖线后没有空格）也是分隔行"
    assert _tally_tables("| 坏改动 | 谁变红了 |\n|---|---|\n| 退出删掉 | harness 判 `CAUGHT` |") == [1]
    assert _tally_tables("| 能力 | 状态 |\n|---|---|\n| 观众模式 | 已可用 |") == [], \
        "没有判词的能力表不是账表：把手册里正常的表都扫成账，这条判据就废了"
    assert _tally_tables("| 能力 | 状态 |\n|---|---|\n| 观众模式 | 已可用 |\n\n"
                         "判词写成 `CAUGHT [hang after Ns]`。") == [], \
        "判词得在表体那一截里；读整页会因一段教判词怎么写的散文把每张表都判成账表"


TALLY_LANDING = "表头没写「变异」的那张账表搬进这一份"
VIEWS_HAZARD_HEADER = "| 坏改动 | 谁变红了 |"


def test_the_views_table_residue_landed_verbatim_in_the_archive():
    """摘走的两截逐字在归档那一节里找得到，且这张表自己不再报某一轮的判决。

    后半句不是 `test_the_manual_carries_no_per_slice_mutation_tally` 的复读：那条只说"扫不到账表"，
    把表头改名或把分隔行删掉也能让它绿。这一条钉的是**摘的正是判词那一格**——躲判据不算搬完。

    登记表那一截按**整行**断言而不是按前缀：电池第一跑的 K6 把登记行中段一个字换掉（去掉→摘掉），
    文档闸门 68 条全绿——前缀只保证开头那一段在，中段被改了也不是"不逐字"。K6 重跑才红在这条上。
    """
    body = _archive_section(TALLY_LANDING)
    for span in ('分支改坏"验过一次它会真的红，改完再按 sha256 校验还原成字节相同的文件。本轮重跑并确认被抓住的：',
                 '| "没有键盘且日志已终局"这条退出删掉 | 同一条**挂起**，harness 判 `CAUGHT [hang after 25s]`'
                 '（去掉退出后循环不再回到 `_read_key`，fake 的 50 次保险也触发不了） |'):
        assert span in body, f"逐字登记缺这一截：{span[:24]}…"
    lines = (ROOT / "docs" / "views.md").read_text(encoding="utf-8").splitlines()
    assert not ANY_VERDICT.search(_table_body(lines, lines.index(VIEWS_HAZARD_HEADER))), \
        "这张表还在报判决：加宽后的判据会一直红着，说明该摘的没摘干净"


# `VERDICT_WORDS` 的单一定义在上面「逐片变异账表」那一节：表体判据与散文判据共用同一对字面量。
# 分数那一形后面只许跟空白，所以反引号必须由这一格自己认下来；具数那两形的间隙是自由字符类，
# 反引号本来就在类里，再挂一个可选 `` `? `` 是永远走不到的分支（07:04:44Z 四种写法各测一遍）。
VERDICT_FRACTION = re.compile(rf"[0-9]{{1,3}}\s*/\s*[0-9]{{1,3}}\s*`?{VERDICT_WORDS}")
VERDICT_COUNT = re.compile(rf"[0-9一二三四五六七八九十]{{1,3}}\s?具[^\n]*?{VERDICT_WORDS}")


def _prose_verdicts(text: str) -> list[tuple[int, str]]:
    """散文里"这一轮电池判成了什么"的读数：具数+判词、或 N/M+判词。整行范围内认。"""
    out: list[tuple[int, str]] = []
    for n, line in enumerate(text.splitlines(), 1):
        hits = [m.group(0) for pat in (VERDICT_FRACTION, VERDICT_COUNT) for m in pat.finditer(line)]
        if hits:
            out.append((n, max(hits, key=len)))
    return out


def test_readme_carries_no_prose_battery_verdict():
    bad = _verdict_readings((ROOT / "README.md").read_text(encoding="utf-8"))
    assert not bad, f"README 里还坐着逐片电池的判决读数（行号, 命中的那一句）：{bad}"


def test_the_other_manual_pages_carry_no_prose_battery_verdict():
    """同一族判据扫其余手册页：`#143` 搬走的是 README，那三本页里的逐片电池账一处没动过。

    `#167` 那一条只管 README，是因为 `docs/metrics.md`、`docs/views.md`、
    `docs/comparison.md` 里这批句子的唯一出处（当年那份跑批输出）已经不在仓库里——先逐字登记进
    归档（`#168` 第一步那张 13 行的表）才有资格摘，否则"精简"就是销毁记录。
    """
    pages = [f for f in _manual_pages() if f.name != "README.md"]
    assert [f.name for f in pages] == ["calibration.md", "comparison.md", "metrics.md", "views.md"], \
        "语料面不叫这四本就不是「手册为零」：扫空了这条闸门照样绿"
    sites = [(f.name, n, s) for f in pages for n, s in _verdict_readings(f.read_text(encoding="utf-8"))]
    assert not sites, f"这些手册页里还坐着逐片电池的判决读数（带时间戳的历史值）：{sites}"


def test_the_verdict_judge_reads_both_shapes_and_spares_the_rule_sentence():
    """两形各钉一次，另两格钉"这句在教判决怎么写，不是在报某一轮的判决"和那格反引号归谁认。"""
    assert _prose_verdicts("变异 5 具于 2026-09-21 跑过一轮，5/5 CAUGHT。") == [
        (1, "5 具于 2026-09-21 跑过一轮，5/5 CAUGHT")]
    assert _prose_verdicts("W6 就是这么一具变异，跑出来 golden 那栏 `SURVIVED`。") == [
        (1, "一具变异，跑出来 golden 那栏 `SURVIVED")]
    assert _prose_verdicts("9/10 `CAUGHT` 是本切片的成绩。") == [(1, "9/10 `CAUGHT")], \
        "分数后面带反引号这一格只有 `VERDICT_FRACTION` 认得：它前面是空白类，反引号得由它自己吃下"
    assert _prose_verdicts("判词写成 `CAUGHT [hang after Ns]` 并记下最后一行开始跑的测试文件名。") == [], \
        "这句在教判决怎么写（没有具数、没有分数），删掉它手册就少一条习惯"


VERDICT_ROSTER = "另外三本手册页里的散文判决"
VERDICT_LANDING = "另外三本手册页里的逐片电池账搬进这一份"
WRAP_LANDING = "跨硬换行的判决读数搬进这一份"
WRAP_GAP = r"[^。\n|]{0,40}"
# 「具数」与判词被一次硬换行劈开：`#167` 把这条不对称记在限界里，`#169` 来收。空行、句号、
# 表格竖线都不许跨——跨过它们的两截不是同一句主张（同 `#150` 给落点账定的界）。
VERDICT_WRAPPED = re.compile(rf"[0-9一二三四五六七八九十]{{1,3}}\s?具{WRAP_GAP}\n{WRAP_GAP}{VERDICT_WORDS}")


def _wrapped_verdicts(text: str) -> list[tuple[int, str]]:
    """整行判据读不到的那一形：具数在上一行、判词在下一行。与 `_prose_verdicts` 两形不相交。"""
    return [(text[:m.start()].count("\n") + 1, m.group(0).replace("\n", "⏎"))
            for m in VERDICT_WRAPPED.finditer(text)]


def _verdict_readings(text: str) -> list[tuple[int, str]]:
    """语料面读的并集：同句内的读数与跨一次硬换行的读数。两形各自判，合并只在这一个地方。"""
    return sorted(_prose_verdicts(text) + _wrapped_verdicts(text))


def test_a_verdict_reading_split_by_one_hard_wrap_is_still_a_reading():
    """`#167` 的限界那一格在这里合上：手册页里现就有两处这种形状，整行判据一处都读不到。"""
    assert _wrapped_verdicts("这切片 5 具变异体\n  （全部 CAUGHT，具名账在下面）") == [
        (1, "5 具变异体⏎  （全部 CAUGHT")]
    assert _wrapped_verdicts("这切片 5 具变异体。\n全部 CAUGHT") == [], "跨过句号就是两句主张"
    assert _wrapped_verdicts("这切片 5 具变异体\n\n全部 CAUGHT") == [], "隔着空行的两截不是同一句"
    assert _verdict_readings("9 具变异体全部 CAUGHT") == _prose_verdicts("9 具变异体全部 CAUGHT"), \
        "同句内的读数只能被整行那一形认一次"
    assert _wrapped_verdicts("| 5 具变异 | 说明 |\n| W1 | 刀 | CAUGHT |") == [], \
        "表格两行之间不算一句主张：竖线把上下两格隔成两条记录"
    cap = int(re.search(r"\{0,(\d+)\}", WRAP_GAP).group(1))
    assert _wrapped_verdicts(f"5 具变异{'填' * (cap - 4)}\n全部 CAUGHT") != [], "上限之内要认"
    assert _wrapped_verdicts(f"5 具变异{'填' * cap}\n全部 CAUGHT") == [], \
        "越过上限就当成两句不相干的话——这条用例钉的是『上限在起作用』，不是钉那个数（数从 `WRAP_GAP` 现取）"


def _manual_verdict_lines() -> list[tuple[str, int, str]]:
    """手册页里每一处出现判词字面量的行——不分形状、不要求邻居。

    这一族前面的判据都要求邻居（具数、分数、表体里的竖线），所以"光杆判词"住在它们的缝里：
    `docs/views.md` 里某一轮的两处读数就是这么活到今天的（`#170` 的票面）。这一条不是又一种
    判据，是这一族的普查地板——管的是"手册里一共出现几处判词字面量、分别是哪一句"。
    """
    out: list[tuple[str, int, str]] = []
    for f in _manual_pages():
        for n, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if ANY_VERDICT.search(ln):
                out.append((f.name, n, ln.strip()[:60]))
    return out


def test_the_manuals_carry_one_bare_verdict_word_and_it_is_the_rule_sentence():
    """手册里只许留一处判词字面量：README 那句在教判词怎么写，不是在报某一轮的判决。

    地板取 1 而不是 0，是因为那句规矩（写超时也要记 hang、光杆的判词不算判决）对手册读者有用，
    把它摘去归档就等于把手册少一条习惯。这一条的失效方向是**漏判**：有人把某一轮的读数写成
    不带邻居、也不带那个教学习语的句式，它照样进名册、照样红；反过来若有人往规矩那句里塞读数，
    同一行里的「判词写成」会替它背书——这是这条地板明说的例外，不是没看见。
    """
    sites = _manual_verdict_lines()
    assert len(sites) == 1, \
        f"手册页里的判词字面量只许那一句规矩，现测 {len(sites)} 处（文件, 行, 原文）：{sites}"
    page, _, line = sites[0]
    assert page == "README.md", f"教判词怎么写的那句在 README，不在 {page}：{line}"
    assert "判词写成" in line, f"这一处不是那句规矩，是某一轮的读数：{line}"


BARE_LANDING = "光杆判词的两处读数搬进这一份"


def test_the_bare_verdict_readings_landed_verbatim_in_the_archive():
    """摘走的那两行**整行**逐字在归档里找得到——普查地板的"摘"不是删。

    整行而不是前缀，是 `#169` 那节 K6 第一跑 SURVIVED 换来的口径：那一轮按前缀断言，把登记行
    中段换掉一个字，文档闸门全绿。
    """
    body = _archive_section(BARE_LANDING)
    for span in ('**0 张弃票**，所以"把 `弃票` 列整个删掉"在整局用例上是等价变异（第一跑实测 `SURVIVED`）；而',
                 '顺带修掉一种**误记**：`N7`（空格填占位符）在七连跑时报的是 `CAUGHT [hang after 60s]`，看着像'):
        assert span in body, f"逐字登记缺这一整行：{span[:30]}…"


def _archive_section(heading: str) -> str:
    """归档里标题以 `heading` 开头的那一节的正文。

    只认标题不认正文，是为了让"落点没有读者"这一形直接抛错而不是静默扫空——
    `#143` 那条"手册里不许坐着账表"曾经因为扫法错了而绿了一整片。
    """
    for _, title, _, body in _ledger_sections():
        if title.startswith(heading) or heading in title:
            return body
    raise AssertionError(f"归档里没有〈{heading}〉这一节，落点核验直接空了")


def _roster_sentences(body: str) -> list[str]:
    """登记表「摘出来的句子」那一列——每格是手册页某一行逐字抄下来的前缀（登记时截到 150 字）。"""
    lines = body.splitlines()
    out: list[str] = []
    for i, ln in enumerate(lines):
        if not ln.startswith("| 编号 |"):
            continue
        if not (i + 1 < len(lines) and lines[i + 1].lstrip().startswith("| ---")):
            continue
        for row in lines[i + 2:]:
            if not row.startswith("| "):
                break
            out.append(row.split("|")[3].strip())
    return out


def test_every_registered_verdict_sentence_lands_verbatim_in_the_archive():
    """登记表里逐字摘下的每一句，都必须原样住在落点那一节里——搬走不等于改写。

    为什么拿登记表查而不是拿手册页查：手册页这一族已经被钉成零，从手册页里再也取不到这些句子；
    而登记表本身就坐在归档里，拿整篇归档自查等于自己背书（`#153` 那条规矩），所以两侧都按标题
    取了各自的正文。
    """
    cells = _roster_sentences(_archive_section(VERDICT_ROSTER))
    assert cells, "登记表那一列扫出来是空的，扫法坏了"
    landed = _archive_section(VERDICT_LANDING)
    missing = [c[:40] for c in cells if c not in landed]
    assert not missing, f"这些登记过的句子在落点那一节里找不到（被改写或被搬丢了）：{missing}"


def test_the_landing_reader_takes_each_side_by_heading():
    """两条核验各自的"扫法"都得有读者，否则它们会在扫空时绿着。

    登记表那一列只认"表头 + 分隔行"这个形状（同 `#143` 给账表立的那条件）；落点那一节里不许
    坐着登记表——落点核验要是退化成"在整篇归档里找"，登记表就会自己给自己背书。
    """
    assert _roster_sentences(
        "| 编号 | 行 | 摘出来的句子 | 节 |\n| --- | --- | --- | --- |\n| A | x | 甲 | 丙 |\n"
        "| B | y | 乙 | 丙 |\n") == ["甲", "乙"]
    assert _roster_sentences("| 编号 | 行 | 摘出来的句子 | 节 |\n散文一行\n| A | x | 甲 | 丙 |") == [], \
        "没有分隔行的那张表不算登记表"
    assert _roster_sentences(_archive_section(VERDICT_LANDING)) == [], \
        "落点那一节里出现了具名表：核验变成自己查自己"


def test_the_landed_verdict_blocks_keep_their_readings():
    """落点那一节自己得是一条有读数的账：块还在但判词被改写成散文、尾段被剪掉，都要在这里红。

    上面那条按"逐字前缀"核，剪掉某一具块的后半段它看不见——那半段里另有读数。地板取 13，
    不是量的数而是**登记表的行数**：第一步每处登记一行、本节每行留一条读数，现测两侧都是 13
    （06:30:31Z 量），少一条就是有一处判决在这一步里变了形。代价写在这里：判据按行计数，
    把相邻两行并成一行会误报，那时要改的是计数口径，不是把地板往下挪。
    """
    landed = _prose_verdicts(_archive_section(VERDICT_LANDING))
    assert len(landed) >= 13, f"落点那一节只剩 {len(landed)} 条判决读数，登记表却有 13 处"


WRAP_SPANS = ("（Q3 实测红在双向对账那条上", "这切片 5 具变异体")


def test_the_two_wrapped_readings_landed_with_their_verdicts():
    """`#169` 搬的两处都是跨行形状：整行那一形在归档里也读不到它们，所以按并集量。

    两条腿各管一件事：句子本身在不在（前缀逐字找）与判决读数还在不在（并集计数）。地板取 2，
    就是这一节搬来的处数。
    """
    body = _archive_section(WRAP_LANDING)
    assert [s for s in WRAP_SPANS if s not in body] == [], "搬来的句子不在了"
    readings = _verdict_readings(body)
    assert len(readings) >= 2, f"这一节里只剩 {len(readings)} 条判决读数：{readings}"


# --------------------------------------------------------------- 「数到 N」的跑次账
# `#135`/`#143` 立过规矩：逐片取证住归档，手册只留"怎么跑"和"该看到什么"。那两轮搬走了散文与变异
# 账表，**跑次读数那一族一处没搬**——〈测试〉里 34 处「某时刻 数到 **N**」加 2 处「N passed」还坐着，
# 而且已经烂了一处：手册第一行写的 `1015 passed`，与 `6141b18` 那一版实收集的 1023 条差 8。这一族的
# 形状没有歧义，外面那两个形状也**没有读者**（链句里嵌的"某模块现 N 条"另算——那三处 `#44` 一直在
# 核）：它报"这一刻套件多大"，而这个数每笔提交都变，写在手册里等于每笔提交制造一句假话——`#151` 那族
# 管的是"锚在哪个 ref 上"，这一族管的是"这个数根本不该住在这儿"。秒数那一形（`N.NNs`）不进判据：
# 延迟语料里它是产品常数，`6141b18` 的 README 里 43 处随这一族一起手搬，不设闸。
# 两形各留一份，好让"搬来的东西在不在归档里"能按形点名。合并式**从这两个分支拼出来**，不是抄第三份：
# `#153` 记过那一族双写的账，而这里连"两形与合并式一致"都不是一条可核对的主张——它是构造出来的。
CHAIN_TALLY = re.compile(r"数到\s*\*\*[0-9]+\*\*")
PASSED_TALLY = re.compile(r"[0-9]{3,6} passed")
RUN_TALLY = re.compile(rf"(?:{CHAIN_TALLY.pattern})|(?:{PASSED_TALLY.pattern})")


def _run_tally_lines(pages: dict[str, str]) -> list[tuple[str, int, str]]:
    """The check itself: which manual lines hold a per-run suite-size reading."""
    bad: list[tuple[str, int, str]] = []
    for page, text in pages.items():
        for no, line in enumerate(text.splitlines(), 1):
            if RUN_TALLY.search(line):
                bad.append((page, no, line.strip()))
    return bad


def test_a_run_tally_reading_is_told_from_a_live_count():
    """夹具：两形都认，三形都放过——被顶掉的旧数、不绑数字的加粗、墙钟与"跑起来 N 个用例"。

    第 4 行那格（`**709**` 光秃秃一个加粗数）放过是**故意的**：手册里"数到"才是跑次账的记号，
    把判据放宽成"任何加粗整数"会连 `#131` 那张表里的形状一起报掉。这一格的账由电池里
    "摘掉 `数到` 只留加粗"那一具还——摘掉之后第 4 行会一起红。
    """
    pages = {"a.md": "\n".join([
        "08:06:23Z 数到 **704**、09:00:43Z 数到",
        "现测 1015 passed，全程离线。",
        "末行印的是 `N tests collected`，要重数。",
        "**709**（中间那五格全是补的）",
        "墙钟 71.52s 只用来判断跑完了没有。",
        "`tests/test_cli.py` 现 63 条、跑起来 72 个用例。",
    ])}
    bad = _run_tally_lines(pages)
    assert [b[1] for b in bad] == [1, 2], \
        f"跑次账只认「数到 **N**」与「N passed」两形，别把加粗数字、墙钟与收集数一起报掉：{bad}"


def test_the_manual_carries_no_per_run_suite_reading():
    bad = _run_tally_lines({f.name: f.read_text(encoding="utf-8") for f in _manual_pages()})
    assert not bad, (
        "手册里还坐着逐片的套件规模读数（格式 文件:行 原文）："
        f"{[(b[0], b[1], b[2][:50]) for b in bad]}——这些数每笔提交都会变，手册抄不动。"
        "搬去 `docs/iterations.md`：那里的账按时刻记，本来就是给复数留的。手册只留"
        "\"总数要重数：数 `--collect-only` 末行\"那句活规矩"
    )


def test_the_archive_is_where_a_moved_run_tally_lands():
    """搬走的东西不许在移动里消失：归档里的跑次读数只多不少。

    上一条断言"手册里没有"，把归档从语料里抹掉它一样绿，所以这一条钉"有"。两形各自的地板不是
    同一种证人，账也分开记（02:12:48Z 现测归档：`数到 **N**` 3 处、`N passed` 152 处）：
    链形地板取 30，**搬运完成之前这一条是红的**（3 < 30），它钉的正是"搬来的 34 处在归档里"；
    `N passed` 那一形归档本来就有 152 处，150 的地板在移动前就满足，因此它不背书"这两处搬来了"，
    只背书"这一族扫法没坏、归档没被顺手截断"——把两处搬来的读数点名的活是落盘脚本当场做的
    （逐行按字节断言），不是一条每次都过的地板能代替的主张。
    """
    text = (ROOT / "docs" / ARCHIVE).read_text(encoding="utf-8")
    chain, passed = len(CHAIN_TALLY.findall(text)), len(PASSED_TALLY.findall(text))
    assert chain >= 30, f"归档里只剩 {chain} 处「数到 **N**」，多半是搬运丢了东西或扫法坏了"
    assert passed >= 150, f"归档里只剩 {passed} 处「N passed」，语料或扫法出问题了"


# --------------------------------------------------------------- 逐片时刻读数
# `#162` 那一族管的是"这一刻套件多大"，这一族管的是"这一刻量过"：现场钟点写进手册，下一次提交就把它
# 变成"手册声称它今天还成立"的假话——`#184` 四步摘掉的 39 处全是这一形。三形分开留，合并式从三个分支
# 拼出来，不抄第四份。
# **三种形状/一格人册明确不进判据**，账记在这里：
# * 裸 `HH:MM`——`100.87.65.60:13000` 那种 IP:端口 撞不得（去掉 `(?![\d:])` 那一格就会把它报成时刻），
#   现测手册五本 0 处裸形，所以这一条形只能靠 `Z` 或靠日期立起来；限界写在注释里而不是断言里。
# * 裸 ISO 日期——手册里 18 处「2026-09-21 起落盘」是能力历史（"什么时候开始有断言"），不是逐片取证。
# * `CLOCK_EXEMPT` 那一格——`calibration.md` 的文件 mtime 是**产物谱系**：sidecar 早于该字段没记采集
#   时间，那一格是这份延迟常数唯一能说"数据是哪一刻的"的东西。它不进"摘掉"那一档，但必须报名字。
CLOCK_HMS = re.compile(r"\d{2}:\d{2}:\d{2}Z")
CLOCK_MIN = re.compile(r"\d{2}:\d{2}Z(?![\d:])")
CLOCK_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}(?::\d{2})?(?::\d{2})?Z?")
ANY_CLOCK = re.compile(rf"(?:{CLOCK_HMS.pattern})|(?:{CLOCK_MIN.pattern})|(?:{CLOCK_ISO.pattern})")
CLOCK_EXEMPT = {("calibration.md", "2026-09-20T12:56:24Z"): "产物谱系：文件 mtime，不是某一片的现场取证"}


def _clock_lines(pages: dict[str, str],
                 exempt: dict[tuple[str, str], str] | None = None) -> list[tuple[str, int, str]]:
    """The check itself: which manual lines hold a per-slice wall-clock reading."""
    exempt = CLOCK_EXEMPT if exempt is None else exempt
    bad: list[tuple[str, int, str]] = []
    for page, text in pages.items():
        for no, line in enumerate(text.splitlines(), 1):
            for m in ANY_CLOCK.finditer(line):
                if (page, m.group()) in exempt:
                    continue
                bad.append((page, no, line.strip()))
                break
    return bad


def _clock_tokens(pages: dict[str, str]) -> set[tuple[str, str]]:
    return {(page, m.group()) for page, text in pages.items() for m in ANY_CLOCK.finditer(text)}


def test_a_clock_reading_is_told_from_an_endpoint_and_a_history_date():
    """夹具：三形都认，三种邻居都放过，而放过的那格豁免必须能被关掉。

    第 6 行（能力历史日期）与第 4 行（IP:端口）放过是**故意的**：把它们算进逐片取证，连"什么时候开始
    有断言"这种手册该留的句子会一起报掉。第 7 行走的是另一条腿——同一具钟点，在册时放过、把名册换成
    `{}` 时必须被抓，所以豁免是一个可核对的开关而不是一句"这格不算"。夹具页必须叫 `calibration.md`：
    名册的键是（文件, 时刻）两样，换具别的名字这一腿就只在测一具永远落不到实处的豁免了。
    """
    page = "\n".join([
        "08:06:23Z 数到 **704**、09:00:43Z 数到",
        "页眉静默少一格。2026-09-22T00:49Z 实测复现：同一份文件",
        "照样绿。13:38Z 实测语料里就有两处把同一个",
        "它要 `100.87.65.60:13000` 上那个判官，见上面〈配置：密钥的值永远不进文件〉。",
        "出厂值下端点判决的最坏时刻 `3×5.0 + (1.5 + 3) = 19.5s` 必须小于席位 deadline 45s。",
        "（一局一格，2026-09-21 起落盘）带着同一份 `RegionBudget`，所以 audit 能从另一侧再减一遍。",
        "唯一可依据的是文件 mtime 2026-09-20T12:56:24Z；本报告离线重渲染自 `data/calibration.json`",
    ])
    bad = _clock_lines({"calibration.md": page}, exempt={})
    assert [b[1] for b in bad] == [1, 2, 3, 7], \
        f"名册关掉之后，逐片时刻只认带 `Z` 的两形与带日期的 `T` 形，别把 IP:端口、延迟常数、能力历史日期一起报掉：{bad}"
    assert [b[1] for b in _clock_lines({"calibration.md": page})] == [1, 2, 3], \
        "第 7 行应当由名册豁免掉——名册不生效说明豁免那一条是空话"


def test_the_manual_carries_no_per_slice_clock_reading():
    bad = _clock_lines({f.name: f.read_text(encoding="utf-8") for f in _manual_pages()})
    assert not bad, (
        "手册里还坐着逐片的现场钟点（格式 文件:行 原文）："
        f"{[(b[0], b[1], b[2][:50]) for b in bad]}——`#184` 把 39 处摘干净并逐字抄进了归档，"
        "再往手册里写时刻等于重新制造那句假话。落点：`docs/iterations.md` 对应片节的〈跑次〉；"
        "确实该留在手册的那一刻（产物谱系）要写进 `CLOCK_EXEMPT` 并附理由，不是把这行删了交差。"
    )


def test_every_declared_clock_exemption_is_still_a_live_reading():
    """豁免名册的每一格都要有主人：文件里还在、且理由非空——否则名册会慢慢变成垃圾桶。

    `#183` 之前那一族"预先声明的缺席"用的是同一个形状：声明本身要可核对，删掉被声明的那格要红。
    """
    live = _clock_tokens({f.name: f.read_text(encoding="utf-8") for f in _manual_pages()})
    ghosts = [k for k in CLOCK_EXEMPT if k not in live]
    assert not ghosts, f"名册里这些时刻在手册里已经找不到了，要么删掉声明要么它在等谁：{ghosts}"
    assert all(reason.strip() for _, reason in CLOCK_EXEMPT.items()), "豁免必须逐格带理由"


def test_the_archive_is_where_a_moved_clock_reading_lands():
    """搬运只许往归档里加：三形各自的地板取 `#184` 动手之前那一版（`de94602`）的实测量。

    地板取**动手前**的数（828 / 928 / 47；今天 870 / 985 / 57）而不是今天的数，因为把地板钉在今天的
    读数上，等于每往归档抄一条时刻就要重顶一次地板——`#151` 与"守卫顶历史"那一族记过这种账。代价要
    如实说：这一形今天有 42 / 57 / 10 的余量，零星少几条不红（电池里那一具截尾 60 行的刀就没红），
    它防的是整节被抹；"摘走的每一句逐字在册"由〈摘走必逐字在册〉那一族管。
    """
    text = (ROOT / "docs" / ARCHIVE).read_text(encoding="utf-8")
    got = [len(p.findall(text)) for p in (CLOCK_HMS, CLOCK_MIN, CLOCK_ISO)]
    floors = [828, 928, 47]
    assert all(g >= fl for g, fl in zip(got, floors)), (
        f"归档里的逐片时刻比 `#184` 动手之前少了：现读 {got}，地板 {floors}——"
        "手册侧摘掉的东西必须在归档里找得到主人，少了就是搬运搬丢了"
    )


# --------------------------------------------------------------- 〈标题〉指针
# `#135`/`#143` 把逐片取证搬去 `docs/iterations.md` 之后，手册里"细节在归档"的入口几乎全写成
# 「见〈某节标题〉」：README 的能力清单压成指针形之后也只有这一个落点。读者照它搜索，标题被改过
# 名字（或被作者记错）就落在空处，而这件事不会红——所以这一族扫"指没指到"。
# 它不管"指对了没有"：那是上面行号那一族的事，指针这里只知道标题文本。
# `[^〉\n]` 那一版把散文的硬换行当成了"这里没有指针"，于是 5 处断行的指针既不进判据也不进计数。
# 改成允许跨行、但不许再套一个 `〈`：有人写了半截括号时，这一条宁可少吞一段，也不会把整节吸进一次匹配。
POINT = re.compile(r"〈([^〉〈]{2,})〉")
HEAD = re.compile(r"^#{2,4}\s+(.*)$")
# 标题里的标点是排版不是主张：指针常把反引号、引号、冒号、逗号省掉，或把全角换成半角。
# `\n` 也在这一份里：跨行的指针折掉换行才是那一节标题，续行的缩进同样一起折掉。
PUNCT = "\"'`“”‘’，,：:、（）()[]【】…—～~ \n" + "　"


def _flat(text: str) -> str:
    for ch in PUNCT:
        text = text.replace(ch, "")
    return text


def _headings(pages: dict[str, str]) -> list[str]:
    """语料里所有二到四级的节标题——指针指得着的只有这一份清单。"""
    out: list[str] = []
    for text in pages.values():
        for line in text.splitlines():
            if (m := HEAD.match(line)):
                out.append(m.group(1).strip())
    return out


def _pointer_corpus() -> dict[str, str]:
    """被扫的那几本：手册五本加归档——历史里指错地方也一样是指错。"""
    return {f.name: f.read_text(encoding="utf-8") for f in DOCS}


def _dead_pointers(pages: dict[str, str], heads: list[str]) -> list[tuple[str, int, str]]:
    """The check itself: which 〈…〉 point at nothing a reader can search for.

    判据只有一条形状：指针去掉标点后必须是某一节标题（同样去掉标点）的子串。省略号尾巴在这一条里
    不需要特例——`〈轴守卫把帮助文本当成了读者…〉` 去掉 `…` 之后本来就是那节标题的子串。**多解不算死**：
    两处都指到时读者仍然搜得到，这一片管的是"点空"，把消歧立成规矩会把 `〈命令一览〉` 这种本来就
    读得通的写法改成得更啰嗦。
    """
    norms = [_flat(h) for h in heads]
    bad: list[tuple[str, int, str]] = []
    for page, text in pages.items():
        for m in POINT.finditer(text):
            want = _flat(m.group(1))
            if not want or not any(want in h for h in norms):
                bad.append((page, text.count("\n", 0, m.start()) + 1, m.group(1)))
    return bad


def test_every_section_pointer_in_the_manuals_points_at_a_real_heading():
    pages = _pointer_corpus()
    bad = _dead_pointers(pages, _headings(pages))
    assert not bad, (
        "这些〈标题〉指针归一化后不是任何一节标题的子串，读者搜它落空（页:行 原文见元组）："
        f"{bad}"
    )


def test_the_pointer_scanner_fires_on_a_dead_title_only():
    """判据的两侧都得有读者：四种指法都算指着，两种点空的都报出来，一条限界按声明放过。

    只跑上一条不足以证明它有用——把 `_dead_pointers` 改瞎（永远返回 `[]`），`assert not bad` 照样绿。
    这一条不读真实语料，所以文档变好不会削弱它：它钉的是检测能力在。
    """
    heads = ["配置：密钥的值永远不进文件",
             '行号闸门把"顶偏"和"点错东西"报成同一句话，于是变异电池拿它当行为证人：`#77`',
             "人怎么上桌：`--human 座位`", "人怎么上桌的另一种写法"]
    alive = "见〈配置：密钥的值永远不进文件〉、〈行号闸门把顶偏和点错东西报成同一句话〉、" \
            "〈人怎么上桌…〉和〈人怎么上桌〉"
    assert _dead_pointers({"a.md": alive}, heads) == [], \
        f"四种指法（连标点抄全、省标点、省略号尾巴）外加两处都指到的歧义都该算指着：{alive}"
    dead = {"a.md": "见〈这一节从来就没有过〉", "b.md": "见〈，：〉"}
    assert [d[0] for d in _dead_pointers(dead, heads)] == ["a.md", "b.md"], \
        "点了不存在的一节、以及去掉标点就剩空串的指针都必须报，不然这一族只会说'都对'"
    assert _dead_pointers({"a.md": "见〈A〉"}, heads) == [], \
        "单字标记不进扫面：这是声明的限界，放宽它（`{2,}` 改成 `{1,}`）这条就得红"


def test_the_pointer_scanner_reads_a_pointer_broken_by_a_hard_wrap():
    """被硬换行截断的指针也要扫得到：断在一行中间的〈…〉，读者照搜，刀也照样要落。

    两侧各一条，缺一不可。只写"死的那条要报"，修的人改成"凡跨行的都报"也能绿；只写"活的那条不报"，
    把这一族整个关黑也一样绿。行号钉在**开括号那一行**：读者按它跳转，落在尾部那行等于没落。
    """
    heads = ["配置：密钥的值永远不进文件"]
    dead = _dead_pointers({"a.md": "见〈这一节从来就\n没有过〉"}, heads)
    assert [d[2] for d in dead] == ["这一节从来就\n没有过"], \
        f"跨行的死指针必须报出来——现行扫法逐行匹配 `POINT`，它落在空处：{dead}"
    assert [d[1] for d in dead] == [1], f"报的行号要是开括号所在那一行，不是尾括号那一行：{dead}"
    assert _dead_pointers({"a.md": "见〈配置：密钥的\n值永远不进文件〉"}, heads) == [], \
        "同一形状的活指针不该被误报，不然这一族会把所有跨行括号都喊成死"


def test_the_pointer_scanner_sees_every_open_bracket_in_the_real_corpus():
    """正控制（真实语料）：每本里扫到的指针数必须等于开括号数，也等于闭括号数。

    这一条不等价于"没有死指针"：它管扫面完整。16:55:03Z 现测折叠扫法 67 处（README 33、归档 31、
    `comparison.md` 3），逐行扫法只有 62——少的 5 处全是被硬换行截断的（README 能力清单里 3 处、归档 2 处）。
    三数不等还接住另一种损坏：有人写了半截括号（只有 `〈` 没有 `〉`），那既不是指针也不是标题，
    逐行扫法会静默跳过它。
    """
    for name, text in _pointer_corpus().items():
        found = len(POINT.findall(text))
        open_, close = text.count("〈"), text.count("〉")
        assert found == open_ == close, (
            f"{name}：扫到 {found} 处，开括号 {open_}、闭括号 {close} —— 三数不等说明"
            "有半截括号，或扫法看不见某种形状"
        )


def test_the_pointer_scanner_is_not_reading_an_empty_corpus():
    """正控制：扫面里必须真有指针，而且 README 和归档两本都要有。

    上一条断言的是"没有死指针"，把 `POINT` 改成接不住任何形状（比如把全角尖括号换成半角）它一样绿。
    地板取 50：判据落地时（15:47:04Z）现测 59 处，讲这一族的散文落盘之后（16:11:32Z）逐行扫法是 62 处
    （README 30、归档 29、`comparison.md` 3）；`#144b` 把跨行的指针收进扫面后同一棵树复测是 67 处
    （README 33、归档 31、`comparison.md` 3）——涨的 5 处不是新写的句子，是以前**看不见**的。
    压缩那一片还会让它涨，所以地板钉在 50 而不是现值。低于地板说明扫法坏了。
    """
    pages = _pointer_corpus()
    hits = {name: len(POINT.findall(text)) for name, text in pages.items()}
    total = sum(hits.values())
    assert total >= 50, f"只扫到 {total} 处〈标题〉指针（16:55:03Z 现测 67），多半是扫法坏了：{hits}"
    assert hits["README.md"] and hits["iterations.md"], \
        f"README 与归档是这一族的两个大户，任一方为 0 说明语料被收窄了：{hits}"


# ------------------------------------------------------------ 「HEAD 那一版」的读数锚
# 一句读数写"拿 HEAD 那一版量的"，落笔时是真的；下一次提交之后同一句话指的是另一棵树——字没动，
# 对象换了。`#150` 的收尾读数就被它自己那一笔提交顶过一次。判据只有一条形状：独立成词的 HEAD
# 所在行，同行必须出现一个 SHA 形状。**词界是判据的一半**：`OFFER_HEADER` 里那个 HEAD 不是这个词。
HEAD_WORD = re.compile(r"(?<![A-Za-z0-9_])HEAD(?![A-Za-z0-9_])")
SHA_WORD = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{7,40}(?![0-9a-f])")


def _unanchored_head_lines(pages: dict[str, str]) -> list[tuple[str, int, str]]:
    """The check itself: which lines lean on `HEAD` without naming the commit they were true of."""
    bad: list[tuple[str, int, str]] = []
    for page, text in pages.items():
        for no, line in enumerate(text.splitlines(), 1):
            if HEAD_WORD.search(line) and not SHA_WORD.search(line):
                bad.append((page, no, line.strip()))
    return bad


def test_a_reading_anchored_on_head_must_name_the_commit():
    """夹具：两种该报的形状都报，三种不该报的都不报——带 SHA 的、锚在父提交的、嵌在标识符里的。

    第 5 行那一格（标识符）落笔即绿，还账的是电池里把词界拆掉那一具：拆掉之后它会连同 `OFFER_HEADER`
    那两处真语料一起被报成假命中。第 3、4 行同理归"把 SHA 要求拆掉"那一具。
    """
    pages = {"a.md": "\n".join([
        "拿 HEAD 那一版与现在各量一遍。",
        "在 HEAD 的代码上一行一行打出来。",
        "这一版 HEAD `0451f43` 才是量过的那一棵。",
        "现 HEAD 上它已进注释（锚在父提交 `f0414b8^`）。",
        "`OFFER_HEADER` 把可答段立成一段。",
        "`git archive HEAD` 出来的整包副本。",
    ])}
    bad = _unanchored_head_lines(pages)
    assert [b[1] for b in bad] == [1, 2, 6], \
        f"无锚的 HEAD 读数要逐行报出来、带 SHA 的和标识符里的不许报：{bad}"


def test_the_head_anchor_scanner_tells_the_word_from_the_identifier():
    """正控制（真实语料）：这一族既不是空扫，也不是把 `OFFER_HEADER` 那种词也算成 HEAD。"""
    pages = _pointer_corpus()
    words = sum(len(HEAD_WORD.findall(line)) for text in pages.values() for line in text.splitlines())
    anywhere = sum(text.count("HEAD") for text in pages.values())
    assert anywhere > words, \
        f"语料里必须真有嵌在标识符里的 HEAD（现测独立成词 {words}、子串 {anywhere}）——相等说明词界没在起作用"
    assert words >= 5, f"独立成词的 HEAD 只有 {words} 处（19:41:31Z 现测 7），多半是扫法坏了"


def test_no_manual_line_leans_on_head_without_naming_a_commit():
    bad = _unanchored_head_lines(_pointer_corpus())
    assert not bad, (
        "这些句子把读数锚在 HEAD 上却没点名提交：下一笔提交之后同一句话指的是另一棵树，而它能核的那条"
        "命令会静默换对象。修法是在同一行写进量过的那个 SHA（`git log -1 --before=<句子里的时刻> "
        '--format=%H main` 能把当时的 HEAD 印出来）；报的格式是 文件:行 原文：'
        f"{[(b[0], b[1], b[2][:60]) for b in bad]}"
    )


# ------------------------------------------------------------- 「N 具变异」的落点账
# 手册能力清单里每一个「N 具变异」都是一句主张：那一批电池跑了 N 具。归档按批记具名表，手册只留
# 一个数，而这个数**没有任何东西在读**——写错了、或者把两批的和当成一批的具数，读者在手册里查不出来。
# 行号和指针两族都不管数：指针只保证"点得到一节"，那节里写着几具它不看。所以这一族回查落点。
CAP_HEADING = "这个仓库现在能做什么、不能做什么"
# 汉字和半角数字都收，空格可有可无：手册写「6 具变异」，也写「八具手术刀」。
# 读取侧允许被硬换行劈开一次（手册里真有两处「N 具」落在行尾），但不许跨空行——
# 隔着一整行空白的两句不是同一句主张。背书侧（`_states`）不收这一形，量过的代价见那条用例。
KNIFE_SEP = r"[ \t]*(?:\n[ \t]*)?"
KNIFE_CLAIM = re.compile(
    rf"(?<!\d)([0-9]+|[一二三四五六七八九十]{{1,3}}){KNIFE_SEP}具{KNIFE_SEP}(?:变异|手术刀|刀)")
KNIFE_NOUNS = "(?:变异|手术刀|刀)"
TICKET = re.compile(r"#(\d{1,3})")
HEAD_LEVEL = re.compile(r"^(#{2,4})\s+(.*)$", re.M)
# 具名表的第一格是「变异」「具」「刀」之一（`#143` 那张 D 表用「变异」，`#71` 用「具」，`#73` 用「刀」）。
LEDGER_TABLE = re.compile(r"^\|\s*(?:变异|具|刀)\s*\|[^\n]*\n\|\s*-{3,}[^\n]*\n((?:\|[^\n]*\n?)+)", re.M)
CN_DIGITS = "零一二三四五六七八九"


def _cn_forms(value: int) -> list[str]:
    """整数的汉字写法（2 另收「两」）——归档里的具数常写成「六具跑完」「八具手术刀」。"""
    if value < 10:
        return [CN_DIGITS[value], "两"] if value == 2 else [CN_DIGITS[value]]
    tens, ones = divmod(value, 10)
    return [("" if tens == 1 else CN_DIGITS[tens]) + "十" + (CN_DIGITS[ones] if ones else "")]


def _to_int(token: str) -> int:
    if token.isdigit():
        return int(token)
    if "十" in token:
        head, _, tail = token.partition("十")
        return (1 if not head else CN_DIGITS.index(head)) * 10 + (CN_DIGITS.index(tail) if tail else 0)
    return CN_DIGITS.index(token)


def _capability_bullets() -> list[tuple[int, str]]:
    """README 那一节的每条 bullet：起始行号 + 拼起来的正文（缩进续行归同一条）。"""
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    if f"## {CAP_HEADING}" not in lines:
        raise AssertionError(f"README 里没有「## {CAP_HEADING}」这一节，扫面直接空了")
    i0 = lines.index(f"## {CAP_HEADING}")
    out: list[tuple[int, str]] = []
    cur: list[str] = []
    start = 0
    for n in range(i0 + 1, len(lines)):
        line = lines[n]
        if line.startswith("## "):
            break
        if line.startswith("- "):
            if cur:
                out.append((start, "\n".join(cur)))
            cur, start = [line], n + 1
        elif line.startswith("  ") and cur:
            cur.append(line)
        elif cur:
            out.append((start, "\n".join(cur)))
            cur = []
    if cur:
        out.append((start, "\n".join(cur)))
    return out


def _ledger_sections() -> list[tuple[str, str, str, str]]:
    """归档里每一节的 (文件, 标题原文, 归一化标题, 正文)——README 不当落点：拿手册查手册是自我背书。"""
    out: list[tuple[str, str, str, str]] = []
    for f in DOCS:
        if f.name == "README.md":
            continue
        text = f.read_text(encoding="utf-8")
        marks = [(m.start(), len(m.group(1)), m.group(2)) for m in HEAD_LEVEL.finditer(text)]
        for i, (pos, level, title) in enumerate(marks):
            end = next((marks[j][0] for j in range(i + 1, len(marks)) if marks[j][1] <= level), len(text))
            out.append((f.name, title, _flat(title), text[pos:end]))
    return out


def _ledger_rows(body: str) -> list[int]:
    """那一节的具名表各有几行数据——表的行数本身就是一个具数。"""
    return [len([ln for ln in m.group(1).splitlines() if ln.strip().startswith("|")])
            for m in LEDGER_TABLE.finditer(body)]


def _state_rule(body: str, value: int) -> tuple[str, str]:
    """这一节给 `value` 这个具数背书**靠的是哪条规则**：`(规则, 被认下的那种写法)`。

    规则取 `noun`（这一节把「N 具+名词」写了出来，汉字与阿拉伯两种写法都算）或 `table`（有一张具名表
    正好 N 行），没背书返回 `("", "")`。两条规则都要**紧**：裸的「N 具」不当背书，因为同一节里「8 具」
    可能说的是另一批。放宽它的代价不是"多认下几句真话"而是**换掉账本**：17:02:11Z 实测 20 处有落点的
    主张里 0 处只靠裸数字，可 20:32:16Z 实测放宽后 5 处靠表行数背书的主张全部易主成裸数背书。
    **这份判据只许住在这里**：正控制过去在自己体内抄了一份同形状的正则，那条用例于是改测自己的拷贝，
    谓词漂走时它不红——漂走的代价见 `#153` 那一节。
    """
    for form in [str(value), *_cn_forms(value)]:
        if re.search(rf"(?<!\d){form}\s?具\s?{KNIFE_NOUNS}", body):
            return "noun", form
    if value in _ledger_rows(body):
        return "table", str(value)
    return "", ""


def _states(body: str, value: int) -> str:
    """背书判决的人话——只由 `_state_rule` 那张牌翻出来，别再抄一份判据。"""
    rule, form = _state_rule(body, value)
    if rule == "noun":
        return f"写了 {form} 具+名词"
    return f"有一张 {value} 行的具名表" if rule == "table" else ""


def _unbacked_knife_counts(bullets: list[tuple[int, str]] | None = None,
                           sections: list[tuple[str, str, str, str]] | None = None,
                           ) -> list[tuple[int, str, list[str]]]:
    """The check itself: which 「N 具变异」 in the manual has no ledger that states N.

    落点从**同一条 bullet**里取：〈指针〉按归一化标题子串找，`` `#NN` `` 按标题尾部的票号找。
    跨 bullet 不算——一条写了数没写落点、邻条写了落点，读者仍然查不到这一条的数。
    """
    bullets = bullets if bullets is not None else _capability_bullets()
    sections = sections if sections is not None else _ledger_sections()
    bad: list[tuple[int, str, list[str]]] = []
    for start, body in bullets:
        claims = KNIFE_CLAIM.findall(body)
        if not claims:
            continue
        wants = [_flat(p) for p in POINT.findall(body)]
        tickets = set(TICKET.findall(body))
        located = [(name, title, text) for name, title, flat, text in sections
                   if any(w and w in flat for w in wants) or any(f"#{t}" in title for t in tickets)]
        for match in KNIFE_CLAIM.finditer(body):
            value = _to_int(match.group(1))
            if any(_states(text, value) for _, _, text in located):
                continue
            bad.append((start, match.group(0), [f"{name}:{title[:22]}" for name, title, _ in located]))
    return bad


def test_every_knife_count_in_the_capability_list_has_a_ledger_that_states_it():
    bad = _unbacked_knife_counts()
    assert not bad, (
        "这些具数在本手册点到的那一节里没有落点（读者无从复核，元组是 行号 主张 点到的节）："
        f"{bad}"
    )


def test_the_knife_ledger_check_bites_on_each_of_its_two_rules():
    """合成语料把判据的每一半各钉一次：两条背书规则、两种定位、一条"跨 bullet 不算"、一条"裸数字不算"。

    真实语料那条跑绿不证明这些半各有用——17:02:11Z 现测 23 处主张里 15 处靠「N 具+名词」、5 处靠
    具名表行数、0 处只靠裸数字；把任一半改成永真，真实语料那条不红，只有这一条红。
    三处聚合数（11/68/17）换成各批自己的具数之后 17:10:15Z 复测是 22 处：20 靠名词、2 靠表行数。
    """
    sections = [("iterations.md", "第一批电池：`#900`", "第一批电池#900", "判据落地时跑了 7 具变异。"),
                ("iterations.md", "第二批电池：`#901`", "第二批电池#901",
                 "| 具 | 改动 | 结果 |\n| --- | --- | --- |\n| K1 | 摘掉判据 | RED |\n"
                 "| K2 | 摘掉地板 | RED |\n| K3 | 负控制 | GREEN |"),
                ("iterations.md", "第三批电池：`#902`", "第三批电池#902", "八具手术刀全部具名兑现。")]
    backed = [(1, "- ✅ 甲：7 具变异见〈第一批电池〉。"),
              (2, "- ✅ 乙：三具变异（`#901`）都具名。"),
              (3, "- ✅ 丙：八具手术刀见〈第三批电池…〉。")]
    assert _unbacked_knife_counts(backed, sections) == [], \
        f"数字与汉字两种写法、相邻与表行数两条规则、指针与票号两种定位都该算指着：{backed}"
    wrong = [(1, "- ✅ 甲：8 具变异见〈第一批电池〉。"),
             (2, "- ✅ 乙：四具变异（`#901`）都具名。")]
    assert [b[1] for b in _unbacked_knife_counts(wrong, sections)] == ["8 具变异", "四具变异"], \
        "同一节里写着 7 具而手册写 8 具、表里三行而手册说四具，都必须报——差一格就是腐烂"
    orphan = [(1, "- ✅ 这条只有数，没有落点：九具变异。"),
              (2, "- ✅ 落点在邻条：见〈第一批电池〉。")]
    assert [b[0] for b in _unbacked_knife_counts(orphan, sections)] == [1], \
        "跨 bullet 的落点不算：读者照着有数的那一条查，仍然查不到"
    bare = [(1, "- ✅ 那一节只给了裸数，不算背书：9 具变异见〈只提裸数〉。")]
    sections_bare = [("iterations.md", "只提裸数：`#903`", "只提裸数#903", "那一跑共 9 具，逐具结论一致。")]
    assert [b[0] for b in _unbacked_knife_counts(bare, sections_bare)] == [1], \
        "裸「N 具」不做背书（17:02:11Z 实测真语料 0 处需要它），放宽它等于给错号留邻居蒙绿的空间"
    launder = [(1, "- ✅ 甲：8 具变异见〈第一批电池〉。")]
    sections_lazy = [("iterations.md", "第一批电池：`#904`", "第一批电池#904", "那一跑共 18 具变异。")]
    assert [b[1] for b in _unbacked_knife_counts(launder, sections_lazy)] == ["8 具变异"], \
        "那一节写着 18 具而手册写 8 具时必须报：背书串的左边界不收住，8 就从 18 里数出来，错号靠邻居蒙绿"


def test_a_knife_count_broken_by_a_hard_wrap_is_still_counted():
    """断在行尾的那句具数，落点账也看得见——这一形在手册里真实存在，只是以前扫不到。

    19:02:11Z 现测：把能力清单那一节的正文逐条交给判据，严格版数到 23 处主张，允许"数字与名词之间
    隔一个换行加续行缩进"的版本数到 25 处，两处新增都仍然有落点。落点侧那半判据**不放宽**——同一次
    扫描量到归档里 0 处具数是只靠跨行拼出来的，所以这一刀只改读取侧。
    """
    wrapped = "- ✅ 甲：那一跑九具\n  变异全在归档，见〈第一批电池〉。"
    assert KNIFE_CLAIM.findall(wrapped) == ["九"], \
        f"被硬换行截断的具数要像写在同一行那样被数到：{KNIFE_CLAIM.findall(wrapped)}"
    assert KNIFE_CLAIM.findall("- ✅ 甲：那一跑九\n  具变异全在归档") == ["九"], \
        "断点落在数字与「具」之间时也算同一句主张（这一条落笔即绿，还账的是电池 K1）"
    sections = [("iterations.md", "第一批电池：`#905`", "第一批电池#905", "判据落地时跑了 7 具变异。")]
    bad = _unbacked_knife_counts([(1, wrapped)], sections)
    assert [b[1].split("具")[0] for b in bad] == ["九"], \
        "落点写着 7 具而手册写九具时必须报——放宽读取侧之后这个错号才有牙"
    assert KNIFE_CLAIM.findall("- ✅ 甲：九具\n\n  变异都具名。") == [], \
        "只许跨一个换行：隔着一整行空白的两句不是同一句主张"
    cross_backed = ("iterations.md", "第四批电池：`#906`", "第四批电池#906",
                    "那一跑了十二具\n  变异，全部具名。")
    assert [b[1] for b in _unbacked_knife_counts(
        [(9, "- ✅ 丁：十二具变异见〈第四批电池〉。")], [cross_backed])] == ["十二具变异"], \
        "背书侧不放宽：归档里被硬换行劈开的具数不算把数说出来了（真语料 0 处需要它，而让账写成一块是更好的约定）"


def test_the_knife_ledger_scanner_is_not_reading_an_empty_corpus():
    """正控制：扫面里必须真有具数主张，而且两条背书规则在真语料上各有读者。

    把 `KNIFE_CLAIM` 改坏（比如只认「变异」不认「刀」）时"没有红"会变成"没主张"，上一条就绿了。
    地板取 15：17:01:25Z 现测 23 处主张（汉字与半角两种写法都在），三处聚合数换成各批自己的具数后
    17:10:15Z 复测 22 处——压掉一半也还在地板上。
    """
    bullets = _capability_bullets()
    claims = [c for _, body in bullets for c in KNIFE_CLAIM.findall(body)]
    assert len(claims) >= 15, f"只扫到 {len(claims)} 处具数主张（17:10:15Z 现测 22），多半是扫法坏了：{claims}"
    sections = _ledger_sections()
    assert all(name != "README.md" for name, _, _, _ in sections), \
        "背书只从 docs/ 取：拿手册查手册是自我背书，README 那句「N 具」不算另一句的落点"
    rules: list[str] = []
    for _, body in bullets:
        wants = [_flat(p) for p in POINT.findall(body)]
        tickets = set(TICKET.findall(body))
        located = [(name, title, text) for name, title, flat, text in sections
                   if any(w and w in flat for w in wants) or any(f"#{t}" in title for t in tickets)]
        for token in KNIFE_CLAIM.findall(body):
            for _, _, text in located:
                rule, _form = _state_rule(text, _to_int(token))
                if rule:
                    rules.append(rule)
                    break
    by_noun, by_table = rules.count("noun"), rules.count("table")
    assert by_noun and by_table, \
        f"两条背书规则必须各有读者（汉字写法与阿拉伯写法、具名表行数），现在是 相邻={by_noun} 表={by_table}"


def test_the_backing_predicate_is_implemented_once_in_this_file():
    """背书判据只许有一份实现。

    正控制那条用例过去在自己体内抄了一份同形状的正则（两条背书规则各写一遍，判据不在被叫到的函数里），
    它于是改测自己的拷贝。代价是量出来的：把名词分支改成"认下了却报成表"（判决一格不移动，只换规则的
    牌子）在 HEAD 那一版上 0 红（`94f0c05`，20:31:11Z–20:31:24Z 那一趟，基线 49 绿），判据抽成一份实现、正控制改成
    读规则名之后，同一把刀红 1 条（20:30:12Z–20:30:41Z 那一趟）。放宽名词要求那一刀两边都不瞎（HEAD 侧
    红 2、现侧红 4），差别在现侧多红了正控制自己——20:32:16Z 现测放宽后真语料 5 处表行数背书全部易主。
    这条用例要的是形状只住一处：形状按运行时拼装，否则这句话自己就命中判据（`#144b` 那条"描述形状别贴
    原形"）。
    """
    src = open(__file__, encoding="utf-8").read().splitlines()
    shape = re.compile("具" + "[^\\n]{0,10}?" + "".join(["KNIFE_", "NOUNS"]))
    owners: dict[str, list[int]] = {}
    cur = "<模块顶层>"
    for n, line in enumerate(src, 1):
        hit = re.match(r"def (\w+)", line)
        if hit:
            cur = hit.group(1)
        if shape.search(line):
            owners.setdefault(cur, []).append(n)
    assert set(owners) == {"_state_rule"}, (
        f"背书判据的形状落在这些函数里：{owners}——它只许住在 `_state_rule` 一处。"
        "多出来的那一份会让正控制改测自己的拷贝：谓词漂走时它不红")


DEMON_PTR = re.compile(r"(?:参见|详见|见|戳|按)[^。\n]{0,12}?(?:上|下|那|这)\s*一?\s*节")
LANDING = re.compile(r"〈[^〉〈]{2,}〉|`#\d+`|#\d+")


def _unanchored_demonstratives(pages: dict[str, str]) -> list[tuple[str, int, str]]:
    """哪些「见…那一节」在同句里拿不到落点。

    位置不是落点：归档往下追加节，「下一节」指的是写它时的那一节；手册里那句
    "见上一节"要退的节住在另一本，读者在手册里退无可退（`#149` 记的第一次指错，账见
    〈手册里"细节在那一节"的指法第一次有人核对指得着〉）。落点取两种现成的形：票号，或者
    〈标题〉——后者还额外被死指针那道闸看着。

    两条豁免都有语料，不是给判据留后门：
    - **反引号跨度内的匹配是在说这个形状，不是在用它。** 归档里有一节整节讨论这族字样，
      它把 `见上一节`、`见下一节` 列成一串扫面对照——不豁免的话它自己就是第一处红。
    - **只取这一句，不取整段**（`_cited_sentence`）。邻句里的票号不给背书：`#108` 为了同一件
      事收紧过一次判据，而这里的形状是「……`#98` 之后是 847，见下面那一节的账」这种——句内
      那个票号恰恰**不是**落点（它指回去年的基线），所以这一条只算"有硬标识"而不核对指向。
      指针自己那一行被硬换行劈开时仍算同句（`#150` 的教训）。
    """
    out: list[tuple[str, int, str]] = []
    for doc, body in pages.items():
        lines = body.splitlines()
        for no, line in enumerate(lines, 1):
            for m in DEMON_PTR.finditer(line):
                if any(s.start() <= m.start() and m.end() <= s.end()
                       for s in BACKTICK.finditer(line)):
                    continue
                block = lines[max(0, no - 3):no + 2]
                sentence = _cited_sentence(block, no - 1 - max(0, no - 3), m.start())
                if not LANDING.search(sentence):
                    out.append((doc, no, m.group(0)))
    return out


def test_a_demonstrative_section_pointer_has_to_name_its_landing_spot():
    bad = _unanchored_demonstratives(_pointer_corpus())
    assert not bad, (
        "这些「见…节」只在说位置、没写落点（页:行 原文见元组）；改成同句带票号或〈标题〉："
        f"{bad}"
    )


def test_the_demonstrative_rule_bites_on_a_bare_pointer_and_grants_its_two_exemptions():
    """合成语料：五形放过、一形报出，且报出的那一处不因邻句有票号而蒙绿。

    真实语料那条只会红不会绿——把判据改瞎（永远返回 `[]`）它照样过，所以这一条不读语料，
    钉的是检测能力在。
    """
    pages = {
        "a.md": "前面写着 `#9` 的基线。所以见下一节。\n"
                "扫面对照列在这一串里（`见上一节`、`见下一节`），它说的是字样本身。\n"
                "另一处见下一节〈这一局是谁答的〉，标题就是落点。\n"
                "票号写在同一句里（`#123`，见下一节）算落点。\n"
                "同句被硬换行劈开也算：`#125`\n的账见下面那一节。\n",
    }
    bad = _unanchored_demonstratives(pages)
    assert [(d, n) for d, n, _t in bad] == [("a.md", 1)], (
        f"该报的只有第 1 行（邻句票号不给背书），实际报出：{bad}")
    assert [t for _d, _n, t in bad] == ["见下一节"]


# ---------------------------------------------------------------- `类.成员` 点名的悬空一侧（`#158`）

MEMBER_PAIR = re.compile(r"([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
QUALIFIER = re.compile(r"([a-z_][a-z0-9_]*)\.$")
HISTORY_MARK = re.compile(r"曾|已随|删|移回|收回|写下当时")


MEMBER_KINDS = ("def", "ann", "assign", "self")


def _class_member_roster(roots: tuple[str, ...] = ("src", "scripts"),
                         kinds: tuple[str, ...] = MEMBER_KINDS) -> dict[str, set[str]]:
    """每一具类**今天**够得着的成员名，连同继承链。`#158` 的尺。

    比 `#156` 那把宽，因为这里问的不是"有没有人读它"，而是"一句 `类.成员` 点下去落不落得空"。
    四种形状都算落得到，`kinds` 就是这四格的名字（`def`／`ann`=dataclass 字段／`assign`=类级赋值，
    枚举成员住在这一格里／`self`=在方法里现挂的属性），外加从**本包**父类继承来的名字。分成参数而不
    是一把梭，是因为下面那条合成用例要**逐格**问"这个名字是不是只靠这一格活着的"——否则那一格删掉
    也没人报，量不出它有没有读者（`#158` 的电池第一跑就有两格是这样的）。
    少收一种就造出假缺陷：`#156` 那把把字段与 `self.x` 排除在外，于是 `Config` 的 `base_url` 在它眼里
    是悬空的，而它每批都跟着落盘（`src/wolfengine/batch.py` 的 meta 里那一格）——同一批 177 处点名
    在两把尺下"成员不在"的格数差出 80 处（00:39:33Z 现测：窄尺 97，宽尺 17）。
    同趟按格数今天有多少处点名是靠这一格才落得到的：`def` 78、`ann` 42、`assign` 28、`self` 0。
    末那一格今天没有真语料读者，它由合成用例里那条只靠 `self.x` 活着的名钉着，落点在
    `test_the_dangling_member_rule_bites_on_a_never_existing_class_and_grants_history` 的前提那一组。

    `scripts/` 也收，是因为散文会写 `calibrate.某具名` 那一形，只数 src 会让它落进噪声。
    限界：跨包继承（第三方 base）看不见——文档点的是我们自己的成员，而第三方那一层的类名不在这份
    名册里，整条会被跳过（见 `_dangling_member_cites` 的噪声两支）。
    """
    trees: dict[str, ast.Module] = {}
    for root in roots:
        for f in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            trees[str(f)] = ast.parse(f.read_text(encoding="utf-8"))

    members: dict[str, set[str]] = {}
    parents: dict[str, list[str]] = {}
    for tree in trees.values():
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            own = members.setdefault(cls.name, set())
            for node in cls.body:
                if "def" in kinds and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    own.add(node.name)
                elif "ann" in kinds and isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name):
                        own.add(node.target.id)
                elif "assign" in kinds and isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            own.add(t.id)
                        elif isinstance(t, ast.Tuple):
                            own.update(e.id for e in t.elts if isinstance(e, ast.Name))
            parents.setdefault(cls.name, []).extend(
                b.id if isinstance(b, ast.Name) else
                (b.attr if isinstance(b, ast.Attribute) else "") for b in cls.bases)

    def own_write(tree) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for node in ast.walk(cls):
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) \
                        and isinstance(node.value, ast.Name) and node.value.id == "self":
                    out.setdefault(cls.name, set()).add(node.attr)
        return out

    if "self" in kinds:
        for tree in trees.values():
            for cls, names in own_write(tree).items():
                members.setdefault(cls, set()).update(names)

    def closed(cls: str, seen: frozenset[str]) -> set[str]:
        out = set(members.get(cls, set()))
        for p in parents.get(cls, []):
            if p in members and p not in seen:
                out |= closed(p, seen | {p})
        return out

    return {cls: closed(cls, frozenset({cls})) for cls in members}


def _engine_module_classes(roots: tuple[str, ...] = ("src", "scripts"),
                           imports: bool = True) -> dict[str, set[str]]:
    """每个本包模块**够得着**的类名：模块体里定义的，加上从别处 import 进来的大写名字。

    收 import 是因为散文写的是"从那个模块看到的名字"，只数定义会把真话判成假话——这一格今天真语料
    里 0 处点名靠它（00:48:35Z 现测：带前缀的点名共 14 处，其中类住在别处的 0 处），所以它由合成用例
    里那条前缀点名钉着，落点在
    `test_the_dangling_member_rule_bites_on_a_never_existing_class_and_grants_history` 的前提那一组。
    小写的绑定（`import cli`、`as np`）后面接不出类名，不收。
    限界：两份 `__init__.py` 共用一个键（按模块名建键，而 Python 里模块名本来就该唯一），
    本仓库那两具的类名册互不重叠，所以这一格今天不改变任何判定。
    """
    out: dict[str, set[str]] = {}
    for root in roots:
        for f in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            names = out.setdefault(f.stem, set())
            for node in ast.parse(f.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.ClassDef):
                    names.add(node.name)
                elif imports and isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        bound = (a.asname or a.name).split(".")[-1]
                        if bound[:1].isupper():
                            names.add(bound)
    return out


# 树上已经没有、语料里还在**不带模块前缀**点名它的引擎类。带前缀的那一形（`state.PublicState`）由
# `_engine_module_classes` 当场查得出那个模块里没有这具类，所以这份名单只管没前缀的这一支。
# 每一格由下面那条守卫管着：不许又回到树上，也不许在语料里再没人点名。
DEAD_ENGINE_CLASSES = {"PublicState"}


def _dangling_member_cites(corpus: dict[str, str], roster: dict[str, set[str]],
                           modules: dict[str, set[str]]) -> tuple[list, dict[str, int]]:
    """语料里点空了的 `类.成员`，连同这一族五种落点的计数。

    返回 `(悬空处, 分档读数)`。悬空的那一处只有在**它自己那句话**带着历史标记时才放过——判据不许
    要求"零悬空"，归档的全部意义就是"我们删了 X"，它管的是"这句读起来像不像在说今天"。

    分档读数放在返回值里而不是留给临时脚本，是因为这一族的总数会被文档复述：`#156` 那一节写过
    "反引点到的成员名共 55 处"，而那一趟的脚本没留读者，这个数今天复现不出来。四个档各是一件事，
    混成一个数就会像那次一样对不上账。

    形状取"整条坐在反引号里"的那一形（`BACKTICK` 已经界定了跨度，跨度内部再找 `类.成员`），
    于是 `rules.NightResolution.peace` 这种**带模块前缀**的点名也扫得到——这一支不是可有可无的加宽：
    本片第一跑抓到的两处假话（README 的⛔清单与归档里那一处）点的是 `rules` 里一具从来没有存在过
    的类，名字抄错了（真名是 `NightResolution`，`peace` 就住在它上），只看末两段那一档就会把它
    当成噪声放过。三种落空分得开，是因为处置不一样：
      * 类在、成员不在 → 这一条成员点名悬空；
      * 前缀是我们的模块、那个模块里没有这具类，或类整个不在树上但在 `DEAD_ENGINE_CLASSES` 里 →
        **整条都悬空**（不查成员名，因为这类点名无论点的是哪一个成员都同样是历史）；
      * 前缀不是我们的模块（`httpx.Response`、测试里的局部变量 `cal.CONF`），或者没有前缀而类名
        两边都不认识（`README.A`、只住在 tests 里的替身 `Chorus.Scripted`）→ 跳过，这两档的规模
        由读数里的 `not_ours` 与 `noise` 两格说给读者看，不靠猜。
    """
    out = []
    census = {"resolved": 0, "member_gone": 0, "class_gone": 0, "not_ours": 0, "noise": 0}
    for path, text in sorted(corpus.items()):
        lines = text.splitlines()
        for no, line in enumerate(lines, 1):
            for span in BACKTICK.finditer(line):
                for pair in MEMBER_PAIR.finditer(span.group(1)):
                    cls, member = pair.group(1), pair.group(2)
                    q = QUALIFIER.search(span.group(1)[:pair.start()])
                    if q:
                        mod = q.group(1)
                        if mod not in modules:
                            verdict = "not_ours"
                        elif cls not in modules[mod]:
                            verdict = "class_gone"
                        else:
                            verdict = "resolved" if member in roster.get(cls, set()) \
                                else "member_gone"
                    elif cls in roster:
                        verdict = "resolved" if member in roster[cls] else "member_gone"
                    elif cls in DEAD_ENGINE_CLASSES:
                        verdict = "class_gone"
                    else:
                        verdict = "noise"
                    census[verdict] += 1
                    if verdict in ("resolved", "not_ours", "noise"):
                        continue
                    block = lines[max(0, no - 3):no + 2]
                    sentence = _cited_sentence(block, no - 1 - max(0, no - 3),
                                               span.start() + pair.start())
                    if not HISTORY_MARK.search(sentence):
                        out.append((path, no, f"{cls}.{member}", sentence.strip()))
    return out, census


def test_a_dangling_class_member_mention_says_out_loud_that_it_is_history():
    """`类.成员` 点空了可以，但同一句里得说清那是历史，不是今天。`#158`。

    第一跑（00:32:28Z，判据已经是带模块前缀的这一版）报 8 处，其中两处是**假话**：README 的⛔清单
    与归档里那一处都点 `rules` 里一具从来没有存在过的类，真名字是 `NightResolution`。
    其余六处不是假话，是句子少了一个"这是历史"的记号，还有一处是把占位符写成了 ASCII 代码串
    （一个大写字母挂在枚举类后面那一形，文件页眉本来就写着不许）。处置全在句子上，判据一行没动。

    收口那一趟（00:39:33Z）的五档读数：160 处落在今树上、17 处成员没了但同句带了记号（按类分是
    `GameState` 7、`Percept` 4、`GameResult` 2，其余四类各 1）、1 处整类没了（`#159` 那具投影类，
    句子写着"已随"）、1 处前缀不是我们的模块、105 处噪声。这一格留在代码里而不是只进归档，是因为
    上面那句"第一跑报 8 处"只有拿同趟读数才能核——`#156` 那句"共 55 处"就是没留读数才对不上账。
    """
    bad, census = _dangling_member_cites(_line_cite_corpus(), _class_member_roster(),
                                         _engine_module_classes())
    assert not bad, (
        "这些 `类.成员` 点的东西树上已经没有，而同一句里没有历史标记，读起来像在说今天："
        f"{bad}\n同趟分档读数：{census}")


def test_the_dead_class_list_names_classes_that_are_gone_and_still_spoken_of():
    """豁免名单两头都要钉住：不许已经回到树上，也不许没人再点名它。`#158`。

    这条是 `#142` 那一族（"给豁免钉形状"）在这一片的样子：一格豁免一旦失去理由，它就从"放过历史"
    变成"放过假话"，而没人会回来删它。两头各一次：回来了就摘掉，没人提了就删掉。
    点名要求带成员的那一形（`PublicState.` 后面接东西），因为这条名单只管得了这个形状。
    """
    roster = _class_member_roster()
    corpus = _line_cite_corpus()
    back = sorted(c for c in DEAD_ENGINE_CLASSES if c in roster)
    assert not back, f"这些类又回到树上了，把它们从名单里摘掉：{back}"
    quiet = sorted(c for c in DEAD_ENGINE_CLASSES
                   if not any(re.search(rf"`[^`]*\b{c}\.", t) for t in corpus.values()))
    assert not quiet, f"这些类没人再按 `类.成员` 点名了，名单里的这一格是死豁免：{quiet}"


def test_the_dangling_member_rule_bites_on_a_never_existing_class_and_grants_history():
    """合成语料：该报的三形各报一次，该放过的五形各放过，且邻句的历史标记不给背书。

    真语料那条只会红不会绿——把判据改瞎（永远返回 `[]`）它照样过，所以这一条不读语料，
    钉的是检测能力在。形状一律运行时拼：本文件也在扫描面里，写死一个字面量就等于在本片
    自己的语料里种一处悬空点名（`#140` 撞过一次）。

    末尾那两行是**正控制**，钉的是名册里两格今天没有真语料读者的加宽（`self.x` 写入、模块名册收
    import 进来的类名）：00:48:35Z 现测两格在真语料上都是 0 处依赖，电池第一跑对这两刀都是 0 红，
    也就是"删了也没人报"。有了这两行，把 `MEMBER_KINDS` 去掉那一格、或把 `_engine_module_classes`
    的 `imports` 改成默认关，这一条就会红——两格从此有名字可对。
    """
    roster, modules = _class_member_roster(), _engine_module_classes()
    live_cls = "Config"
    live_member = sorted(m for m in roster[live_cls] if not m.startswith("_"))[0]
    ghost_cls, ghost_member = "NightResult", "peace"
    gone_member = "public_state"
    self_cls, self_member = "HumanActor", "console"
    imp_mod, imp_cls = "cli", "EventLog"
    imp_member = sorted(m for m in roster[imp_cls] if not m.startswith("_"))[0]
    assert ghost_cls not in roster and ghost_cls not in modules["rules"]
    assert live_member in roster[live_cls] and gone_member not in roster["GameState"]
    assert self_member in roster[self_cls] and self_member not in _class_member_roster(
        kinds=("def", "ann", "assign"))[self_cls]
    assert imp_cls in modules[imp_mod] and imp_cls not in _engine_module_classes(
        imports=False)[imp_mod]
    dotted = lambda c, m: f"{c}.{m}"            # noqa: E731  拼形状，不留字面量
    pages = {
        "a.md": "".join([
            f"`{dotted(live_cls, live_member)}` 天天落盘。\n",
            f"`{dotted('GameState', gone_member)}` 渲染那一屏。\n",
            f"`{dotted('rules', dotted(ghost_cls, ghost_member))}` 算出来之后没人读。\n",
            f"`{dotted('httpx.Client', 'request')}` 那一层不归本仓库管。\n",
            f"上面那句里 `{dotted('Chorus', 'Scripted')}` 是测试替身，不在这棵树里。\n",
            f"已随 `#159` 删掉的是 `{dotted('PublicState', 'as_dict')}`。\n",
            f"邻句写着删过别的。`{dotted('GameState', gone_member)}` 渲染那一屏。\n",
            f"`{dotted(self_cls, self_member)}` 是真人那一屏的把手。\n",
            f"`{dotted(imp_mod, dotted(imp_cls, imp_member))}` 记的是整局事件。\n"]),
    }
    bad, census = _dangling_member_cites(pages, roster, modules)
    assert [(p, no, name) for p, no, name, _s in bad] == [
        ("a.md", 2, dotted("GameState", gone_member)),
        ("a.md", 3, dotted(ghost_cls, ghost_member)),
        ("a.md", 7, dotted("GameState", gone_member))], f"实际报出：{bad}"
    assert census == {"resolved": 3, "member_gone": 2, "class_gone": 2,
                      "not_ours": 1, "noise": 1}, f"分档读数对不上：{census}；各处：{bad}"


# ------------------------------------ 从手册摘走的每一行都要逐字躺在归档里（#171）

MANUAL_CUT_ANCHOR = "2b57a58"          # #172 收尾那一版：README〈测试〉一节还没被摘过一行
TEST_SECTION = "## 测试"


def _section_of(text: str, heading: str) -> list[str]:
    """切出一节：整行等于 heading 起，到下一个同级 `## ` 标题前止（不含两者）。"""
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        raise AssertionError(f"手册里没有整行等于 {heading!r} 的标题，扫的是空气") from None
    body: list[str] = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        body.append(ln)
    return body


def _mask_counts(line: str) -> str:
    """把这一行里**被 `#44`/`#61` 认成计数主张的那些数字**换成 `N`，其余字符一个不动。

    形状直接借那三条已有判据（`MINIMAL`/`BARE`/`COLLECTED`），不写第四份计数正则：闸门不认的
    数字（`24 条自报文本`、`98 条` 那种后面还接得上词的）在这里也不许抹，否则"抹掉数字再比"会
    比计数闸门本身更宽。
    """
    def rep(m: re.Match) -> str:
        return re.sub(r"\d+", "N", m.group(0))

    for pat in (MINIMAL, BARE, COLLECTED):
        line = pat.sub(rep, line)
    return line


def _registered(ln: str, archive_lines: list[str], by_mask: dict[str, list[int]],
                anchor: str) -> bool:
    """这一行在归档里算不算在册：整行逐字相等，或者**只有计数数字被重数过**且锚点名在附近。

    锚的窗口取"这一行往上共三行"——和 `_collected_claims` 给一句 `跑起来 N 个用例` 找模块名的
    窗口同一个形状。它要装得下归档那一族的排版：说明行、围栏起行、被登记的那一行。
    这里没有"这一行没有计数主张就先返回 False"那一记：抹不动数字的行，它的 mask 就是它自己，
    能在 by_mask 里配上的归档行只能是逐字相等的那一行，而那种情况第一记已经放行了
    （本片的 K8 删掉那一记后名册一字未变，是它证明的）。
    """
    if ln in archive_lines:
        return True
    return any(anchor in "\n".join(archive_lines[max(0, i - 2):i + 1])
               for i in by_mask.get(_mask_counts(ln), ()))


def _departures(old_text: str, now_lines: list[str], archive_lines: list[str],
                heading: str = TEST_SECTION,
                anchor: str = MANUAL_CUT_ANCHOR) -> tuple[list[str], list[str]]:
    """(消失且未在册的行, 消失的行) —— 名册由 git 那头的原文算，测试里不重抄一份被搬走的散文。

    口径：逐行整行相等才算"还在"。改一个字就算离开（旧句子是当年的话，要留档才谈得上核对），
    搬到手册外任何地方都算还在（这一条只管"摘了没登记"，不管"挪了个位置"）；空行不进名册。
    唯一的例外是 `_mask_counts` 那一族数字：见上一段的理由。归档那一侧的重数索引一次算完，
    否则每消失一行都要把整本归档重扫一遍。
    """
    gone = [ln for ln in _section_of(old_text, heading) if ln.strip() and ln not in now_lines]
    by_mask: dict[str, list[int]] = {}
    for i, a in enumerate(archive_lines):
        by_mask.setdefault(_mask_counts(a), []).append(i)
    return [ln for ln in gone if not _registered(ln, archive_lines, by_mask, anchor)], gone


def test_a_line_that_left_the_manual_is_registered_verbatim_in_the_archive():
    """`#135` 那条"先逐字登记才有资格摘"从此有断言读它：摘走而没登记的每一行都会报出来。

    动因是本轮数 README〈测试〉一节时现出来的形状：那一节 228 行（去掉空行 188 行）里坐着 D/K/A
    三段的逐片叙事，而"搬走的东西一字未改"这几句一直只是散文——`#143` 那四张表、`#163` 那 22 行、
    `#168` 那 13 处判决句，每一次都靠写盘脚本自觉，脚本改一半或有人手改 README 删一段，没有任何东西会红。
    这一条不点名搬了哪些句子（那是第二份账，会和归档各自演化），它去问锚点 `2b57a58`
    （08:49:01Z，#172 收尾那一跑之后）的 README：那一节里如今不在 README 中的每一行，
    都要能在 `docs/iterations.md` 里逐字找到。
    """
    shown = subprocess.run(["git", "show", f"{MANUAL_CUT_ANCHOR}:README.md"], cwd=ROOT,
                           capture_output=True, text=True)
    assert shown.returncode == 0, f"锚点 {MANUAL_CUT_ANCHOR} 上读不到 README：{shown.stderr.strip()[:120]}"
    now = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    archive = (ROOT / "docs" / ARCHIVE).read_text(encoding="utf-8").splitlines()
    missing, gone = _departures(shown.stdout, now, archive)
    assert len(gone) >= 8, (
        f"锚点之后〈测试〉一节一行都没摘（消失 {len(gone)} 行）——名册是空的，这条判据还没活")
    assert not missing, f"这些行从手册里消失了，却没在归档逐字在册：{missing[:3]}"


def test_the_departure_judge_reads_a_reworded_line_as_gone_and_a_moved_one_as_registered():
    """六格各钉一次：还在的、改了一个字的、整行搬去归档的、空行、重数过且点了锚的、重数过没点锚的。

    第二格是这条判据的全部牙：只比"在不在"会放过就地改写，而改写恰恰是让旧句子无声消失的那只手。
    第三格钉反方向——搬走了就算在册，否则判据会把每一次成功的搬运报成缺陷。
    第五、六格管的是这一族里唯一会撞车的形状：摘来的行里带着"`x.py` N 条"这种计数主张，而当年那个
    数在搬运的这一刻已经过期（本轮给这只闸门添了用例），照抄会让 `#44`/`#61` 红在归档里。处置是
    **数字重数、其余逐字**，而重数过的那一行必须由它自己或往上两行之内的某一行点名摘来那一版的
    SHA——不然第五格就成了"数字随便可改"的口子，第六格就是那口子不存在时红的那一格（第六格离锚
    四行，量的正是这个窗口）。
    """
    old = ("## 测试\n"
           "这一行留在原地。\n"
           "这一行的旧说法被改了一个字。\n"
           "这一行整行搬去了归档。\n"
           "`tests/test_demo.py`（70 条、跑起来 70 个用例）重数过。\n"
           "`tests/test_other.py`（70 条、跑起来 70 个用例）重数过。\n"
           "\n"
           "## 下一节\n"
           "隔壁节的行不归这一条管。\n")
    now = ["这一行留在原地。", "这一行的旧说法被改了一个字儿。", "隔壁节的行不归这一条管。"]
    archive = ["这一行整行搬去了归档。", "顺手记一句别的。",
               f"下面这一行的两个数按 {MANUAL_CUT_ANCHOR} 之后的那一版重数过。",
               "```text",
               "`tests/test_demo.py`（71 条、跑起来 71 个用例）重数过。",
               "```", "隔开的一行", "再隔开的一行",
               "`tests/test_other.py`（71 条、跑起来 71 个用例）重数过。"]
    missing, gone = _departures(old, now, archive)
    assert gone == [
        "这一行的旧说法被改了一个字。",
        "这一行整行搬去了归档。",
        "`tests/test_demo.py`（70 条、跑起来 70 个用例）重数过。",
        "`tests/test_other.py`（70 条、跑起来 70 个用例）重数过。"], gone
    assert missing == ["这一行的旧说法被改了一个字。",
                       "`tests/test_other.py`（70 条、跑起来 70 个用例）重数过。"], missing


def test_the_count_mask_blind_spots_only_the_digits_a_rerun_forgets():
    """夹具三向：数字换掉而模块名还在时两行**相等**，模块名换掉时**不等**，别处的数字不许被抹。

    第二向是这条判据的牙：整段换成哨兵会让"将 70 条挂到另一个模块上"也判成在册。
    对照用例调的是 `_mask_counts` 本身，不复算它的形状（`#153`）。
    """
    assert _mask_counts("`tests/test_a.py`（70 条、跑起来 70 个用例）") == \
        _mask_counts("`tests/test_a.py`（71 条、跑起来 71 个用例）")
    assert _mask_counts("`tests/test_a.py`（70 条）") != _mask_counts("`tests/test_b.py`（70 条）")
    assert _mask_counts("`tests/test_a.py` 里有 98 条自报文本") == \
        "`tests/test_a.py` 里有 98 条自报文本"
