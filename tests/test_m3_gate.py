"""M3 闸门：把 §10 预注册的五条判据变成一句能引用的话。

阈值本身不是这里的新东西（计划 §十 M3★ 那行早就写死了），新的是**判定**：在此之前
`m5_style_collapse` 只把三个阈值原样打印出来，人要拿三个数自己比大小。一个会说"过 / 不过 /
没法判"的发射器才有两处用途：M3 跑完当天就能记录结论（§12 验收第 3 条要求"或有明确失败记录"），
以及一份不合格的批次不会再被当成一份可对比的批次。

这一页全部可以在端点关着的时候验证，因为判的是聚合算术与"数据不足时不许假装通过"，
不是模型行为——真行为等 M3 的 20 次切片。
"""
from __future__ import annotations

import dataclasses
import inspect
import re
import shutil
from pathlib import Path

import pytest

from wolfengine import batch, cli, metrics
from wolfengine.config import Config
from wolfengine.events import Event, Kind, empty_notice, meta_notice

# 一局"活着"的发言轮：9 个互不相同的开头、每条都点具体座位、act 是指派里的攻击性动作。
# 措辞是真量过的（`collapse_round`=0.043、`opening_distinct_rate`=1.0），不是随手写的相似句——
# 上一版这里写成"X号玩家讲一句全新的话"模板，四条 4-gram 骨架完全一样，collapse 实测 0.58，
# 于是"PASS 基准"自己先过不了 collapse 那条，测试会因为 fixture 而不是因为实现变红/变绿。
LIVELY = ["我先不表态，等听完再判断。我指控1号", "这轮我选择保留看法。我指控2号",
          "暂时没什么要补充的。我指控3号", "我想再听下去再说。我指控4号",
          "现在开口容易暴露思路。我指控5号", "我先记下来，晚点讲。我指控6号",
          "这个节点我不太想站边。我指控7号", "姑且观望一下局势。我指控8号",
          "容我想想再发言。我指控9号"]
# §十 重心修正的那张脸：措辞全异、开头全异，行为上清一色是"先听"（collapse=0.0、passivity=1.0）。
MEASURED_PASSIVE = ["我先不表态，等听完再判断。", "这轮我选择保留看法。", "暂时没什么要补充的。",
                    "我想再听下去再说。", "现在开口容易暴露思路。", "我先记下来，晚点讲。",
                    "这个节点我不太想站边。", "姑且观望一下局势。", "容我想想再发言。"]


_NO_KEY = object()


def _speech(seq: int, seat: int, text: str, act: str, *, day: int = 1,
            lat: float = 4.2, assigned: object = _NO_KEY, finish: str | None = None,
            attempts: list[dict] | None = None, **meta: object) -> Event:
    req: dict[str, object] = {"total_tokens_est": 900}
    if assigned is not _NO_KEY:
        # 三种形状都得能分开造出来：`_NO_KEY` 是"`#68` 之前的日志，这一格根本没落盘"，
        # `assigned=None` 是"量过了、这一轮法官没有指派"，字符串才是"指派了 X"。
        req["assigned_act"] = assigned
    resp: dict[str, object] = {"latency_s": lat, "completion_tokens": 60}
    if finish is not None:
        # 缺席而不是 `None`：端点没报 `finish_reason` 和报了"没切断"是两件事（`#92`）。
        resp["finish_reason"] = finish
    return Event(seq=seq, kind=Kind.SPEECH, day=day, phase="day_speech", visibility="all",
                 actor=seat,
                 payload={"seat": seat, "text": text, "act": act,
                          "meta": {"rung": 0, "fallback": 0, **meta}},
                 request=req,
                 attempts=attempts or [],
                 response=resp)


def _vote(seq: int, seat: int, *, day: int = 1, lat: float = 4.2,
          finish: str | None = None) -> Event:
    """一次不是发言的决定：`day_vote` 的 `max_tokens` 和发言的不是一个数，所以切断要按相读。"""
    resp: dict[str, object] = {"latency_s": lat, "completion_tokens": 40}
    if finish is not None:
        resp["finish_reason"] = finish
    return Event(seq=seq, kind=Kind.VOTE, day=day, phase="day_vote", visibility="all",
                 actor=seat,
                 payload={"seat": seat, "target": seat % 9 + 1,
                          "meta": {"rung": 0, "fallback": 0}},
                 request={"total_tokens_est": 900}, response=resp)


def _round(day: int, first_seq: int, texts: list[str], act: str = "accuse",
           finish: str | None = None, **meta: object) -> list[Event]:
    return [_speech(first_seq + i, i + 1, t, act, day=day, finish=finish, **meta)
            for i, t in enumerate(texts)]


def _game(events: list[Event], *, synthetic: bool = False, game_id: str = "g1") -> metrics.Game:
    return metrics.Game(path=Path(f"/tmp/{game_id}.jsonl"),
                        meta={"game_id": game_id,
                              "actor_kinds": ["mock"] if synthetic else ["llm"]},
                        events=events)


def _lively_batch(n_games: int = 2) -> list[metrics.Game]:
    return [_game(_round(1, 1, LIVELY) + _round(2, 11, LIVELY), game_id=f"g{n}")
            for n in range(n_games)]


# ------------------------------------------------------------------------------------ 五条判据
def test_the_gate_returns_a_verdict_for_every_pre_registered_criterion():
    """五条，一条都不能悄悄少：§十 M3★ 写的是"预注册判据全部达标"才过，
    所以发射器必须逐条给值、给阈值、给达标与否，而不是只回一个布尔。"""
    out = metrics.m3_gate_verdict(_lively_batch())
    assert out["verdict"] == "PASS", out
    assert set(out["criteria"]) == {"passivity_rate", "collapse_round",
                                    "opening_distinct_rate", "latency_p95_s",
                                    "context_overflows"}
    for key, c in out["criteria"].items():
        assert c["value"] is not None, f"{key} 没有值却判了 PASS"
        assert c["ok"] is True, f"{key}={c['value']} 对 {c['comparator']} {c['threshold']}"
        assert c["comparator"] in ("<", ">", "<=", ">=", "==")
    # 阈值就是计划里那几个数，抄错的阈值比没有阈值更危险。
    assert out["criteria"]["passivity_rate"]["threshold"] == 0.4
    assert out["criteria"]["collapse_round"]["threshold"] == 0.35
    assert out["criteria"]["opening_distinct_rate"]["threshold"] == 0.8
    assert out["criteria"]["latency_p95_s"]["threshold"] == 20.0
    assert out["criteria"]["context_overflows"]["threshold"] == 0


def test_the_denominators_are_printed_next_to_the_rates():
    """一个没有分母的比率不能拿去开会：passivity 按发言条数、collapse 按轮、p95 按调用次数，
    三个分母各不同，报告里必须各写各的。"""
    out = metrics.m3_gate_verdict(_lively_batch(2))
    assert out["n_games"] == 2
    assert out["criteria"]["passivity_rate"]["n"] == 36, "2 局 × 2 轮 × 9 人"
    assert out["criteria"]["collapse_round"]["n"] == 4, "轮数按回合数，不按发言条数"
    assert out["criteria"]["latency_p95_s"]["n"] == 36


# ---------------------------------------------------------------------------- 不许"没数据=通过"
def test_an_empty_batch_is_not_evaluable_rather_than_a_pass():
    """`passivity_rate([])` 返回 0.0（见它自己的定义），0.0 < 0.4 就是"通过"。
    这个默认值在按轮调用时是对的——一轮总有话——但把它端上批级判定就变成：
    一次什么都没跑上的切片给整个产品盖了个合格章。"""
    out = metrics.m3_gate_verdict([])
    assert out["verdict"] == "NOT_EVALUABLE", out
    assert out["criteria"]["passivity_rate"]["value"] is None
    assert "没有" in out["note"] or "空" in out["note"], out["note"]


def test_a_batch_of_stand_in_tables_is_not_evaluable_rather_than_a_pass():
    """plan §11（"必须真端点，mock 只会自证"）：替身桌永不进评测语料。M1 已经按这条剔样本了，
    闸门也必须拒——而且理由要说出口，不能让人以为闸门坏了。§15 管的是含真人的那一种。"""
    out = metrics.m3_gate_verdict([_game(_round(1, 1, LIVELY), synthetic=True, game_id="g1"),
                                   _game(_round(1, 1, LIVELY), synthetic=True, game_id="g2")])
    assert out["verdict"] == "NOT_EVALUABLE"
    assert out["n_synthetic_excluded"] == 2 and out["n_games"] == 2
    assert "§十一" in out["note"] and "替身桌" in out["note"], out["note"]


