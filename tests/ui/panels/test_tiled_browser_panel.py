"""Regression tests for TiledBrowserPanel._do_fetch sort + backend fallback.

The Tiled ``.sort(...)`` call is lazy: a server-side failure on the sort query
param does not raise where ``.sort()`` is called -- it surfaces when ``.items()``
is materialized. The correct key is backend-dependent and the two are mutually
incompatible:

* tiled SQL catalog: sorts nested ``start.time``; ``time`` is silently wrong.
* mongo_normalized (databroker): sorts top-level ``time``; ``start.time`` 500s.

So ``_do_fetch`` tries ``start.time`` first (correct on SQL, a loud 500 on
mongo), falls back to ``time`` on a 500 (never the reverse -- ``time`` on SQL is
silently wrong), caches the working key, and lists unsorted if both 500.

``_do_fetch`` is pure Python (no Qt access), so we call it unbound with a stub
``self`` -- no QApplication required.
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
    """A fake Tiled container. ``fail_keys`` are sort keys whose listing 500s
    (e.g. {"start.time"} models a mongo backend; () models the SQL catalog)."""

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


def _stub(result: _Result) -> SimpleNamespace:
    return SimpleNamespace(
        _build_query=lambda client, filters: result,
        _entry_to_record=lambda key, entry: MagicMock(plan_name="count"),
        _sort_key="start.time",
    )


def _fetch(stub):
    return TiledBrowserPanel._do_fetch(
        stub, client=None, filters=None, page=0, page_size=10
    )


def test_sql_backend_sorts_on_start_time_no_fallback():
    result = _Result(fail_keys=())  # SQL catalog: start.time works
    stub = _stub(result)

    records, total, _ = _fetch(stub)

    assert total == 2
    assert len(records) == 2
    assert result.sort_calls == ["start.time"]  # never tries the silently-wrong "time"
    assert stub._sort_key == "start.time"


def test_mongo_backend_falls_back_to_time_and_caches():
    result = _Result(fail_keys={"start.time"})  # mongo: start.time 500s
    stub = _stub(result)

    records, total, _ = _fetch(stub)

    assert total == 2
    assert len(records) == 2
    assert result.sort_calls == ["start.time", "time"]  # loud 500 -> fall back
    assert stub._sort_key == "time"  # cached so later pages skip start.time


def test_both_keys_500_lists_unsorted():
    result = _Result(fail_keys={"start.time", "time"})
    stub = _stub(result)

    records, total, plan_names = _fetch(stub)

    assert total == 2
    assert len(records) == 2  # unsorted fallback still loads the browser
    assert plan_names == ["count"]
