r"""文档里每张 Markdown 表的**形状**也是主张：表头几列，每一条数据行就得几列。

这一族以前只靠"写文档的那个脚本自己数一遍"（`#127` 那一片的写盘脚本里就有一份），于是它有
一份没有读者的规则：脚本改了、或者有人直接在 README 里手改一行表格，没有任何东西会红。而形状
坏了的表在渲染时**不报错**——多出来的格子被丢掉、少掉的格子留空，读的人看到的是一张"写着对
的话的错表"，正是这一族最便宜也最安静的腐烂。

判据只有两条，都是 GitHub 渲染 Markdown 表时真正用的那两条：

* 表头行与分隔行（`|---|---|`）成对出现才算一张表，两者的列数必须相等；
* 每一条数据行的列数必须等于表头的列数。

围栏里的东西一张都不算：文档里 ` ``` ` 包着的常常是 CLI 输出或一张坏了的示例表，那种行不是
表，拿它报红就是在冤枉一句正确的话。列按未转义的 `|` 切，格子里写着 `\|` 不算分列。

MUTATION NOTE：这一片没有新的生产代码——闸门整个住在测试文件里（和 `test_doc_citations.py`
同一套取法）。四具"只改闸门不改文档"的刀（漏数一列、把围栏算进来、分隔行不查、认不出转义）
各自该弄红哪一条用例是下面各条 docstring 里点名的判据。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]
SEP = re.compile(r"^\|[\s:|-]+\|$")
SPLIT = re.compile(r"(?<!\\)\|")          # 分列只数**没有**反斜杠挡着的竖线
FENCE = "```"


def _corpus() -> dict[str, str]:
    return {f.name: f.read_text(encoding="utf-8") for f in DOCS}


def _cells(line: str) -> list[str]:
    """一行切成格，切法和渲染器一样：首尾那对竖线是边框不是格子，所以先脱掉再切。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return SPLIT.split(s)


def _is_row(line: str) -> bool:
    return bool(SPLIT.search(line))


def _iter_tables(md: str):
    """逐张表吐出 `(表头行号, 表头格数, 分隔行号, 分隔行格数, [(数据行号, 格数)])`。

    一张表的开头判据是"这一行的**下一行**长得像分隔行"，而不是"这一行以竖线开头"：文档里两种
    表头写法都有，只认后一种会整批漏掉。表在第一个不像数据行的地方结束（Markdown 也那么结束
    它），围栏里的东西一张都不算——文档会把坏形状包在 ` ``` ` 里给人看长什么样。
    """
    lines = md.splitlines()
    fence, i = False, 0
    while i < len(lines):
        if lines[i].lstrip().startswith(FENCE):
            fence = not fence
            i += 1
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if fence or not _is_row(lines[i]) or not SEP.match(nxt):
            i += 1
            continue
        rows: list[tuple[int, int]] = []
        j = i + 2
        while j < len(lines) and _is_row(lines[j]) and not lines[j].lstrip().startswith(FENCE):
            rows.append((j + 1, len(_cells(lines[j]))))
            j += 1
        yield i + 1, len(_cells(lines[i])), i + 2, len(_cells(nxt)), rows
        i = j


def _bad_tables(bodies: dict[str, str]) -> list[tuple[str, int, str]]:
    """The check itself, over caller-supplied text: `(文件, 行号, 为什么)`.

    为什么不写成一句"这张表不对"：一处形状坏了的表在渲染时不报错，只把多出来的格子丢掉，所以
    报告必须落在**那一行**上，否则改的人要在一张二十行的表里自己数。
    """
    out = []
    for doc, md in bodies.items():
        for _, n, sep_no, sep_cols, rows in _iter_tables(md):
            if sep_cols != n:
                out.append((doc, sep_no, f"分隔行 {sep_cols} 格，表头 {n} 格"))
            out += [(doc, no, f"这一行 {k} 格，表头 {n} 格") for no, k in rows if k != n]
    return sorted(out)


def _scanned_sizes(bodies: dict[str, str]) -> tuple[int, int]:
    """(扫到的表数, 扫到的数据行数)——只给"守卫真的在扫"那一条用。"""
    tables = [t for md in bodies.values() for t in _iter_tables(md)]
    return len(tables), sum(len(t[4]) for t in tables)


def test_every_table_in_the_docs_has_a_uniform_column_count():
    """真语料一张一张数：表头几列，每条数据行就得几列。"""
    bad = _bad_tables(_corpus())
    assert not bad, f"文档里的表格形状坏了：{bad}"


