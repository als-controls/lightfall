"""Tests for trigger filter predicates."""
from __future__ import annotations

import pytest

from lightfall.acquire.triggers.filter import FilterPredicate


def test_filter_plan_name_exact_match():
    f = FilterPredicate(plan_name="count")
    assert f.matches({"plan_name": "count", "tags": []})
    assert not f.matches({"plan_name": "scan", "tags": []})


def test_filter_plan_name_any_of():
    f = FilterPredicate(plan_name=["count", "scan"])
    assert f.matches({"plan_name": "scan", "tags": []})
    assert not f.matches({"plan_name": "list_scan", "tags": []})


def test_filter_tags_includes_any():
    f = FilterPredicate(tags_includes=["saxs"])
    assert f.matches({"plan_name": "count", "tags": ["saxs", "raw"]})
    assert not f.matches({"plan_name": "count", "tags": ["waxs"]})


def test_filter_start_doc_match_exact():
    f = FilterPredicate(start_doc_match={"sample_name": "Si-001"})
    assert f.matches({"plan_name": "count", "tags": [], "sample_name": "Si-001"})
    assert not f.matches({"plan_name": "count", "tags": [], "sample_name": "Si-002"})


def test_filter_combination_is_and():
    f = FilterPredicate(plan_name="count", tags_includes=["saxs"])
    assert f.matches({"plan_name": "count", "tags": ["saxs"]})
    assert not f.matches({"plan_name": "count", "tags": ["waxs"]})
    assert not f.matches({"plan_name": "scan", "tags": ["saxs"]})


def test_filter_empty_matches_all():
    f = FilterPredicate()
    assert f.matches({"plan_name": "count", "tags": []})
    assert f.matches({})
