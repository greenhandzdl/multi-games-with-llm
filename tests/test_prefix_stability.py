"""前缀稳定性：缓存收益的唯一证明，也是"B 区永不改写已写入部分"的可执行版本。

端点实测同一段 10k 前缀 cold 4.28s / warm 1.24s（3.44×）。§6 的整个延迟模型建立在
"A+B 对全体座位字节相同、且随回合单调增长"之上。这个假设破了不会有报错，症状是每局
莫名慢三倍——所以它必须是断言，不是注释。

这里跑一整局 `--mock`（真 `game.play`、真 `agent` 装配、真落盘），再把日志里每次调用的
`request.messages` 读回来查。查的是**磁盘上的字节**，不是内存里的中间量：会发给模型的
才是前缀。

两条曾经*不*成立、由这个文件逼出来的事实（都写进了 `assemble`/`compress` 的注释）：

1. 热窗口上限写死 16 条 ⇒ 公开事件一过 16 条，B 的**开头**每回合挪一格，一局 3 天里
   挪了 33 次 = 每次发言都重新 prefill。改成几何边界 8→12→18→27→40→60→90。
2. 局况卡（B0）放在 B 的最前面，而它一天被重写 2–3 次（夜里报死、投票放逐各一次），
   于是每次重写把它后面的整条编年史冲出缓存。改成卡片放 B 末尾：它自己那 ~40 token
   重新 prefill，前面的保持不动。
"""

from __future__ import annotations

import asyncio
import random
import re

import pytest

from wolfengine import game
from wolfengine.actors import MockActor
from wolfengine.compress import chronicle, estimate_tokens
from wolfengine.config import Config, RegionBudget
from wolfengine.events import EventLog, Kind

SEED = 7
ALL_SEATS = tuple(range(1, 10))
TAG = re.compile(r"^\[e(\d+)\]")
CARD = "== 局况 =="


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    out = tmp_path_factory.mktemp("prefixrun")
    cfg = Config()
    actors = {s: MockActor(s, synthesize=True, rng=random.Random(s * 31 + SEED))
              for s in ALL_SEATS}
    res = asyncio.run(game.play(cfg=cfg, deal_seed=SEED, out_dir=out, actors=actors))
    events, meta = EventLog.read_records(res.path)
    calls = [(e.seq, e.day, e.request.get("messages", []), str(e.payload.get("_idem", "")))
             for e in events if e.request.get("messages")]
    assert len(calls) >= 40, f"a whole game should produce dozens of calls; got {len(calls)}"
    return cfg, res, events, meta, calls


def _regions(messages: list[dict]) -> tuple[str, str, str]:
    sysm = [m["content"] for m in messages if m["role"] == "system"]
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    return (sysm[0] if sysm else "", sysm[1] if len(sysm) > 1 else "", user)


def _split_card(b: str) -> tuple[str, str]:
    """(只往后长的编年史, 允许每回合重写的局况卡)。"""
    head, _, card = b.partition(CARD)
    return head.rstrip("\n"), (CARD + card if card else "")


def test_region_a_is_one_frozen_block_for_the_whole_game(played):
    cfg, _, _, _, calls = played
    a_texts = {_regions(m)[0] for _, _, m, _ in calls}
    assert len(a_texts) == 1, "region A changed mid-game: every cached prefix is now void"
    a = a_texts.pop()
    assert cfg.contract_version in a and cfg.rules_version in a, \
        "A carries no version string, so a change to it would be invisible in the log"


def test_nothing_volatile_precedes_the_chronicle(played):
    """The structural half of the fix: the card must be *after* the last rendered event.

    Asserting the byte order rather than the flush count is what keeps this from silently
    reverting — a future refactor that puts a fresher block first would still pass a
    "grows by appending" test on days 1–2, when nothing has moved yet.
    """
    _, _, _, _, calls = played
    for seq, _, messages, _idem in calls:
        b = _regions(messages)[1]
        chronicle, card = _split_card(b)
        assert card, f"seq={seq}: no status card in region B at all"
        last_tag = max((i for i, ln in enumerate(chronicle.splitlines()) if TAG.match(ln)),
                       default=None)
        assert last_tag is not None, f"seq={seq}: region B has no rendered events"
        assert b.endswith(card), f"seq={seq}: the status card is not the last block of region B"


