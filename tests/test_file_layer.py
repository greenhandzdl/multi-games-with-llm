"""`#187`：**整本文件**这一维的死名探测。

`#81`→`#172` 那一族数过函数、方法、类、导入、模块级常量、类体字段，每一层都在**名字**那一维上。
这一本管的是比名字大一档的那一形：把某本 helper 唯一的 importer 删掉，那本 helper 会原地变成死文件，
而套件不会红——`tests/` 下非 `test_*.py` 的本子 pytest 从不收集，也没有任何闸门读过它们。

四条谓词（"被点到"在这一维上不是一条规则，理由挂在每条后面）：

1. `src/**/*.py`：**被另一本在册 .py 的 import 语句点名**，AST 取——函数体里缩进的延迟 import 也算。
2. `tests/*.py` 里非 `test_`、非 `conftest.py` 的：同上，被另一本在册 .py 的 import 语句点名。
3. `tests/test_*.py` 与 `tests/conftest.py`：**自己声明至少一条可被收集的东西**（一条 `def test_`，
   conftest 里是一条 hook 或 fixture）。这一层不靠点名背书——现测 42 本测试文件每一本的 basename
   其实都在别处被拼出来过（`#186` 那 7 本是扫描器形状 bug 报的假孤儿），但"被文档提过"不是它们活着的
   原因，被收集才是；一本被收集却什么都不声明的文件才是这一条要抓的。
4. 其余在册文件（`__init__.py`、fixtures、根级、`scripts/`、docs）：basename 出现在另一本在册正文里。

量过又收回的那一格：入口那本 `cli` 本来另开了一条豁免——`pyproject.toml` 的 `[project.scripts]`
（`wolf = "wolfengine.cli:main"`）。两形对比之下这条豁免是**零读者**的：把名册整条摘掉，孤儿名单
一字不变（现测空），因为 tests 里有一句 `from wolfengine.cli import main` 先把它认领了。按"新限定
条件零读者就删分支而不是留断言"那条规矩，这一本没有这条豁免，代价写在下面第三条 bullet 里。

限界两条，写的都是这一本明知会放过什么：`src/**/__init__.py` 走第四条（包被 import 时那本由 import
系统构造执行，名字不是它的读者，所以一本空 init 只要别处拼出过它的文件名就放得过）；第四条认的是
**拼出来**，所以一本只在文档里被提过一次的脚本不会被这本报成缺陷。
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DECLARS_A_CASE = re.compile(r"^(?:async )?def test_", re.M)
DECLARS_A_PYTEST_HOOK = re.compile(r"^(?:async )?def pytest_|@pytest\.fixture", re.M)


def _tracked_bodies() -> dict[str, str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr.strip()[:120]
    return {f: (ROOT / f).read_text(encoding="utf-8", errors="replace")
            for f in out.stdout.split("\n") if f}


def _imported_names(bodies: dict[str, str]) -> set[str]:
    """在册 .py 里被 import 语句点名过的每一段名字：`from a.b import c` 收 `a`、`b`，不收 `c`。

    走 AST 不走行首正则：`#187` 第一版把 import 锚在行首，于是函数体里那处缩进的
    `from wolfengine.batch import _cell` 没被认出来，四本 src 模块连 `tests/conftest.py` 一起被报成
    死文件——同一棵树、同一个形状假设，和 `#186` 那处"任一命名片段"是同一笔账。
    纯相对导入（`from . import x`）没有 `module`，走 alias 那一支。
    """
    names: set[str] = set()
    for path, text in bodies.items():
        if not path.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.update(p for p in a.name.split(".") if p)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.update(p for p in node.module.split(".") if p)
                elif node.level:
                    names.update(a.name for a in node.names)
    return names


def _pointed_elsewhere(path: str, name: str, bodies: dict[str, str]) -> bool:
    return any(name in text for p, text in bodies.items() if p != path)


def _dead_files(bodies: dict[str, str]) -> list[str]:
    """这一本的全部判据：四条谓词各自认哪一形，都收在这一个函数里，守卫和夹具共用它。

    `#153` 那条规矩在这一本同样成立：判定不许在守卫和夹具里各抄一遍。`#186` 量到的第一版 bug
    也住在这里——它写成"任一命名片段 0 命中就算孤儿"，于是走 `import declared_kinds` 那一形的
    **真** importer 认不出 `declared_kinds.py`，两本 helper 被报成死文件。这一本因此一条谓词只认
    一个名字：src 与 tests 那两层只认 import 语句里的位置，不看带后缀的那一形。
    """
    imported = _imported_names(bodies)
    dead: list[str] = []
    for path, text in bodies.items():
        name = path.rsplit("/", 1)[-1]
        if not name.endswith(".py") or name == "__init__.py":
            ok = _pointed_elsewhere(path, name, bodies)
        elif name == "conftest.py":
            ok = bool(DECLARS_A_PYTEST_HOOK.search(text))
        elif path.startswith("tests/") and name.startswith("test_"):
            ok = bool(DECLARS_A_CASE.search(text))
        elif path.startswith("src/") or path.startswith("tests/"):
            ok = name[:-3] in imported
        else:
            ok = _pointed_elsewhere(path, name, bodies)
        if not ok:
            dead.append(path)
    return sorted(dead)


def test_no_tracked_file_is_left_without_a_claimant():
    """`#187`：在册的每一本都要有一个说得出名字的认领者，现测孤儿为空。

    要的不是"每个文件都被文档点名"：src 那层的认领者是 import 它的模块，测试辅助那层是 import 它的
    用例，`test_*.py` 那层是它自己体内那条 `def test_`，其余是别处把它的文件名拼出来的那一本。摘掉
    任何一本的认领者，这一条会点名报出那一本。
    """
    bodies = _tracked_bodies()
    assert len(bodies) >= 80, f"只数到 {len(bodies)} 本在册文件，多半是 `git ls-files` 这步坏了"
    dead = _dead_files(bodies)
    assert not dead, f"这些在册文件没有任何认领者：{dead}"


def test_the_four_predicates_each_need_their_own_shape():
    """夹具：十一本合成在册文件，该报的三本各钉一层，该放行的三形一次都不报。

    `src/app/loose.py` 钉 `#186` 那一形：它只被 `import loose`（不带 `.py`）点名，第一版扫描器因为
    "带后缀那一形没人拼"就会把它报成孤儿，这一本必须放行。`src/app/lazy.py` 钉 AST 那一形：它唯一的
    importer 缩在函数体里，行首正则认不出（`#187` 第一次跑就因此误报过四本真模块）。
    `src/app/main.py` 的名字靠一句带点号的 `from app.main import run` 才被认出来。
    `tests/test_empty.py` 钉第三条（被收集却一条都不声明），`tests/sub/conftest.py` 钉第三条的
    conftest 那一支（pytest 会执行它，但里面既无 hook 也无 fixture），`src/app/dead.py` 钉
    第一、二条的反向。
    """
    bodies = {
        "src/app/helper.py": "X = 1\n",
        "src/app/loose.py": "X = 1\n",
        "src/app/lazy.py": "X = 1\n",
        "src/app/main.py": "def go():\n    import lazy\n    import loose\n    return lazy\n",
        "src/app/dead.py": "X = 1\n",
        "tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef any_fixture():\n    return 1\n",
        "tests/sub/conftest.py": "X = 1\n",
        "tests/test_ok.py": 'import helper\nimport support\nfrom app.main import run\n\n\n'
                           'def test_x(helper, run, support):\n'
                           '    assert (helper, run, support, "data/thing.json")\n',
        "tests/test_empty.py": "def helper():\n    return 1\n",
        "tests/support.py": "X = 1\n",
        "data/thing.json": "{}\n",
    }
    assert _dead_files(bodies) == [
        "src/app/dead.py", "tests/sub/conftest.py", "tests/test_empty.py"]


def test_no_case_reads_an_untracked_path_under_the_scratch_dir():
    """用例读的每一份 sidecar 都必须在册——`#187` 管"本文件有没有人认领"，这一格管"文件里的读取"。

    动因：报告证人 `test_the_committed_report_is_still_what_the_code_renders` 拿
    `data/calibration.json` 重渲染 `docs/calibration.md`，而那份 sidecar 是
    `scripts/calibrate.py` 体检真端点的产物、一直没入库。本机有它，`git clone --depth 1`
    出来的仓库没有，于是这位证人在克隆里是 FileNotFoundError——同一件事在
    `tests/test_m3_gate.py` 那一条里是被当成规矩写下来的（真日志不入库，所以用例自己造现场）。
    比较的前缀运行时拼出来（`"dat" + "a/"`）：这一条扫的正是 tests/，写成整串就会指着
    自己这段 docstring 报红（`#153` 那一课）。
    """
    prefix = "dat" + "a/"
    reads = []
    for f in sorted((ROOT / "tests").glob("*.py")):
        for m in re.finditer(r'ROOT\s*/\s*"([^"]+)"', f.read_text(encoding="utf-8")):
            if m.group(1).startswith(prefix):
                reads.append((f.name, m.group(1)))
    assert reads, "一处 sidecar 读取都没扫到，多半是扫法坏了"
    untracked = sorted(
        {(f, p) for f, p in reads
         if subprocess.run(["git", "ls-files", "--error-unmatch", p], cwd=ROOT,
                           capture_output=True).returncode != 0})
    assert not untracked, f"这些用例读的 sidecar 不在册，克隆里必红：{untracked}"
