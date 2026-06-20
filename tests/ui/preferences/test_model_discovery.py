from __future__ import annotations

import pytest

from lightfall.ui.preferences import claude_settings as cs


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client. `script` maps header-style -> (status, payload)."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.calls: list = []
        _FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        return _FakeClient._responder(url, headers or {})


@pytest.fixture(autouse=True)
def _reset_cache():
    cs._MODELS_CACHE.clear()
    _FakeClient.instances.clear()
    yield
    cs._MODELS_CACHE.clear()


@pytest.fixture
def fake_httpx(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    return _FakeClient


def test_anthropic_shape_returns_ids(fake_httpx):
    fake_httpx._responder = lambda url, h: _FakeResp(
        200, {"data": [{"type": "model", "id": "claude-sonnet-4-5"},
                        {"type": "model", "id": "claude-opus-4-5"}]}
    )
    models = cs.fetch_available_models("https://api.anthropic.com", "k")
    assert models == ["claude-sonnet-4-5", "claude-opus-4-5"]


def test_openai_shape_returns_ids(fake_httpx):
    fake_httpx._responder = lambda url, h: _FakeResp(
        200, {"object": "list", "data": [{"id": "claude-sonnet-4"}, {"id": "gpt-x"}]}
    )
    assert cs.fetch_available_models("https://api.cborg.lbl.gov", "k") == [
        "claude-sonnet-4", "gpt-x"
    ]


def test_401_retries_with_bearer(fake_httpx):
    def responder(url, headers):
        if "x-api-key" in headers:
            return _FakeResp(401, {})
        assert headers.get("Authorization") == "Bearer k"
        return _FakeResp(200, {"data": [{"id": "m1"}]})
    fake_httpx._responder = responder
    assert cs.fetch_available_models("https://gw.example", "k") == ["m1"]


def test_non_200_after_retry_returns_none(fake_httpx):
    fake_httpx._responder = lambda url, h: _FakeResp(404, {})
    assert cs.fetch_available_models("https://x", "k") is None


def test_missing_key_or_url_returns_none(fake_httpx):
    assert cs.fetch_available_models("https://x", None) is None
    assert cs.fetch_available_models(None, "k") is None
    assert cs.fetch_available_models("", "k") is None


def test_exception_returns_none(fake_httpx, monkeypatch):
    def boom(url, headers=None):
        raise RuntimeError("network down")
    monkeypatch.setattr(_FakeClient, "get", boom)
    assert cs.fetch_available_models("https://x", "k") is None


def test_get_cached_models_caches_success(fake_httpx):
    calls = {"n": 0}
    def responder(url, h):
        calls["n"] += 1
        return _FakeResp(200, {"data": [{"id": "m1"}]})
    fake_httpx._responder = responder
    a = cs.get_cached_models("https://x", "k")
    b = cs.get_cached_models("https://x", "k")
    assert a == b == ["m1"]
    assert calls["n"] == 1  # second call served from cache
    c = cs.get_cached_models("https://x", "k", refresh=True)
    assert c == ["m1"] and calls["n"] == 2  # refresh re-fetches


def test_get_cached_models_does_not_cache_failure(fake_httpx):
    fake_httpx._responder = lambda url, h: _FakeResp(500, {})
    assert cs.get_cached_models("https://x", "k") is None
    assert "https://x" not in cs._MODELS_CACHE
