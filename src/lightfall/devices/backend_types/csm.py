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

CSM_KEY_SCOPES = ["read", "display:write", "instantiables:create"]


def csm_base(url: str) -> str:
    """A CSM base URL in one canonical spelling (no trailing slash, lower case)."""
    return url.strip().rstrip("/").lower()


def csm_service_id(url: str) -> str:
    """The SessionManager service id under which this CSM's API key is cached."""
    return "csm:" + csm_base(url)


#: Preference holding the facility CSM's URL, minted at every login (like ``tiled_url``).
CSM_URL_PREF = "csm_url"


def get_csm_base_url() -> str:
    """The configured facility CSM base URL (canonical spelling), or the ALS default."""
    try:
        from lightfall.ui.preferences import PreferencesManager

        value = PreferencesManager.get_instance().get(CSM_URL_PREF, None)
    except Exception:
        value = None
    return csm_base(value or DEFAULT_CSM_URL)


def csm_mint_targets() -> list[str]:
    """Distinct base URLs of every CSM row (enabled or not), for minting keys at login.

    Disabled rows count: the Keycloak bearer exists only during login, so a new
    CSM row is saved disabled, and its key is minted at the next login before
    the row can be finished and enabled.
    """
    from lightfall.devices.backend_types.rows import load_rows
    from lightfall.ui.preferences import PreferencesManager

    rows = load_rows(PreferencesManager.get_instance().get) or []
    # The facility CSM is always minted, so a first CSM row works without a re-login.
    targets: list[str] = [get_csm_base_url()]
    for row in rows:
        url = row.config.get("url") if row.type == "csm" else None
        if url and csm_base(url) not in targets:
            targets.append(csm_base(url))
    return targets


def session_authenticated() -> bool:
    """Whether a user is logged in to Lightfall right now."""
    from lightfall.auth.session import SessionManager

    return SessionManager.get_instance().is_authenticated


def csm_api_key(url: str) -> str | None:
    """This session's minted CSM key for ``url``, or None (logged out / mint failed)."""
    from lightfall.auth.session import SessionManager

    return SessionManager.get_instance().get_api_key(csm_service_id(url))


def csm_auth(url: str) -> httpx.Auth:
    """Per-request auth for ``url``: reads the minted key on every request."""
    from lightfall.auth.service_key_auth import ServiceKeyAuth

    return ServiceKeyAuth(csm_service_id(url))


def _get(
    url: str, path: str, auth: httpx.Auth | None, params: dict[str, Any] | None, timeout: float
) -> Any:
    response = httpx.get(f"{url.rstrip('/')}{path}", params=params, auth=auth, timeout=timeout)
    if response.status_code == 401:
        raise PermissionError(
            "CSM rejected Lightfall's key (401). Was a CSM key minted at login? "
            "Look in the log for 'mint failed for service=csm:'."
        )
    if response.status_code == 403:
        raise PermissionError("CSM: no reader role for this user (403).")
    response.raise_for_status()
    return response.json()


def fetch_beamlines(
    url: str, auth: httpx.Auth | None, timeout: float = 10.0
) -> list[tuple[int, str]]:
    """Beamlines visible to the user, as ``(id, name)`` sorted by name."""
    rows = _get(url, "/api/admin/beamlines", auth, None, timeout)
    return sorted(((int(b["id"]), str(b["name"])) for b in rows), key=lambda item: item[1])


def fetch_iocs(
    url: str, auth: httpx.Auth | None, beamline_id: int, timeout: float = 10.0
) -> list[tuple[int, str]]:
    """IOCs that own instantiables on a beamline, as ``(id, name)`` sorted by name."""
    rows = _get(
        url,
        "/api/instantiables",
        auth,
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

        self._url = QLineEdit(str(config.get("url") or get_csm_base_url()))
        self._url.editingFinished.connect(self.refresh)
        self._url.textChanged.connect(lambda *_: self.changed.emit())
        form.addRow("CSM URL:", self._url)

        self._beamline = QComboBox()
        self._set_items(
            self._beamline,
            [],
            config.get("beamline_id"),
            config.get("beamline_name"),
            with_all=False,
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
        form.addRow(
            hint(
                "Loads the instantiables CSM has for this scope and builds their python "
                "recipes with happi. Uses the CSM key Lightfall mints when you log in; loads after login."
            )
        )
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

    def _run(self, job: Any, *, callback: Any) -> None:
        """Run ``job()`` off the GUI thread.

        ``job`` takes no arguments and reads the token itself: QThreadFuture
        logs ``repr(args)`` when a worker raises, and the bearer token must
        never reach the logs. ``_on_fetch_error`` does the logging instead.
        """
        from lightfall.utils.threads import QThreadFuture

        future = QThreadFuture(
            job,
            callback_slot=callback,
            except_slot=self._on_fetch_error,
            log_exceptions=False,
        )
        self._futures.append(future)
        future.start()

    def refresh(self) -> None:
        """Reload the beamline list (and then IOCs) from CSM if logged in."""
        url = self._url.text().strip()
        if not url or not csm_api_key(url):
            self._beamline.setEnabled(False)
            self._ioc.setEnabled(False)
            if url and session_authenticated():
                # Keys are minted only at login (the bearer is gone afterwards).
                self._status.setText(
                    "No CSM key for this URL in this session. Save this row disabled, "
                    "then log out and back in to mint one; the lists will load then."
                )
            else:
                self._status.setText("Log in to refresh beamline and IOC lists.")
            return
        self._status.setText("Loading beamlines from CSM…")
        self._run(lambda: fetch_beamlines(url, csm_auth(url)), callback=self._on_beamlines)

    def _on_beamlines(self, items: list[tuple[int, str]]) -> None:
        if not self._alive():
            return
        keep_id, keep_name = self._current(self._beamline)
        self._set_items(self._beamline, items, keep_id, keep_name, with_all=False)
        self._beamline.setEnabled(True)
        self._status.setText("")
        self._load_iocs()

    def _on_beamline_changed(self, *_: Any) -> None:
        # The user picked another beamline: the old IOC belongs to the old one.
        self._set_items(self._ioc, [], None, None, with_all=True)
        self._load_iocs()
        self.changed.emit()

    def _load_iocs(self) -> None:
        url = self._url.text().strip()
        beamline_id = self._beamline.currentData()
        if beamline_id is None or not csm_api_key(url):
            return
        self._run(lambda: fetch_iocs(url, csm_auth(url), beamline_id), callback=self._on_iocs)

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
            "url": get_csm_base_url(),
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
            csm_auth(str(config["url"])),
            beamline_id=int(config["beamline_id"]),
            ioc_id=config.get("ioc_id"),
            name_filter=str(config.get("name_filter") or ""),
            regex=bool(config.get("regex", False)),
            include_inactive=bool(config.get("include_inactive", False)),
            beamline_name=config.get("beamline_name"),
            name=name,
            instantiate=ctx.instantiate_mode,
            connection_timeout=ctx.connection_timeout,
        )
