"""The on-disk payload contract: what `Kind`'s table promises vs what actually lands.

`Kind`'s docstring says payload shapes are documented there "rather than in a design doc,
because a shape that drifts from the code is exactly the failure above wearing a new hat".
Until #114 nothing enforced that sentence, and the drift was not hypothetical: three of the
double writers this repo has deleted (`vote_result.summary`, `vote_result.abstainers`,
`death.cause_zh`) were payload keys the table never named. A byte-identical second writer is
invisible to every behaviour probe — the chronicle renders fine either way — so the only
thing that can catch the class is a structural assertion over the whole key set.

This module is that assertion. It reads the table out of the source (the table is comments,
so reflection cannot see it) and diffs it against events written by the *current* code, not
against old logs: history legitimately contains keys that no longer exist.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import sys
from pathlib import Path

import pytest

from wolfengine import metrics
from wolfengine.events import KINDS, EventLog, Kind

sys.path.insert(0, str(Path(__file__).parent))
import test_golden_game as G  # noqa: E402  (the authored transcript, reused as the fixture)

TABLE = Path("src/wolfengine/events.py")


def declared_shapes() -> dict[str, set[str]]:
    """The `{"key": ...}` set attached to each `Kind` line, continuation comments included."""
    block = TABLE.read_text(encoding="utf-8").split("class Kind:", 1)[1].split("\nKINDS", 1)[0]
    out: dict[str, set[str]] = {}
    current = None
    for line in block.splitlines():
        head = re.search(r'^\s*[A-Z_]+\s*=\s*"([a-z_]+)"', line)
        if head:
            current = head.group(1)
            out[current] = set()
        if current is not None:
            out[current] |= set(re.findall(r'"([a-z_]+)"\s*:', line))
    return out


def undeclared_keys(events) -> dict[str, set[str]]:
    """Per kind, payload keys that the table does not name. Empty means the table is honest."""
    decl = declared_shapes()
    gaps: dict[str, set[str]] = {}
    for e in events:
        extra = set(e.payload) - decl.get(e.kind, set())
        if extra:
            gaps.setdefault(e.kind, set()).update(extra)
    return gaps


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    """One full mock game, read back off disk — the code's own output, not a hand-built dict."""
    out = tmp_path_factory.mktemp("payloadshape")
    res = G.play_authored(out)
    events, _meta = EventLog.read_records(res.path)
    return events


def test_the_table_names_every_key_that_lands_on_disk(written):
    """A key nobody declared is a key nobody is responsible for.

    That is how `summary` came to sit next to `tally` and keep a sentence alive that the
    engine had already started recomputing: the table never named it, so the table could not
    contradict it.
    """
    gaps = undeclared_keys(written)
    assert not gaps, f"落盘的键没写进 events.Kind 的形状表：{ {k: sorted(v) for k, v in gaps.items()} }"


def test_the_gate_itself_has_a_corpus(written):
    """A structural guard over an empty corpus passes silently, so the corpus is pinned first.

    13 of the 14 kinds come out of one mock game; `compaction` needs a squeezed B-region
    budget and is checked where that setup already lives (test_live_path.py, which calls
    `undeclared_keys` on its own markers).
    """
    seen = {e.kind for e in written}
    assert seen >= KINDS - {Kind.COMPACTION}, sorted(KINDS - {Kind.COMPACTION} - seen)
    assert all(declared_shapes()[k] for k in seen), "形状表里有一格是空的，上面的断言会假绿"


def test_the_table_names_no_key_the_engine_stopped_writing(written):
    """The other direction: a declared key nobody writes is the vocabulary lie in reverse.

    `Kind.COMPACTION` lived in the table with a renderer and a counter and no emitter for a
    whole milestone, which read as "this game never folded". Same rule here for payloads — a
    key the table promises and the code no longer produces means either the table or the
    writer drifted, and one of the two is a lie that `test_no_secrets` cannot see.
    """
    decl = declared_shapes()
    written_keys: dict[str, set[str]] = {}
    for e in written:
        written_keys.setdefault(e.kind, set()).update(e.payload)
    stale = {k: sorted(decl[k] - written_keys.get(k, set())) for k in written_keys
             if decl[k] - written_keys.get(k, set())}
    assert not stale, f"形状表声明了、这一局却没写：{stale}"


def test_the_chosen_act_is_the_only_record_of_which_potion_was_spent(written):
    """`schema.Action.potion` exists to catch a model that says `act=save, potion=poison`;
    `legality` refuses that answer by name. Copying the field into the payload after the
    check passed records the same fact twice — and the two copies of 7/7 stored potions on
    the real logs were byte-identical, which is what `legality` had already agreed to, so no
    reader could ever tell which one moved.
    """
    offenders = [(e.seq, e.payload.get("act"), e.payload["potion"])
                 for e in written if "potion" in e.payload]
    assert not offenders, f"potion 是 act 的第二支笔：{offenders}"


SRC = Path("src/wolfengine")

