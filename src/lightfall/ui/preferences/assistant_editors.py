"""Custom form editors for Settings -> Assistant: the Claude model and the agy model + effort.

Both model lists come from a background ``QThreadFuture`` (a network call for
Claude, an ``agy models`` run for agy); a result that arrives after the
editor is gone is dropped. Neither editor writes preferences: the form reads
``value()`` (and the agy editor's ``companion_values()``) and writes them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import shiboken6
from qtpy.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from lightfall.agents.backends.info import (
    ModelOption,
    backend_info,
    effort_options_for,
    split_effort_variant,
)
from lightfall.ui.preferences.claude_settings import (
    MODEL_OPTIONS,
    ClaudeSettingsProvider,
    cached_model_ids,
    get_cached_models,
)
from lightfall.ui.preferences.form.editors import NOT_SET, ChoiceEditor, FieldEditor
from lightfall.ui.preferences.form.schema import EditorSpec, SettingField
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

AGY_EFFORT = "agy_effort"


class _OptionsCombo(ChoiceEditor):
    """A ChoiceEditor whose options can be replaced later, optionally editable.

    Editable: typed text is the value (stripped; blank or "Not set" is None) unless
    it matches an option's label, and finishing an edit emits ``changed``.
    """

    def __init__(self, editable: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(EditorSpec("choice", options=()), parent)
        self._editable = editable
        self._last: Any = None
        self._combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        if editable:
            self._combo.setEditable(True)
            self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            line = self._combo.lineEdit()
            line.editingFinished.connect(self._on_edit_finished)
            line.textChanged.connect(lambda _t: self._mute_not_set())
        self.changed.connect(self._remember)

    def _remember(self) -> None:
        self._last = self.value()

    def _on_edit_finished(self) -> None:
        if not self._loading and self.value() != self._last:
            self.changed.emit()

    def _shows_not_set(self) -> bool:
        return self._combo.currentIndex() == 0 and self._combo.currentText() == NOT_SET

    def options(self) -> list[Any]:
        return self._values[1:]

    def set_options(self, options: Sequence[tuple[Any, str]], keep: Any = None) -> None:
        """Replace the options (after "Not set") and show ``keep``, silently."""
        self._loading = True
        try:
            self._combo.clear()
            self._values = [None]
            self._combo.addItem(NOT_SET)
            for val, label in options:
                self._values.append(val)
                self._combo.addItem(label)
        finally:
            self._loading = False
        self.set_value(keep)

    def value(self) -> Any:
        if not self._editable:
            return super().value()
        text = self._combo.currentText().strip()
        index = self._combo.findText(text)
        if index > 0:
            return self._values[index]
        return None if not text or text == NOT_SET else text

    def set_value(self, value: Any) -> None:
        if not self._editable or value is None or value in self._values[1:]:
            super().set_value(value)
        else:  # an id the list does not offer is shown as typed text
            self._loading = True
            try:
                self._combo.setCurrentIndex(-1)
                self._combo.setEditText(str(value))
                self._mute_not_set()
            finally:
                self._loading = False
        self._last = self.value()


class ClaudeModelEditor(_OptionsCombo):
    """Editable Claude model combo; the endpoint's model list is fetched in the background."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(editable=True, parent=parent)
        self._future: QThreadFuture | None = None
        base_url = ClaudeSettingsProvider.get_base_url()
        cached = cached_model_ids(base_url)
        self._fill(cached)
        if base_url and cached is None:
            api_key = ClaudeSettingsProvider.get_api_key()
            self._future = QThreadFuture(
                lambda: get_cached_models(base_url, api_key),
                callback_slot=self._on_models,
                except_slot=lambda exc: logger.debug("Claude model list fetch failed: {}", exc),
                name="assistant-claude-models",
                log_exceptions=False,
            )
            self._future.start()

    def _fill(self, ids: Sequence[str] | None) -> None:
        ids = ids or [m for m in MODEL_OPTIONS if m]
        self.set_options([(m, m) for m in ids], keep=self.value())

    def _on_models(self, ids: list[str] | None) -> None:
        if not shiboken6.isValid(self):
            logger.debug("Claude model list arrived after its editor closed; dropped")
            return
        if ids:
            self._fill(ids)


class AgyModelEditor(FieldEditor):
    """agy model combo (editable) with its effort combo; owns ``agy_effort`` as a companion.

    A saved effort-variant id (``gemini-4-argon-medium``) is shown split into
    model and effort; the split values are written on the next edit.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._model = _OptionsCombo(editable=True, parent=self)
        self._effort = _OptionsCombo(parent=self)
        layout.addWidget(self._model)
        layout.addWidget(QLabel("Effort", self))
        layout.addWidget(self._effort)
        self._models: list[ModelOption] | None = None  # None until agy has listed them
        self._variant_level = ""
        self._model.changed.connect(self._on_model_changed)
        self._effort.changed.connect(self.changed)
        agy_path = ClaudeSettingsProvider.get_agy_path() or None
        self._future = QThreadFuture(
            lambda: backend_info("agy", agy_path=agy_path).list_models(),
            callback_slot=self._on_models,
            except_slot=self._on_models_failed,
            name="assistant-agy-models",
            log_exceptions=False,
        )
        self._future.start()

    # Model list -----------------------------------------------------------
    def _on_models(self, models: list[ModelOption] | None) -> None:
        if not shiboken6.isValid(self):
            logger.debug("agy model list arrived after its editor closed; dropped")
            return
        if not models:
            return  # nothing listed: keep what is shown
        self._models = list(models)
        self._model.set_options([(m.id, m.label) for m in self._models if m.id], keep=self._model.value())
        self._refresh_efforts(self._effort.value())

    def _on_models_failed(self, exc: Exception) -> None:
        logger.info("agy model list failed: {}", exc)

    def _refresh_efforts(self, keep: str | None) -> None:
        """Offer the model's levels; ``keep`` stays if offered, else Not set.

        Before agy has listed its models only ``keep`` is offered.
        """
        if self._models is None:
            levels = [keep] if keep else []
        else:
            levels = effort_options_for(self._models, self._model.value() or "")[1:]
        self._effort.set_options([(lv, lv) for lv in levels], keep=keep if keep in levels else None)

    def _on_model_changed(self) -> None:
        self._refresh_efforts(self._effort.value())
        self.changed.emit()

    # FieldEditor ----------------------------------------------------------
    def value(self) -> str | None:
        return self._model.value()

    def set_value(self, value: Any) -> None:
        base, level = split_effort_variant(str(value)) if value else ("", "")
        self._variant_level = level
        self._model.set_value(base or None)
        self._refresh_efforts(level or self._effort.value())

    def companion_values(self) -> dict[str, str | None]:
        return {AGY_EFFORT: self._effort.value()}

    def set_companion_values(self, values: dict[str, Any]) -> None:
        # A variant id's level wins over the stored effort (as ClaudeSettingsProvider's migration does).
        self._refresh_efforts(self._variant_level or values.get(AGY_EFFORT) or None)

    def set_read_only(self, read_only: bool) -> None:
        self._model.set_read_only(read_only)
        self._effort.set_read_only(read_only)


def claude_model_editor(field: SettingField) -> ClaudeModelEditor:
    """``Custom`` factory for ``claude_model``."""
    return ClaudeModelEditor()


def agy_model_editor(field: SettingField) -> AgyModelEditor:
    """``Custom`` factory for ``agy_model`` (companion ``agy_effort``)."""
    return AgyModelEditor()