def test_a_zero_clock_cannot_satisfy_the_latency_criterion():
    """时钟没走过两种形态，都必须落到"无读数"而不是"够快"：
    ① 时钟没走过的一串调用（`TransportResult.latency_s` 的默认值 0.0 被原样写进 `response`）；
    ② 替身座位（MockActor 根本不经 transport）——实测整份日志一个 `response` 字段都没有，
       于是读数列表是空的。
    端点不可能 0 秒回九张座位，所以这两种情况下"p95 = 0 < 20"都是一句假话。"""
    zero_clock = [_game(_round(1, 1, LIVELY, lat=0.0))]              # ① 时钟全程 0.0
    no_response = [_game([dataclasses.replace(e, response={})        # ② 根本没有 response
                          for e in _round(1, 1, LIVELY)])]
    for games in (zero_clock, no_response):
        out = metrics.m3_gate_verdict(games)
        assert out["criteria"]["latency_p95_s"]["ok"] is None, (games, out["criteria"])
        assert out["criteria"]["latency_p95_s"]["value"] is None
        assert out["verdict"] == "NOT_EVALUABLE"
        assert out["failed"] == [], "时钟没走不是失败，是没测到——两者不能混成一个词"


def test_a_batch_the_engine_answered_for_cannot_be_certified_not_passive():
    """主判据给一张模型一次也没答过的桌发了"不消极"合格证（`#117`）。

    机制不在阈值上：兜底发言的 `text` 是空串、`meta.rung=-1`（连提案都没有），而 `passivity_rate`
    只把 `act in (listen,align)` 记成消极——于是引擎写的空嘴里，凡是指派了攻击性动作的那几张被记成
    **主动发言**。真日志（`/tmp/down116/…g00000001.jsonl`，26/26 次调用超时）读出来是
    `passivity_rate = 0.3333 达标`；这里把它造得更干净：九张全空、全 `-1`，读数 0.0。
    """
    dead = [_speech(1 + i, i + 1, "", "accuse", rung=-1, fallback=1, timed_out=1)
            for i in range(9)]
    out = metrics.m3_gate_verdict([_game(dead)])
    assert out["criteria"]["passivity_rate"]["value"] == 0.0, "fixture 失效：这局不该读成不消极"
    assert out["criteria"]["passivity_rate"]["ok"] is None, (
        "一次模型回答都没有的批次，主判据不许说达标：", out["criteria"]["passivity_rate"])
    assert out["verdict"] == "NOT_EVALUABLE" and out["failed"] == []
    assert "引擎" in out["note"], out["note"]
    # 对照：同一张桌子的九句话若是模型自己说的，读数照旧算数（这条挡住"顺手把空文本都拒了"，
    # 而空文本该不该算弃答是 `#40` 的开放问题，不在这里改判）。
    spoken = [_speech(1 + i, i + 1, "", "accuse") for i in range(9)]
    live = metrics.m3_gate_verdict([_game(spoken)])
    assert live["criteria"]["passivity_rate"]["ok"] is True, live["criteria"]


def test_the_gate_prints_who_wrote_the_answers_it_is_scoring():
    """半死的批次：闸门必须说得出"这 18 轮里有 9 轮的回答不是模型写的"。

    这一条不改判据的算术（`#107` 那一句"沉默正是要量的东西，一个 turn 也不能少算"仍然成立，
    它说的是模型的沉默），改的是**可见性**：在此之前的输出里，代打轮和真回答混在同一个 n 里，
    读的人拿不到任何线索去问"这 0.1111 是谁的 0.1111"。
    """
    games = [_game(_round(1, 1, LIVELY), game_id="alive"),
             _game([_speech(1 + i, i + 1, "", "accuse", rung=-1, fallback=1)
                    for i in range(9)], game_id="dead")]
    out = metrics.m3_gate_verdict(games)
    assert (out["engine_written"]["n_turns"], out["engine_written"]["n_engine"]) == (18, 9)
    line = "\n".join(batch._m3_md(["A"], {"A": out}))
    assert "9/18" in line, line
    assert "引擎" in line, line
    # 干净批次不许长出这一格的分母：一个 `0` 也要说成"量过了"，而不是不印。
    clean = metrics.m3_gate_verdict([_game(_round(1, 1, LIVELY))])
    assert clean["engine_written"]["n_engine"] == 0
    assert "0/9" in "\n".join(batch._m3_md(["A"], {"A": clean}))


def test_a_refused_criterion_is_not_printed_as_a_missing_reading():
    """`不计` 与 `无读数` 在渲染里必须是两个词，否则拒绝白做（`#117`）。

    判层已经把这一格拒了（`ok=None`），但读者拿到的是那一行字：如果渲染器把它印成"无读数"，
    报告读起来就是"闸门缺了一次测量"，而事实是"闸门手上有数、拒绝把它当模型的数"。这两种
    处置下一步不一样——前者要去补数据，后者要去查端点。所以这一条钉的是渲染，不是算术。
    """
    dead = [_speech(1 + i, i + 1, "", "accuse", rung=-1, fallback=1) for i in range(9)]
    out = metrics.m3_gate_verdict([_game(dead)])
    line = "\n".join(batch._m3_md(["A"], {"A": out}))
    assert "passivity_rate = 0.0（需 < 0.4，n=9）不计：9/9 轮发言是引擎代打的" in line, line
    # 反面对照：同一个 `ok=None` 若是真的没测到（这份连 `response` 都没有），必须仍然印
    # "无读数"。两个词一混，读者就分不清"去补数据"和"去查端点"这两条下一步。
    blank = metrics.m3_gate_verdict([_game([dataclasses.replace(e, response={})
                                           for e in _round(1, 1, LIVELY)])])
    b_line = "\n".join(batch._m3_md(["A"], {"A": blank}))
    assert "无读数" in b_line, b_line
    assert "不计：" not in b_line, b_line


def test_the_engine_share_counts_calls_separately_from_turns():
    """两个分母得真的是两个数：代打的**次数**里含非发言的决定，代打的**轮数**里不含（`#107` 的形）。

    一条 `night`/`vote` 决定不是"一轮发言"，但它是"一次调用"。所以往死桌里塞一张引擎代投的票，
    `n_engine_calls` 该比 `n_engine_turns` 多 1，而印出来的那一句的两个分数不能是同一个数抄两遍。
    """
    v_live = _vote(30, 3)
    v_dead = dataclasses.replace(v_live, payload={**v_live.payload,
                                                  "meta": {"rung": -1, "fallback": 1}})
    games = [_game(_round(1, 1, LIVELY) + [v_live], game_id="alive"),
             _game([_speech(1 + i, i + 1, "", "accuse", rung=-1, fallback=1)
                    for i in range(9)] + [v_dead], game_id="dead")]
    ew = metrics.m3_gate_verdict(games)["engine_written"]
    assert (ew["n_turns"], ew["n_engine"]) == (18, 9), ew
    assert (ew["n_calls"], ew["n_engine_calls"]) == (20, 10), ew
    assert "9/18 轮发言、10/20 次调用" in ew["note"], ew["note"]


# -------------------------------------------------------------------------------------- 失败长相
def test_varied_wording_with_passive_behaviour_fails_the_primary_criterion_alone():
    """§十 的重心修正：温度能买到 8/8 唯一开头，买不到行为。
    这一批措辞全异、开头全异，但每一轮都是 listen 且没人点名——必须只有主判据红，
    否则就等于把 M3 的成败交给次判据。"""
    assert metrics.opening_distinct_rate(MEASURED_PASSIVE) == 1.0, "fixture 失效：开头重了"
    assert metrics.collapse_round(MEASURED_PASSIVE) < 0.35, "fixture 失效：措辞太像，两条会一起红"
    out = metrics.m3_gate_verdict([_game(_round(1, 1, MEASURED_PASSIVE, act="listen"))])
    assert out["verdict"] == "FAIL"
    assert out["failed"] == ["passivity_rate"], out["criteria"]
    assert out["criteria"]["passivity_rate"]["value"] == pytest.approx(1.0)


def test_a_context_overflow_fails_the_gate_even_in_a_lively_batch():
    """0 次 context 400 是预注册的硬条件之一：它塌缩的是缓存前缀，不是措辞，
    所以风格四条全绿也救不回来。"""
    out = metrics.m3_gate_verdict(
        [_game(_round(1, 1, LIVELY, context_overflow=1))])
    assert out["verdict"] == "FAIL"
    assert out["failed"] == ["context_overflows"], out["criteria"]


def test_a_slow_batch_fails_only_the_latency_criterion():
    """p95 是"单轮"的，不是整局墙钟：30s 的 p95 说明这一刀切片的观感已经不可用了（§六）。
    顺带钉住 `_pct` 的最近秩算法在 n=9 时 p95 就等于最大值——真切片是 5 次 × 9 人，
    分位数没那么容易被单个离群点决定，但样本量小的时候要心里有数。"""
    games = [_game([_speech(1 + i, i + 1, LIVELY[i], "accuse", lat=2.0 if i < 8 else 31.0)
                     for i in range(9)])]
    out = metrics.m3_gate_verdict(games)
    assert out["failed"] == ["latency_p95_s"], out["criteria"]
    assert out["criteria"]["latency_p95_s"]["value"] == pytest.approx(31.0)


