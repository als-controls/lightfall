"""CSM backend type: one CSM scope (beamline + optional IOC) of instantiables."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QWidget,
)
from shiboken6 import isValid

from lightfall.devices.backend_types.base import BackendEditor, BackendType, BuildContext, hint
from lightfall.devices.backends.csm import CSMDeviceBackend

DEFAULT_CSM_URL = "https://csm.als.lbl.gov"


def session_token() -> str | None:
    """The logged-in user's Keycloak access token, or None."""
    from lightfall.auth.session import SessionManager

    session = SessionManager.get_instance().session
    return session.token if session is not None else None


def _get(url: str, path: str, token: str, params: dict[str, Any] | None, timeout: float) -> Any:
    response = httpx.get(
        f"{url.rstrip('/')}{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    if response.status_code == 401:
        raise PermissionError(
            "CSM rejected the Lightfall token (401). "
            "Check that 'LUCID' is in CSM's KEYCLOAK_ACCEPTED_CLIENT_IDS."
        )
    if response.status_code == 403:
        raise PermissionError("CSM: no reader role for this user (403).")
    response.raise_for_status()
    return response.json()


def fetch_beamlines(url: str, token: str, timeout: float = 10.0) -> list[tuple[int, str]]:
    """Beamlines visible to the user, as ``(id, name)`` sorted by name."""
    rows = _get(url, "/api/admin/beamlines", token, None, timeout)
    return sorted(((int(b["id"]), str(b["name"])) for b in rows), key=lambda item: item[1])


def fetch_iocs(url: str, token: str, beamline_id: int, timeout: float = 10.0) -> list[tuple[int, str]]:
    """IOCs that own instantiables on a beamline, as ``(id, name)`` sorted by name."""
    rows = _get(
        url,
        "/api/instantiables",
        token,
        {"beamline_id": beamline_id, "include_inactive": "true"},
        timeout,
    )
    iocs = {
        int(r["ioc_id"]): str(r.get("ioc_name") or r["ioc_id"])
        for r in rows
        if r.get("ioc_id") is not None
    }
    return sorted(iocs.items(), key=lambda item: item[1])


class _CSMEditor(BackendEditor):
    def __init__(self, config: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._futures: list[Any] = []
        form = QFormLayout(self)

        self._url = QLineEdit(str(config.get("url") or DEFAULT_CSM_URL))
        self._url.editingFinished.connect(self.refresh)
        self._url.textChanged.connect(lambda *_: self.changed.emit())
        form.addRow("CSM URL:", self._url)

        self._beamline = QComboBox()
        self._set_items(
            self._beamline, [], config.get("beamline_id"), config.get("beamline_name"), with_all=False
        )
        self._beamline.currentIndexChanged.connect(self._on_beamline_changed)
        refresh = QToolButton()
        refresh.setText("⟳")
        refresh.setToolTip("Reload beamlines and IOCs from CSM")
        refresh.clicked.connect(self.refresh)
        beamline_row = QHBoxLayout()
        beamline_row.addWidget(self._beamline, 1)
        beamline_row.addWidget(refresh)
        form.addRow("Beamline:", beamline_row)

        self._ioc = QComboBox()
        self._set_items(self._ioc, [], config.get("ioc_id"), config.get("ioc_name"), with_all=True)
        self._ioc.currentIndexChanged.connect(lambda *_: self.changed.emit())
        form.addRow("IOC:", self._ioc)

        self._name_filter = QLineEdit(str(config.get("name_filter") or ""))
        self._name_filter.setPlaceholderText("(optional) instantiable name")
        self._regex = QCheckBox("regex")
        self._regex.setChecked(bool(config.get("regex", False)))
        filter_row = QHBoxLayout()
        filter_row.addWidget(self._name_filter, 1)
        filter_row.addWidget(self._regex)
        form.addRow("Name filter:", filter_row)

        self._include_inactive = QCheckBox("Include retired instantiables")
        self._include_inactive.setChecked(bool(config.get("include_inactive", False)))
        form.addRow(self._include_inactive)

        self._name_filter.textChanged.connect(lambda *_: self.changed.emit())
        self._regex.toggled.connect(lambda *_: self.changed.emit())
        self._include_inactive.toggled.connect(lambda *_: self.changed.emit())

        self._status = QLabel()
        self._status.setWordWrap(True)
        form.addRow(self._status)
        form.addRow(hint(
            "Loads the instantiables CSM has for this scope and builds their python "
            "recipes with happi. Uses your Lightfall login; loads after you log in."
        ))
        self.refresh()

    # -- combo helpers ---------------------------------------------------

    @staticmethod
    def _set_items(
        combo: QComboBox,
        items: list[tuple[int, str]],
        keep_id: int | None,
        keep_name: str | None,
        *,
        with_all: bool,
    ) -> None:
        """Fill ``combo`` with ``items``, keeping the saved selection visible."""
        combo.blockSignals(True)
        combo.clear()
        if with_all:
            combo.addItem("(all)", None)
        ids = {item_id for item_id, _ in items}
        if keep_id is not None and keep_id not in ids:
            combo.addItem(str(keep_name or keep_id), keep_id)
        for item_id, name in items:
            combo.addItem(name, item_id)
        index = combo.findData(keep_id)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    @staticmethod
    def _current(combo: QComboBox) -> tuple[int | None, str | None]:
        item_id = combo.currentData()
        return (item_id, combo.currentText() if item_id is not None else None)

    # -- fetching ----------------------------------------------------------

    def _alive(self) -> bool:
        return isValid(self)

    def _run(self, fn: Any, *args: Any, callback: Any) -> None:
        from lightfall.utils.threads import QThreadFuture

        future = QThreadFuture(fn, *args, callback_slot=callback, except_slot=self._on_fetch_error)
        self._futures.append(future)
        future.start()

    def refresh(self) -> None:
        """Reload the beamline list (and then IOCs) from CSM if logged in."""
        token = session_token()
        url = self._url.text().strip()
        if not token or not url:
            self._beamline.setEnabled(False)
            self._ioc.setEnabled(False)
            self._status.setText("Log in to refresh beamline and IOC lists.")
            return
        self._status.setText("Loading beamlines from CSM…")
        self._run(fetch_beamlines, url, token, callback=self._on_beamlines)

    def _on_beamlines(self, items: list[tuple[int, str]]) -> None:
        if not self._alive():
            return
        keep_id, keep_name = self._current(self._beamline)
        self._set_items(self._beamline, items, keep_id, keep_name, with_all=False)
        self._beamline.setEnabled(True)
        self._status.setText("")
        self._load_iocs()

    def _on_beamline_changed(self, *_: Any) -> None:
        self._load_iocs()
        self.changed.emit()

    def _load_iocs(self) -> None:
        token = session_token()
        beamline_id = self._beamline.currentData()
        if not token or beamline_id is None:
            return
        self._run(fetch_iocs, self._url.text().strip(), token, beamline_id, callback=self._on_iocs)

    def _on_iocs(self, items: list[tuple[int, str]]) -> None:
        if not self._alive():
            return
        keep_id, keep_name = self._current(self._ioc)
        self._set_items(self._ioc, items, keep_id, keep_name, with_all=True)
        self._ioc.setEnabled(True)

    def _on_fetch_error(self, exc: Exception) -> None:
        if not self._alive():
            return
        logger.warning("CSM lookup failed: {}", exc)
        self._status.setText(f"Could not reach CSM: {exc}")

    # -- config ------------------------------------------------------------

    def get_config(self) -> dict[str, Any]:
        beamline_id, beamline_name = self._current(self._beamline)
        ioc_id, ioc_name = self._current(self._ioc)
        return {
            "url": self._url.text().strip(),
            "beamline_id": beamline_id,
            "beamline_name": beamline_name,
            "ioc_id": ioc_id,
            "ioc_name": ioc_name,
            "name_filter": self._name_filter.text().strip(),
            "regex": self._regex.isChecked(),
            "include_inactive": self._include_inactive.isChecked(),
        }


class CSMBackendType(BackendType):
    type_id = "csm"
    display_name = "CSM"
    requires_auth = True

    def default_config(self) -> dict[str, Any]:
        return {
            "url": DEFAULT_CSM_URL,
            "beamline_id": None,
            "beamline_name": None,
            "ioc_id": None,
            "ioc_name": None,
            "name_filter": "",
            "regex": False,
            "include_inactive": False,
        }

    def summary(self, config: dict[str, Any]) -> str:
        host = urlparse(config.get("url") or "").hostname or "?"
        text = f"{host} · {config.get('beamline_name') or '?'}"
        if config.get("ioc_id") is not None:
            text += f" / {config.get('ioc_name') or config['ioc_id']}"
        return text

    def create_editor(self, config: dict[str, Any], parent: QWidget | None = None) -> BackendEditor:
        return _CSMEditor(config, parent)

    def validate(self, config: dict[str, Any]) -> list[str]:
        if not str(config.get("url") or "").strip():
            return ["CSM URL is required"]
        if config.get("beamline_id") is None:
            return ["Beamline is required"]
        return []

    def build(self, config: dict[str, Any], name: str, ctx: BuildContext) -> CSMDeviceBackend:
        return CSMDeviceBackend(
            str(config["url"]),
            session_token,
            beamline_id=int(config["beamline_id"]),
            ioc_id=config.get("ioc_id"),
            name_filter=str(config.get("name_filter") or ""),
            regex=bool(config.get("regex", False)),
            include_inactive=bool(config.get("include_inactive", False)),
            name=name,
            instantiate=ctx.instantiate_mode,
            connection_timeout=ctx.connection_timeout,
        )
