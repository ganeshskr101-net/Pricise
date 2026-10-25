"""Shared test setup: every test starts in stub mode, whatever the developer's .env says. Tests that need the real
Deal Agent (or Trust Agent) set AGENT_DEAL=real / AGENT_TRUST=real themselves (monkeypatch.setenv)."""
import pytest


@pytest.fixture(autouse=True)
def _stub_mode(monkeypatch):
    monkeypatch.setenv("AGENT_DEAL", "stub")
    monkeypatch.setenv("AGENT_TRUST", "stub")
