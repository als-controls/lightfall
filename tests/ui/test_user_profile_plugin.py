# tests/ui/test_user_profile_plugin.py
"""UI-side tests for UserProfileSettingsPlugin.

Uses pytest-qt's qtbot fixture and a stubbed Session so the widget can be
constructed without a real auth backend. Key/value operations are tested
against PreferencesManager; the blob (upload/download) path still hits the
mocked UserSettingsClient directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest


@dataclass
class _StubUser:
    username: str = "rpandolfi"
    display_name: str = "Ron Pandolfi"
    email: str = "rp@lbl.gov"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class _StubSession:
    user: _StubUser = field(default_factory=_StubUser)


@pytest.fixture(autouse=True)
def _reset_prefs(monkeypatch):
    """Patch UserSettingsClient.get_instance() and set up a real
    PreferencesManager backed by a mock ConfigManager."""
    from lucid.settings import user_settings_client as usc_mod
    from lucid.ui.preferences.manager import PreferencesManager

    fake_client = MagicMock()
    fake_client.set.return_value = None
    fake_client.delete.return_value = None
    fake_client.get_all.return_value = {}
    monkeypatch.setattr(
        usc_mod.UserSettingsClient,
        "get_instance",
        classmethod(lambda cls: fake_client),
    )

    cm = MagicMock()
    cm._store: dict = {}
    cm.get.side_effect = lambda k, default=None: cm._store.get(k, default)
    cm.set.side_effect = lambda k, v, persist=True: cm._store.__setitem__(k, v)

    PreferencesManager.reset()
    PreferencesManager._instance = PreferencesManager(config_manager=cm)
    yield fake_client
    PreferencesManager.reset()


@pytest.fixture
def stub_session(monkeypatch):
    """Patch SessionManager.get_instance() to return a stub session."""
    from lucid.auth import session as session_mod

    sm = MagicMock()
    sm.session = _StubSession()
    monkeypatch.setattr(
        session_mod.SessionManager, "get_instance", classmethod(lambda cls: sm)
    )
    return sm


def test_plugin_metadata():
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    assert p.name == "user_profile"
    assert p.display_name == "User Profile"
    assert p.category == "general"
    assert p.priority == 1


def test_create_widget_shows_identity_labels(qtbot, stub_session):
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    # Walk all QLabels and assert username/email/display name appear
    from PySide6.QtWidgets import QLabel
    label_text = " ".join(
        lbl.text() for lbl in w.findChildren(QLabel)
    )
    assert "rpandolfi" in label_text
    assert "rp@lbl.gov" in label_text
    assert "Ron Pandolfi" in label_text


def test_orcid_row_hidden_when_absent(qtbot, stub_session):
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    from PySide6.QtWidgets import QLabel
    label_text = " ".join(lbl.text() for lbl in w.findChildren(QLabel))
    assert "ORCID" not in label_text


def test_orcid_row_shown_when_present(qtbot, monkeypatch):
    from lucid.auth import session as session_mod
    user = _StubUser(attributes={"orcid": "0000-0001-2345-6789"})
    sm = MagicMock()
    sm.session = _StubSession(user=user)
    monkeypatch.setattr(
        session_mod.SessionManager, "get_instance", classmethod(lambda cls: sm)
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)

    from PySide6.QtWidgets import QLabel
    label_text = " ".join(lbl.text() for lbl in w.findChildren(QLabel))
    assert "0000-0001-2345-6789" in label_text


def test_load_settings_no_image_keeps_placeholder(qtbot, stub_session):
    """When no profile_image_id is in prefs, load_settings leaves placeholder."""
    # _reset_prefs leaves the cache empty → get("profile_image_id") returns None
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p.load_settings()
    qtbot.wait(100)
    assert p._avatar_label is not None
    assert not p._avatar_label.pixmap().isNull()


def test_load_settings_with_image_fetches_bytes(
    qtbot, stub_session, _reset_prefs
):
    """When an image_id is in prefs cache, the bytes are fetched and rendered."""
    fake_client = _reset_prefs
    image_bytes = _png_bytes_1x1_red()
    fake_client.download_image.return_value = (image_bytes, "image/png")

    from lucid.ui.preferences.manager import PreferencesManager
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    # Seed the cache directly so get() returns the id without HTTP
    prefs = PreferencesManager.get_instance()
    prefs._user_portable._cache["profile_image_id"] = "img-1"

    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p.load_settings()

    qtbot.waitUntil(
        lambda: p._loaded_image_id == "img-1",
        timeout=5000,
    )
    assert not p._avatar_label.pixmap().isNull()


ALLOWED_MIMES = {"image/png", "image/jpeg", "image/gif"}
MAX_BYTES = 20 * 1024 * 1024


def test_choose_image_happy_path(
    qtbot, stub_session, _reset_prefs, tmp_path, monkeypatch
):
    """Selecting a small valid PNG uploads it, writes profile_image_id via
    PreferencesManager, and the subscription re-renders the avatar."""
    fake_client = _reset_prefs
    png_bytes = _png_bytes_1x1_red()
    png_path = tmp_path / "me.png"
    png_path.write_bytes(png_bytes)

    # upload_image returns the new image id; download_image returns the bytes
    fake_client.upload_image.return_value = "new-id"
    fake_client.download_image.return_value = (png_bytes, "image/png")

    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **kw: (str(png_path), "Images (*.png *.jpg *.gif)")),
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p.load_settings()  # subscribe so the prefs change triggers re-render
    p._on_choose_clicked()

    # The full chain: upload → PreferencesManager.set → UserPortableBackend
    # worker → client.set succeeds → changed signal → _on_image_id_changed →
    # download worker → _on_image_ready → _loaded_image_id == "new-id"
    qtbot.waitUntil(lambda: p._loaded_image_id == "new-id", timeout=5000)


def test_choose_image_rejects_too_large(
    qtbot, stub_session, tmp_path, monkeypatch
):
    """Files over 20 MB are rejected client-side with no upload attempted."""
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG" + b"\x00" * (MAX_BYTES + 1))

    from PySide6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **kw: (str(big), "Images (*.png)")),
    )
    shown = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **kw: shown.append(a) or QMessageBox.StandardButton.Ok),
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p._on_choose_clicked()
    assert shown, "Expected QMessageBox.warning to be shown"


def test_choose_image_rejects_unknown_mime(
    qtbot, stub_session, tmp_path, monkeypatch
):
    """Files whose mime can't be determined or isn't allowed → warning, no upload."""
    bad = tmp_path / "thing.bmp"
    bad.write_bytes(b"BM" + b"\x00" * 100)

    from PySide6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **kw: (str(bad), "Images (*.bmp)")),
    )
    shown = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **kw: shown.append(a) or QMessageBox.StandardButton.Ok),
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p._on_choose_clicked()
    assert shown


def test_remove_image_clears_setting(qtbot, stub_session):
    """Clicking Remove routes through PreferencesManager.remove; the
    subscription callback clears _loaded_image_id."""
    from lucid.ui.preferences.manager import PreferencesManager
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    # Pre-seed the cache so the plugin thinks an image is loaded
    prefs = PreferencesManager.get_instance()
    prefs._user_portable._cache["profile_image_id"] = "old"

    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p.load_settings()  # subscribe; _on_image_id_changed("old") → starts fetch
    # Skip waiting for the image fetch; just force the loaded state directly
    p._loaded_image_id = "old"

    p._on_remove_clicked()
    # UserPortableBackend.remove fires a worker → client.delete → on_ok →
    # cache.pop → changed(key, None) → _on_image_id_changed(None) → _loaded_image_id = None
    qtbot.waitUntil(lambda: p._loaded_image_id is None, timeout=5000)


def _png_bytes_1x1_red() -> bytes:
    import struct, zlib

    def chunk(t, d):
        return (
            struct.pack(">I", len(d))
            + t + d
            + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        )

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\x00\x00")
    idat = chunk(b"IDAT", raw)
    iend = chunk(b"IEND", b"")
    return header + ihdr + idat + iend
