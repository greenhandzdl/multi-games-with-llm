"""读数层的地板分支：十一格各有执行证人（`#241`）。

`#237` 那张普查名单里，进程内可达的 27 格有十一格是"什么都没有时说什么"——`return 0.0`、
`return 1.0`、`return ""`、`isinstance` 那一支、以及一枚非对象的 sidecar。它们没有一条会算错
数字，它们的价值在于**地板由谁踩**：没有用例走到那一支时，一次重构就能把地板换成异常或换成
`None`，而整套测试照样绿。

这里钉的十一格分三本：`metrics.py` 五格（短文本的 n-gram、两段空白算全同、空名单的消极率、
空序列的分位数、非对象 sidecar 的拒用句）、`report.py` 四格（同一族分位数、单点方差、
嵌套列表的压平形状、延迟 `import` 的那条 dataclass 支）、`batch.py` 两格（`--set` 的布尔与
字符串型判定）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from wolfengine import batch, metrics, report


# ---------------------------------------------------------------- metrics: 相似度的两枚地板
def test_a_text_shorter_than_n_grams_yields_the_whole_string_as_its_only_gram():
    """不足一枚 n-gram 的文本不返回空集，而是把整串当作一枚 gram——包括空串返回真正的空。

    `char_ngrams` 只在一句长到能切出窗口时才有"gram"这个概念；短于窗口时若回空集，两句三个字的
    发言就会被算成"零共享"，而它们其实是同一句话。空串是这一支的另一侧，所以两向都在这一条里。
    """
    assert metrics.char_ngrams("狼人", 4)["狼人"] == 1
    assert sum(metrics.char_ngrams("狼人", 4).values()) == 1
    assert not metrics.char_ngrams("", 4)


def test_two_silent_seats_count_as_identical_rather_than_unrelated():
    """两个人都没说话，这一对的相似度是 1.0 而不是 0/0。

    分母为 0 时 `jaccard` 有两格地板：`ga`、`gb` 同时为空回 1.0，以及 `union` 为 0 也回 1.0。
    这一条钉前一枚，并顺手给出反面——一段空白对一段有话，不算全同。塌缩判据
    （`collapse_round`）把"整桌都说不出话"当成最像的一种塌缩，靠的就是这一格。
    """
    assert metrics.jaccard("", "") == 1.0
    assert metrics.jaccard("刀口在三号", "") < 1.0


def test_an_empty_round_is_reported_as_zero_passive_not_as_missing():
    """没有一轮可量时消极率回 0.0：这一格是地板，不是"这桌不消极"的结论。

    `passivity_rate` 的分母是本轮发言数，空名单没有分母。引擎选了 0.0 而不是 `None`，所以
    M3 闸门在替身桌之外遇到空轮时读到的就是"零消极"。这一条钉住**当前口径**，并在句子下面
    留一句：把它读成合格证是错的，读法归 `#95` 那一族（沉默要给 None）。
    """
    assert metrics.passivity_rate([]) == 0.0
    assert metrics.passivity_rate([("listen", "我先听听大家怎么说。")]) == 1.0


def test_a_percentile_of_nothing_returns_the_zero_floor():
    """`_pct` 在空序列上取 0.0——p95 无定义时的地板，与"延迟为零"是两件事。

    分位数在 `metrics` 与 `report` 两本里各有一枚同名 helper，取法不同（这里 `ceil` 取位、
    那里线性插值），地板却同为 0.0。两条用例各钉一本，避免其中一枚被另一枚"代理"。
    """
    assert metrics._pct([], 0.95) == 0.0
    assert metrics._pct([1.0, 2.0, 3.0], 0.95) == 3.0


# ---------------------------------------------------------------- metrics: sidecar 不是对象
def test_a_calibration_sidecar_that_is_not_an_object_is_refused_by_name(tmp_path):
    """`calibration.json` 解析得出来、但不是对象——拒用的那句话要说清是形状不对。

    同一个读取器在"文件不存在""读不动/解不动"之后有第三格：拿到的是一个 JSON 数组。这是
    手工编辑或半截写入最可能留下的形状，而它走不到任何常数上，所以 note 里必须带上文件名，
    否则 `wolf audit` 只会印一句"未标定"而没人知道该去看哪一份。
    """
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    out = metrics.load_calibration(path)
    assert out["usable"] is False
    assert "不是一个对象" in out["note"]
    assert str(path) in out["note"]


# ---------------------------------------------------------------- report: 两枚统计地板
def test_the_bootstrap_percentile_of_no_replicates_is_the_same_floor():
    """`report._pct` 收的是排好序的重复统计量；空列表回 0.0，单点回它自己。

    单点那一半是这一条的真正内容：B 次重采样全落在同一个值上时区间宽度为 0，而插值式
    （`lo == hi`）必须回那个值而不是 0.0——把它读成"置信区间塌到零"是另一件事。
    """
    assert report._pct([], 0.5) == 0.0
    assert report._pct([2.5], 0.5) == 2.5
    assert report._pct([0.0, 1.0], 0.5) == 0.5


def test_a_single_replicate_has_no_sample_variance():
    """样本方差（n−1）在两点以下没有定义，地板回 0.0 而不是除零。

    `deff` 是拿这个数算出来的比值，读者拿它对照 1；单点时 0.0 会让 `deff` 走上游那一格的
    兜底，所以这里也钉一下反向：两点才算得出方差。
    """
    assert report._var([1.0]) == 0.0
    assert report._var([]) == 0.0
    assert report._var([1.0, 3.0]) == 2.0


# ---------------------------------------------------------------- report: 压平与映射
def test_a_nested_list_is_flattened_to_its_repr_not_to_leaf_paths():
    """列表里还套着结构时，`flatten` 整格写成 `repr`——轴守卫要能一眼比出顺序变了。

    逐项下钻会把 `regions.b2.0.kind` 这类路径造出来，两臂之间一项增删就让"哪些键不同"变成
    键名增删，而不是值变化。所以这一支把整格压成一个字符串；反面（纯标量列表）走 else 那一支。
    """
    flat = report.flatten({"arms": [{"name": "a"}, {"name": "b"}], "seats": [1, 2]})
    assert flat["arms"] == repr([{"name": "a"}, {"name": "b"}])
    assert flat["seats"] == [1, 2]


@dataclass
class _Card:
    seat: int
    role: str


def test_a_plain_dataclass_reaches_asdict_when_it_has_no_to_dict():
    """没有 `to_dict` 的对象走 `dataclasses.asdict` 那一支：延迟 import，函数体内才发生。

    `flatten` 对配置对象先试 `to_dict`，再试 dataclass。中间这一支在离线套件里从没被走过，
    因为批次真正喂进去的对象都带 `to_dict`。这一格值得钉的正是那枚 **import 语句本身**：
    它坐在分支体里，删掉它就只会在这条路径上抛 `NameError`，而那条路径平时没人走。
    """
    assert report._as_mapping(_Card(seat=3, role="witch")) == {"seat": 3, "role": "witch"}


# ---------------------------------------------------------------- batch: --set 的型判定
def test_a_bool_field_refuses_to_be_filled_with_a_number():
    """`--set quick=1` 被拒：布尔位不是"任何整数"。

    `_type_ok` 先问"这一个是布尔吗"，所以 `True` 与 `1` 之间的不对称是设计如此，而不是漏判。
    """
    assert batch._type_ok(True, True) is True
    assert batch._type_ok(True, 1) is False


def test_a_string_field_refuses_to_be_filled_with_a_number():
    """`--set model=42` 被拒：字符串格不收数字，收别的字符串。

    与上一格同一族但不同支：这里判的是"原来那一个是字符串"。两格各钉一支，是因为它们在同一条
    `--set` 校验链上，一次改动容易只挪走其中一支而另一支照样绿。
    """
    assert batch._type_ok("gemma", "qwen") is True
    assert batch._type_ok("gemma", 42) is False
