"""M0 体检工具的排练：一台 127.0.0.1 上的桩端点，让 `main()` 从头到尾真跑一遍。

`scripts/calibrate.py` 是全工程唯一有权产出延迟常数的程序。它此前离线覆盖到的只有**渲染**那一半：
`test_calibrate_guard.py` 管 redact 与"报告还是代码的输出"，`test_calibration_loader.py` 管常数块
的读侧和写侧键名。`main()` 自己的三个决策点一次都没被执行过——

1. 没有导出 key 就不该开始（`Client.__init__`）；
2. 端点不通就不该留下任何文件（预检：把"服务不可用"记成"模型被动"是这台机器上发生过一次的事）；
3. 跑完之后，报告和 sidecar 必须互相承认同一件事（同一份 `derive_constants()`）。

而这三条都只有"真端点在场"时才有第二次机会，端口开着的时间窗口又很短。第一通真电话不该拿去调试
工具本身。

桩是**规格形状**的服务器，不是**速度形状**的：它按 OpenAI 兼容的字段回答，所以"六段探针能不能跑
完"在这里有答案；它不假装自己有延迟，所以**任何一个常数都不许从这次排练里读走**。零延迟反而露出
一个真实的角——`per_call_fixed_overhead_s = short.dt − short.ct/69` 在这里是**负数**，于是"报告
自称构成常数来源、`load_calibration` 却说这份文件不可用"的分歧当场可见。

同一台桩有两个朝向：正面桩兑现 `stop`/`logprobs`/确定性，反面桩三样都收单不兑现。报告里每个判定
都被两头钉过一次，写死任何一个结论都会红。

这些用例只碰 loopback，不碰 `network` 标记的那台共享机器，也不需要真 key。
"""

from __future__ import annotations

import asyncio
import http.server
import importlib.util
import json
import socket
import sys
import threading
import zlib
from pathlib import Path

import pytest

from wolfengine import metrics

