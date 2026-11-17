"""Shortcut step, page and the shortcuts helpers. Never touches the real desktop."""
from __future__ import annotations

import sys

import pytest

from lightfall.setup import shortcuts
from lightfall.setup.shortcuts import Launcher, Location, ShortcutError
from lightfall.setup.step import StepStatus
from lightfall.setup.steps import shortcut as step_mod
from lightfall.setup.steps.shortcut import DECLINED_KEY, ShortcutStep


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Shortcuts land in tmp dirs; ``created`` records what was written."""
    target = tmp_path / "Scripts" / "lightfall-gui.exe"
    launch = Launcher(target=target, working_dir=tmp_path, icon=tmp_path / "icon.ico")
    paths = {Location.DESKTOP: tmp_path / "Desktop" / "Lightfall.lnk",
             Location.MENU: tmp_path / "Programs" / "Lightfall.lnk"}
    created: list[Location] = []

    def fake_create(location, _launch):
        path = paths[location]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("lnk")
        created.append(location)
        return path

    monkeypatch.setattr(shortcuts, "launcher", lambda: launch)
    monkeypatch.setattr(shortcuts, "shortcut_path", lambda location: paths[location])
    monkeypatch.setattr(shortcuts, "create_shortcut", fake_create)
    return paths, created


def _page(qtbot, ctx, step):
    page = step.widget(ctx)
    qtbot.addWidget(page)
    return page


# --- detect --------------------------------------------------------------------------

def test_detect_needs_input_without_a_desktop_shortcut(ctx, fake_env):
    state = ShortcutStep().detect(ctx)
    assert state.status is StepStatus.NEEDS_INPUT


def test_detect_satisfied_by_an_existing_desktop_shortcut(ctx, fake_env):
    paths, _ = fake_env
    paths[Location.DESKTOP].parent.mkdir(parents=True)
    paths[Location.DESKTOP].write_text("made by hand")
    state = ShortcutStep().detect(ctx)
    assert state.satisfied and state.value == "On desktop"


def test_detect_satisfied_when_declined_before(ctx, fake_env):
    ctx.config.set(DECLINED_KEY, True, persist=True)
    assert ShortcutStep().detect(ctx).satisfied


def test_detect_satisfied_when_there_is_nothing_to_point_at(ctx, monkeypatch):
    monkeypatch.setattr(shortcuts, "launcher", lambda: None)
    assert ShortcutStep().detect(ctx).value == "Not available"


# --- page and apply ------------------------------------------------------------------

def test_page_defaults_to_desktop_checked(qtbot, ctx, fake_env):
    page = _page(qtbot, ctx, ShortcutStep())
    assert page.desktop_box.isChecked()
    assert page.chosen_locations()[0] is Location.DESKTOP
    assert page.primary_label() == "Create shortcut"


def test_apply_creates_chosen_shortcuts(qtbot, ctx, fake_env):
    _, created = fake_env
    step = ShortcutStep()
    page = _page(qtbot, ctx, step)
    page.menu_box.setChecked(False)
    step.apply(ctx)
    assert created == [Location.DESKTOP]
    assert page.summary() == "On desktop"
    assert ctx.config.get(DECLINED_KEY) is False


def test_apply_with_nothing_chosen_records_the_choice(qtbot, ctx, fake_env):
    _, created = fake_env
    step = ShortcutStep()
    page = _page(qtbot, ctx, step)
    page.desktop_box.setChecked(False)
    page.menu_box.setChecked(False)
    assert page.primary_label() == "Continue"
    step.apply(ctx)
    assert created == []
    assert page.summary() == "No shortcut"
    assert ctx.config.get(DECLINED_KEY) is True
    assert step.detect(ctx).satisfied  # a Help re-run doesn't ask again


def test_apply_failure_warns_and_does_not_raise(qtbot, ctx, fake_env, monkeypatch):
    def boom(_location, _launch):
        raise ShortcutError("access denied")

    warnings = []
    monkeypatch.setattr(shortcuts, "create_shortcut", boom)
    monkeypatch.setattr(step_mod.logger, "warning", lambda *a, **k: warnings.append(a))
    step = ShortcutStep()
    page = _page(qtbot, ctx, step)
    step.apply(ctx)  # must not raise: the wizard moves on
    assert page.summary() == "Couldn't create shortcut"
    assert len(warnings) == 2


def test_page_notes_an_existing_shortcut_will_be_replaced(qtbot, ctx, fake_env):
    paths, _ = fake_env
    paths[Location.DESKTOP].parent.mkdir(parents=True)
    paths[Location.DESKTOP].write_text("made by hand")
    page = _page(qtbot, ctx, ShortcutStep())
    page.on_enter()
    assert "replaced" in page.note.text()


# --- helpers -------------------------------------------------------------------------

def test_icon_is_a_packaged_resource():
    assert shortcuts.icon_path().is_file()


def test_desktop_entry_points_at_the_gui_script(tmp_path):
    launch = Launcher(tmp_path / "lightfall-gui", tmp_path, tmp_path / "icon.png")
    entry = shortcuts.desktop_entry(launch)
    assert f'Exec="{launch.target}"' in entry
    assert "Terminal=false" in entry


@pytest.mark.skipif(sys.platform != "win32", reason="Windows .lnk via WScript.Shell")
def test_windows_lnk_is_written_and_overwritten_in_place(tmp_path, monkeypatch):
    win32com = pytest.importorskip("win32com.client")
    link = tmp_path / "Lightfall.lnk"
    monkeypatch.setattr(shortcuts, "shortcut_path", lambda _location: link)
    target = tmp_path / "lightfall-gui.exe"
    target.write_text("")
    launch = Launcher(target, tmp_path, shortcuts.icon_path())
    shortcuts.create_shortcut(Location.DESKTOP, launch)
    shortcuts.create_shortcut(Location.DESKTOP, launch)  # again: replaces, no duplicate
    assert [p.name for p in tmp_path.glob("*.lnk")] == ["Lightfall.lnk"]
    saved = win32com.Dispatch("WScript.Shell").CreateShortcut(str(link))
    assert saved.TargetPath.lower() == str(target).lower()
    assert saved.IconLocation.lower().startswith(str(launch.icon).lower())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders")
def test_windows_desktop_comes_from_the_known_folder():
    assert shortcuts.shortcut_path(Location.DESKTOP).parent.is_dir()
