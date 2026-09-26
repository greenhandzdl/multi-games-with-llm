"""M7 批次层：N 局 × K 配置、配对 seed、run_manifest、canary 前后夹。

这一层唯一的新事实是**配对**：第 i 局两个配置共用同一个 `deal_seed`（发牌 + 座位序 + 夜晚
RNG 流都由它派生，见 `game.RngStreams.for_seed`），差异只允许出现在声明的那一根轴上。
配对一旦坏掉，McNemar 就从"检验"退化成"两组的巧合"，而症状只是 p 值变小——所以这里钉的
是文件里存下来的 `deal_seed`，不是内存中的循环变量。

canary 用注入的探针执行器，不用真端点：端点现在关着，而"探针不一致就整批作废"这条逻辑
必须能在端点下线时被验证（plan R7 的场景恰恰是"批跑到一半被人重启了"）。真探针在
`cli.batch` 里接 `LLM`，这里只接一个 async callable。
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import re
import shutil
from pathlib import Path

import pytest

from wolfengine import batch, events, game, metrics, report
from wolfengine.config import Config
from wolfengine.llm import EndpointUnavailable


def _arms(**overrides: dict[str, object]) -> list[batch.Arm]:
    """One Arm per name; the overrides are the claim about what this arm changed."""
    return [batch.Arm(name, batch.apply_overrides(Config(), dict(o)),
                      overrides=tuple(o)) for name, o in overrides.items()]


def test_overrides_reach_nested_budgets_and_reject_a_typo():
    """点号路径写进 `--set A.regions.b2=900` 这一类命令行参数里，写错一个字母就必须停下来：
    静默忽略的 override 会让"两配置只差一根轴"变成一句谎话。"""
    cfg = batch.apply_overrides(Config(), {"temperature": 0.6, "regions.b2": 900})
    assert cfg.temperature == 0.6 and cfg.regions.b2 == 900
    with pytest.raises(batch.BadOverride):
        batch.apply_overrides(Config(), {"regions.b33": 1})
    with pytest.raises(batch.BadOverride):
        batch.apply_overrides(Config(), {"temperature": "hot"})


def test_a_derived_field_is_not_a_treatment_axis():
    """`actor_kinds` 是日志对"这局谁在桌边"的记录，不是可调参数：允许 `--set A.actor_kinds=…`
    就等于让一批 mock 数据自称真桌，而把替身桌挡在语料外那条规则（mock 在 plan §十一、真人在
    §十五）靠的就是这一格。

    名单是派生的（`Config.axis_fields` 来自 dataclass 字段），所以新增字段默认进对比：漏声明
    会撞上 compare 的 AXIS_VIOLATION，是响的；写死名单才会静默。
    """
    fields = Config().axis_fields
    assert "temperature" in fields and "regions" in fields
    assert not set(fields) & set(report.FORBIDDEN_AXIS), fields
    with pytest.raises(batch.BadOverride):
        batch.apply_overrides(Config(), {"actor_kinds": ["llm"]})
    # 名单只有一张：`--set B.model=…` 在命令行上就该停，而不是等 compare 事后拒绝——
    # 到那时端点的时间已经花掉了。
    for name in report.FORBIDDEN_AXIS:
        with pytest.raises(batch.BadOverride):
            batch.apply_overrides(Config(), {name: "x"})


def _reads_attribute(text: str, name: str) -> bool:
    """Is there an `obj.<name>` access in this module's *code*? Prose is not a reader.

    One predicate, one implementation: the src-side scan and the `config.py` side's "is that
    method reached from elsewhere" scan are the same question asked of two file sets, and the
    line-regex shape of it (scanning `\\.<name>\\b` over `splitlines()`) answers both by finding
    the name inside help text. `#76` is the field that survived that.
    """
    return any(isinstance(n, ast.Attribute) and n.attr == name
               for n in ast.walk(ast.parse(text)))


def _reachable_config_reader(field: str, src: dict[str, str]) -> str:
    """A `Config` method that reads `self.<field>` *and* is called from another module; else "".

    `src` is deliberately the tree minus `config.py`, so this is the only way a read inside the
    dataclass's own file can enter the evidence.
    """
    tree = ast.parse(Path("src/wolfengine/config.py").read_text(encoding="utf-8"))
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for meth in (n for n in cls.body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            if not any(isinstance(n, ast.Attribute) and n.attr == field
                       for n in ast.walk(meth)):
                continue
            if any(_reads_attribute(text, meth.name) for text in src.values()):
                return meth.name
    return ""


def _readers(field: str, src: dict[str, str]) -> list[str]:
    """Modules that read `field` as an attribute. Comments and docstrings are not readers.

    A field's only reader can live in `config.py` on purpose: `token_budget_for` is the single
    place that decides which `max_tokens` a phase is billed at, and `tests/test_purity.py` fails
    any other module that writes that branch a second time. Both guards serve the same bug class
    (an enumeration typed twice drifts), so excluding the whole of `config.py` from the evidence —
    the original shape of this check — made them fight: the single-source refactor left
    `max_tokens_speech` and `max_tokens_action` with a reader this guard could not see, and a
    green 598-test suite turned red on the field it was praising.

    The exclusion therefore moved from file-level to reason-level. A read inside `Config` counts
    only when the method holding it is called from outside `config.py`: a field read by nobody, or
    read only by a method nobody reaches, is still a label and not a knob.
    """
    hits = [name for name, text in src.items() if _reads_attribute(text, field)]
    if not hits:
        called = _reachable_config_reader(field, src)
        hits = [f"config.py:{called}"] if called else []
    return hits


def _src_outside_config() -> dict[str, str]:
    """The whole `src/` tree except `config.py`, as filename -> text."""
    return {f.name: f.read_text(encoding="utf-8")
           for f in sorted(Path("src/wolfengine").rglob("*.py")) if f.name != "config.py"}


def test_a_field_named_only_inside_prose_has_no_reader():
    """帮助文本里出现过点号加字段名，不算"有代码读它"。

    轴守卫问的是"有没有一处属性访问读了这个字段"，那是一个 AST 上的事实。按行扫正则的写法会把
    `cli.py` 的 `--set` 例子当成读者（那份文件里 `--set B.temperature=0.6` 出现过两次，一次在
    docstring、一次在帮助字符串，而 `cli.py` 从头到尾没有一处 `cfg.temperature`）：于是**从没上过
    线**的字段顶着"有读者"的判定通过守卫，两臂发出同一串字节，报告里却写着"声明的处理轴：
    temperature"。`#76` 量的就是这一格，而它修完之后 `temperature` 有了真读者（`temperature_for`），
    所以这条用例钉的是判据本身，不是那个字段当时的状态——字段的现状由下一条用例看着。
    """
    src = _src_outside_config()
    # 先自证这一格不是空转：散文里确实说过两次，而代码里一次都没读。
    assert src["cli.py"].count("--set B.temperature=0.6") >= 2, "夹具退化了：例子不在 `cli.py` 里"
    assert _reads_attribute(src["cli.py"], "temperature") is False
    # 反向一格，免得 `is False` 是因为辅助函数压根不认任何属性访问。
    assert _reads_attribute("def f(cfg):\n    return cfg.demo_field\n", "demo_field") is True


def test_an_axis_field_no_code_reads_is_a_lie_not_a_knob():
    """`--set` 能改的字段必须真的有代码读它，否则两臂差的是一个标签。

    `axis_fields` 是从 dataclass 派生的（上一条用例就是这个设计在说话），所以"新增字段默认
    进对比"只对**被接住的**字段成立：一个没有任何读取点的字段照样进 `config_hash`，于是
    `--set A.enable_sheriff=true` 能过守卫、能出两份 hash、能生成一份"警长局 vs 无警长局"
    的对比报告——而九个人从头到尾玩的是同一套规则。静默的漏不在这里（漏的是报告说谎）。
    判定按读取点算：只看 `cfg.<字段>` 这一类属性访问，注释行不算；`config.py` 里的读取点也
    算，但要求那个方法被别的模块调过——理由写在 `_readers` 里。
    """
    src = _src_outside_config()
    unread = [field for field in Config().axis_fields if not _readers(field, src)]
    assert unread == [], f"这些字段被当作处理轴暴露给 --set，但 src/ 里没人读它：{unread}"
    # 反方向同样要钉：一旦谁把某个开关接上了代码，它就必须离开 `_INERT` 去当一根正常的轴。
    # 留在名单里不只是难看——它仍然进 `config_hash`，于是两臂的 hash 差了而玩法一模一样，
    # 而 `--set` 这时拒绝的理由是"没人读它"，一句已经假掉的话。
    implemented = {f: h for f in Config().inert_fields if (h := _readers(f, src))}
    assert not implemented, (
        f"这些字段已经有人读了，还挂在 inert 名单上拒绝当处理轴：{implemented}")


def test_an_unimplemented_knob_is_refused_for_the_right_reason():
    """`--set A.enable_sheriff=true` 必须停在命令行上，而且理由要说"没实现"。

    拒绝有两类，共用一条 `if` 就会共用一句话：`actor_kinds`/`model` 那类是**身份**（改了就不
    是同一批数据），`enable_sheriff` 这类是**还没做**（改了还是同一批数据，只是 hash 变了）。
    把后者说成前者，读错误的人会以为自己在碰一条受保护的元数据；而真正要传达的是"这个开关
    后面没有代码"——plan §13 明写不做，字段留着只为把默认关死这件事说清楚。
    """
    with pytest.raises(batch.BadOverride) as who:
        batch.apply_overrides(Config(), {"actor_kinds": ["llm"]})
    assert "身份" in str(who.value)
    with pytest.raises(batch.BadOverride) as knob:
        batch.apply_overrides(Config(), {"enable_sheriff": True})
    msg = str(knob.value)
    assert "身份" not in msg, msg
    assert "没有" in msg and "读" in msg, f"拒绝理由没说是没实现：{msg}"
    assert "enable_sheriff" in Config().to_dict(), "字段要留着：默认关死本身就是一条声明"


# plan §5 预算表里既没接刀、也没进 `REGION_CAP_KEYS` 的那两格。B0 和 C1–C4 曾经也在这里，
# `#63` 给它们配了读数（`assemble.block_tokens`）和尺子（`metrics.REGION_CAP_KEYS`）之后，
# 按下面那条双向判据就必须离开——留在名单里会让 `--set` 说一句已经假掉的话。
NESTED_UNREAD = ("tokens.warn", "tokens.force_compact")


def _src_texts() -> dict[str, str]:
    return {f.name: f.read_text(encoding="utf-8")
            for f in sorted(Path("src/wolfengine").rglob("*.py")) if f.name != "config.py"}


def _leaf_hits(leaf: str, src: dict[str, str]) -> list[str]:
    """读取点 = `.leaf` 属性访问，**或** `"leaf"` 这个字符串常量。

    把字符串常量算进来不是放宽：区域上限是 `metrics.REGION_CAP_KEYS` 按名字查表读的
    （`caps.get("b2")`），只看属性访问会把 `a_hard`、`b1` 判成没人读——那两个数一旦被拒，
    `--set` 就从"拒掉一个标签"变成"拒掉一根真轴"，那是更坏的错。
    """
    pat = re.compile(rf"\.{leaf}\b|\"{leaf}\"")
    return [n for n, text in src.items()
            if any(pat.search(ln) and not ln.strip().startswith("#")
                   for ln in text.splitlines())]


@pytest.mark.parametrize("path", NESTED_UNREAD)
def test_a_nested_budget_cell_with_no_reader_is_not_a_knob(path: str):
    """`--set A.tokens.warn=1` 曾经能过：两臂 hash 不同、玩法相同，报告把标签叫处理轴。

    顶层那一族早有判据（`test_an_axis_field_no_code_reads_is_a_lie_not_a_knob`），但它数的是
    `Config.to_dict()` 的顶层键；`_set_path` 往下走时 `getattr(obj, "axis_fields")` 在
    `RegionBudget` 上是 `None`，于是整块嵌套表都不设防。plan §5 给 B0 和 C1–C4 写了预算数，
    装配器当时读的是 `c_total` 和 `b2`——那五格在 `#63` 之前只是记账，动它们不动任何东西，
    所以也在这张名单里；现在它们各有自己的读数和尺子，按下面那条双向判据必须出去。
    理由要说"没人读"，不能说"身份"（那是 `actor_kinds` 的话）。
    """
    with pytest.raises(batch.BadOverride) as why:
        batch.apply_overrides(Config(), {path: 1})
    msg = str(why.value)
    assert path in msg, msg
    assert "身份" not in msg, msg
    assert "读" in msg, f"拒绝理由没说是没人读：{msg}"


@pytest.mark.parametrize("path", ["regions.a_hard", "regions.b1", "regions.b2",
                                  "regions.c_total", "regions.a_target", "regions.b_steady",
                                  "tokens.absolute_ceiling",
                                  # `#63` 之后这五格有了尺子（`metrics.REGION_CAP_KEYS`），
                                  # 其中 `regions.c_belief` 后面还接着刀。误拒它们比放行假轴更糟。
                                  "regions.b0", "regions.c_persona", "regions.c_belief",
                                  "regions.c_private", "regions.c_task"])
def test_a_nested_cell_that_something_really_reads_is_still_a_knob(path: str):
    """反方向：这十二格是真轴，误拒它们比放行假轴更糟——`--set A.regions.b1=2000` 改的是热窗口。"""
    cfg = batch.apply_overrides(Config(), {path: 1})
    got = cfg
    for part in path.split("."):
        got = getattr(got, part)
    assert got == 1


def test_the_no_reader_list_and_the_source_agree_in_both_directions():
    """名单是手抄的，所以两边都会烂：多列一格 = 拒掉一根真接上的轴；少列一格 = 放行一个标签。

    `Config().inert_leaves` 是 `--set` 唯一的依据（库不去扫 `src/`，那会把打包后的安装变成
    依赖源码树在不在），这一条用例替它扫：把 `flatten` 出的每个嵌套叶子按 `_leaf_hits` 查一遍，
    没人读的集合必须和名单**相等**——接上代码的那一格会自己从名单里掉出去，新增而没接的那一格
    会自己挤进来。
    """
    src = _src_texts()
    nested = [k for k in report.flatten(Config()) if "." in k]
    unread = sorted(k for k in nested if not _leaf_hits(k.split(".")[-1], src))
    assert unread == sorted(Config().inert_leaves), (
        f"名单与 src/ 不一致：src 里没人读的是 {unread}")


def test_a_batch_pairs_the_deal_seed_across_arms(tmp_path):
    """两臂的第 i 局必须同一副牌：查的是落盘的 `deal_seed`，不是跑的时候循环变量对不对。"""
    arms = _arms(A={}, B={"temperature": 0.6})
    res = asyncio.run(batch.run_batch(arms, games=2, seed0=100, out_dir=tmp_path,
                                      mock=True, canary=batch.NO_CANARY))
    assert res.pair_keys == [100, 101]
    seen: dict[str, list[int]] = {}
    for arm in arms:
        rows = batch.read_arm(tmp_path / arm.name)
        seen[arm.name] = sorted(r["deal_seed"] for r in rows)
    assert seen["A"] == seen["B"] == [100, 101], seen
    assert res.n_logs == 4


def test_the_manifest_records_the_things_compare_will_need(tmp_path):
    """`compare` 只能凭 manifest 判断"这两批是不是同一天、同一端点、同一被试"，所以这些
    字段必须在批跑完时就固化下来，而不是等 compare 去猜目录名。"""
    arms = _arms(A={}, B={"temperature": 0.6})
    asyncio.run(batch.run_batch(arms, games=1, seed0=7, out_dir=tmp_path,
                                mock=True, canary=batch.NO_CANARY))
    man = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert man["arms"]["A"]["config_hash"] != man["arms"]["B"]["config_hash"]
    assert man["arms"]["B"]["overrides"] == ["temperature"]
    assert man["seed0"] == 7 and man["games"] == 1
    assert man["actor_kinds"] == ["mock"] and man["synthetic"] is True, (
        "合成桌必须被写下来：局文件与 manifest 都记这一格是 §十五 的落实方式，而这张 mock 桌"
        "被挡在语料外面的依据本身是 §十一")
    assert man["model"] == Config().model and man["base_url"] == Config().base_url
    # 每局一行，`fallbacks` 旁边要有那一格的**判定**，不只是计数：报告点名的是"超过阈值的局"，
    # 而阈值是这根轴上可能被 override 的配置。只有计数的话，读 manifest 的人得自己重算一遍
    # `> 12`，而算的时候用的是今天的配置——预先声明的剔除规则就是这么变成事后规则的。
    assert [r["degraded"] for r in man["rows"]] == [False, False]
    assert [r["fallbacks"] for r in man["rows"]] == [0, 0], \
        "mock 桌打不出 fallback：判定和计数必须在同一行里都是干净的"


def test_a_synthetic_batch_is_usable_for_plumbing_and_not_for_conclusions(tmp_path):
    """mock 批次的价值是证明管线跑得通；它的胜率不是发现。让 compare 把这句话打印出来，
    而不是让人对着 40 局的表自己想起来。"""
    arms = _arms(A={}, B={"temperature": 0.6})
    asyncio.run(batch.run_batch(arms, games=2, seed0=1, out_dir=tmp_path,
                                mock=True, canary=batch.NO_CANARY))
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "SYNTHETIC_TABLE"
    assert "合成桌" in out["markdown"] and out["win"] == {}
    # 被算出来的那一格也要清掉：合成桌的 McNemar 是 40 局 mock 的巧合，读 nested stats 的
    # 脚本比读 markdown 的人更容易把它抄进结论表。
    assert out["stats"]["win"] == {}, out["stats"]["win"]
    assert out["stats"]["utterance"], "逐条率照旧留着：mock 批的价值就是证明管线通"


def test_head_canary_failure_stops_the_batch_before_the_first_game(tmp_path):
    """探针跑不动就不要开局：端点不可用被记成一批"模型很被动"的数据，是 plan R7 里
    最贵的那种误读——而且这一批事后看不出问题。"""
    async def dead(_prompt: str) -> dict:
        # 真实探针包装器抛的就是游戏路径会抛的那一类，不是测试专用的异常类型。
        raise EndpointUnavailable("端点无响应")

    arms = _arms(A={})
    with pytest.raises(batch.BatchAborted):
        asyncio.run(batch.run_batch(arms, games=1, seed0=1, out_dir=tmp_path,
                                    mock=True, canary=dead))
    assert not list(tmp_path.glob("A/*.jsonl")), "开局之后才失败是另一回事，开局之前不许有日志"


def test_a_drifted_tail_canary_marks_the_whole_batch(tmp_path):
    """批跑到一半端点被人换了权重：首尾探针答案不同 ⇒ 整批 INVALID_DRIFT，compare 拒绝。"""
    calls = {"n": 0}

    async def probe(prompt: str) -> dict:
        calls["n"] += 1
        late = calls["n"] > len(batch.CANARY_PROMPTS)  # second pass answers differently
        return {"answer": "42" if late else "7", "latency_s": 2.0, "completion_tokens": 3}

    arms = _arms(A={}, B={"temperature": 0.6})
    res = asyncio.run(batch.run_batch(arms, games=1, seed0=3, out_dir=tmp_path,
                                      mock=True, canary=probe))
    assert res.terminal == "INVALID_DRIFT"
    man = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert man["canary"]["terminal"] == "INVALID_DRIFT"
    assert (tmp_path / "drift.md").exists(), "plan §8 第 5 条：漂移要落一份能读的东西"
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "INVALID_DRIFT" and "不可比" in out["markdown"]


def test_compare_refuses_an_undeclared_second_difference(tmp_path):
    """treatment-axis 守卫落在 compare 上（plan §8 第 2 条）：批可以照跑（数据已经花了钱），
    但一旦两臂的差超出白名单，胜率就不许多说一个字，且要打印逐项差异。"""
    arms = _arms(A={}, B={"temperature": 0.6, "max_tokens_speech": 200})
    asyncio.run(batch.run_batch(arms, games=2, seed0=11, out_dir=tmp_path,
                                mock=True, canary=batch.NO_CANARY))
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "AXIS_VIOLATION"
    assert set(out["axis"].undeclared) == {"max_tokens_speech"}, out["axis"].undeclared
    assert "max_tokens_speech" in out["markdown"]
    assert out["win"] == {}
    # 同一批、同样的两臂，把两根轴都声明出来就不再被拒（被拒的是"没说"，不是"多了"）
    assert batch.compare(tmp_path, axis=("temperature", "max_tokens_speech"))["verdict"] \
        == "SYNTHETIC_TABLE"


def test_a_real_batch_of_two_identical_arms_still_reports_the_pairing(tmp_path):
    """两臂完全同配置时 compare 必须给出"无差异可比"而不是一个 p=1 的检验：同一配置跑两遍
    测的是端点噪声，把这个说清楚是报告的责任（plan §八 的复现命令旁边要能看见结论的边界）。"""
    arms = _arms(A={}, B={})
    asyncio.run(batch.run_batch(arms, games=2, seed0=21, out_dir=tmp_path,
                                mock=True, canary=batch.NO_CANARY))
    out = batch.compare(tmp_path, axis=())
    assert out["verdict"] == "IDENTICAL_ARMS"


def _paired(tmp_path, arms, *, games: int = 2, seed0: int = 5) -> None:
    asyncio.run(batch.run_batch(arms, games=games, seed0=seed0, out_dir=tmp_path,
                                mock=True, canary=batch.NO_CANARY))


def test_compare_refuses_one_arm_batch(tmp_path):
    """单臂批次没什么可拒的——但拒绝必须是显式的，否则 `compare` 会拿空配对算出一个 p=1。"""
    _paired(tmp_path, _arms(A={}))
    out = batch.compare(tmp_path)
    assert out["verdict"] == "NEEDS_TWO_ARMS" and "A" in out["why"]


def test_compare_refuses_a_directory_holding_logs_from_two_batches(tmp_path):
    """同一个 `--out` 跑过两次不同配置，目录里就混进了别批的日志。这时配对表看起来仍然整齐
    （每臂文件数一样），唯一的证据是日志自己记下的 config_hash。"""
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}))
    foreign = sorted((tmp_path / "B").glob("*.jsonl"))[0]
    shutil.copy(foreign, tmp_path / "A" / ("stray-" + foreign.name))
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "HASH_MISMATCH"
    assert "stray-" in out["why"], out["why"]


def test_compare_refuses_when_an_arm_is_missing_a_board(tmp_path):
    """某一臂少一局（跑到一半崩了）时，逐对差值不再成对，McNemar 退化成两组巧合。
    "两臂都在"不等于"两臂配对"，所以这条得单独说。"""
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}))
    sorted((tmp_path / "A").glob("*.jsonl"))[0].unlink()
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "PAIRING_BROKEN"
    assert "deal_seed" in out["why"], out["why"]


def test_compare_refuses_a_seed_that_appears_twice_in_one_arm(tmp_path):
    """重复跑同一副牌（手工补过一局）会悄悄把那副牌的权重加一倍：文件数仍然相等，
    hash 仍然对得上，只有 seed 集合看得出。"""
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}))
    src = sorted((tmp_path / "A").glob("*.jsonl"))[0]
    shutil.copy(src, tmp_path / "A" / ("again-" + src.name))
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "PAIRING_BROKEN"
    assert "两次" in out["why"], out["why"]


def _as_real_table(tmp_path) -> None:
    """把 mock 批的 `actor_kinds` 就地改成 `["llm"]`（日志与 manifest 一起），造一张"真桌"表。

    这是伪造的批次，伪造的只有那一格标签：配对、config_hash、逐条率的分子分母全部来自
    真实跑出来的日志。它验证的是 OK 路径的配对算术与 markdown 渲染，不是模型行为——那要等端点。
    """
    man_path = tmp_path / "run_manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man["actor_kinds"] = ["llm"]
    man["synthetic"] = False
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    for p in sorted(tmp_path.glob("*/*.jsonl")):
        lines = p.read_text(encoding="utf-8").splitlines()
        head = json.loads(lines[0])
        head["meta"]["actor_kinds"] = ["llm"]
        lines[0] = json.dumps(head, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flip_one_winner(tmp_path, arm: str) -> None:
    """把该臂第一局的胜方就地翻掉。配对批里两臂的同 seed 局几乎一模一样，A/B 互不相等
    这类断言在对称数据上是空断言，所以需要一个只在测试里造出来的不对称。"""
    for p in sorted(Path(tmp_path, arm).glob("*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for i in range(len(lines) - 1, -1, -1):
            rec = json.loads(lines[i])
            if rec.get("kind") != "game_over":
                continue
            assert rec["payload"].get("winner") == "good", "换掉这一局就翻不出不对称"
            rec["payload"]["winner"] = "wolf"
            lines[i] = json.dumps(rec, ensure_ascii=False)
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    raise AssertionError(f"{arm} 里没有 game_over 事件")


def test_a_real_paired_batch_reaches_the_conclusion_path(tmp_path):
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    assert out["win"]["n_usable"] == 2 and out["win"]["n_dropped"] == 0
    assert set(out["utterance"]) == {"uncited_speech", "refused_turn", "passive_turn"}
    md = out["markdown"]
    assert "配对局数 **2**" in md and "cluster bootstrap" in md
    # 两臂同 seed 同 mock ⇒ 不一致对为 0，报告必须自己承认这批改不出结论
    assert "统计力不足" in md, md


def test_a_draw_pair_is_counted_out_loudly(tmp_path):
    """日数上限当处理轴 = 让"没有赢家"的一对真的走进配对检验。

    B 臂压到 3 天，两臂同 seed，于是 B 里出现的每一局平局都对应一对"一方有赢家、另一方没有"
    的数据。McNemar 把它丢出分母是对的，**静默**丢掉不是：报告要自己说丢了几对，而且要说清
    丢掉的理由是"没有赢家"，不是"未打完"——把平局写成未打完，正好是这一片想分开的两件事
    （端点断了 vs 打完了但没分胜负）。
    """
    _paired(tmp_path, _arms(A={}, B={"max_days": 3}), games=6)
    _as_real_table(tmp_path)
    draws = [metrics.read_game(p) for p in sorted((tmp_path / "B").glob("*.jsonl"))]
    n_draws = sum(1 for g in draws if g.terminal == game.DRAW_DAY_LIMIT)
    assert n_draws >= 1, f"B 臂压到 3 天仍一局平局都没有（{[g.terminal for g in draws]}）"

    out = batch.compare(tmp_path, axis=("max_days",))
    assert out["verdict"] == "OK", out.get("why")
    assert out["win"]["n_dropped"] == n_draws
    assert out["win"]["n_dropped_draw"] == n_draws and out["win"]["n_dropped_other"] == 0
    assert out["win"]["n_usable"] + out["win"]["n_dropped"] == out["win"]["n_pairs"]
    md = out["markdown"]
    assert f"被丢弃 {n_draws} 对" in md, md
    assert f"平局 {n_draws}、中断或未完 0" in md, \
        "丢掉的对数要能追问原因并加起来：平局和中断混成一格，正是这一片要分开的两件事"
    assert "没有赢家" in md and "未打完" not in md, "报告把平局和中断混成了一句话"


def test_an_aborted_side_outweighs_a_drawn_side_in_the_same_pair(tmp_path):
    """一臂断了、另一臂打到上限：这一对记在"中断"那一格，不能记成平局。

    两桶加起来等于被丢弃的对数，所以记错一格就是拿一次端点故障去给"这批出现了平局"背书——
    正是这一片要分开的两件事。这种配对在离线 mock 里不会自然出现（mock 桌打不出 `aborted_*`），
    所以 A 臂那一局的终局是**在内存里**改出来的：只动 GAME_OVER 的 payload，不碰日志文件、
    也不碰 run_manifest（动了就会被"日志与 manifest 不一致"那道闸门拒掉，测的就不是这里了）。
    """
    _paired(tmp_path, _arms(A={}, B={"max_days": 3}), games=6)
    a = [metrics.read_game(p) for p in sorted((tmp_path / "A").glob("*.jsonl"))]
    b = [metrics.read_game(p) for p in sorted((tmp_path / "B").glob("*.jsonl"))]
    n_draws = sum(1 for g in b if g.terminal == game.DRAW_DAY_LIMIT)
    assert n_draws >= 1, "B 臂压到 3 天没有平局，这一对配不出来"
    i = next(k for k, g in enumerate(b) if g.terminal == game.DRAW_DAY_LIMIT)
    over = [e for e in a[i].events if e.kind == metrics.Kind.GAME_OVER][-1]
    over.payload["terminal"] = "aborted_endpoint"
    over.payload["winner"] = None
    assert a[i].terminal == "aborted_endpoint" and b[i].terminal == game.DRAW_DAY_LIMIT

    win = batch.compare_games(a, b)["win"]
    assert win["n_dropped_other"] == 1 and win["n_dropped_draw"] == n_draws - 1
    assert win["n_dropped"] == win["n_dropped_draw"] + win["n_dropped_other"]


def test_a_single_paired_game_renders_without_an_interval(tmp_path):
    """一局也能出报告：区间必须缺席而不是 KeyError。真实场景是端点半夜挂掉、只有第一对
    跑完，那时读一份崩掉的报告比读一份写着"不足"的报告更没用。"""
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=1)
    _as_real_table(tmp_path)
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    assert "不足" in out["markdown"], out["markdown"]
    thin = out["utterance"]["uncited_speech"]
    assert thin["ci"] == [None, None] and thin["point_a"] is not None, (
        "区间缺席不等于点估计也丢了：报告要能看出方向，只是不能声称显著")
    assert thin["deff_gain"] is None, "分母都没有，比值只能缺席，不能是 0 也不是无穷大"
    assert not any(ch.isdigit() for ch in _rate_row(out["markdown"], "uncited_speech")[7])


def test_the_conclusion_report_carries_m1_for_each_arm(tmp_path):
    """M1 的分母是**一批**局，`wolf audit` 只有一个文件、给不出这个分母；`compare` 是唯一
    已经把这些局全部读回内存的地方。不在这里打印，`m1_win_rate` 就只是一段没人调用的正确代码。

    markdown 里带的是 `m1["note"]` 原句而不是另抄一遍：note 会随样本形状换话（全平局时说
    "分母为空"，有胜负时说"仅作描述"），手写的那句会在其中一种形状下说谎。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    _flip_one_winner(tmp_path, "B")
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    assert set(out["m1"]) == {"A", "B"}, sorted(out.get("m1", {}))
    for arm in ("A", "B"):
        m = out["m1"][arm]
        assert m["n_games"] == 2 and m["n_synthetic_excluded"] == 0
        assert {"good_win_rate", "wilson95", "by_role_survival", "descriptive"} <= set(m)
    # 两臂的数字必须各不相同才有资格当断言：配对批里 A/B 的同 seed 局几乎一样，把 games_a
    # 传给两个臂这个 bug 在对称数据上是隐形的，所以手工翻掉 B 里一局的好人胜。
    assert out["m1"]["A"]["good_win_rate"] != out["m1"]["B"]["good_win_rate"], "fixture 失效"
    md = out["markdown"]
    assert "## 胜负与存活（M1，各臂绝对值）" in md, md
    assert "| A | 2 | 2 | 0.5 | [0.095, 0.905] |" in md, md
    assert "| B | 2 | 2 | 0.0 | [0.0, 0.658] |" in md, md
    assert out["m1"]["A"]["note"] in md and out["m1"]["B"]["note"] in md


