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
from pathlib import Path

import pytest

from wolfengine import metrics
from wolfengine.events import Event, Kind

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
            lat: float = 4.2, assigned: object = _NO_KEY,
            attempts: list[dict] | None = None, **meta: object) -> Event:
    req: dict[str, object] = {"total_tokens_est": 900}
    if assigned is not _NO_KEY:
        # 三种形状都得能分开造出来：`_NO_KEY` 是"`#68` 之前的日志，这一格根本没落盘"，
        # `assigned=None` 是"量过了、这一轮法官没有指派"，字符串才是"指派了 X"。
        req["assigned_act"] = assigned
    return Event(seq=seq, kind=Kind.SPEECH, day=day, phase="day_speech", visibility="all",
                 actor=seat,
                 payload={"seat": seat, "text": text, "act": act,
                          "meta": {"rung": 0, "fallback": 0, **meta}},
                 request=req,
                 attempts=attempts or [],
                 response={"latency_s": lat, "completion_tokens": 60})


def _round(day: int, first_seq: int, texts: list[str], act: str = "accuse",
           **meta: object) -> list[Event]:
    return [_speech(first_seq + i, i + 1, t, act, day=day, **meta)
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
