"""PySide feature-scan bypass and the lazily imported TiledWriter."""

from __future__ import annotations

import sys

import pytest

from lightfall.utils import qt_import_hook


@pytest.fixture
def feature():
    import PySide6.QtCore  # noqa: F401

    mod = sys.modules["shibokensupport.feature"]
    original = mod._mod_uses_pyside
    yield mod
    mod._mod_uses_pyside = original


def test_skip_replaces_the_source_scan(feature, monkeypatch):
    monkeypatch.delenv(qt_import_hook.ENV_VAR, raising=False)
    assert qt_import_hook.skip_pyside_feature_scan() is True
    assert feature._mod_uses_pyside is qt_import_hook._no_scan
    assert feature._mod_uses_pyside(sys.modules[__name__]) is False


def test_env_var_keeps_pyside_default(feature, monkeypatch):
    original = feature._mod_uses_pyside
    monkeypatch.setenv(qt_import_hook.ENV_VAR, "1")
    assert qt_import_hook.skip_pyside_feature_scan() is False
    assert feature._mod_uses_pyside is original


def test_tiled_writer_is_resolved_lazily():
    import lightfall.services.tiled_service as ts

    cls = ts._tiled_writer_class()
    from lightfall.services.tiled_writer_patch import TiledWriter

    assert cls is TiledWriter
    assert ts.TiledWriter is TiledWriter
