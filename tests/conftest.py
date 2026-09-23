"""Fixtures that more than one test module needs.

`key` lives here because it is injected by *name*: pytest looks the parameter up in the module
namespace, so an import that exists only to hand the fixture to a second file has no AST reader
at all — the unused-import gate in `tests/test_wiring.py` correctly calls it dead. Moving it to
the framework's own address deletes that blind spot instead of exempting it, so the gate stays
blunt: no `# noqa`, no allowlist, every imported name on the test side has a real reader.
"""

from __future__ import annotations

import pytest

from wolfengine.config import Config

PLACEHOLDER = "PLACEHOLDER-NOT-A-KEY"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(Config().api_key_env, PLACEHOLDER)
    return PLACEHOLDER
