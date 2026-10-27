"""Loads the repo's .env into os.environ at startup, so switches like AGENT_DEAL work from .env as well as the shell.

Real environment variables always win (a value set in the shell is never overwritten), empty values are skipped, and a
missing .env is fine: every setting has a safe default (stub mode). No dependency: a tiny KEY=VALUE reader.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


# SAMPLE_MODE=true: the sample/stub mode a clean clone runs in. Every agent is scripted (stub), data is the in-memory
# Store Lab catalog, and nothing external is called: no Zetaris, no Meterless Nightshift (no model call), no SerpApi, no
# RDAP lookup, no orchestrator sidecar. It overrides the switches below even when the shell sets them.
SAMPLE_OVERRIDES = {"AGENT_DEAL": "stub", "AGENT_TRUST": "stub", "AGENT_PRICE": "stub", "AGENT_ORCH": "off",
                    "DATA_SOURCE": "fixture", "TRUST_RDAP": "off"}
SAMPLE_BLANKED = ("SERPAPI_KEY", "METERLESS_BASE_URL", "METERLESS_RUNTIME_TOKEN")  # "replace-..." counts as not configured


def sample_mode() -> bool:
    return os.environ.get("SAMPLE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def apply_sample_mode() -> bool:
    """With SAMPLE_MODE=true, force the sample/stub setup. Returns whether sample mode is on."""
    if not sample_mode():
        return False
    os.environ.update(SAMPLE_OVERRIDES)
    for name in SAMPLE_BLANKED:
        os.environ[name] = "replace-sample-mode"
    return True
