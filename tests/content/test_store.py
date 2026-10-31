"""ContentStore: one folder per revision, the installed index, prune and per-bind chains."""
from __future__ import annotations

import os

import pytest

from lightfall.content import store as store_mod
from lightfall.content.extract import UnsafeBundle
from lightfall.content.store import ContentStore, default_content_dir, valid_item
from lightfall.scopes.client import ContentListing
from tests.content.conftest import item_for, make_zip

BR = "branch:7.0.1.1"


def _install(store, tmp_path, revision, files, name="xas_tools"):
    data = make_zip(files)
    z = tmp_path / f"r{revision}-{name}.zip"
    z.write_bytes(data)
    item = item_for("plugin", name, revision, data, files)
    store.install(BR, item, z)
    return item


def test_install_puts_each_revision_in_its_own_folder(tmp_path):
    store = ContentStore(tmp_path / "root")
    _install(store, tmp_path, 1, {"__init__.py": b"v1", "old.py": b"x"})
    _install(store, tmp_path, 2, {"__init__.py": b"v2"})
    v1 = store.item_path(BR, "plugin", "xas_tools", 1)
    v2 = store.item_path(BR, "plugin", "xas_tools", 2)
    assert v2 == tmp_path / "root" / "branch-7.0.1.1" / "plugin" / "xas_tools@2"
    assert (v1 / "__init__.py").read_bytes() == b"v1"  # untouched: a running plugin keeps it
    assert sorted(p.name for p in v2.iterdir()) == ["__init__.py"]
    assert list((tmp_path / "root" / ".staging").iterdir()) == []


def test_install_of_an_existing_revision_is_a_no_op(tmp_path):
    store = ContentStore(tmp_path / "root")
    _install(store, tmp_path, 1, {"__init__.py": b"v1"})
    _install(store, tmp_path, 1, {"__init__.py": b"v1"})
    assert (store.item_path(BR, "plugin", "xas_tools", 1) / "__init__.py").read_bytes() == b"v1"


def test_unsafe_bundle_leaves_nothing(tmp_path):
    store = ContentStore(tmp_path / "root")
    _install(store, tmp_path, 1, {"__init__.py": b"v1"})
    with pytest.raises(UnsafeBundle):
        _install(store, tmp_path, 2, {"../evil.py": b"v2"})
    assert not store.item_path(BR, "plugin", "xas_tools", 2).exists()
    assert store.item_path(BR, "plugin", "xas_tools", 1).is_dir()
    assert list((tmp_path / "root" / ".staging").iterdir()) == []


def test_declared_counts_are_enforced_with_fallbacks(tmp_path, monkeypatch):
    store = ContentStore(tmp_path / "root")
    files = {"a.py": b"1", "b.py": b"2"}
    data = make_zip(files)
    z = tmp_path / "r.zip"
    z.write_bytes(data)
    with pytest.raises(UnsafeBundle):
        store.install(BR, item_for("plugin", "p", 1, data, files, file_count=1), z)
    monkeypatch.setattr(store_mod, "FALLBACK_MAX_FILES", 1)
    with pytest.raises(UnsafeBundle):  # an older server declares nothing: fallback limits
        store.install(BR, item_for("plugin", "p", 1, data, files, file_count=None,
                                   uncompressed_size=None), z)


def test_listing_round_trip_and_unreadable_index_is_none(tmp_path):
    store = ContentStore(tmp_path / "root")
    assert store.listing(BR) is None
    data = make_zip({"a.md": b"x"})
    listing = ContentListing(BR, (item_for("agent", "a", 1, data, {"a.md": b"x"}),), "e1")
    store.save_listing(listing)
    assert store.listing(BR) == listing
    (store.scope_dir(BR) / "index.json").write_text("{not json", encoding="utf-8")
    assert store.listing(BR) is None


def test_prune_removes_unindexed_folders_and_staging(tmp_path):
    store = ContentStore(tmp_path / "root")
    old = _install(store, tmp_path, 1, {"__init__.py": b"v1"})
    new = _install(store, tmp_path, 2, {"__init__.py": b"v2"})
    gone = _install(store, tmp_path, 3, {"__init__.py": b"g"}, name="retracted")
    store.save_listing(ContentListing(BR, (new,), "e"))
    (store.root / ".staging" / "dl-left-by-a-crash.zip").write_bytes(b"x")
    store.prune()
    assert store.item_path(BR, "plugin", "xas_tools", 2).is_dir()
    assert not store.item_path(BR, "plugin", "xas_tools", old.revision).exists()
    assert not store.item_path(BR, "plugin", "retracted", gone.revision).exists()
    assert list((store.root / ".staging").iterdir()) == []


def test_prune_keeps_a_scope_whose_index_is_unreadable(tmp_path):
    store = ContentStore(tmp_path / "root")
    _install(store, tmp_path, 1, {"__init__.py": b"v1"})
    (store.scope_dir(BR) / "index.json").write_text("{not json", encoding="utf-8")
    store.prune()
    assert store.item_path(BR, "plugin", "xas_tools", 1).is_dir()


def test_chain_record_is_per_bind(tmp_path):
    store = ContentStore(tmp_path / "root")
    store.save_chain("branch:a", ["facility", "beamline:x", "branch:a", "user:u1"])
    store.save_chain("branch:b", ["facility", "beamline:x", "branch:b"])
    assert store.chain("branch:a") == ("facility", "beamline:x", "branch:a")
    assert store.chain("branch:b") == ("facility", "beamline:x", "branch:b")
    assert store.chain("facility") == ()
    store.scope_dir("facility").mkdir(parents=True)
    store.scope_dir("branch:a").mkdir(parents=True)
    assert store.sources("branch:a") == [("facility", store.scope_dir("facility")),
                                         ("branch:a", store.scope_dir("branch:a"))]


@pytest.mark.parametrize("type_, name, ok", [
    ("plan", "xafs_scan", True), ("skill", "a.b-c", True), ("exe", "x", False),
    ("plan", "../x", False), ("plan", "a/b", False), ("plan", "a..b", False),
    ("plan", "", False), ("plan", "x" * 65, False),
])
def test_valid_item(type_, name, ok):
    assert valid_item(type_, name) is ok


@pytest.mark.skipif(os.name != "nt", reason="Windows path rule")
def test_default_content_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert default_content_dir() == tmp_path / "local" / "lightfall" / "content"


@pytest.mark.skipif(os.name == "nt", reason="POSIX path rule")
def test_default_content_dir_uses_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_content_dir() == tmp_path / "xdg" / "lightfall" / "content"
