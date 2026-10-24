"""MINIMAL Meterless Nightshift client (placeholder until Abel's full version lands; keep this signature).

Every model call in Pricise goes through complete(). Nightshift is OpenAI-compatible: POST {METERLESS_BASE_URL}/chat/completions.
Reads METERLESS_BASE_URL, METERLESS_RUNTIME_TOKEN, NEMOTRON_FAST_MODEL, NEMOTRON_DEEP_MODEL from the environment or .env.
Returns "" when they are missing or the call fails, so everything still runs without keys. Never logs the token.
"""
from __future__ import annotations

import json
import os
import urllib.request
from functools import lru_cache
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
TIMEOUT_S = 8
LANES = {"fast": "NEMOTRON_FAST_MODEL", "deep": "NEMOTRON_DEEP_MODEL"}


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _env(name: str) -> str:
    v = os.environ.get(name) or _dotenv().get(name, "")
    return "" if "replace" in v.lower() else v  # .env.example placeholders count as missing


def configured(lane: str = "fast") -> bool:
    return all(_env(n) for n in ("METERLESS_BASE_URL", "METERLESS_RUNTIME_TOKEN", LANES.get(lane, "")))


def model_name(lane: str = "fast") -> str:
    return _env(LANES.get(lane, "")) or f"nemotron {lane} lane (not configured)"


def complete(prompt: str, lane: str = "fast", max_tokens: int = 64) -> str:
    """One chat completion on the given Nightshift lane. Blocking; call it with asyncio.to_thread from async code."""
    if not configured(lane):
        return ""
    body = json.dumps({"model": _env(LANES[lane]), "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(_env("METERLESS_BASE_URL").rstrip("/") + "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {_env('METERLESS_RUNTIME_TOKEN')}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read())
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:  # network, auth, shape: the caller falls back to rules
        return ""
