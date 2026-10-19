"""Tests for Claude-settings hot-reload in ClaudePanel.

Changing Claude settings (model, endpoint, API key, ...) in the Preferences
dialog writes ``claude_*`` preferences but, before this change, nothing rebuilt
the already-running agent -- so the change only took effect on the next
lightfall restart. ClaudePanel now subscribes to those preference keys and
rebuilds the agent when the *effective* config changes: immediately when the
agent is idle, or via the existing reload banner when a query is in flight (so a
running conversation is never torn out from under the user).

Two subtleties drive the design and these tests:

* The Preferences dialog calls ``save_settings()`` on *every* plugin on OK, and
  the preference backend emits ``changed`` unconditionally -- so a change to an
  unrelated setting (e.g. the theme) re-writes the Claude prefs to identical
  values. The reload must be gated on the effective config actually differing,
  not merely on a write happening.
* The in-panel model picker applies the model live (no rebuild) and updates the
  tracked config, so its own write does not trigger a redundant reload.

These tests cover the decision seams without constructing the heavy real agent
(SDK import + connection). ``_setup_ui`` is stubbed so the panel is a bare,
well-formed QWidget; the methods under test are driven directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, QTimer, Signal

from lightfall.ui.panels.claude_panel import ClaudePanel


@pytest.fixture
def panel(qtbot, monkeypatch):
    # Skip the heavy _setup_ui (SDK import, agent construction, auth checks).
    # BasePanel.__init__ still builds self._layout etc., so the panel is valid.
    monkeypatch.setattr(ClaudePanel, "_setup_ui", lambda self: None)
    p = ClaudePanel()
    qtbot.addWidget(p)
    return p


class _FakePrefs:
    def __init__(self) -> None:
        self.subscribed: list[tuple[str, object]] = []
        self.values: dict[str, object] = {}

    def subscribe(self, key, slot):
        self.subscribed.append((key, slot))

    def set(self, key, value):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def fake_prefs(monkeypatch):
    store = _FakePrefs()
    from lightfall.ui.preferences import manager as mgr
    monkeypatch.setattr(
        mgr.PreferencesManager, "get_instance", classmethod(lambda cls: store)
    )
    return store


def _idle_widget(backend_info=None) -> MagicMock:
    w = MagicMock()
    w.agent.is_busy.return_value = False
    w.backend_info = backend_info  # None: the panel falls back to the live pref
    return w


def _busy_widget() -> MagicMock:
    w = MagicMock()
    w.agent.is_busy.return_value = True
    w.backend_info = None
    return w


# A config tuple guaranteed to differ from any real _current_claude_config().
_STALE_CONFIG = ("<stale-sentinel>",)


# --- subscription ------------------------------------------------------------


def test_subscribe_registers_all_watched_keys(panel, fake_prefs):
    panel._subscribe_to_claude_settings()

    keys = {k for k, _ in fake_prefs.subscribed}
    assert keys == set(ClaudePanel._WATCHED_CLAUDE_KEYS)
    # Every subscription targets the panel's change handler.
    assert all(slot == panel._on_claude_pref_changed for _, slot in fake_prefs.subscribed)


def test_watched_keys_cover_model_and_connection(panel):
    # The keys that determine which backend/model the agent connects with must
    # all be watched, or a Preferences change to them would still need a restart.
    for key in (
        "claude_model",
        "claude_effort",
        "claude_endpoint",
        "claude_custom_url",
        "claude_api_key",
    ):
        assert key in ClaudePanel._WATCHED_CLAUDE_KEYS


# --- change handling / debounce ---------------------------------------------


def test_pref_change_schedules_a_reload(panel):
    panel._is_agent_ready = True
    assert panel._settings_reload_pending is False

    panel._on_claude_pref_changed("some-new-value")

    assert panel._settings_reload_pending is True


def test_pref_changes_coalesce_into_one_pending_reload(panel):
    panel._is_agent_ready = True
    panel._on_claude_pref_changed("a")
    panel._on_claude_pref_changed("b")
    # Still a single pending reload; the timer fires once.
    assert panel._settings_reload_pending is True


# --- reload decision: effective-change gate ----------------------------------


def test_no_reload_when_effective_config_unchanged(panel, fake_prefs, monkeypatch):
    # The dominant case: a Preferences OK re-writes Claude prefs to the SAME
    # values (or an unrelated setting changed). The agent must NOT churn.
    panel._is_agent_ready = True
    panel._claude_widget = _idle_widget()
    panel._active_agent_config = panel._current_claude_config()
    panel._settings_reload_pending = True

    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(
        panel, "_show_settings_reload_banner", lambda: calls.append("banner")
    )

    panel._do_claude_settings_reload()

    assert calls == []
    assert panel._settings_reload_pending is False


def test_do_reload_rebuilds_agent_when_idle(panel, fake_prefs, monkeypatch):
    panel._is_agent_ready = True
    panel._claude_widget = _idle_widget()
    panel._active_agent_config = _STALE_CONFIG  # differs from current -> reload
    panel._settings_reload_pending = True

    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(
        panel, "_show_settings_reload_banner", lambda: calls.append("banner")
    )

    panel._do_claude_settings_reload()

    assert calls == ["reload"]
    assert panel._settings_reload_pending is False


def test_do_reload_shows_banner_when_busy(panel, fake_prefs, monkeypatch):
    panel._is_agent_ready = True
    panel._claude_widget = _busy_widget()
    panel._active_agent_config = _STALE_CONFIG  # differs from current
    panel._settings_reload_pending = True

    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(
        panel, "_show_settings_reload_banner", lambda: calls.append("banner")
    )

    panel._do_claude_settings_reload()

    assert calls == ["banner"]


def test_do_reload_noop_while_still_loading(panel, monkeypatch):
    # Settings changed while plugins are still loading (no prior build, so no
    # error): the agent isn't built yet, construction will read fresh prefs --
    # nothing to reload.
    panel._is_agent_ready = False
    panel._error_message = None  # never attempted a build yet
    panel._claude_widget = None
    panel._settings_reload_pending = True

    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(
        panel, "_show_settings_reload_banner", lambda: calls.append("banner")
    )

    panel._do_claude_settings_reload()

    assert calls == []


def test_failed_build_recovers_on_settings_change(panel, monkeypatch):
    # The headline case: the agent failed to build (e.g. no/invalid API key),
    # so it's in the error state (_error_message set, not ready). The user then
    # fixes the setting -> the change must re-attempt the build, NOT bail on the
    # not-ready guard (which would require the restart this feature eliminates).
    panel._is_agent_ready = False
    panel._error_message = "Claude authentication not configured."
    panel._claude_widget = None
    panel._settings_reload_pending = True

    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(
        panel, "_show_settings_reload_banner", lambda: calls.append("banner")
    )

    panel._do_claude_settings_reload()

    assert calls == ["reload"]


def test_reload_agent_disposes_settings_banner(panel, monkeypatch):
    # A non-button reload path (effort change, session restore, recovery) must
    # not leave the settings banner orphaned in the layout.
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(panel, "_initialize_claude_widget", lambda: None)
    monkeypatch.setattr(panel, "_reset_cockpit", lambda: None)

    banner = QLabel()
    panel._layout.insertWidget(0, banner)
    panel._reload_banner = banner
    assert panel._layout.indexOf(banner) != -1

    panel._reload_agent()

    assert panel._reload_banner is None
    assert panel._layout.indexOf(banner) == -1  # actually removed, not just deref'd


def test_disable_betas_is_watched(panel):
    # claude_disable_betas is now wired into the agent (it sets
    # CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC), so toggling it must rebuild the
    # agent for the change to take effect.
    assert "claude_disable_betas" in ClaudePanel._WATCHED_CLAUDE_KEYS


# --- picker live-switch updates tracked config so it doesn't double-reload ----


def test_pick_model_live_switch_updates_active_config(panel, fake_prefs, monkeypatch):
    panel._is_agent_ready = True
    panel._claude_widget = _idle_widget()

    panel._on_pick_model("claude-opus")

    # Pref persisted and live-switch invoked with the CLI-resolved alias.
    assert fake_prefs.values["claude_model"] == "claude-opus"
    panel._claude_widget.agent.set_model.assert_called_once_with("opus")

    # The tracked config now reflects the live switch, so a subsequent reload
    # decision is a no-op (the live conversation is preserved).
    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(
        panel, "_show_settings_reload_banner", lambda: calls.append("banner")
    )
    panel._settings_reload_pending = True
    panel._do_claude_settings_reload()

    assert calls == []


class _FakeAgent(QObject):
    query_completed = Signal()
    query_cancelled = Signal()
    error_occurred = Signal(str)

    def __init__(self, busy: bool):
        super().__init__()
        self.busy = busy

    def is_busy(self):
        return self.busy


class _FakeTab:
    def __init__(self, busy: bool):
        self.claude_widget = MagicMock()
        self.claude_widget.agent = _FakeAgent(busy)
        self.reloads = 0

    def reload_agent(self):
        self.reloads += 1

    def close_session(self, **_kwargs):  # panel teardown closes every tab
        pass


def test_backend_prefs_are_watched(panel):
    for key in ("agent_backend", "agy_path", "agy_gcp_project", "agy_gcp_location", "agy_model"):
        assert key in ClaudePanel._WATCHED_CLAUDE_KEYS


def test_backend_switch_changes_config(panel, fake_prefs):
    before = panel._current_claude_config()
    fake_prefs.set("agent_backend", "agy")
    assert panel._current_claude_config() != before
    fake_prefs.set("agy_model", "gemini-3-pro")
    assert panel._current_claude_config()[-2] == "gemini-3-pro"  # (..., agy_model, agy_effort)


def test_settings_change_reloads_idle_extra_tabs(panel, fake_prefs, monkeypatch):
    panel._is_agent_ready = True
    panel._claude_widget = _idle_widget()
    panel._active_agent_config = _STALE_CONFIG
    extra = _FakeTab(busy=False)
    monkeypatch.setattr(panel, "_session_tabs", lambda: [extra])
    monkeypatch.setattr(panel, "_reload_agent", lambda: None)

    panel._do_claude_settings_reload()

    assert extra.reloads == 1


def test_busy_extra_tab_reloads_after_completion(panel, fake_prefs, monkeypatch, qtbot):
    panel._is_agent_ready = True
    panel._claude_widget = _idle_widget()
    panel._active_agent_config = _STALE_CONFIG
    extra = _FakeTab(busy=True)
    monkeypatch.setattr(panel, "_session_tabs", lambda: [extra])
    monkeypatch.setattr(panel, "_reload_agent", lambda: None)

    panel._do_claude_settings_reload()
    assert extra.reloads == 0  # mid-reply: not torn down

    # Real ordering: the signal fires while is_busy() is still True.
    agent = extra.claude_widget.agent
    agent.query_completed.emit()
    QTimer.singleShot(300, lambda: setattr(agent, "busy", False))
    qtbot.wait(100)
    assert extra.reloads == 0
    qtbot.waitUntil(lambda: extra.reloads == 1, timeout=2000)
    extra.claude_widget.agent.query_completed.emit()  # a later turn: no second reload
    qtbot.wait(50)
    assert extra.reloads == 1


def test_busy_main_tab_banner_then_auto_reload(panel, fake_prefs, monkeypatch, qtbot):
    panel._is_agent_ready = True
    agent = _FakeAgent(busy=True)
    panel._claude_widget = MagicMock()
    panel._claude_widget.agent = agent
    panel._active_agent_config = _STALE_CONFIG
    monkeypatch.setattr(panel, "_session_tabs", lambda: [])
    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(panel, "_show_settings_reload_banner", lambda: calls.append("banner"))

    panel._do_claude_settings_reload()
    assert calls == ["banner"]

    agent.query_cancelled.emit()
    QTimer.singleShot(300, lambda: setattr(agent, "busy", False))
    qtbot.waitUntil(lambda: calls == ["banner", "reload"], timeout=2000)


def _busy_extra(panel, monkeypatch):
    panel._is_agent_ready = True
    panel._claude_widget = _idle_widget()
    panel._active_agent_config = _STALE_CONFIG
    extra = _FakeTab(busy=True)
    monkeypatch.setattr(panel, "_session_tabs", lambda: [extra])
    monkeypatch.setattr(panel, "_reload_agent", lambda: None)
    monkeypatch.setattr(ClaudePanel, "_DEFERRED_POLL_MS", 10)
    return extra


def test_busy_past_many_polls_never_force_reloads(panel, fake_prefs, monkeypatch, qtbot):
    extra = _busy_extra(panel, monkeypatch)
    panel._do_claude_settings_reload()
    agent = extra.claude_widget.agent
    agent.query_completed.emit()  # still busy: post-turn work / next turn
    qtbot.wait(150)  # many 10 ms poll intervals
    assert extra.reloads == 0
    agent.busy = False
    qtbot.waitUntil(lambda: extra.reloads == 1, timeout=2000)
    qtbot.wait(60)
    assert extra.reloads == 1


def test_new_turn_before_poll_keeps_reload_waiting(panel, fake_prefs, monkeypatch, qtbot):
    extra = _busy_extra(panel, monkeypatch)
    panel._do_claude_settings_reload()
    agent = extra.claude_widget.agent
    agent.query_completed.emit()  # turn 1 ends...
    # ...and turn 2 begins before any poll sees idle: busy simply stays True.
    qtbot.wait(80)
    assert extra.reloads == 0
    agent.query_completed.emit()  # turn 2 ends, agent goes idle
    agent.busy = False
    qtbot.waitUntil(lambda: extra.reloads == 1, timeout=2000)
    qtbot.wait(60)
    assert extra.reloads == 1


def test_closed_tab_not_reloaded_after_signal(panel, fake_prefs, monkeypatch, qtbot):
    extra = _busy_extra(panel, monkeypatch)
    panel._do_claude_settings_reload()
    agent = extra.claude_widget.agent
    agent.query_completed.emit()
    panel._deferred_reload.discard(extra)  # tab closed between signal and timer
    agent.busy = False
    qtbot.wait(150)
    assert extra.reloads == 0


def test_error_occurred_ends_deferral(panel, fake_prefs, monkeypatch, qtbot):
    extra = _busy_extra(panel, monkeypatch)
    panel._do_claude_settings_reload()
    agent = extra.claude_widget.agent
    agent.error_occurred.emit("boom")
    agent.busy = False
    qtbot.waitUntil(lambda: extra.reloads == 1, timeout=2000)


def test_pick_model_on_agy_writes_agy_model_and_switches(panel, fake_prefs, monkeypatch):
    fake_prefs.set("agent_backend", "agy")
    panel._is_agent_ready = True
    panel._claude_widget = _idle_widget()

    panel._on_pick_model("gemini-3-pro")

    assert fake_prefs.values["agy_model"] == "gemini-3-pro"
    assert "claude_model" not in fake_prefs.values
    panel._claude_widget.agent.set_model.assert_called_once_with("gemini-3-pro")
    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(panel, "_session_tabs", lambda: [])
    panel._settings_reload_pending = True
    panel._do_claude_settings_reload()
    assert calls == []  # the picker's own write must not rebuild the agent


def test_pick_default_on_agy_sends_none(panel, fake_prefs):
    fake_prefs.set("agent_backend", "agy")
    panel._claude_widget = _idle_widget()
    panel._on_pick_model("")
    panel._claude_widget.agent.set_model.assert_called_once_with(None)


# --- effort picker: live on agy, dialog + rebuild on Claude --------------------


def test_agy_effort_is_watched_and_in_config(panel, fake_prefs):
    assert "agy_effort" in ClaudePanel._WATCHED_CLAUDE_KEYS
    before = panel._current_claude_config()
    fake_prefs.set("agy_effort", "high")
    assert panel._current_claude_config() != before


def test_pick_effort_on_agy_applies_live_without_dialog(panel, fake_prefs, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: pytest.fail("no dialog on agy"))
    fake_prefs.set("agent_backend", "agy")
    panel._is_agent_ready = True
    panel._claude_widget = _idle_widget()
    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(panel, "_session_tabs", lambda: [])

    panel._on_pick_effort("high")

    assert fake_prefs.values["agy_effort"] == "high"
    assert "claude_effort" not in fake_prefs.values
    panel._claude_widget.agent.set_effort.assert_called_once_with("high")
    panel._settings_reload_pending = True
    panel._do_claude_settings_reload()
    assert calls == []  # neither the pick nor the debounced subscription rebuilds


def test_pick_default_effort_on_agy_sends_none(panel, fake_prefs, monkeypatch):
    fake_prefs.set("agent_backend", "agy")
    fake_prefs.set("agy_effort", "high")
    panel._claude_widget = _idle_widget()
    panel._on_pick_effort("")
    assert fake_prefs.values["agy_effort"] == ""
    panel._claude_widget.agent.set_effort.assert_called_once_with(None)


@pytest.mark.parametrize("answer,expect_reload", [(True, True), (False, False)])
def test_pick_effort_on_claude_asks_and_rebuilds(panel, fake_prefs, monkeypatch,
                                                 answer, expect_reload):
    from PySide6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or (
        QMessageBox.StandardButton.Yes if answer else QMessageBox.StandardButton.No))
    panel._claude_widget = _idle_widget()
    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))

    panel._on_pick_effort("low")

    assert len(asked) == 1
    assert calls == (["reload"] if expect_reload else [])
    assert fake_prefs.values.get("claude_effort") == ("low" if expect_reload else None)
    panel._claude_widget.agent.set_effort.assert_not_called()


# --- pending reload: picks and toasts follow the live agent's backend ---------


def test_pick_model_while_reload_pending_targets_the_live_backend(panel, fake_prefs):
    from lightfall.agents.backends.info import ClaudeCodeInfo

    fake_prefs.set("agent_backend", "agy")  # saved mid-reply; Claude agent still live
    panel._claude_widget = _idle_widget(ClaudeCodeInfo())
    panel._on_pick_model("claude-opus")
    assert fake_prefs.values["claude_model"] == "claude-opus"
    assert "agy_model" not in fake_prefs.values
    panel._claude_widget.agent.set_model.assert_called_once_with("opus")


def test_pick_effort_while_reload_pending_targets_the_live_backend(panel, fake_prefs,
                                                                   monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from lightfall.agents.backends.info import AgyInfo

    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: pytest.fail("live agy agent: no dialog"))
    fake_prefs.set("agent_backend", "claude-code")  # saved mid-reply; agy agent still live
    panel._claude_widget = _idle_widget(AgyInfo())
    panel._on_pick_effort("high")
    assert fake_prefs.values["agy_effort"] == "high"
    assert "claude_effort" not in fake_prefs.values
    panel._claude_widget.agent.set_effort.assert_called_once_with("high")


def test_approval_toast_names_the_live_backend(panel, fake_prefs, monkeypatch, qtbot):
    from PySide6.QtWidgets import QWidget

    from lightfall.agents.backends.info import AgyInfo
    from lightfall.ui.panels import claude_panel as cp

    shown = []

    class _Toasts:
        def warning(self, title, text, duration=0):
            shown.append(text)
            toast = QWidget()
            qtbot.addWidget(toast)
            return toast

    monkeypatch.setattr(cp.ToastManager, "get_instance", classmethod(lambda cls: _Toasts()))
    fake_prefs.set("agent_backend", "claude-code")
    panel._claude_widget = _idle_widget(AgyInfo())
    panel._on_approval_needed("r1", "mcp__bus__send_message", {})
    assert shown == ["Gemini wants to use: Bus  Send Message"]


def test_cockpit_cost_follows_the_built_widget(panel, fake_prefs):
    from lightfall.agents.backends.info import AgyInfo, ClaudeCodeInfo

    fake_prefs.set("agent_backend", "claude-code")
    panel._wire_main_widget(_idle_widget(AgyInfo()))
    assert panel._cockpit.show_cost is False
    panel._wire_main_widget(_idle_widget(ClaudeCodeInfo()))
    assert panel._cockpit.show_cost is True


# --- deferral bookkeeping -------------------------------------------------------


def _receivers(agent) -> int:
    from PySide6.QtCore import SIGNAL

    return sum(agent.receivers(SIGNAL(sig)) for sig in
               ("query_completed()", "query_cancelled()", "error_occurred(QString)"))


def test_end_signal_handler_disconnects_once_fired(panel, fake_prefs, monkeypatch, qtbot):
    extra = _busy_extra(panel, monkeypatch)
    agent = extra.claude_widget.agent
    before = _receivers(agent)
    panel._do_claude_settings_reload()
    assert _receivers(agent) == before + 3
    agent.query_completed.emit()
    assert _receivers(agent) == before  # one-shot: nothing accumulates per deferral
    agent.busy = False
    qtbot.waitUntil(lambda: extra.reloads == 1, timeout=2000)


def test_tab_gone_without_close_request_stops_polling(panel, fake_prefs, monkeypatch, qtbot):
    extra = _busy_extra(panel, monkeypatch)
    panel._do_claude_settings_reload()
    agent = extra.claude_widget.agent
    agent.query_completed.emit()
    assert extra in panel._deferred_polling
    monkeypatch.setattr(panel, "_session_tabs", lambda: [])  # removed some other way
    qtbot.waitUntil(lambda: extra not in panel._deferred_polling, timeout=2000)
    assert extra not in panel._deferred_reload
    agent.busy = False
    qtbot.wait(100)
    assert extra.reloads == 0


def test_end_signal_for_a_gone_tab_does_not_poll(panel, fake_prefs, monkeypatch, qtbot):
    extra = _busy_extra(panel, monkeypatch)
    panel._do_claude_settings_reload()
    monkeypatch.setattr(panel, "_session_tabs", lambda: [])
    extra.claude_widget.agent.busy = False
    extra.claude_widget.agent.query_completed.emit()
    qtbot.wait(100)
    assert extra.reloads == 0
    assert extra not in panel._deferred_reload and extra not in panel._deferred_polling


def test_busy_idle_busy_flip_before_a_poll_keeps_waiting(panel, fake_prefs, monkeypatch, qtbot):
    extra = _busy_extra(panel, monkeypatch)
    panel._do_claude_settings_reload()
    agent = extra.claude_widget.agent
    agent.query_completed.emit()  # turn 1 ends; the first poll still sees busy
    agent.busy = False            # idle for a moment...
    agent.busy = True             # ...then turn 2 starts before the next poll
    qtbot.wait(80)                # several 10 ms polls, all busy
    assert extra.reloads == 0
    agent.busy = False
    qtbot.waitUntil(lambda: extra.reloads == 1, timeout=2000)


def test_banner_reload_clears_the_main_deferral(panel, fake_prefs, monkeypatch, qtbot):
    monkeypatch.setattr(ClaudePanel, "_DEFERRED_POLL_MS", 10)
    builds = []
    monkeypatch.setattr(panel, "_initialize_claude_widget", lambda: builds.append(1))
    monkeypatch.setattr(panel, "_reset_cockpit", lambda: None)
    monkeypatch.setattr(panel, "_session_tabs", lambda: [])
    panel._is_agent_ready = True
    agent = _FakeAgent(busy=True)
    panel._claude_widget = MagicMock()
    panel._claude_widget.agent = agent
    panel._active_agent_config = _STALE_CONFIG

    panel._do_claude_settings_reload()
    assert panel._reload_banner is not None and None in panel._deferred_reload
    panel._reload_agent()  # the banner's Reload button
    assert builds == [1]
    assert None not in panel._deferred_reload and panel._reload_banner is None
    agent.busy = False
    agent.query_completed.emit()  # the old agent's reply ends after the manual reload
    qtbot.wait(80)
    assert builds == [1]  # no second rebuild


def test_main_and_extra_both_busy_reload_independently(panel, fake_prefs, monkeypatch, qtbot):
    monkeypatch.setattr(ClaudePanel, "_DEFERRED_POLL_MS", 10)
    main_reloads = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: main_reloads.append(1))
    monkeypatch.setattr(panel, "_show_settings_reload_banner", lambda: None)
    panel._is_agent_ready = True
    main_agent = _FakeAgent(busy=True)
    panel._claude_widget = MagicMock()
    panel._claude_widget.agent = main_agent
    panel._active_agent_config = _STALE_CONFIG
    extra = _FakeTab(busy=True)
    monkeypatch.setattr(panel, "_session_tabs", lambda: [extra])

    panel._do_claude_settings_reload()
    assert main_reloads == [] and extra.reloads == 0

    main_agent.busy = False
    main_agent.query_completed.emit()
    qtbot.waitUntil(lambda: main_reloads == [1], timeout=2000)
    assert extra.reloads == 0  # the extra tab is still mid-reply

    extra.claude_widget.agent.busy = False
    extra.claude_widget.agent.query_completed.emit()
    qtbot.waitUntil(lambda: extra.reloads == 1, timeout=2000)
    assert main_reloads == [1]