def test_the_m1_table_shows_the_files_that_held_no_game(tmp_path):
    """「局数」那一格在文件比局多时必须同时看得见两个数，否则新发的字段是没人读的第二格。

    `#99` 的 M4 教的就是这一格：把 `n_files` 改成别的算法之后整套全绿，因为标题里那个份数是另一
    只手数出来的。所以这里不只钉指标，钉的是**渲染器真的从指标里取**：A 臂多一份 0 字节的文件，
    单元格里就得长出 `（3 份）`，B 臂干净就不许长出括号。下面那一句关于形状的话（`meta_notice`）
    同理，从 `hollow.note` 原样印出，不在渲染器里另写一遍；"是哪一份"也从指标里取
    （`hollow.paths`），渲染器只把它接到行尾——`#101` 量的就是这个字段在此之前没有读者。

    不走 `compare` 端到端：只有一臂多出来的文件会先被配对守卫拒掉（两臂 `deal_seed` 集合不同，
    `_pairing_error`（`batch.py:590`），那条路测的是另一件事，而且它根本到不了这张表。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    (tmp_path / "A" / "interrupted.jsonl").write_text("", encoding="utf-8")
    m1 = {n: metrics.m1_win_rate([metrics.read_game(r["path"]) for r in batch.read_arm(tmp_path / n)])
          for n in ("A", "B")}
    assert (m1["A"]["n_games"], m1["A"]["n_files"]) == (2, 3), m1["A"]
    assert (m1["B"]["n_games"], m1["B"]["n_files"]) == (2, 2), m1["B"]
    text = "\n".join(batch._m1_md("A", "B", m1))
    assert "| A | 2（3 份） |" in text, text
    assert "| B | 2 | 2 |" in text, text
    assert events.meta_notice({}) in text, "少掉的那一份是什么形状，表里要读得出来"
    # 整段括住才算点名：印全路径也能过"包含文件名"那一条，而那一屏会被路径挤掉。
    assert "（文件：interrupted.jsonl）" in text, "只报份数不算点名：拿不到文件名就删不掉那一份字节"


def _degrade(tmp_path, arm: str, i: int = 0) -> str:
    """把该局 GAME_OVER 的 `degraded_game` 就地改成"退化"，返回它的 game_id。

    离线 mock 桌打不出 fallback（替身演员的自白永远是合法动作），所以这一格只能在文件里造。
    阈值那一格**不动**：报告要印的是这一臂声明过的规则，把 12 也一起改成 0 就顺带测了另一件
    事（两臂阈值不一致时点名表怎么合并），那是另一条用例的活。
    和 `_flip_one_winner` 一样：只动 GAME_OVER 的 payload，不碰 meta、也不碰 run_manifest——
    动了就会被"日志与 manifest 不一致"那道闸门拒掉，测的就不再是这里。
    """
    path = sorted(p for p in Path(tmp_path, arm).glob("*.jsonl")
                  if not p.name.endswith(".prompts.jsonl"))[i]
    lines = path.read_text(encoding="utf-8").splitlines()
    for n in range(len(lines) - 1, -1, -1):
        rec = json.loads(lines[n])
        if rec.get("kind") == "game_over":
            rec["payload"]["degraded_game"] = True
            lines[n] = json.dumps(rec, ensure_ascii=False)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return json.loads(lines[0])["meta"]["game_id"]
    raise AssertionError(f"{arm} 第 {i} 局没有 game_over 事件")


def test_a_degraded_game_is_named_in_the_report_and_kept_in_every_denominator(tmp_path):
    """plan §143：退化局**点名，不剔除**——剔除规则要预先声明，不能事后悄悄做。

    这一片要同时钉两件事，缺一个都会留下真实的错：
    * 报告必须说得出是哪一局（只报个计数，读的人就没法去翻那一局的转录，而点名唯一的用处
      就是让人去翻）；
    * 点名不许改变任何分母。把退化局从胜率里剔掉是"事后"决定，而 12 这个阈值是"事前"定的，
      两者一旦在不同批次里反着来，同一份数据就能算出两个方向的结论。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    named = _degrade(tmp_path, "A")
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    assert out["degraded"]["A"] == {"n": 1, "ids": [named], "unrecorded": 0,
                                    "threshold": Config().degraded_game_fallbacks}
    assert out["degraded"]["B"]["n"] == 0 and out["degraded"]["B"]["ids"] == []
    assert out["degraded"]["B"]["unrecorded"] == 0, "新跑的批次不该有一格是空的"
    assert out["win"]["n_pairs"] == 2 and out["m1"]["A"]["n_games"] == 2, \
        "点名改变了分母：退化局被悄悄剔出去了"
    md = out["markdown"]
    assert "点名" in md and named in md, md
    assert "不剔除" in md, "报告说了哪一局，但没说清这一局还在结论里"


