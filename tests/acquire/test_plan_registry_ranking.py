"""PlanRegistry.register_ranked: core and package beat scoped and local sources."""
from __future__ import annotations

import pytest

from lightfall.acquire.plans.registry import PlanRegistry, create_default_registry
from lightfall.content.clashes import Clash


def _plan_a():
    """Plan A."""
    yield from ()


def _plan_b():
    """Plan B."""
    yield from ()


@pytest.fixture
def registry():
    return PlanRegistry()


def test_package_beats_local_and_displaces_it(registry):
    assert registry.register_ranked("p", _plan_a, "scan", "local") is None
    clash = registry.register_ranked("p", _plan_b, "scan", "package")
    assert clash == Clash("plan", "p", "package", "local", False)
    info = registry.get_plan("p")
    assert info.func is _plan_b
    assert info.source == "package"


def test_local_refused_against_core(registry):
    registry.register_ranked("p", _plan_a, "scan", "core")
    clash = registry.register_ranked("p", _plan_b, "scan", "local")
    assert clash == Clash("plan", "p", "core", "local", False)
    info = registry.get_plan("p")
    assert info.func is _plan_a
    assert info.source == "core"


def test_first_package_wins_among_packages(registry):
    registry.register_ranked("p", _plan_a, "scan", "package")
    clash = registry.register_ranked("p", _plan_b, "scan", "package")
    assert clash == Clash("plan", "p", "package", "package", False)
    assert registry.get_plan("p").func is _plan_a


def test_core_beats_package_first_wins(registry):
    registry.register_ranked("p", _plan_a, "scan", "core")
    clash = registry.register_ranked("p", _plan_b, "scan", "package")
    assert clash.winner == "core"
    assert registry.get_plan("p").func is _plan_a


def test_branch_beats_facility(registry):
    registry.register_ranked("p", _plan_a, "scan", "facility")
    clash = registry.register_ranked("p", _plan_b, "scan", "branch:7.0.1.1")
    assert clash == Clash("plan", "p", "branch:7.0.1.1", "facility", False)
    assert registry.get_plan("p").source == "branch:7.0.1.1"


def test_local_beats_branch(registry):
    registry.register_ranked("p", _plan_a, "scan", "branch:7.0.1.1")
    clash = registry.register_ranked("p", _plan_b, "scan", "local")
    assert clash == Clash("plan", "p", "local", "branch:7.0.1.1", False)
    assert registry.get_plan("p").func is _plan_b


def test_equal_specificity_existing_stays(registry):
    registry.register_ranked("p", _plan_a, "scan", "beamline:7.0.1")
    clash = registry.register_ranked("p", _plan_b, "scan", "beamline:7.0.2")
    assert clash.loser == "beamline:7.0.2"
    assert registry.get_plan("p").func is _plan_a


def test_builtins_are_core():
    registry = create_default_registry()
    assert len(registry) > 0
    assert {info.source for info in registry.list_plans()} == {"core"}