def test_region_b_is_identical_across_seats_in_the_same_wave(played):
    """The cache header is shared *between* seats, not merely stable per seat.

    The wave that proves it is the ballot: `as_of` pins every voter's world to the instant
    the phase opened, so all nine seats are asked about byte-identical A+B bytes. If they
    were not, §6's batch prefill model (P_batch) is fiction.
    """
    _, _, _, _, calls = played
    by_wave: dict[str, set[tuple[str, str]]] = {}
    for seq, _, messages, idem in calls:
        if not idem.startswith("day_vote"):
            continue
        a, b, _ = _regions(messages)
        by_wave.setdefault(idem, set()).add((a, b))
    assert len(by_wave) >= 2, f"no ballot waves captured: {sorted(by_wave)}"
    for key, group in by_wave.items():
        assert len(group) == 1, \
            f"ballot wave {key} handed {len(group)} different A+B byte sets to its seats"


def test_written_lines_never_change_once_rendered(played):
    """plan §5 规则②：`[eNNN]` 一经写入，其渲染输出永不改变。

    这条最容易被"顺手加个当前时间/第几轮"破坏，而那正好把整个前缀缓存冲掉。
    """
    _, _, _, _, calls = played
    seen: dict[str, str] = {}
    for seq, _, messages, _idem in calls:
        chronicle, _card = _split_card(_regions(messages)[1])
        for ln in chronicle.splitlines():
            if not (m := TAG.match(ln)):
                continue
            tag = f"[e{m.group(1)}]"
            if tag in seen:
                assert seen[tag] == ln, f"{tag} rendered twice with different bytes:\n" \
                                        f"  {seen[tag]!r}\n  {ln!r}"
            seen[tag] = ln
    assert len(seen) > 20, "too few tagged lines — the check above is vacuous"


def test_the_chronicle_only_ever_appends(played):
    """一局之内编年史部分只往后长，从不回头改——缓存唯一可能成立的条件。"""
    _, _, _, _, calls = played
    prev = ""
    for seq, day, messages, _idem in calls:
        chronicle, _card = _split_card(_regions(messages)[1])
        assert chronicle.startswith(prev), \
            f"seq={seq} day={day}: region B rewrote {len(prev)} bytes of history"
        prev = chronicle


def test_no_call_ever_crossed_the_context_ceiling(played):
    cfg, _, _, _, calls = played
    worst = 0
    for seq, _, messages, _idem in calls:
        total = sum(estimate_tokens(m["content"], cfg.tokens) for m in messages)
        worst = max(worst, total)
        assert total <= cfg.tokens.absolute_ceiling, \
            f"seq={seq} sent {total} tokens, over the {cfg.tokens.absolute_ceiling} ceiling"
    assert worst > 500, f"a whole game fitted in {worst} tokens — the fixture is too small " \
                        f"to have exercised the budget"


def test_shrink_never_fired_just_to_fit(played):
    """`shrink>0` means the pre-send estimate was wrong and the overflow lever had to be
    pulled mid-game. Legal, but it must be visible: it changes what a seat sees, so a batch
    that leaned on it is not comparable to one that did not."""
    _, res, _, _, _calls = played
    assert res.shrinks == 0, f"{res.shrinks} shrink events: the budget table no longer fits a game"


