"""Network page: probe the facility server; off-site, offer the SOCKS proxy."""
from __future__ import annotations

from urllib.parse import urlsplit

import shiboken6
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from lightfall.setup.bootstrap import FROM_SETTINGS, Bootstrap, BootstrapError
from lightfall.setup.context import SetupContext
from lightfall.setup.facilities import as_domain_list
from lightfall.setup.page import StepPage
from lightfall.setup.steps import network as net
from lightfall.ui.preferences.proxy_settings import DEFAULT_PROXY_HOST, DEFAULT_PROXY_PORT
from lightfall.utils.threads import QThreadFuture


class NetworkPage(StepPage):
    def __init__(self, ctx: SetupContext) -> None:
        super().__init__(ctx)
        self._generation = 0
        self._socks_generation = 0
        self._futures: list[QThreadFuture] = []
        self._state = "idle"  # idle | checking | ok | unreachable | failed
        self._via_proxy = False
        self._host = ""

        self._proxy_box = QWidget(self)
        self._proxy_box.setObjectName("setupProxyBox")
        grid = QGridLayout(self._proxy_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnMinimumWidth(0, 120)
        use_label = QLabel("Use proxy", self._proxy_box)
        use_label.setProperty("modern", "muted")
        self._proxy_toggle = QCheckBox(self._proxy_box)
        self._proxy_toggle.setObjectName("setupProxyToggle")
        self._proxy_toggle.setChecked(True)
        self._proxy_toggle.toggled.connect(lambda _checked: self.primary_changed.emit())
        host_label = QLabel("Host · port", self._proxy_box)
        host_label.setProperty("modern", "muted")
        self._proxy_host = QLineEdit(self._proxy_box)
        self._proxy_host.setObjectName("setupProxyHost")
        self._proxy_port = QSpinBox(self._proxy_box)
        self._proxy_port.setObjectName("setupProxyPort")
        self._proxy_port.setRange(1, 65535)
        self._detect_link = QPushButton("Auto-detect", self._proxy_box)
        self._detect_link.setObjectName("setupProxyDetect")
        self._detect_link.setProperty("modern", "link")
        self._detect_link.setFlat(True)
        self._detect_link.clicked.connect(self._auto_detect)
        host_row = QHBoxLayout()
        host_row.addWidget(self._proxy_host, 1)
        host_row.addWidget(self._proxy_port)
        grid.addWidget(use_label, 0, 0)
        grid.addWidget(self._proxy_toggle, 0, 1)
        grid.addWidget(host_label, 1, 0)
        grid.addLayout(host_row, 1, 1)
        grid.addWidget(self._detect_link, 1, 2)
        self._proxy_box.hide()
        self.body().addWidget(self._proxy_box)
        self.body().addStretch(1)

    # --- StepPage ---------------------------------------------------------------

    def heading(self) -> tuple[str, str]:
        return (f"Reach the {self.ctx.facility_label()} network",
                "Lightfall checks that it can reach your facility's server, then loads the "
                "facility's settings.")

    def primary_label(self) -> str:
        if self._state == "checking":
            return "Checking…"
        if self._state == "unreachable":
            return "Test & continue" if self._will_use_proxy() else "Try again"
        if self._state == "failed":
            return "Try again"
        return "Continue"

    def primary_enabled(self) -> bool:
        return self._state != "checking"

    def summary(self) -> str:
        return "Via SOCKS proxy" if self._via_proxy else "Direct"

    def on_enter(self) -> None:
        self._check(FROM_SETTINGS, persist_proxy=False)

    def on_leave(self) -> None:
        self._generation += 1
        self._socks_generation += 1
        if self._state == "checking":
            self._set_state("idle")
            self.note.clear()  # the busy "Testing connection…" note is stale now

    def close_page(self) -> None:
        self.on_leave()

    def on_primary(self) -> None:
        if self._state == "ok":
            self.completed.emit()
        elif self._state == "unreachable" and self._will_use_proxy():
            self._check(self._proxy_url(), persist_proxy=True)
        else:
            self._check(FROM_SETTINGS, persist_proxy=False)

    # --- checks -----------------------------------------------------------------

    def _domains(self) -> list[str]:
        if self.ctx.facility is not None:
            return list(self.ctx.facility.proxy_domains)
        return as_domain_list(self.ctx.prefs.get("proxy_auto_detect_domains", None))

    def _proxy_offered(self) -> bool:
        return bool(self._domains())

    def _will_use_proxy(self) -> bool:
        """Will the primary button retry through the SOCKS proxy (not a plain retry)?"""
        return self._proxy_offered() and self._proxy_toggle.isChecked()

    def _proxy_url(self) -> str:
        host = self._proxy_host.text().strip() or DEFAULT_PROXY_HOST
        return f"socks5://{host}:{self._proxy_port.value()}"

    def _set_state(self, state: str) -> None:
        self._state = state
        self.primary_changed.emit()

    def _check(self, proxy: object, *, persist_proxy: bool) -> None:
        server = self.ctx.server_url()
        if not server:
            self._set_state("failed")
            self.note.show_note("warn", "Choose a facility first.")
            return
        self._generation += 1
        gen = self._generation
        self._host = net.host_of(server)
        self._set_state("checking")
        self.note.show_busy(f"Testing connection to {self._host}…")
        future = QThreadFuture(
            net.connect, server, proxy,
            callback_slot=lambda bootstrap, g=gen: self._on_connected(g, bootstrap,
                                                                      persist_proxy),
            except_slot=lambda exc, g=gen: self._on_failed(g, exc),
            name="setup-network-check", log_exceptions=False,
        )
        self._futures = [f for f in self._futures if f.isRunning()] + [future]
        future.start()

    def _alive(self, gen: int) -> bool:
        return gen == self._generation and shiboken6.isValid(self)

    def _on_connected(self, gen: int, bootstrap: Bootstrap, persist_proxy: bool) -> None:
        if not self._alive(gen):
            return  # the user moved on; drop the late result
        self.ctx.bootstrap = bootstrap
        if persist_proxy:
            self._persist_proxy()
        self._via_proxy = persist_proxy or net.route_label(bootstrap.server_url) == (
            "Via SOCKS proxy")
        self.ctx.via_proxy = self._via_proxy
        self._set_state("ok")
        loaded = ", ".join(bootstrap.summary()) or "no facility settings yet"
        name = bootstrap.facility_name or self.ctx.facility_label()
        self.note.show_note("ok", f"Connected to {self._host}. Loaded from {name}: {loaded}.")
        if persist_proxy:
            self.completed.emit()  # "Test & continue"

    def _on_failed(self, gen: int, exc: Exception) -> None:
        if not self._alive(gen):
            return
        if isinstance(exc, BootstrapError):
            self._set_state("failed")
            self.note.show_note("error", f"Couldn't load facility settings: {exc}")
            return
        self._set_state("unreachable")
        message = f"{self._host} didn't answer ({exc})."
        server = self.ctx.server_url() or ""
        if (self.ctx.facility is not None and self.ctx.facility.scheme_assumed
                and server.startswith("https://")):
            message += (" If the server doesn't use HTTPS, try "
                        f"http://{urlsplit(server).netloc}.")
        if self._proxy_offered():
            message += (f" Off-site machines reach {', '.join(self._domains())} through a "
                        "SOCKS proxy.")
            self._show_proxy_group()
        self.note.show_note("warn", message)

    def _show_proxy_group(self) -> None:
        if self._proxy_box.isVisible():
            return
        prefs = self.ctx.prefs
        self._proxy_host.setText(str(prefs.get("proxy_host", DEFAULT_PROXY_HOST)
                                     or DEFAULT_PROXY_HOST))
        try:
            port = int(prefs.get("proxy_port", DEFAULT_PROXY_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_PROXY_PORT
        self._proxy_port.setValue(port)
        self._proxy_toggle.setText(f"Use a SOCKS proxy for {', '.join(self._domains())}")
        self._proxy_box.show()

    def _persist_proxy(self) -> None:
        prefs = self.ctx.prefs
        prefs.set("proxy_type", "socks5")
        prefs.set("proxy_host", self._proxy_host.text().strip() or DEFAULT_PROXY_HOST)
        prefs.set("proxy_port", self._proxy_port.value())
        prefs.set("proxy_auto_detect", True)

    # --- auto-detect ---------------------------------------------------------------

    def _auto_detect(self) -> None:
        self._socks_generation += 1
        gen = self._socks_generation
        self.note.show_busy(
            f"Looking for a SOCKS proxy on {DEFAULT_PROXY_HOST}:{DEFAULT_PROXY_PORT}…")
        future = QThreadFuture(
            net.probe_socks, DEFAULT_PROXY_HOST, DEFAULT_PROXY_PORT,
            callback_slot=lambda found, g=gen: self._on_socks(g, found),
            name="setup-socks-probe", log_exceptions=False,
        )
        self._futures = [f for f in self._futures if f.isRunning()] + [future]
        future.start()

    def _on_socks(self, gen: int, found: bool) -> None:
        if gen != self._socks_generation or not shiboken6.isValid(self):
            return
        where = f"{DEFAULT_PROXY_HOST}:{DEFAULT_PROXY_PORT}"
        if found:
            self._proxy_host.setText(DEFAULT_PROXY_HOST)
            self._proxy_port.setValue(DEFAULT_PROXY_PORT)
            self._proxy_toggle.setChecked(True)
            self.note.show_note("ok", f"Found a SOCKS proxy on {where}.")
        else:
            self.note.show_note(
                "warn", f"Nothing is listening on {where}. Start your SOCKS tunnel, then try again.")
