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
from wolfengine.events import EventLog, Kind

sys.path.insert(0, str(Path(__file__).parent))
import test_golden_game as G  # noqa: E402  (the authored transcript, reused as the fixture)
from declared_kinds import KINDS  # noqa: E402  (the roster, derived once, test-side since `#160`)

TABLE = Path("src/wolfengine/events.py")


def declared_shapes() -> dict[str, set[str]]:
    """The `{"key": ...}` set attached to each `Kind` line, continuation comments included.

    块尾的边界自 `#160` 起是下一具顶层 dataclass：这一格以前切在 `KINDS` 那一行上，而那一行搬走了。
    切错了不会报错，只会把整份 `events.py` 当成形状表读，所以边界本身要钉一下。
    """
    text = TABLE.read_text(encoding="utf-8").split("class Kind:", 1)[1]
    block = text.split("\n@dataclass", 1)[0]
    assert "class Event" not in block, "块尾边界没切成，形状表读到了 Kind 之外的整份文件"
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


def test_the_citation_list_is_recorded_once(written):
    """引用了哪几条事件编号，落盘的有两支笔：`payload.evidence` 与 `payload.meta.citation_stats`。

    后者是 `legality._check_citations` 算出来的（cited / valid / invented / not_visible /
    malformed / uncited，`agent._write` 原样抄进 meta），前者是同一个 `action` 上再抄一遍。
    实测 11:00:47Z 扫 `data/**/*.jsonl`：553 条带 `evidence` 的记录里，把三份清单各自去重排序后
    `evidence == valid` 553/553、`evidence == cited` 553/553，一条不差——因为它们是同一次
    `check_legality` 的两个出口，不是两个来源。而读者只有一支笔有（`metrics.m4_citation_integrity`
    与 `m5_style_diagnostics`、`batch` 的 uncited 聚合读 meta，11:00:47Z 三处），另一支的读者是
    0 个产品读点（11:00:48Z：唯一的两处 `["evidence"]` 在 schema.py 的 `out` 上，不是 payload）。
    这一格和 `potion` 那一条是同一个判据：第二支笔不许活过这一关。#110/#111/#113 删掉三份双写
    走的也是这条路——删的是**抄的那一份**，被抄的那一份留着并补上读者。
    """
    offenders = [(e.seq, e.payload["evidence"],
                  (e.payload.get("meta") or {}).get("citation_stats", {}).get("valid"))
                 for e in written if "evidence" in e.payload]
    assert not offenders, f"evidence 是 meta.citation_stats 的第二支笔：{offenders[:6]}"


SRC = Path("src/wolfengine")

# 形状表里出现过的键名，全库一份。住在豁免表旁边是因为两边都要它：豁免表按它点名，
# `src_load_sites` 的控制用例要拿它逐个问"这一格到底有没有读者"。
DECLARED_KEYS = frozenset({k for keys in declared_shapes().values() for k in keys})

# "被取键的东西像不像一份 payload"认的就是这三个名字，外加任何链上出现 `payload` 的表达式。
# 认名字是这条判据已知最弱的一层，代价与依据写在下面的控制用例里。
PAYLOAD_BASES = frozenset({"p", "payload", "pl"})


def is_payload(base: str) -> bool:
    return "payload" in base or base in PAYLOAD_BASES

# Keys the table declares and no product code reads. Pinned to this exact set in both
# directions: a fourth name appearing reddens the assertion below, and so does leaving a
# name here after someone finally wired a reader to it.
UNREAD = {
    "action": "night_action 的 `action` 是法官问的那一格（`save_or_poison`），与 `act`（她答的）"
              "不是一件事，#114 因此两格都留。但产物链上没人读它（实测 01:14:53Z：src 里 0 个"
              "Load 点），唯一的读点是 `test_golden_game.py:337` 那行的 `payload` 拿它挑狼人那一格。",
}