ROOT = Path(__file__).resolve().parents[1]
KEY_ENV = "WOLF_LLM_API_KEY"
# 一个刻意不像密钥的占位值：它存在的意义只是"环境变量里有东西"，并且必须不出现在任何产物里。
STUB_KEY = "rehearsal-placeholder-9d4b"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("calibrate_rehearsal",
                                                  ROOT / "scripts" / "calibrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # top level is pure: Config() + metric helpers, no I/O
    return mod


def _blob(body: dict) -> str:
    return "\n".join(str(m.get("content", "")) for m in (body.get("messages") or []))


class _Handler(http.server.BaseHTTPRequestHandler):
    """OpenAI 兼容的*形状*：逐条对应 `Client.complete` 读取的字段，不多不少。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:  # noqa: A003
        pass                            # 静默：测试输出里不要 78 行访问日志

    def _send(self, code: int, payload, *, sse: bool = False) -> None:
        if sse:
            raw, ctype = payload.encode("utf-8"), "text/event-stream"
        else:
            raw, ctype = json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        self.server.state["calls"].append(("GET " + self.path, None))
        if self.path.endswith("/models"):
            # 真端点就是这个形状：没有 revision/version 字段，所以 R7（被人换了权重）只能间接发现。
            self._send(200, {"object": "list", "success": True,
                             "data": [{"id": self.server.model, "object": "model",
                                       "created": 1626777600, "owned_by": "openai"}]})
        else:
            self._send(404, {"error": {"message": "no such route"}})

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        self.server.state["calls"].append(("POST " + self.path, body))
        if self.path.endswith("/chat/completions"):
            self._chat(body)
        else:
            self._send(404, {"error": {"message": "no such route"}})   # /tokenize 探测落在这

    def _chat(self, body: dict) -> None:
        if body.get("n", 1) > 1 or "response_format" in body:
            # 真端点对这两个参数就是 400。400 只该发一次——那条短路交给用例去数请求。
            self._send(400, {"error": {"message": "this parameter is not supported"}})
            return
        honors = self.server.honors
        blob = _blob(body)
        h = zlib.crc32(blob.encode("utf-8"))
        if "deterministic" not in honors:
            h += self.server.state["tick"]      # 同一串 prompt 也给不出同一个回答
        self.server.state["tick"] += 1
        if "1 2 3 4 5" in blob:
            text = "1 2 3 4 5 6 7 8 9 10 11 12"  # stop 探针要的两边可判的样本
        else:
            # 同一串 prompt → 同一个回答（确定性探针）；不同座位 → 不同措辞（多样性扫描的输入）。
            text = f"我看{(h % 9) + 1}号的话前后对不上，先听{((h >> 8) % 9) + 1}号说完。"
        finish = "stop"
        if "stop" in honors:
            for s in body.get("stop") or []:
                if s in text:
                    text, finish = text.split(s, 1)[0], "stop"
                    break
        pt = max(1, len(blob) // 4)
        choice = {"message": {"content": text}, "finish_reason": finish, "index": 0}
        if body.get("logprobs") and "logprobs" in honors:
            # 收下请求**并且**给出可读的数。"200 但把 logprobs 丢了"是另一种答案，由反面桩给。
            choice["logprobs"] = {"content": [{"token": text[:4], "logprob": -0.12,
                                               "top_logprobs": []}]}
        seen = self.server.state["prefixes"]
        head = blob[:80]
        cached = max(0, pt - 4) if head in seen else 0
        seen[head] = True
        if body.get("stream"):
            pieces = [text[i:i + 4] for i in range(0, max(1, len(text)), 4)]
            sse = "".join("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": p}}]},
                                                ensure_ascii=False) + "\n\n" for p in pieces)
            self._send(200, sse + "data: [DONE]\n\n", sse=True)
            return
        self._send(200, {"id": "stub", "object": "chat.completion", "model": self.server.model,
                         "choices": [choice],
                         "usage": {"prompt_tokens": pt, "completion_tokens": max(1, len(text)),
                                   "total_tokens": pt + len(text),
                                   "prompt_tokens_details": {"cached_tokens": cached}}})


def _serve(honors: frozenset = frozenset({"stop", "logprobs", "deterministic"})) -> object:
    class _Srv(http.server.ThreadingHTTPServer):
        daemon_threads = True

    srv = _Srv(("127.0.0.1", 0), _Handler)
    srv.model = "gemma-stub"
    srv.honors = honors
    srv.state = {"calls": [], "prefixes": {}, "tick": 0}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                 # 关掉：这个端口上现在没人听，connect 立刻被拒
    return port


def _main(cal, out: Path, monkeypatch, *, base_url: str | None = None, with_key=True) -> None:
    """Call `main()` exactly the way the shell does: argv + env. No hook into the script."""
    if with_key:
        monkeypatch.setenv(KEY_ENV, STUB_KEY)
    else:
        monkeypatch.delenv(KEY_ENV, raising=False)
    if base_url:
        monkeypatch.setattr(cal.CONF, "base_url", base_url)
    monkeypatch.setattr(sys, "argv", ["calibrate.py", "--quick",
                                      "--out", str(out / "cal.md"), "--json", str(out / "cal.json")])
    asyncio.run(cal.main())


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One full rehearsal run of `main()`; the assertions below only read what it left behind."""
    cal = _load()
    srv = _serve()
    out = tmp_path_factory.mktemp("rehearsal")
    mp = pytest.MonkeyPatch()          # module-scoped fixture may not take the function-scoped one
    try:
        _main(cal, out, mp, base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1")
    finally:
        mp.undo()
        srv.shutdown()
        srv.server_close()

    class _R:
        md = (out / "cal.md").read_text(encoding="utf-8")
        json_path = out / "cal.json"
        sidecar = json.loads((out / "cal.json").read_text(encoding="utf-8"))
        calls = srv.state["calls"]
        base_url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    return _R


# ---------------------------------------------------------------------- 跑不完就该闭嘴
def test_no_key_exported_means_the_script_never_starts(monkeypatch, tmp_path):
    """约定的执行点就在这里：key 的值只在 shell 里 export，配置里只有变量名。没有它，程序
    不该发出第一个字节，也不该留下半份产物。"""
    cal = _load()
    with pytest.raises(SystemExit) as e:
        _main(cal, tmp_path, monkeypatch, base_url=f"http://127.0.0.1:{_closed_port()}/v1",
              with_key=False)
    assert KEY_ENV in str(e.value)
    assert list(tmp_path.iterdir()) == []


def test_a_dead_endpoint_writes_no_report(monkeypatch, tmp_path):
    """预检失败必须**在矩阵之前**退出。上一版拿 12k token 的前缀开路，后面每一格都失败，
    读起来像"这个模型很被动"，其实是共享机器被人占满了。"""
    cal = _load()
    with pytest.raises(SystemExit) as e:
        _main(cal, tmp_path, monkeypatch,
              base_url=f"http://127.0.0.1:{_closed_port()}/v1")
    assert "端点预检失败" in str(e.value)
    assert list(tmp_path.iterdir()) == [], \
        "端点不可达却留下了报告，等于把『没拿到答案』写成了一个答案"


def test_a_400_on_the_wrong_request_is_not_resent(run):
    """400 = 请求本身不对，重发是循环；只有 5xx/网关 HTML 值得退避。这条短路此前没人执行过。"""
    with_n = [c for c in run.calls if c[1] and c[1].get("n", 1) > 1]
    with_fmt = [c for c in run.calls if c[1] and "response_format" in c[1]]
    assert len(with_n) == 1 and len(with_fmt) == 1, \
        f"n=2 发了 {len(with_n)} 次、response_format 发了 {len(with_fmt)} 次：400 被当成可重试的错"
    assert run.sidecar["features"]["n_gt_1"].startswith("rejected")


# ---------------------------------------------------------------------- 跑完了：两份产物互相承认
def test_every_phase_ran_and_no_probe_died_on_the_way(run):
    """六段全过、一次 `PROBE FAILED` 都不许有——这就是"端口开了第一通电话会不会中途塌"的答案。"""
    assert "PROBE FAILED" not in run.md, run.md[max(0, run.md.find("PROBE FAILED") - 200):]
    assert len(run.calls) >= 70, \
        f"只发了 {len(run.calls)} 个请求：这不是整跑，是某一段被静默跳过了"
    s = run.sidecar
    assert set(s) >= {"features", "stream", "ratio", "throughput", "latency", "temps", "constants"}
    assert len(s["latency"]) == 6 and len(s["temps"]) == 1, \
        "--quick 的矩阵是 2 个前缀 × k∈{1,4,8}、1 个温度档；格数不对就是没跑到那一段"
    assert s["temps"][0]["n_speech"] == 27


def test_the_report_names_the_endpoint_it_measured(run):
    """全工程唯一合法的常数来源必须自己说清是谁的数：桩跑出来的报告带着 127.0.0.1，就不会
    被误当实测值引用；真端点跑的同理。占位 key 则一个字都不许落盘。"""
    line = [l for l in run.md.splitlines() if l.startswith("- base_url")]
    assert len(line) == 1 and "127.0.0.1" in line[0], line
    assert STUB_KEY not in run.md and STUB_KEY not in json.dumps(run.sidecar, ensure_ascii=False)


def test_the_verdicts_are_read_off_the_wire_not_written_into_the_prose(run):
    """报告里每个判定都必须是"服务器怎么答 → 工具怎么判"的函数。桩答得规格正确，判定就得是
    肯定侧；哪天有人把判定写死，下一条的反面桩就先红。"""
    f = run.sidecar["features"]
    assert f["stop_honored"]["verdict"] == "honored"
    assert f["logprobs"]["verdict"] == "returned"
    assert f["greedy_seed42_distinct_of_3"] == 1 and f["determinism_verdict"] == "deterministic"
    assert f["apc_visibility"]["has_cached_tokens"] is True
    assert run.sidecar["stream"]["chunks"] >= 2, "流式那段没读到分块，判定就只是默认值"


async def test_a_server_that_disagrees_is_recorded_as_disagreeing(monkeypatch):
    """反面桩：收了 `stop` 不截断、收了 `logprobs` 就丢、同 prompt 给不同回答——三种"文档里支持、
    现场不兑现"各占一个。只测肯定侧的话，把判定写死成 `"honored"` 也能全绿，而那份报告一旦开始
    报喜不报忧，就再没有人能发现它。"""
    import httpx

    cal = _load()
    srv = _serve(honors=frozenset())
    monkeypatch.setenv(KEY_ENV, STUB_KEY)
    monkeypatch.setattr(cal.CONF, "base_url", f"http://127.0.0.1:{srv.server_address[1]}/v1")
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            f = await cal.probe_features(c, cal.Client())
    finally:
        srv.shutdown()
        srv.server_close()
    assert f["stop_honored"]["verdict"] == "NOT honored", f["stop_honored"]
    assert f["logprobs"]["verdict"].startswith("NOT returned")
    assert f["greedy_seed42_distinct_of_3"] == 3
    assert f["determinism_verdict"].startswith("NON-deterministic")
    # 反面桩没有延迟，`apc` 两段仍然要跑完：证明失败的是端点行为，不是探针自己塌了。
    assert f["apc_visibility"]["probe_ok"] is True


def test_the_sidecar_and_the_report_come_from_one_run_and_one_fit(run, tmp_path):
    """`--from-json` 的许诺是"不重跑也能修页眉"，那它重渲染出来的就必须是当时那份报告——那句
    出处本身除外。仓库里现存的 sidecar 是旧 schema，所以这条只能在一次新跑上验。"""
    cal = _load()
    # 这里**不**再把 CONF.base_url 指回那次跑用的地址：`--from-json` 若还需要活配置才能对上页眉，
    # 就说明它仍在替配置说话而不是替那次测量说话，那条断言会因此红在这里，而不是被顺手糊过去。
    out = tmp_path / "again.md"
    cal.render_from_json(str(run.json_path), str(out))

    def keep(text: str) -> list[str]:
        return [l for l in text.splitlines()
                if not l.startswith(("- 数据来源：", "- 生成命令："))]

    assert keep(out.read_text(encoding="utf-8")) == keep(run.md), \
        "重渲染与当次渲染分叉：两条路读的不是同一份 sections"


def test_a_re_render_carries_the_measured_endpoint_not_the_current_config(run, tmp_path):
    """页眉那行 base_url 和 `model` 是同一种陈述：关于**那次测量**的。`model` 已经进了 sidecar，
    `base_url` 没有，所以哪天端点换了地址，`--from-json` 会拿配置里现在的地址给旧数盖章——报告
    唯一的出处线索就变成假的。"""
    cal = _load()
    # `_load()` 重新执行了一遍模块，`CONF = Config()` 于是也是新的一份——这里改它不会串到别的
    # 用例，但也正因为如此，它改的必须是这个模块实例才有效。
    cal.CONF.base_url = "http://10.0.0.9:9999/v1"
    out = tmp_path / "moved.md"
    cal.render_from_json(str(run.json_path), str(out))
    md = out.read_text(encoding="utf-8")
    assert run.base_url in md, "重渲染把测量当时打过的地址换成了配置里现在的地址"
    assert "10.0.0.9" not in md


def test_the_report_only_claims_to_be_a_source_when_the_loader_agrees(run):
    """页眉那句"本节尚不构成常数来源"和 `load_calibration()` 的 `usable` 是同一句话的两种写法，
    今天却用了两把尺：报告只看 `is None`，loader 还要求 `> 0`。桩没有延迟，
    `per_call_fixed_overhead_s` 因此被拟合成了负数——同一份数据，一份产物说可以用、另一份说
    不可用，而引用报告的人不会去跑 loader。"""
    read = metrics.load_calibration(run.json_path)
    claims_source = "尚不构成常数来源" not in run.md
    assert claims_source == read["usable"], (
        f"报告自称常数来源={claims_source}，loader 判 usable={read['usable']}（{read['note']}）："
        "判据写了两遍，就会只改到其中一处")


def _header(md: str) -> str:
    line = [l for l in md.splitlines() if l.startswith("- base_url：")]
    assert len(line) == 1, f"页眉的出处行必须只有一条，实际 {len(line)} 条"
    return line[0]


def test_the_re_rendered_model_comes_from_the_record_too(run, tmp_path):
    """`render_from_json` 把 `model` 递给了 `render_md`，而 `render_md` 打印的是 `CONF.model`——
    一个从没被读过的参数。它比"没有这个参数"更糟：看起来像已经修好了。把记录里的 model 换成别的，
    页眉必须跟着换。"""
    cal = _load()
    data = json.loads(Path(run.json_path).read_text(encoding="utf-8"))
    data["model"] = "model-from-the-record"
    src = tmp_path / "renamed.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "renamed.md"
    cal.render_from_json(str(src), str(out))

    line = _header(out.read_text(encoding="utf-8"))
    assert line.split("model：")[1] == "`model-from-the-record`", line
    assert f"`{cal.CONF.model}`" not in line, "页眉仍在替活配置说话：" + line


def test_a_sidecar_that_never_recorded_the_endpoint_says_so(run, tmp_path):
    """仓库里现存的那份 sidecar 属于这一类：`base_url` 和 `model` 两个字段都还没被写进去。
    今天 `--from-json` 碰到缺字段会静默拿活配置补齐，于是页眉把"现在的配置"说成"那次测量"——
    端点哪天换过地址，旧数就被盖上了新出处，而这是报告里唯一一条出处线索。"""
    cal = _load()
    data = json.loads(Path(run.json_path).read_text(encoding="utf-8"))
    for k in ("base_url", "model"):
        data.pop(k, None)
    src = tmp_path / "old.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cal.CONF.base_url = "http://10.0.0.9:9999/v1"
    out = tmp_path / "old.md"
    cal.render_from_json(str(src), str(out))

    line = _header(out.read_text(encoding="utf-8"))
    assert "未记录" in line, "字段缺失被静默补成了配置值，页眉于是伪造了一次出处：" + line
    assert "当前配置" in line and "10.0.0.9" in line, \
        "回拨值可以出现，但必须标着它来自配置，读者才知道这条线索不可信：" + line
    md = out.read_text(encoding="utf-8")
    assert "不承认" not in md, \
        "没有记录就不许比对：拿活配置去和那次测量的清单对账，得出的是一条关于 None 的假结论"
    fresh = _header(run.md)
    assert "未记录" not in fresh and "当前配置" not in fresh, "一次真跑不该自带不可信标记：" + fresh


def test_the_sidecar_carries_the_evidence_that_discredits_itself(run):
    """这次排练里桩列的 model 就是我们问的那个之外的那个（`gemma-stub` vs 配置里的名字），
    所以这份 sidecar 天生带着一条对自己不利的证据。它必须被原样记进机器可读的那一半，
    并且 `load_calibration` 要据它拒绝——只把对人有利的事实写进产物的话，读侧就永远看不到这件事。
    """
    assert run.sidecar["model_declared"] == ["gemma-stub"], run.sidecar.get("model_declared")
    read = metrics.load_calibration(run.json_path)
    assert read["usable"] is False
    assert "不承认" in read["note"], read["note"]


def test_a_denial_alone_stops_both_products_from_claiming_a_source(tmp_path):
    """把两份产物逼到只有这一个理由的角落：常数全拟合得出、正数、齐三个键，唯一不对劲的是端点
    不承认它被请求的那个 model。这时页眉和 loader 必须同时改口——它们本来就是同一句话的两种写法，
    而"两种写法"正是这一片反复出错的地方（`is None` 与 `> 0` 那一次就是这么分叉的）。

    它不靠这次排练的数据：`render_md` 是从记录的行**重新拟合**的，所以这里直接照写侧的形状造一份
    能拟合出三个正数的记录，把"未测得"这条理由从测试里拿掉。
    """
    cal = _load()
    # loader 读的是**记录里的** `constants`，`render_md` 是从行里**重新拟合**的：两边都要给，
    # 而且要给成同一组正数，否则这条测试钉的就不是"只有否认这一个理由"，而是"缺常数块"。
    fitted = {"D_decode_tok_s": 41.2, "per_call_fixed_overhead_s": 0.83,
              "P_prefill_tok_s_best_observed": 3600.0}
    feats = {"models_endpoint": {"body": {"data": [{"id": "a-weight-we-did-not-ask-for"}]}}}
    payload = {"ran_utc": "2026-09-21T00:00:00Z", "quick": True, "model": cal.CONF.model,
               "base_url": cal.CONF.base_url, "features": feats, "stream": {}, "ratio": {},
               "constants": fitted,
               "throughput": {"decode_tps_overhead_corrected": 41.2,
                              "per_call_fixed_overhead_s": 0.83},
               "latency": [{"k": 1, "prefix_reps": 130, "wall_s": 3.2, "agg_prefill_tps": 3600.0},
                           {"k": 2, "prefix_reps": 130, "wall_s": 5.0}],
               "temps": []}
    src = tmp_path / "only-denied.json"
    src.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "only-denied.md"
    cal.render_from_json(str(src), str(out))

    read = metrics.load_calibration(src)
    assert read["usable"] is False and "不承认" in read["note"], read["note"]
    md = out.read_text(encoding="utf-8")
    assert "尚不构成常数来源" in md, \
        "loader 拒绝、页眉却仍然自称来源：两条判据又分叉了，而引用报告的人不会去跑 loader"
    assert "不承认" in md, "页眉说了不可用却没说为什么"


def test_a_model_the_endpoint_does_not_advertise_is_flagged(run, tmp_path):
    """§1 的 JSON 里躺着 `/v1/models` 的答复，页眉的 model 是**请求值**，两者从来没人比对。桩就是
    一个不承认请求值的端点（它列 `gemma-stub`，我们问 `gemma-4-26b-a4b-nvfp4`），所以这条差异现在
    只存在于读的人的心算里。R7（被人换了权重）在报告里必须占一行，不能只占一句散文。"""
    cal = _load()
    listed = run.sidecar["features"]["models_endpoint"]["body"]["data"][0]["id"]
    assert listed != cal.CONF.model, "桩失配了：这条测试的两边必须本来就不同"
    flagged = [l for l in run.md.splitlines() if "不承认" in l]
    assert flagged, f"端点列出的 {listed} 与请求的 {cal.CONF.model} 不一致，报告却一字未提"
    assert listed in flagged[0] and cal.CONF.model in flagged[0], flagged[0]

    # 反面：数值对得上时不许凭空报警——否则这条结论会在每次真体检里稀释成一个背景噪音。
    data = json.loads(Path(run.json_path).read_text(encoding="utf-8"))
    data["features"]["models_endpoint"]["body"]["data"][0]["id"] = cal.CONF.model
    data["model_declared"] = [cal.CONF.model]
    src = tmp_path / "matches.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "matches.md"
    cal.render_from_json(str(src), str(out))
    assert "不承认" not in out.read_text(encoding="utf-8")


# ------------------------------------------------------------ 一次成功不是一次测量（#103）
def test_a_single_success_in_the_diversity_scan_is_reported_as_no_reading():
    """某个温度下整批采样只成 1 次时，两把措辞尺子没有可比对象，`None` 必须原样进表。

    `#103` 把 `collapse_round`/`opening_distinct_rate` 的地板抬到"两个开口的"之后，这条扫描是它上游
    唯一会花真钱的地方：端点抖动时这里可能只拿到一份发言，而 `round(None, 3)` 会在六段探针全部跑完
    之后才炸，报告和 sidecar 一个字都留不下。20:26:50Z 那具变异（把守卫删回 `round(collapse, 3)`）
    在 863 条里是 MISSED——这一条就是补上的那个读者。
    """
    cal = _load()

    class OneThenNothing:
        def __init__(self) -> None:
            self.n = 0

        async def complete(self, _c, _msgs, **_kw):
            self.n += 1
            if self.n == 1:
                return {"ok": True, "text": "我听听大家的发言，再决定指控谁。"}
            return {"ok": False, "status": 503, "err": "stub: 这个温度下只成了一次"}

    rows = asyncio.run(cal.temp_diversity_scan(None, OneThenNothing(), True))
    assert rows[0]["n_speech"] == 1, rows[0]
    assert rows[0]["collapse_round"] is None, "一次成功的采样不是一次测量"
    assert rows[0]["opening_distinct_rate"] is None
    assert rows[0]["seat_mention_rate"] == 0.0, "点名率是逐条算的，一份发言也量得到"
