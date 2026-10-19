"""Tests for the developer memory status bar indicator."""

from __future__ import annotations

import gc
from types import SimpleNamespace

import pytest

from lightfall.ui.statusbar.plugins import memory_status as ms
from lightfall.ui.statusbar.plugins.memory_status import MemoryStatusPlugin, format_bytes


class FakeProcess:
    def __init__(self, *rss):
        self._rss = list(rss)

    def memory_info(self):
        value = self._rss.pop(0) if len(self._rss) > 1 else self._rss[0]
        return SimpleNamespace(rss=value)


def make_plugin(qtbot, monkeypatch, *rss, show=True):
    monkeypatch.setattr(ms, "should_show", lambda: show)
    plugin = MemoryStatusPlugin(process=FakeProcess(*rss))
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    plugin.connect_signals()
    return plugin


def test_format_bytes():
    assert format_bytes(412 * 2**20) == "412 MB"
    assert format_bytes(1.37 * 2**30) == "1.37 GB"


def test_hidden_unless_enabled(qtbot, monkeypatch):
    plugin = make_plugin(qtbot, monkeypatch, 100 * 2**20, show=False)
    assert not plugin.is_visible
    assert plugin._timer is None
    plugin.disconnect_signals()


def test_shows_rss_peak_and_pending_garbage(qtbot, monkeypatch):
    plugin = make_plugin(qtbot, monkeypatch, 300 * 2**20, 200 * 2**20)
    plugin.update()  # second reading is lower; the peak stays
    assert plugin.is_visible
    assert plugin._button.text() == "200 MB"
    tip = plugin._button.toolTip()
    assert "peak 300 MB" in tip
    assert "Pending garbage (gen 0/1/2)" in tip
    plugin.disconnect_signals()


def test_backlog_turns_warning_color(qtbot, monkeypatch):
    plugin = make_plugin(qtbot, monkeypatch, 100 * 2**20)
    colors = []
    monkeypatch.setattr(plugin, "set_color", colors.append)
    monkeypatch.setattr(gc, "get_count", lambda: (10_000, 0, 0))
    monkeypatch.setattr(gc, "get_threshold", lambda: (700, 10, 10))
    plugin.update()
    assert colors[-1] is not None
    monkeypatch.setattr(gc, "get_count", lambda: (5, 0, 0))
    plugin.update()
    assert colors[-1] is None
    plugin.disconnect_signals()


def test_click_collects_and_reports(qtbot, monkeypatch):
    plugin = make_plugin(qtbot, monkeypatch, 100 * 2**20, 500 * 2**20, 450 * 2**20)
    monkeypatch.setattr(gc, "collect", lambda *a: 42)
    plugin.on_clicked()
    assert "42 objects freed" in plugin._button.toolTip()
    assert "500 MB → 450 MB" in plugin._button.toolTip()
    plugin.disconnect_signals()


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_env_enables(monkeypatch, value):
    monkeypatch.setenv(ms.SHOW_ENV, value)
    assert ms.should_show()


def test_real_rss_is_positive():
    assert MemoryStatusPlugin().rss() > 0