# Keys the table declares and no product code reads. Pinned to this exact set in both
# directions: a fourth name appearing reddens the assertion below, and so does leaving a
# name here after someone finally wired a reader to it.
UNREAD = {
    "action": "night_action 的 `action` 是法官问的那一格（`save_or_poison`），与 `act`（她答的）"
              "不是一件事，#114 因此两格都留。但产物链上没人读它（实测 01:14:53Z：src 里 0 个"
              "Load 点），唯一的读点是 `test_golden_game.py:337` 那行的 `payload` 拿它挑狼人那一格。",
}


def src_load_sites(key: str) -> list[str]:
    """AST 里真读了这个键的地方；写侧（`payload[k] = …`、字典字面量的键）不算。

    刻意用 AST 而不是 grep：这一轮里 grep 骗了我两次，方向还一致——都是把"没人读"说得太容易。
    `\\["k"\\]` 漏掉 `p.get('k')`（单引号），而 `k` 出现在 docstring 里又会被当成读者。两条都在
    下面的控制用例里钉住了。
    """
    hits: list[str] = []
    for f in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                    and node.slice.value == key and isinstance(node.ctx, ast.Load):
                hits.append(f"{f.name}:{node.lineno}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("get", "pop") and node.args \
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value == key:
                hits.append(f"{f.name}:{node.lineno}")
    return hits


def test_every_declared_key_is_read_by_somebody_or_named_here(written):
    """形状表里每格都该有人读，否则它只是"作者记得写过"的凭据——#88 那一族。

    `compaction.window` 与 `folded_days` 上一轮还是零 Load 点：三局真日志里确实折到了 9 条、15 条
    （实测 01:15:55Z），而产物链上没有任何地方说得出这件事，`audit` 那一块只数标记的**个数**。
    豁免表把剩下的那一格说清楚，而不是让"没人读"重新变成一句只能靠人 grep 一遍才看得见的散文。
    """
    declared = {k for keys in declared_shapes().values() for k in keys}
    unread = {k for k in declared if not src_load_sites(k)}
    assert unread == set(UNREAD), f"新出现的无人读取的键 / 已有人读却还挂着的：{sorted(unread)} vs " \
                                  f"{sorted(UNREAD)}"
    assert all(reason.strip() for reason in UNREAD.values()), "豁免要写理由，不然它只是另一个键"


def test_the_reader_gate_sees_code_that_a_grep_misses_and_refuses_prose_that_a_grep_invents():
    """两条控制用例，一条证明这把尺子不瞎，一条证明它不轻信。

    * `compress.py` 读折叠内容写的是 `p.get('summary', '')`——单引号，且在 f-string 里。
      双引号版的 grep 报"零读者"，AST 报得出来。
    * `_night_text` 的 docstring 里有一句 `payload["action"]`，字面量和读法一模一样。
      grep 会把它当成读者（我这轮真的这么被骗过一次），AST 只看代码，所以 `action` 是零。
    """
    assert any(h.startswith("compress.py") for h in src_load_sites("summary")), \
        "单引号 + f-string 里的 .get 读不到，说明这把尺子又瞎了"
    assert not src_load_sites("action"), \
        "docstring 里的那句 `payload[\"action\"]` 被当成了读者"


def test_the_two_fallback_copies_are_compared_where_a_reader_can_see_it(written):
    """`fallback` 有两支笔、四个读者，今天没有一处对过账（`#119`）。

    落盘的两份：`payload.meta.fallback`（`metrics.m3_gate_pressure` 读）与 `result.fallback`
    （直播和复盘 HTML 的〔引擎代打〕标记读）。实测 03:04:01Z 扫语料：`data/**/*.jsonl` 11 份里
    464 条带 `payload.meta` 的决策记录，取 `rec["payload"]["meta"]["fallback"]` 与
    `rec["result"]["fallback"]` 两处，两键每条都在且逐条相等。"目前没坏"不等于"有人看着"，
    所以判据住在 `metrics`、读者是 `audit` 那一格。三段断言各自要挡的假绿：一致也要数得出分母、
    坏一处要点得出来、少一份拷贝的不许算进分母（否则 `divergent: 0` 会把"没看过"说成"没坏"）。
    """
    clean = metrics.fallback_copy_check(written)
    assert clean["divergent"] == 0, clean
    assert clean["n_compared"] == len(metrics.decisions(written)) > 0, clean

    first = metrics.decisions(written)[0]
    flipped = dataclasses.replace(first, result={**first.result,
                                                 "fallback": 0 if first.result["fallback"] else 1})
    one = metrics.fallback_copy_check([*(flipped if e is first else e for e in written)])
    assert one == {"n_compared": clean["n_compared"], "divergent": 1}, one

    no_copy = dataclasses.replace(first, payload={**first.payload, "meta": {"rung": 0}})
    assert metrics.decisions([no_copy]) == [no_copy], "这条反例得先真的是一个决策记录"
    blind = metrics.fallback_copy_check([*(no_copy if e is first else e for e in written)])
    assert blind == {"n_compared": clean["n_compared"] - 1, "divergent": 0}, blind
