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
4. `[project.scripts]` 里每一个 `wolf = "模块:属性"` 的两半都要在树里：模块是某本在册 .py，
   属性是它那一段顶层定义（`#193`）。入口是安装时才解析、并且**装的那一刻烤进 `bin/wolf`** 的，
   所以写错一个字母并不会让所有人都撞见：照着错版本重装出来的那本 `.venv` 里 README 那三条执行证人
   会红，而本机那本早就装好的 venv 里它们照旧绿（现测：错入口 + 旧 venv → 3 passed）。
   这一格读的是 pyproject 自己，不靠谁重装。
5. `[tool.pytest.ini_options].markers` 里注册的每一枚 marker，要在 `tests/` 里真被挂过一次
   （`#228`）。注册句会印进 `pytest --markers`，所以一句"拿 -m 可以 deselect 掉它"而没有任何用例
   戴着的注册，是递给每一个敲的人一条不动任何东西的命令。`network` 就是被这一格抓住之后删掉的。

判定住在 `_undeclared_imports()` / `_unread_dependencies()` / `_missing_from_lock()` /
`_broken_entry_points()` / `_markers_without_users()` 五个纯函数里，
真仓库和夹具共用同一份（`#153` 那条规矩：判据不许在守卫和夹具里各抄一遍）。前四条的包名比对一律先过
`_canonical()` 走 PEP 503 口径——`pydantic-core` 和 `import pydantic_core` 是同一只包，换个分隔符
或大小写不算缺陷，`uv` 自己也不这么认；报出来的仍是文件里写着的原样。第五条不走这张桌子：
marker 名是 Python 标识符，`Network` 与 `network` 在 `--strict-markers` 下是两枚。

限界五条，写的都是这一本明知会放过什么：只看顶层名，所以 `import a.b.c` 里住着的三方子模块
不会被拆开对账；`optional-dependencies` 的每一组都算进"在册"，所以一个只有文档里提到、
从来没人装的第二组 extra 不会被这本报出来；锁那一格只问"有没有同名包"，不问版本区间是否还对得上
——`uv lock --check` 那一步仍然归人（或 CI）跑，本包不假设那把命令在机器上存在；第四条只认冒号后
那一个顶层属性名，所以 `pkg:Class.method` 那一形会被它报成缺陷，真要改成那种写法得先改这一本；
第五条的"挂过"只认 `pytest.mark.x` 那个属性访问，所以 `from pytest import mark` 之后写 `@mark.x`
不算使用者（报错方向是多事那一边），而它读的注册表整段允许为空——空语料那一格开不开火不由它自己
证明，住在夹具那一格加一具还原刀里。
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
NAME_VARIANT = re.compile(r"[-_.]+")


def _canonical(name: str) -> str:
    """包名的 PEP 503 口径：小写，`-`、`_`、`.` 的连续段都算同一个分隔符。

    `uv` 和 PyPI 自己就是这么认名字的，所以三种拼法在锁里、在 pyproject 里、在 import 语句里
    指的是同一只包。这一本只在**比对**时归一，报出来的还是写在那份文件里的原样——人被点名时
    要能对回自己写的那一行。
    """
    return NAME_VARIANT.sub("-", name).lower()


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
    allowed = {_canonical(x) for x in deps | own | set(sys.stdlib_module_names)}
    return sorted(t for t in tops if _canonical(t) not in allowed)


def _unread_dependencies(deps: set[str], tops: set[str], cfg_keys: set[str],
                         test_texts: list[str]) -> list[str]:
    """第二条判据：在册的每一条都要有读者，读者有两种形。"""
    unread: list[str] = []
    imported = {_canonical(t) for t in tops}
    for dep in sorted(deps):
        if _canonical(dep) in imported:
            continue
        evidence = PYTEST_PLUGIN_READERS.get(_canonical(dep))
        if evidence and evidence[0] in cfg_keys and any(evidence[1] in t for t in test_texts):
            continue
        unread.append(dep)
    return unread


