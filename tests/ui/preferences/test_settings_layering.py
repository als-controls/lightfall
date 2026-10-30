"""Console layer vs. user scope (docs/developer-guide/settings-layering.md)."""
# ruff: noqa: F811  (the make fixture is imported from test_scoped_backend)
from __future__ import annotations

from lightfall.scopes.client import ChainInfo
from tests.ui.preferences.test_scoped_backend import (
    FakeLocal,
    Server,
    make,  # noqa: F401, F811 - fixture
)

BRANCH = "branch:7.0.1.1"
ROWS = "device_backends"


def row(rid: str, **over) -> dict:
    return {"id": rid, "type": "happi", "name": rid, "enabled": True, "config": {}, **over}


class BranchServer(Server):
    """A 7.0.1.1 console; ``role`` is the caller's role on the branch."""

    def __init__(self, role=None, **kw):
        super().__init__(**kw)
        self.role = role

    def chain(self, bind):
        return ChainInfo(chain=("facility", "beamline:7.0.1", BRANCH, "user:u1"),
                         roles={"facility": None, "beamline:7.0.1": None, BRANCH: self.role,
                                "user:u1": "owner"},
                         entitled=(BRANCH,), facility_label="ALS")


def _yaml(local):
    return lambda: {k.split(".", 1)[1]: v for k, v in local._cm.data.items()}


# -- the 7.0.1.1 incident ----------------------------------------------------

def test_user_rows_add_to_branch_and_console_rows_instead_of_replacing(make):
    server = BranchServer()
    server.rows[(BRANCH, ROWS)] = ({"b": row("b")}, 1)
    server.rows[("user:u1", ROWS)] = ({"u": row("u")}, 1)
    local = FakeLocal(device_backends={"c": row("c")})
    backend, *_ = make(server=server, local=local, bind=BRANCH)
    backend.activate()
    assert list(backend.get(ROWS)) == ["b", "c", "u"]
    assert backend.item_origins(ROWS) == {"b": BRANCH, "c": "local", "u": "user:u1"}
    assert local.get(ROWS) == {"c": row("c")}  # the console's rows were not touched


def test_user_override_of_an_inherited_row_is_per_item(make):
    server = BranchServer()
    server.rows[(BRANCH, ROWS)] = ({"b": row("b")}, 1)
    server.rows[("user:u1", ROWS)] = ({"b": row("b", enabled=False)}, 1)
    backend, *_ = make(server=server, bind=BRANCH)
    backend.activate()
    assert backend.get(ROWS)["b"]["enabled"] is False
    assert backend.item_origins(ROWS) == {"b": "user:u1"}


def test_legacy_list_in_the_console_yaml_still_merges(make):
    server = BranchServer()
    server.rows[(BRANCH, ROWS)] = ({"b": row("b")}, 1)
    backend, *_ = make(server=server, local=FakeLocal(device_backends=[row("c")]), bind=BRANCH)
    backend.activate()
    assert list(backend.get(ROWS)) == ["b", "c"]


def test_user_scope_identity_value_is_ignored(make):
    server = BranchServer()
    server.rows[(BRANCH, "tiled_beamline")] = ("7.0.1.1", 1)
    server.rows[("user:u1", "tiled_beamline")] = ("12.0.2.1", 1)
    backend, *_ = make(server=server, bind=BRANCH)
    backend.activate()
    assert backend.get("tiled_beamline") == "7.0.1.1"


# -- writes --------------------------------------------------------------------

def test_local_write_goes_to_the_yaml_and_emits(make):
    server = BranchServer()
    server.rows[(BRANCH, ROWS)] = ({"b": row("b")}, 1)
    backend, _, local, changes, _ = make(server=server, bind=BRANCH)
    backend.activate()
    backend.set(ROWS, {"c": row("c")}, scope="local")
    assert local.get(ROWS) == {"c": row("c")}
    assert not [c for c in server.calls if c[0] == "put"]
    assert changes[-1] == (ROWS, {"b": row("b"), "c": row("c")})
    backend.remove(ROWS, scope="local")
    assert local.get(ROWS) is None
    assert changes[-1] == (ROWS, {"b": row("b")})


def test_identity_key_write_goes_to_the_branch_for_a_maintainer(make):
    server = BranchServer(role="maintainer")
    backend, *_ = make(server=server, bind=BRANCH)
    backend.activate()
    backend.set("ipc_topic_prefix", "als.7011.test")
    assert server.rows[(BRANCH, "ipc_topic_prefix")][0] == "als.7011.test"
    assert ("user:u1", "ipc_topic_prefix") not in server.rows


def test_identity_key_write_is_refused_without_a_role(make):
    server = BranchServer(role="operator")
    backend, _, local, _, notes = make(server=server, bind=BRANCH)
    backend.activate()
    backend.set("tiled_beamline", "12.0.2.1")
    assert not [c for c in server.calls if c[0] == "put"]
    assert local.get("tiled_beamline") is None
    assert "beamline staff" in notes[-1][1]