def test_the_budget_lever_folds_whole_days_and_stops_at_the_day_floor():
    """预算掐死时折叠的落点：整天整天地折，折到只剩"当天"就收手，宁可 B2 超软预算。

    这条钉的是被换掉的那段逻辑。旧循环是"窗口对折到 4 为止"，在 `b2_cap`  binding 时给出一个
    固定条数的窗口，于是编年史每长一条它就往前挪一格——`test_a_fold_does_not_make_the_window_slide_every_turn`
    实测 15 次改写 / 2 次折叠。新循环按天推进，所以：
    ① `window` 永远不小于 `min_window`（不会把座位折到只剩一行摘要）；
    ② B2 的第一条一定是某一天的开头（折掉的都是整天，B1 的摘要和 B2 的逐条不会重复同一条）；
    ③ 轮数由阶梯窗口内的天数决定，编年史再长也不多付一次冲洗（90 是阶梯顶，400 条仍是 4 次）。
    """
    from wolfengine.compress import plan_fold
    from wolfengine.events import Event, Kind

    def chronicle(n: int):
        return tuple(Event(seq=i, kind=Kind.SPEECH, day=1 + i // 20, phase="day_speech",
                           visibility="all", actor=(i % 9) + 1,
                           payload={"act": "accuse", "target": (i % 7) + 1,
                                    "text": f"{(i % 9) + 1}号说昨晚的动静不对劲。"})
                     for i in range(1, n + 1))

    fold = lambda n: plan_fold(chronicle(n), est=len,                     # noqa: E731
                               start_window=90, min_window=4, b2_cap=1)
    for n in (60, 90, 200, 400):
        plan = fold(n)
        assert plan.window >= 4, f"n={n}: {plan} 折穿了当天，座位只剩摘要可看"
        first_kept = chronicle(n)[n - plan.window]
        assert first_kept.day == max(plan.folded_days, default=0) + 1, \
            f"n={n}: 折掉 {plan.folded_days} 却从第{first_kept.day}天中间开始留，" \
            f"那条既在摘要里又在逐条里"
    assert fold(60) == fold(60) and fold(60).rounds == 2, fold(60)
    assert fold(400).rounds == 4, "编年史再长也不该多付前缀冲洗：阶梯把窗口钉在 90 条"
    # `shrink` 仍然是最后那把刀：它才允许在一天之内下刀，而它是被计数的（`res.shrinks`）。
    squeezed = plan_fold(chronicle(60), est=len, start_window=90,
                         min_window=4, b2_cap=1, shrink=1)
    assert squeezed.window == fold(60).window // 2, f"溢出杠杆没对上：{squeezed} vs {fold(60)}"


# --------------------------------------------------- 预算掐紧时：折叠真的发生，且按天锚定
@pytest.fixture(scope="module")
def squeezed(tmp_path_factory):
    """同一局牌，只把 B2 的预算掐紧，让"预算 binding"这个分支真的被走到。

    默认预算下整局 mock 的编年史装得进 B2，于是上面六条断言跑的其实是"从不折叠"这条路径——
    `plan_fold` 的对折循环一次都没执行过。实测（seed 7、`regions.b2=400`、54 次调用）：
    compaction 只在 seq 51/66 各发生一次，编年史头部却被改写了 15 次。
    """
    out = tmp_path_factory.mktemp("squeezedrun")
    cfg = Config(regions=RegionBudget(b2=400))
    actors = {s: MockActor(s, synthesize=True, rng=random.Random(s * 31 + SEED))
              for s in ALL_SEATS}
    res = asyncio.run(game.play(cfg=cfg, deal_seed=SEED, out_dir=out, actors=actors))
    events, meta = EventLog.read_records(res.path)
    calls = [(e.seq, e.day, e.request.get("messages", []), str(e.payload.get("_idem", "")))
             for e in events if e.request.get("messages")]
    assert len([e for e in events if e.kind == Kind.COMPACTION]) >= 1, \
        "掐了预算还没折叠，这个 fixture 就退化成上面那批断言的复读"
    return cfg, res, events, meta, calls


@pytest.fixture(scope="module")
def airtight(tmp_path_factory):
    """B2 掐到连"只剩当天"都装不下：天地板必须生效，而这件事得在日志里读得出来。

    `squeezed`（b2=400）不够狠——实测同一局牌折到底之后 B2 是 250 tok，仍小于预算，"超了多少"
    在那里恒为 0，拿它当 fixture 就是让新断言空转。b2=200 时折到第 4 天收手（rounds=3、
    window=20），B2 实测 296 tok > 200：板真的生效，才有超额可测。
    """
    out = tmp_path_factory.mktemp("airtight")
    cfg = Config(regions=RegionBudget(b2=200))
    actors = {s: MockActor(s, synthesize=True, rng=random.Random(s * 31 + SEED))
              for s in ALL_SEATS}
    res = asyncio.run(game.play(cfg=cfg, deal_seed=SEED, out_dir=out, actors=actors))
    events, meta = EventLog.read_records(res.path)
    reqs = [e.request for e in events if e.request.get("messages")]
    assert reqs, "这一局没有 per-turn 的 request 记录，fixture 空了"
    return cfg, res, events, meta, reqs


