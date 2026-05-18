"""PipelineClient - LUCID-side client for the lucid-pipelines NATS service.

Responsibilities:
- Mint a job-scoped Tiled API key via `lucid.auth.job_key.mint_job_key()`.
- Build a JobMessage and dispatch over IPCService request/reply.
- Subscribe to progress events; re-emit as Qt signals.
- Revoke the API key after the job terminates (best-effort).
"""
from __future__ import annotations

import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from PySide6.QtCore import QObject, Signal

from lucid.auth.job_key import mint_job_key, revoke_job_key


class PipelineClient(QObject):
    """In-process LUCID client; pairs 1:1 with a running `lucid-pipelines` executor.

    `host` is the executor hostname; subjects are built as
    `lucid.pipeline.{host}[.suffix]`.
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
        bearer_provider: Callable[[], str],
        default_lifetime: int = 86400,
        default_scopes: Optional[List[str]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._ipc = ipc
        self._host = host
        self._tiled_url = tiled_url
        self._get_bearer = bearer_provider
        self._default_lifetime = default_lifetime
        self._default_scopes = list(default_scopes or [
            "read:metadata", "read:data", "write:metadata", "write:data",
        ])
        self._active_keys: Dict[str, str] = {}                # job_id -> first_eight

        self._ipc.subscribe(self._progress_subject, self._on_progress, main_thread=True)

    # -- subject helpers ---------------------------------------------------

    @property
    def _submit_subject(self) -> str:
        return f"lucid.pipeline.{self._host}"

    @property
    def _list_subject(self) -> str:
        return f"lucid.pipeline.{self._host}.list"

    @property
    def _progress_subject(self) -> str:
        return f"lucid.pipeline.{self._host}.progress"

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
        """Mint a key, send the job; returns job_id.

        Note: TriggerManager.fire() calls submit_callable as
        ``submit_callable(pipeline=, run_uid=, parameters=)``. Wiring
        TriggerManager directly to this method requires an adapter that
        resolves ``input_access_blob`` and ``user_id`` from the start doc
        and the current session, and renames ``run_uid`` to ``input_run_uid``.
        """
        job_id = str(uuid.uuid4())
        bearer = self._get_bearer()
        minted = mint_job_key(
            self._tiled_url,
            bearer,
            lifetime=self._default_lifetime,
            scopes=self._default_scopes,
            note=f"lucid pipeline {pipeline} job {job_id[:8]}",
        )
        self._active_keys[job_id] = minted.first_eight

        payload = {
            "job_id": job_id,
            "tiled_url": self._tiled_url,
            "api_key": minted.secret,
            "api_key_expires_at": minted.expires_at,
            "input_run_uid": input_run_uid,
            "input_access_blob": input_access_blob,
            "pipeline": pipeline,
            "parameters": parameters,
            "user_id": user_id,
            "requested_by": f"lucid@{socket.gethostname()}",
            "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        reply = self._ipc.request(self._submit_subject, payload, timeout_ms=timeout_ms)
        if reply is None:
            try:
                revoke_job_key(self._tiled_url, self._get_bearer(), first_eight=minted.first_eight)
            except Exception as e:
                logger.warning("PipelineClient: submit-failure revoke failed: {}", e)
            self._active_keys.pop(job_id, None)
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
            self._maybe_revoke(data.get("job_id"))
        elif status == "failed":
            self.sigJobFailed.emit(data)
            self._maybe_revoke(data.get("job_id"))

    def _maybe_revoke(self, job_id: Optional[str]) -> None:
        if not job_id:
            return
        first_eight = self._active_keys.pop(job_id, None)
        if not first_eight:
            return
        try:
            revoke_job_key(self._tiled_url, self._get_bearer(), first_eight=first_eight)
        except Exception as e:
            logger.warning("PipelineClient: revoke failed for {}: {}", first_eight, e)
