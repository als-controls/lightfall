"""Tests for ProfileAvatarWidget."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    """ProfileAvatarWidget is a QWidget — need a full QApplication."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _reset_prefs(qapp, monkeypatch):
    from unittest.mock import MagicMock
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
    # Construct via the singleton's __init__ so get_instance() returns this one.
    PreferencesManager._instance = PreferencesManager(config_manager=cm)
    yield
    PreferencesManager.reset()


def test_initial_render_is_placeholder(qapp):
    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget

    w = ProfileAvatarWidget()
    assert w._loaded_image_id is None
    assert w.minimumSize().width() > 0


def test_subscribe_with_new_id_triggers_fetch(qapp):
    import time
    from lucid.ui.widgets import profile_avatar as pa_mod
    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget
    from lucid.ui.preferences.manager import PreferencesManager

    fake_qimage = QImage(16, 16, QImage.Format.Format_ARGB32)
    fake_qimage.fill(Qt.GlobalColor.red)

    fetched_ids: list[str] = []

    def fake_fetch(client, image_id):
        fetched_ids.append(image_id)
        return fake_qimage

    with patch.object(pa_mod, "_fetch_qimage", side_effect=fake_fetch):
        w = ProfileAvatarWidget()
        prefs = PreferencesManager.get_instance()
        # Simulate a backend-driven update by routing through the topic.
        prefs._user_portable._cache["profile_image_id"] = "img-1"
        prefs._on_backend_changed("profile_image_id", "img-1")

        deadline = time.monotonic() + 2.0
        while not fetched_ids and time.monotonic() < deadline:
            QCoreApplication.processEvents()

        assert fetched_ids == ["img-1"]


def test_subscribe_same_id_does_not_refetch(qapp):
    import time
    from lucid.ui.widgets import profile_avatar as pa_mod
    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget
    from lucid.ui.preferences.manager import PreferencesManager

    fake_qimage = QImage(16, 16, QImage.Format.Format_ARGB32)
    fake_qimage.fill(Qt.GlobalColor.red)

    fetched_ids: list[str] = []

    def fake_fetch(client, image_id):
        fetched_ids.append(image_id)
        return fake_qimage

    with patch.object(pa_mod, "_fetch_qimage", side_effect=fake_fetch):
        w = ProfileAvatarWidget()
        prefs = PreferencesManager.get_instance()

        prefs._on_backend_changed("profile_image_id", "img-1")
        deadline = time.monotonic() + 2.0
        while not fetched_ids and time.monotonic() < deadline:
            QCoreApplication.processEvents()

        prefs._on_backend_changed("profile_image_id", "img-1")
        for _ in range(20):
            QCoreApplication.processEvents()

        assert fetched_ids == ["img-1"]


def test_subscribe_none_reverts_to_placeholder(qapp):
    import time
    from lucid.ui.widgets import profile_avatar as pa_mod
    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget
    from lucid.ui.preferences.manager import PreferencesManager

    fake_qimage = QImage(16, 16, QImage.Format.Format_ARGB32)
    fake_qimage.fill(Qt.GlobalColor.red)

    with patch.object(pa_mod, "_fetch_qimage", return_value=fake_qimage):
        w = ProfileAvatarWidget()
        prefs = PreferencesManager.get_instance()

        prefs._on_backend_changed("profile_image_id", "img-1")
        deadline = time.monotonic() + 2.0
        while w._loaded_image_id is None and time.monotonic() < deadline:
            QCoreApplication.processEvents()
        assert w._loaded_image_id == "img-1"

        prefs._on_backend_changed("profile_image_id", None)
        QCoreApplication.processEvents()
        assert w._loaded_image_id is None


def test_mouse_press_emits_clicked(qapp):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QMouseEvent

    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget

    w = ProfileAvatarWidget()
    received: list = []
    w.clicked.connect(lambda: received.append(True))

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    w.mousePressEvent(event)
    assert received == [True]


def test_right_click_does_not_emit_clicked(qapp):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget

    w = ProfileAvatarWidget()
    received: list = []
    w.clicked.connect(lambda: received.append(True))

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    w.mousePressEvent(event)
    assert received == []