def test_collapsed_wording_fails_the_style_criteria_but_not_the_primary_one():
    """反方向的对照：全桌复读同一句。措辞两条该红；`passivity_rate` 该绿，
    因为那句里点了名、act 也不是 listen——这正是"主判据可能被措辞骗过"的另一半。"""
    same = ["我觉得3号玩家说得很有道理，我先听一下其他人的想法。"] * 9
    out = metrics.m3_gate_verdict([_game(_round(1, 1, same))])
    assert out["failed"] == ["collapse_round", "opening_distinct_rate"], out["criteria"]
    assert out["criteria"]["passivity_rate"]["ok"] is True


def test_m5_template_top1_is_the_share_of_a_round_not_a_head_count():
    """`template_top1_freq` 这个名字承诺的是比例（plan §8 M5 那格写"占全体比例"），
    10:48:41Z 的实现给的是**份数**，而且全仓库没有一个读者：`grep -rn template_top1 src/ tests/`
    只命中产出它的那两行，测试里零断言。名字说谎 + 没人读，两样各修一样都不够，所以这一条同时钉：
    字段名要能望文生义，值要能跨轮比。

    份数不可比是这里的关键，不是命名洁癖：五份发言里三句念同一句（0.6）和三份里三句念同一句
    （1.0，全桌塌完）在份数上都是 3。而 M5 是按轮算再取均值，均值底下混着两种 `n`。
    """
    chorus = "我先听听大家的发言，"
    texts = [chorus + "我保留意见。", chorus + "有道理。", chorus + "今晚不投。",
             "独立的一句话，谁也别学谁。", "另一句，也不重复。"]
    row = metrics.m5_style_collapse(_round(1, 1, texts))["rounds"][0]
    assert row["n"] == 5, row
    assert row["template_top1"] == chorus, row
    assert row["template_top1_share"] == pytest.approx(3 / 5), row
    # 分母是**本轮**的份数，不是这一局的：同一批句子拆成 5 人轮 + 3 人轮，第二轮得读成"全塌"。
    two = metrics.m5_style_collapse(_round(1, 1, texts) + _round(2, 11, texts[:3]))
    assert [r["template_top1_share"] for r in two["rounds"]] == pytest.approx([0.6, 1.0]), two
    # 反向对照：一桌各说各的，读数得是 0.0 而不是"没测到"（None 会被均值悄悄吞掉）。
    quiet = metrics.m5_style_collapse(_round(1, 1, LIVELY))["rounds"][0]
    assert quiet["template_top1"] == "", quiet
    assert quiet["template_top1_share"] == 0.0, quiet


# ---- 这把尺子的分母与地板（#105）
def test_the_template_share_divides_by_the_seats_that_spoke():
    """同一行里不许有两个分母：改动前 `template_top1_share` 除的是本轮**条目数**。

    21:12:57Z 现测（改动前）：同样三句复读，配上六个空手 seats 的那一行读
    `template_top1_share=0.3333`，不配空手的同一批句子读 `1.0`；而稀释那一行的
    `dup_exact6_rate` 已经读 `1.0` 了（`#104` 把地板抬到了开口人数）。所以这不是"另一种合理
    口径"，是 `#102` 那个形状——同一个分母在同一个出口的两格里差三倍。空手不是一次"说了不一样
    的话"，它不该把模板的比例摊薄。
    """
    chorus = "我先听听大家的发言，"
    three = [chorus + "我保留意见。", chorus + "有道理。", chorus + "今晚不投。"]
    diluted = metrics.m5_style_collapse(_round(1, 1, three + [""] * 6))["rounds"][0]
    plain = metrics.m5_style_collapse(_round(1, 1, three))["rounds"][0]
    assert diluted["n"] == 9 and plain["n"] == 3, (diluted, plain)
    assert diluted["template_top1_share"] == pytest.approx(1.0), diluted
    assert diluted["template_top1_share"] == plain["template_top1_share"], \
        "同一句模板、同一批开口的人，六张空手不该把比例改掉"
    assert diluted["dup_exact6_rate"] == 1.0, "同一行的另一把尺子必须讲同一个故事"


def test_a_round_without_three_microphones_gives_no_template_reading():
    """两份话筒的轮量不出模板：这把尺子的地板是 3（`min_count`），不是 2。

    21:12:57Z 现测（改动前）：两份逐字相同的发言那一行是 `dup_exact6_rate=1.0` +
    `template_top1_share=0.0`，一个人开口 + 八张空手那一行是三条措辞尺子全 `None` +
    `template_top1_share=0.0`。同一行里同时说"开口的人全在复读"和"本轮没有模板"，而后者只是
    "不可能有候选"的另一个写法（`#95`/`#103`/`#104` 那条：定义值不是测量）。
    反向也要钉住：九个人各说各的确实量得到，那一行仍是 `""` + `0.0`，不许为了统一口径改成 None
    （`#66` 那条注释就写着 None 会被均值吞掉）。
    """
    chorus = "我先听听大家的发言，"
    two = metrics.m5_style_collapse(_round(1, 1, [chorus] * 2))["rounds"][0]
    assert (two["template_top1"], two["template_top1_share"]) == (None, None), two
    solo = metrics.m5_style_collapse(_round(1, 1, [chorus] + [""] * 8))["rounds"][0]
    assert (solo["template_top1"], solo["template_top1_share"]) == (None, None), solo
    quiet = metrics.m5_style_collapse(_round(1, 1, LIVELY))["rounds"][0]
    assert quiet["template_top1"] == "" and quiet["template_top1_share"] == 0.0, \
        "三个人以上开口、确实没有共享片段：这是量出来的 0.0，不是缺席"


def test_the_template_share_and_the_c4_miner_share_one_floor():
    """`docs/metrics.md` 承诺"同一份算术还喂给提示词的 C4 黑名单"。两个默认值一旦各自漂，
    那句话就成假话：C4 按 ≥4 屏蔽、读数按 ≥3 报，人拿看到的数字去解释提示词里为什么少了那一句。
    """
    share = inspect.signature(metrics.template_top_share).parameters
    miner = inspect.signature(metrics.template_top_fragments).parameters
    assert share["min_len"].default == miner["min_len"].default, (share, miner)
    assert share["min_count"].default == miner["min_count"].default, (share, miner)


def test_the_row_prints_both_of_its_denominators():
    """一行有两个分母，就得两格都印出来（`#105` 收尾点名的那一格，`#107`）。

    21:38:12Z 现测（改动前）：真日志三局九个发言轮里，`n` 与"真正开口的人数"只有一轮分家
    （`g00000301` 的 day-1 PK：`n=2`、一张话筒），而那一行同时写着三条措辞尺子全 `None` 和
    `passivity_rate=0.5`。读者看见的是 2，看不出这个 2 是"两个人都说话了"还是"一个人说话一个人
    空手"——`#95`/`#103`/`#104`/`#105` 四轮挣来的那四种缺席，在行里没有一个格子解释它。
    `passivity_rate` 的分母按设计就是条目数（沉默正是它量的东西，一个 turn 也不能少算），措辞三把
    的分母是开口人数：两个都真，那就两个都印。
    """
    chorus = "我先听听大家的发言，"
    three = [chorus + "我保留意见。", chorus + "有道理。", chorus + "今晚不投。"]
    diluted = metrics.m5_style_collapse(_round(1, 1, three + [""] * 6))["rounds"][0]
    pk = metrics.m5_style_collapse(_round(1, 1, [chorus, ""]))["rounds"][0]
    dead = metrics.m5_style_collapse(_round(1, 1, [""] * 9))["rounds"][0]
    assert (diluted["n"], diluted["n_spoken"]) == (9, 3), (diluted, pk, dead)
    assert (pk["n"], pk["n_spoken"]) == (2, 1), (pk, diluted, dead)
    assert (dead["n"], dead["n_spoken"]) == (9, 0), (dead, diluted, pk)
    # 印出来的那格必须就是缺席服从的那道地板，否则它只是一格好看的装饰。
    for row in (pk, dead):
        assert row["n_spoken"] < 2
        assert (row["collapse_round"], row["dup_exact6_rate"], row["opening_distinct_rate"]) \
            == (None, None, None), row
    assert diluted["n_spoken"] >= 2
    assert (diluted["collapse_round"], diluted["dup_exact6_rate"], diluted["opening_distinct_rate"]) \
        != (None, None, None), diluted


