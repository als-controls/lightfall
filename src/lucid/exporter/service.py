"""Exporter NATS service — receives jobs, queues them, dispatches to converters."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nats

from lucid.exporter.converters import CONVERTERS, get_converter
from lucid.exporter.tiled_utils import connect_tiled, get_run

logger = logging.getLogger(__name__)


@dataclass
class ExportJob:
    """A parsed export job."""

    job_id: str
    tiled_url: str
    auth_token: str | None
    run_uids: list[str]
    export_type: str
    params: dict[str, Any]
    proxy_url: str | None = None

    @property
    def output_dir(self) -> Path:
        return Path(self.params["output_dir"])


class ExporterService:
    """Headless exporter that subscribes to NATS and processes export jobs.

    Subscribes to:
        - ``lucid.export.<hostname>`` — job requests (request/reply)
        - ``lucid.export.<hostname>.ping`` — health check (request/reply)

    Publishes to:
        - ``lucid.export.<hostname>.progress`` — job progress events
    """

    def __init__(self, nats_url: str, hostname: str) -> None:
        self._nats_url = nats_url
        self._hostname = hostname
        self._nc: nats.NATS | None = None
        self._queue: asyncio.Queue[ExportJob] = asyncio.Queue()
        self._running = False

    @property
    def job_subject(self) -> str:
        return f"lucid.export.{self._hostname}"

    @property
    def ping_subject(self) -> str:
        return f"lucid.export.{self._hostname}.ping"

    @property
    def progress_subject(self) -> str:
        return f"lucid.export.{self._hostname}.progress"

    def _parse_job(self, data: dict[str, Any]) -> ExportJob:
        """Parse and validate a job message. Raises on invalid data."""
        job = ExportJob(
            job_id=data["job_id"],
            tiled_url=data["tiled_url"],
            auth_token=data.get("auth_token"),
            run_uids=data["run_uids"],
            export_type=data["export_type"],
            params=data["params"],
            proxy_url=data.get("proxy_url"),
        )
        if job.export_type not in CONVERTERS:
            raise ValueError(
                f"Unknown export type '{job.export_type}'. "
                f"Available: {list(CONVERTERS.keys())}"
            )
        return job

    def _build_ping_response(self) -> dict[str, Any]:
        """Build a response for ping requests."""
        return {
            "hostname": self._hostname,
            "status": "ready",
            "queue_depth": self._queue.qsize(),
        }

    async def _publish_progress(
        self,
        job_id: str,
        status: str,
        current_run: int = 0,
        total_runs: int = 0,
        detail: str = "",
    ) -> None:
        """Publish a progress event."""
        if self._nc is None:
            return
        payload = json.dumps({
            "job_id": job_id,
            "status": status,
            "current_run": current_run,
            "total_runs": total_runs,
            "detail": detail,
        }).encode()
        await self._nc.publish(self.progress_subject, payload)

    async def _handle_job_request(self, msg: Any) -> None:
        """Handle an incoming job request — parse, queue, reply."""
        try:
            data = json.loads(msg.data.decode())
            job = self._parse_job(data)
            await self._queue.put(job)
            reply = {"job_id": job.job_id, "status": "queued"}
            logger.info(
                "Queued job %s (%d runs, type=%s)",
                job.job_id,
                len(job.run_uids),
                job.export_type,
            )
        except Exception as e:
            reply = {"error": str(e)}
            logger.error("Failed to parse job: %s", e)

        if msg.reply:
            await self._nc.publish(msg.reply, json.dumps(reply).encode())

    async def _handle_ping(self, msg: Any) -> None:
        """Handle a ping request."""
        if msg.reply and self._nc:
            resp = json.dumps(self._build_ping_response()).encode()
            await self._nc.publish(msg.reply, resp)

    async def _process_jobs(self) -> None:
        """Worker loop — pull jobs from queue and process sequentially."""
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            logger.info("Processing job %s", job.job_id)
            try:
                client = await asyncio.to_thread(connect_tiled, job.tiled_url, job.auth_token, job.proxy_url)
                converter_cls = get_converter(job.export_type)
                converter = converter_cls()

                for i, uid in enumerate(job.run_uids, 1):
                    await self._publish_progress(
                        job.job_id, "processing", i, len(job.run_uids),
                        f"Exporting run {uid[:8]}...",
                    )
                    try:
                        run = await asyncio.to_thread(get_run, client, uid)
                        await converter.export(
                            run_client=run,
                            run_uid=uid,
                            params=job.params,
                            output_dir=job.output_dir,
                            progress_cb=lambda detail: None,
                        )
                    except Exception as e:
                        logger.error("Failed to export run %s: %s", uid, e)
                        await self._publish_progress(
                            job.job_id, "failed", i, len(job.run_uids),
                            f"Failed on run {uid[:8]}: {e}",
                        )
                        break
                else:
                    await self._publish_progress(
                        job.job_id, "completed", len(job.run_uids), len(job.run_uids),
                        f"All {len(job.run_uids)} runs exported to {job.output_dir}",
                    )
                    logger.info("Job %s completed", job.job_id)
            except Exception as e:
                logger.error("Job %s failed: %s", job.job_id, e)
                await self._publish_progress(job.job_id, "failed", detail=str(e))

    async def run(self) -> None:
        """Connect to NATS, subscribe, and process jobs until stopped."""
        self._nc = await nats.connect(self._nats_url)
        logger.info("Connected to NATS at %s", self._nats_url)

        await self._nc.subscribe(self.job_subject, cb=self._handle_job_request)
        await self._nc.subscribe(self.ping_subject, cb=self._handle_ping)
        logger.info("Subscribed to %s and %s", self.job_subject, self.ping_subject)

        self._running = True
        await self._process_jobs()

    async def stop(self) -> None:
        """Stop processing and disconnect."""
        self._running = False
        if self._nc:
            await self._nc.drain()
