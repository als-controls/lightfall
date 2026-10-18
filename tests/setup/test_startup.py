"""First-run trigger and Help → Setup wizard…"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QWidget

from lightfall.setup.progress import is_setup_completed, mark_setup_completed
from lightfall.setup.registry import SetupRegistry
from tests.setup.test_wizard import FakeStep


@pytest.fixture
def startup(qtbot, monkeypatch):
    from lightfall import main as main_mod
    from lightfall.setup import wizard as wizard_mod

    record = {"shown": [], "opened": [], "fired": []}
    monkeypatch.setattr(main_mod, "_show_startup_login", lambda w: record["shown"].append(w))

    def fake_open(parent, *, on_closed=None, **kwargs):
        record["opened"].append(parent)
        if on_closed is not None:
            on_closed()

    monkeypatch.setattr(wizard_mod, "open_setup_wizard", fake_open)
    window = QWidget()
    qtbot.addWidget(window)
    record["window"] = window
    record["run"] = lambda cfg: main_mod._startup_login(
        window, cfg, lambda: record["fired"].append(1))
    return record


def test_first_run_skips_the_startup_login_and_opens_the_wizard(qtbot, cfg, startup):
    startup["run"](cfg)
    assert startup["shown"] == [] and startup["opened"] == [] and startup["fired"] == []
    qtbot.waitUntil(lambda: startup["opened"] == [startup["window"]], timeout=3000)
    assert startup["fired"] == [1]


def test_completed_setup_keeps_the_startup_login(qtbot, cfg, startup):
    mark_setup_completed(cfg)
    startup["run"](cfg)
    assert startup["shown"] == [startup["window"]]
    assert startup["fired"] == [1]
    qtbot.wait(50)
    assert startup["opened"] == []


def test_wizard_crash_on_first_run_still_fires_plugin_wave(qtbot, cfg, startup, monkeypatch):
    from lightfall.setup import wizard as wizard_mod

    def boom(parent, **kwargs):
        raise RuntimeError("bad wizard")

    monkeypatch.setattr(wizard_mod, "open_setup_wizard", boom)
    startup["run"](cfg)
    qtbot.waitUntil(lambda: startup["fired"] == [1], timeout=3000)


def test_closing_first_run_wizard_fires_plugin_wave(qtbot, cfg):
    from lightfall.setup.context import SetupContext
    from lightfall.setup.wizard import open_setup_wizard

    fired = []
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    registry = SetupRegistry()
    registry.register(FakeStep("a"))
    ctx = SetupContext(config=cfg, session=MagicMock(), prefs=MagicMock())
    wizard = open_setup_wizard(window, on_closed=lambda: fired.append(1), ctx=ctx,
                               registry=registry)
    qtbot.waitUntil(lambda: wizard.current_step_id() == "a", timeout=3000)
    assert wizard.isModal() or wizard.windowModality() != Qt.WindowModality.NonModal
    qtbot.keyClick(wizard, Qt.Key.Key_Escape)
    assert fired == [1]
    assert is_setup_completed(cfg) is False


def test_help_menu_opens_the_setup_wizard(qtbot, monkeypatch):
    from lightfall.setup import wizard as wizard_mod
    from lightfall.ui import LFMainWindow

    opened = []
    monkeypatch.setattr(wizard_mod, "open_setup_wizard",
                        lambda parent, **kwargs: opened.append(parent))
    window = LFMainWindow()
    qtbot.addWidget(window)
    help_menu = next(m for m in window.findChildren(QMenu) if m.title() == "&Help")
    texts = [a.text() for a in help_menu.actions()]
    assert texts.index("Setup &wizard…") == texts.index("&Welcome Tutorial") + 1
    next(a for a in help_menu.actions() if a.text() == "Setup &wizard…").trigger()
    assert opened == [window]
