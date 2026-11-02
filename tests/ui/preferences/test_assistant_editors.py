"""Settings -> Assistant form editors: Claude model combo and agy model + effort."""

from __future__ import annotations

import pytest

from lightfall.agents.backends.info import ModelOption
from lightfall.ui.preferences import assistant_editors as ae
from lightfall.ui.preferences import claude_settings as cs
from lightfall.ui.preferences.form.editors import NOT_SET
from lightfall.ui.preferences.form.schema import SettingField, Text


class _FakePrefs:
    def __init__(self, data=None):
        self.values = dict(data or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):  # pragma: no cover - editors must never write
        raise AssertionError(f"editor wrote {key}")


class DeferredFuture:
    """Stands in for QThreadFuture: ``finish()`` delivers the result later."""

    started: list[DeferredFuture] = []

    def __init__(self, fn, *args, callback_slot=None, except_slot=None, **_kwargs):
        self._fn, self._args = fn, args
        self._callback, self._except = callback_slot, except_slot

    def start(self):
        DeferredFuture.started.append(self)

    def isRunning(self):  # noqa: N802
        return False

    def finish(self):
        try:
            result = self._fn(*self._args)
        except Exception as exc:  # noqa: BLE001 - mirrors QThreadFuture's except_slot
            self._except(exc)
        else:
            self._callback(result)


class InlineFuture(DeferredFuture):
    def start(self):
        super().start()
        self.finish()


@pytest.fixture
def prefs(monkeypatch):
    store = _FakePrefs({"claude_api_key": "k", "claude_endpoint": "anthropic", "agy_path": "C:/agy.exe"})
    monkeypatch.setattr(cs.PreferencesManager, "get_instance", classmethod(lambda cls: store))
    monkeypatch.setattr(cs, "_MODELS_CACHE", {})
    DeferredFuture.started = []
    return store


def _use(monkeypatch, cls):
    monkeypatch.setattr(ae, "QThreadFuture", cls)
    return DeferredFuture.started


def _fetch(monkeypatch, result):
    calls = []

    def fetch(base_url, api_key, *, refresh=False):
        calls.append((base_url, api_key))
        return result

    monkeypatch.setattr(ae, "get_cached_models", fetch)
    return calls


FIELD = SettingField("claude_model", Text())


def _claude(qtbot):
    editor = ae.claude_model_editor(FIELD)
    qtbot.addWidget(editor)
    return editor


def _items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


# Claude ---------------------------------------------------------------------
def test_claude_model_not_set_round_trips(qtbot, monkeypatch, prefs):
    _use(monkeypatch, InlineFuture)
    _fetch(monkeypatch, None)
    editor = _claude(qtbot)
    editor.set_value("claude-sonnet")
    editor.set_value(None)
    assert editor.value() is None
    assert editor._combo.currentIndex() == 0
    editor._combo.setEditText("   ")
    assert editor.value() is None  # blank text is not set, never ""


def test_claude_model_presets_before_list(qtbot, monkeypatch, prefs):
    started = _use(monkeypatch, DeferredFuture)
    calls = _fetch(monkeypatch, ["claude-x-1"])
    editor = _claude(qtbot)
    assert _items(editor._combo) == [NOT_SET, "claude-opus", "claude-sonnet", "claude-haiku"]
    assert len(started) == 1 and calls == []  # not run on construction (the future does it)
    started[0].finish()
    assert calls == [("https://api.anthropic.com", "k")]


def test_claude_model_custom_id_round_trips(qtbot, monkeypatch, prefs):
    _use(monkeypatch, InlineFuture)
    _fetch(monkeypatch, None)
    editor = _claude(qtbot)
    seen = []
    editor.changed.connect(lambda: seen.append(editor.value()))
    editor.set_value("my-custom-model")
    assert editor.value() == "my-custom-model"
    assert editor._combo.currentText() == "my-custom-model"
    assert seen == []  # set_value is silent
    editor._combo.lineEdit().setText("  other-model ")
    editor._combo.lineEdit().editingFinished.emit()
    assert seen == ["other-model"]


def test_claude_model_index_change_emits(qtbot, monkeypatch, prefs):
    _use(monkeypatch, InlineFuture)
    _fetch(monkeypatch, None)
    editor = _claude(qtbot)
    seen = []
    editor.changed.connect(lambda: seen.append(editor.value()))
    editor._combo.setCurrentIndex(2)
    assert seen == ["claude-sonnet"]


def test_claude_model_list_refreshes_keeping_value(qtbot, monkeypatch, prefs):
    started = _use(monkeypatch, DeferredFuture)
    _fetch(monkeypatch, ["claude-x-1", "claude-sonnet"])
    editor = _claude(qtbot)
    editor.set_value("claude-sonnet")
    seen = []
    editor.changed.connect(lambda: seen.append(1))
    started[0].finish()
    assert _items(editor._combo) == [NOT_SET, "claude-x-1", "claude-sonnet"]
    assert editor.value() == "claude-sonnet"
    assert seen == []  # a refill is not an edit