def test_the_shape_scanner_fires_on_a_row_with_one_cell_too_many_and_only_there():
    """两向都要证：故意写坏的一行必须报，写对的那张必须干净——否则"没报"和"扫不到"分不开。

    把 `_bad_tables` 改成永远返回 `[]`，上面那条真语料用例照样绿，所以检测能力得在合成文本里
    钉。报的三样东西要齐：哪个文件、第几行、实际几列对表头几列——少一样就要人自己去找。
    """
    probe = ("表头 | 二 | 三\n"
             "|---|---|---|\n"
             "| a | b | c |\n"
             "| a | b | c | d |\n")
    bad = _bad_tables({"probe.md": probe})
    assert len(bad) == 1, bad
    doc, no, why = bad[0]
    assert (doc, no) == ("probe.md", 4), bad
    assert "4" in why and "3" in why, f"没说清是几列对几列：{why}"


def test_a_missing_cell_in_a_data_row_is_reported_too():
    """多一格会渲染成"最后一格被丢掉"，少一格会渲染成空白——两向都得报。"""
    bad = _bad_tables({"probe.md": "h | i | j\n|---|---|---|\n| a | b |\n"})
    assert len(bad) == 1 and bad[0][1] == 3, bad


def test_the_separator_row_has_to_have_as_many_columns_as_the_header():
    """分隔行比表头少一列时整张表**不成为表**：渲染器退回成两段散文，而读的人不知道。

    诚实说一句：这一格今天在真语料上换不来任何新读数（90 张表的分隔行列数都等于表头，改前的
    写盘脚本也没查过这一格），它的牙长在构造的夹具上——和 `#105`、`#107` 那几条一样。
    """
    bad = _bad_tables({"probe.md": "h | i | j\n|---|---|\n| a | b | c |\n"})
    assert len(bad) == 1 and bad[0][1] == 2, bad
    assert "分隔行" in bad[0][2], bad[0]


def test_pipe_lines_inside_a_fence_are_not_tables():
    """围栏里那张"坏表"是给人看长什么样的，不是主张：把它算进来，守卫就在文档没写错时报红。

    文档里真有这一族——讲某个闸门怎么工作时贴一段坏形状的例子。所以这一条钉两格：围栏里的
    坏表不许报，围栏外紧跟的那张真表仍然要报（围栏开关不能被同一段里的两行 ` ``` ` 弄乱）。
    """
    probe = ("坏的样子（围栏里，不算表）：\n\n"
             "```\n"
             "h | i | j\n"
             "|---|---|\n"
             "| a | b | c | d |\n"
             "```\n"
             "\n"
             "围栏外这张是真表：\n"
             "h | i | j\n"
             "|---|---|---|\n"
             "| a | b | c | d |\n")
    bad = _bad_tables({"probe.md": probe})
    assert [b[1] for b in bad] == [12], f"围栏里的行被当成表了，或围栏外那张漏了：{bad}"


def test_an_escaped_pipe_inside_a_cell_does_not_split_it():
    """格子里要写一个竖线只能写 `\\|`，那是 Markdown 的转义，不是分列。

    真实语料里有这一族：讲渲染行的格式时格子里就带一个竖线。把它算成两列会让守卫在正确的表上
    报红，而一条常报红的守卫三次之内就会被关掉。
    """
    good = "h | i\n|---|---|\n| a \\| b | c |\n"
    assert _bad_tables({"probe.md": good}) == [], _bad_tables({"probe.md": good})
    assert len(_bad_tables({"probe.md": good.replace("a \\| b", "a | b")})) == 1


def test_a_row_of_pipes_without_a_separator_row_is_not_a_table():
    """没有分隔行的几根竖线只是散文里带了个竖线，不许被当成一张表来数格子。"""
    probe = ("两种口径：`每轮均值 | 合并分母`。\n"
             "这一行 | 也带竖线 | 而已\n"
             "换行之后就是普通句子了。\n")
    assert _bad_tables({"probe.md": probe}) == []


def test_the_shape_scanner_actually_scans_the_docs():
    """防"守卫因为没扫到东西而永远绿"：范围、表数、数据行数都得有规模。

    `assert not bad` 在一张表都没扫到时同样成立——那正是 `git init` 之前 `git grep` 返回 0 的那
    类假绿。这里断言的是扫到的**规模**，不绑某一张表，所以它不会随文档增删而失效。
    """
    tables, rows = _scanned_sizes(_corpus())
    assert tables >= 90, f"只扫到 {tables} 张表，多半是围栏或正则坏了"
    assert rows >= 600, f"只扫到 {rows} 条数据行，多半是表的边界判定坏了"