def test_a_batch_whose_logs_predate_the_verdict_says_unrecorded(tmp_path):
    """旧批次缺这一格，报告要写"未判定"而不是"0 局退化"。

    上一用例里两臂都有 degraded_game（mock 批现在统一写 False），所以"缺格"这条分支必须造：
    就地抹掉 B 臂一局里的那两格，报告的 B 行要从 `0 局退化` 变成说得出"这一局没人判过"。
    没有这一条，字段落地前的批次会在报告里自动变成好消息——而这份报告的全部作用就是说清
    哪些局不该进结论。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    _degrade(tmp_path, "A")
    path = sorted(p for p in (tmp_path / "B").glob("*.jsonl")
                  if not p.name.endswith(".prompts.jsonl"))[0]
    lines = path.read_text(encoding="utf-8").splitlines()
    for n in range(len(lines) - 1, -1, -1):
        rec = json.loads(lines[n])
        if rec.get("kind") == "game_over":
            rec["payload"].pop("degraded_game")
            rec["payload"].pop("degraded_threshold")
            lines[n] = json.dumps(rec, ensure_ascii=False)
            break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    assert out["degraded"]["B"] == {"n": 0, "ids": [], "unrecorded": 1,
                                    "threshold": Config().degraded_game_fallbacks}
    assert "未判定" in out["markdown"], out["markdown"]


def test_an_arm_whose_logs_disagree_on_the_threshold_reports_none(tmp_path):
    """一臂里出现两个阈值，点名表不能替它挑一个。

    正常批次打不出这个形状：阈值和判定在 `game.py` 里同源同刻写出，混批又先被 `config_hash`
    闸门拒掉。能造出它的只有手工改过的文件——而这恰恰是最不能让报告假装没事的时候：它接下来
    要说的是"这些局超没超过 12"，挑一个数字就把两种规则印成了一种。
    反向对照（只看被改的那一局必须报得出 5）证明 `None` 是分歧造成的，不是读不出来。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    games = [metrics.read_game(p) for p in sorted((tmp_path / "A").glob("*.jsonl"))
             if not p.name.endswith(".prompts.jsonl")]
    over = [e for e in games[0].events if e.kind == metrics.Kind.GAME_OVER][-1]
    over.payload["degraded_threshold"] = 5
    assert batch.degraded_games(games)["threshold"] is None
    assert batch.degraded_games(games[:1])["threshold"] == 5