def test_claude_typed_model_survives_the_refill(qtbot, monkeypatch, prefs):
    started = _use(monkeypatch, DeferredFuture)
    _fetch(monkeypatch, ["claude-x-1"])
    editor = _claude(qtbot)
    editor._combo.setEditText("my-custom-model")
    started[0].finish()
    assert "claude-x-1" in _items(editor._combo)
    assert editor.value() == "my-custom-model"


def test_claude_cached_models_fill_without_a_fetch(qtbot, monkeypatch, prefs):
    started = _use(monkeypatch, DeferredFuture)
    calls = _fetch(monkeypatch, ["unused"])
    cs._MODELS_CACHE["https://api.anthropic.com"] = ["claude-cached"]
    editor = _claude(qtbot)
    assert _items(editor._combo) == [NOT_SET, "claude-cached"]
    assert started == [] and calls == []


def test_claude_model_result_after_close_dropped(qtbot, monkeypatch, prefs):
    started = _use(monkeypatch, DeferredFuture)
    _fetch(monkeypatch, ["claude-x-1"])
    editor = ae.claude_model_editor(FIELD)
    editor.deleteLater()
    qtbot.waitUntil(lambda: not _alive(editor), timeout=3000)
    started[0].finish()  # no RuntimeError from a dead widget


def _alive(obj):
    import shiboken6

    return shiboken6.isValid(obj)


# agy ------------------------------------------------------------------------
MODELS = [
    ModelOption("gemini-4-argon", "Gemini 4 Argon", ("low", "medium", "high")),
    ModelOption("gemini-4-flash", "Gemini 4 Flash", ("low",)),
    ModelOption("plain", "Plain"),
]


class _Info:
    def __init__(self, models=None, error=None):
        self.models, self.error = models, error
        self.paths = []

    def factory(self, kind, *, agy_path=None):
        assert kind == "agy"
        self.paths.append(agy_path)
        return self

    def list_models(self, refresh=False):
        if self.error is not None:
            raise self.error
        return self.models


def _agy(qtbot, monkeypatch, info, future=InlineFuture):
    started = _use(monkeypatch, future)
    monkeypatch.setattr(ae, "backend_info", info.factory)
    editor = ae.agy_model_editor(SettingField("agy_model", Text(), companions=("agy_effort",)))
    qtbot.addWidget(editor)
    return editor, started


def _efforts(editor):
    return _items(editor._effort._combo)


def test_agy_model_effort_companion(qtbot, monkeypatch, prefs):
    info = _Info(MODELS)
    editor, _ = _agy(qtbot, monkeypatch, info)
    assert info.paths == ["C:/agy.exe"]
    editor.set_value("gemini-4-argon")
    editor.set_companion_values({"agy_effort": "medium"})
    assert editor._model._combo.currentText() == "Gemini 4 Argon"
    assert editor.value() == "gemini-4-argon"
    assert _efforts(editor) == [NOT_SET, "low", "medium", "high"]
    assert editor.companion_values() == {"agy_effort": "medium"}
    seen = []
    editor.changed.connect(lambda: seen.append(editor.companion_values()))
    editor._effort._combo.setCurrentIndex(3)
    assert seen == [{"agy_effort": "high"}]
    editor.set_companion_values({"agy_effort": None})
    assert editor.companion_values() == {"agy_effort": None}


def test_agy_not_set(qtbot, monkeypatch, prefs):
    editor, _ = _agy(qtbot, monkeypatch, _Info(MODELS))
    editor.set_value(None)
    editor.set_companion_values({"agy_effort": None})
    assert editor.value() is None
    assert editor.companion_values() == {"agy_effort": None}


def test_agy_effort_resets_when_model_lacks_level(qtbot, monkeypatch, prefs):
    editor, _ = _agy(qtbot, monkeypatch, _Info(MODELS))
    editor.set_value("gemini-4-argon")
    editor.set_companion_values({"agy_effort": "low"})
    seen = []
    editor.changed.connect(lambda: seen.append((editor.value(), editor.companion_values())))
    editor._model._combo.setCurrentIndex(2)  # Gemini 4 Flash offers low
    assert seen[-1] == ("gemini-4-flash", {"agy_effort": "low"})
    editor._effort._combo.setCurrentIndex(0)
    editor.set_companion_values({"agy_effort": "low"})
    editor._model._combo.setCurrentIndex(1)  # back to argon: low kept
    editor._effort._combo.setCurrentIndex(3)  # high
    editor._model._combo.setCurrentIndex(2)  # flash has no high
    assert seen[-1] == ("gemini-4-flash", {"agy_effort": None})
    assert _efforts(editor) == [NOT_SET, "low"]


