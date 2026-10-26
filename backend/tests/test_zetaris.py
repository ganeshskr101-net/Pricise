"""Zetaris client: request shape, rows as dicts, clear errors, the key never leaks. HTTP is mocked; the live check at the
bottom skips itself when Zetaris isn't configured or reachable."""
import io
import json
import urllib.error
import uuid

import pytest

from backend.data import zetaris
from backend.data.zetaris import ZetarisError, run_sql

KEY = "sk-test-secret-key"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ZETARIS_REST_URL", "http://zetaris.test/api/v1.0/")
    monkeypatch.setenv("ZETARIS_ORG_ID", "1")
    monkeypatch.setenv("ZETARIS_API_KEY", KEY)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock(monkeypatch, reply=None, exc=None):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["req"], seen["timeout"] = req, timeout
        if exc:
            raise exc
        return _Resp(json.dumps(reply).encode())

    monkeypatch.setattr(zetaris.urllib.request, "urlopen", fake_urlopen)
    return seen


def _http_error(code):
    return urllib.error.HTTPError("http://zetaris.test", code, "err", {}, io.BytesIO(b'{"message":"nope"}'))


def test_request_shape_and_rows(env, monkeypatch):
    seen = _mock(monkeypatch, {"headers": ["one", "s"], "data": [["1", "x"]], "total": 1})
    assert run_sql("SELECT 1 AS one, 'x' AS s;", limit=7) == [{"one": "1", "s": "x"}]
    req = seen["req"]
    assert req.full_url == "http://zetaris.test/api/v1.0/sql-editor/sqls/run"
    assert req.get_method() == "POST" and seen["timeout"] == 8
    assert req.get_header("Authorization") == f"Bearer {KEY}"
    assert req.get_header("X-org-id") == "1"
    uuid.UUID(req.get_header("X-request-id"))
    body = json.loads(req.data)
    assert body["sql"] == "SELECT 1 AS one, 'x' AS s"  # trailing ';' stripped
    assert body["source"] == "SqlEditor" and body["limit"] == 7
    uuid.UUID(body["queryId"])


def test_fresh_request_id_each_call(env, monkeypatch):
    ids = []
    monkeypatch.setattr(zetaris.urllib.request, "urlopen",
                        lambda req, timeout: ids.append(req.get_header("X-request-id")) or _Resp(b'{"data": []}'))
    run_sql("SELECT 1")
    run_sql("SELECT 1")
    assert len(set(ids)) == 2


def test_empty_result(env, monkeypatch):
    _mock(monkeypatch, {"headers": ["a"], "data": []})
    assert run_sql("SELECT a FROM t") == []


@pytest.mark.parametrize("code, words", [(400, "ZETARIS_ORG_ID"), (401, "ZETARIS_API_KEY")])
def test_clear_http_errors_without_key(env, monkeypatch, code, words):
    _mock(monkeypatch, exc=_http_error(code))
    with pytest.raises(ZetarisError) as e:
        run_sql("SELECT 1")
    msg = str(e.value)
    assert f"HTTP {code}" in msg and words in msg
    assert KEY not in msg


def test_unreachable_and_timeout(env, monkeypatch):
    _mock(monkeypatch, exc=urllib.error.URLError("connection refused"))
    with pytest.raises(ZetarisError, match="Cannot reach"):
        run_sql("SELECT 1")
    _mock(monkeypatch, exc=TimeoutError())
    with pytest.raises(ZetarisError, match="8s"):
        run_sql("SELECT 1")


def test_not_configured(monkeypatch):
    monkeypatch.setattr(zetaris, "load_dotenv", lambda: None)
    for k in ("ZETARIS_REST_URL", "ZETARIS_ORG_ID", "ZETARIS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert not zetaris.configured()
    with pytest.raises(ZetarisError, match="not configured"):
        run_sql("SELECT 1")


def test_placeholder_key_counts_as_missing(env, monkeypatch):
    monkeypatch.setenv("ZETARIS_API_KEY", "replace-me")
    assert not zetaris.configured()


def test_live_select_1():
    """Integration: runs only when .env has Zetaris settings and the instance answers."""
    if not zetaris.configured():
        pytest.skip("Zetaris not configured")
    try:
        rows = run_sql("SELECT 1 AS one")
    except ZetarisError as e:
        pytest.skip(f"Zetaris not reachable: {e}")
    assert rows == [{"one": "1"}]
