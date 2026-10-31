"""Tests for the Qt-free FormModel."""

from __future__ import annotations

from lightfall.ui.preferences.form.model import FormModel, WriteOp

U = "user:u1"
S = "site"


def make(data=None):
    store = dict(data or {})
    return FormModel(lambda key, scope: store.get((scope, key))), store


def allow(key, scope):
    return True


def test_untouched_model_has_no_ops():
    m, _ = make({(U, "k"): "a"})
    m.loaded(U, "k")
    assert m.ops(allow) == []
    assert m.dirty_scopes() == set()


def test_edit_then_ops():
    m, _ = make()
    m.edit(U, "k", "x")
    assert m.ops(allow) == [WriteOp(U, "k", "x")]
    assert m.dirty_scopes() == {U}


def test_clear_is_remove_op():
    m, _ = make({(U, "k"): "a"})
    m.edit(U, "k", None)
    assert m.ops(allow) == [WriteOp(U, "k", None)]


def test_empty_string_is_a_value():
    m, _ = make()
    m.edit(U, "k", "")
    assert m.ops(allow) == [WriteOp(U, "k", "")]


def test_none_vs_falsy_values_differ():
    m, _ = make({(U, "a"): 0, (U, "b"): False})
    m.edit(U, "a", False)
    m.edit(U, "b", 0)
    m.edit(U, "c", False)
    assert {o.key for o in m.ops(allow)} == {"a", "b", "c"}


def test_clear_when_already_unset_is_clean():
    m, _ = make()
    m.edit(U, "k", None)
    assert not m.is_dirty(U, "k")
    assert m.ops(allow) == []


def test_edit_back_to_loaded_is_clean():
    m, _ = make({(U, "k"): "a"})
    m.edit(U, "k", "b")
    assert m.is_dirty(U, "k")
    m.edit(U, "k", "a")
    assert not m.is_dirty(U, "k")
    assert m.ops(allow) == []


def test_two_scopes_kept_separately():
    m, _ = make({(S, "k"): "s"})
    m.edit(U, "k", "u")
    assert m.pending(U, "k") == "u"
    assert m.pending(S, "k") == "s"
    assert m.dirty_scopes() == {U}
    assert m.values(U).get("k") == "u"
    assert m.values(S).scope == S


def test_ops_skip_unwritable_scope():
    m, _ = make()
    m.edit(U, "k", "u")
    m.edit(S, "k", "s")
    assert m.ops(lambda key, scope: scope == U) == [WriteOp(U, "k", "u")]


def test_ops_sorted_and_writable_arg_order():
    m, _ = make()
    seen = []

    def writable(key, scope):
        seen.append((key, scope))
        return True

    m.edit(U, "b", 1)
    m.edit(S, "z", 1)
    m.edit(U, "a", 1)
    assert [(o.scope, o.key) for o in m.ops(writable)] == [(S, "z"), (U, "a"), (U, "b")]
    assert ("z", S) in seen


def test_reload_keeps_edit_and_flags_conflict():
    m, store = make({(U, "k"): "a"})
    m.edit(U, "k", "mine")
    store[(U, "k")] = "theirs"
    assert m.reload(U, "k") is True
    assert m.pending(U, "k") == "mine"
    assert m.conflict(U, "k")
    m.edit(U, "k", "mine2")
    assert m.conflict(U, "k")
    m.edit(U, "k", "theirs")
    assert not m.conflict(U, "k")
    m.edit(U, "k", "again")
    assert not m.conflict(U, "k")


def test_reload_updates_clean_field():
    m, store = make({(U, "k"): "a"})
    assert m.loaded(U, "k") == "a"
    store[(U, "k")] = "b"
    assert m.reload(U, "k") is True
    assert m.pending(U, "k") == "b"
    assert not m.conflict(U, "k")
    assert m.reload(U, "k") is False


def test_commit_sets_loaded_and_drops_edit_and_conflict():
    m, store = make({(U, "k"): "a"})
    m.edit(U, "k", "mine")
    store[(U, "k")] = "theirs"
    m.reload(U, "k")
    m.commit(U, "k")
    assert m.loaded(U, "k") == "mine"
    assert not m.is_dirty(U, "k")
    assert not m.conflict(U, "k")
    assert m.ops(allow) == []


def test_discard_keeps_loaded_cache():
    m, store = make({(U, "k"): "a"})
    m.edit(U, "k", "b")
    store[(U, "k")] = "changed"
    m.discard()
    assert m.ops(allow) == []
    assert m.pending(U, "k") == "a"
