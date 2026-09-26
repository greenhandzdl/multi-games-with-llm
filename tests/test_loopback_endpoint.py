r"""全工程唯一开真 socket 的用例：打到 127.0.0.1 上的桩端点，量**进程真实退出码**。

为什么必须真开一条连接，而不是用 `httpx.MockTransport`（`test_transport.py` 的手法）：缺陷不在
"发出去的字节对不对"，在**哪个 event loop 关的 client**。`cli.py` 的收尾是这样的两行——

    rc = asyncio.run(_run_many(...))        # loop A 建立连接，`asyncio.run` 退出时关掉自己
    asyncio.run(transport.aclose())         # loop B：anyio -> transport.close() -> A.call_soon
    RuntimeError: Event loop is closed

`MockTransport` 根本没有连接可关，端口不通时也一条都没建立过（connect 被拒 = 没有 stream），
所以这两条路径上它永远绿。2026-09-22 端口终于通了，第一次真跑就撞上：

* 05:19:13Z `wolf run`（key 被拒 401）——stdout 那一行读数**是对的**（`aborted_endpoint`），
  日志末行 `terminal=aborted_endpoint` 也是对的，stderr 却是 54 行 traceback；
* 05:23:33Z 同一台桩回 200 但内容不合法，一局**打完了**（`draw_day_limit`、60 个事件），
  本该 rc=0，实际 **rc=1 + traceback**。

第二具才是这一轮真正要修的：`rc` 是脚本唯一读得到的判决（plan §12 R10 的复判、`--games N`
的循环都按它分支），而崩溃发生在 `finally` 里，把 `return rc` 整个顶掉——于是**成功**也报失败。
`cmd_batch` 是同一只手的第二处，实测 05:25:14Z：批次跑完、`run_manifest.json` 里 canary 是
`ok`、两臂日志都落了盘，但 `批次 -> …` 那一行**根本没印出来**（它印在 `finally` 之后，崩在
它前面），stderr 只有 traceback。

三条用例各钉一处，缺一处就漏一具变异：`err == ""` 钉"噪声"，`rc` 钉"判决没被顶掉"，
stdout 那一行钉"读数还是到得了读者"。桩一律回 20ms 定长延迟，因为 canary 比的是头尾探针的
墙钟比（实测 ratio 1.056，阈值 1.5）——不垫这个底，第一口的 TCP 建连就把批次判成 INVALID_DRIFT，
这条用例就会去钉一个时间读数。

不打 `network` 标记：这些字节一个都没离开过这台机器，plan §11 的"离线"承诺指的是那台私有端点。
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from wolfengine import cli

PLACEHOLDER = "STUB-NOT-A-KEY"   # 不是 key，形状也不会被密钥扫描当成 key
MAX_DAYS = "2"


class _Stub(http.server.BaseHTTPRequestHandler):
    mode = "garbage"

    def log_message(self, *a):    # 每次请求都往 stderr 写一行，会把这一轮要钉的 stderr 淹没
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        time.sleep(0.02)                      # 定长：让 canary 的头尾比测的是端点，不是建连

        if self.mode == "refuse":             # new-api 网关拒 key 的原样形状
            body = json.dumps({"error": {"code": "", "type": "new_api_error",
                                         "message": "Invalid token (request id: stub)"}})
            code = 401
        else:                                 # 200，但内容不是一个动作：每座都落到引擎兜底
            body = json.dumps({"choices": [{"message": {"content": "我无法给出动作"},
                                             "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 3,
                                         "total_tokens": 13}})
            code = 200
        raw = body.encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def stub(request):
    """一台只活在测试进程里的端点：`mode` 由每条用例自己点名（见 `_wolf` 的 `mode=`）。"""
    mode = getattr(request, "param", "garbage")

    class Srv(http.server.ThreadingHTTPServer):
        daemon_threads = True

    handler = type(f"_Stub_{mode}", (_Stub,), {"mode": mode})
    srv = Srv(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()
    srv.server_close()


def _wolf(url: str, tmp_path, *argv: str):
    """子进程跑 `wolf`，所以拿到的是**进程退出码**——本轮的缺陷正好只存在于进程这一层。

    `build_config` 是测试侧唯一的改动点：它就是把 `Config.base_url` 指向桩，与 shell 用户改
    配置里的局域网地址是同一件事（key 仍旧只走环境变量名，值是桩形状常数）。
    """
    prog = (
        "import sys\n"
        "from wolfengine import cli\n"
        "from wolfengine.config import Config\n"
        "base = Config(base_url=sys.argv[1])\n"
        'cli.build_config = lambda name="default": base\n'
        "raise SystemExit(cli.main(sys.argv[2:]))\n"
    )
    r = subprocess.run([sys.executable, "-c", prog, url, *argv], capture_output=True,
                       text=True, cwd=str(tmp_path), timeout=180,
                       env={**os.environ, "WOLF_LLM_API_KEY": PLACEHOLDER})
    return r


def _last_event(tmp_path):
    logs = sorted(tmp_path.rglob("*.jsonl"))
    assert logs, list(tmp_path.iterdir())
    return json.loads(logs[-1].read_text(encoding="utf-8").strip().splitlines()[-1])


# ---------------------------------------------------------------- 端点说不：一句读数，不许有噪声
@pytest.mark.parametrize("stub", ["refuse"], indirect=True)
def test_a_refused_key_is_one_clean_line_and_exit_1(stub, tmp_path):
    """默认拒绝（#39）在真 socket 上的样子：401 不是模型的行为，`aborted_endpoint` 才是对的
    读数——这一条它确实是这么记的。这一轮钉的是**旁边那两件事**：rc 必须由这个读数给（1），
    不由崩溃给；stderr 不许有第二句话。`fallback=0` 是这条链的反塌缩底线——端点没答应，
    九座就不许被写成"引擎替它出了手"。
    """
    r = _wolf(stub, tmp_path, "run", "--seed", "11", "--games", "1", "--max-days", MAX_DAYS,
              "--quiet", "--out", str(tmp_path))
    assert r.stderr == "", r.stderr
    assert r.returncode == 1, f"rc={r.returncode} out={r.stdout!r}"
    assert "aborted_endpoint" in r.stdout, r.stdout
    assert "fallback=0" in r.stdout, "端点拒答被记成了引擎代打：这一行是它唯一的读者"
    ev = _last_event(tmp_path)
    assert ev["payload"]["terminal"] == "aborted_endpoint", ev


# ---------------------------------------------------------------------------- 打完了就得是 0
def test_a_finished_game_exits_0_although_a_socket_was_opened(stub, tmp_path):
    """这一具是本轮的严重项：一局**打完了**的局（末行 `game_over`、终态 `draw_day_limit`，
    2026-09-22T05:23:33Z 实测 60 个事件），`_run_many` 给的 rc 就是 0（它那句"平局是答案，
    故障不是"的判据，`cli.py` 里 `res.terminal not in metrics.DECISIVE | {DRAW_DAY_LIMIT}`
    一条），可 `finally` 里的崩溃把 `return rc` 顶掉，进程交回 1。
    对只读退出码的脚本来说，"成功"和"崩溃"从此同一个码——而且它只在真端点上出现，
    mock 与端口不通两条路都看不见。
    """
    r = _wolf(stub, tmp_path, "run", "--seed", "11", "--games", "1", "--max-days", MAX_DAYS,
              "--quiet", "--out", str(tmp_path))
    assert r.stderr == "", r.stderr
    assert r.returncode == 0, f"rc={r.returncode}（打完的局被收尾崩溃报成失败）out={r.stdout!r}"
    assert "] draw_day_limit winner" in r.stdout, r.stdout


# ----------------------------------------------------------------------------- 批次是第二处
def test_a_finished_batch_prints_its_line_and_exits_0(stub, tmp_path):
    """`cmd_batch` 收尾用的是同一只手（`cli.py:533` 那个函数；这行注释在 `#123` 之前就已经
    漂过一次——`#126` 起行号闸门也扫 `tests/*.py`，所以它再漂就有人报）。
    它比 `run` 还多丢一样东西：`批次 -> …`
    那一行印在 `finally` **之后**，所以崩溃把读数也一起吞了（实测 05:25:14Z：stdout 是空的，
    而 `run_manifest.json` 里 canary 已经 `ok`、两臂日志都落了盘）。读者拿到的是 traceback，
    不是"这批能不能进结论"。
    """
    r = _wolf(stub, tmp_path, "batch", "--configs", "A,B", "--set", "B.temperature=0.6",
              "--games", "1", "--seed0", "11", "--out", str(tmp_path))
    assert r.stderr == "", r.stderr
    assert "批次 ->" in r.stdout, r.stdout
    assert r.returncode == 0, f"rc={r.returncode} out={r.stdout!r}"


# --------------------------------------------------------------------- 中途抛出来也要关上
def test_the_client_is_closed_even_when_the_run_raises():
    """`finally` 那一格是修前就有的（`--games 3` 里第二局炸了，第一局的 socket 还挂在 client
    上），这一轮做的是把 close 搬进同一个 loop——所以它也是这手的另一处风险：搬的时候把
    异常路径丢了，三条子进程用例全都不会红（它们跑的都不抛异常）。这一条直接问那只手。
    """
    closed: list[int] = []

    class _T:
        async def aclose(self):
            closed.append(1)

    async def boom():
        raise RuntimeError("这一局崩了")

    with pytest.raises(RuntimeError, match="崩了"):
        cli._run_and_close(boom, _T())
    assert closed == [1], "异常把 client 留成不关：close 必须在 finally 里，不是在它后面"
