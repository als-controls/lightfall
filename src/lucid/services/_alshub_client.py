"""Tiny async client for ALS hub API.

Used at write time to look up the active ESAF for a beamline. This is
intentionally separate from any als-tiled client — LUCID authenticates
to alshub-api via API key (machine-to-machine), not via the user's JWT.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class AlshubClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None,
                 timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._timeout = timeout

    async def get_active_esaf(self, beamline: str) -> Optional[dict]:
        """Return active-esaf payload, or None on 404. Raises on network errors.

        Distinguishing "no schedule" (404) from "alshub down" (raise) lets
        the caller (AccessStamper) flag the run as `esaf_source="pending"`
        when the lookup actually failed, vs `esaf_source="none"` when there
        was just nothing scheduled.
        """
        params = {}
        if self.api_key:
            params["api-key"] = self.api_key
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
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
            logger.warning("alshub get_active_esaf(%s) failed: %s", beamline, e)
            raise