def test_targets(make):
    backend, *_ = make(server=BranchServer(role="maintainer"), bind=BRANCH)
    backend.activate()
    assert backend.targets(ROWS) == ["user:u1", "local", BRANCH]
    assert backend.targets("tiled_beamline") == [BRANCH]
    assert backend.targets("theme") == ["user:u1"]
    operator, *_ = make(server=BranchServer(role="operator"), bind=BRANCH)
    operator.activate()
    assert operator.targets(ROWS) == ["user:u1", "local"]


# -- migration and the old YAML cache -------------------------------------------

def test_bound_console_never_uploads_its_yaml(make):
    backend, server, *_ = make(server=BranchServer(), local=FakeLocal(theme="dark"),
                               prefs={"theme": "dark"}, bind=BRANCH)
    backend.activate()
    assert ("user:u1", "theme") not in server.rows


def test_unbound_migration_skips_beamline_capable_keys(make):
    local = FakeLocal(theme="dark", device_backends={"c": row("c")},
                      default_data_dir="/home/alsuser/data")
    backend, server, *_ = make(local=local, prefs=_yaml(local)(), bind="facility")
    backend.activate()
    assert server.rows[("user:u1", "theme")][0] == "dark"
    assert ("user:u1", ROWS) not in server.rows
    assert ("user:u1", "default_data_dir") not in server.rows


def test_old_cache_of_the_same_user_is_dropped_but_console_values_kept(make):
    server = BranchServer()
    server.rows[("user:u1", "theme")] = ("dark", 1)
    server.rows[("user:u1", ROWS)] = ({"u": row("u")}, 1)
    local = FakeLocal(theme="dark", font_size=14, device_backends={"u": row("u")},
                      _scoped_cache_owner="u1")
    backend, *_ = make(server=server, local=local, prefs=_yaml(local)(), bind=BRANCH)
    backend.activate()
    assert local.get("theme") is None and local.get(ROWS) is None  # cached copies
    assert local.get("font_size") == 14                            # not the user's value
    assert local.get("_scoped_cache_owner") is None
    assert backend.item_origins(ROWS) == {"u": "user:u1"}


def test_old_cache_of_another_user_is_dropped_entirely(make):
    local = FakeLocal(theme="bob", device_backends={"x": row("x")}, _scoped_cache_owner="bob")
    backend, server, *_ = make(server=BranchServer(), local=local, prefs=_yaml(local)(),
                               bind="facility")
    backend.activate()
    assert local.get("theme") is None and local.get(ROWS) is None
    assert ("user:u1", "theme") not in server.rows   # bob's YAML is never uploaded
    assert backend.get("theme") is None


def test_apply_with_the_beamline_value_or_blank_writes_nothing(make):
    # Settings pages save every field on Apply; for an identity key that must neither
    # nag an operator nor touch the branch's value.
    server = BranchServer(role="maintainer")
    server.rows[(BRANCH, "ipc_topic_prefix")] = ("als.7011", 1)
    backend, _, _, _, notes = make(server=server, bind=BRANCH)
    backend.activate()
    backend.set("ipc_topic_prefix", "als.7011")
    backend.remove("ipc_topic_prefix")
    assert not [c for c in server.calls if c[0] in ("put", "delete")]
    assert server.rows[(BRANCH, "ipc_topic_prefix")] == ("als.7011", 1)
    assert notes == []


def test_pending_reason_says_whether_anything_awaits_approval():
    from lightfall.ui.preferences.scoped_backend import _pending_reason

    assert "awaiting approval" in _pending_reason("pending")
    assert "rejected" in _pending_reason("rejected")
    assert "no proposal was filed" in _pending_reason("unproposed")
    assert "no proposal was filed" in _pending_reason(None)


def test_branch_value_counts_once_chain_carries_its_approval(tmp_path, qapp):
    """A key approved on the server but unknown to this client resolves by the server's rules."""
    from lightfall.scopes.cache import SnapshotCache
    from lightfall.scopes.journal import WriteJournal
    from lightfall.ui.preferences.scoped_backend import ScopedBackend
    from tests.ui.preferences.test_scoped_backend import sync_run

    class Approving(BranchServer):
        specs = ()

        def chain(self, bind):
            info = super().chain(bind)
            from dataclasses import replace
            return replace(info, specs=self.specs)

    server = Approving()
    server.rows[(BRANCH, "voice.wake_word")] = ("hey lightfall", 1)
    backend = ScopedBackend(FakeLocal(), client=server, cache=SnapshotCache(tmp_path / "c"),
                            journal=WriteJournal(tmp_path / "j.json"), bind=BRANCH,
                            identity="u1", run=sync_run, notify=lambda *a: None, poll_ms=0)
    changes = []
    backend.changed.connect(lambda k, v: changes.append((k, v)))
    backend.activate()
    assert backend.get("voice.wake_word") is None                # user-only: branch ignored
    server.specs = ({"key": "voice.wake_word", "type": "str", "scopes": ["branch", "user"]},)
    backend.refresh()
    assert backend.get("voice.wake_word") == "hey lightfall"
    assert ("voice.wake_word", "hey lightfall") in changes
