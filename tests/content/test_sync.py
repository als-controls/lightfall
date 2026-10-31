"""sync_scope / sync_chain against a fake ScopesClient."""
from __future__ import annotations

import hashlib

from lightfall.content.store import ContentStore
from lightfall.content.sync import sync_chain, sync_scope
from lightfall.scopes.client import ContentListing, ScopesError
from tests.content.conftest import item_for, make_zip

BR = "branch:7.0.1.1"


class FakeClient:
    def __init__(self):
        self.listings: dict[str, ContentListing | Exception] = {}
        self.bundles: dict[tuple[str, str, str, int], bytes | Exception] = {}
        self.listing_calls: list[tuple[str, str | None]] = []

    def publish(self, scope, type_, name, revision, files, **over):
        data = make_zip(files)
        self.bundles[(scope, type_, name, revision)] = data
        return item_for(type_, name, revision, data, files, **over)

    def serve(self, scope, items, etag):
        self.listings[scope] = ContentListing(scope, tuple(items), etag)

    def content_listing(self, scope, etag=None):
        self.listing_calls.append((scope, etag))
        got = self.listings.get(scope, ContentListing(scope, (), "empty"))
        if isinstance(got, Exception):
            raise got
        return None if etag and etag == got.etag else got

    def download_bundle(self, scope, type_, name, revision, dest, *, expected_size):
        got = self.bundles[(scope, type_, name, revision)]
        if isinstance(got, Exception):
            raise got
        dest.write_bytes(got)
        return hashlib.sha256(got).hexdigest()


def _store(tmp_path):
    return ContentStore(tmp_path / "root")


def current(store, type_, name, scope=BR):
    """The folder of the installed revision the index names."""
    item = next(i for i in store.listing(scope).items if (i.type, i.name) == (type_, name))
    return store.item_path(scope, type_, name, item.revision)


