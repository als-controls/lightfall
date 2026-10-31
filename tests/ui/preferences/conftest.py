"""Guard: a preferences test must never open a real modal dialog.

Static QMessageBox helpers and every ``exec()`` raise unless the test patches
them itself (a test's own monkeypatch / mock.patch replaces these stand-ins).
"""

from __future__ import annotations

import pytest


def _refuse(*_args, **_kwargs):
    raise AssertionError("unpatched modal dialog in test")


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(_refuse))
    for cls in (QMessageBox, QDialog, QFileDialog, QInputDialog):
        monkeypatch.setattr(cls, "exec", _refuse)
    for name in ("getOpenFileName", "getSaveFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, staticmethod(_refuse))
    for name in ("getText", "getItem", "getInt", "getDouble"):
        monkeypatch.setattr(QInputDialog, name, staticmethod(_refuse))
