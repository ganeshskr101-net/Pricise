"""Shared test setup: every test starts in stub mode, whatever the developer's .env says. Tests that need the real
Deal Agent set AGENT_DEAL=real themselves (monkeypatch.setenv)."""
import pytest


@pytest.fixture(autouse=True)
def _stub_mode(monkeypatch):
    monkeypatch.setenv("AGENT_DEAL", "stub")


@pytest.fixture(autouse=True)
def _fixture_data(monkeypatch):
    """Every test starts on the in-memory catalog with the scripted Price Agent; tests opt in to Zetaris / the real agent."""
    monkeypatch.setenv("DATA_SOURCE", "fixture")
    monkeypatch.setenv("AGENT_PRICE", "stub")
