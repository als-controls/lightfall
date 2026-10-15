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

from typing import Any

import httpx

from lightfall.devices.backends.happi import HappiBackend

__all__ = ["CSMDeviceBackend"]


class CSMDeviceBackend(HappiBackend):
    """Read-only device backend for one CSM scope (beamline, optional IOC).

    Args:
        url: CSM base URL, e.g. ``"https://csm.als.lbl.gov"``.
        auth: Per-request authentication, normally ``ServiceKeyAuth(csm_service_id(url))``.
        beamline_id: CSM beamline id to load instantiables for.
        ioc_id: Optional CSM IOC id to narrow to one IOC.
        name_filter: Optional instantiable-name filter.
        regex: Treat ``name_filter`` as a regex.
        include_inactive: Include retired instantiables.
        name: Catalog key for this instance.
        instantiate: happi instantiation mode (see :class:`HappiBackend`).
        connection_timeout: Per-device connection timeout in seconds.
        transport: Optional httpx transport (tests).
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
        name: str = "csm",
        instantiate: str = "background",
        connection_timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
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
        params: dict[str, Any] = {"beamline_id": beamline_id}
        if ioc_id is not None:
            params["ioc_id"] = ioc_id
        if name_filter:
            params["name"] = name_filter
            if regex:
                params["regex"] = "true"
        params["include_inactive"] = "true" if include_inactive else "false"
        self._base_params = params

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
        )
        return happi.Client(database=database)

    def get_backend_info(self) -> dict[str, Any]:
        info = super().get_backend_info()
        info.update({"type": "csm", "url": self._url, "filters": self.base_params})
        return info
