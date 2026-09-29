"""Fixtures that more than one test module needs.

`key` lives here because it is injected by *name*: pytest looks the parameter up in the module
namespace, so an import that exists only to hand the fixture to a second file has no AST reader
at all — the unused-import gate in `tests/test_wiring.py` correctly calls it dead. Moving it to
the framework's own address deletes that blind spot instead of exempting it, so the gate stays
blunt: no `# noqa`, no allowlist, every imported name on the test side has a real reader.

`no_network` came the same way for the same reason: `--dry-run` was not the only command that has
to be unable to send. `run --human` is refused before a transport exists (`#123`), and a fixture
that only one file can reach would have to be copied there — two copies of "零请求" is the shape
this repo calls a defect.

`git_history_is_shallow` moved here from `tests/test_doc_citations.py` for the same reason twice
over (`#153`): that file asks it before reading a README from history, and `tests/test_no_secrets.py`
asks it before certifying that no commit ever added a key value (`#190`). A predicate that decides
whether a gate answers at all is not something to keep two copies of.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from wolfengine.config import Config
from wolfengine.transport import HttpTransport

PLACEHOLDER = "PLACEHOLDER-NOT-A-KEY"

HTTP_SEND_METHODS = ("delete", "get", "head", "options", "patch", "post",
                     "put", "request", "send", "stream")


def git_history_is_shallow(repo: Path | str) -> bool:
    """这份克隆的 git 历史是被截断的吗（`git clone --depth 1`、CI 的默认深度）。"""
    r = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=repo,
                       capture_output=True, text=True)
    return r.stdout.strip() == "true"


@pytest.fixture
def full_and_shallow_clone(tmp_path):
    """两个现造的小仓库：两次提交的，和它的 `--depth 1` 克隆——探针要两向都判过才算有读者。

    第二次提交同时改第一份文件（不只是加一份新的）：完整历史里那一次留下一行删除，浅克隆把
    当前树整个当成"新加的"、一行删除都没有。截断的症状就是这一格，而按行数设的地板看不见它。
    """
    full = tmp_path / "full"
    full.mkdir()

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=full, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "probe@example.invalid")
    git("config", "user.name", "probe")
    (full / "f1.txt").write_text("aaa\nbbb\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "c1")
    (full / "f1.txt").write_text("aaa\nCCC\n", encoding="utf-8")
    (full / "f2.txt").write_text("ddd\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "c2")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--no-hardlinks",
                    f"file://{full}", str(shallow)], capture_output=True, text=True)
    return full, shallow


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(Config().api_key_env, PLACEHOLDER)
    return PLACEHOLDER


def block_the_endpoint(monkeypatch, scene: str) -> list[int]:
    """把"这一桌不许碰端点"装好：两层，动词名单只住这一个函数。

    第二层逐个换掉 `httpx.AsyncClient` 上**所有**能把字节发出去的方法，一个都不挑：名单窄了不会让
    任何用例变红，它只会让那批"零请求"的用例变成静默通过（`#222` 量出来旧名单只有 `post` 和
    `request`，而 `send`、`stream` 这两个真能触网的动词没人守）。名单应当由谁给，由
    `test_wiring.py` 里那条从现装 httpx 源码推的尺子盯着。
    """
    calls: list[int] = []

    async def boom(self, *a, **kw):
        calls.append(1)
        raise AssertionError(f"{scene}：发出了 HTTP 请求（第 {len(calls)} 次）")

    monkeypatch.setattr(HttpTransport, "chat", boom)
    for verb in HTTP_SEND_METHODS:
        monkeypatch.setattr(httpx.AsyncClient, verb, boom)
    monkeypatch.delenv(Config().api_key_env, raising=False)
    return calls


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to reach the endpoint fails the test, loudly, with a count to assert on.

    Blocked at two layers on purpose. Patching only `HttpTransport.chat` would go quiet if a
    refactor ever posted through `client` directly, and the claim under test is the absence of a
    request, not the absence of one particular function call.
    """
    return block_the_endpoint(monkeypatch, "这条命令不许碰端点")