def test_the_pooled_headline_prints_its_own_denominator():
    """`passivity_pooled` 是这一族里唯一没有分母陪着印出来的比例（`#107` 的另一半）。

    21:38:12Z 现读：真日志 `g00000301` 那一局印的是 `passivity_pooled=0.1111` 加 `n_rounds=3`，
    旁边就是闸门那句 `<0.4`。0.1111 是 2/18 还是 20/180，行里看不出来——`m3_gate_verdict` 那边每条
    判据都自带一个 `n`（`#102` 挣来的），这一格却把 `pooled_passivity` 算好的分母扔掉了。
    替身桌的 batch 报告同理：一臂里混进半局空转时，读者拿两个规模不同的数去比同一个阈值。
    """
    chorus = "我先听听大家的发言，我保留意见。"
    events = _round(1, 1, LIVELY) + _round(2, 11, [chorus, ""], act="listen")
    view = metrics.m5_style_collapse(events)
    assert view["n_turns"] == 11 == sum(r["n"] for r in view["rounds"]), view
    assert view["n_turns"] != sum(r["n_spoken"] for r in view["rounds"]), \
        "头条除的是 turn 数，不是开口人数：拿错分母在这一格也要红"
    assert view["passivity_pooled"] == pytest.approx(0.1818), view  # 2/11，空手也算一次被动，不是 2/10
    quiet = metrics.m5_style_collapse([])
    assert (quiet["passivity_pooled"], quiet["n_turns"]) == (None, 0), quiet


# ------------------------------------------------------------------------------------ 一处来源
def test_the_printed_gate_and_the_verdict_share_one_threshold_table():
    """`m5_style_collapse` 里那格 `gate` 早就在打印阈值了；两处各写一份数字，
    过一阵就会有一处先被改，而报告不会为这种分歧变红。这里钉住：视图那格必须是同表的子集。"""
    assert set(metrics.M3_GATE) == {"passivity_rate", "collapse_round",
                                    "opening_distinct_rate", "latency_p95_s",
                                    "context_overflows"}
    view = metrics.m5_style_collapse(_round(1, 1, LIVELY))["gate"]
    assert set(view) == {"passivity_rate<", "collapse_round<", "opening_distinct_rate>"}
    for k, v in view.items():
        key, op = k[:-1], k[-1]
        assert metrics.M3_GATE[key] == (op, v), f"{k} 在两处不一致"


def test_a_measured_failure_is_not_hidden_behind_a_missing_reading():
    """一批既没有延迟读数、又确实被动（主判据红）的批，答案必须是 FAIL。
    "没测到"排在"测出来坏了"前面，就等于把坏消息藏进缺数据里——而 §十二 验收第 3 条要的
    恰恰是"或有明确失败记录"。"""
    games = [_game(_round(1, 1, MEASURED_PASSIVE, act="listen", lat=None))]
    out = metrics.m3_gate_verdict(games)
    assert out["failed"] == ["passivity_rate"], out["criteria"]
    assert out["criteria"]["latency_p95_s"]["ok"] is None
    assert out["verdict"] == "FAIL", out


# ------------------------------------------------------------------------------ 指派服从率（#68）
def test_the_assignment_denominator_counts_only_turns_that_were_assigned():
    """`request.assigned_act` 从落盘那天起零读者（12:19:14Z：两局 mock 的 43 条 speech 请求全带
    指派，`grep -rn assigned_act src/ tests/` 却只有写它的那一行），而 §7 第 1 条要的那个问题——
    "是不是只对指派表说 listen"——答不了：audit 只数座位**实际采取**的 act（`speech_acts`），
    指派那一半没人读。

    分母必须只数"真的被指派过"的轮：`kinds` 里 `act_not_as_assigned` 只记打过回的轮，
    "没指派"、"指派了且听了"、"指派了没听"三者在它那里都是"没有这条码"，所以分母只能来自这一格。
    下面 4 条被指派、2 条明确没指派（`assigned=None`）、1 条字段整个缺失（`_NO_KEY`）——
    把后两类算进分母会让服从率凭空掉一半，而把它们算成"听了指派"会让它凭空涨。
    """
    ev = [_speech(1, 1, "我指控2号。", "accuse", assigned="accuse"),
          _speech(2, 2, "我请3号说说。", "probe", assigned="probe"),
          _speech(3, 3, "我跟1号一致。", "align", assigned="align"),
          _speech(4, 4, "我先听。", "listen", assigned="listen"),
          _speech(5, 5, "我没被指派。", "listen", assigned=None),
          _speech(6, 6, "同上。", "accuse", assigned=None),
          _speech(7, 7, "旧日志。", "accuse")]
    out = metrics.assignment_compliance(ev)
    assert out["turns"] == 4, out
    assert out["recorded"] == 6, "带这个键的轮：4 指派 + 2 明确没指派，缺键的那条不算量过"
    assert out["by_assigned"] == {"accuse": 1, "probe": 1, "align": 1, "listen": 1}, out
    assert out["obeyed_first_try"] == 1.0 and out["obeyed_final"] == 1.0, out


def test_an_unrelated_refusal_is_not_the_same_as_ignoring_the_assignment():
    """两个口径各钉一半：第一次尝试"没因不合规指派被打回"≠"最终 act 等于指派"。

    这一条要的是**分辨力**：a 号被指派 `accuse`、第一次因为编造引用被打回但 act 一直是指派那个，
    b 号被指派 `accuse`、第一次就写了 `act_not_as_assigned` 且重问后仍交出自选的 `listen`。
    如果实现只看 `attempts` 非空（那是 M2 的 `refused_turns` 的算法），两半会一起变红，
    "闸门压力"和"指派表被无视"就成了同一个数的两个名字——而 §7 第 1 条要防的只是后者。
    """
    ev = [_speech(1, 1, "我指控3号。", "accuse", assigned="accuse",
                  attempts=[{"rung": 0, "violations": ["citation_not_in_chronicle:77"]}]),
          _speech(2, 2, "我先听一下。", "listen", assigned="accuse", fallback=1,
                  attempts=[{"rung": 0,
                             "violations": ["act_not_as_assigned:listen (法官指派 accuse)"]}])]
    out = metrics.assignment_compliance(ev)
    assert out["turns"] == 2, out
    assert out["obeyed_first_try"] == 0.5, out
    assert out["obeyed_final"] == 0.5, out
    assert out["by_assigned"] == {"accuse": 2}, "两轮的指派是同一个动作，分布要合到一格"


def test_a_log_from_before_the_assignment_field_says_it_has_no_reading():
    """字段缺失（`#68` 之前落盘的日志）与"这一批没人被指派"必须是两个不同的读数。

    两档都给 `None` 的比率就够了吗——不够：`recorded` 那一格才分得开"没人量过"和"量了，
    答案是这批桌没有指派轮"。后者是真行为（也许那批配置把发言相指派关了），前者只是这份
    文件旧。把前者印成 0/None 而不带这个数，报告就会把"升级前跑的批"说成"没有指派需求的批"。
    """
    out = metrics.assignment_compliance(_round(1, 1, LIVELY))
    assert out["recorded"] == 0 and out["turns"] == 0, out
    assert out["obeyed_first_try"] is None and out["obeyed_final"] is None, out
    assert out["by_assigned"] == {}, out
    # 对照：全都量过、只是没人被指派，`recorded` 就不是 0。
    none_assigned = metrics.assignment_compliance(
        [_speech(i, i, "我没被指派。", "listen", assigned=None) for i in range(1, 4)])
    assert none_assigned["recorded"] == 3 and none_assigned["turns"] == 0, none_assigned
    assert none_assigned["obeyed_final"] is None, none_assigned


# ------------------------------------------------------------------------ 闸门的发射口（#94）
def _stub_batch(tmp_path, *, games: int, mock: bool = False, finish: str | None = None):
    """一臂（`--configs A`）跑完整批次：`mock=True` 是替身桌，否则九座由桩判官回答。

    单臂不是省事：M3★ 判的是"这一臂的桌子塌不塌缩"，它天生就是一臂一个判定。两臂的形状
    在这里反而是错的——那会先把批次推进 `compare` 的"恰好两个配置臂"那道门（batch.py:493）。
    """
    import asyncio

    import httpx

    from wolfengine import batch
    from wolfengine.transport import HttpTransport

    from test_live_path import _chat_body, _user_text, oracle_answer

    arms = [batch.Arm("A", Config())]

    def reply(request: httpx.Request) -> httpx.Response:
        body = _chat_body(oracle_answer(_user_text(request)))
        if finish is not None:
            # 只改 `finish_reason`、内容保持合法：这一条钉的是那一格走没走到产物里。切断后的
            # 文本长什么样是上面那组算术用例的事，这里再把内容砍一半只会多一个失败原因。
            body["choices"][0]["finish_reason"] = finish
        return httpx.Response(200, json=body)

    async def go():
        if mock:
            return await batch.run_batch(arms, games=games, seed0=5, out_dir=tmp_path,
                                          mock=True, canary=batch.NO_CANARY)
        client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        try:
            return await batch.run_batch(arms, games=games, seed0=5, out_dir=tmp_path,
                                         transport=HttpTransport(Config(), client=client),
                                         canary=batch.NO_CANARY)
        finally:
            await client.aclose()

    return asyncio.run(go())


