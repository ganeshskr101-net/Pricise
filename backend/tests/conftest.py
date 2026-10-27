"""Shared test setup: every test starts in stub mode, whatever the developer's .env says. Tests that need the real
Deal Agent (or Trust Agent) set AGENT_DEAL=real / AGENT_TRUST=real themselves (monkeypatch.setenv)."""
import pytest


@pytest.fixture(autouse=True)
def _stub_mode(monkeypatch):
    monkeypatch.setenv("AGENT_DEAL", "stub")
    monkeypatch.setenv("AGENT_TRUST", "stub")
    monkeypatch.delenv("AGENT_ORCH", raising=False)  # tests that need the orchestrator set AGENT_ORCH=on themselves
    monkeypatch.setenv("WORLD_MODEL_URL", "http://127.0.0.1:1")  # never a running sidecar; sidecar tests set their own
    monkeypatch.setenv("TRUST_RDAP", "off")  # no registry lookups from tests; RDAP tests mock the transport


@pytest.fixture(autouse=True)
def _fixture_data(monkeypatch):
    """Every test starts on the in-memory catalog with the scripted Price Agent; tests opt in to Zetaris / the real agent."""
    monkeypatch.setenv("DATA_SOURCE", "fixture")
    monkeypatch.setenv("AGENT_PRICE", "stub")


@pytest.fixture(autouse=True)
def _no_price_observations():
    """Price observations kept in memory (fixture mode) never leak from one test into another."""
    from backend.data import repo

    repo.OBS_MEMORY.clear()
    yield
    repo.OBS_MEMORY.clear()
