"""Build access_blob metadata for Tiled writes.

The stamper reads the operator's identity from SessionManager, the
active ESAF either from a SettingsPlugin admin override or from
alshub-api, and produces the blob shape expected by als-tiled's
ALSAccessPolicy. See docs/superpowers/specs/2026-04-26-als-tiled-per-
entry-authorization-design.md (in the als-tiled repo) for the spec.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from lightfall.services._alshub_client import AlshubClient

logger = logging.getLogger(__name__)


class MissingSessionError(RuntimeError):
    """Raised when no Keycloak session is available — write must be aborted."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def compute_access_tags(blob: dict[str, Any]) -> list[str]:
    """Project the human-readable access blob into the flat tag list that
    Tiled's PostgreSQL ``AccessBlobFilter`` actually queries.

    ``bluesky_tiled_plugins.TiledWriter`` pops a ``tiled_access_tags`` key
    from the run-start doc and forwards it to ``client.create_container(
    access_tags=...)``, which lands in Tiled's dedicated ``access_blob``
    column as ``{"tags": [...]}``. Without that key the column stays
    empty and every authenticated read filters down to nothing — the data
    is in the catalog but invisible to the policy.

    Tag schema MUST stay in lockstep with als_tiled.write_helpers.access_tags
    (the symmetric reader-side tag minting in ALSAccessPolicy.filters).

      esaf:<esaf_id>
      beamline:<beamline>
      participant:keycloak_sub:<sub>
      participant:orcid:<orcid>
    """
    tags: list[str] = []
    if blob.get("esaf_id"):
        tags.append(f"esaf:{blob['esaf_id']}")
    if blob.get("beamline"):
        tags.append(f"beamline:{blob['beamline']}")
    for p in blob.get("participants") or []:
        if not isinstance(p, dict):
            continue
        sub = p.get("keycloak_sub")
        orcid = p.get("orcid")
        if sub:
            tags.append(f"participant:keycloak_sub:{sub}")
        if orcid:
            tags.append(f"participant:orcid:{orcid}")
    return tags


class AccessStamper:
    """Builds access_blob for Tiled writes."""

    def __init__(
        self,
        beamline: str,
        alshub_client: AlshubClient,
        session_provider: Callable[[], Any],
        settings_provider: Callable[[], Any],
        version: str = "lightfall:dev",
    ):
        self.beamline = beamline
        self._alshub = alshub_client
        self._session_provider = session_provider
        self._settings_provider = settings_provider
        self._version = version

    def _operator_identity(self) -> tuple[str | None, str | None]:
        """Pull (orcid, keycloak_sub) from the Keycloak session.

        Decoded JWT claims live on ``session.user.attributes``, populated by
        Lightfall's Keycloak provider at login. ``orcid`` is only present if the
        Keycloak realm is configured to emit it; ``sub`` is always present
        in any Keycloak token.
        """
        session = self._session_provider()
        # Auth-v2: bearer is discarded post-mint, so `session.token` is always
        # None for an authenticated user. `session.user` (set by the provider in
        # the same place as the token) is the canonical "logged in" signal.
        if session is None or session.user is None:
            raise MissingSessionError("No Keycloak session — refusing to stamp")
        user = getattr(session, "user", None)
        claims = getattr(user, "attributes", None) or {}
        orcid = claims.get("orcid")
        sub = claims.get("sub")
        return orcid, sub

    def _resolve_override(self) -> dict | None:
        settings = self._settings_provider()
        override = getattr(settings, "access_override", None)
        if override is None:
            return None
        try:
            now = datetime.now(UTC)
            start = override.start
            end = override.end
            if start is None or end is None or override.esaf_id is None:
                logger.warning("access_override missing fields; ignoring")
                return None
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
            if not (start <= now <= end):
                return None
            return {
                "esaf_id": override.esaf_id,
                "set_by": getattr(override, "set_by", None),
            }
        except Exception as e:
            logger.warning("access_override malformed: %s; ignoring", e)
            return None

    async def build_blob(self) -> dict[str, Any]:
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
                "added_by":      "lightfall",
            }],
        }
        if esaf_source != "schedule":
            logger.warning("blob stamped with esaf_source=%s for %s",
                           esaf_source, self.beamline)
        return blob

    @staticmethod
    def _derive_proposal(esaf_id: str | None) -> str | None:
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


def install_into_run_engine(stamper: AccessStamper, run_engine: Any) -> None:
    """Install the stamper as a bluesky preprocessor on the RunEngine.

    On each ``RE(plan)`` call, the preprocessor builds a fresh access_blob
    (so operator identity changes, override toggling, and alshub schedule
    rolling are picked up live) and injects it into the run's ``open_run``
    message via :func:`bluesky.preprocessors.inject_md_wrapper`. Bluesky
    then merges that kwarg into the emitted start document.

    Why a preprocessor and not ``RE.md``: bluesky inserts ``RE.md`` values
    verbatim into the start doc — it does NOT evaluate callables. Storing
    a function under ``RE.md["access_blob"]`` either fails to serialize or
    embeds a function reference, neither of which is what we want.
    Preprocessors are the supported per-run metadata mechanism.

    Idempotent: if a stamper preprocessor was previously installed, it is
    removed before the new one is appended (so reconfigure-and-reconnect
    flows don't accumulate stamping passes).
    """
    import asyncio
    import concurrent.futures

    from bluesky.preprocessors import inject_md_wrapper

    existing = getattr(run_engine, "preprocessors", None) or []
    run_engine.preprocessors = [
        p for p in existing if not getattr(p, "_is_access_stamper", False)
    ]

    def _build_blob_sync() -> dict[str, Any]:
        # The RE owns an event loop; preprocessors execute inside it. A
        # worker thread gives asyncio.run a fresh, loop-free context.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, stamper.build_blob()).result(timeout=10)

    def _stamping_preprocessor(plan):
        blob = _build_blob_sync()
        # `access_blob` lands in the start doc's metadata for human-readable
        # audit (which ESAF, which participants, why); `tiled_access_tags`
        # is what TiledWriter actually pops and routes to Tiled's dedicated
        # access_blob column for the AccessBlobFilter SQL predicate. Both
        # are needed: the former for traceability, the latter for the
        # actual read-side gate.
        tags = compute_access_tags(blob)
        return (yield from inject_md_wrapper(
            plan, {"access_blob": blob, "tiled_access_tags": tags},
        ))

    _stamping_preprocessor._is_access_stamper = True  # type: ignore[attr-defined]
    run_engine.preprocessors.append(_stamping_preprocessor)
