"""Threaded wrapper for TiledWriter to prevent blocking the main thread.

The TiledWriter makes synchronous HTTP calls which can block the UI during
scans. This wrapper queues documents and processes them in a background
QThreadFuture, integrating with Lightfall's ThreadManager for lifecycle
management and monitoring.
"""

from __future__ import annotations

import collections
import queue
import threading
import time
from typing import TYPE_CHECKING, Any

from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

if TYPE_CHECKING:
    pass

_STOP = object()

# How many recently persisted run uids to remember for wait_until_flushed().
_PERSISTED_UIDS_KEPT = 512


class ThreadedTiledWriter:
    """Non-blocking wrapper for TiledWriter.

    Queues incoming documents and processes them in a background
    QThreadFuture, preventing HTTP calls from blocking the main thread.
    The thread is registered with Lightfall's ThreadManager for visibility
    in the threads monitoring panel.

    The wrapper implements the Bluesky callback protocol (__call__) so it
    can be subscribed directly to the RunEngine or Engine.

    Example:
        >>> from bluesky_tiled_plugins import TiledWriter
        >>> from tiled.client import from_uri
        >>>
        >>> client = from_uri("http://localhost:8000")
        >>> writer = TiledWriter(client)
        >>> threaded_writer = ThreadedTiledWriter(writer)
        >>> RE.subscribe(threaded_writer)
    """

    def __init__(
        self,
        writer: Any,
        max_queue_size: int = 10000,
        error_callback: Any | None = None,
    ) -> None:
        """Initialize the threaded writer.

        Args:
            writer: The underlying TiledWriter instance.
            max_queue_size: Maximum documents to queue before dropping.
            error_callback: Optional callback for errors, called with
                (name, doc, exception).
        """
        self._writer = writer
        self._error_callback = error_callback
        self._queue: queue.Queue[tuple[str, dict] | object] = queue.Queue(
            maxsize=max_queue_size
        )
        self._error_count = 0
        self._processed_count = 0
        self._dropped_count = 0
        self._lock = threading.Lock()
        # Uids whose stop document the worker has written successfully.
        self._persisted_cond = threading.Condition()
        self._persisted_uids: collections.deque[str] = collections.deque(
            maxlen=_PERSISTED_UIDS_KEPT
        )

        self._thread = QThreadFuture(
            self._process_queue,
            name="TiledWriter",
            interrupt_callable=self._unblock_queue,
        )
        self._thread.start()

    def _unblock_queue(self) -> None:
        """Send stop sentinel so the queue loop exits promptly on cancel."""
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass

    def _process_queue(self) -> None:
        """Background thread: process documents from the queue."""
        thread = self._thread
        while not thread.isInterruptionRequested():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is _STOP:
                break

            name, doc = item
            try:
                self._writer(name, doc)
                with self._lock:
                    self._processed_count += 1
                if name == "stop":
                    self._note_stop_persisted(doc)
            except Exception as e:
                with self._lock:
                    self._error_count += 1
                # A failure here means data silently does NOT reach Tiled — e.g. a
                # run's array node is left at its initial 0-row shape because the
                # stream_datum/stop flush never completed, so the image 500s on read.
                # Surface it loudly with a traceback instead of hiding it at DEBUG.
                logger.opt(exception=True).error(
                    "TiledWriter failed on '{}' document (data not written): {}",
                    name,
                    e,
                )
                if self._error_callback:
                    try:
                        self._error_callback(name, doc, e)
                    except Exception:
                        pass
            finally:
                self._queue.task_done()

        logger.debug(
            "TiledWriter stopped: processed={}, errors={}, dropped={}",
            self._processed_count,
            self._error_count,
            self._dropped_count,
        )

    def _note_stop_persisted(self, doc: Any) -> None:
        uid = doc.get("run_start") if isinstance(doc, dict) else None
        if not isinstance(uid, str) or not uid:
            return
        with self._persisted_cond:
            self._persisted_uids.append(uid)
            self._persisted_cond.notify_all()

    def wait_until_flushed(
        self, uid: str | None = None, timeout: float = 30.0
    ) -> bool:
        """Wait until a run's documents are persisted (thread-safe).

        Must not be called from the Qt GUI thread (it blocks).

        Args:
            uid: Run start uid. Waits until that run's ``stop`` document has
                been written by the worker. If None, waits for the whole
                queue to drain (same as :meth:`flush`).
            timeout: Maximum seconds to wait.

        Returns:
            True once persisted, False on timeout (or if the worker stopped
            first, or the stop document failed to write / was dropped).
        """
        if uid is None:
            return self.flush(timeout=timeout)
        deadline = time.monotonic() + max(0.0, timeout)
        with self._persisted_cond:
            while uid not in self._persisted_uids:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._thread.isRunning():
                    return uid in self._persisted_uids
                self._persisted_cond.wait(min(remaining, 0.5))
        return True

    def __call__(self, name: str, doc: dict[str, Any]) -> None:
        """Handle a Bluesky document (non-blocking).

        Queues the document for background processing and returns immediately.

        Args:
            name: Document name ('start', 'descriptor', 'event', 'stop').
            doc: Document dictionary.
        """
        try:
            self._queue.put_nowait((name, doc))
        except queue.Full:
            with self._lock:
                self._dropped_count += 1
            logger.warning(
                "TiledWriter queue full, dropping '{}' document", name
            )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background thread.

        Args:
            timeout: Seconds to wait for thread to finish.
        """
        self._thread.cancel(timeout_ms=int(timeout * 1000))

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait for all queued documents to be processed.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            True if queue was flushed, False if timeout.
        """
        # queue.Queue.join() has no timeout: it would block forever once the
        # worker has stopped with documents still queued (shutdown order).
        deadline = time.monotonic() + max(0.0, timeout)
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._thread.isRunning():
                    return False
                self._queue.all_tasks_done.wait(min(remaining, 0.5))
        return True

    @property
    def queue_size(self) -> int:
        """Current number of documents in queue."""
        return self._queue.qsize()

    @property
    def stats(self) -> dict[str, int]:
        """Get processing statistics."""
        with self._lock:
            return {
                "processed": self._processed_count,
                "errors": self._error_count,
                "dropped": self._dropped_count,
                "queued": self._queue.qsize(),
            }