def _missing_from_lock(deps: set[str], lock_names: set[str]) -> list[str]:
    """第三条判据：pyproject 点名的每一条要在锁里有同名包。"""
    locked = {_canonical(n) for n in lock_names}
    return sorted(d for d in deps if _canonical(d) not in locked)


def _candidate_paths(module: str) -> tuple[str, ...]:
    """一个点分模块名在册树里可能的落点：src 布局与平铺布局各两种（模块 / 包根）。"""
    slash = module.replace(".", "/")
    return (f"src/{slash}.py", f"{slash}.py", f"src/{slash}/__init__.py", f"{slash}/__init__.py")


def _defines_toplevel(text: str, attr: str) -> bool:
    """属性名是否在这本模块的顶层定义过（def/class/赋值/带注解的赋值）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return attr in names


def _broken_entry_points(scripts: dict[str, str], tracked: set[str],
                         bodies: dict[str, str]) -> list[str]:
    """第四条判据：每个 console script 的目标要写成 `模块:属性`，且两半都在树里。

    只认顶层那一个属性名，所以 `pkg.mod:Class.method` 这一形会被报成缺陷——那是处置变更不是放过，
    真要这么写的人得先改这一本。
    """
    broken: list[str] = []
    for name, target in sorted(scripts.items()):
        module, sep, attr = target.partition(":")
        hit = next((p for p in _candidate_paths(module) if p in tracked), None) if sep else None
        if hit is None or not attr or not _defines_toplevel(bodies.get(hit, ""), attr):
            broken.append(f"{name} -> {target}")
    return broken


def _declared_markers(marker_specs: list[str]) -> set[str]:
    """`markers = ["network: 说明……", "slow: 说明"]`，每一条形如 `name: description`，取冒号左边。

    说明里也可以有冒号（`-m 'not network'` 那句就有），所以只切第一刀。
    """
    return {s.split(":", 1)[0].strip() for s in marker_specs if s.split(":", 1)[0].strip()}


def _markers_without_users(declared: set[str], used: set[str]) -> list[str]:
    """第五条判据：注册过的每一枚 marker，至少要在 `tests/` 里被挂过一次。

    这里**不**走 `_canonical()`：marker 名是 Python 标识符，大小写敏感，`Network` 和 `network`
    在 `--strict-markers` 下就是两枚。
    """
    return sorted(declared - used)


def _marker_uses(bodies: dict[str, str]) -> set[str]:
    """`tests/` 里真挂上去的 marker 名：`@pytest.mark.x` 与 `pytestmark = [pytest.mark.x]` 两种形。

    走 AST 而不是扫文本，理由是这一本的语料包含它自己这一本：文本扫描会让一条夹具字符串替它
    声称的那枚 marker 充当使用者（`#153` 那一形）。代价是只认点分那一形，所以
    `pytest.mark` 别名（`from pytest import mark` 之后写 `@mark.x`）不算使用者——报的仍是多事那一边。
    """
    used: set[str] = set()
    for path, text in bodies.items():
        if not path.startswith("tests/") or not path.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "mark" and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "pytest"):
                used.add(node.attr)
    return used


def _tracked_py_files() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr.strip()[:120]
    files = {f for f in out.stdout.split("\n") if f.endswith(".py")}
    assert len(files) >= 60, f"只数到 {len(files)} 本在册 .py，多半是 `git ls-files` 这步坏了"
    return files


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _real_entry_points() -> tuple[dict[str, str], set[str], dict[str, str]]:
    files = _tracked_py_files()
    scripts = _pyproject()["project"].get("scripts", {})
    assert scripts, "pyproject 里没有 [project.scripts] 这一段，第四条判据没有对象"
    bodies = {f: (ROOT / f).read_text(encoding="utf-8", errors="replace") for f in files}
    return scripts, files, bodies


def _real_inputs() -> tuple[set[str], set[str], set[str], set[str], list[str], set[str]]:
    files = _tracked_py_files()
    bodies = {f: (ROOT / f).read_text(encoding="utf-8", errors="replace") for f in files}

    project = _pyproject()
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


def _real_marker_inputs() -> tuple[set[str], set[str]]:
    """第五条判据的两半：pyproject 里注册的那几张名，和 tests/ 里真挂上去的那几张名。"""
    ini = _pyproject().get("tool", {}).get("pytest", {}).get("ini_options", {})
    declared = _declared_markers(ini.get("markers", []))
    files = _tracked_py_files()
    bodies = {f: (ROOT / f).read_text(encoding="utf-8", errors="replace")
              for f in files if f.startswith("tests/")}
    return declared, _marker_uses(bodies)


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


def test_declared_names_match_regardless_of_spelling_variants():
    """包名按 PEP 503 的口径比，不比字面：`-`、`_`、`.` 和大小写都不是缺陷的证据。

    三条判据第一版都拿字符串直接比，于是同一只包换个拼法就同时是"没点名""没人读""锁落后"——
    而 `uv` 自己认为这两个名字是同一个包。这一族的报错方向是多事，多事的闸门会让人学着忽略它，
    所以先把三形各钉一格。
    """
    assert _undeclared_imports({"pydantic-core"}, {"pydantic_core"}, set()) == []
    assert _unread_dependencies({"HTTPX"}, {"httpx"}, set(), []) == []
    assert _missing_from_lock({"pytest_asyncio"}, {"pytest-asyncio"}) == []


def test_every_console_script_target_resolves_in_the_tracked_tree():
    """`#193` 第四条：`[project.scripts]` 指的那个 `模块:属性` 要在在册的树里真存在。

    现测为空：`wolf = "wolfengine.cli:main"`，`src/wolfengine/cli.py` 在册且顶层定义着 `main`。
    写错成 `wolfengine.clie:main` 或 `wolfengine.cli:run` 之后会不会有人撞见，取决于他手上那本
    `.venv` 是什么时候装的：重装过的会撞见（README 那三条执行证人在那种树上一起红，红是
    `ModuleNotFoundError`），没重装的一律绿——那三条走的是 `bin/wolf`，而那一行是装的时候烤进去的。
    这一格不靠重装：它读 pyproject 那一句自己，所以"改了声明而没重装、也没敲那条命令"这一形
    第一次有人看着。它是 `#191` 那一族"公开克隆才是现场"的入口脚本档。
    """
    scripts, tracked, bodies = _real_entry_points()
    broken = _broken_entry_points(scripts, tracked, bodies)
    assert not broken, f"这些 console script 指到树里不存在的东西：{broken}"


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


def test_a_console_script_is_only_fine_when_both_halves_of_the_target_exist():
    """夹具：`模块:属性` 那一形的四种走法，只有全对的那一种不报。

    这一形值得单独钉，因为它的两种失败在装出来之前都不响：模块名拼错和属性名拼错都会被
    `uv sync` 原样接受（入口是安装时才解析的），只有真去敲那条命令才看得见——而没有人敲。
    包根那一种（`src/<pkg>/__init__.py` 顶层定义的属性）是放行面的一部分：`packages = ["src/wolfengine"]`
    这个布局下包根也是合法目标，把它判成缺陷会误报。
    """
    tracked = {"src/wolfengine/cli.py", "src/wolfengine/__init__.py", "other.py"}
    bodies = {
        "src/wolfengine/cli.py": "def main():\n    pass\n",
        "src/wolfengine/__init__.py": "ROOT_ATTR = 1\n",
        "other.py": "def main():\n    pass\n",
    }
    assert _broken_entry_points({"wolf": "wolfengine.cli:main"}, tracked, bodies) == []
    assert _broken_entry_points({"wolf": "wolfengine:ROOT_ATTR"}, tracked, bodies) == []
    assert _broken_entry_points({"wolf": "other:main"}, tracked, bodies) == []
    assert _broken_entry_points({"wolf": "wolfengine.clie:main"}, tracked, bodies) == [
        "wolf -> wolfengine.clie:main"]
    assert _broken_entry_points({"wolf": "wolfengine.cli:run"}, tracked, bodies) == [
        "wolf -> wolfengine.cli:run"]
    assert _broken_entry_points({"wolf": "wolfengine.cli"}, tracked, bodies) == ["wolf -> wolfengine.cli"]
    assert _broken_entry_points({}, tracked, bodies) == []


def test_every_declared_pytest_marker_has_a_test_wearing_it():
    """`#228` 第五条：注册过的每一枚 marker，至少要在 `tests/` 里被挂过一次。

    开火时读数 1 枚：`network`。那句注册自己写着"拿 -m 'not network' 可以 deselect"，而这条 deselect
    今天一条都不掉（反过来 `-m network` 会把整本都 deselect 掉）——`pytest --markers` 把这条没有对象的
    命令印给每一个敲它看的人。真正挡端点的是
    `tests/conftest.py` 里那具 `block_the_endpoint`：它先把 `Config().api_key_env` 指着的那个
    环境变量删掉，再按名字 patch 掉 transport 与 socket 两层的动词，所以这套用例里挂不出一条
    该挂 `network` 的用例——那些真发字节的用例发的是 127.0.0.1 上自己起的桩。

    这一格的语料允许为空（注册删干净之后它就是空的），这跟第四条不一样：那一条读的是安装时才
    解析的入口，不可能没有对象。所以它开不开火不由自己证明，证明住在
    `test_a_marker_declaration_needs_a_wearer_and_a_string_is_not_one` 与一具"把那句注册原样塞回去"
    的还原刀里。
    """
    declared, used = _real_marker_inputs()
    dead = _markers_without_users(declared, used)
    assert not dead, f"这些 marker 注册了而 tests/ 里一次都没挂过：{dead}"


def test_a_marker_declaration_needs_a_wearer_and_a_string_is_not_one():
    """夹具：第五条判据的形状，外加"提到 ≠ 挂上"那一格。

    `pytestmark` 那一形必须算使用者，否则整文件级的 marker 会被成片误报。最后一格钉的是这本守卫
    自己的生存方式：它的语料包含 tests/ 也包含它自己，所以一句写在字符串里的 marker 不能替那枚
    marker 充当使用者——要算，得是代码里那个属性访问。别名 `from pytest import mark` 之后写
    `@mark.x` 不在放行面里，那是这一本明知会放过的一形（放过＝多事的方向反了，先记着）。
    """
    assert _declared_markers(
        ["network: hits the real endpoint (deselect with -m 'not network')", "slow: 慢的那几条"]
    ) == {"network", "slow"}
    assert _declared_markers(["", "   "]) == set()

    bodies = {
        "tests/test_worn.py": "@pytest.mark.decoy_one\ndef test_a():\n    assert 1\n",
        "tests/test_marked.py": ("import pytest\n\npytestmark = [pytest.mark.decoy_two]\n"
                                 "\ndef test_b():\n    assert 1\n"),
        "tests/test_prose.py": ('"""we do not wear pytest.mark.decoy_three here"""\n'
                                "import pytest\n\n\ndef test_c():\n"
                                "    assert pytest.mark.decoy_four is not None\n"),
        "src/mypkg/not_a_test.py": "@pytest.mark.decoy_five\ndef f():\n    return 1\n",
    }
    used = _marker_uses(bodies)
    assert used == {"decoy_one", "decoy_two", "decoy_four"}, sorted(used)
    assert _markers_without_users({"decoy_three", "decoy_five"}, used) == ["decoy_five", "decoy_three"]
    assert _markers_without_users({"decoy_one"}, used) == []
    assert _markers_without_users(set(), used) == []
    assert _markers_without_users({"Decoy_One"}, used) == ["Decoy_One"]
