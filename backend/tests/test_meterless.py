from __future__ import annotations

import ast
import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

_LLM_DIR = Path(__file__).resolve().parents[1] / "llm"
if str(_LLM_DIR) not in sys.path:
    sys.path.insert(0, str(_LLM_DIR))

import meterless  # noqa: E402

TOKEN = "test-secret-token-do-not-leak"
FAST_MODEL = "nvidia/llama-3.1-nemotron-fast"
DEEP_MODEL = "nvidia/llama-3.1-nemotron-deep"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeClient:
    def __init__(self, handler, timeout: float | None = None) -> None:
        self._handler = handler
        self.timeout = timeout

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> FakeResponse:
        return self._handler(url, json or {}, headers or {})


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1")
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", TOKEN)
    monkeypatch.setenv("NEMOTRON_FAST_MODEL", FAST_MODEL)
    monkeypatch.setenv("NEMOTRON_DEEP_MODEL", DEEP_MODEL)
    trace_file = tmp_path / "traces" / "llm_calls.jsonl"
    monkeypatch.setattr(meterless, "_trace_path", lambda: trace_file)
    return trace_file


def _ok_payload(model: str, text: str = "ok") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> list[float]:
    timeouts: list[float] = []

    def factory(*_args: object, **kwargs: object) -> FakeClient:
        timeouts.append(float(kwargs.get("timeout", 0)))
        return FakeClient(handler, timeout=kwargs.get("timeout"))  # type: ignore[arg-type]

    monkeypatch.setattr(meterless.httpx, "Client", factory)
    return timeouts


