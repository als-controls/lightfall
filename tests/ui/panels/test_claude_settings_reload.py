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


def _idle_widget() -> MagicMock:
    w = MagicMock()
    w.agent.is_busy.return_value = False
    return w


def _busy_widget() -> MagicMock:
    w = MagicMock()
    w.agent.is_busy.return_value = True
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


def test_do_reload_noop_when_agent_not_ready(panel, monkeypatch):
    # Settings changed while plugins still loading: the agent isn't built yet,
    # so construction will read the fresh prefs -- nothing to reload.
    panel._is_agent_ready = False
    panel._claude_widget = None
    panel._settings_reload_pending = True

    calls = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: calls.append("reload"))
    monkeypatch.setattr(
        panel, "_show_settings_reload_banner", lambda: calls.append("banner")
    )

    panel._do_claude_settings_reload()

    assert calls == []


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
