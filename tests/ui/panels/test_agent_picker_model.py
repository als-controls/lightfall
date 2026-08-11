"""Pure-logic tests for the agent picker model (`openable_specs`).

The "+" button on the tabbed Claude panel offers the agents a user may open in
a new tab: enabled, ``openable``, and not already open (tabs are singletons per
agent). Core agents list first so the built-ins are easy to find.
"""

from __future__ import annotations

from pathlib import Path

from lightfall.agents.spec import AgentSpec
from lightfall.ui.panels.claude.agent_picker import openable_specs


def _spec(name, scope="core", **kw):
    defaults = {
        "description": f"d-{name}",
        "prompt": "p",
        "scope": scope,
        "source_path": Path(f"{name}.md"),
    }
    defaults.update(kw)
    return AgentSpec(name=name, **defaults)


class _FakeRegistry:
    """Stands in for AgentSpecRegistry: only ``enabled_specs`` is consumed."""

    def __init__(self, specs):
        self._specs = specs

    def enabled_specs(self):
        return list(self._specs)


def test_excludes_already_open_agents():
    reg = _FakeRegistry([_spec("lightfall"), _spec("saxs")])
    names = [s.name for s in openable_specs(reg, {"lightfall"})]
    assert names == ["saxs"]


def test_excludes_non_openable_agents():
    reg = _FakeRegistry([_spec("saxs"), _spec("observer", openable=False)])
    names = [s.name for s in openable_specs(reg, set())]
    assert names == ["saxs"]


def test_excludes_disabled_agents():
    # Disabling is the registry's job -- the picker must read enabled_specs(),
    # not specs(), so a disabled agent never reaches the menu.
    reg = _FakeRegistry([_spec("saxs")])
    reg.specs = lambda: [_spec("saxs"), _spec("disabled_one")]
    names = [s.name for s in openable_specs(reg, set())]
    assert names == ["saxs"]


def test_sorted_core_first_then_name():
    reg = _FakeRegistry([
        _spec("zeta", scope="core"),
        _spec("alpha", scope="user"),
        _spec("beta", scope="beamline"),
        _spec("aardvark", scope="core"),
    ])
    names = [s.name for s in openable_specs(reg, set())]
    assert names == ["aardvark", "zeta", "alpha", "beta"]


def test_empty_when_everything_is_open():
    reg = _FakeRegistry([_spec("lightfall")])
    assert openable_specs(reg, {"lightfall"}) == []
