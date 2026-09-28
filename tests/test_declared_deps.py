"""`#191`：声明层进死名探测——`pyproject.toml` 和代码、和 `uv.lock` 两两对账。

前面那几层数的都是名字：函数 `#81`、导入 `#83`、类 `#85`、入参 `#86`、模块级常量 `#160`、
类体字段 `#172`、整本文件 `#187`、用例读的 sidecar `#189`。这一本管比"文件"大一档的那一形：
**声明**。三份东西各自说话，两两之间目前没有东西在读：

1. 代码里 import 的每一个三方顶层名，必须在 `pyproject.toml` 的 `dependencies` 或
   `optional-dependencies` 里点名过。没点名也能在作者机器上跑——`.venv` 里恰好装着它——
   而照着 README〈安装〉那两句装出来的机器上没有。这一形是 `#189` 那句"公开克隆才是现场"的依赖版。
2. 每一条在册依赖要有读者：要么 AST 里被 import 过，要么它是 pytest 插件、被 pytest 自己的配置读走。
   `pytest-asyncio` 谁都不 import，它读的是 `asyncio_mode` 那一句，所以它的读者住在配置里——
   按"只在 import 语句里找读者"来判，104 条 `async def test_` 会被判成没有背书人。
3. 每一条在册依赖要在 `uv.lock` 里有同名包。`uv sync` 照的是锁不是 pyproject：加了依赖忘了重锁，
   README 那条安装命令在新机器上直接报错，而本机什么都绿。

判定住在 `_undeclared_imports()` / `_unread_dependencies()` / `_missing_from_lock()` 三个纯函数里，
真仓库和夹具共用同一份（`#153` 那条规矩：判据不许在守卫和夹具里各抄一遍）。

限界三条，写的都是这一本明知会放过什么：只看顶层名，所以 `import a.b.c` 里住着的三方子模块
不会被拆开对账；`optional-dependencies` 的每一组都算进"在册"，所以一个只有文档里提到、
从来没人装的第二组 extra 不会被这本报出来；锁那一格只问"有没有同名包"，不问版本区间是否还对得上
——`uv lock --check` 那一步仍然归人（或 CI）跑，本包不假设那把命令在机器上存在。
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 插件型依赖不靠 import 被读走，靠宿主配置文件里的那一句。这张表是这一本唯一手写的知识：
# 以后接来一个新插件而忘了登记，那条依赖会被报成"没有读者"——报错方向是多事，不是放过。
PYTEST_PLUGIN_READERS = {
    "pytest-asyncio": ("asyncio_mode", "async def test_"),
}

SPEC_CUT = re.compile(r"[<>=!~;\[\s]")


def _dep_name(spec: str) -> str:
    """`"httpx>=0.27"`、`"rich<14,>=13.7"`、`"foo ; extra == 'x'"` 都取回包名那一段。"""
    return SPEC_CUT.split(spec, maxsplit=1)[0].strip()


def _own_roots(files: set[str]) -> set[str]:
    """这个仓库自己长出来的模块名：src 下的包根，和 tests/、scripts/ 下一本本可被 import 的模块。

    tests/ 那一层要放行是因为 pytest 的 prepend 导入模式会把 `tests/` 插进 `sys.path`，
    于是 `from conftest import …`、`import declared_kinds` 都是合法读者而不是三方包。第一版扫描器
    只放行了包根，把这几本 helper 连同 `test_*` 之间的互相 import 一起报成了未声明依赖——
    和 `#186`、`#187` 那两回一样，先怀疑扫描器的形状假设。
    """
    roots: set[str] = set()
    for f in files:
        parts = Path(f).parts
        if parts[0] == "src" and len(parts) > 2:
            roots.add(parts[1])
        elif parts[0] in ("tests", "scripts") and parts[-1].endswith(".py"):
            roots.add(parts[-1][:-3])
    return roots


def _imported_tops(bodies: dict[str, str]) -> set[str]:
    """在册 .py 里每一条 import 语句的顶层名（相对导入没有顶层名，不收）。"""
    tops: set[str] = set()
    for path, text in bodies.items():
        if not path.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                tops.add(node.module.split(".")[0])
    return tops


def _undeclared_imports(deps: set[str], tops: set[str], own: set[str]) -> list[str]:
    """第一条判据：三方顶层名（不是标准库、不是本仓库自己的模块）必须在册。"""
    return sorted(t for t in tops if t not in deps | own | set(sys.stdlib_module_names))


def _unread_dependencies(deps: set[str], tops: set[str], cfg_keys: set[str],
                         test_texts: list[str]) -> list[str]:
    """第二条判据：在册的每一条都要有读者，读者有两种形。"""
    unread: list[str] = []
    for dep in sorted(deps):
        if dep.replace("-", "_") in tops:
            continue
        evidence = PYTEST_PLUGIN_READERS.get(dep)
        if evidence and evidence[0] in cfg_keys and any(evidence[1] in t for t in test_texts):
            continue
        unread.append(dep)
    return unread


def _missing_from_lock(deps: set[str], lock_names: set[str]) -> list[str]:
    """第三条判据：pyproject 点名的每一条要在锁里有同名包。"""
    return sorted(d for d in deps if d not in lock_names)


def _real_inputs() -> tuple[set[str], set[str], set[str], set[str], list[str], set[str]]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr.strip()[:120]
    files = {f for f in out.stdout.split("\n") if f.endswith(".py")}
    assert len(files) >= 60, f"只数到 {len(files)} 本在册 .py，多半是 `git ls-files` 这步坏了"
    bodies = {f: (ROOT / f).read_text(encoding="utf-8", errors="replace") for f in files}

    with (ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)
    assert "dependencies" in project["project"], "pyproject 里没有 dependencies 这一段，这一本无从对账"
    deps = {_dep_name(s) for s in project["project"]["dependencies"]}
    for group in project["project"].get("optional-dependencies", {}).values():
        deps |= {_dep_name(s) for s in group}
    cfg_keys = set(project.get("tool", {}).get("pytest", {}).get("ini_options", {}))

    with (ROOT / "uv.lock").open("rb") as fh:
        lock = tomllib.load(fh)
    lock_names = {p["name"] for p in lock.get("package", [])}
    assert lock_names, "uv.lock 里一个包都没数到，这一本的第三条判据没有对象"

    tops = _imported_tops(bodies)
    test_texts = [t for f, t in bodies.items() if f.startswith("tests/")]
    return deps, tops, _own_roots(files), cfg_keys, test_texts, lock_names


def test_no_third_party_import_lives_outside_the_manifest():
    """`#191` 第一条：本机绿不等于新机器绿——被 import 的每一个三方名字都得点名。

    现测为空。要红只需要一句 `import tenacity`：作者的 `.venv` 里如果恰好躺着它（作为某个包的
    传递依赖），全套件一路绿灯，而 `uv sync --extra dev` 出来的环境里 import 就炸。
    """
    deps, tops, own, _cfg, _tests, _lock = _real_inputs()
    undeclared = _undeclared_imports(deps, tops, own)
    assert not undeclared, f"这些三方顶层名被 import 了却没在 pyproject 里点名：{undeclared}"


def test_every_declared_dependency_has_a_reader():
    """`#191` 第二条：`dependencies` 里的每一条要么被 import，要么被宿主的配置读走。

    现测为空：`httpx`、`pydantic`、`rich`、`pytest` 都有 import 读者，`pytest-asyncio` 的读者是
    `asyncio_mode` 那一句加 tests 里那 104 条 `async def test_`。删掉一个包（或删掉那一句配置）
    就有一条报出来，方向与 `#146`、`#161` 那两笔"宣传了却没人读"同形。
    """
    deps, tops, _own, cfg, tests, _lock = _real_inputs()
    unread = _unread_dependencies(deps, tops, cfg, tests)
    assert not unread, f"这些依赖在册但说不出谁在读它：{unread}"


def test_every_declared_dependency_is_in_the_lock():
    """`#191` 第三条：`uv sync` 照的是锁，pyproject 加了依赖而锁里没有同名包＝安装命令照抄会炸。

    现测为空。这一格是本机能测到的最外一层：README〈安装〉那一句被 `#136` 那族执行证人跑过，
    但它跑的是作者这台已经装好的机器，锁落后了它也不红。
    """
    deps, _tops, _own, _cfg, _tests, lock_names = _real_inputs()
    stale = _missing_from_lock(deps, lock_names)
    assert not stale, f"这些在册依赖不在 uv.lock 里，锁落后于 pyproject：{stale}"


def test_the_three_predicates_each_need_their_own_shape():
    """夹具：一份合成 manifest 与一本合成代码，三格各自该报的报出来，该放行的一个都不报。

    `tenacity` 钉第一条（import 了没点名）；`orphanlib` 钉第二条（点名了没人 import、也没登记成插件）；
    `unlocked` 钉第三条（在册、有读者、锁里没有）。放行的那几形各有来头：`json` 走标准库，
    `mypkg` 走 src 包根，`support` 走 tests 那层可被 import 的模块名。
    插件那一形用真名字 `pytest-asyncio` 走三遍：配置有那一句、且 tests 里真有异步用例才放行——
    这一形正反都测，因为它是这张表唯一手写的知识，配错或删掉那一句的人都该被点名。
    """
    bodies = {
        "src/mypkg/app.py": "import json\nimport tenacity\nfrom mypkg.other import x\n",
        "tests/test_needs_plugin.py": "import support\n\n\nasync def test_slow():\n    assert 1\n",
        "tests/support.py": "Y = 2\n",
    }
    tops = _imported_tops(bodies)
    own = _own_roots(set(bodies))
    deps = {"tenacity", "orphanlib", "unlocked", "pytest-asyncio"}
    assert _undeclared_imports(deps, tops, own) == [], "点名过的依赖被报成了未点名"
    assert _undeclared_imports(set(), tops, own) == ["tenacity"]

    assert _unread_dependencies(deps, tops, {"asyncio_mode"},
                                ["async def test_slow():\n"]) == ["orphanlib", "unlocked"]
    assert _unread_dependencies({"pytest-asyncio"}, set(), set(),
                                ["async def test_slow():\n"]) == ["pytest-asyncio"]
    assert _unread_dependencies({"pytest-asyncio"}, set(), {"asyncio_mode"},
                                ["def test_sync():\n"]) == ["pytest-asyncio"]

    assert _missing_from_lock(deps, {"tenacity", "orphanlib", "unlocked",
                                     "pytest-asyncio", "some-transitive"}) == []
    assert _missing_from_lock(deps, {"tenacity", "orphanlib", "pytest-asyncio"}) == ["unlocked"]
    assert [_dep_name(s) for s in ["httpx>=0.27", "rich<14,>=13.7", "a ; extra == 'x'", "plain"]] == [
        "httpx", "rich", "a", "plain"]
