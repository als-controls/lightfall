"""Build access_blob metadata for Tiled writes.

The stamper reads the operator's identity from SessionManager, the
active ESAF either from a SettingsPlugin admin override or from
alshub-api, and produces the blob shape expected by als-tiled's
ALSAccessPolicy. See docs/superpowers/specs/2026-04-26-als-tiled-per-
entry-authorization-design.md (in the als-tiled repo) for the spec.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from lucid.services._alshub_client import AlshubClient

logger = logging.getLogger(__name__)


class MissingSessionError(RuntimeError):
    """Raised when no Keycloak session is available — write must be aborted."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccessStamper:
    """Builds access_blob for Tiled writes."""

    def __init__(
        self,
        beamline: str,
        alshub_client: AlshubClient,
        session_provider: Callable[[], Any],
        settings_provider: Callable[[], Any],
        version: str = "lucid:dev",
    ):
        self.beamline = beamline
        self._alshub = alshub_client
        self._session_provider = session_provider
        self._settings_provider = settings_provider
        self._version = version

    def _operator_identity(self) -> tuple[Optional[str], Optional[str]]:
        session = self._session_provider()
        if session is None or session.token is None:
            raise MissingSessionError("No Keycloak session — refusing to stamp")
        claims = getattr(session.token, "claims", {}) or {}
        orcid = claims.get("orcid")
        sub = claims.get("sub")
        return orcid, sub

    def _resolve_override(self) -> Optional[dict]:
        settings = self._settings_provider()
        override = getattr(settings, "access_override", None)
        if override is None:
            return None
        try:
            now = datetime.now(timezone.utc)
            start = override.start
            end = override.end
            if start is None or end is None or override.esaf_id is None:
                logger.warning("access_override missing fields; ignoring")
                return None
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if not (start <= now <= end):
                return None
            return {
                "esaf_id": override.esaf_id,
                "set_by": getattr(override, "set_by", None),
            }
        except Exception as e:
            logger.warning("access_override malformed: %s; ignoring", e)
            return None

    async def build_blob(self) -> Dict[str, Any]:
        operator_orcid, operator_sub = self._operator_identity()

        override = self._resolve_override()
        if override is not None:
            esaf_id = override["esaf_id"]
            proposal_id = self._derive_proposal(esaf_id)
            esaf_source = "admin_override"
        else:
            try:
                payload = await self._alshub.get_active_esaf(self.beamline)
            except Exception as e:
                logger.warning("alshub lookup failed: %s", e)
                payload = None
                esaf_source = "pending"
                esaf_id = None
                proposal_id = None
            else:
                if payload is None:
                    esaf_id = None
                    proposal_id = None
                    esaf_source = "none"
                else:
                    esaf_id = payload.get("EsafFriendlyId")
                    proposal_id = payload.get("ProposalFriendlyId")
                    esaf_source = "schedule"

        blob = {
            "esaf_id": esaf_id,
            "proposal_id": proposal_id,
            "beamline": self.beamline,
            "created_at": _now_iso(),
            "stamped_by": self._version,
            "esaf_source": esaf_source,
            "participants": [{
                "orcid":         operator_orcid,
                "keycloak_sub":  operator_sub,
                "role":          "operator",
                "added_at":      _now_iso(),
                "added_by":      "lucid",
            }],
        }
        if esaf_source != "schedule":
            logger.warning("blob stamped with esaf_source=%s for %s",
                           esaf_source, self.beamline)
        return blob

    @staticmethod
    def _derive_proposal(esaf_id: Optional[str]) -> Optional[str]:
        """Derive proposal_id from esaf_id when possible.

        ALS convention: ESAF IDs look like 'BLS-00480-001' where 'BLS-00480'
        is the proposal. If the format doesn't match, return None and let
        the operator/admin fix it via the override.
        """
        if not esaf_id:
            return None
        parts = esaf_id.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0]
        return None


def install_into_run_engine(stamper: "AccessStamper", run_engine: Any) -> None:
    """Install the stamper's blob as a lazy entry in `RE.md`.

    Bluesky's RE accepts callables in `md` — the value is called for each
    run start. Using a callable means the blob is built fresh per run
    (operator changes, override toggling, alshub schedule rolling).
    """
    import asyncio
    import threading

    if not hasattr(run_engine, "md") or run_engine.md is None:
        run_engine.md = {}

    async def _build():
        return await stamper.build_blob()

    def _md_provider() -> Dict[str, Any]:
        # Bluesky's md callables are sync. Bridge via the running loop or
        # asyncio.run.
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context (e.g., pytest-asyncio test).
            # Use a thread to run asyncio.run without deadlock.
            result = None
            exception = None

            def _run_in_thread():
                nonlocal result, exception
                try:
                    result = asyncio.run(_build())
                except Exception as e:
                    exception = e

            thread = threading.Thread(target=_run_in_thread, daemon=False)
            thread.start()
            thread.join(timeout=10)

            if exception:
                raise exception
            if result is None:
                raise TimeoutError("MD provider timed out")
            return result
        except RuntimeError:
            # No running loop; use asyncio.run directly (production case)
            return asyncio.run(_build())

    run_engine.md["access_blob"] = _md_provider
