"""依赖方向的守卫：纯核不碰 I/O，编排层不认识模型。

plan §11 把这条写成"出现即测试失败"的一条 grep。它存在的理由不是风格：
`rules|belief|compress|schema|legality|assemble|info|state` 一旦能读环境、开文件，
"零 LLM 单测整局"就再也不成立——测试会在某台没导出密钥的机器上变红，或者更糟，
静默地依赖网络。`phases.py` 那条同理：它要是 import 了 `llm`，真人座位就没法和模型
坐在同一张桌子上（plan §15）。

用 grep 而不是 import 检查，是因为要防的是"源码里出现了这条路"，不是"这条路被走到了"。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "wolfengine"

# plan §11 的名单，外加 roles.py（它自称"纯声明"，那就让它证明自己）。
# events.py 刻意不在名单里：它就是那个写文件的模块。
PURE_CORE = ("rules.py", "belief.py", "compress.py", "schema.py", "legality.py",
             "assemble.py", "info.py", "state.py", "roles.py")

IO_EDGE = {
    "network": re.compile(r"\bhttpx\b|\burllib\b|\bsocket\b|\baiohttp\b|\brequests\b"),
    "environment": re.compile(r"os\.environ|os\.getenv|getenv\("),
    "filesystem": re.compile(r"\bopen\(|\.open\(|Path\(|shutil\.|\bsubprocess\b"),
    "subprocess": re.compile(r"\bsubprocess\b|\bos\.system\b"),
}

# 纯核可以 import 的兄弟模块：同为纯核的那些。出现下面任何一个就是方向反了。
IMPURE = ("llm", "transport", "actors", "agent", "phases", "game", "cli",
          "batch", "report", "render_live", "render_html", "metrics")

LOCAL_IMPORT = re.compile(r"^\s*from\s+\.(\w+)|^\s*from\s+\.\s+import\s+(\w+)|^\s*import\s+\.(\w+)",
                          re.MULTILINE)


def _sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(SRC.glob("*.py"))}


@pytest.mark.parametrize("name", PURE_CORE)
def test_pure_core_has_no_io_edge(name):
    text = _sources()[name]
    # 文档字符串里提到"os.environ"是解释为什么不这么做，不是这么做。剥掉再查。
    stripped = re.sub(r'""".*?"""|\'\'\'.*?\'\'\'', "", text, flags=re.DOTALL)
    stripped = "\n".join(line.split("#")[0] for line in stripped.splitlines())
    hits = {kind: pat.findall(stripped) for kind, pat in IO_EDGE.items()}
    bad = {k: v for k, v in hits.items() if v}
    assert not bad, f"{name} reached for I/O: {bad}"


@pytest.mark.parametrize("name", PURE_CORE)
def test_pure_core_does_not_import_an_io_module(name):
    text = _sources()[name]
    deps = {a or b or c for a, b, c in LOCAL_IMPORT.findall(text)}
    assert not (deps & set(IMPURE)), f"{name} imports {sorted(deps & set(IMPURE))} — the " \
                                     f"dependency direction in plan §3 is one-way"


def test_the_orchestration_layer_does_not_know_what_a_model_is():
    """`phases.py` 里不出现任何 LlmActor 专有假设（plan §11 M1 判据原文）。"""
    text = _sources()["phases.py"]
    assert not re.search(r"from \.(llm|transport) import|import \.(llm|transport)", text), \
        "phases.py imports the model layer; a human seat would no longer be addressable by it"
    assert "LlmActor" not in text, "phases.py names a specific actor class"
    assert "completion_tokens" not in text and "temperature" not in text, \
        "phases.py assumes per-call model economics; that belongs to agent/llm"


def test_only_the_declared_boundaries_read_the_environment():
    allowed = {"config.py", "transport.py", "cli.py"}
    offenders = {n for n, t in _sources().items()
                 if re.search(r"os\.environ|os\.getenv", re.sub(r'""".*?"""', "", t, flags=re.DOTALL))
                 and n not in allowed}
    assert not offenders, f"{sorted(offenders)} read the environment; only {sorted(allowed)} may"


def test_key_lookup_happens_at_call_time_and_is_not_cached():
    """`require_key()` 必须是函数内 import：模块级 `import os` + 读取会让一份密钥常驻。"""
    text = _sources()["config.py"]
    body = text[text.index("def require_key"):]
    body = body.split("\n    def ")[0]
    assert "import os" in body, \
        "require_key should import os inside the function so nothing caches the value"
    assert "self._key" not in text and "_api_key" not in text, "Config caches a key"


def test_the_token_budget_enumeration_is_only_ever_read_through_one_function():
    """*"哪些阶段按发言计价"* 只许住在 `config.py`；第二个读者只能问函数，不能重抄名单。

    值是可比对的，出处不能：`actors.py` 若把 `Phase in (...) ? speech : action` 再内联一遍，
    当下两侧完全等价，没有任何一条断言会红——直到有人改了 `SPEECH_PHASES`，于是发出去的和
    普查印出来的再次分家，而这正是 `token_budget_for` 被抽出来的原因。所以这一条不看值，
    看字面量出现在哪几个文件里（和上面几条守卫同一手法）。
    """
    offenders = {}
    for name, text in _sources().items():
        if name == "config.py":
            continue
        stripped = re.sub(r'""".*?"""|\'\'\'.*?\'\'\'', "", text, flags=re.DOTALL)
        stripped = "\n".join(line.split("#")[0] for line in stripped.splitlines())
        hits = sorted({f for f in ("max_tokens_speech", "max_tokens_action", "SPEECH_PHASES")
                       if re.search(rf"\b{f}\b", stripped)})
        if hits:
            offenders[name] = hits
    assert not offenders, (
        f"这些模块自己重抄了计价名单，改一处不会带动另一处：{offenders}")


def test_the_two_refusal_sites_do_not_keep_their_own_copy_of_the_lists():
    """`--set` 那一半和 `compare` 这一半必须问 `config.py` 要名单，不能各自抄一份。

    独占的读数在顶层那一格：R11（`/tmp/mut65c.py`，10:05:07Z→10:07:22Z）只把 `enable_sheriff`
    内联进 `report.py`，742 条里**只有这一条红**——`test_batch_paired.py` 里那条对账守卫
    （`test_the_no_reader_list_and_the_source_agree_in_both_directions`）是绿的，因为它扫的名单
    只有点号路径（`flatten(Config())` 里带 `.` 的那些），一个没有点号的名字被抄走它看不见。
    内联**完整**名单（含 `"regions.b0"`）时就有第二个读者了：R9/R10
    （`/tmp/mut65.py`，09:59:44Z）里那条对账守卫把 `batch.py` 中的字符串常量当成读取点，
    "零读者名单"与源码对不上，于是它也红——那两只的预期写成"行为套件不动"被打掉过一次。
    两条守卫的分工就是这两半：对账那条管点号路径，这一条管顶层那一格有没有被抄走。
    手法与上面那条计价守卫一致：不看值，看字段名字出现在哪几个文件里，
    docstring 与 `#` 注释剥掉，所以 `batch.py` 里那句 "`enable_sheriff` is refused because…"
    不算抄。名单只列**拿得出手**的名字：`b0` 和 `warn` 这种两三个字母的格子的**读取点**将来会长在
    `assemble.py` 里（`#63`），按子串扫会把该写的代码挡在外面。
    """
    names = ("enable_sheriff", "c_persona", "c_belief", "c_private", "c_task",
             "force_compact")
    src = _sources()
    offenders = {}
    for name in ("batch.py", "report.py"):
        stripped = re.sub(r'""".*?"""|\'\'\'.*?\'\'\'', "", src[name], flags=re.DOTALL)
        stripped = "\n".join(line.split("#")[0] for line in stripped.splitlines())
        hits = sorted(f for f in names if re.search(rf"\b{f}\b", stripped))
        if hits:
            offenders[name] = hits
    assert not offenders, (
        f"这两个拒绝点自己重抄了名单，改 `config.py` 不会带动它们：{offenders}")


def test_pure_core_modules_all_exist():
    """A guard whose target was deleted is a guard that passes. This one fails instead."""
    present = set(_sources())
    missing = set(PURE_CORE) | {"phases.py", "events.py", "agent.py", "actors.py", "game.py"}
    assert not (missing - present), f"guarded module(s) vanished: {sorted(missing - present)}"
