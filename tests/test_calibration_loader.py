"""calibration sidecar 的读取策略：够不着的常数 must not 变成一个自信的 `null`。

`scripts/calibrate.py` 采完数据会落两份东西：给人读的 `docs/calibration.md`，和给机器读的
`data/calibration.json`（sidecar）。而 `m7_cost_profile` 的 drift 自检要的恰好是机器能读的
那三个数——问题在于**过去没有任何代码去读那个文件**：`audit` 里 `constants` 永远是
`None`，自检永远是 `drift: None`，一段写得挺对、但谁也喂不到它嘴里的死代码。

接起来之后，真正的风险从"没有数字"变成"有数字但不该用"：端点换了模型、上一批的 sidecar
只跑完一半、`per_call_fixed_overhead_s` 没测出来。所以这里钉的全是**拒绝使用的条件**，
以及被拒绝时说的是不是当时真正的原因。仓库里现存的那份 `data/calibration.json` 恰好是
最老的一种形状（连 `throughput` 都没有，更没有 constants 块），第 4 条测试就是照它的形状写的。

一句反面教训写在这儿免得再犯：note 里的键名是从**读到的文件**里来的，不是从测试的 fixture
里来的——把"P 为 null"写成"D 为 null"正好是这类代码最贵的错，因为它把人支到不存在的问题上。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from wolfengine import metrics

ROOT = Path(__file__).resolve().parents[1]

COMPLETE = {"D_decode_tok_s": 41.2, "per_call_fixed_overhead_s": 0.83,
            "P_prefill_tok_s_best_observed": 3664.2}


def _write(tmp_path: Path, payload: dict, name: str = "calibration.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _sidecar(tmp_path: Path, *, constants=COMPLETE, model="gemma-fit", **top) -> Path:
    body = {"ran_utc": "2026-09-20T18:45:36Z", "quick": False, "model": model,
            "features": {}, "stream": {}, "ratio": {}, "throughput": {},
            "latency": [], "temps": []}
    if constants is not None:
        body["constants"] = constants
    body.update(top)
    return _write(tmp_path, body)


# ------------------------------------------------------------------ 什么时候可以用
def test_a_measured_sidecar_returns_the_constants_and_its_own_provenance(tmp_path):
    path = _sidecar(tmp_path)
    cal = metrics.load_calibration(path)
    assert cal["usable"] is True
    assert cal["constants"] == COMPLETE
    # 数字要带着出处：drift 是一个比值，读的人在报告里看到 1.9× 时无法判断它是新数据还是
    # 半个月前另一台机器上采的，除非这两件事印在同一格里。
    assert cal["ran_utc"] == "2026-09-20T18:45:36Z" and cal["model"] == "gemma-fit"
    assert cal["source"] == str(path)


def test_the_default_call_never_raises_and_never_reads_the_cwd(tmp_path):
    """没有 `--calibration` 时 `audit` 不碰文件系统：一个 JSON 对象的输出如果取决于运行时
    的工作目录，那它就不再是"日志的纯函数"，也没法再拿去对账。"""
    cal = metrics.load_calibration(tmp_path / "does-not-exist.json")
    assert cal["usable"] is False and cal["constants"] is None
    assert "does-not-exist.json" in cal["note"], "note 要点名它找的是哪个路径"
    assert "不存在" in cal["note"]


# ------------------------------------------------------------------ 什么时候必须拒绝
def test_a_file_that_is_not_json_is_reported_as_a_file_problem(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text("{ this is not json", encoding="utf-8")
    cal = metrics.load_calibration(path)
    assert cal["usable"] is False
    assert "解析" in cal["note"] and "constants" not in cal["note"], \
        "JSON 都读不出来，就不要假装知道它缺哪个键"


def test_the_legacy_sidecar_shape_is_named_as_a_missing_block_not_a_null_key(tmp_path):
    """仓库里现存的那份 sidecar 就是这个形状：一次跑废了的体检，连 `throughput` 都没落下来。
    对它说"constants 为空"是错的——文件里根本没有这一块，正确的建议是重跑体检（或离线
    backfill），而不是去补某个键。"""
    path = _write(tmp_path, {"features": {}, "stream": {}, "ratio": {},
                             "latency": [], "temps": []})
    cal = metrics.load_calibration(path)
    assert cal["usable"] is False
    for key in COMPLETE:
        assert key not in cal["note"], f"文件里没这一说，note 不该点名 {key}"
    assert "constants" in cal["note"]


@pytest.mark.parametrize("missing", sorted(COMPLETE))
def test_a_null_constant_names_the_key_that_is_null(tmp_path, missing):
    consts = dict(COMPLETE, **{missing: None})
    cal = metrics.load_calibration(_sidecar(tmp_path, constants=consts))
    assert cal["usable"] is False and cal["constants"] is None
    assert missing in cal["note"]
    for other in COMPLETE:
        if other != missing:
            assert other not in cal["note"], f"{other} 是测出来的，别把它说成缺的"


def test_a_missing_fixed_overhead_is_not_zero_overhead(tmp_path):
    """`m7` 以前把缺省的 overhead 当 0 用，于是预测值系统性偏小、measured/predicted
    系统性偏大——一个假的 "suspect"。三个数在 calibrate 的 §0 里被一并声明为
    "别的代码允许引用的三个数"，那么要用就一起用。"""
    consts = dict(COMPLETE, per_call_fixed_overhead_s=None)
    cal = metrics.load_calibration(_sidecar(tmp_path, constants=consts))
    assert cal["usable"] is False
    assert "per_call_fixed_overhead_s" in cal["note"]


def test_constants_fit_for_another_model_are_refused(tmp_path):
    cal = metrics.load_calibration(_sidecar(tmp_path, model="qwen-something"),
                                   expect_model="gemma-fit")
    assert cal["usable"] is False
    assert "qwen-something" in cal["note"] and "gemma-fit" in cal["note"], \
        "两个名字都要在，否则读的人不知道该信哪一边"


def test_a_match_on_model_does_not_by_itself_make_the_constants_usable(tmp_path):
    """反面对照：只给 `expect_model` 而文件里恰好同名的正常 sidecar 必须仍然可用，
    否则上面那条拒绝是"任何带 expect_model 的调用都拒绝"的假阳性。"""
    cal = metrics.load_calibration(_sidecar(tmp_path), expect_model="gemma-fit")
    assert cal["usable"] is True


def test_a_sidecar_without_a_model_stamp_is_usable_but_cannot_be_attributed(tmp_path):
    """老文件没记 model。这种情况下 refuse 会让 audit 在真实数据上永远读不到常数，
    而"无法确认"和"确认不符"是两句不同的话，得说成前一句。"""
    cal = metrics.load_calibration(_sidecar(tmp_path, model=None),
                                   expect_model="gemma-fit")
    assert cal["usable"] is True
    assert "model" in cal["note"] and "无法确认" in cal["note"]


# --------------------------------------------------- 端点自己承认过这个 model 吗
def _listing(*ids: str) -> dict:
    """`features` 里那个原始探针的形状——端点自己列出的名字只住在这一格。"""
    return {"models_endpoint": {"body": {"data": [{"id": i} for i in ids]}}}


def test_constants_from_a_model_the_endpoint_denies_are_refused(tmp_path):
    """这是 R7（服务被人重启换了权重）在机器可读这一侧唯一能直接依据的证据：请求体里的 model
    永远是配置给的那个，端点却可能把它打到别的权重上。清单里没有这个名字，这份常数就不能被
    当成"我们这个模型的成本"。"""
    cal = metrics.load_calibration(_sidecar(tmp_path, features=_listing("some-other-weight")))
    assert cal["usable"] is False
    assert "不承认" in cal["note"] and "some-other-weight" in cal["note"], cal["note"]
    assert "gemma-fit" in cal["note"], "被否认的那个名字也得在，否则读的人不知道去哪找它"


def test_a_listing_that_names_the_model_adds_no_doubt(tmp_path):
    """反面对照：清单里有这个名字时必须一句都不多说。否则每条真体检都自带一句噪音，
    而噪音会把上面那条拒绝稀释成"反正它总在报警"。"""
    cal = metrics.load_calibration(_sidecar(tmp_path, features=_listing("other", "gemma-fit")))
    assert cal["usable"] is True
    assert "不承认" not in cal["note"], cal["note"]


def test_an_absent_or_empty_listing_is_no_evidence_not_a_contradiction(tmp_path):
    """没有清单（老文件、或者那次探针自己失败了）不等于端点否认。把它当否认，audit 就会在
    所有真实数据上永远读不到常数——那正是"没有数字"和"有数字但不该用"两种病混成了一句。
    与 `model` 缺失那条不同：清单缺失只影响"端点是否承认"这一条，`model` 缺失连归属都做不了。"""
    for payload in ({}, {"features": {}}, {"features": _listing()},
                    {"features": {"models_endpoint": {"status": 500}}},
                    {"model": None, "features": _listing("some-other-weight")}):
        cal = metrics.load_calibration(_sidecar(tmp_path, **payload))
        assert cal["usable"] is True, payload
        assert "不承认" not in cal["note"], payload


def test_a_flat_copy_of_the_listing_does_not_outvote_the_raw_block(tmp_path):
    """同一个文件里如果有两条路径能说出清单，读侧就必须在它们冲突时站在原始探针那一侧：
    `docs/metrics.md` 里点名的失败正是这一对——报告的 §0 从 `features` 现推，它说"不承认"，
    audit 却从另一格读出"承认"，于是放行了一份没人承认的常数。"""
    cal = metrics.load_calibration(_sidecar(
        tmp_path, features=_listing("some-other-weight"), model_declared=["gemma-fit"]))
    assert cal["usable"] is False, cal["note"]
    assert "不承认" in cal["note"] and "some-other-weight" in cal["note"], cal["note"]


def test_the_writer_stores_the_listing_only_in_the_raw_block(cal):
    """上面那对读者要成立，写侧就得只留一个落点：派生出来的扁平清单不再进 sidecar。
    它与 `features` 由同一个 `declared_models()` 从同一格算出，而 redact 按 CRED_KEYS 的键名
    清洗，`models_endpoint` / `body` / `data` / `id` 一格都不在名单上，所以抄本不额外保住任何证据。"""
    lat = [{"k": 1, "prefix_reps": 130, "wall_s": 3.2, "agg_prefill_tps": 3600.0},
           {"k": 2, "prefix_reps": 130, "wall_s": 5.0}]
    tp = {"decode_tps_overhead_corrected": 41.2, "per_call_fixed_overhead_s": 0.83}
    payload = cal.sidecar("2026-09-21T00:00:00Z", False, _listing("gemma-fit", "other"),
                          {}, {}, tp, lat, [], model="gemma-fit",
                          base_url="http://10.0.0.1:13000/v1")
    assert "model_declared" not in payload, sorted(payload)
    assert metrics.declared_models(payload["features"]) == ["gemma-fit", "other"]


# ------------------------------------------------------------------ note 的单一来源
def test_a_zero_constant_is_refused_because_it_sits_in_a_denominator(tmp_path):
    """0 是"没测得"的另一种写法：`D_decode_tok_s = 0` 在拟合式里站在分母上，而它看起来
    比 `null` 可信得多——一个有值的格子。"""
    consts = dict(COMPLETE, D_decode_tok_s=0)
    cal = metrics.load_calibration(_sidecar(tmp_path, constants=consts))
    assert cal["usable"] is False and cal["constants"] is None
    assert "D_decode_tok_s" in cal["note"]


def test_m7_prints_the_loader_s_reason_instead_of_inventing_its_own():
    """两个地方各写一份"为什么没有 drift"，迟早一处说缺 D、另一处说文件不存在。"""
    out = metrics.m7_cost_profile([], constants=None,
                                  calibration_note="data/calibration.json 不存在")
    assert out["drift"] is None
    assert out["drift_note"] == "data/calibration.json 不存在"


def test_m7_still_refuses_to_treat_a_null_overhead_as_zero():
    """Loader 之外再钉一次：直接手写 `constants=` 的调用者（batch、以后的脚本）也不能
    绕过这条要求。"""
    out = metrics.m7_cost_profile([], constants={"D_decode_tok_s": 40.0,
                                                 "P_prefill_tok_s_best_observed": 3000.0})
    assert out["drift"] is None
    assert "per_call_fixed_overhead_s" in out["drift_note"]


def test_m7_refuses_a_zero_overhead_rather_than_a_free_first_token():
    """同一件事从 m7 这头再钉一次：调用方手写 `constants=` 时也绕不过去。
    `fixed + pt/P + ct/D` 里少了一项固定开销，等于宣布第一次调用不要时间。"""
    out = metrics.m7_cost_profile([], constants=dict(COMPLETE, per_call_fixed_overhead_s=0))
    assert out["drift"] is None
    assert "per_call_fixed_overhead_s" in out["drift_note"]


def test_m7_does_not_call_an_empty_batch_ok():
    """常数齐了、一局里却没有任何带 latency 的调用时，比值集合是空的——`any()` 在空集合上
    为假，于是 verdict 会自己写成 "ok"。这和 M3 里"没读数不算通过"是同一个错，只是这次
    伪装成 drift 自检通过。"""
    out = metrics.m7_cost_profile([], constants=dict(COMPLETE))
    assert out["drift"]["verdict"] == "not_evaluable"
    assert out["drift"]["worst"] is None


# ------------------------------------------------------------------ 写侧：sidecar 得真的有这一块
def _load_calibrate():
    spec = importlib.util.spec_from_file_location("calibrate", ROOT / "scripts" / "calibrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cal():
    return _load_calibrate()


def test_the_sidecar_a_run_writes_contains_the_block_the_loader_reads(cal, tmp_path):
    """读侧和写侧对同一个键名各写一遍字符串，是这条链断掉三年的方式：`main()` 落盘的字典里
    从来没有 `constants`，而 loader 只会读 `constants`。所以这里两头都碰：先让 calibrate 的
    写路径产出一份 sidecar，再用 loader 去读它。"""
    lat = [{"k": 1, "prefix_reps": 130, "wall_s": 3.2, "agg_prefill_tps": 3600.0},
           {"k": 2, "prefix_reps": 130, "wall_s": 5.0}]
    tp = {"decode_tps_overhead_corrected": 41.2, "per_call_fixed_overhead_s": 0.83}
    payload = cal.sidecar("2026-09-21T00:00:00Z", False, {}, {}, {}, tp, lat, [],
                          model="gemma-fit", base_url="http://10.0.0.1:13000/v1")
    path = _write(tmp_path, json.loads(json.dumps(payload, ensure_ascii=False)))
    assert "constants" in payload and payload["constants"]["D_decode_tok_s"] == 41.2
    # The stamp is the whole reason `load_calibration` can refuse a cross-model file; a
    # sidecar without it degrades every later audit into "出处存疑" instead of a rejection.
    assert payload["model"] == "gemma-fit"
    assert payload["base_url"] == "http://10.0.0.1:13000/v1", \
        "页眉那行 base_url 说的是那次测量，不在 sidecar 里就等于重渲染时现编"
    assert payload["ran_utc"] == "2026-09-21T00:00:00Z"
    cal_read = metrics.load_calibration(path)
    assert cal_read["usable"] is True
    assert cal_read["constants"]["P_prefill_tok_s_best_observed"] == 3600.0
    # `derive_constants` 交出的块比能引用的多（并发增益、 batching 结论）。原样透传会让
    # `drift.constants` 把整段体检结论印进 audit 里，读的人分不清哪一个数真的进了预测式。
    assert set(cal_read["constants"]) == set(metrics.CALIBRATION_KEYS)
    # 写侧：端点自己列出的名字只能由 `features` 现推，而且在这份文件里只落一处——它是这份文件里
    # 唯一一条可能自我否证的证据，多存一份扁平抄本就是给"§0 报了、audit 放行"那种分叉留门。
    feats = {"models_endpoint": {"body": {"data": [{"id": "gemma-fit"}, {"id": "other"}]}}}
    stamped = cal.sidecar("2026-09-21T00:00:00Z", False, feats, {}, {}, tp, lat, [],
                          model="gemma-fit", base_url="http://10.0.0.1:13000/v1")
    assert metrics.declared_models(stamped["features"]) == ["gemma-fit", "other"]
    assert metrics.load_calibration(_write(tmp_path, stamped))["usable"] is True
    silent = cal.sidecar("2026-09-21T00:00:00Z", False, {}, {}, {}, tp, lat, [],
                         model="gemma-fit", base_url="http://10.0.0.1:13000/v1")
    note = metrics.load_calibration(_write(tmp_path, silent))["note"]
    assert "不承认" not in note, "探针没答上来时读侧要沉默，不能把『没证据』说成『否认』"


def test_redact_leaves_the_constants_block_alone(cal):
    """The credential guard is a key-name substring matcher, and a constant that says
    `..._tok_s_...` is exactly the shape of thing a widened list would eat next time. The
    constants are now written by the same `redact()` that ate `has_cached_tokens` once, so the
    guard and the authority have to be tested against each other, not each on its own."""
    lat = [{"k": 1, "prefix_reps": 130, "wall_s": 3.2, "agg_prefill_tps": 3600.0}]
    tp = {"decode_tps_overhead_corrected": 41.2, "per_call_fixed_overhead_s": 0.83}
    payload = cal.redact(cal.sidecar("2026-09-21T00:00:00Z", False, {}, {}, {}, tp, lat, [],
                                     model="gemma-fit", base_url="http://10.0.0.1:13000/v1"))
    block = payload["constants"]
    assert "<elided>" not in json.dumps(block, ensure_ascii=False)
    assert block["D_decode_tok_s"] == 41.2 and block["per_call_fixed_overhead_s"] == 0.83
    # base_url 是配置不是凭据（局域网地址可入库），它进 sidecar 后必须活着出来——
    # 被 redact 吃掉就等于把上一条测试的出处线索又变回"重渲染时现编"。
    assert payload["base_url"] == "http://10.0.0.1:13000/v1"


def test_the_quotable_keys_have_one_source(cal):
    """`derive_constants` 的返回值、§0 的 `missing` 名单、以及 loader 的可用性判据必须是
    同一份名单、同一把尺。写死两遍的话，加第四个常数或收紧判据时只会改到其中一处。"""
    assert set(cal.QUOTABLE_KEYS) == set(metrics.CALIBRATION_KEYS) == set(COMPLETE)
    assert cal.fitted_constant is metrics.fitted_constant, \
        "§0 的『没测得』和 loader 的『不可用』用了两把尺：负数开销能过前者、过不去后者"
    assert cal.model_denial is metrics.model_denial, \
        "报告里那条『端点不承认这个 model』和 loader 的拒绝必须是同一个函数对象"
    assert cal.declared_models is metrics.declared_models, \
        "`/v1/models` 的清单只许有一处解析：写侧、报告侧、读侧都从同一个函数拿"
