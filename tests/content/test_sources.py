"""synced_items: ordering, entry points per type, and skipping malformed items."""
from __future__ import annotations

import shutil

import pytest

from lightfall.content import sources as sources_mod
from lightfall.content.sources import SourceItem, default_synced_items, specificity, synced_items
from lightfall.content.store import ContentStore
from lightfall.scopes.client import ContentListing
from tests.content.conftest import item_for, make_zip

FAC, BL, BR = "facility", "beamline:7.0.1", "branch:7.0.1.1"
BIND = BR


def _put(store, tmp_path, scope, type_, name, rev, files):
    data = make_zip(files)
    z = tmp_path / f"{scope.replace(':', '_')}-{type_}-{name}-{rev}.zip"
    z.write_bytes(data)
    item = item_for(type_, name, rev, data, files)
    store.install(scope, item, z)
    return item


def _publish(store, scope, items):
    store.save_listing(ContentListing(scope=scope, items=tuple(items)))


@pytest.fixture
def store(tmp_path):
    s = ContentStore(tmp_path / "root")
    s.save_chain(BIND, [FAC, BL, BR])
    return s


def test_synced_items_ordered_general_to_specific(store, tmp_path):
    for scope in (BR, FAC, BL):  # install order must not matter
        item = _put(store, tmp_path, scope, "plan", "scan", 1, {"scan.py": b"x"})
        _publish(store, scope, [item])
    got = synced_items(store, BIND, "plan")
    assert [i.source for i in got] == [FAC, BL, BR]
    assert all(i.name == "scan" and i.revision == 1 for i in got)


def test_entry_points_per_type(store, tmp_path):
    items = [
        _put(store, tmp_path, BR, "plan", "scan", 2, {"scan.py": b"x", "extra.txt": b"y"}),
        _put(store, tmp_path, BR, "plugin", "single", 1, {"single.py": b"x"}),
        _put(store, tmp_path, BR, "plugin", "pkg", 3, {"__init__.py": b"x", "m.py": b"y"}),
        _put(store, tmp_path, BR, "agent", "helper", 1, {"helper.md": b"x"}),
        _put(store, tmp_path, BR, "skill", "tips", 1, {"SKILL.md": b"x", "a/b.txt": b"y"}),
    ]
    _publish(store, BR, items)
    plan = store.item_path(BR, "plan", "scan", 2)
    assert synced_items(store, BIND, "plan") == [SourceItem("plan", "scan", BR, plan / "scan.py", 2)]
    plugins = {i.name: i.path for i in synced_items(store, BIND, "plugin")}
    assert plugins == {"single": store.item_path(BR, "plugin", "single", 1) / "single.py",
                       "pkg": store.item_path(BR, "plugin", "pkg", 3)}
    (agent,) = synced_items(store, BIND, "agent")
    assert agent.path == store.item_path(BR, "agent", "helper", 1) / "helper.md"
    (skill,) = synced_items(store, BIND, "skill")
    assert skill.path == store.item_path(BR, "skill", "tips", 1)


def test_malformed_items_are_skipped(store, tmp_path):
    good = _put(store, tmp_path, BR, "plan", "good", 1, {"good.py": b"x"})
    bad_plan = _put(store, tmp_path, BR, "plan", "bad", 1, {"other.py": b"x"})
    _publish(store, BR, [bad_plan, good])
    assert [i.name for i in synced_items(store, BIND, "plan")] == ["good"]

    two_md = _put(store, tmp_path, BR, "agent", "two", 1, {"a.md": b"x", "b.md": b"y"})
    no_skill = _put(store, tmp_path, BR, "skill", "bare", 1, {"README.md": b"x"})
    gone = _put(store, tmp_path, BR, "agent", "gone", 4, {"gone.md": b"x"})
    ok = _put(store, tmp_path, BR, "agent", "ok", 1, {"ok.md": b"x"})
    _publish(store, BR, [two_md, no_skill, gone, ok])
    shutil.rmtree(store.item_path(BR, "agent", "gone", 4))
    assert [i.name for i in synced_items(store, BIND, "agent")] == ["ok"]
    assert synced_items(store, BIND, "skill") == []


def test_default_synced_items_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no site")

    monkeypatch.setattr(sources_mod, "get_site_bind", boom)
    assert default_synced_items("plan") == []


def test_specificity():
    assert specificity("core") == 0 and specificity("package") == 0
    assert [specificity(s) for s in (FAC, BL, BR, "local")] == [1, 2, 3, 4]
