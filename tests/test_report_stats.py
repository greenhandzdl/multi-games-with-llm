"""M7 的统计层：配对检验、按局 cluster bootstrap、treatment-axis 守卫、漂移 canary。

这一层的存在理由全部来自"数字会被拿去下结论"：48 局的胜率 CI 半宽 ≈±14pp（plan §8 M1
自己写的），同局 90 条发言共享牌局与死法所以**不是独立样本**（§8 第 4 条），局域网端点
是别人会重启的共享资源（R7）。这三件事各自都有一个会假装没事的实现方式，所以每一件都
钉成断言。

数值全部手算可验：McNemar 是 `math.comb` 精确二项。曾经还有一句"cluster bootstrap 的 deff
在局内完全相关的构造数据上应当等于局内样本数"——那条断言挂的是 `cluster_bootstrap_rate`，
`#155` 把它作为零生产调用者的名字删掉了（见 `docs/iterations.md` 那一节），删的同时这个
理论值锚点一起没了；剩下的配对差只钉两个 CI 的宽度关系。**这是本层的已知缺口，不是遗漏。**
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from wolfengine import metrics, report
from wolfengine.config import Config


# ------------------------------------------------------------------ McNemar exact
def test_mcnemar_is_the_exact_two_sided_binomial_not_a_normal_approximation():
    """b=8, c=1 ⇒ p = 2·P(X≤1 | n=9, ½) = 2·(1+9)/512。

    正态近似在这个 n 上会给出一个看起来更"显著"的数，而 48 局批次的 b+c 恰好就落在
    近似最不可信的区间，所以这里钉的是精确值。
    """
    assert report.mcnemar_exact(8, 1) == pytest.approx(20 / 512, abs=1e-12)
    assert report.mcnemar_exact(0, 0) == 1.0, "no discordance is no evidence, not p=0"
    assert report.mcnemar_exact(1, 0) == 1.0, "one discordant pair cannot be significant"
    # symmetry: swapping which config won the discordant pairs cannot change p
    assert report.mcnemar_exact(9, 2) == pytest.approx(report.mcnemar_exact(2, 9), abs=1e-12)
    assert report.mcnemar_exact(30, 6) < 0.001


def test_a_batch_too_small_to_say_so_says_so():
    """plan §8 第 3 条：`b+c < 6` 必须打印"本批统计力不足"，不许用 p 值盖过它。"""
    weak = report.mcnemar(4, 1)
    assert weak["underpowered"] is True and weak["n_discordant"] == 5
    assert "统计力不足" in weak["verdict"]
    strong = report.mcnemar(8, 1)
    assert strong["underpowered"] is False and strong["p"] < 0.05
    assert "统计力不足" not in strong["verdict"]


def test_paired_win_rate_drops_undecided_games_from_the_denominator_and_reports_it():
    """配对表里一方 abort 了，这一对既不是好人的证据也不是狼人的证据——但它必须被*数出来*，
    否则 48 局的批次实际只有 31 对在算，而报告写着 48。"""
    pairs = [("good", "good"), ("good", "wolf"), ("wolf", "good"), ("good", "wolf"),
             ("good", None), (None, "wolf"), ("draw", "good")]
    out = report.paired_win_test(pairs)
    assert (out["n_pairs"], out["n_usable"], out["n_dropped"]) == (7, 4, 3)
    assert (out["b"], out["c"]) == (2, 1)
    assert out["underpowered"] is True


# ------------------------------------- cluster bootstrap (配对速率差)
def test_rate_difference_resamples_pairs_not_sides():
    """两配置逐局配对：每局的差在 +0.1 上下小幅摆动，而局间基线本身剧烈波动。

    按"两侧各自独立重采样"做，局间基线波动会被当成处理效应的不确定性，CI 宽到把 0 包
    进去——配对设计的全部意义就此作废。所以这里钉的是两个 CI 的宽度关系，不是某一个数。
    """
    base = [8, 42, 20, 33, 12, 27, 45, 5, 38, 16]
    offs = [5, 6, 4, 7, 5, 6, 4, 5, 6, 5]  # per-game effect ≈ 0.1, small spread
    a = [(k, 50) for k in base]
    b = [(k + o, 50) for k, o in zip(base, offs)]
    paired = report.cluster_bootstrap_rate_diff(a, b, B=2000, seed=3)
    unpaired = report.cluster_bootstrap_rate_diff(a, b, B=2000, seed=3, paired=False)
    assert paired["diff"] == pytest.approx(sum(offs) / 10 / 50, abs=1e-9)
    w = lambda d: d["ci"][1] - d["ci"][0]  # noqa: E731
    assert w(paired) < 0.5 * w(unpaired), (paired["ci"], unpaired["ci"])
    assert paired["ci"][0] > 0, "a constant positive shift must not straddle 0"
    assert unpaired["ci"][0] < 0 < unpaired["ci"][1], "配对的意义就在于此"


# ------------------------------------------------------------- treatment-axis guard
def test_the_axis_guard_accepts_only_the_declared_difference():
    a = Config()
    b = dataclasses.replace(Config(), temperature=0.6)
    out = report.axis_diff(a, b, axis=("temperature",))
    assert out.ok is True and out.undeclared == {} and out.rejected == {}


def test_the_axis_guard_itemises_every_undeclared_difference():
    """拒绝之外还要打印逐项差异（plan §8 第 2 条）：只说"不可比"的报告第二天会被同一个
    人再跑一遍，指出是哪一格变了才是可修的东西。"""
    a = Config()
    b = dataclasses.replace(Config(), temperature=0.6, max_tokens_speech=200)
    out = report.axis_diff(a, b, axis=("temperature",))
    assert out.ok is False
    assert out.undeclared == {"max_tokens_speech": (a.max_tokens_speech, b.max_tokens_speech)}
    text = out.render(a.config_hash(), b.config_hash())
    assert a.config_hash() in text and b.config_hash() in text
    assert "max_tokens_speech" in text


def test_a_nested_budget_counts_as_its_own_axis():
    """regions/tokens 是嵌套 dataclass：整块相等才算相等，改一个子字段必须被点名为
    `regions.b2`，否则报告里只会写着"budgets differ"而看不出是哪一格。"""
    a = Config()
    b = dataclasses.replace(Config(), regions=dataclasses.replace(Config().regions, b2=900))
    out = report.axis_diff(a, b, axis=("temperature",))
    assert set(out.undeclared) == {"regions.b2"}
    assert report.axis_diff(a, b, axis=("regions.b2",)).ok is True


def test_declaring_a_parent_axis_covers_every_leaf_under_it():
    """`--axis regions` 的自然读法是"整块预算表就是处理轴"，逐格写 12 个名字不是要求。"""
    a = Config()
    b = dataclasses.replace(Config(), regions=dataclasses.replace(Config().regions, b2=900))
    assert report.axis_diff(a, b, axis=("regions",)).ok is True


def test_an_axis_that_is_only_a_prefix_of_real_fields_covers_nothing():
    """反向的一半：前缀匹配少了那个点，`max_tokens` 就会顺手放行
    `max_tokens_speech` 与 `max_tokens_action` 两个字段。守卫太严只是麻烦，太松是会出结论的。"""
    a = Config()
    b = dataclasses.replace(Config(), max_tokens_speech=200)
    out = report.axis_diff(a, b, axis=("max_tokens",))
    assert out.ok is False and "max_tokens_speech" in out.undeclared


def test_a_human_seat_in_one_arm_and_not_the_other_is_rejected_flatly():
    """plan §十五：真人不可 seed 控制，混进配对表会直接污染 McNemar 的配对前提。
    这不是"另一个轴"，声明了也不给过。"""
    a = Config()
    b = dataclasses.replace(Config(), actor_kinds=("llm",) * 8 + ("human",))
    out = report.axis_diff(a, b, axis=("actor_kinds",))
    assert out.ok is False and set(out.rejected) == {"actor_kinds"}


def test_switching_endpoint_or_model_is_never_a_legitimate_axis():
    """换 model 就不是同一个被试了，McNemar 的配对假设彻底失效。"""
    a = Config()
    b = dataclasses.replace(Config(), model="something-else")
    out = report.axis_diff(a, b, axis=("model",))
    assert set(out.rejected) == {"model"} and out.ok is False


# ------------------------------------------------- the inert list's second reader
# `batch.apply_overrides` 在门口拒过记账字段，但 `compare` 拿的是 manifest 里记下来的配置
# （`batch.py:500`：`man["arms"][a]["config"]`），手改 manifest 不经过那道门。文档写着"两处读
# 同一个常量"，所以这一组钉的是**第二个读者在不在**，不是门口那句报错的措辞。
INERT_CELLS = Config().inert_fields + Config().inert_leaves


def _bump(config: Config, path: str) -> Config:
    """把一格挪动一点，绕开 `apply_overrides`：这一组要模拟的是 manifest 已经写着它了。"""
    head, _, leaf = path.partition(".")
    if not leaf:
        return dataclasses.replace(config, **{head: not getattr(config, head)})
    child = getattr(config, head)
    inner = dataclasses.replace(child, **{leaf: getattr(child, leaf) + 1})
    return dataclasses.replace(config, **{head: inner})


@pytest.mark.parametrize("path", INERT_CELLS)
def test_a_cell_with_no_code_behind_it_is_refused_even_when_declared_as_the_axis(path):
    """门口拒过一次，不等于出结论的那一步也拒：`compare` 拿的是 manifest 里记下来的配置，
    手改过的那一份不经过 `--set`。声明成轴还不给过，跟 `model` 一样——但理由得说对。"""
    a = Config()
    out = report.axis_diff(a, _bump(a, path), axis=(path,))
    assert out.ok is False, f"{path} 声明成轴就放行了，报告会印'除声明轴外无差异'"
    assert path in out.inert
    assert out.rejected == {}, "那是身份那一类的桶"
    assert out.undeclared == {}, "已经声明过了，不该再算未声明"


@pytest.mark.parametrize("path", Config().inert_leaves)
def test_a_parent_axis_does_not_cover_a_leaf_that_nothing_reads(path):
    """`--axis regions` 放行整块预算表是刻意的（见〈declaring_a_parent_axis…〉），但"整块"
    里那些后面没有代码的格子不该跟着被放行——它们两边玩的是同一套规则。"""
    a = Config()
    parent = path.split(".")[0]
    out = report.axis_diff(a, _bump(a, path), axis=(parent,))
    assert out.ok is False and path in out.inert


def test_the_refusal_for_an_empty_cell_names_the_empty_cell_not_the_paired_assumption():
    """两类的修法在两个地方：记账格要回去把代码补上，身份格是这批数据已经废了。把后者印给
    前者看，读的人就会去重跑批次——而重跑一百次也一样。

    格子是从名单里取的而不是写死的：这张名单会随实现变（`regions.b0` 在 `#63` 配上尺子之前
    就在这里，之后就不在了），而这条用例钉的是文案，不是哪一格。
    """
    a = Config()
    assert a.inert_leaves, "名单空了，这一条就没有可拒的格子了"
    leaf = a.inert_leaves[0]
    inert = report.axis_diff(a, _bump(a, leaf), axis=(leaf,)).render("ha", "hb")
    assert leaf in inert and "没有代码" in inert
    assert "配对前提已失效" not in inert
    ident = report.axis_diff(a, dataclasses.replace(a, model="other"),
                             axis=("model",)).render("ha", "hb")
    assert "配对前提已失效" in ident and "没有代码" not in ident


# --------------------------------------------------------------------------- canary
def _probe(pid, answer, lat):
    return {"id": pid, "answer": answer, "latency_s": lat, "completion_tokens": 40}


def test_an_identical_canary_passes():
    before = [_probe(f"p{i}", f"a{i}", 2.0) for i in range(5)]
    after = [_probe(f"p{i}", f"a{i}", 2.2) for i in range(5)]
    out = report.canary_verdict(before, after)
    assert out["ok"] is True and out["drifted"] == []


def test_one_flipped_answer_invalidates_the_batch_and_names_the_probe():
    """答案变了 = 权重变了，延迟再快也不是同一个人。整批标 INVALID_DRIFT（plan §8 第 5 条）。"""
    before = [_probe(f"p{i}", f"a{i}", 2.0) for i in range(5)]
    after = [_probe("p2", "different", 2.0)] + before[:2] + before[3:]
    out = report.canary_verdict(before, after)
    assert out["ok"] is False and out["drifted"] == ["p2:answer"]
    assert "INVALID_DRIFT" in out["terminal"]


def test_latency_only_drift_uses_the_same_1_5_threshold_as_m7():
    """1.4× 过、1.6× 不过：阈值和 metrics.m7 的漂移自检共用一个常数，
    否则一个说"端点变了"另一个说没变，报告就自相矛盾。"""
    before = [_probe(f"p{i}", f"a{i}", 2.0) for i in range(5)]
    ok = report.canary_verdict(before, [_probe(f"p{i}", f"a{i}", 2.8) for i in range(5)])
    bad = report.canary_verdict(before, [_probe(f"p{i}", f"a{i}", 3.2) for i in range(5)])
    assert ok["ok"] is True
    assert bad["ok"] is False and bad["drifted"] == ["latency×1.60"]
    assert report.DRIFT_RATIO == 1.5


def test_a_faster_endpoint_is_drift_too():
    """只查"变慢"的守卫会在被人换成小模型的那天彻底失效——而且是最可信的那种失效：
    延迟更好看，谁都不会去看 canary。"""
    before = [_probe(f"p{i}", f"a{i}", 2.0) for i in range(5)]
    out = report.canary_verdict(before, [_probe(f"p{i}", f"a{i}", 1.0) for i in range(5)])
    assert out["ok"] is False and out["drifted"] == ["latency×0.50"]
    assert out["terminal"] == "INVALID_DRIFT"


def test_a_missing_probe_is_drift_not_a_skipped_row():
    """尾探针少跑了一条：宁可比"端点下线了"，也不要静默少一列——那正是 R7 的样子。"""
    before = [_probe(f"p{i}", f"a{i}", 2.0) for i in range(5)]
    out = report.canary_verdict(before, before[:4])
    assert out["ok"] is False and out["drifted"] == ["p4:missing"]


# ------------------------------------------------------------------ 胜率的 Wilson 区间
Z95 = 1.959964


def _wilson_by_inversion(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson 区间的**定义式**：所有"score 检验在 z² 下不拒绝"的 `p0`，用二分反解出来。

    刻意不复用 `metrics.wilson_ci` 的闭式解——两条独立的路径对上了才算交叉验证。补这片之前的
    情况是：这个公式唯一的数值锚点是 `test_golden_game.py` 里那条 `wilson95 == [0.207, 1.0]`，
    它挂在一条名字讲的是"替身桌不计分"的用例里，而且落在 `p̂=1` 这个角上。角上 `p(1-p)=0`，于是
    "只把 `p(1-p)/n` 这一项漏掉"的实现（W6）在角上和正确解**逐位相同**——实测
    `(1,1) → (0.206549, 1.0)` 两边一致，`(20,40)` 才分开。内点才是这条公式的证人。
    """
    z2 = z * z

    def rejected(p0: float) -> bool:
        p0 = min(max(p0, 1e-12), 1 - 1e-12)
        return (k - n * p0) ** 2 / (n * p0 * (1 - p0)) > z2

    p_hat = k / n
    a, b = 0.0, p_hat
    for _ in range(100):
        mid = (a + b) / 2
        a, b = (mid, b) if rejected(mid) else (a, mid)
    lo = 0.0 if k == 0 else (a + b) / 2
    a, b = p_hat, 1.0
    for _ in range(100):
        mid = (a + b) / 2
        a, b = (a, mid) if rejected(mid) else (mid, b)
    hi = 1.0 if k == n else (a + b) / 2
    return lo, hi


