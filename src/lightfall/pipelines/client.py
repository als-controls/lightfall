"""PipelineClient - Lightfall-side client for the lucid-pipelines NATS service.

Responsibilities:
- Read the cached Tiled API key from SessionManager (auth-v2 minted at login).
- Build a JobMessage and dispatch over IPCService request/reply.
- Subscribe to progress events; re-emit as Qt signals.

Key revocation is owned by SessionManager (cache cleared on logout).
"""
from __future__ import annotations

import socket
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from loguru import logger
from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from lightfall.auth.service_key import MintedKey


class PipelineClient(QObject):
    """In-process Lightfall client; pairs 1:1 with a running `lucid-pipelines` executor.

    `host` is the executor hostname; subjects are built as
    `lightfall.pipeline.{host}[.suffix]`.
    """

    sigJobQueued = Signal(dict)
    sigJobProgress = Signal(dict)
    sigJobCompleted = Signal(dict)
    sigJobFailed = Signal(dict)

    def __init__(
        self,
        *,
        ipc: Any,
        host: str,
        tiled_url: str,
        key_provider: Callable[[str], Optional["MintedKey"]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._ipc = ipc
        self._host = host
        self._tiled_url = tiled_url
        self._key_provider = key_provider

        self._ipc.subscribe(self._progress_subject, self._on_progress, main_thread=True)

    # -- subject helpers ---------------------------------------------------

    @property
    def _submit_subject(self) -> str:
        return f"lightfall.pipeline.{self._host}"

    @property
    def _list_subject(self) -> str:
        return f"lightfall.pipeline.{self._host}.list"

    @property
    def _progress_subject(self) -> str:
        return f"lightfall.pipeline.{self._host}.progress"

    # -- public API --------------------------------------------------------

    def list_available(self, timeout_ms: int = 5000) -> List[Dict[str, Any]]:
        """Synchronous request to the executor; returns its discovered plugins."""
        reply = self._ipc.request(self._list_subject, {}, timeout_ms=timeout_ms)
        if reply is None:
            return []
        return reply.get("pipelines", [])

    def submit(
        self,
        *,
        pipeline: str,
        input_run_uid: str,
        parameters: Dict[str, Any],
        input_access_blob: Dict[str, Any],
        user_id: str,
        timeout_ms: int = 5000,
    ) -> str:
        """Build a JobMessage and dispatch over IPCService; returns job_id.

        Note: TriggerManager.fire() calls submit_callable as
        ``submit_callable(pipeline=, run_uid=, parameters=)``. Wiring
        TriggerManager directly to this method requires an adapter that
        resolves ``input_access_blob`` and ``user_id`` from the start doc
        and the current session, and renames ``run_uid`` to ``input_run_uid``.
        """
        job_id = str(uuid.uuid4())

        minted = self._key_provider("tiled")
        if minted is None:
            raise RuntimeError(
                "No Tiled API key in session cache; login may have failed to mint. "
                "Re-login to refresh."
            )

        payload = {
            "job_id": job_id,
            "tiled_url": self._tiled_url,
            "api_key": minted.secret,
            "api_key_expires_at": minted.expires_at.isoformat() if minted.expires_at else None,
            "input_run_uid": input_run_uid,
            "input_access_blob": input_access_blob,
            "pipeline": pipeline,
            "parameters": parameters,
            "user_id": user_id,
            "requested_by": f"lightfall@{socket.gethostname()}",
            "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        reply = self._ipc.request(self._submit_subject, payload, timeout_ms=timeout_ms)
        if reply is None:
            raise RuntimeError(
                f"Pipeline executor '{self._host}' did not respond to submit"
            )

        logger.info(
            "PipelineClient: submitted job_id={} pipeline={} user={}",
            job_id, pipeline, user_id,
        )
        self.sigJobQueued.emit({
            "job_id": job_id,
            "pipeline": pipeline,
            "input_run_uid": input_run_uid,
        })
        return job_id

    # -- progress handling -------------------------------------------------

    def _on_progress(self, subject: str, data: Dict[str, Any], reply: Optional[str]) -> None:
        if not data.get("job_id") or not data.get("status"):
            logger.warning(
                "PipelineClient: malformed progress event on {}: {}", subject, data,
            )
            return
        self.sigJobProgress.emit(data)
        status = data.get("status")
        if status == "completed":
            self.sigJobCompleted.emit(data)
        elif status == "failed":
            self.sigJobFailed.emit(data)
