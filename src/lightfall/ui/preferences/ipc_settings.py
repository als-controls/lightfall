"""IPC/NATS settings plugin.

Configures the connection to the NATS message broker for inter-process
communication, including topic prefix and trusted application management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from PySide6.QtWidgets import QListWidget, QPushButton, QVBoxLayout, QWidget

from lightfall.ipc.defaults import DEFAULT_IPC_NATS_URL, DEFAULT_IPC_TOPIC_PREFIX
from lightfall.ipc.local_server import (
    nats_binary_version,
    probe_nats,
    resolve_nats_binary,
)
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.binding import binding_derived, shown_unless_bound
from lightfall.ui.preferences.form.schema import (
    Action,
    Embed,
    FormValues,
    Info,
    Integer,
    Section,
    SettingField,
    Text,
    Toggle,
    Url,
)
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon

    from lightfall.ipc.trust import TrustManager

# Console-only keys and the items about this machine are shown at "This console" only.
CONSOLE = frozenset({"local"})
DEFAULT_LOCAL_PORT = 4222
LIST_MAX_HEIGHT = 120
NO_BINARY = "nats-server not found — install it or `pip install lightfall[local-nats]`"

_PREFIX_FIELD, _PREFIX_BINDING = binding_derived(
    SettingField("ipc_topic_prefix", Text(), label="Topic Prefix", placeholder=DEFAULT_IPC_TOPIC_PREFIX),
    DEFAULT_IPC_TOPIC_PREFIX,
)


def _use_local(values: FormValues) -> bool:
    return values.effective("ipc_use_local_nats", False) is True


def _not_local(values: FormValues) -> bool:
    return not _use_local(values)


def _test_connection(values: FormValues) -> str:
    """Read the NATS INFO banner from the configured URL (worker thread); raise on failure."""
    url = (values.effective("ipc_nats_url", DEFAULT_IPC_NATS_URL) or "").strip()
    if not url:
        raise RuntimeError("No URL configured")
    parsed = urlparse(url)
    info = probe_nats(parsed.hostname or "localhost", parsed.port or DEFAULT_LOCAL_PORT, timeout=5.0)
    if info is None:
        raise RuntimeError("Could not reach a NATS server")
    return f"Connected — NATS v{info.get('version', '?')}"


def _trust_manager() -> TrustManager | None:
    """The application's TrustManager, or None outside a running app."""
    from lightfall.core.services import ServiceRegistry
    from lightfall.ipc.trust import TrustManager

    try:
        return ServiceRegistry.get_instance().get(TrustManager, None)
    except Exception as e:  # noqa: BLE001 - no registry: show an empty list
        logger.debug("No TrustManager for the IPC settings page: {}", e)
        return None