def test_agy_variant_id_displays_split_without_changed(qtbot, monkeypatch, prefs):
    editor, _ = _agy(qtbot, monkeypatch, _Info(MODELS))
    seen = []
    editor.changed.connect(lambda: seen.append(1))
    editor.set_value("gemini-4-argon-medium")
    editor.set_companion_values({"agy_effort": None})  # the variant's level wins
    assert seen == []
    assert editor._model._combo.currentText() == "Gemini 4 Argon"
    assert editor._effort._combo.currentText() == "medium"
    editor._effort._combo.setCurrentIndex(3)  # a later edit writes the split values
    assert seen == [1]
    assert editor.value() == "gemini-4-argon"
    assert editor.companion_values() == {"agy_effort": "high"}


def test_agy_saved_level_offered_before_list(qtbot, monkeypatch, prefs):
    editor, started = _agy(qtbot, monkeypatch, _Info(MODELS), future=DeferredFuture)
    editor.set_value("gemini-4-argon")
    editor.set_companion_values({"agy_effort": "high"})
    assert _efforts(editor) == [NOT_SET, "high"]
    assert editor.companion_values() == {"agy_effort": "high"}
    started[0].finish()
    assert _efforts(editor) == [NOT_SET, "low", "medium", "high"]
    assert editor.companion_values() == {"agy_effort": "high"}
    assert editor.value() == "gemini-4-argon"


def test_agy_list_failure_keeps_value(qtbot, monkeypatch, prefs):
    editor, started = _agy(qtbot, monkeypatch, _Info(error=RuntimeError("agy not found")),
                           future=DeferredFuture)
    editor.set_value("gemini-9-custom")
    editor.set_companion_values({"agy_effort": "max"})
    started[0].finish()
    assert editor.value() == "gemini-9-custom"
    assert editor.companion_values() == {"agy_effort": "max"}


def test_agy_typed_model_survives_list(qtbot, monkeypatch, prefs):
    editor, started = _agy(qtbot, monkeypatch, _Info(MODELS), future=DeferredFuture)
    editor._model._combo.setEditText("my-model")
    started[0].finish()
    assert editor.value() == "my-model"


def test_agy_result_after_close_dropped(qtbot, monkeypatch, prefs):
    info = _Info(MODELS)
    started = _use(monkeypatch, DeferredFuture)
    monkeypatch.setattr(ae, "backend_info", info.factory)
    editor = ae.agy_model_editor(SettingField("agy_model", Text(), companions=("agy_effort",)))
    editor.deleteLater()
    qtbot.waitUntil(lambda: not _alive(editor), timeout=3000)
    started[0].finish()


# Not set is a placeholder, not text ------------------------------------------
def test_claude_not_set_is_placeholder_and_typing_gives_the_id(qtbot, monkeypatch, prefs):
    _use(monkeypatch, InlineFuture)
    _fetch(monkeypatch, None)
    editor = _claude(qtbot)
    editor.set_value("claude-sonnet")
    editor.set_value(None)
    line = editor._combo.lineEdit()
    assert line.text() == "" and line.placeholderText() == NOT_SET
    qtbot.keyClicks(line, "claude-x")
    assert editor.value() == "claude-x"
    editor._combo.setCurrentIndex(1)
    editor._combo.setCurrentIndex(0)  # picking Not set from the list
    assert line.text() == "" and editor.value() is None


def test_agy_not_set_is_placeholder_and_typing_gives_the_id(qtbot, monkeypatch, prefs):
    editor, _ = _agy(qtbot, monkeypatch, _Info(MODELS))
    editor.set_value(None)
    line = editor._model._combo.lineEdit()
    assert line.text() == "" and line.placeholderText() == NOT_SET
    qtbot.keyClicks(line, "claude-x")
    assert editor.value() == "claude-x"


# The agy list is fetched again when the popup opens and it is still unknown ---
class _FlakyInfo(_Info):
    def __init__(self):
        super().__init__(MODELS)
        self.calls = 0

    def list_models(self, refresh=False):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("not signed in")
        return self.models


def _popup(combo):
    combo.showPopup()
    combo.hidePopup()


def test_agy_popup_refetches_unknown_list(qtbot, monkeypatch, prefs):
    info = _FlakyInfo()
    editor, started = _agy(qtbot, monkeypatch, info)
    assert info.calls == 1 and _items(editor._model._combo) == [NOT_SET]
    _popup(editor._model._combo)
    assert info.calls == 2
    assert _items(editor._model._combo) == [NOT_SET, "Gemini 4 Argon", "Gemini 4 Flash", "Plain"]
    _popup(editor._model._combo)  # known now: no further fetch
    assert info.calls == 2


def test_agy_popup_does_not_refetch_while_in_flight(qtbot, monkeypatch, prefs):
    info = _FlakyInfo()
    editor, started = _agy(qtbot, monkeypatch, info, future=DeferredFuture)
    _popup(editor._model._combo)
    assert len(started) == 1  # the first fetch has not finished
    started[0].finish()  # fails
    _popup(editor._model._combo)
    assert len(started) == 2
    started[1].finish()
    assert editor._models is not None