def test_a_single_arm_batch_leaves_its_gate_verdict_on_disk(tmp_path, key):
    """跑完一批真桌子，闸门判定必须是**产物**，不是终端上滚过的一行。

    §十二 验收第 3 条要的是"全部达标或有明确失败记录"——记录得在批次的目录里，跟着日志一起被
    引用。这一条以前不存在：`m3_gate_verdict` 唯一的生产调用点在 `compare` 里，而 compare 开头就
    要两个臂，所以 `--configs A` 这种最自然的 M3 跑法跑完，目录里只有日志和 manifest。
    """
    _stub_batch(tmp_path, games=2)
    text = (tmp_path / "m3_gate.md").read_text(encoding="utf-8")
    assert re.search(r"^- A｜(PASS|FAIL|NOT_EVALUABLE)$", text, re.M), text
    for criterion in metrics.M3_GATE:
        assert f"{criterion} =" in text, f"{criterion} 那一行没印出来：判定少一条就是少一条闸门"
    assert "plan §十 M3★" in text, text


def test_a_synthetic_batch_gate_refuses_to_say_pass(tmp_path, key):
    """替身桌的批次目录里那一份判定，必须说"不给判定"而不是 PASS。

    反向对照：`m3_gate_verdict` 已经会因 `is_synthetic` 把五条判据全留给 `ok=None`（本文件上面
    那一组用例钉着），这一条钉的是**发射口没有把它翻译松**——一个把 verdict 印成
    `NOT_EVALUABLE`、却在旁边写"闸门通过"的渲染器，比不印还糟。
    """
    _stub_batch(tmp_path, games=1, mock=True)
    text = (tmp_path / "m3_gate.md").read_text(encoding="utf-8")
    assert "｜NOT_EVALUABLE" in text, text
    assert "替身桌" in text, text
    assert "PASS" not in text, text


def test_the_batch_summary_line_points_at_the_gate(tmp_path, capsys):
    """终端那一行要说清判定写在哪儿，否则"有产物"等于"没人打开过"。"""
    assert cli.main(["batch", "--out", str(tmp_path), "--configs", "A", "--games", "1",
                     "--seed0", "7", "--mock"]) == 0
    out = capsys.readouterr().out
    assert "m3_gate.md" in out, out


# ------------------------------------------- 独立 run 出来的日志没有判定可读（#98）
def _loose(tmp_path, *, mock: bool = False, games: int = 2) -> Path:
    """打一版，把日志从臂目录里挑平、manifest 留在原地：这就是三次 `wolf run` 的磁盘形状。

    不直接读 `data/` 里那三局真日志——那个目录是 gitignored 的，用例必须在克隆出来的仓库里跑得动。
    """
    src = tmp_path / "batch"
    _stub_batch(src, games=games, mock=mock)
    dst = tmp_path / "loose"
    dst.mkdir()
    for p in sorted((src / "A").glob("*.jsonl")):
        shutil.copy(p, dst / p.name)
    assert not (dst / "run_manifest.json").exists(), "夹具没挑平：manifest 跟着过来了就不是散日志"
    return dst


def test_loose_logs_get_a_gate_without_a_batch_manifest(tmp_path, key, capsys):
    """`wolf run` 一局一局地打出来的目录，以前在任何地方都拿不到 M3 判定。

    2026-09-24 那三局真数据正是这个形状：三次 run、一个目录、没有 `run_manifest.json`。于是
    `compare` 在门口就拒（"不是 wolf batch 产出的目录"），`batch` 要重新打牌才有臂，而验收
    第 3 条要的是"全部达标**或有明确失败记录**"——判定读不到，剩下的就只有散文。
    rc 只在 PASS 时是 0：这一格钉的是"退出码跟着读数走"，桩桌到底过没过由读数自己说。
    """
    dst = _loose(tmp_path, games=2)
    games = [metrics.read_game(p) for p in sorted(dst.glob("*.jsonl"))]
    rc = cli.main(["gate", str(dst)])
    want = metrics.m3_gate_verdict(games)
    assert rc == (0 if want["verdict"] == "PASS" else 1), (rc, want["verdict"])
    text = (dst / "m3_gate.md").read_text(encoding="utf-8")
    assert f"cfg={games[0].meta['config_hash']}｜{want['verdict']}" in text, text
    for criterion in metrics.M3_GATE:
        assert f"{criterion} =" in text, f"{criterion} 那一行没印出来：判定少一条就是少一条闸门"
    assert "2 局" in text, "分母没点名：三局的判定和二十局的判定长得一样，就等着被引用错"
    assert "m3_gate.md" in capsys.readouterr().out


def test_a_gate_over_loose_stand_in_logs_refuses_to_say_pass(tmp_path, key):
    """替身桌挑平了喂给 `gate`，读到的必须还是"不给判定"，退出码也不许是 0。

    这一条与上面那条是一对：上面钉"能读到"，这里钉"读到的东西没被翻译松"。`wolf run --mock`
    是最容易顺手敲的一条命令，它的日志和真日志在磁盘上只差 `actor_kinds` 一个字段。
    """
    dst = _loose(tmp_path, games=1, mock=True)
    assert cli.main(["gate", str(dst)]) == 1
    text = (dst / "m3_gate.md").read_text(encoding="utf-8")
    assert "｜NOT_EVALUABLE" in text, text
    assert "替身桌" in text, text
    assert "PASS" not in text, text


def test_the_loose_gate_prints_the_same_numbers_as_the_batch_gate(tmp_path, key):
    """`gate` 印的五格数字必须逐字等于 `m3_gate_verdict` 的输出，不是第二份算术。

    这一页上面已经栽过两次"同一个率两处实现"（`act`/`action` 两个字段名、fallback 两个分母）。
    阈值表只有 `M3_GATE` 一份是同一族缺陷的另一半：新命令最容易犯的错，是抄一份更"顺手"的渲染。
    """
    from wolfengine.batch import _cell

    dst = _loose(tmp_path, games=2)
    assert cli.main(["gate", str(dst)]) in (0, 1)
    text = (dst / "m3_gate.md").read_text(encoding="utf-8")
    v = metrics.m3_gate_verdict([metrics.read_game(p) for p in sorted(dst.glob("*.jsonl"))])
    for key_name, c in v["criteria"].items():
        line = (f"  - {key_name} = {_cell(c['value'])}（需 {c['comparator']} "
                f"{c['threshold']}，n={c['n']}）"
                + {True: "达标", False: "未达标", None: "无读数"}[c["ok"]])
        assert line in text, (key_name, line, text)
    assert v["truncation"]["note"] in text, "截断那一格是判定的邻居，抄漏就等于把天花板抹了"


def test_a_folder_of_two_config_tables_gets_no_gate(tmp_path, key, capsys):
    """两张预算表的日志混在一个目录里，`gate` 要拒判，而不是把两批并进一个分母。

    批次侧靠 `run_manifest.json` 分臂，松散日志身上只剩 `meta.config_hash`。合并两张表算
    `passivity_rate` 会把"处理"和"噪声"搅在一起——这正是 `compare` 的轴守卫在门口拦的东西。
    这里靠改一份日志的 config_hash 造出分歧：真日志里它是 `--set` 拧出来的，夹具不必再打一批。
    """
    import json

    dst = _loose(tmp_path, games=2)
    _, b = sorted(dst.glob("*.jsonl"))
    lines = b.read_text(encoding="utf-8").splitlines()
    head = json.loads(lines[0])
    original = head["meta"]["config_hash"]
    forged = "0123456789ab"
    head["meta"]["config_hash"] = forged
    b.write_text(json.dumps(head, ensure_ascii=False) + "\n" + "\n".join(lines[1:]) + "\n",
                 encoding="utf-8")
    assert cli.main(["gate", str(dst)]) == 2
    err = capsys.readouterr().err
    assert forged in err and original in err, f"两句 hash 都要点名，才知道该把哪一个移走：{err}"
    assert "Traceback" not in err, err
    assert not (dst / "m3_gate.md").exists(), "拒判还落盘，读的人就拿到一份只判了一半的表"


# ------------------------------------------------- 一份文件里没有一局，却被数成一局（#99）
_NO_TABLE = metrics.Game(path=Path("/tmp/no-table.jsonl"), meta={}, events=[])
#: `write_meta()` 落在第一条事实之前，于是"开局后被打断"正好留下这一种字节。
_OPENED_ONLY = metrics.Game(path=Path("/tmp/opened-only.jsonl"),
                            meta={"game_id": "opened-only"}, events=[])
#: 同一形状，但页眉登记的是真端点：`is_synthetic` 这道门**放行**它，于是它从"多一局"变成了"多一局可用的"。
_OPENED_ONLY_LLM = metrics.Game(path=Path("/tmp/opened-only-llm.jsonl"),
                                meta={"game_id": "opened-only-llm", "actor_kinds": ["llm"]},
                                events=[])


