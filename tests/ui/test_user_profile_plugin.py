# tests/ui/test_user_profile_plugin.py
"""UI-side tests for UserProfileSettingsPlugin.

Uses pytest-qt's qtbot fixture and a stubbed Session so the widget can be
constructed without a real auth backend. UserSettingsClient calls are
intercepted via the singleton's reset/init pattern + httpx_mock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

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
def _reset_settings_client():
    from lucid.settings.user_settings_client import UserSettingsClient
    UserSettingsClient.reset()
    UserSettingsClient.init(base_url="https://lb.test")
    yield
    UserSettingsClient.reset()


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
    text = w.findChildren(type(w))  # silence unused-import
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


def test_load_settings_no_image_keeps_placeholder(qtbot, stub_session, httpx_mock):
    """When no profile_image_id is set, load_settings leaves placeholder."""
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        status_code=404,
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p.load_settings()
    # Spin the event loop briefly to let the worker thread finish (or be
    # absent if no image_id).
    qtbot.wait(100)
    # Placeholder pixmap exists, label is not empty
    assert p._avatar_label is not None
    assert not p._avatar_label.pixmap().isNull()


def test_load_settings_with_image_fetches_bytes(
    qtbot, stub_session, httpx_mock, monkeypatch
):
    """When an image_id is set, the bytes are fetched and rendered."""
    image_bytes = _png_bytes_1x1_red()
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        json={
            "user_id": "rpandolfi",
            "beamline": "",
            "key": "profile_image_id",
            "value": "img-1",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    httpx_mock.add_response(
        url="https://lb.test/logbook/images/img-1",
        content=image_bytes,
        headers={"content-type": "image/png"},
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p.load_settings()

    # Wait for the worker thread to deliver the QImage to the GUI
    qtbot.waitUntil(
        lambda: p._loaded_image_id == "img-1",
        timeout=5000,
    )
    assert not p._avatar_label.pixmap().isNull()


ALLOWED_MIMES = {"image/png", "image/jpeg", "image/gif"}
MAX_BYTES = 20 * 1024 * 1024


def test_choose_image_happy_path(
    qtbot, stub_session, httpx_mock, tmp_path, monkeypatch
):
    """Selecting a small valid PNG uploads it and writes profile_image_id."""
    png_path = tmp_path / "me.png"
    png_path.write_bytes(_png_bytes_1x1_red())

    # Patch QFileDialog to return the prepared path
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **kw: (str(png_path), "Images (*.png *.jpg *.gif)")),
    )

    httpx_mock.add_response(
        method="POST",
        url="https://lb.test/logbook/images",
        json={"image_id": "new-id", "mime_type": "image/png", "size_bytes": len(_png_bytes_1x1_red())},
        status_code=201,
    )
    httpx_mock.add_response(
        method="PUT",
        url="https://lb.test/logbook/settings/profile_image_id",
        match_json={"value": "new-id", "beamline": ""},
        json={
            "user_id": "rpandolfi",
            "beamline": "",
            "key": "profile_image_id",
            "value": "new-id",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    # load_settings() after upload will GET profile_image_id then GET the image bytes
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        json={
            "user_id": "rpandolfi",
            "beamline": "",
            "key": "profile_image_id",
            "value": "new-id",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    httpx_mock.add_response(
        url="https://lb.test/logbook/images/new-id",
        content=_png_bytes_1x1_red(),
        headers={"content-type": "image/png"},
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p._on_choose_clicked()
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


def test_remove_image_clears_setting(qtbot, stub_session, httpx_mock):
    httpx_mock.add_response(
        method="DELETE",
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        status_code=204,
    )
    # After delete, load_settings does a GET that 404s → placeholder
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        status_code=404,
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    # Pretend an image was loaded
    p._loaded_image_id = "old"
    p._on_remove_clicked()
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
