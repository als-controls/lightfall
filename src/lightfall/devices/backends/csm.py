"""Lightfall device backend over CSM's instantiable registry.

A :class:`~lightfall.devices.backends.happi.HappiBackend` whose happi
database is :class:`lightfall.happi_backend.csm.CSMBackend`, scoped to one
beamline (and optionally one IOC) of CSM. Metadata, instantiation (happi
renders each instantiable's python recipe) and background connection all
go through the existing happi code path.

Authentication is an ``httpx.Auth``, normally Lightfall's
``ServiceKeyAuth(csm_service_id(url))``: it reads the CSM API key minted at
login on every request, so a re-login is picked up without rebuilding the
backend.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from loguru import logger

from lightfall.devices.backends.happi import HappiBackend

__all__ = ["CSMDeviceBackend"]


class CSMDeviceBackend(HappiBackend):
    """Read-only device backend for one CSM scope (beamline, optional IOC).

    Args:
        url: CSM base URL, e.g. ``"https://bcgmgmt.als.lbl.gov"``.
        auth: Per-request authentication, normally ``ServiceKeyAuth(csm_service_id(url))``.
        beamline_id: CSM beamline id to load instantiables for.
        ioc_id: Optional CSM IOC id to narrow to one IOC.
        name_filter: Optional instantiable-name filter.
        beamline_name: CSM's name for ``beamline_id``; scopes beam-path rows.
        regex: Treat ``name_filter`` as a regex.
        include_inactive: Include retired instantiables.
        name: Catalog key for this instance.
        instantiate: happi instantiation mode (see :class:`HappiBackend`).
        connection_timeout: Per-device connection timeout in seconds.
        transport: Optional httpx transport (tests).
        proxy: Proxy URL for CSM requests (e.g. ``socks5://localhost:1080``), or None.
    """

    def __init__(
        self,
        url: str,
        auth: httpx.Auth | None,
        *,
        beamline_id: int,
        ioc_id: int | None = None,
        name_filter: str = "",
        regex: bool = False,
        include_inactive: bool = False,
        beamline_name: str | None = None,
        name: str = "csm",
        instantiate: str = "background",
        connection_timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
        proxy: str | None = None,
    ) -> None:
        super().__init__(
            path=None,
            beamline=None,
            instantiate=instantiate,
            connection_timeout=connection_timeout,
            name=name,
        )
        self._url = url
        self._auth = auth
        self._transport = transport
        self._proxy = proxy
        self._beamline_name = beamline_name
        params: dict[str, Any] = {"beamline_id": beamline_id}
        if ioc_id is not None:
            params["ioc_id"] = ioc_id
        if name_filter:
            params["name"] = name_filter
            if regex:
                params["regex"] = "true"
        params["include_inactive"] = "true" if include_inactive else "false"
        self._base_params = params
        self._csm_db: Any = None  # the happi_backend.CSMBackend behind the client

    @property
    def is_editable(self) -> bool:
        return False

    @property
    def base_params(self) -> dict[str, Any]:
        """The CSM search filters this backend applies to every query."""
        return dict(self._base_params)

    def _make_client(self) -> Any:
        import happi

        from lightfall.happi_backend.csm import CSMBackend

        database = CSMBackend(
            self._url,
            auth=self._auth,
            base_params=self._base_params,
            transport=self._transport,
            proxy=self._proxy,
        )
        self._csm_db = database
        return happi.Client(database=database)

    #: The display keys a device edit can change (scope keys belong to scope rows).
    _DEVICE_DISPLAY_KEYS = ("display_name", "icon_override", "group", "synoptic")

    @staticmethod
    def _display_of(device: Any) -> dict[str, Any]:
        """The display values a DeviceInfo carries, in CSM's key names."""
        return {
            "display_name": device.display_name,
            "icon_override": device.icon_override,
            "group": device.group,
            "synoptic": (device.metadata or {}).get("synoptic"),
        }

    def update_device(self, device: Any) -> bool:
        """Write back display metadata only; refuse edits to what the device is.

        Compares the device's display values with CSM's current document and
        sends just the changes (``None`` for removed values) in one
        ``PUT /display``. The generic happi path is bypassed on purpose: it
        rewrites every field (``device_class`` defaults, blank ``beamline`` /
        ``location``) and never deletes keys, so it can neither be trusted to
        touch display keys alone nor to persist a removal.
        """
        from lightfall.happi_backend.csm import _blank_as_none

        if self._csm_db is None or device.id not in self._devices:
            return False
        try:
            current = self._csm_db.get_by_id(device.name)
            if current is None:
                logger.warning("CSM instantiable '{}' not found; edit not saved", device.name)
                return False
            for attr, key in (("prefix", "prefix"), ("device_class", "device_class")):
                if _blank_as_none(getattr(device, attr, None)) != _blank_as_none(current.get(key)):
                    logger.warning(
                        "CSM rows are read-only except display metadata; refused change to "
                        "'{}' of '{}'",
                        attr,
                        device.name,
                    )
                    return False
            patch = {
                key: _blank_as_none(value)
                for key, value in self._display_of(device).items()
                if _blank_as_none(value) != _blank_as_none(current.get(key))
            }
            if patch:
                self._csm_db.put_display(current, patch)
        except Exception as exc:
            logger.error("Failed to update CSM device '{}': {}", device.name, exc)
            return False
        self._devices[device.id] = device
        return True

    def remove_device(self, device_id: Any) -> bool:
        logger.warning("CSM instantiables are not deleted from Lightfall (device {})", device_id)
        return False

    def _own_scope(self, scope: str) -> str | None:
        """``beamline:<this row's CSM beamline>`` if ``scope`` is about it, else None.

        A CSM row speaks only for its own beamline: CSM decides who may write a
        beam-path row by the beamline it names, so the stored scope uses CSM's
        spelling. Lightfall's beamline id is matched loosely (case, punctuation),
        so ``beamline:7011`` finds CSM's ``7.0.1.1``.
        """
        prefix = "beamline:"
        if not self._beamline_name or not scope.startswith(prefix):
            return None

        def _norm(text: str) -> str:
            return re.sub(r"[^a-z0-9]", "", text.lower())

        if _norm(scope[len(prefix) :]) != _norm(self._beamline_name):
            return None
        return prefix + self._beamline_name

    def get_scope_metadata(self, scope: str) -> dict[str, Any] | None:
        # Scope rows have no IOC, so the beamline-filtered search can't see them;
        # CSM's /find looks them up by name alone.
        own = self._own_scope(scope)
        if self._csm_db is None or own is None:
            return None
        scope = own
        try:
            doc = self._csm_db.get_by_id(self._scope_item_name(scope))
        except Exception as exc:
            logger.warning("get_scope_metadata('{}') failed: {}", scope, exc)
            return None
        if doc is None or doc.get("lightfall_scope") != scope:
            return None
        return doc.get("scope_metadata")

    def update_scope_metadata(self, scope: str, metadata: dict[str, Any]) -> bool:
        own = self._own_scope(scope)
        if self._csm_db is None or own is None:
            return False
        scope = own
        name = self._scope_item_name(scope)
        try:
            existing = self._csm_db.get_by_id(name)
            if existing is None:
                self._csm_db.save(
                    name,
                    {
                        "name": name,
                        "device_class": "lightfall.scope",
                        "args": [],
                        "kwargs": {},
                        "active": False,
                        "lightfall_scope": scope,
                        "scope_metadata": metadata,
                    },
                    insert=True,
                )
            else:
                self._csm_db.save(
                    name,
                    {**existing, "lightfall_scope": scope, "scope_metadata": metadata},
                    insert=False,
                )
            return True
        except Exception as exc:
            logger.error("update_scope_metadata('{}') on CSM failed: {}", scope, exc)
            return False

    def get_backend_info(self) -> dict[str, Any]:
        info = super().get_backend_info()
        info.update({"type": "csm", "url": self._url, "filters": self.base_params})
        return info
