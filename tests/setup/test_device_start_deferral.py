"""Device catalog starts after the startup login screen, not before (task: defer devices)."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from lightfall.setup.progress import mark_setup_completed


def test_device_starter_waits_one_event_loop_turn_and_runs_once(qtbot, monkeypatch):
    from lightfall import main as main_mod

    calls = []
    monkeypatch.setattr(main_mod, "_start_devices", lambda: calls.append(1))
    start = main_mod._make_device_starter()

    start()
    start()
    assert calls == []  # deferred: the window shows first
    qtbot.waitUntil(lambda: calls == [1], timeout=2000)
    qtbot.wait(50)
    assert calls == [1]


def test_devices_start_only_after_login_dialog_closes(qtbot, cfg, monkeypatch):
    from lightfall import main as main_mod

    events = []
    monkeypatch.setattr(main_mod, "_start_devices", lambda: events.append("devices"))
    monkeypatch.setattr(main_mod, "_show_startup_login", lambda w: events.append("login"))
    mark_setup_completed(cfg)
    window = QWidget()
    qtbot.addWidget(window)
    start = main_mod._make_device_starter()

    main_mod._startup_login(window, cfg, start)

    assert events == ["login"]
    qtbot.waitUntil(lambda: events == ["login", "devices"], timeout=2000)


def test_start_devices_builds_catalog_and_runs_tunnel_retries(monkeypatch):
    from lightfall import main as main_mod

    calls = []
    monkeypatch.setattr(main_mod, "_setup_devices", lambda: calls.append("setup"))
    app_module = __import__(main_mod.__name__)
    # Set by hand: _start_devices deletes it, which monkeypatch's undo can't handle.
    app_module._ca_tunnel_schedule_retries = lambda: calls.append("retries")
    try:
        main_mod._start_devices()
        assert calls == ["setup", "retries"]
        assert not hasattr(app_module, "_ca_tunnel_schedule_retries")
    finally:
        if hasattr(app_module, "_ca_tunnel_schedule_retries"):
            del app_module._ca_tunnel_schedule_retries