def _last_trace(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    return json.loads(lines[-1])


def test_fast_uses_fast_model(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(url: str, payload: dict[str, Any], headers: dict[str, str]) -> FakeResponse:
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return FakeResponse(200, _ok_payload(FAST_MODEL, "fast-hi"))

    _patch_client(monkeypatch, handler)
    text, reported = meterless.fast([{"role": "user", "content": "hi"}])

    assert text == "fast-hi"
    assert reported == FAST_MODEL
    assert captured["payload"]["model"] == FAST_MODEL
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["headers"]["meterless-app"] == "guardian"
    assert captured["headers"]["meterless-scope"] == "user"
    assert captured["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_deep_uses_deep_model(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(_url: str, payload: dict[str, Any], _headers: dict[str, str]) -> FakeResponse:
        captured["model"] = payload["model"]
        return FakeResponse(200, _ok_payload(DEEP_MODEL, "deep-hi"))

    _patch_client(monkeypatch, handler)
    text, reported = meterless.deep([{"role": "user", "content": "hi"}])

    assert text == "deep-hi"
    assert reported == DEEP_MODEL
    assert captured["model"] == DEEP_MODEL


def test_trace_line_has_expected_fields(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_url: str, _payload: dict[str, Any], _headers: dict[str, str]) -> FakeResponse:
        return FakeResponse(200, _ok_payload(FAST_MODEL, "traced"))

    timeouts = _patch_client(monkeypatch, handler)
    meterless.fast([{"role": "user", "content": "hi"}])
    record = _last_trace(env)

    assert timeouts == [30.0]
    assert record["lane"] == "fast"
    assert record["requested_model"] == FAST_MODEL
    assert record["reported_model"] == FAST_MODEL
    assert record["status"] == "success"
    assert record["error"] is None
    assert record["prompt_tokens"] == 4
    assert record["completion_tokens"] == 2
    assert isinstance(record["latency_ms"], int)
    assert "timestamp" in record
    assert record["non_nemotron"] is False
    assert "messages" not in record
    assert "prompt" not in record


def test_retry_once_then_raises_meterless_error(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def handler(_url: str, _payload: dict[str, Any], _headers: dict[str, str]) -> FakeResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.TimeoutException("timed out")
        return FakeResponse(503, {"error": "down"})

    _patch_client(monkeypatch, handler)
    with pytest.raises(meterless.MeterlessError, match="HTTP 503") as exc_info:
        meterless.fast([{"role": "user", "content": "hi"}])

    assert attempts["n"] == 2
    assert TOKEN not in str(exc_info.value)
    record = _last_trace(env)
    assert record["status"] == "error"
    assert record["lane"] == "fast"


def test_token_never_appears_in_logs_or_trace(
    env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(_url: str, _payload: dict[str, Any], _headers: dict[str, str]) -> FakeResponse:
        return FakeResponse(200, _ok_payload("gpt-4o", "leaky"))

    _patch_client(monkeypatch, handler)
    with caplog.at_level(logging.WARNING, logger=meterless._LOG.name):
        meterless.fast([{"role": "user", "content": "hi"}])

    raw = env.read_text(encoding="utf-8")
    assert TOKEN not in raw
    assert TOKEN not in caplog.text
    assert "Bearer" not in raw


def test_non_nemotron_model_is_flagged(
    env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(_url: str, _payload: dict[str, Any], _headers: dict[str, str]) -> FakeResponse:
        return FakeResponse(200, _ok_payload("some-other-router", "oops"))

    _patch_client(monkeypatch, handler)
    with caplog.at_level(logging.WARNING, logger=meterless._LOG.name):
        _text, reported = meterless.deep([{"role": "user", "content": "hi"}])

    assert reported == "some-other-router"
    record = _last_trace(env)
    assert record["non_nemotron"] is True
    assert record["requested_model"] == DEEP_MODEL
    assert "non-Nemotron" in caplog.text


def _ok_handler(model: str):
    def handler(_url: str, _payload: dict[str, Any], _headers: dict[str, str]) -> FakeResponse:
        return FakeResponse(200, _ok_payload(model))

    return handler


@pytest.mark.parametrize(("call", "model"), [(meterless.fast, FAST_MODEL), (meterless.deep, DEEP_MODEL)])
def test_default_timeout_is_30s_and_custom_timeout_is_used(
    env: Path, monkeypatch: pytest.MonkeyPatch, call, model: str
) -> None:
    timeouts = _patch_client(monkeypatch, _ok_handler(model))
    call([{"role": "user", "content": "hi"}])
    call([{"role": "user", "content": "hi"}], timeout=4.5)

    assert timeouts == [30.0, 4.5]


def test_complete_uses_8s_timeout(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    timeouts = _patch_client(monkeypatch, _ok_handler(FAST_MODEL))

    assert meterless.complete("hi") == "ok"
    assert timeouts == [8.0]


def test_timeout_is_not_sent_in_payload(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payloads: list[dict[str, Any]] = []

    def handler(_url: str, payload: dict[str, Any], _headers: dict[str, str]) -> FakeResponse:
        payloads.append(payload)
        return FakeResponse(200, _ok_payload(FAST_MODEL))

    _patch_client(monkeypatch, handler)
    meterless.fast([{"role": "user", "content": "hi"}], timeout=3.0, temperature=0)
    meterless.complete("hi")

    assert len(payloads) == 2
    assert all("timeout" not in p for p in payloads)
    assert payloads[0]["temperature"] == 0


def test_timeout_error_shows_timeout_used(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_url: str, _payload: dict[str, Any], _headers: dict[str, str]) -> FakeResponse:
        raise httpx.TimeoutException("timed out")

    timeouts = _patch_client(monkeypatch, handler)
    with pytest.raises(meterless.MeterlessError, match=r"timed out after 2\.5s") as exc_info:
        meterless.fast([{"role": "user", "content": "hi"}], timeout=2.5)

    assert timeouts == [2.5, 2.5]  # one attempt plus one retry
    assert "30s" not in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)


def test_env_value_is_defined_once() -> None:
    tree = ast.parse(Path(meterless.__file__).read_text(encoding="utf-8"))
    defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_env_value"]

    assert len(defs) == 1