def test_the_day_floor_breach_is_written_into_every_request(airtight):
    """天地板允许 B2 超它的软预算——超了多少必须有数，不能只写在散文里。

    三件事各缺一条就换成另一句谎话：`region_tokens` 说的是 B1/B2 各自多大，不是"板有没有生效"
    （`#25` 之前那里只有 A/B/C 三区总数，折掉的和逐条的混在 B 里）；单个日志文件的读者拿不到
    `Config`（audit 只有 `config_hash`），所以预算数字必须跟着测量一起落盘，否则"超额 96 tok"没有
    分母；而"折到底仍然超"是设计不是 bug（宁可超也不把当天折成一行），它需要自己的读数，"这一批有多少
    prompt 是踩着地板发出去的"才成为一个问得出口的问题。
    """
    cfg, _, _, _, reqs = airtight
    for r in reqs:
        toks = r["region_tokens"]
        assert {"B1", "B2"} <= set(toks), sorted(toks)
        assert r["b2_over_cap"] >= 0, "超额是『超出多少』不是『还差多少』，负数会把 0 混进来"
        # 局况卡和两个小节标题也在 B 里，所以两半之和只该比整体小一截卡片钱，不该小很多
        # （那是"其中一格算的不是这段字节"），也不该大过整体（那是一格把另一格又数了一遍）。
        slack = toks["B"] - toks["B1"] - toks["B2"]
        assert 10 <= slack <= 100, f"两半没算尽 B，差 {slack} tok：{toks}"
    b1 = [r["region_tokens"]["B1"] for r in reqs]
    assert all(x <= y for x, y in zip(b1, b1[1:])), \
        "折掉的一天不会退回来：B1 只该随折叠单调变长，跳回去说明它算的不是那一段"
    over = [r for r in reqs if r["b2_over_cap"] > 0]
    assert over, "fixture 失效：这一局的 B2 从没踩过地板，那条读数就是恒 0"
    assert max(r["b2_over_cap"] for r in over) == \
        max(r["region_tokens"]["B2"] for r in over) - cfg.regions.b2, \
        "超额的尺子不是 `regions.b2`，那就是另起了一分账"


def _head(b: str) -> str:
    return _split_card(b)[0]


def test_a_fold_does_not_make_the_window_slide_every_turn(squeezed):
    """改写头部的次数必须≈折叠次数，否则 §6 的缓存收益在这里就是虚构的。

    一次折叠 = 一次前缀冲洗 = 实测 3.44× 的 prefill 重付。几何阶梯的设计是"一局只压 3–4 次"，
    而对折循环给出的窗口一旦比编年史短，`events[-window:]` 就每回合往前挪一格——头部每回合
    改写一次，正是本文件开头写着"已经修掉"的那个 33 次/局 的故障。多容忍 1 次是首次折叠之前
    什么都没有可比的这一格。
    """
    _, _, events, _, calls = squeezed
    comp = [e for e in events if e.kind == Kind.COMPACTION]
    prev, breaks = "", []
    for seq, day, messages, _idem in calls:
        head = _head(_regions(messages)[1])
        if not head.startswith(prev):
            breaks.append(seq)
        prev = head
    assert len(breaks) <= len(comp) + 1, \
        f"{len(breaks)} 次头部改写 vs {len(comp)} 次折叠（改写在 seq {breaks}）：" \
        f"预算一 binding 就退回逐回合滑窗，缓存每回合作废"


