"""Meterless Nightshift OpenAI-compatible client (only model-call path)."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values, load_dotenv

Lane = Literal["fast", "deep"]
ChatMessage = dict[str, Any]

_LOG = logging.getLogger(__name__)
_TIMEOUT_S = 30.0
_MAX_ATTEMPTS = 2  # original + one retry
_APP_ID = "guardian"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_ENV = (
    "METERLESS_BASE_URL",
    "METERLESS_RUNTIME_TOKEN",
    "NEMOTRON_FAST_MODEL",
    "NEMOTRON_DEEP_MODEL",
)


class MeterlessError(Exception):
    """Nightshift/Meterless call failed. Message never includes secrets."""


def fast(messages: list[ChatMessage], **kw: Any) -> tuple[str, str]:
    """Chat completion on NEMOTRON_FAST_MODEL. Returns (text, reported_model)."""
    return _complete("fast", messages, **kw)


def deep(messages: list[ChatMessage], **kw: Any) -> tuple[str, str]:
    """Chat completion on NEMOTRON_DEEP_MODEL. Returns (text, reported_model)."""
    return _complete("deep", messages, **kw)


def ping() -> tuple[str, str]:
    """One-word fast-lane request for a future /health check."""
    return fast([{"role": "user", "content": "ping"}])


def _complete(lane: Lane, messages: list[ChatMessage], **kw: Any) -> tuple[str, str]:
    settings = _settings()
    requested = settings["fast_model"] if lane == "fast" else settings["deep_model"]
    payload: dict[str, Any] = dict(kw)
    payload.pop("model", None)
    payload["model"] = requested
    payload["messages"] = messages

    url = f"{settings['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings['token']}",
        "Content-Type": "application/json",
        "meterless-app": _APP_ID,
        "meterless-scope": "user",
    }

    started = time.perf_counter()
    last_error = "request failed"
    reported: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    text: str | None = None
    status: Literal["success", "error"] = "error"

    try:
        raw, reported, prompt_tokens, completion_tokens = _post_with_retry(
            url, headers, payload, settings["token"]
        )
        text = _message_text(raw)
        reported = reported or ""
        status = "success"
        return text, reported
    except MeterlessError as exc:
        last_error = str(exc)
        raise
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)
        non_nemotron = bool(reported) and not _is_nemotron(reported)
        if status == "success" and non_nemotron:
            _LOG.warning(
                "Meterless %s lane reported non-Nemotron model %s (requested %s)",
                lane,
                reported,
                requested,
            )
        _append_trace(
            {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lane": lane,
                "requested_model": requested,
                "reported_model": reported,
                "latency_ms": latency_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "status": status,
                "error": None if status == "success" else last_error,
                "non_nemotron": non_nemotron,
            }
        )


def _post_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    token: str,
) -> tuple[dict[str, Any], str | None, int | None, int | None]:
    last_error: MeterlessError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException:
            last_error = MeterlessError("Meterless request timed out after 30s")
            if attempt == 0:
                continue
            raise last_error from None
        except httpx.HTTPError as exc:
            raise MeterlessError(_redact(f"Meterless HTTP error: {exc}", token)) from None

        if response.status_code >= 500:
            last_error = MeterlessError(
                _redact(
                    f"Meterless server error HTTP {response.status_code}",
                    token,
                )
            )
            if attempt == 0:
                continue
            raise last_error

        if response.status_code >= 400:
            raise MeterlessError(
                _redact(
                    f"Meterless request failed HTTP {response.status_code}",
                    token,
                )
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise MeterlessError("Meterless returned non-JSON body") from exc

        if not isinstance(body, dict):
            raise MeterlessError("Meterless returned an unexpected JSON payload")

        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        prompt_tokens = _as_int(usage.get("prompt_tokens"))
        completion_tokens = _as_int(usage.get("completion_tokens"))
        reported = body.get("model")
        reported_name = reported if isinstance(reported, str) else None
        return body, reported_name, prompt_tokens, completion_tokens

    raise last_error or MeterlessError("Meterless request failed")


def _message_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MeterlessError("Meterless response had no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise MeterlessError("Meterless response had an invalid choice")
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(first.get("text"), str):
        return first["text"]
    raise MeterlessError("Meterless response had no text")


def _settings() -> dict[str, str]:
    _load_env()
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise MeterlessError(f"Missing required environment variables: {', '.join(missing)}")
    token = os.environ["METERLESS_RUNTIME_TOKEN"].strip()
    return {
        "base_url": _normalize_base_url(os.environ["METERLESS_BASE_URL"]),
        "token": token,
        "fast_model": os.environ["NEMOTRON_FAST_MODEL"].strip(),
        "deep_model": os.environ["NEMOTRON_DEEP_MODEL"].strip(),
    }


def _load_env() -> None:
    env_path = _PROJECT_ROOT / ".env"
    load_dotenv(env_path, override=False)
    file_vals = dotenv_values(env_path)
    for name in _REQUIRED_ENV:
        if os.environ.get(name, "").strip():
            continue
        file_val = file_vals.get(name)
        if file_val and file_val.strip():
            os.environ[name] = file_val.strip()


def _normalize_base_url(raw: str) -> str:
    value = raw.strip()
    parsed = urlparse(value if "://" in value else f"http://{value}")
    host = parsed.netloc or parsed.path.split("/")[0]
    if not host:
        raise MeterlessError("METERLESS_BASE_URL is invalid")
    path = parsed.path if parsed.netloc else "/" + "/".join(parsed.path.split("/")[1:])
    path = path.rstrip("/")
    for suffix in ("/chat/completions", "/models", "/healthz"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    while path.endswith("/v1/v1"):
        path = path[:-3]
    if path.endswith("/v1"):
        path = path[:-3]
    scheme = parsed.scheme or "http"
    return f"{scheme}://{host}/v1"


def _is_nemotron(model_name: str) -> bool:
    return "nemotron" in model_name.lower()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _redact(text: str, token: str) -> str:
    if token and token in text:
        return text.replace(token, "[redacted]")
    return text


def _trace_path() -> Path:
    return _PROJECT_ROOT / "traces" / "llm_calls.jsonl"


def _append_trace(record: dict[str, Any]) -> None:
    path = _trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# Compatibility layer for the teammates' placeholder API.
# Their agents call configured(), model_name() and complete(); keep these
# signatures. complete() returns "" when keys are missing or the call fails,
# so everything still runs without keys.
# ---------------------------------------------------------------------------

_LANE_ENV = {"fast": "NEMOTRON_FAST_MODEL", "deep": "NEMOTRON_DEEP_MODEL"}
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _env_value(name: str) -> str:
    """Env value, treating .env.example placeholders ("replace") as missing."""
    _load_env()
    value = os.environ.get(name, "").strip()
    return "" if "replace" in value.lower() else value


def configured(lane: str = "fast") -> bool:
    """True when the base URL, token and the lane's model name are all set."""
    return all(
        _env_value(name)
        for name in ("METERLESS_BASE_URL", "METERLESS_RUNTIME_TOKEN", _LANE_ENV.get(lane, ""))
    )


def model_name(lane: str = "fast") -> str:
    """Configured model name for the lane, or a readable fallback."""
    return _env_value(_LANE_ENV.get(lane, "")) or f"nemotron {lane} lane (not configured)"


def complete(prompt: str, lane: str = "fast", max_tokens: int = 64) -> str:
    """One chat completion on the given lane. Blocking; use asyncio.to_thread from async code.

    Returns "" if Nightshift is not configured or the call fails. Strips any
    <think>...</think> reasoning block the model puts in front of its answer.
    """
    if not configured(lane):
        return ""
    call = deep if lane == "deep" else fast
    try:
        text, _ = call(
            [{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=max_tokens,
        )
    except MeterlessError:
        return ""
    return _THINK_RE.sub("", text).strip()