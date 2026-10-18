"""Facility and Network pages (pytest-qt)."""
from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QPushButton, QSpinBox, QWidget

from lightfall.setup.bootstrap import FROM_SETTINGS, Bootstrap, BootstrapError, NetworkError
from lightfall.setup.facilities import load_facilities
from lightfall.setup.page import Tile
from lightfall.setup.steps import network as net

BOOT = Bootstrap("http://bcglucidlogbook.dhcp.lbl.gov:8000", {
    "schema": 1, "facility": {"id": "als", "name": "ALS"},
    "auth": {"provider": "keycloak"}, "services": {"tiled_url": "http://tiled.example"},
})


@pytest.fixture
def als(ctx):
    ctx.facility = next(f for f in load_facilities() if f.id == "als")
    return ctx


def _facility_page(qtbot, ctx):
    from lightfall.setup.pages.facility import FacilityPage

    page = FacilityPage(ctx, load_facilities())
    qtbot.addWidget(page)
    page.show()
    return page


def _network_page(qtbot, ctx):
    from lightfall.setup.pages.network import NetworkPage

    page = NetworkPage(ctx)
    qtbot.addWidget(page)
    page.show()
    return page


def test_single_packaged_facility_is_preselected(qtbot, ctx):
    page = _facility_page(qtbot, ctx)
    assert page.findChild(Tile, "facilityTile-als").is_selected()
    assert ctx.facility.id == "als"
    assert page.heading()[0] == "Where are you working?"
    assert page.summary() == "Advanced Light Source"


def test_other_facility_url_is_normalized_or_rejected(qtbot, ctx):
    page = _facility_page(qtbot, ctx)
    done = []
    page.completed.connect(lambda: done.append(1))
    qtbot.mouseClick(page.findChild(Tile, "facilityTile-other"), Qt.MouseButton.LeftButton)
    url = page.findChild(QLineEdit, "facilityUrl")
    assert url.isVisible() and ctx.facility is None
    url.setText("ftp://nope")
    page.on_primary()
    assert done == [] and page.note.kind() == "error" and ctx.facility is None
    url.setText(" lightfall.example.org/health/ ")
    page.on_primary()
    assert done == [1]
    assert ctx.facility.server_url == "https://lightfall.example.org"


def test_network_success_shows_what_loaded(qtbot, als, monkeypatch):
    calls = []
    monkeypatch.setattr(net, "connect",
                        lambda server, proxy: calls.append((server, proxy)) or BOOT)
    page = _network_page(qtbot, als)
    page.on_enter()
    qtbot.waitUntil(lambda: page.note.kind() == "ok", timeout=3000)
    assert calls == [("http://bcglucidlogbook.dhcp.lbl.gov:8000", FROM_SETTINGS)]
    assert als.bootstrap is BOOT
    assert page.primary_label() == "Continue"
    assert "sign-on, Tiled" in page.note.text()
    assert page.summary() == "Direct"
    assert page.heading()[0] == "Reach the ALS network"


def test_unreachable_offers_the_proxy_and_test_and_continue_uses_it(qtbot, als, monkeypatch):
    calls = []

    def fake_connect(server, proxy):
        calls.append(proxy)
        if proxy is FROM_SETTINGS:
            raise NetworkError("could not connect")
        return BOOT

    monkeypatch.setattr(net, "connect", fake_connect)
    page = _network_page(qtbot, als)
    done = []
    page.completed.connect(lambda: done.append(1))
    page.on_enter()
    qtbot.waitUntil(lambda: page.primary_label() == "Test & continue", timeout=3000)
    assert page.findChild(QWidget, "setupProxyBox").isVisible()
    assert page.note.kind() == "warn" and "*.lbl.gov" in page.note.text()
    assert page.findChild(QLineEdit, "setupProxyHost").text() == "localhost"
    assert page.findChild(QSpinBox, "setupProxyPort").value() == 1080
    page.on_primary()
    qtbot.waitUntil(lambda: done == [1], timeout=3000)
    assert calls == [FROM_SETTINGS, "socks5://localhost:1080"]
    assert als.prefs.get("proxy_auto_detect") is True
    assert als.prefs.get("proxy_host") == "localhost"
    assert als.prefs.get("proxy_port") == 1080
    assert page.summary() == "Via SOCKS proxy" and als.via_proxy is True


def test_bootstrap_failure_is_reported(qtbot, als, monkeypatch):
    def bad(server, proxy):
        raise BootstrapError("unsupported bootstrap schema 2 (expected 1)")

    monkeypatch.setattr(net, "connect", bad)
    page = _network_page(qtbot, als)
    page.on_enter()
    qtbot.waitUntil(lambda: page.note.kind() == "error", timeout=3000)
    assert page.note.text().startswith("Couldn't load facility settings: unsupported")
    assert page.primary_label() == "Try again"
    assert als.bootstrap is None


def test_network_result_after_leaving_is_dropped(qtbot, als, monkeypatch):
    gate = threading.Event()

    def slow(server, proxy):
        gate.wait(5)
        return BOOT

    monkeypatch.setattr(net, "connect", slow)
    page = _network_page(qtbot, als)
    try:
        page.on_enter()
        assert page.primary_label() == "Checking…" and not page.primary_enabled()
        page.on_leave()
    finally:
        gate.set()
    qtbot.wait(300)
    assert als.bootstrap is None
    assert page.note.kind() != "ok"


def test_auto_detect_fills_localhost_1080(qtbot, als, monkeypatch):
    def down(server, proxy):
        raise NetworkError("could not connect")

    monkeypatch.setattr(net, "connect", down)
    monkeypatch.setattr(net, "probe_socks", lambda host, port: True)
    page = _network_page(qtbot, als)
    page.on_enter()
    qtbot.waitUntil(lambda: page.primary_label() == "Test & continue", timeout=3000)
    port = page.findChild(QSpinBox, "setupProxyPort")
    port.setValue(9999)
    page.findChild(QPushButton, "setupProxyDetect").click()
    qtbot.waitUntil(lambda: port.value() == 1080, timeout=3000)
    assert page.note.kind() == "ok"


def _unreachable_page(qtbot, ctx, monkeypatch, facility):
    ctx.facility = facility

    def fake_connect(server, proxy):
        raise NetworkError("could not connect")

    monkeypatch.setattr(net, "connect", fake_connect)
    page = _network_page(qtbot, ctx)
    page.on_enter()
    qtbot.waitUntil(lambda: page.note.kind() == "warn", timeout=3000)
    return page


def test_unreachable_assumed_https_suggests_the_http_form(qtbot, ctx, monkeypatch):
    from lightfall.setup.facilities import other_facility

    page = _unreachable_page(qtbot, ctx, monkeypatch, other_facility("srv.example:8000"))
    assert "didn't answer" in page.note.text()
    assert "If the server doesn't use HTTPS, try http://srv.example:8000." in page.note.text()


@pytest.mark.parametrize("typed", ["http://srv.example:8000", "https://srv.example:8000"])
def test_unreachable_with_a_typed_scheme_has_no_http_hint(qtbot, ctx, monkeypatch, typed):
    from lightfall.setup.facilities import other_facility

    page = _unreachable_page(qtbot, ctx, monkeypatch, other_facility(typed))
    assert "try http://" not in page.note.text()