def test_the_verbatim_part_starts_at_a_day_boundary(squeezed):
    """B1 是"按天折叠"的摘要层，所以一旦发生了折叠，B2 的起点只能落在某一天的第一条上。

    起点落在一天中间，下一回合它就必然要往前挪（那一天还在长），滑窗由此而来；按天锚定之后
    一天之内它一格都不动，冲洗只随"又多折了一天"发生。
    """
    _, _, events, _, calls = squeezed
    # 每天的第一条编年史记录：B2 的起点必须是其中之一，而不是那天的中间。
    chrono = chronicle(tuple(events))
    seen_days, day_starts = set(), set()
    for e in chrono:
        if e.day not in seen_days:
            seen_days.add(e.day)
            day_starts.add(e.seq)
    for seq, _day, messages, _idem in calls:
        chronicle_text, _card = _split_card(_regions(messages)[1])
        if "== 已折叠 ==" not in chronicle_text:
            continue
        tags = [int(m.group(1)) for ln in chronicle_text.splitlines() if (m := TAG.match(ln))]
        assert tags, f"seq={seq}: 折叠之后 B2 里一条逐条记录都没有"
        start = tags[0]
        assert start in day_starts, \
            f"seq={seq}: B2 从 [e{start}] 开始，那是某一天的中间，不是开头"


def test_a_longer_day_appends_to_b2_instead_of_moving_its_front():
    """预算 binding 时也必须只往后长：同一天内多加一条，B2 的起点不能挪。

    `plan_fold` 的对折循环给的是一个**固定条数**的窗口，而编年史每回合同步长一条，
    `events[-window:]` 于是每回合往前挪一格——头部改写一次，缓存作废一次。按天锚定之后，
    窗口大小是"从折叠点到结尾"，一天之内只增不减，冲洗就只跟着"又多折了一天"发生。
    """
    from wolfengine.compress import chrono_bytes, plan_fold
    from wolfengine.events import Event, Kind

    def speech(i: int) -> Event:
        return Event(seq=i, kind=Kind.SPEECH, day=1 + i // 10, phase="day_speech",
                     visibility="all", actor=(i % 9) + 1,
                     payload={"act": "accuse", "target": (i % 7) + 1,
                              "text": f"{(i % 9) + 1}号：{(i % 7) + 1}号这话前后对不上，我保留意见。"})

    def fold_of(n: int):
        evs = tuple(speech(i) for i in range(1, n + 1))
        plan = plan_fold(evs, est=len, b2_cap=1)
        return evs, plan

    a_evs, a_plan = fold_of(23)
    b_evs, b_plan = fold_of(24)          # 同一天（第 3 天）的下一条发言
    a_body = chrono_bytes(a_evs, a_plan.folded_days, a_plan.window)
    b_body = chrono_bytes(b_evs, b_plan.folded_days, b_plan.window)
    assert b_body.startswith(a_body), \
        f"同日多加一条就把 B2 前面挪掉了：\n{a_body[-120:]!r}\n{b_body[-120:]!r}"


def test_a_shrink_pull_never_drops_a_day_without_summarising_it():
    """`shrink` 是"看不见了"的杠杆，不是"没了"的杠杆：被切掉的每一条，所在那天必须在 B1 里。

    按天折叠保证这一点（折叠点就是天边界），但 `shrink` 会在当天中间再下一刀。那把刀落下之后，
    `folded_days` 必须跟着挪——否则被切掉的几条既不在逐条里、也不在摘要里，模型看到的是这几天
    凭空短了一截，而日志上只写着"压过一次"。这条断言读的是计划本身，不是渲染结果：B1 和 B2
    由同一个 `folded_days` 决定，所以钉住它就钉住了两边。
    """
    from wolfengine.compress import plan_fold
    from wolfengine.events import Event, Kind

    def speech(i: int) -> Event:
        return Event(seq=i, kind=Kind.SPEECH, day=1 + (i - 1) // 10, phase="day_speech",
                     visibility="all", actor=(i % 9) + 1,
                     payload={"act": "accuse", "target": (i % 7) + 1,
                              "text": f"{(i % 9) + 1}号：{(i % 7) + 1}号这话前后对不上。"})

    evs = tuple(speech(i) for i in range(1, 31))
    for shrink in (0, 1, 2, 3):
        plan = plan_fold(evs, est=len, start_window=90,
                         min_window=4, b2_cap=1, shrink=shrink)
        dropped = {e.day for e in evs[:len(evs) - plan.window]}
        assert dropped <= set(plan.folded_days), \
            f"shrink={shrink}: 第{sorted(dropped - set(plan.folded_days))}天被切掉了却没进摘要"


