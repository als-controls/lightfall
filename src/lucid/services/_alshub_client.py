"""Tiny async client for ALS hub API.

Used at write time to look up the active ESAF for a beamline. The
``/beamlines/{bl}/active-esaf`` route on alshub-api is public (no API
key required), so this client only needs the base URL and an optional
proxy.

The proxy hook exists for development setups where the LUCID host is
off the LBL network and reaches ``*.lbl.gov`` through a SOCKS proxy
(typically ``socks5h://localhost:1080``). Beamline workstations inside
the LBL network leave it empty — direct access works.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class AlshubClient:
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 5.0,
        proxy: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._timeout = timeout
        self._proxy = proxy or None

    async def get_active_esaf(self, beamline: str) -> Optional[dict]:
        """Return active-esaf payload, or None on 404. Raises on network errors.

        Distinguishing "no schedule" (404) from "alshub down" (raise) lets
        the caller (AccessStamper) flag the run as ``esaf_source="pending"``
        when the lookup actually failed, vs ``esaf_source="none"`` when
        there was just nothing scheduled.
        """
        params = {}
        if self.api_key:
            params["api-key"] = self.api_key

        client_kwargs = {"timeout": self._timeout}
        if self._proxy:
            # httpx supports socks5/socks5h via the `socksio` package.
            client_kwargs["proxy"] = self._proxy

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(
                    f"{self.base_url}/beamlines/{beamline}/active-esaf",
                    params=params,
                )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            return None
        except (httpx.NetworkError, httpx.TimeoutException) as e:
            # Some httpx exceptions stringify empty; log the class name too.
            logger.warning(
                "alshub get_active_esaf(%s) failed: %s: %s (proxy=%r)",
                beamline,
                type(e).__name__,
                str(e) or "(no message)",
                self._proxy,
            )
            raise