@pytest.mark.parametrize("k,n", [(20, 40), (1, 40), (39, 40), (7, 9), (0, 12), (12, 12), (5, 500)])
def test_the_interval_is_the_score_test_inversion_at_the_batch_sizes_used_here(k, n):
    """n=40 是"过夜一批"的规模，`k/n` 取 0、1、½、逼近 1 四种，两端各配一个纯边界局。"""
    assert metrics.wilson_ci(k, n) == pytest.approx(_wilson_by_inversion(k, n), abs=1e-9)


def test_the_interval_stays_inside_the_unit_interval_where_the_normal_approximation_leaves():
    """`wilson_ci` 的 docstring 就是拿这句话立的论：换成 Wilson 不是口味问题。

    两条断言缺一不可：只查 Wilson 落在 [0,1] 里，换成"任何有夹逼的实现"都能过；所以同时钉住
    对照——教科书式 `p ± z·sd` 在这个点上确实越界，前提不成立时这条就红。
    """
    p, n = 1 / 40, 40
    naive_lo = p - Z95 * math.sqrt(p * (1 - p) / n)
    lo, hi = metrics.wilson_ci(1, n)
    assert naive_lo < 0.0, "正态近似没越界，那这个对照就什么都没对照到"
    assert 0.0 < lo <= hi <= 1.0
    assert metrics.wilson_ci(39, n)[1] < 1.0


def test_an_empty_denominator_is_the_whole_interval_rather_than_a_division_error():
    """零局可判的批次是真的会发生的：`m1_win_rate` 自己就有"分母为空"那条 note。"""
    assert metrics.wilson_ci(0, 0) == (0.0, 1.0)