def _damage_seq(tmp_path, arm: str, i: int = 0, *, rename: dict[int, int]) -> str:
    """把该局里若干条事件的 `seq` 就地换成别的数字，返回文件名。

    引擎写不出破损编号（`EventLog.append` 自己递增，一局一个文件），所以这一格只能在文件里造
    ——而"文件被人动过"恰恰是批次侧读数的唯一来源。只改事件行的 `seq`：不碰 meta、不碰
    run_manifest（动了先被"日志与 manifest 不符"那道闸门拒掉，测的就不是这里），也不删行
    （行数不变，撕裂末行那句 `torn_notice` 就不参与这一片）。
    """
    path = [p for p in sorted(Path(tmp_path, arm).glob("*.jsonl"))
            if not p.name.endswith(".prompts.jsonl")][i]
    lines = path.read_text(encoding="utf-8").splitlines()
    left = dict(rename)
    for n, line in enumerate(lines):
        rec = json.loads(line)
        if rec.get("seq") in left:
            rec["seq"] = left.pop(rec["seq"])
            lines[n] = json.dumps(rec, ensure_ascii=False)
    assert not left, f"{path.name}: 要改的编号 {sorted(left)} 在这个文件里不存在"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.name


def _arm_games(tmp_path, arm: str) -> list[metrics.Game]:
    return [metrics.read_game(p) for p in sorted(Path(tmp_path, arm).glob("*.jsonl"))
            if not p.name.endswith(".prompts.jsonl")]


def _cut_tail(tmp_path, arm: str, i: int = 0, *, keep: int = 70) -> tuple[str, str]:
    """把某一局日志的末行只留下前 `keep` 个字节，返回 (文件名, 那半行残句)。

    引擎写不出残句（每条记录自带 `\\n`），所以"文件被砍过"只能在文件里造。砍的是**末行**：一局打完
    的末行是 `game_over`，它带着 `terminal`，于是这一砍同时制造了 #56 的两个后果——`torn_tail` 有了
    内容，而 `terminal` 从 `good_win` 变成 `unfinished`。
    """
    path = [p for p in sorted(Path(tmp_path, arm).glob("*.jsonl"))
            if not p.name.endswith(".prompts.jsonl")][i]
    lines = path.read_text(encoding="utf-8").splitlines()
    torn = lines[-1][:keep]
    try:
        json.loads(torn)
        raise AssertionError(f"{path.name}: 砍完之后还是合法 JSON，这一刀没造出撕裂末行")
    except json.JSONDecodeError:
        pass
    path.write_text("\n".join(lines[:-1] + [torn]) + "\n", encoding="utf-8")
    return path.name, torn


def test_the_batch_side_counts_which_files_lost_their_last_line(tmp_path):
    """`audit` 印 `torn_tail`、`replay` 说〔日志在这里截断〕，批次侧以前只看 `terminal`：一局被砍掉
    末行的日志，进不了 usable 对，就被报告说成"这局没打完"——文件的问题被记成了模型的行为。

    这里钉的是**归约**：`n` 按文件计，`lines`/`chars` 从 `events.torn_extent` 取（不在批次里再数一
    遍字节），缺席的那一臂必须是 0 而不是"没有读数"。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    name0, torn0 = _cut_tail(tmp_path, "A", 0)

    a = batch.truncated_tails(_arm_games(tmp_path, "A"))
    assert a == {"n": 1, "files": [name0], "lines": 1, "chars": len(torn0)}, a
    assert batch.truncated_tails(_arm_games(tmp_path, "B")) == {
        "n": 0, "files": [], "lines": 0, "chars": 0}, "没砍过的臂要报 0，报不出才要和报不出来分得开"

    name1, torn1 = _cut_tail(tmp_path, "A", 1)
    a2 = batch.truncated_tails(_arm_games(tmp_path, "A"))
    assert a2["n"] == 2 and a2["files"] == [name0, name1], a2
    assert a2["chars"] == len(torn0) + len(torn1), a2

    assert set(a2) == {"n", "files"} | set(events.torn_extent([])), (
        "批次这一格的键和那只共用的手脱钩了")
    assert a2["n"] == a2["lines"], (
        "今天这两个数必然相等（末行只容忍一行，两行是损坏、根本读不进来）。它们一旦不等，"
        "说明容忍被放宽了——那时'几行'和'几个文件'就是两件事，这一格要重写")


def test_a_game_carries_the_same_numbering_reading_the_transcript_prints(tmp_path):
    """`replay` 会说"这份日志的编号不是连续递增的"，`audit` 会印 `seq_damage`，批次侧什么都没有：
    一局破损的日志照样进配对表、进分母、进 bootstrap，报告里一句不提。这一格补的是批次缺的那个
    **读数**。

    钉的是"同一个读数"，不是"批次再算一遍"：`Game.seq_damage` 必须就是 `events.seq_damage` 的一次
    调用——再写一份就是把 #54 刚拆掉的"三个出口各有一套算法"搬到批次里。正反两半都要有：只断言
    破损局那一半，一个恒返回 `{"gaps": 1, …}` 的属性也能绿。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    name = _damage_seq(tmp_path, "A", rename={6: 7})
    damaged, clean = _arm_games(tmp_path, "A")
    assert damaged.path.name == name, "fixture 挑的文件和被改的不是同一个"
    assert damaged.seq_damage == events.seq_damage(damaged.events)
    assert damaged.seq_damage == {"gaps": 1, "duplicates": 1, "out_of_order": 0}, damaged.seq_damage
    assert clean.seq_damage == {"gaps": 0, "duplicates": 0, "out_of_order": 0}


def test_the_numbering_tally_counts_files_not_kinds_of_damage(tmp_path):
    """`n` 是**几局**，不是**几处**：一次编辑就能同时造出缺号和重号（把 6 改成 7），把两个计数
    加起来报成"2 局"等于把一份文件说成两份。三种损伤分开数正是 `seq_damage` 不做成布尔值的原因
    （`events.py` 里那句注释说的就是这件事），批次侧的归约不能把它压回去。

    两个文件各带一种形状：A 的第 1 局是"缺号+重号"，第 2 局是"顺序倒挂"（对调两个相邻编号）。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    n1 = _damage_seq(tmp_path, "A", 0, rename={6: 7})
    n2 = _damage_seq(tmp_path, "A", 1, rename={20: 21, 21: 20})
    assert batch.numbering_damage(_arm_games(tmp_path, "A")) == {
        "n": 2, "files": [n1, n2], "gaps": 1, "duplicates": 1, "out_of_order": 1}
    assert batch.numbering_damage(_arm_games(tmp_path, "B")) == {
        "n": 0, "files": [], "gaps": 0, "duplicates": 0, "out_of_order": 0}, \
        "没被改过的臂要报 0 而不是缺席——缺席和“没人量过”在报告里长一个样"
    # 键名跟着 `events.seq_damage` 走，不是在批次里抄一份名单：`DAMAGE_WORDS` 哪天加一类损伤，
    # 批次这一格要么跟着多一项，要么当场红在这里。
    assert set(batch.numbering_damage([])) == {"n", "files"} | set(events.seq_damage([]))


def test_the_batch_report_names_the_files_whose_numbering_is_not_what_the_engine_wrote(tmp_path):
    """破损要**点名到文件**，理由和 `degraded_games` 点名一样：一个计数开不了那个文件。
    点路径而不是点 `game_id`，不是为了区分两臂（实测 04:33:11Z：同 seed 的两个文件连名字都一样，
    `A/` 与 `B/` 下都是 `<utc>_g00000005.jsonl`，真正分开它们的是臂目录）——是因为 `game_id` 住在
    文件里面，而这一格要报的是"这个文件被人改过"。拿被改对象内部的标签去指它，就是 #54 那个
    "两局 `cat` 在一起、页眉照着后一条 manifest 报错局号"的形状。

    同时钉住"只点名不剔除"：编号破损不说明这局打得好不好，把它悄悄剔出分母就是看完结果之后再
    改预注册协议（plan §8 第 5 条同一个道理）。收尾那句必须是**这一节自己的**话：`"不剔除" in md`
    是空腿，退化局那一节永远印着它。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    name = _damage_seq(tmp_path, "A", rename={6: 7})
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    assert out["numbering"]["A"] == {"n": 1, "files": [name], "gaps": 1, "duplicates": 1,
                                     "out_of_order": 0}, out["numbering"]["A"]
    assert out["numbering"]["B"] == {"n": 0, "files": [], "gaps": 0, "duplicates": 0,
                                     "out_of_order": 0}
    assert out["win"]["n_pairs"] == 2 and out["m1"]["A"]["n_games"] == 2, \
        "点名改变了分母：破损局被悄悄剔出去了"
    md = out["markdown"]
    assert "## 编号破损" in md, md
    assert name in md, md
    assert "缺号 1 处" in md and "重号 1 个" in md, \
        "只报了破了几局、没报破在哪一类：删掉那一截括号，这一条读得出来"
    assert "编号是文件的问题，不是这一局算不算数的问题" in md, \
        "点名那一节没有说清这一局还在分母里（上一节的同一句话不算，它管的是退化局）"


