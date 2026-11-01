"""ClashLog: new clashes only, in order."""
from __future__ import annotations

import pytest

from lightfall.content.clashes import Clash, ClashLog


@pytest.fixture
def log(qapp):
    ClashLog.reset_instance()
    yield ClashLog.get_instance()
    ClashLog.reset_instance()


def _c(name, loser="facility"):
    return Clash("plan", name, "local", loser, False)


def test_report_emits_only_new_clashes(log):
    seen = []
    log.added.connect(seen.append)
    log.report([_c("a")])
    log.report([_c("a")])
    log.report([_c("a"), _c("b")])
    assert seen == [[_c("a")], [_c("b")]]


def test_all_keeps_order(log):
    log.report([_c("b"), _c("a")])
    log.report([_c("c"), _c("b")])
    assert [c.name for c in log.all()] == ["b", "a", "c"]
    assert ClashLog.get_instance() is log
