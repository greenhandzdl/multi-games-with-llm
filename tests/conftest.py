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
"""

from __future__ import annotations

import httpx
import pytest

from wolfengine.config import Config
from wolfengine.transport import HttpTransport

PLACEHOLDER = "PLACEHOLDER-NOT-A-KEY"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(Config().api_key_env, PLACEHOLDER)
    return PLACEHOLDER


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to reach the endpoint fails the test, loudly, with a count to assert on.

    Blocked at two layers on purpose. Patching only `HttpTransport.chat` would go quiet if a
    refactor ever posted through `client` directly, and the claim under test is the absence of a
    request, not the absence of one particular function call.
    """
    calls: list[int] = []

    async def boom(self, messages, **kw):
        calls.append(1)
        raise AssertionError(f"这条命令不得调用端点（第 {len(calls)} 次）")

    async def post_boom(self, *a, **kw):
        calls.append(1)
        raise AssertionError(f"这条命令发出了 HTTP 请求（第 {len(calls)} 次）")

    monkeypatch.setattr(HttpTransport, "chat", boom)
    monkeypatch.setattr(httpx.AsyncClient, "post", post_boom)
    monkeypatch.setattr(httpx.AsyncClient, "request", post_boom)
    monkeypatch.delenv(Config().api_key_env, raising=False)
    return calls
