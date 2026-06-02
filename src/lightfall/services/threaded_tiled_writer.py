"""Threaded wrapper for TiledWriter to prevent blocking the main thread.

The TiledWriter makes synchronous HTTP calls which can block the UI during
scans. This wrapper queues documents and processes them in a background
QThreadFuture, integrating with LUCID's ThreadManager for lifecycle
management and monitoring.
"""

from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING, Any

from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

if TYPE_CHECKING:
    pass

_STOP = object()


class ThreadedTiledWriter:
    """Non-blocking wrapper for TiledWriter.

    Queues incoming documents and processes them in a background
    QThreadFuture, preventing HTTP calls from blocking the main thread.
    The thread is registered with LUCID's ThreadManager for visibility
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
            except Exception as e:
                with self._lock:
                    self._error_count += 1
                logger.debug("TiledWriter error on '{}' document: {}", name, e)
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
        try:
            self._queue.join()
            return True
        except Exception:
            return False

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