def test_a_cut_last_line_is_blamed_on_the_file_and_not_on_the_model(tmp_path):
    """一局**打完了**的日志，末行就是 `game_over`。把它砍掉半行，`terminal` 从 `good_win` 变成
    `unfinished`（实测 04:40:29Z，同一份文件砍之前砍之后各读一遍），而批次只看 `terminal`——于是
    报告里那句"中断或未完 1"说的是模型的行为，真相是文件被人生砍。#55 那一节钉的是"没人说"，
    这一节钉的是**说错了**：归因错到被评测的对象身上，和 #39 对端点拒答做的是同一件事。

    三条腿各有分工：`n_dropped*` 那三条钉"归因不许变成第四个桶"（预注册的分母一个都不许动，
    plan §8）；`usable 对` 那一行钉"这一句必须就地改口"（读者不会翻到下一节才知道上一节在骗人）；
    `## 末行截断` 那一节钉每臂的字节数——它必须和 `replay` 那句话里同一个数，因为两边读的是
    `events.torn_extent` 这一只手。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    name, torn = _cut_tail(tmp_path, "A", 0)
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    assert out["torn"]["A"] == {"n": 1, "files": [name], "lines": 1, "chars": len(torn)}, \
        out["torn"]["A"]
    assert out["torn"]["B"] == {"n": 0, "files": [], "lines": 0, "chars": 0}
    assert out["win"]["n_dropped"] == 1 and out["win"]["n_dropped_other"] == 1, \
        "被砍的那一对不许新开一个桶：它本来就是『没有赢家』的一种，分母照旧"
    assert out["win"]["n_dropped_torn"] == 1
    assert out["win"]["n_pairs"] == 2 and out["m1"]["A"]["n_games"] == 2, \
        "点名改变了分母：被砍的局被悄悄剔出去了"

    md = out["markdown"]
    # `startswith` 不是洁癖：下面那一节的收尾句里也有"usable 对"这几个字，用 `in` 会把两行都捞进来，
    # 而"这一句必须就地改口"钉的正是被丢弃那一行——捞错行就变成在比对措辞。
    drop = [ln for ln in md.splitlines() if ln.startswith("- usable 对")]
    assert len(drop) == 1, drop
    assert "其中 1 对的日志末行被砍" in drop[0] and name in drop[0], drop[0]
    assert "不是这局没打完" in drop[0], drop[0]
    # 单独点名**那一节**的那一行：`name in md` 分不出是丢弃句里有的还是这一节里有的（两处都印），
    # 而丢弃句整段被删掉时，只剩这一节还在点名——所以这一节得自己站得住。
    sec = [ln for ln in md.splitlines() if ln.startswith("- A：") and "末行没能读成事件" in ln]
    assert len(sec) == 1, sec
    assert name in sec[0] and f"{len(torn)} 字节" in sec[0], sec[0]
    assert "## 末行截断" in md, md
    assert "被砍掉的那一行通常落在 `game_over` 上" in md, \
        "那一节没说清这些局为什么同时也不在 usable 对里"


def test_a_tail_canary_that_dies_still_leaves_a_manifest(tmp_path):
    """批跑到一半端点挂了：已经花掉的日志必须带着一份"这批不可用"的 manifest 落地，而不是
    抛异常留下没有 manifest 的孤儿目录——那时 compare 只能报 JSONDecodeError，钱和证据全丢。"""
    calls = {"n": 0}

    async def dies_later(prompt: str) -> dict:
        calls["n"] += 1
        if calls["n"] > len(batch.CANARY_PROMPTS):
            raise EndpointUnavailable("端点半路死了")
        return {"answer": "7", "latency_s": 1.0, "completion_tokens": 3}

    arms = _arms(A={}, B={"temperature": 0.6})
    res = asyncio.run(batch.run_batch(arms, games=1, seed0=41, out_dir=tmp_path,
                                      mock=True, canary=dies_later))
    man = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert man["canary"]["terminal"] == "CANARY_LOST", man["canary"]
    assert res.terminal == "CANARY_LOST"
    assert (tmp_path / "drift.md").exists()
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "CANARY_LOST" and "半路" in out["why"], out["why"]


def test_a_batch_without_a_canary_says_nobody_looked(tmp_path):
    """"没发现漂移"和"没人看过"必须能在报告里分开读：前者是证据，后者只是缺证据。"""
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    out = batch.compare(tmp_path, axis=("temperature",))
    assert "SKIPPED" in out["markdown"] and "未跑" in out["markdown"], out["markdown"]


def test_a_batch_with_a_matching_canary_reports_ok(tmp_path):
    """探针跑过且首尾一致 ⇒ 报告里是 `ok`，且不再带"未跑 canary"那句免责声明。
    这条同时钉住 `SKIPPED` 不是唯一的非失败状态——否则第 5 条闸门等于只认两种。"""
    async def probe(prompt: str) -> dict:
        return {"answer": prompt[:3], "latency_s": 1.2, "completion_tokens": 4}

    arms = _arms(A={}, B={"temperature": 0.6})
    asyncio.run(batch.run_batch(arms, games=2, seed0=31, out_dir=tmp_path,
                                mock=True, canary=probe))
    _as_real_table(tmp_path)
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    assert "`ok`" in out["markdown"] and "未跑" not in out["markdown"], out["markdown"]


def _invent_clock(tmp_path, seconds: float) -> None:
    """给批里每条事件补一个非零 `response.latency_s`，模拟"这局确实打在一个会慢的端点上"。

    实测：替身桌（MockActor）根本不经 transport，整份日志一个 `response` 字段都没有（不是 0.0，
    是没有），所以这里必须**写入**而不是改写。戳到每一条上是故意的——只挑 `DECISION_KINDS`
    等于在测试里再抄一份那个集合，改了名单的两边会一起静默。
    补时钟只为喂饱 M3 的延迟判据；M1 的算术不需要它。
    """
    for p in sorted(Path(tmp_path).glob("*/*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            rec = json.loads(line)
            if rec.get("seq") == 0:
                continue
            rec["response"] = {**(rec.get("response") or {}), "latency_s": seconds}
            lines[i] = json.dumps(rec, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_relabelling_a_mock_batch_cannot_turn_the_m3_gate_green(tmp_path):
    """`actor_kinds` 改一格就能让一批 mock 日志自称真桌（上面几条用例正是靠这个造 OK 路径）。
    闸门不能只看标签：它的延迟判据要的是**真实计时读数**，而替身桌压根没有读数（见 `_invent_clock`
    里那条实测）。于是这一批在 M1 里可以进算术（胜率、区间、配对），在 M3 里只能是"没法判"。"""
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")
    v = out["m3_verdict"]["A"]
    assert v["verdict"] == "NOT_EVALUABLE", v
    assert v["criteria"]["latency_p95_s"]["n"] == 0
    assert v["criteria"]["latency_p95_s"]["ok"] is None, v["criteria"]
    assert v["failed"] == []
    assert "闸门" in out["markdown"], out["markdown"]


def test_a_real_clock_gives_latency_a_reading_without_certifying_the_arm(tmp_path):
    """补上时钟，延迟那一格就该有读数——但**有了读数不等于这臂合格**：这张桌子的发言是替身座位
    写的（`_as_real_table` 只改页眉，`meta.rung` 仍然是 -1），主判据拿不到一句模型的话。
    钉三件事：延迟从空读数变成 3.0、JSON 与 markdown 说同一句话、闸门不许因为"四条都有数了"
    就给一张模型从没答过的桌发合格证（`#117`；这一条以前落在这里，是因为那时候闸门根本没有
    "这轮不是模型答的"这一格，PASS 是唯一可能的输出）。
    到底是哪一条红不取决于措辞分布（下一用例才去构造一个明确的 FAIL）。"""
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    _invent_clock(tmp_path, 3.0)
    out = batch.compare(tmp_path, axis=("temperature",))
    for arm in ("A", "B"):
        v = out["m3_verdict"][arm]
        assert v["criteria"]["latency_p95_s"]["value"] == pytest.approx(3.0)
        assert v["criteria"]["latency_p95_s"]["ok"] is True
        assert v["criteria"]["passivity_rate"]["ok"] is None, v["criteria"]["passivity_rate"]
        assert v["verdict"] == "NOT_EVALUABLE", v["verdict"]
        assert "引擎代打" in v["note"], v["note"]
        assert f"{arm}｜{v['verdict']}" in out["markdown"], (arm, v["verdict"], out["markdown"])


def _collapse_arm_b_speech(tmp_path) -> None:
    """把 B 臂每条发言换成同一句话：造一个只有 B 会多栽一条判据的不对称。

    配对批的两臂在 mock 下几乎逐字相同（同 seed、同一张替身表），"A 与 B 不同"这类断言在
    对称数据上是空断言——M1 那几条靠 `_flip_one_winner` 破这个，闸门这边靠复读。
    """
    for p in sorted((Path(tmp_path) / "B").glob("*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            rec = json.loads(line)
            if rec.get("kind") == "speech" and (rec.get("payload") or {}).get("text"):
                rec["payload"]["text"] = "我先听听别人怎么说再定。"
                lines[i] = json.dumps(rec, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_each_arm_gets_its_own_gate_verdict_rather_than_one_shared_answer(tmp_path):
    """两臂各判各的：把 B 的发言全换成同一句，B 就该比 A 多栽在措辞两条上，
    而 markdown 里两行 verdict 必须跟着各自的 JSON 走。

    读法上要小心：A 臂这里拿到的**不是**合格证——替身桌换了页眉仍然是替身桌，主判据因为
    "9/9 轮不是模型答的"被记成不计（`#117`），所以它是 NOT_EVALUABLE。B 臂那条 FAIL 是量出来的
    复读，测量失败优先于缺读数，不会被 A 那一格挡住。这条钉的还是"各臂各算"。"""
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    _invent_clock(tmp_path, 3.0)
    _collapse_arm_b_speech(tmp_path)
    out = batch.compare(tmp_path, axis=("temperature",))
    va, vb = out["m3_verdict"]["A"], out["m3_verdict"]["B"]
    assert (va["verdict"], va["failed"]) == ("NOT_EVALUABLE", []), va
    assert vb["verdict"] == "FAIL"
    assert vb["failed"] == ["collapse_round", "opening_distinct_rate"], vb
    assert vb["criteria"]["collapse_round"]["value"] == pytest.approx(1.0)
    assert "A｜NOT_EVALUABLE" in out["markdown"] and "B｜FAIL" in out["markdown"], out["markdown"]
    assert "- collapse_round = 1.0（需 < 0.35" in out["markdown"], out["markdown"]


# ------------------------------------------------------------------ region budget readout
def _games_in(tmp_path, arm: str) -> list[metrics.Game]:
    return [metrics.read_game(p) for p in sorted((Path(tmp_path) / arm).glob("*.jsonl"))
            if not p.name.endswith(".prompts.jsonl")]


def _edit_meta_regions(tmp_path, arm: str, edit) -> int:
    """就地改这一臂每份日志 `meta.regions`（返回改了几份）：造"没有尺子"和"两份尺子"这两种形状。

    正常批次打不出这两种形状——上限是 `Config` 的一部分，混了上限的目录会先被 `config_hash`
    闸门拒掉。能造出它们的只有手工改过的文件，而那恰恰是最不能让报告假装没事的时候。
    """
    n = 0
    for p in sorted((Path(tmp_path) / arm).glob("*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        head = json.loads(lines[0])
        if "regions" in (head.get("meta") or {}):
            head["meta"]["regions"] = edit(head["meta"]["regions"])
            lines[0] = json.dumps(head, ensure_ascii=False)
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            n += 1
    return n


def test_the_report_shows_each_arm_the_region_excess_its_own_logs_record(tmp_path):
    """上限已经跟着日志走了，批次报告就不该逼人对 80 个文件逐条 `wolf audit | jq`。

    B 臂那格的数字必须等于"这一臂各局里最狠的一次"，而且是**从日志读出来的**（本用例拿
    `metrics.region_budget_check` 自己算一遍去比 md 里印的那个数）：报告里最坏的错是一个看着
    合理却来自第二处实现的数。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_total": 250}), games=2)
    _as_real_table(tmp_path)
    out = batch.compare(tmp_path, axis=("regions.c_total",))
    assert out["verdict"] == "OK", out.get("why")
    per = out["region_budget"]
    assert per["A"]["worst_over"] == {k: 0 for k in metrics.REGION_CAP_KEYS}, per
    b_games = _games_in(tmp_path, "B")
    expect = max(metrics.region_budget_check(g)["worst_over"]["C"] for g in b_games)
    assert expect > 0, "这一批没造出超额，用例是空的"
    assert per["B"]["worst_over"]["C"] == expect, per
    assert per["B"]["games_over"]["C"] == len(b_games), "每一局的 C 都压不过地板，局数要跟着说"
    md = out["markdown"]
    assert "区域预算" in md and f"| {expect} |" in md, md