def payload_read(node) -> tuple[str, str] | None:
    """这个节点在**读**一个键吗？是则返回 (键名, 被取键那个对象的源码文本)。

    写侧不算：`payload[k] = …` 的 ctx 是 Store，字典字面量里的 `"k": v` 连 Subscript 都不是。
    `.pop` 算读者——它把值取走了，但"取走"不能替一个从未渲染的键作证，所以它和 `.get` 同权。
    """
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
            and isinstance(node.ctx, ast.Load) and isinstance(node.slice.value, str):
        return node.slice.value, ast.unparse(node.value)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in ("get", "pop") and node.args \
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value, ast.unparse(node.func.value)
    return None


def src_load_sites(key: str) -> list[str]:
    """AST 里真读了这个键的地方，且**被取键的东西得是一份 payload**。

    刻意用 AST 而不是 grep：上一轮里 grep 骗了我两次，方向还一致——都是把"没人读"说得太容易。
    `\\["k"\\]` 漏掉 `p.get('k')`（单引号），而 `k` 出现在 docstring 里又会被当成读者。两条都在
    下面的控制用例里钉住了。

    只认"payload 上取这个键"是 `#132` 加的第二层：上一版数的是键名，于是 `schema.py:352` 那句
    `out.get("evidence")`——读的是模型答出来的那个 dict，跟落盘的 payload 没有半点关系——替五个
    kind 的 `evidence` 格子付了账。名字撞对不等于同一格事。
    """
    hits: list[str] = []
    for f in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            found = payload_read(node)
            if found and found[0] == key and is_payload(found[1]):
                hits.append(f"{f.relative_to(SRC.parent)}:{node.lineno}")
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
    """三条控制用例，一条证明这把尺子不瞎，两条证明它不轻信。

    * `compress.py` 读折叠内容写的是 `p.get('summary', '')`——单引号，且在 f-string 里。
      双引号版的 grep 报"零读者"，AST 报得出来。
    * `_night_text` 的 docstring 里有一句 `payload["action"]`，字面量和读法一模一样。
      grep 会把它当成读者（我这轮真的这么被骗过一次），AST 只看代码，所以 `action` 是零。
    * `schema.py:352` 的 `out.get("evidence")` 是真代码、真键名、真 Load——但 `out` 是模型答
      出来的那个 dict，不是落盘的 payload。只数键名的尺子会替 payload 的格子付假账（`#132`）。
    """
    assert any("compress.py:" in h for h in src_load_sites("summary")), \
        "单引号 + f-string 里的 .get 读不到，说明这把尺子又瞎了"
    assert not src_load_sites("action"), \
        "docstring 里的那句 `payload[\"action\"]` 被当成了读者"
    assert not src_load_sites("evidence"), \
        "schema.py 读的是模型答出来的那个 dict，不是 payload，它不该替 payload 的格子付账"


def test_a_read_only_counts_when_what_got_subscripted_is_a_payload():
    """第三条控制的反面：这把尺子不能只是"换个词再 grep 一遍"，它得认得别名。

    `compress.py:71` 写的是 `p, k = e.payload, e.kind`，此后整条渲染链都通过别名 `p` 取键；
    而 `report.py`/`batch.py`/`metrics.py` 里也有 47 个 base 恰为 `p` 的 Load 点（实测 10:59:19Z，
    其中键名落在形状表里的 24 个**全部**在 compress.py）。所以"像 payload"认的是
    `payload` / `p` / `pl` 三个名字加上任何含 `payload` 的链式表达式——认名字而不是认数据流是
    这条判据已知最弱的一格：它今天没有放过任何东西（那 24 处都是真读者），但下一个把 `p`
    用作别的字典、又恰好撞上表里键名的人会被误算。故写在这里，而不是等它坏了再找。
    """
    assert any("compress.py:" in h for h in src_load_sites("teammates")), \
        "别名 `p` 上的真读者被算丢了"
    assert all("schema.py" not in h for key in DECLARED_KEYS for h in src_load_sites(key)), \
        "schema.py 读的是解析出来的答案，不是落盘的 payload"


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
