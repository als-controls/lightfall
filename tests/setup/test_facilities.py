"""Packaged facility list and server-URL normalisation."""
from __future__ import annotations

import pytest

from lightfall.setup.facilities import (
    as_domain_list,
    load_facilities,
    normalize_server_url,
    other_facility,
)


def test_packaged_list_has_als_on_the_live_logbook():
    from lightfall.logbook.url import DEFAULT_LOGBOOK_URL

    (als,) = [f for f in load_facilities() if f.id == "als"]
    assert als.name == "Advanced Light Source"
    assert als.short_name == "ALS" and als.label == "ALS"
    assert als.subtitle == "Lawrence Berkeley National Lab"
    assert als.server_url == DEFAULT_LOGBOOK_URL
    assert als.proxy_domains == ("*.lbl.gov",)


def test_bad_entries_are_skipped():
    text = """
facilities:
  - id: ok
    name: OK
    server_url: https://ok.example/
  - name: missing id
    server_url: https://x
  - id: bad
    name: Bad
    server_url: "ftp://nope"
  - just a string
"""
    facilities = load_facilities(text)
    assert [f.id for f in facilities] == ["ok"]
    assert facilities[0].server_url == "https://ok.example"
    assert load_facilities("- not a mapping") == []


@pytest.mark.parametrize(("text", "expected"), [
    ("bcglucidlogbook.dhcp.lbl.gov:8000", "https://bcglucidlogbook.dhcp.lbl.gov:8000"),
    ("  http://h.example:8000/  ", "http://h.example:8000"),
    ("http://h.example:8000/health", "http://h.example:8000"),
    ("https://h.example/.well-known/lightfall", "https://h.example"),
    ("https://h.example/lb/health/", "https://h.example/lb"),
    ("HTTP://h.example/lb/", "http://h.example/lb"),
])
def test_normalize_server_url(text, expected):
    assert normalize_server_url(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "ftp://h.example", "http://", "http://a b.example"])
def test_normalize_server_url_rejects(text):
    with pytest.raises(ValueError):
        normalize_server_url(text)


def test_other_facility_uses_the_host_as_its_name():
    fac = other_facility(" lightfall.example.org ")
    assert fac.id == "other"
    assert fac.server_url == "https://lightfall.example.org"
    assert fac.name == "lightfall.example.org" and fac.proxy_domains == ()


@pytest.mark.parametrize(("value", "expected"), [
    ("*.LBL.gov", ["*.lbl.gov"]),
    (["*.lbl.gov", " ", 5, "*.Example.org "], ["*.lbl.gov", "*.example.org"]),
    (None, []),
    ({"a": 1}, []),
])
def test_as_domain_list(value, expected):
    assert as_domain_list(value) == expected


@pytest.mark.parametrize("text", ["h.example:abc", "http://h.example:abc/x", "h.example:99999"])
def test_normalize_server_url_rejects_a_bad_port(text):
    with pytest.raises(ValueError, match="port"):
        normalize_server_url(text)


def test_other_facility_remembers_an_assumed_scheme():
    assert other_facility("h.example:8000").scheme_assumed is True
    assert other_facility("https://h.example").scheme_assumed is False
    assert other_facility("http://h.example:8000").scheme_assumed is False
