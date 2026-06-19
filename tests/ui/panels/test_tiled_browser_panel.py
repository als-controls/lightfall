"""Regression tests for TiledBrowserPanel sort-key backend detection.

The two bluesky catalog backends sort runs on different, incompatible keys:

* tiled SQL catalog (CatalogOfBlueskyRuns v2+): nested ``start.time``; ``time``
  is silently wrong.
* mongo_normalized (databroker; CatalogOfBlueskyRuns v1): top-level ``time``;
  ``start.time`` 500s.

``_detect_sort_key`` picks the key deterministically from the catalog spec
version (NOT by interpreting a 500, which can occur for unrelated reasons). The
lazy ``.sort()`` failure still falls back to an unsorted listing per page, but
the cached key is not changed.

``_do_fetch``/``_detect_sort_key`` are pure Python (no Qt), so we call them
unbound with a stub ``self`` -- no QApplication required.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

from lightfall.ui.panels.tiled_browser_panel import TiledBrowserPanel


def _http_500() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://tiled.example/api/v1/search/cms/raw")
    response = httpx.Response(500, request=request)
    return httpx.HTTPStatusError(
        "Server error '500 Internal Server Error'", request=request, response=response
    )


class _Items:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    def __getitem__(self, _slice):
        if self._fail:
            raise _http_500()
        return [("uid-1", object()), ("uid-2", object())]


class _SortResult:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    def items(self) -> _Items:
        return _Items(fail=self._fail)


class _Result:
    """Fake container. ``fail_keys`` are sort keys whose listing 500s."""

    def __init__(self, fail_keys=()) -> None:
        self._fail_keys = set(fail_keys)
        self.sort_calls: list[str] = []

    def __len__(self) -> int:
        return 2

    def sort(self, spec):
        key, _direction = spec
        self.sort_calls.append(key)
        return _SortResult(fail=key in self._fail_keys)

    def items(self) -> _Items:
        return _Items(fail=False)  # unsorted listing always works


def _client(*specs):
    """Build a fake client whose .specs are (name, version) pairs."""
    return SimpleNamespace(
        specs=[SimpleNamespace(name=n, version=v) for n, v in specs]
    )


def _stub(result: _Result) -> SimpleNamespace:
    return SimpleNamespace(
        _build_query=lambda client, filters: result,
        _entry_to_record=lambda key, entry: MagicMock(plan_name="count"),
        _sort_key=None,
        _detect_sort_key=TiledBrowserPanel._detect_sort_key,  # exercise real detection
    )


def _fetch(stub, client):
    return TiledBrowserPanel._do_fetch(
        stub, client=client, filters=None, page=0, page_size=10
    )


def test_mongo_v1_spec_detected_as_time():
    result = _Result()
    stub = _stub(result)

    _fetch(stub, _client(("CatalogOfBlueskyRuns", "1")))

    assert stub._sort_key == "time"
    assert result.sort_calls == ["time"]


def test_sql_v3_spec_detected_as_start_time():
    result = _Result()
    stub = _stub(result)

    _fetch(stub, _client(("CatalogOfBlueskyRuns", "3.0")))

    assert stub._sort_key == "start.time"
    assert result.sort_calls == ["start.time"]


def test_unknown_spec_defaults_to_start_time():
    # Unknown backend -> start.time, which fails loudly (not silently wrong).
    assert TiledBrowserPanel._detect_sort_key(_client()) == "start.time"
    assert TiledBrowserPanel._detect_sort_key(SimpleNamespace()) == "start.time"


def test_500_falls_back_to_unsorted_without_changing_key():
    # SQL backend (start.time is correct) but a genuine/transient 500 occurs.
    result = _Result(fail_keys={"start.time"})
    stub = _stub(result)

    records, total, _ = _fetch(stub, _client(("CatalogOfBlueskyRuns", "3.0")))

    assert total == 2
    assert len(records) == 2  # unsorted fallback still loads the browser
    assert stub._sort_key == "start.time"  # NOT flipped to the wrong key
    assert result.sort_calls == ["start.time"]  # only the detected key tried
