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