class TrustedAppsWidget(QWidget):
    """Applications trusted this session, with "Revoke Selected" (applies at once).

    Not a preference: trust is session state held by the TrustManager, so the
    list loads when the widget is built and a revoke takes effect immediately.
    """

    def __init__(self, trust_manager: TrustManager | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._trust = trust_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self.list = QListWidget(self)
        self.list.setSelectionBehavior(QListWidget.SelectionBehavior.SelectRows)
        self.list.setMaximumHeight(LIST_MAX_HEIGHT)
        layout.addWidget(self.list)
        self.revoke_button = QPushButton("Revoke Selected", self)
        self.revoke_button.clicked.connect(self._on_revoke)
        layout.addWidget(self.revoke_button)
        if trust_manager is not None:
            self.list.addItems(sorted(trust_manager.trusted_apps))
        self.revoke_button.setEnabled(trust_manager is not None)

    def _on_revoke(self) -> None:
        selected = self.list.currentItem()
        if selected is None or self._trust is None:
            return
        self._trust.revoke(selected.text())
        self.list.takeItem(self.list.row(selected))


class IPCSettingsPlugin(SettingsPlugin):
    """Settings plugin for IPC/NATS configuration.

    Preferences keys:
    - ``ipc_nats_url``: str — NATS broker URL
    - ``ipc_topic_prefix``: str — topic prefix for all published messages; follows
      the console's binding when bound, editable at "This console" when unbound
    - ``ipc_display_name``: str — human-readable name for this Lightfall instance
    - ``ipc_use_local_nats``: bool — run a bundled local nats-server instead of the site broker
    - ``ipc_local_nats_port``: int — port for the local nats-server (default 4222)

    The nats-server binary is looked up each time the form asks for ``sections()``
    (every scope rebuild), not on every edit; predicates and the status line read
    that cached result. Its version is read once per binary path.
    """

    def __init__(self) -> None:
        super().__init__()
        self._trust_override: TrustManager | None = None
        self._nats_path: str | None = None
        self._versions: dict[str, str | None] = {}

    @property
    def name(self) -> str:
        return "ipc"

    @property
    def display_name(self) -> str:
        return "IPC"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 80

    def set_trust_manager(self, trust_manager: TrustManager) -> None:
        """Use ``trust_manager`` instead of the application's registered one."""
        self._trust_override = trust_manager

    # Predicates and status lines --------------------------------------------
    def _binary_found(self, _values: FormValues | None = None) -> bool:
        return self._nats_path is not None

    def _toggle_enabled(self, values: FormValues) -> bool:
        # A stale "on" stays switchable so it can be turned off without the binary.
        return self._binary_found() or _use_local(values)

    def _port_enabled(self, values: FormValues) -> bool:
        return self._binary_found() and _use_local(values)

    def _binary_status(self, _values: FormValues) -> str:
        path = self._nats_path
        if path is None:
            return NO_BINARY
        if path not in self._versions:
            self._versions[path] = nats_binary_version(path)
        version = self._versions[path]
        return f"{f'v{version}' if version else '(version unknown)'} — {path}"

    # The topic prefix follows the console's binding (form/binding.py).
    _prefix_unbound = staticmethod(shown_unless_bound)
    _prefix_binding = staticmethod(_PREFIX_BINDING.text)

    @staticmethod
    def _turned_on_now(values: FormValues) -> bool:
        """The user switched the local server on in this edit (a stale stored "on"
        doesn't count: startup copes with a missing binary)."""
        return values.get("ipc_use_local_nats") is True and values.is_dirty("ipc_use_local_nats")

    def _trusted_apps(self, parent: QWidget | None) -> QWidget:
        trust = self._trust_override if self._trust_override is not None else _trust_manager()
        return TrustedAppsWidget(trust, parent)

    # Form ---------------------------------------------------------------------
    def sections(self) -> Sequence[Section]:
        self._nats_path = resolve_nats_binary()
        return [
            Section(
                "Local NATS Server",
                [
                    SettingField(
                        "ipc_use_local_nats",
                        Toggle(),
                        label="Run a local NATS server",
                        description="Instead of the site broker",
                        enabled_when=self._toggle_enabled,
                    ),
                    SettingField(
                        "ipc_local_nats_port",
                        Integer(1, 65535),
                        label="Port",
                        placeholder=str(DEFAULT_LOCAL_PORT),
                        enabled_when=self._port_enabled,
                    ),
                    Info(self._binary_status, scopes=CONSOLE),
                ],
            ),
            Section(
                "NATS Connection",
                [
                    SettingField(
                        "ipc_nats_url",
                        Url(schemes=("nats",)),
                        label="Server URL",
                        description="Leave empty to disable IPC",
                        placeholder=DEFAULT_IPC_NATS_URL,
                        enabled_when=_not_local,
                    ),
                    _PREFIX_FIELD,
                    _PREFIX_BINDING,
                    SettingField(
                        "ipc_display_name",
                        Text(),
                        label="Display Name",
                        placeholder="e.g. CMS Hutch",
                    ),
                    Action("Test Connection", _test_connection, scopes=CONSOLE),
                ],
            ),
            Section("Trusted Applications", [Embed(self._trusted_apps, scopes=CONSOLE)]),
        ]

    def validate_values(self, values: FormValues) -> list[str]:
        # Every rule concerns console-only keys; elsewhere effective() would read
        # this console's state and block unrelated edits (e.g. the branch prefix).
        if values.scope != "local":
            return []
        if _use_local(values):
            errors: list[str] = []
            port = values.effective("ipc_local_nats_port", DEFAULT_LOCAL_PORT)
            if not isinstance(port, int) or not 1 <= port <= 65535:
                errors.append("Local NATS port must be between 1 and 65535")
            if not self._binary_found() and self._turned_on_now(values):
                errors.append("nats-server not found (install it or `pip install lightfall[local-nats]`)")
            return errors
        url = (values.effective("ipc_nats_url", "") or "").strip()
        if url and not url.startswith("nats://"):
            return ["NATS server URL must start with 'nats://' (or leave empty to disable IPC)"]
        return []