def test_one_marker_per_distinct_fold_state(squeezed):
    """折叠标记记的是"折叠状态变了"，不是"这一回合又看见了一次同一个状态"。

    `Kind.COMPACTION` 的注释就是这么承诺的（"one per distinct fold state"），而 audit 的
    缓存冲洗账、`cli` 只取最后一条当账本的做法都建立在它上面。改成按天锚定之后 B2 的**条数**
    每回合都在长（那正是只往后长的意思），所以幂等键里带上 `window` 就等于每回合发一条新标记：
    实测 15 条标记 vs 2 个折叠状态，账面上多算了 13 次根本不存在的冲洗。
    """
    _, _, events, _, _calls = squeezed
    comp = [e for e in events if e.kind == Kind.COMPACTION]
    states = {tuple(c.payload.get("folded_days") or ()) for c in comp}
    assert len(comp) == len(states), \
        f"{len(comp)} 条 compaction 只对应 {len(states)} 个折叠状态：{sorted(states)}"


def test_manifest_records_the_actor_kinds_of_every_game(played):
    """plan §15：真人局永远不进配对语料，靠的就是这里落的字段。"""
    _, _, _, meta, _ = played
    assert "mock" in str(meta.get("actor_kinds")), f"manifest has no actor_kinds: {sorted(meta)}"
    assert meta.get("deal_seed") == SEED


def test_a_marker_is_not_chronicle_material_even_handed_to_the_primitives():
    """入参契约写在门口，而不是只写在文档里：`chrono_bytes`/`plan_fold`/`fold_body` 收下的事件先过 `chronicle()`。

    调用点（`assemble`）已经过滤了，为什么还要在纯函数门口再来一次：折叠标记一旦落盘就在日志里，
    下一个新写的调用点忘了过滤不会报错——它会是一段摘要去摘要自己，而且它带的新 seq 插在逐条那段
    前面，把已缓存的前缀改写掉。症状只有延迟，延迟没有断言就没人会去看。

    `fold_body` 那一道只能用**私有事件**来钉：它的 COMPACTION 半边是等价变异——`day_fold_lines`
    本来就只数 SPEECH/VOTE/DEATH，一条摘要混进某一天的事件列表也不会改一个字节。可见性那半边是真
    约束（一条夜里私聊混进去，"发言N人"就多一个），所以断言打在这一边，而不是假装前者也能测出来。
    """
    from wolfengine import compress
    from wolfengine.events import Event, Kind

    speeches = [Event(seq=i, kind=Kind.SPEECH, day=1 + i // 4, phase="day_speech",
                      visibility="all", actor=(i % 9) + 1,
                      payload={"act": "accuse", "target": (i % 7) + 1,
                               "text": f"{(i % 9) + 1}号：{(i % 7) + 1}号这话前后对不上。"})
                for i in range(1, 9)]
    marker = Event(seq=9, kind=Kind.COMPACTION, day=3, phase="day_speech", visibility="all",
                   payload={"summary": "第1天：发言3人。 出局：无人。", "window": 4,
                            "folded_days": [1, 2]})
    private = Event(seq=10, kind=Kind.SPEECH, day=1, phase="night_whisper",
                    visibility=frozenset({2}), actor=2,
                    payload={"act": "accuse", "target": 3, "text": "2号只告诉3号的一句话。"})
    dirty = [*speeches, marker]

    assert compress.chrono_bytes(tuple(dirty), (), 4) == compress.chrono_bytes(tuple(speeches), (), 4)
    assert "[e9]" not in compress.chrono_bytes(tuple(dirty), (1,), 4)
    # 8 条还是 9 条会改几何边界（8→8 与 9→12），所以 `plan_fold` 那一步的漏过滤不是看不见的：
    # 它对折的次数会多一轮，窗口的落点也一样，但 rounds 不同——缓存冲洗就多算了这一次。
    fold = lambda evs: compress.plan_fold(  # noqa: E731
        evs, est=estimate_tokens, b2_cap=1)
    assert fold(tuple(dirty)) == fold(tuple(speeches)), \
        "标记占了编年史的一格：窗口、对折轮数都会因为它多出一条而挪动"
    assert compress.fold_body(tuple(speeches) + (private,), (1,)) == \
        compress.fold_body(tuple(speeches), (1,)), \
        "门口的 `chronicle()` 一撤，一句私聊就进了第1天的摘要（发言人数多一个）"
