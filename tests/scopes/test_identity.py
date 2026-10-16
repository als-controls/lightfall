from __future__ import annotations

import pytest

from lightfall.scopes.identity import beamline_of, derived_default


@pytest.mark.parametrize("bind,beamline", [
    ("branch:7.0.1.1", "7.0.1"), ("beamline:7.0.1", "7.0.1"), ("facility", None),
    ("branch:nodots", None),
])
def test_beamline_of(bind, beamline):
    assert beamline_of(bind) == beamline


def test_branch_bind_derives_all_three():
    assert derived_default("tiled_beamline", "branch:7.0.1.1", "als") == "7.0.1"
    assert derived_default("current_beamline", "branch:7.0.1.1", "als") == "7.0.1"
    assert derived_default("ipc_topic_prefix", "branch:7.0.1.1", "als") == "als.7011"


def test_beamline_bind_prefix_uses_the_beamline():
    assert derived_default("ipc_topic_prefix", "beamline:7.0.1", "als") == "als.701"


@pytest.mark.parametrize("bind", [None, "", "facility"])
def test_unbound_derives_nothing(bind):
    assert derived_default("tiled_beamline", bind, "als") is None
    assert derived_default("ipc_topic_prefix", bind, "als") is None


def test_other_keys_are_never_derived():
    assert derived_default("theme", "branch:7.0.1.1", "als") is None
