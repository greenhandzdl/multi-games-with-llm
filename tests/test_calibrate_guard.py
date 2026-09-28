"""calibrate.py 的自毁守卫：redact 抹掉了什么，必须留在报告上。

背景是真实事故而不是假想：第一版 `CRED_KEYS` 里有裸子串 `"token"`，于是
`usage_keys_seen`、`has_cached_tokens` 这些**结论本身**被写成 `<elided>`，报告把
"端点没有这个字段"和"我们的防护逻辑把它遮掉了"渲染成同一行字。

键名收窄修好了那一次。这个守卫管的是下一次——往 `CRED_KEYS` 里再加一个会撞上结论的
键，或端点哪天返回一个真含 `api_key` 字段的 usage——都该在 §0 里被点名，而不是静默地
变成一个看起来可信的空格。

后半截管的是另一件自毁：`calibrate.py` 自己抄了一份 `cached_tokens` 的读法，而这份读法
在 `transport.usage_from()` 里已经有一个带断言的权威版本。抄的那一份只处理了一种形状，
于是"端点回了一个我们没想到的 usage"会被 `complete()` 那个兜底的 `except Exception`
记成一次失败的调用——体检报告里那句"端点不健康"和"端点报了我们没解析的形状"就此同形。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("calibrate", ROOT / "scripts" / "calibrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # top level is pure: Config() + metric helpers, no I/O
    return mod


@pytest.fixture(scope="module")
def cal():
    return _load()


def _md(cal, features):
    return cal.render_md(features, {}, {}, {}, [], [], argparse.Namespace(quick=True), twin=cal.TWIN_PROMISE)


def test_the_mark_is_findable_and_the_findings_are_not_blind(cal):
    """Two directions in one row: the guard must see what redact destroyed, and must not
    have grown back the substring collision that destroyed the findings."""
    payload = {"has_cached_tokens": True,
               "usage": {"api_key": "secret-value", "prompt_tokens": 12},
               "rows": [{"x-api-key": "abc", "n": 3}]}
    out = cal.elided_paths(cal.redact(payload))
    assert out == ["usage.api_key", "rows[0].x-api-key"], out
    assert "has_cached_tokens" not in out and "usage.prompt_tokens" not in out, \
        "a bare 'token' in CRED_KEYS is the bug this file exists to catch"


def test_the_report_names_every_blanked_field(cal):
    md = _md(cal, {"apc_visibility": {"has_cached_tokens": True,
                                       "authorization": "Bearer something"}})
    assert "redact" in md and "features.apc_visibility.authorization" in md, md
    assert "不是" in md and "端点没返回" in md, \
        "the line must say the blank is our doing, not the endpoint's absence"


def test_a_clean_report_stays_quiet(cal):
    """The reverse direction, same seriousness: a guard that cries on every run trains
    nobody to read it, and the completeness section is quoted by other docs."""
    md = _md(cal, {"apc_visibility": {"has_cached_tokens": True, "cached_value": 5120}})
    assert "redact" not in md, md


def test_the_committed_report_is_still_what_the_code_renders(cal, tmp_path):
    """`docs/calibration.md` 是这份代码的输出，不是一篇手写的散文——那就必须有人盯着它还是。

    这条测试是被一次真实事故逼出来的：变异自检的 `--verify` 拿 18:03 的旧备份覆盖了
    `render_md` 里刚加的"孪生件"那一行，当时那 488 个测试全绿，只有落盘的报告和代码开始悄悄分叉。
    报告是"全工程唯一合法的延迟常数来源"，它与代码分叉的那一天就是这条链断掉的那一天。

    唯一跳过的是 `- 数据来源：` 那一行：它写的是 `data/calibration.json` 的**文件 mtime**，
    而 mtime 会被一次 `git checkout` 改动，那不是任何人写坏了报告。
    """
    cal.render_from_json(str(ROOT / "data/calibration.json"), str(tmp_path / "cal.md"))

    def keep(path: Path) -> list[str]:
        return [l for l in path.read_text(encoding="utf-8").splitlines()
                if not l.startswith("- 数据来源：")]

    assert keep(tmp_path / "cal.md") == keep(ROOT / "docs/calibration.md"), \
        "报告与 render_md 分叉了：要么重跑 --from-json，要么承认这次改动不该进报告"


def _call(cal, monkeypatch, usage):
    """把一次"200，usage 长这样"喂给 `Client.complete`，绕开网络。

    `complete()` 只碰注入进来的 client 的 `post`，所以这里要的是一个**形状**而不是一个服务器
    （真套件的桩在 `tests/test_calibrate_rehearsal.py`，那台管的是"六段探针跑得完吗"）。
    """
    monkeypatch.setenv(cal.KEY_ENV, "guard-placeholder-4f7a")

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"index": 0, "message": {"content": "OK"},
                                 "finish_reason": "stop"}],
                    "usage": usage}

    class Post:
        async def post(self, *a, **k):
            return Resp()

    return asyncio.run(cal.Client().complete(Post(), [{"role": "user", "content": "x"}],
                                            tries=1))


def test_an_unparsed_usage_shape_stays_a_shape_not_an_outage(cal, monkeypatch):
    """"这一格有没有值"只许有一个读法，读法错了要红在形状上，不能红成"端点不健康"。

    `prompt_tokens_details` 是个字符串、是个列表，或整块 `usage` 为 null 时，脚本自己那份
    `.get` 链抛 AttributeError，被 `complete()` 的兜底 except 接住，于是这次调用记成
    `ok: False`——一次明明返回了 200 和一份能读的 `prompt_tokens` 的调用被写成了端点故障，
    而 §5 的整条前缀缓存账正是从这句话里读出来的。反向同样要钉：形状对的时候那一格必须照常
    落到 `cached` 上，否则这条守卫只会把探针弄哑。
    """
    good = _call(cal, monkeypatch, {"prompt_tokens": 900, "completion_tokens": 8,
                                    "prompt_tokens_details": {"cached_tokens": 5120}})
    assert good["ok"] is True and good["cached"] == 5120, good
    assert good["pt"] == 900, good

    shapes = {
        "一个字符串": {"prompt_tokens": 900, "prompt_tokens_details": "unavailable"},
        "一个列表": {"prompt_tokens": 900, "prompt_tokens_details": ["cached_tokens", 5120]},
        "分支为 null": {"prompt_tokens": 900, "prompt_tokens_details": None},
        "整块 usage 为 null": None,
    }
    for label, usage in shapes.items():
        got = _call(cal, monkeypatch, usage)
        assert got["ok"] is True, f"一次返回 200 的调用被记成端点故障（{label}）：{got}"
        assert got["cached"] is None, f"{label} 要留在'端点没说'那一侧，不是 0：{got}"


def test_the_script_asks_transport_for_the_usage_block_and_keeps_no_copy(cal, monkeypatch):
    """上一条管行为，这一条管"只有一个读取点"这件事本身——手法与 `test_purity.py` 那两条一致：
    不看值，看字面量落在哪几个文件里。

    只测行为挡不住下一次分家：谁把嵌套读法**正确地**内联回 `calibrate.py`（带上 isinstance），
    上一条照样绿，而 `transport.usage_from()` 与它就此各自演化——正是 `#70` 之后 README
    〈只有一处能说清〉那一节要防的那件事。docstring 与 `#` 注释剥掉，所以那两段讲这件事的
    文字不算抄。
    """
    text = (ROOT / "scripts" / "calibrate.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""|\'\'\'.*?\'\'\'', "", text, flags=re.DOTALL)
    code = "\n".join(l.split("#")[0] for l in code.splitlines())
    assert "prompt_tokens_details" not in code, \
        "脚本里还留着一份嵌套读法：它和 transport.usage_from() 会各自演化"
    assert "usage_from" in code, "改走唯一读法了吗？这一格现在没人读"
    # 而且真的读得出来：走唯一读法之后，形状对的那一格仍然要落到 cached 上。
    got = _call(cal, monkeypatch, {"prompt_tokens": 900, "completion_tokens": 8,
                                   "prompt_tokens_details": {"cached_tokens": 777}})
    assert got["cached"] == 777, got


def _twin_of(cal, src: Path, out: Path) -> str:
    """离线重渲染一遍，取回页眉里那条讲孪生件的 bullet（判据只看那一行，别的改动不该顶绿它）。"""
    cal.render_from_json(str(src), str(out))
    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("- 机器可读的孪生件")]
    assert len(lines) == 1, f"页眉那条孪生件的话应当只有一句，读到的是：{lines}"
    return lines[0]


def test_the_twin_line_refuses_when_the_sidecar_has_no_block(cal, tmp_path):
    """`docs/calibration.md` 第 5 行那句"取值以孪生件为准"今天是一句假话。

    在册的 sidecar 里根本没有 `constants` 块（顶层只有 features/stream/ratio/latency/temps），
    `metrics.load_calibration()` 对它给的是 `usable=False` 并写着"重跑 scripts/calibrate.py"——
    那条拒绝的话甚至就住在 loader 自己的分支里，注释还点名"仓库里现存的那份 sidecar 就是这一类"。
    而页面 promise 的那个块不存在，读的人照 README 里那条注释把文件递进去，拿到的是拒绝。
    """
    line = _twin_of(cal, ROOT / "data" / "calibration.json", tmp_path / "c.md")
    assert "取值以孪生件为准" not in line, line
    assert "没有 constants 块" in line, f"应当原样带上 loader 的拒绝，读到的是：{line}"


def test_the_twin_line_keeps_its_promise_for_a_sidecar_that_carries_the_block(cal, tmp_path):
    """反向那一格：修成"按 sidecar 里真有的东西说话"之后，带可用常数块的那一份仍要说"为准"。

    少了这一格，上一条可以靠"永远说读不出常数"来绿——那只是把假话换成了废话。这份合成 sidecar 是
    自洽的：`throughput` 与 `latency` 供 `derive_constants()` 拟合，`constants` 是它拟合出来的那三个数，
    所以页面上不许同时出现"为准"和"本节尚不构成常数来源"。
    """
    import json
    payload = {"throughput": {"decode_tps_overhead_corrected": 40.0,
                              "per_call_fixed_overhead_s": 1.2},
               "latency": [{"prefix_reps": 130, "k": 1, "wall_s": 2.0, "agg_prefill_tps": 2000.0}],
               "temps": [], "features": {}, "stream": {}, "ratio": {},
               "constants": {"D_decode_tok_s": 40.0, "per_call_fixed_overhead_s": 1.2,
                             "P_prefill_tok_s_best_observed": 2000.0}}
    src = tmp_path / "good.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    line = _twin_of(cal, src, tmp_path / "g.md")
    assert "取值以孪生件为准" in line, line
    page = (tmp_path / "g.md").read_text(encoding="utf-8")
    assert "尚不构成常数来源" not in page, "同一页不许一边说为准、一边说自己不是来源"