def test_a_hollow_llm_log_does_not_join_the_usable_pool():
    """`["llm"]` 页眉的空文件比 mock 那种更坏：它进了 `usable`，于是剔除数变成**负数**。

    18:40:24Z 拿真日志目录实测（两局真的 + 一份只有页眉的 `["llm"]` + 一份 0 字节）：闸门印
    `n_games_usable=3` 而 `n_games=2`、`n_synthetic_excluded=-1`。算术是 `played - usable`，
    一个按" played 之外"的集合去减"games 之内"的集合——两个分母不同底，减出来的不是任何数量。
    这一格要是被哪个渲染器拿去印"剔了几局"，读者看到的是"这一臂剔了 -1 局"。
    """
    v = metrics.m3_gate_verdict(_lively_batch() + [_OPENED_ONLY_LLM])
    assert v["hollow"]["n"] == 1, v["hollow"]
    assert v["n_games"] == 2 and v["n_games_usable"] == 2, v
    assert v["n_synthetic_excluded"] == 0, "剔除数被一个不在被减集合里的文件顶成了负数"
    assert v["n_games_usable"] <= v["n_games"], "可用局不许多于局"


def test_a_log_with_events_and_no_manifest_line_is_still_a_game():
    """`meta_notice` 管的是"叫不出这是哪一局"，不是"这一局不存在"——两种破损不能共用一个判据。

    #99 把 `hollow_notice` 接进闸门时用的是 `meta_notice(meta) or empty_notice(...)`，于是"页眉被删掉
    但事件还在"的文件（人手改过、或 `#54` 那两条 manifest 被砍掉一条）从五条判据的分母里整个消失：
    `n_games=0`、`NOT_EVALUABLE`。那一局真打了，事件就在文件里；页眉缺失有它自己的判据和自己的句子
    （三个给人看的出口从 #52 起就在说"说不出它是哪一局"），不该被翻译成"这里没有局"。
    """
    unnamed = metrics.Game(path=Path("/tmp/unnamed.jsonl"),
                           meta={"actor_kinds": ["llm"]}, events=_round(1, 1, LIVELY))
    v = metrics.m3_gate_verdict([unnamed])
    assert v["n_games"] == 1 and v["hollow"]["n"] == 0, v
    assert v["hollow"]["note"] == "" and v["hollow"]["paths"] == [], v["hollow"]
    assert v["criteria"]["passivity_rate"]["n"] == 9, v["criteria"]
    # 反向对照：同样的事件数、页眉写着局号，和事件全空，两格都必须照旧。
    named = metrics.Game(path=unnamed.path, meta={"game_id": "unnamed", **unnamed.meta},
                         events=unnamed.events)
    assert metrics.m3_gate_verdict([named])["n_games"] == 1
    assert metrics.m3_gate_verdict([dataclasses.replace(unnamed, events=[])])["n_games"] == 0


def test_a_file_without_a_manifest_is_not_counted_as_a_game():
    """0 字节的日志不是一局：`本批 N 局` 数的是文件，不是里面有没有东西。

    2026-09-24T18:20Z 实测：`wolf gate` 拿到一份空文件时印的是"本批 1 局全为替身桌"——既凭空造了
    一局，又替它编了一个没人登记的座位。这两句话在 `events.py` 里早就有唯一的出处
    （`meta_notice`/`empty_notice`），三个给人看的出口在用，闸门这一侧一个读者都没有。
    """
    base = metrics.m3_gate_verdict(_lively_batch())
    assert base["verdict"] == "PASS", base
    v = metrics.m3_gate_verdict(_lively_batch() + [_NO_TABLE])
    assert v["n_games"] == 2, "空文件被数进了局的分母"
    assert v["hollow"]["n"] == 1 and meta_notice({}) in v["hollow"]["note"], v["hollow"]
    assert v["criteria"] == base["criteria"], "多一个空文件挪动了判据——它没有事件，本不该参与任何算术"
    assert v["verdict"] == "PASS"


def test_a_log_that_only_opened_is_not_a_second_stand_in_table():
    """只有开局记录的那一份，也不许被数成"又一局替身桌"。

    它跟空文件的区别是 `empty_notice` 和 `meta_notice` 的区别（互斥，两句不会同时出现），
    共同点是两样都没有可判的东西。这里两局里只有一局是真的替身桌，所以那句"本批 N 局全为替身桌"
    的 N 必须是 1。
    """
    v = metrics.m3_gate_verdict([_game(_round(1, 1, LIVELY), synthetic=True), _OPENED_ONLY])
    assert v["n_games"] == 1, v
    assert "本批 1 局全为替身桌" in v["note"], v["note"]
    assert empty_notice([], {"game_id": "opened-only"}) in v["hollow"]["note"], v["hollow"]
    assert v["verdict"] == "NOT_EVALUABLE"


def test_a_folder_of_such_files_says_there_is_no_game_rather_than_a_stand_in_table():
    """全是空文件时，闸门要说"没有一局"，不能说"这些桌子都是替身桌"。

    后者是一句关于**谁在打牌**的假话：这些文件连座位表都没有。`is_synthetic` 是关门用的
    （没登记就不许进结论），不能反过来当成"登记成了替身"。
    """
    v = metrics.m3_gate_verdict([_NO_TABLE, _OPENED_ONLY])
    assert v["n_games"] == 0 and v["hollow"]["n"] == 2, v
    assert "替身桌" not in v["note"], v["note"]
    assert v["verdict"] == "NOT_EVALUABLE"


def test_the_headline_counts_the_files_it_judged_not_the_files_it_was_handed(tmp_path, key):
    """标题里"几局"和"几份"必须说同一批文件：份数不能另数一遍交进来的行。

    这一条是给 `emit_gate` 的取数出处补的牙，不是给一个新行为。M4（把 `n_files` 改成
    `len(played)`）暴露出 `n_files` 以前**一个读者都没有**——标题里那个份数由 `len(rows)` 独立数
    出来，所以 M9（把接线退回去）在整套 853 条里抓不到。把两处数法掰开才有红可看：一份行不属于被点
    的臂，`games` 按臂过滤不会读它，于是接线前印"（3 份日志）"（18:51:46Z 现跑：`A 2 局（3 份日志）`，
    而这一屏只判了 A 臂那两份），接线后判据与份数同源。
    """
    src = tmp_path / "batch"
    _stub_batch(src, games=3)
    logs = sorted((src / "A").glob("*.jsonl"))
    assert len(logs) == 3, logs
    rows = [{"arm": "A", "path": str(p)} for p in logs[:2]] + [{"arm": "B", "path": str(logs[2])}]
    out = batch.emit_gate(src, ["A"], rows, label="探针")
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "A 2 局（2 份日志）" in text, text.splitlines()[2]
    assert "3 份" not in text, "份数数的是交进来的行，而这一屏只判了 A 臂那两份"


def test_a_log_without_a_config_table_is_not_a_second_config_table(tmp_path, key, capsys):
    """一份没登记配置的文件，不许把同目录里两局好日志一起拒判。

    `cmd_gate` 的分臂守卫取的是 `meta.config_hash` 的集合，于是空文件贡献了一个 `None`——
    它把"没有表"说成"第二张表"，还报出 `混着 2 张配置表（None、fb3e…）`。这正是 `#51` 那一族：
    一个坏字节让整目录读起来像"数据不可比"。
    """
    dst = _loose(tmp_path, games=2)
    (dst / "interrupted.jsonl").write_text("", encoding="utf-8")
    assert cli.main(["gate", str(dst)]) in (0, 1), "该判的目录被一份空文件拒了"
    text = (dst / "m3_gate.md").read_text(encoding="utf-8")
    assert "None" not in text, text
    assert "2 局" in text and "3 份日志" in text, "局数和份数不等时，标题里就要看得见那两个数"
    assert meta_notice({}) in text, "闸门得说清少的那一份是什么，而不是安静地少一局"


def test_the_gate_names_which_files_held_no_game(tmp_path, key, capsys):
    """"不是局的文件：N 份"要点名是那 N 份，否则那一份字节留在目录里会继续污染下一批。

    `#101`：`hollow.paths` 从 `#99` 起就在指标里算出来，但两个出口都只印 `note`，于是这个字段
    **没有读者**——把 `paths` 改成恒空列表，整套 858 条全绿（19:34:39Z 现测）。同一条口径在
    `docs/comparison.md` 的退化局那一节早就写死了："只报计数不算点名"，拿着计数开不了那一局的
    转录；这里拿着份数也删不掉那一份文件。
    """
    dst = _loose(tmp_path, games=2)
    (dst / "interrupted.jsonl").write_text("", encoding="utf-8")
    assert cli.main(["gate", str(dst)]) in (0, 1)
    text = (dst / "m3_gate.md").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if meta_notice({}) in l)
    assert "（文件：interrupted.jsonl）" in line, f"那一行没点名是哪一份：{line}"


