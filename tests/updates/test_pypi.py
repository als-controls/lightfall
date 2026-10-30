"""Tests for the PyPI latest-version check."""

from __future__ import annotations

import httpx

from lightfall.updates.pypi import DEFAULT_INDEX_URL, PypiState, latest_version

URL = f"{DEFAULT_INDEX_URL}/pypi/lightfall/json"


def releases(*versions, yanked=()):
    return {"releases": {v: [{"yanked": v in yanked}] for v in versions}}


def test_newer_release(httpx_mock):
    httpx_mock.add_response(url=URL, json=releases("0.3.0", "0.4.0"))
    latest = latest_version("lightfall", "0.3.0")
    assert latest == "0.4.0"
    assert PypiState("lightfall", "0.3.0", latest).has_update


def test_prerelease_ignored_for_stable(httpx_mock):
    httpx_mock.add_response(url=URL, json=releases("0.4.0", "0.5.0rc1"))
    latest = latest_version("lightfall", "0.4.0")
    assert latest == "0.4.0"
    assert not PypiState("lightfall", "0.4.0", latest).has_update


def test_prerelease_offered_to_prerelease(httpx_mock):
    httpx_mock.add_response(url=URL, json=releases("0.4.0", "0.5.0rc1", "0.5.0rc2"))
    assert latest_version("lightfall", "0.5.0rc1") == "0.5.0rc2"


def test_yanked_release_skipped(httpx_mock):
    httpx_mock.add_response(url=URL, json=releases("0.4.0", "0.5.0", yanked=("0.5.0",)))
    assert latest_version("lightfall", "0.4.0") == "0.4.0"


def test_404_is_quiet(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=404)
    assert latest_version("lightfall", "0.4.0") is None


def test_timeout_is_quiet(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=URL)
    assert latest_version("lightfall", "0.4.0") is None


def test_bad_json_is_quiet(httpx_mock):
    httpx_mock.add_response(url=URL, text="<html>")
    assert latest_version("lightfall", "0.4.0") is None


def test_index_url_is_used(httpx_mock):
    httpx_mock.add_response(url="https://mirror.example/pypi/lightfall/json",
                            json=releases("1.0"))
    assert latest_version("lightfall", "0.1", index_url="https://mirror.example") == "1.0"


def test_no_latest_is_not_an_update():
    assert not PypiState("lightfall", "0.4.0", None).has_update