def _row(md: str, arm: str) -> str:
    """预算那一节里这一臂的那一行；按行找而不是整篇匹配，因为列宽跟着数字位数变。

    先切到那一节再找：报告里 M1、退化局、逐条率三张表也用 `| A |`、`| B |` 开头，全文找会
    一次撞上好几行。切到**下一节标题为止**同理会跟着成立——前缀缓存那一节就接在区域预算后面。
    """
    body = md.split("## 区域预算", 1)[1].split("\n## ", 1)[0]
    rows = [ln for ln in body.splitlines() if ln.startswith(f"| {arm} |")]
    assert len(rows) == 1, (arm, rows)
    return rows[0]


def test_an_arm_without_the_caps_in_its_logs_says_no_rather_than_zero(tmp_path):
    """缺尺子的臂写"无上限读数"，不写 0：0 是"每一段都舒舒服服待在上限以内"，那句话没人说过。

    同一份报告里 A 臂仍然有数字，所以这一条顺手钉住"一格缺尺子不把整张表抹成 —"——反过来也
    成立：A 行必须是数字，不然"B 行是 —"只是把整张表放弃了的症状。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_total": 250}), games=2)
    _as_real_table(tmp_path)
    assert _edit_meta_regions(tmp_path, "B", lambda r: None) == 2
    out = batch.compare(tmp_path, axis=("regions.c_total",))
    per = out["region_budget"]
    assert per["B"]["worst_over"] is None and per["B"]["n_without_caps"] == 2, per
    assert per["A"]["worst_over"] == {k: 0 for k in metrics.REGION_CAP_KEYS}, \
        "另一臂不该被牵连"
    md = out["markdown"]
    assert "无上限读数" in md, md
    assert "—" in _row(md, "B"), _row(md, "B")
    assert "—" not in _row(md, "A"), _row(md, "A")
    assert per["B"]["card_prompts_thinned"] > 0, \
        "刀的读数用不着尺子，缺 `meta.regions` 的臂也照报自己砍过几条"
    assert per["A"]["card_prompts_thinned"] == 0, "出厂预算那一臂从没动过刀"


def test_an_arm_whose_logs_carry_two_different_caps_refuses_to_pick_one(tmp_path):
    """一臂里出现两份上限，超额就不能减：那是两种规则的平均值，谁也不是。

    正常批次打不出这个形状（上限进了 `config_hash`，混批先被 hash 闸门拒掉），所以只能改文件
    来造——而"改过的文件"恰恰是这份表唯一需要防的东西。反向对照跟着：单独读被改的那一局，
    它仍然报得出自己那把尺子下的数，所以 `None` 是分歧造成的，不是读不出来。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_total": 250}), games=2)
    _as_real_table(tmp_path)
    path = [p for p in sorted((tmp_path / "B").glob("*.jsonl"))
            if not p.name.endswith(".prompts.jsonl")][0]
    lines = path.read_text(encoding="utf-8").splitlines()
    head = json.loads(lines[0])
    head["meta"]["regions"]["c_total"] = 900
    lines[0] = json.dumps(head, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    games = _games_in(tmp_path, "B")
    assert path.samefile(Path(games[0].path)), "反向对照要读的就是被改的那一局"
    per = batch.region_budget_by_arm(games)
    assert per["worst_over"] is None and per["distinct_caps"] == 2, per
    single = metrics.region_budget_check(games[0])
    assert single["caps"]["c_total"] == 900 and single["worst_over"]["C"] is not None, single
    out = batch.compare(tmp_path, axis=("regions.c_total",))
    assert "两份上限" in out["markdown"], out["markdown"]
    assert "—" in _row(out["markdown"], "B"), _row(out["markdown"], "B")


def _drop_request_field(tmp_path, field: str, *arms: str, limit: int | None = None) -> int:
    """删掉这些臂每条请求里的 `field`，返回删了几条——造"这一格从没落过盘"的批次。

    老日志就是这个形状：某一格比另一格早上线还是晚上线，取决于批次是哪天打的，而报告分不出
    "这一格量过且没问题"和"根本没量过"。多臂参数是必须的：只剥一臂的话，`all(... is None)`
    写错成 `any(...)` 在单臂工具上永远测不出来。`limit` 只剥该臂前 N 份文件，造的是臂内半缺——
    那是 `all` 与 `any` 唯一的分界，见
    :func:`test_half_an_arm_without_the_witness_keeps_the_reading_that_survived`。
    """
    n = 0
    for arm in arms:
        files = sorted((Path(tmp_path) / arm).glob("*.jsonl"))
        if limit is not None:
            files = files[:limit]
        for p in files:
            if p.name.endswith(".prompts.jsonl"):
                continue
            lines = p.read_text(encoding="utf-8").splitlines()
            out = []
            for ln in lines:
                rec = json.loads(ln)
                req = rec.get("request")
                if isinstance(req, dict) and field in req:
                    req.pop(field)
                    n += 1
                    ln = json.dumps(rec, ensure_ascii=False)
                out.append(ln)
            p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n


def _drop_b2_witness(tmp_path, arm: str) -> int:
    """`b2_over_cap` 那一格的单臂快捷方式。"""
    return _drop_request_field(tmp_path, "b2_over_cap", arm)


def _drop_knife_witness(tmp_path) -> int:
    """两臂的 `card_claims_dropped` 一起剥光：装配器还没记这一格时批次就是这个形状。"""
    return _drop_request_field(tmp_path, "card_claims_dropped", "A", "B")


def test_an_arm_nobody_cross_checked_prints_no_rather_than_zero(tmp_path):
    """没有证人的臂写"无读数"，不写 0：0 是"每条都对上了"，那句话这一臂没资格说。

    反向对照跟着前两格：证人没了不影响超额那一栏（那是从长度和上限减出来的，不需要证人），
    所以 `—` 只出现在证人那一列。整行抹成 `—` 和只抹一格是两件事，后者才说得出证据在哪。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_total": 250}), games=2)
    _as_real_table(tmp_path)
    games_a = _games_in(tmp_path, "A")
    assert sum(1 for g in games_a
               if metrics.region_budget_check(g)["b2_witness_agrees"] is True), "证人本来是在的"
    assert _drop_b2_witness(tmp_path, "A") > 0
    per = batch.region_budget_by_arm(_games_in(tmp_path, "A"))
    assert per["witness_disagreements"] is None, per
    assert per["worst_over"] == {k: 0 for k in metrics.REGION_CAP_KEYS}, \
        "少一根证人不该带走整行"
    out = batch.compare(tmp_path, axis=("regions.c_total",))
    row = _row(out["markdown"], "A")
    assert "—" in row, row
    assert row.count("—") == 1, "只有证人那一格该没读数：" + row
    assert "0" in _row(out["markdown"], "B").split("|")[3], "另一臂的证人不受牵连"


def test_a_summary_only_one_arm_could_measure_does_not_speak_for_both(tmp_path):
    """表格里 B 行的 `—` 是诚实的，摘要那句"两臂的证人全部与长度一致"却不是。

    一格一格的 null 已经由上面几条钉住了，这一条钉的是**跨格的那句话**：合并两臂的汇总最容易
    滑过去的正是"把没测到的那一臂算进测到的里面"，而读者读到"全部一致"时不会回头数列。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_total": 250}), games=2)
    _as_real_table(tmp_path)
    assert _drop_b2_witness(tmp_path, "B") > 0
    out = batch.compare(tmp_path, axis=("regions.c_total",))
    sec = out["markdown"].split("## 区域预算", 1)[1].split("\n## ", 1)[0]
    assert "全部与长度一致" not in sec, sec
    assert "B" in sec and "管不着" in sec, "要点名那句对哪一臂说不上：" + sec
    assert "A 臂" in sec, "有读数的那一臂该照样说：" + sec
    assert _row(out["markdown"], "B").split("|")[3].strip() == "—"
    assert _row(out["markdown"], "A").split("|")[3].strip() == "0"


def _forge_b2_witness(tmp_path, arm: str, value: int = 9001) -> int:
    """把这一臂第一份日志里第一条请求的 `b2_over_cap` 改成 `value`（返回改了几条，这里要 1）。

    只改第一条：一臂里"多数对得上、有一条对不上"才是臂级汇总会结巴的地方——全改的话"有几局对
    不上"和"有没有对不上的"两种实现打印出同一个东西，分辨不出来（`tests/test_cli.py` 里那具
    `all`→`any` 的变异体就是这么逃过第一版的）。
    """
    for p in sorted((Path(tmp_path) / arm).glob("*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            rec = json.loads(ln)
            req = rec.get("request")
            if isinstance(req, dict) and "b2_over_cap" in req:
                req["b2_over_cap"] = value
                lines[i] = json.dumps(rec, ensure_ascii=False)
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return 1
    return 0


def test_two_arms_with_no_witness_at_all_say_so_rather_than_agreeing(tmp_path):
    """两臂都没有证人时，那一栏整列没读数——"没有分歧"和"没有可核对的东西"必须分得开。

    上一条钉的是一臂缺、这一条钉两臂都缺：合并逻辑在最坏形状下的输出就是它自己的下限，
    缺一条的话"`measured` 为空"这分支可以随便写坏而没人红。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_total": 250}), games=2)
    _as_real_table(tmp_path)
    assert _drop_b2_witness(tmp_path, "A") > 0, "A 臂本来是有证人的"
    assert _drop_b2_witness(tmp_path, "B") > 0, "两臂都要真的缺，否则钉的是上一条的形状"
    out = batch.compare(tmp_path, axis=("regions.c_total",))
    sec = out["markdown"].split("## 区域预算", 1)[1].split("\n## ", 1)[0]
    assert "整列没有读数" in sec, sec
    assert "对得上" not in sec and "一致" not in sec, sec
    assert _row(out["markdown"], "B").split("|")[3].strip() == "—"


def test_one_disagreement_prints_one_game_not_a_prompt_count(tmp_path):
    """一条 prompt 被改过 → 那一臂的证人格写的是**局数** 1，摘要那句也得跟着说。

    这张表前面几条全在测"没读数"，而 `total` 那一支一次都没被非零走过：写死成 0 也不会红，
    "先怀疑文件被改过"这句话就只是印在代码里的。只伪造一条而不是全部，是为了让"数局数"和
    "有没有对不上"两种实现分得开。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_total": 250}), games=2)
    _as_real_table(tmp_path)
    assert _forge_b2_witness(tmp_path, "B") == 1
    out = batch.compare(tmp_path, axis=("regions.c_total",))
    assert out["region_budget"]["B"]["witness_disagreements"] == 1, out["region_budget"]
    assert out["region_budget"]["A"]["witness_disagreements"] == 0, "另一臂不该被牵连"
    sec = out["markdown"].split("## 区域预算", 1)[1].split("\n## ", 1)[0]
    assert "证人对不上" in sec, sec
    assert _row(out["markdown"], "B").split("|")[3].strip() == "1", _row(out["markdown"], "B")
    assert _row(out["markdown"], "A").split("|")[3].strip() == "0", _row(out["markdown"], "A")


def _seed_knife(tmp_path, arm: str, value: int = 4) -> int:
    """把这一臂每份日志**第一条**请求的 `card_claims_dropped` 写成 `value`，返回动了几局。

    每局动一条、只动 B 臂：这样"臂级的数"就是 2 而不是 24，而"跨局求和"与"数有几局非零"两种
    写法在一条全改的日志上分不出来。
    """
    n = 0
    for p in sorted((Path(tmp_path) / arm).glob("*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            rec = json.loads(ln)
            req = rec.get("request")
            if isinstance(req, dict) and "card_claims_dropped" in req:
                req["card_claims_dropped"] = value
                lines[i] = json.dumps(rec, ensure_ascii=False)
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
                n += 1
                break
    return n


def test_only_one_arm_having_the_knife_count_is_named_rather_than_summed(tmp_path):
    """一臂有读数、一臂没有：`—` 只许盖在没读数那一臂头上，也不许把两臂合起来说一句。

    跨格合并最容易滑过去的正是"把没有的那一臂算进有的里面"（证人那一栏由
    `test_a_log_without_the_caps_in_its_logs_says_no_rather_than_zero` 钉过同样的形状，这里是
    第二格）。这一条也是 `all(... is None)` 的判据：写成 `any(...)` 时 A 臂那两个数会被整行
    抹成"没有读数"，而两臂全缺的那一条用例分辨不出这两种写法。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_belief": 9999}), games=2)
    _as_real_table(tmp_path)
    assert _seed_knife(tmp_path, "A") == 2
    assert _drop_request_field(tmp_path, "card_claims_dropped", "B") >= 2, "B 臂要真的剥干净"
    out = batch.compare(tmp_path, axis=("regions.c_belief",))
    per = out["region_budget"]
    assert per["A"]["card_prompts_thinned"] == 2, per
    assert per["B"]["card_prompts_thinned"] is None, per
    sec = out["markdown"].split("## 区域预算", 1)[1].split("\n## ", 1)[0]
    assert "A 臂 2 个" in sec and "B 臂 —" in sec, sec
    assert "两臂的日志里都没有" not in sec, sec


def test_a_batch_from_before_the_knife_count_says_it_has_no_reading(tmp_path):
    """两臂都没有 `card_claims_dropped` 时，那一行要说"没有读数"，不能印"0 个"。

    把"找不到这一格"读成 0，报告就会对着一批从没记过刀的日志宣布"没有一个 prompt 被削过主张
    卡"——那句好消息没人量过。这一族已经由 `_drop_b2_witness` 那两条钉过证人那一栏，这里是同一
    个错误在第二格上的形状：两臂都得剥，只剥一臂的话 `all(... is None)` 写错成 `any(...)` 也
    照样绿（下面那条变异就是照这个写的）。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_belief": 9999}), games=2)
    _as_real_table(tmp_path)
    assert _drop_knife_witness(tmp_path) >= 4, "两臂每条请求都要剥干净，否则钉的是单边形状"
    out = batch.compare(tmp_path, axis=("regions.c_belief",))
    per = out["region_budget"]
    assert per["A"]["card_prompts_thinned"] is None, per
    assert per["B"]["card_prompts_thinned"] is None, per
    sec = out["markdown"].split("## 区域预算", 1)[1].split("\n## ", 1)[0]
    assert "card_claims_dropped" in sec and "没有读数" in sec, sec


def test_half_an_arm_without_the_witness_keeps_the_reading_that_survived(tmp_path):
    """一臂里只有一局丢了证人，另一局还在：这一臂仍要有读数，不能跟着整臂变 `—`。

    `None` 的判据写的是"这一臂没有一局量过"，不是"这一臂有一局没量过"。写成 `any(...)` 就
    是第三种错：一份日志被截断、或某局在字段上线前打的，会把同臂其他局的量测一起抹掉——而
    报告上留下的那句"没有读数"看起来仍是诚实的。三格（`witness_disagreements`、
    `card_prompts_thinned`、`card_worst_claims_dropped`）共用这一条规则，所以一起断言。

    上面两条臂级的用例（全有 / 全无）分辨不出 `all` 与 `any`：只有半缺这一格能。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_belief": 9999}), games=2)
    _as_real_table(tmp_path)
    assert _seed_knife(tmp_path, "B") == 2, "两局各留一条被削过的 prompt"
    assert _drop_request_field(tmp_path, "card_claims_dropped", "B", limit=1) > 0
    assert _drop_request_field(tmp_path, "b2_over_cap", "B", limit=1) > 0
    per = batch.compare(tmp_path, axis=("regions.c_belief",))["region_budget"]
    b = per["B"]
    assert b["card_prompts_thinned"] == 1, b
    assert b["card_worst_claims_dropped"] == 4, b
    assert b["witness_disagreements"] == 0, \
        f"活下来的那一局证人是对上的：{b}（丢掉的那局不算分歧，也不把整臂读成没测过）"
    assert per["A"]["card_prompts_thinned"] == 0, per


def test_the_report_counts_prompts_whose_card_was_thinned(tmp_path):
    """"这一臂的模型少看了几条指控"是处理差异，不是噪声，所以它得跨过 audit 那一级。

    出厂预算下三局 mock 的 C2 峰值 328（11:12:16Z），刀从不落下，于是这个读数在单局里永远是 0、
    谁都看不出它缺不缺。臂级才是它被读的地方：两臂的 `C` 长度不同本来就是 §5 的预期，只有"其中
    一条 prompt 被砍过"能把"预算宽紧"和"喂给模型的事实少了一半"分开。A 臂那两个 0 是这条用例的
    另一半——不写反向对照，"恒 0"的实现和正确的实现在 B 臂那一格上长得一样。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_belief": 9999}), games=2)
    _as_real_table(tmp_path)
    assert _seed_knife(tmp_path, "B") == 2, "两局各写一条"
    out = batch.compare(tmp_path, axis=("regions.c_belief",))
    assert out["verdict"] == "OK", out["why"]
    per = out["region_budget"]
    assert per["B"]["card_prompts_thinned"] == 2, per
    assert per["B"]["card_worst_claims_dropped"] == 4, per
    assert per["A"]["card_prompts_thinned"] == 0 and per["A"]["card_worst_claims_dropped"] == 0, per
    sec = out["markdown"].split("## 区域预算", 1)[1].split("\n## ", 1)[0]
    assert "主张卡" in sec and "2" in sec, sec


def test_the_budget_table_header_names_every_ruler_the_rows_carry(tmp_path):
    """表头是手抄的四段，行里的格子却跟着 `REGION_CAP_KEYS` 长——#63 把尺子从 4 涨到 9 之后就错位了。

    markdown 不抱怨列数对不上：多出来的格子被塞进最后一列，读者仍看到"A B1 B2 C"四个标题，
    下面那一行却是九个数。所以这一条不是排版洁癖，而是"表上写的字与表里装的数不是一回事"——
    这一族一直在拆的东西（#27/#28/#48）。修法是把表头也交给同一个来源：以后再加一把尺子，
    标题自己跟着长，不需要有人记得改第二处。`<= set(cols)` 那一格防止这条断言在名单缩回
    四格时照样绿。
    """
    _paired(tmp_path, _arms(A={}, B={"regions.c_total": 250}), games=2)
    _as_real_table(tmp_path)
    md = batch.compare(tmp_path, axis=("regions.c_total",))["markdown"]
    lines = [ln for ln in md.split("## 区域预算", 1)[1].split("\n## ", 1)[0].splitlines()
             if ln.startswith("|")]
    assert len(lines) >= 4, f"表连数据行都不齐：{lines}"
    head, rule, *rows = lines
    cols = [c.strip() for c in head.strip("|").split("|")]
    assert cols == ["臂", "局数", "证人分歧", *metrics.REGION_CAP_KEYS], cols
    assert {"B0", "C1", "C2", "C3", "C4"} <= set(cols), cols
    for ln in [rule, *rows]:
        assert len(ln.strip("|").split("|")) == len(cols), f"这一行与表头列数对不上：{ln}"



# ----------------------------------------------------------- 逐条率表里的"配对收益"那一列
def _flip_uncited(tmp_path, arm: str) -> int:
    """把这一臂**隔一条**发言的 `citation_stats.uncited` 翻个面，返回改了几条。

    配对批的两臂是同 seed 跑出来的，逐条率因此逐位相同：每一副重采样得到同一个差，区间宽度 0，
    这一列永远是 `—`。要让它有数，只能让两臂的分子真的不同——改的是日志里的读数而不是配置，
    因为轴上任何配置差异都已经被 `--set` 走过了，而 mock 不读温度。
    """
    n = 0
    for p in sorted((Path(tmp_path) / arm).glob("*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        out, seen, changed = [], 0, False
        for line in p.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            stats = ((rec.get("payload") or {}).get("meta") or {}).get("citation_stats")
            if isinstance(stats, dict):
                seen += 1
                if seen % 2:
                    stats["uncited"] = not stats.get("uncited")
                    changed, n = True, n + 1
                    out.append(json.dumps(rec, ensure_ascii=False))
                    continue
            out.append(line)
        if changed:
            p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n


def _rate_row(md: str, metric: str) -> list[str]:
    rows = [ln.split("|") for ln in md.split("\n") if ln.startswith(f"| {metric} |")]
    assert len(rows) == 1, rows
    return [c.strip() for c in rows[0]]


def test_the_pairing_gain_is_the_ratio_of_the_two_intervals_it_is_named_after(tmp_path):
    """`配对收益` 这个名字是一句承诺：非配对区间宽度 ÷ 本表区间宽度。此前没有任何断言读过这一格，
    而 docs/comparison.md 早就把它写成表里的一列——散文先于读数，是这个仓库反复犯的那类错。

    测试自己从日志重算两臂的逐局分子/分母，再拿 `report` 的非配对那一路量出对照区间。这样
    "拿 naive 宽度冒充非配对宽度""两臂串了""比值写反"三类改动各能红一次，而不是红一次代表三类。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=4)
    _as_real_table(tmp_path)
    assert _flip_uncited(tmp_path, "B") > 0
    a = [batch.game_rates(g)["uncited_speech"] for g in _games_in(tmp_path, "A")]
    b = [batch.game_rates(g)["uncited_speech"] for g in _games_in(tmp_path, "B")]
    assert a != b, "两臂逐局分子完全一样，这一列无从测起——是 fixture 没生效，不是结论"

    out = batch.compare(tmp_path, axis=("temperature",))
    v = out["utterance"]["uncited_speech"]
    assert v["ci_halfwidth"], "配对区间宽度为 0，这一格的除法无从谈起"
    unpaired = report.cluster_bootstrap_rate_diff(a, b, paired=False)
    assert unpaired["ci_halfwidth"], "非配对那一路也没读数，钉不住比值"
    assert v["deff_gain"] == round(unpaired["ci_halfwidth"] / v["ci_halfwidth"], 2), (
        v, unpaired["ci_halfwidth"])
    assert _rate_row(out["markdown"], "uncited_speech")[7] == f"×{v['deff_gain']}", (
        "印出来的必须是同一个数")


def test_a_pairing_gain_below_one_is_a_conclusion_not_a_missing_reading(tmp_path):
    """实测：把两臂的分子改开之后，3/4/6 局的 mock 批给出 ×0.73 / ×0.84 / ×0.88——**小于 1**。

    名字里写着"收益"的一列印出小于 1 的数，读者只会往两个方向之一想：配对失效了，或者数据不够。
    两个都不对——它是这批数据的一个结论（这批上配对让区间变宽），而"数据不够"另有记号（`—`）。
    所以报告自己必须把这句话说出来，不能留给 docs。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=4)
    _as_real_table(tmp_path)
    _flip_uncited(tmp_path, "B")
    out = batch.compare(tmp_path, axis=("temperature",))
    v = out["utterance"]["uncited_speech"]
    assert v["deff_gain"] is not None and v["deff_gain"] < 1, v
    sec = out["markdown"].split("## 逐条率", 1)[1].split("\n## ", 1)[0]
    assert "不是缺数据" in sec, "报告没有分辨“`×0.8`（量出来了，配值是负的）”和“`—`（没量出来）”"


def test_a_batch_with_identical_arms_prints_no_pairing_gain(tmp_path):
    """两臂逐位相同（同 seed 的 mock 批本来就是这样）时这一格必须是"没有读数"，不是 `×0`。

    `×0` 读起来像"配对一点没换来东西"——那是一条结论；真相是这批没有可测的差。前者会让人去
    改配对实现，后者只需要多跑几局。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=3)
    _as_real_table(tmp_path)
    out = batch.compare(tmp_path, axis=("temperature",))
    v = out["utterance"]["uncited_speech"]
    assert v["ci_halfwidth"] == 0.0, f"这批本该对称：{v['ci']}"
    assert v["deff_gain"] is None, v
    cell = _rate_row(out["markdown"], "uncited_speech")[7]
    assert not any(ch.isdigit() for ch in cell), f"没读数被印成了一个数：{cell}"


def test_compare_refuses_a_cell_with_no_code_even_when_the_manifest_never_met_the_door(tmp_path):
    """门口那道 `BadOverride` 只挡命令行，而 `compare` 读的是 manifest 里记下来的配置——这一只
    批次从头到尾没经过那道门（臂自己带着 `tokens.warn` 的差异落地，旧版本的批就是这个形状）。

    差异是**手工造**的而不是改 manifest 造出来的：改 manifest 会先撞上 `_hash_integrity`
    （日志里记的 hash 与 manifest 不符），那条路要测的是另一件事。这里两臂的日志逐字相同——
    因为 `warn` 后面没有代码——所以报告若出结论，说的就是"处理效应"，而它量到的是一枚标签。

    这一只以前用的是 `regions.b0`。`#63` 给 B0 配上读数和尺子之后它就有了代码，所以换到这张
    名单里还剩的两格之一——名单会自己变，判据不能跟着烂。
    """
    b_cfg = dataclasses.replace(Config(),
                                tokens=dataclasses.replace(Config().tokens, warn=1))
    _paired(tmp_path, [batch.Arm("A", Config()),
                       batch.Arm("B", b_cfg, overrides=("tokens.warn",))], games=2)
    out = batch.compare(tmp_path, axis=("tokens.warn",))
    assert out["verdict"] == "AXIS_VIOLATION", out.get("why")
    assert "tokens.warn" in out["markdown"] and "没有代码" in out["markdown"]
    assert "配对前提已失效" not in out["markdown"], \
        "身份那一类的文案会把人送去重跑批次，而这一格要的是把代码补上"


def _flip_fallback_copy(tmp_path, arm: str, i: int = 0, *, pick: int = 0) -> str:
    """把某一局里一条决策记录的 `result.fallback` 就地翻掉，返回文件名。

    引擎写不出两份不一致的拷贝：`payload.meta.fallback` 由判官侧落笔、`result.fallback` 由每次调用
    落笔，正常路径上同源。所以这一格只能在文件里造——和 `_damage_seq`、`_cut_tail` 同一类反例，
    钉的是"批次侧读得出来"，不是"引擎会犯这个错"。只改 `result` 那一份：不碰 `seq`、不碰末行，
    那两格各有自己的点名节，混进来就分不出是谁报的了。

    挑记录用 `metrics.decisions` 而不是自己认 kind：这一片测的就是"两份拷贝都带 key 的那些记录"，
    判据跟被聚合的那只手共用一份，才不会测完还不知道对不对得上。
    """
    path = [p for p in sorted(Path(tmp_path, arm).glob("*.jsonl"))
            if not p.name.endswith(".prompts.jsonl")][i]
    game = metrics.read_game(path)
    seqs = sorted(e.seq for e in metrics.decisions(game.events)
                  if "fallback" in e.payload.get("meta", {}) and "fallback" in e.result)
    assert len(seqs) > pick, f"{path.name}: 只有 {len(seqs)} 条带两份拷贝的记录，翻不到第 {pick} 条"
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines):
        rec = json.loads(line)
        if rec.get("seq") == seqs[pick] and isinstance(rec.get("result"), dict):
            rec["result"]["fallback"] = 0 if rec["result"]["fallback"] else 1
            lines[n] = json.dumps(rec, ensure_ascii=False)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return path.name
    raise AssertionError(f"{path.name}: seq={seqs[pick]} 的记录不在文件里")


def test_the_batch_side_names_files_whose_two_fallback_copies_disagree(tmp_path):
    """`audit` 从 #119 起会对账，批次侧以前没人聚合：一臂 40 局里有一条对不上，`compare` 是沉默的。

    钉三件事：`n` 按**文件**计而 `divergent` 按**条**计（一份文件里两条对不上是一局的事，不是两局）；
    找到不一致**不缩小分母**——那条记录既被比过也被点名，所以 `n_compared` 在对上前后必须同一个数；
    键的形状跟着 `metrics.fallback_copy_check` 走，批次这只手不许自己另起一套名字。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    clean = batch.fallback_copies_by_arm(_arm_games(tmp_path, "A"))
    assert clean == {"n": 0, "files": [], "n_compared": clean["n_compared"], "divergent": 0}, clean
    assert clean["n_compared"] > 0, "0 条都没比过的臂，报 0 和报不出来是同一句话"

    name = _flip_fallback_copy(tmp_path, "A")
    a = batch.fallback_copies_by_arm(_arm_games(tmp_path, "A"))
    assert a == {"n": 1, "files": [name], "n_compared": clean["n_compared"], "divergent": 1}, \
        "对不上的那条被剔出分母了：分母一动，这一格就不再是#119 那只手的口径"
    assert set(a) == {"n", "files"} | set(metrics.fallback_copy_check([])), \
        "批次这一格的键和那只共用的手脱钩了"
    assert batch.fallback_copies_by_arm(_arm_games(tmp_path, "B")) == clean, \
        "另一臂一个字没改，读数就得一个字不变"

    # 同一份文件里再翻一条：`n`（按文件）与 `divergent`（按条）必须分开活着。少了这一腿，
    # `sum(d["divergent"] ...)` 换成 `sum(1 for d in per if d["divergent"])` 也能全绿——
    # 那份实现把"一份文件漂了两条"和"两局各漂一条"压成同一个数，而报告点的是文件。
    _flip_fallback_copy(tmp_path, "A", pick=1)
    a2 = batch.fallback_copies_by_arm(_arm_games(tmp_path, "A"))
    assert a2 == {"n": 1, "files": [name], "n_compared": clean["n_compared"], "divergent": 2}, \
        "条数被当成局数归约了：局和条是两件事，报告点名的那一格要的是前者"


def test_the_batch_report_prints_the_reconciled_denominator_for_both_arms(tmp_path):
    """报告里这一节存在的理由：`0 条对不上` 只有在同一行印出"比过多少条"时才是读数。

    两臂各一行、干净那一臂照印（`_torn_md` 同一形状）：只在出问题时才开口的守卫，读者分不清
    "查过、没有"和"没人查"，而后者正是 #119 之前所有批次的真实状态。
    """
    _paired(tmp_path, _arms(A={}, B={"temperature": 0.6}), games=2)
    _as_real_table(tmp_path)
    name = _flip_fallback_copy(tmp_path, "A")
    out = batch.compare(tmp_path, axis=("temperature",))
    assert out["verdict"] == "OK", out.get("why")

    cell = out["fallback_copies"]["A"]
    assert cell["n"] == 1 and cell["files"] == [name] and cell["divergent"] == 1, cell
    assert cell["n_compared"] > 0, cell
    games_a = _arm_games(tmp_path, "A")
    assert cell["n_compared"] == sum(metrics.fallback_copy_check(g.events)["n_compared"]
                                     for g in games_a), "批次这一格和逐局那只手对不上数"

    md = out["markdown"]
    assert "## 两份 `fallback` 拷贝对账" in md, md
    row_a = [ln for ln in md.splitlines() if ln.startswith("- A：") and "两份拷贝对不上" in ln]
    row_b = [ln for ln in md.splitlines() if ln.startswith("- B：") and "两份拷贝对不上" in ln]
    assert len(row_a) == 1 and len(row_b) == 1, (row_a, row_b)
    assert name in row_a[0] and f"比过 {cell['n_compared']} 条" in row_a[0], row_a[0]
    b_cell = out["fallback_copies"]["B"]
    assert f"比过 {b_cell['n_compared']} 条" in row_b[0] and "0 条两份拷贝对不上" in row_b[0], row_b[0]
    assert b_cell["n_compared"] > 0, "干净臂连分母都没印出来：这一节就退化成一句'没问题'"