def test_two_real_config_tables_still_refuse_even_with_a_tableless_file_there(tmp_path, key,
                                                                             capsys):
    """拒判那一条不能因为上面那个修复而变松：两张表还是要拒，只是不再把 `None` 当成一张。

    这一条是上一条的对照组。两边都在，"忽略无表文件"才不是一句把守卫改宽的借口。
    """
    import json

    dst = _loose(tmp_path, games=2)
    _, b = sorted(dst.glob("*.jsonl"))
    lines = b.read_text(encoding="utf-8").splitlines()
    head = json.loads(lines[0])
    original, forged = head["meta"]["config_hash"], "0123456789ab"
    head["meta"]["config_hash"] = forged
    b.write_text(json.dumps(head, ensure_ascii=False) + "\n" + "\n".join(lines[1:]) + "\n",
                 encoding="utf-8")
    (dst / "interrupted.jsonl").write_text("", encoding="utf-8")
    assert cli.main(["gate", str(dst)]) == 2
    err = capsys.readouterr().err
    assert forged in err and original in err, err
    assert "None" not in err, f"没有表的文件不该被叫成一张表：{err}"
    assert not (dst / "m3_gate.md").exists()


# ------------------------------------------------------------------ 一整轮没人开口（#95）
def test_a_round_of_nothing_said_gives_no_opening_reading():
    """空白不是"开头全不一样"，也不是"全塌缩"，是没有读数。

    炸点不是这里编出来的：16:02:06Z 一具 `wolf batch --configs A,B` 跑完，批次目录里新加的
    那份闸门判定把 `collapse_round`（`metrics.py:73`）的除法顶成 `ZeroDivisionError`（一整轮的发言全是空串），
    子进程交回 traceback。同一只手在 `compare` 上本来就会炸，只是那条路从来没有用例喂过
    "整轮没人说话"的日志。
    """
    assert metrics.opening_distinct_rate(["", "   ", "\n"]) is None
    assert metrics.opening_distinct_rate([]) is None, "空列表和空话是同一件事：没有可比的开头"
    # 反向对照：只有一个人空手，均值不该被拖走，分母只数开了口的那几个。
    assert metrics.opening_distinct_rate(["", "我先不表态，等听完再判断。", "这轮我选择保留看法。"]) == 1.0


def test_a_silent_round_leaves_the_gate_without_a_reading_but_keeps_the_measured_ones():
    """闸门混着"量到了的失败"和"没量到"时，两格都要照实写，且 precedence 不变。

    一轮三句空话（指派的是 listen，所以被动率**量得出来**：1.0，主判据必须红）；两条风格判据
    没有可比的内容，只能给 `ok=None`。如果实现把"没读数"当成 1.0（修之前那条除零崩溃的另一端
    就是它），这批就会被说成"塌缩判据达标"——而这一桌其实一句话都没说。
    """
    blank = [_speech(i, i, "", "listen") for i in range(1, 4)]
    out = metrics.m3_gate_verdict([_game(blank)])
    assert out["criteria"]["opening_distinct_rate"]["value"] is None, out["criteria"]
    assert out["criteria"]["opening_distinct_rate"]["ok"] is None, out["criteria"]
    assert out["criteria"]["collapse_round"]["value"] is None, out["criteria"]
    assert out["criteria"]["passivity_rate"]["value"] == 1.0, out["criteria"]
    assert out["verdict"] == "FAIL" and out["failed"] == ["passivity_rate"], out
    # 视图那一格同一条规则：整轮的均值不能被一个 None 拖成崩溃，也不能把它算成 0。
    view = metrics.m5_style_collapse(blank)
    assert view["rounds"][0]["opening_distinct_rate"] is None, view["rounds"]
    assert view["rounds"][0]["collapse_round"] is None, view["rounds"]
    assert view["opening_distinct_mean"] is None, view
    assert view["collapse_round_mean"] is None, view
    assert view["worst_round"] is None, "没有可比的轮，就不该有一轮被指成最塌缩的"
    mixed = metrics.m5_style_collapse(blank + _round(2, 11, LIVELY))
    assert mixed["rounds"][0]["opening_distinct_rate"] is None, mixed["rounds"]
    assert mixed["opening_distinct_mean"] == 1.0, mixed
    assert mixed["worst_round"]["day"] == 2, mixed["worst_round"]


# 一局里两轮不等长的发言：九人轮五个人被动（0.5556）+ 单人轮不被动（0.0）。
# 每轮速率再取均值 = 0.2778，按次合并 = 5/10 = 0.5。阈值是 <0.4，两个数一个在下一个在上。
_NINE = (_round(1, 1, MEASURED_PASSIVE[:5], act="listen")
         + _round(1, 6, LIVELY[:4], act="accuse"))
_ONE = _round(2, 11, LIVELY[:1], act="accuse")


def test_the_passivity_headline_pools_turns_instead_of_averaging_rounds():
    """主判据在单局视图里也要按次合并：一个单人轮不能和一个九人轮同权重。

    闸门自己的 docstring 就写着 "Denominators are pooled, not averaged… a mean of per-game means
    would let a 3-turn game count as much as a 90-turn one"（那说的是局），而 `m5_style_collapse`
    在轮这一层犯的正是它说的那个错。修法不是把闸门拉低，是把这一格换成同一次算术。
    """
    view = metrics.m5_style_collapse(_NINE + _ONE)
    assert [r["passivity_rate"] for r in view["rounds"]] == [0.5556, 0.0], view["rounds"]
    assert view["passivity_pooled"] == 0.5, "每轮均值会把这一格读成 0.2778"


def test_the_audit_view_and_the_gate_report_the_same_primary_criterion():
    """`wolf audit` 与 `wolf gate` 对同一局不许各算一遍主判据。

    19:51:04Z 现测（改动前）：这十个 turn 在 `wolf audit` 的 `m5_style.passivity_mean` 里是
    `0.2778`，在 `wolf gate` 的 `criteria.passivity_rate.value` 里是 `0.5`（n=10，判 FAIL）。
    真日志上同一个差是 1.83 倍——`data/real-20260924/…g00000301` 的三轮规模 9/2/7，每轮速率
    0.1111、0.5、0.0，audit 读 0.2037、闸门读 0.1111（19:49:01Z）。单局视图旁边还印着它自己那格
    `gate`（`passivity_rate<0.4`），拿一个不同分母的数去比那个阈值，就是把一桌沉默读成达标。

    另外两条风格判据今天就是相等的（两处都是按轮取均值），放进这一条是钉子：共用一次算术之后，
    谁把它们再拆开就该红。
    """
    events = _NINE + _ONE
    view = metrics.m5_style_collapse(events)
    crit = metrics.m3_gate_verdict([_game(events)])["criteria"]
    assert view["passivity_pooled"] == crit["passivity_rate"]["value"], "同一个主判据两个分母"
    assert view["collapse_round_mean"] == crit["collapse_round"]["value"]
    assert view["opening_distinct_mean"] == crit["opening_distinct_rate"]["value"]


# ---- 一轮只有一张话筒（#103）
def test_a_round_with_one_microphone_gives_no_pairwise_reading():
    """`collapse_round` 按定义是"同轮内**两两** Jaccard 的均值"（plan §8 M5 那格）：开口人数不到
    两个就没有一对可比，而"没有一对"不是"每一对都不像"。

    20:13:16Z 现测（改动前）：`collapse_round(["随便说一句"])` 是 `0.0`、
    `opening_distinct_rate(["随便说一句"])` 是 `1.0`——两个都是阈值方向的**通过**值（`< 0.35`
    与 `> 0.8`），而它们量到的东西是"没有可比的"。同一句话在 `[]` 那一侧还不一致：
    `collapse_round([])` 给 `0.0`，`opening_distinct_rate([])` 给 `None`（`#95` 立的规矩），
    两兄弟对同一件事两个答案。`#95` 只挡了"整轮没人说话"，没挡"整轮只有一个人说话"。
    """
    solo = "1号你凭什么投我？你明明在攻击我制造对立。"
    assert metrics.collapse_round([solo]) is None
    assert metrics.opening_distinct_rate([solo]) is None
    assert metrics.collapse_round([solo, ""]) is None, "一个人开口 + 一个人空手：还是一对都没有"
    assert metrics.collapse_round([]) is None, "空列表和一个人说话是同一件事：没有可比的一对"