def test_first_sync_installs_everything(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    a = c.publish(BR, "agent", "helper", 1, {"helper.md": b"hi"})
    p = c.publish(BR, "plugin", "xas_tools", 2, {"__init__.py": b"x", "t/r.html": b"<p>"})
    c.serve(BR, [a, p], "e1")
    result = sync_scope(c, store, BR)
    assert set(result.changed) == {("agent", "helper"), ("plugin", "xas_tools")}
    assert (current(store, "plugin", "xas_tools") / "t" / "r.html").exists()
    assert store.listing(BR) == ContentListing(BR, (a, p), "e1")
    assert list((tmp_path / "root" / ".staging").iterdir()) == []


def test_304_is_unchanged(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    c.serve(BR, [c.publish(BR, "agent", "helper", 1, {"helper.md": b"hi"})], "e1")
    sync_scope(c, store, BR)
    result = sync_scope(c, store, BR)
    assert result.unchanged and result.changed == ()
    assert c.listing_calls[-1] == (BR, "e1")


def test_new_revision_replaces_item(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    c.serve(BR, [c.publish(BR, "agent", "helper", 1, {"helper.md": b"v1"})], "e1")
    sync_scope(c, store, BR)
    c.serve(BR, [c.publish(BR, "agent", "helper", 2, {"helper.md": b"v2"})], "e2")
    result = sync_scope(c, store, BR)
    assert result.changed == (("agent", "helper"),)
    assert (current(store, "agent", "helper") / "helper.md").read_bytes() == b"v2"


def test_retracted_item_is_removed(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    a = c.publish(BR, "agent", "helper", 1, {"helper.md": b"hi"})
    b = c.publish(BR, "skill", "fit", 1, {"SKILL.md": b"s"})
    c.serve(BR, [a, b], "e1")
    sync_scope(c, store, BR)
    c.serve(BR, [b], "e2")
    result = sync_scope(c, store, BR)
    assert result.removed == (("agent", "helper"),) and result.changed == ()
    assert store.listing(BR).items == (b,)
    folder = store.item_path(BR, "agent", "helper", 1)
    assert folder.is_dir()  # still loaded somewhere, maybe: gone at the next prune
    store.prune()
    assert not folder.exists()


def test_failed_item_keeps_previous_copy_and_retries(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    a1 = c.publish(BR, "agent", "helper", 1, {"helper.md": b"v1"})
    b1 = c.publish(BR, "agent", "other", 1, {"other.md": b"o1"})
    c.serve(BR, [a1, b1], "e1")
    sync_scope(c, store, BR)
    a2 = c.publish(BR, "agent", "helper", 2, {"helper.md": b"v2"})
    b2 = c.publish(BR, "agent", "other", 2, {"other.md": b"o2"})
    c.bundles[(BR, "agent", "helper", 2)] = make_zip({"helper.md": b"tampered"})  # sha mismatch
    c.serve(BR, [a2, b2], "e2")
    result = sync_scope(c, store, BR)
    assert result.changed == (("agent", "other"),)
    assert [(t, n) for t, n, _ in result.failed] == [("agent", "helper")]
    assert (current(store, "agent", "helper") / "helper.md").read_bytes() == b"v1"
    assert (current(store, "agent", "other") / "other.md").read_bytes() == b"o2"
    saved = store.listing(BR)
    assert saved.etag == "" and set(saved.items) == {a1, b2}
    c.bundles[(BR, "agent", "helper", 2)] = make_zip({"helper.md": b"v2"})
    retry = sync_scope(c, store, BR)
    assert retry.changed == (("agent", "helper"),)
    assert (current(store, "agent", "helper") / "helper.md").read_bytes() == b"v2"
    assert store.listing(BR).etag == "e2"


def test_download_error_keeps_previous_copy(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    c.serve(BR, [c.publish(BR, "agent", "helper", 1, {"helper.md": b"v1"})], "e1")
    sync_scope(c, store, BR)
    a2 = c.publish(BR, "agent", "helper", 2, {"helper.md": b"v2"})
    c.bundles[(BR, "agent", "helper", 2)] = ScopesError("gone", status=404)
    c.serve(BR, [a2], "e2")
    result = sync_scope(c, store, BR)
    assert len(result.failed) == 1
    assert (current(store, "agent", "helper") / "helper.md").read_bytes() == b"v1"


def test_sync_skips_invalid_listing_entries(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    bad = [c.publish(BR, "agent", "../../x", 1, {"x.md": b"x"}),
           c.publish(BR, "agent", "a/b", 1, {"x.md": b"x"}),
           c.publish(BR, "exe", "x", 1, {"x.md": b"x"})]
    good = c.publish(BR, "agent", "ok", 1, {"ok.md": b"x"})
    c.serve(BR, [*bad, good], "e1")
    result = sync_scope(c, store, BR)
    assert result.changed == (("agent", "ok"),)
    written = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file()}
    assert all(p.parts[0] == "root" for p in written)
    assert store.listing(BR).items == (good,)


def test_sync_chain_skips_user_scope_and_goes_offline_on_transport_error(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    c.serve("facility", [c.publish("facility", "skill", "s", 1, {"SKILL.md": b"s"})], "f1")
    c.listings["beamline:7.0.1"] = ScopesError("unreachable")
    chain = ["facility", "beamline:7.0.1", BR, "user:u1"]
    result = sync_chain(c, store, BR, chain)
    assert result.offline
    assert [s.scope for s in result.scopes] == ["facility"]
    assert store.chain(BR) == ("facility", "beamline:7.0.1", BR)
    assert all(scope != "user:u1" for scope, _ in c.listing_calls)
    assert result.changed_types == frozenset({"skill"})


def test_sync_chain_http_error_on_one_scope_continues(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    c.listings["facility"] = ScopesError("boom", status=500)
    c.serve(BR, [c.publish(BR, "plan", "xafs", 1, {"xafs.py": b"p"})], "b1")
    result = sync_chain(c, store, BR, ["facility", BR])
    assert not result.offline
    assert [s.scope for s in result.scopes] == ["facility", BR]
    assert result.scopes[0].failed and result.scopes[1].changed == (("plan", "xafs"),)
    assert result.changed_types == frozenset({"plan"})


def test_missing_folder_is_reinstalled_despite_matching_etag(tmp_path):
    import shutil

    c, store = FakeClient(), _store(tmp_path)
    c.serve(BR, [c.publish(BR, "agent", "helper", 1, {"helper.md": b"hi"})], "e1")
    sync_scope(c, store, BR)
    shutil.rmtree(current(store, "agent", "helper"))  # antivirus, a person, a failed delete
    result = sync_scope(c, store, BR)
    assert c.listing_calls[-1] == (BR, None)  # no etag: a 304 would skip the repair
    assert result.changed == (("agent", "helper"),)
    assert (current(store, "agent", "helper") / "helper.md").read_bytes() == b"hi"


def test_unexpected_install_error_is_per_item(tmp_path, monkeypatch):
    c, store = FakeClient(), _store(tmp_path)
    a = c.publish(BR, "agent", "a", 1, {"a.md": b"a"})
    b = c.publish(BR, "agent", "b", 1, {"b.md": b"b"})
    c.serve(BR, [a, b], "e1")
    real = store.install

    def install(scope, item, zip_path):
        if item.name == "a":
            raise RuntimeError("deflate64 not supported")
        real(scope, item, zip_path)

    monkeypatch.setattr(store, "install", install)
    result = sync_scope(c, store, BR)
    assert result.changed == (("agent", "b"),) and [f[1] for f in result.failed] == ["a"]
    assert store.listing(BR).items == (b,)


def test_sync_chain_skips_while_another_process_syncs(tmp_path):
    from filelock import FileLock

    c, store = FakeClient(), _store(tmp_path)
    store.root.mkdir(parents=True)
    with FileLock(str(store.root / ".lock")):
        result = sync_chain(c, store, BR, ["facility", BR])
    assert result.scopes == () and not result.offline and c.listing_calls == []


def test_sync_chain_prunes_when_asked(tmp_path):
    c, store = FakeClient(), _store(tmp_path)
    c.serve(BR, [c.publish(BR, "agent", "helper", 1, {"helper.md": b"v1"})], "e1")
    sync_chain(c, store, BR, [BR])
    c.serve(BR, [c.publish(BR, "agent", "helper", 2, {"helper.md": b"v2"})], "e2")
    sync_chain(c, store, BR, [BR])
    assert store.item_path(BR, "agent", "helper", 1).is_dir()
    sync_chain(c, store, BR, [BR], prune=True)
    assert not store.item_path(BR, "agent", "helper", 1).exists()
    assert current(store, "agent", "helper").is_dir()
