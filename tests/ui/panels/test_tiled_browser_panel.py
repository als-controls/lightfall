"""Regression tests for TiledBrowserPanel._do_fetch sort fallback.

The Tiled ``.sort(...)`` call is lazy: a server-side failure on the sort query
param (e.g. a client/server version mismatch on ``sort=-start.time``, returned
as a 500) does not raise where ``.sort()`` is called -- it surfaces later when
``.items()`` is materialized. ``_do_fetch`` must catch that and re-list without
the sort so the browser still loads.

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


class _SortFails:
    """What ``result.sort(...)`` returns when the server rejects the sort:
    its ``.items()`` 500s."""

    def items(self) -> _Items:
        return _Items(fail=True)


class _SortOk:
    def items(self) -> _Items:
        return _Items(fail=False)


class _Result:
    def __init__(self, *, sort_fails: bool) -> None:
        self._sort_fails = sort_fails

    def __len__(self) -> int:
        return 2

    def sort(self, *_args):
        return _SortFails() if self._sort_fails else _SortOk()

    def items(self) -> _Items:
        return _Items(fail=False)


def _stub(result: _Result) -> SimpleNamespace:
    return SimpleNamespace(
        _build_query=lambda client, filters: result,
        _entry_to_record=lambda key, entry: MagicMock(plan_name="count"),
    )


def test_do_fetch_falls_back_to_unsorted_when_sort_500s():
    records, total, plan_names = TiledBrowserPanel._do_fetch(
        _stub(_Result(sort_fails=True)),
        client=None,
        filters=None,
        page=0,
        page_size=10,
    )

    assert total == 2
    assert len(records) == 2  # fell back to the unsorted listing
    assert plan_names == ["count"]


def test_do_fetch_uses_sorted_results_when_sort_works():
    records, total, _ = TiledBrowserPanel._do_fetch(
        _stub(_Result(sort_fails=False)),
        client=None,
        filters=None,
        page=0,
        page_size=10,
    )

    assert total == 2
    assert len(records) == 2