def test_a_one_microphone_round_is_left_out_of_the_style_denominators():
    """单人轮不进分母，也不把九人轮的读数稀释一半。

    夹具照着真日志摆：九个人的一整轮 + 一个 PK 轮（两条发言，其中一条是空串，所以只有一个人开口）。
    20:13:16Z 现测（改动前）：闸门 `criteria.collapse_round` 是 value `0.0216`、n `2`，而九人轮
    自己那格是 `0.0433`——那一半是被一个"没有可比的"轮换来的。真日志同一件事在
    `data/real-20260924/…g00000301` 上是 `0.0089` 对 `0.0134`（20:12:07Z，三轮 9/2/7，PK 轮
    只有一个人开口）。`n` 这一格以前没有行为读者：读它的两条断言里，带 `== 4` 的那条夹具四轮全有
    读数（`len(per)` 与"有读数的轮数"当时是同一个数），带沉默轮的那条只断言 `value is None`——
    `#102` 电池里的 M4 就是从这里过去的（它只红了行号闸门，`#77` 点名的假证人）。
    """
    nine = _round(1, 1, LIVELY, act="accuse")
    pk = _round(2, 11, ["", LIVELY[0]], act="listen")
    crit = metrics.m3_gate_verdict([_game(nine + pk)])["criteria"]
    assert crit["collapse_round"]["n"] == 1, "只有九人轮那一次比得出两两相似度"
    assert crit["collapse_round"]["value"] == pytest.approx(0.0433, abs=5e-5), crit["collapse_round"]
    assert crit["opening_distinct_rate"]["n"] == 1
    assert crit["passivity_rate"]["n"] == 11, "主判据照旧按次数：空手那条也是一个 turn，它量的是行为"

    view = metrics.m5_style_collapse(nine + pk)
    assert [r["collapse_round"] for r in view["rounds"]] == [0.0433, None], view["rounds"]
    assert view["collapse_round_mean"] == crit["collapse_round"]["value"], "两个出口一份算术"
    assert view["worst_round"]["n"] == 9, "没有读数的轮不许被指成最塌缩的那一轮"


# ---- 第三条措辞尺子还在旧地板上（#104）
def test_the_third_wording_ruler_shares_the_two_microphone_floor():
    """`dup_exact6_rate` 问的是"这份发言有没有和**别的**发言共享 ≥6 字"，一份发言答案永远是"没有"。

    20:45:51Z 现测（改动前）：`shared_substring_rate([solo])` 是 `0.0`、
    `shared_substring_rate([solo, ""])` 是 `0.0`、`shared_substring_rate([])` 也是 `0.0`——`#103`
    给另外两把尺子立的地板（开口人数不到两个 → 没有读数）漏掉了这一把，于是它交回的还是那个
    通过方向的定义值。第二半更难看：它的分母是"条目数"而不是"开口人数"，所以两个人**逐字复读**、
    七个人空手的一轮读成 `0.2222`，而"这一轮说话的人全在复读"是 `1.0`——沉默在这里不是"没重复"，
    它把措辞尺子的分母撑大了（还是 `#95` 说的那件事，第三条尺子上没人执行）。
    """
    solo = "1号你凭什么投我？你明明在攻击我制造对立。"
    assert metrics.shared_substring_rate([solo]) is None
    assert metrics.shared_substring_rate([solo, ""]) is None, "一个人开口 + 一个人空手：还是没有可比的一对"
    assert metrics.shared_substring_rate([]) is None, "整轮没人说话：与 `#95`、`#103` 同一口径"
    echo = "今天我先说结论，1号昨天的行为最可疑。"
    assert metrics.shared_substring_rate([echo, echo] + [""] * 7) == 1.0, \
        "开口的那两个逐字复读，读数就该是 1.0，不是被七个空手稀释成的 0.2222"


def test_dup_exact6_mean_has_a_reader_and_skips_the_round_it_cannot_measure():
    """`dup_exact6_mean` 是 `wolf audit` 的 `m5_style` 里的一格，而全仓库没有一条断言读过它。

    20:08:45Z 现读那四个键：`collapse_round_mean`、`dup_exact6_mean`、`opening_distinct_mean`、
    `passivity_pooled`。`#102` 电池里"M5 `dup_exact6_mean` 取错列"那一具是当场兑现的 MISSED
    （把这一格换成另一列，863 条全绿）——没有读者的格就是这样的。这一条补那个读者，顺带钉地板：
    夹具是"九个人里两个逐字复读"的一轮（`0.2222`）加一个只有一张话筒的 PK 轮，均值必须还是
    `0.2222`，不是把那个没有读数的轮按 `0.0` 收进来之后现测的 `0.1111`（20:45:51Z，改动前）。
    """
    echo = "今天我先说结论，1号昨天的行为最可疑。"
    nine = _round(1, 1, [echo, echo] + LIVELY[2:], act="accuse")
    pk = _round(2, 11, ["", LIVELY[0]], act="listen")
    view = metrics.m5_style_collapse(nine + pk)
    assert [r["dup_exact6_rate"] for r in view["rounds"]] == [0.2222, None], view["rounds"]
    assert view["dup_exact6_mean"] == pytest.approx(0.2222, abs=5e-5), view["dup_exact6_mean"]
    assert view["collapse_round_mean"] == pytest.approx(0.0539, abs=5e-5), \
        "两格出自同一轮算术的两个列：取错列时这一条与上一条不可能同时绿"


# ---- 回答被自家预算切断（#92）
def test_the_gate_counts_the_answers_our_own_cap_cut_off():
    """M3 的两条风格判据量的可以是一句被 `max_tokens` 拦腰截断的话，而这件事以前没人说。

    120/173：真端点上六成的回答是被切断的（`data/real-20260924` 的普查），"我很想指控但…"这种
    半句在 `collapse_round` 眼里像模板，在 `passivity_rate` 眼里像没点人。这一条钉的是闸门旁边
    那一格读得出来：按**次**数，不按时相，且指名切断最多的那个相。

    那三次投票各自带 `finish_reason`，是为了让 `per_phase` 有两个键：只放一个相进去时
    `worst_phase` 的 max 和 min 是同一个值，把判据换成"取最少的那个相"也读不出来（第一轮电池
    里的 M3 就是这样活下来的——等价变异，不是没人读的字段）。
    """
    events = (_round(1, 1, LIVELY, finish="length")
              + [_vote(11 + i, i, finish="length" if i == 1 else "stop") for i in range(1, 4)])
    cut = metrics.m3_gate_verdict([_game(events)])["truncation"]
    assert (cut["n_calls"], cut["n_recorded"], cut["n_cut"]) == (12, 12, 10), cut
    assert cut["rate"] == round(10 / 12, 4) and cut["worst_phase"] == "day_speech", cut
    assert "截断" in cut["note"] and "day_speech" in cut["note"], cut


def test_an_unrecorded_finish_reason_is_not_read_as_nobody_was_cut():
    """端点没报 `finish_reason` 时给"读不出来"，不给"0 次"——后者是一句关于模型的话。

    `_lively_batch` 的夹具刻意不带这个字段，所以这一格同时也是"替身桌/老日志"那一支：`n_calls`
    仍然数得出来，缺的是分母。
    """
    cut = metrics.m3_gate_verdict(_lively_batch(1))["truncation"]
    assert cut["n_calls"] == 18 and cut["n_recorded"] == 0, cut
    assert cut["rate"] is None and cut["worst_phase"] is None, cut
    assert "没有一次" in cut["note"] and "不是 0" in cut["note"], cut


def test_the_cut_rate_divides_over_only_the_calls_that_answered_it():
    """一半调用报了 `finish_reason`、一半没报：比率按报了的算，并注明覆盖了多少。

    和 `fill_rate` 的 `asked_calls` 同一条规则：分母里不许混进"没被问到的"。反过来把没报的当成
    没被切断，就等于让一个不报这个字段的端点自己把 60% 的切断洗成 30%。
    """
    events = (_round(1, 1, LIVELY[:4], finish="length")
              + _round(1, 11, LIVELY[4:8]) + [_vote(21, i) for i in range(1, 3)])
    cut = metrics.m3_gate_verdict([_game(events)])["truncation"]
    assert (cut["n_calls"], cut["n_recorded"], cut["n_cut"]) == (10, 4, 4), cut
    assert cut["rate"] == 1.0, cut
    assert "4/10" in cut["note"] or "10 次里 4 次" in cut["note"], cut


def test_a_batch_that_was_never_cut_says_so_on_the_gate_file(tmp_path, key):
    """正反两对：桩线报 `stop` 的批次要说"无一被切断"，报 `length` 的批次要说被切断了。

    这一格以前在产物里不存在（`truncations` 只有 `wolf audit` 的 JSON 读得到），所以两种说法都要
    钉住——只钉"有切断时印出来"会让一个永远印"无一被切断"的渲染器绿着。
    """
    _stub_batch(tmp_path / "clean", games=1)
    clean = (tmp_path / "clean" / "m3_gate.md").read_text(encoding="utf-8")
    assert "无一被" in clean, clean

    _stub_batch(tmp_path / "cut", games=1, finish="length")
    cut = (tmp_path / "cut" / "m3_gate.md").read_text(encoding="utf-8")
    assert "次回答被 `max_tokens` 截断" in cut, cut
    assert "无一被" not in cut, cut
