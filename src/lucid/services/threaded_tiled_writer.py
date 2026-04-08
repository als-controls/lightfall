"""Threaded wrapper for TiledWriter to prevent blocking the main thread.

The TiledWriter from bluesky makes synchronous HTTP calls which can block
the UI during scans. This wrapper queues documents and processes them in
a background thread.
"""

from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    pass


class ThreadedTiledWriter:
    """Non-blocking wrapper for TiledWriter.

    Queues incoming documents and processes them in a background thread,
    preventing HTTP calls from blocking the main thread.

    The wrapper implements the Bluesky callback protocol (__call__) so it
    can be subscribed directly to the RunEngine or Engine.

    Example:
        >>> from bluesky.callbacks.tiled_writer import TiledWriter
        >>> from tiled.client import from_uri
        >>>
        >>> client = from_uri("http://localhost:8000")
        >>> writer = TiledWriter(client)
        >>> threaded_writer = ThreadedTiledWriter(writer)
        >>> RE.subscribe(threaded_writer)
    """

    # Sentinel value to signal thread shutdown
    _STOP = object()

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
            error_callback: Optional callback for errors, called with (name, doc, exception).
        """
        self._writer = writer
        self._error_callback = error_callback
        self._queue: queue.Queue[tuple[str, dict] | object] = queue.Queue(
            maxsize=max_queue_size
        )
        self._thread: threading.Thread | None = None
        self._running = False
        self._error_count = 0
        self._processed_count = 0
        self._dropped_count = 0
        self._lock = threading.Lock()

        # Start the background thread
        self._start_thread()

    def _start_thread(self) -> None:
        """Start the background processing thread.

        Uses a non-daemon thread so that Python waits for queued documents
        (especially stop docs) to be processed before exiting.
        """
        if self._thread is not None and self._thread.is_alive():
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._process_queue,
            name="ThreadedTiledWriter",
            daemon=False,
        )
        self._thread.start()
        logger.debug("ThreadedTiledWriter background thread started")

    def _process_queue(self) -> None:
        """Background thread: process documents from the queue."""
        while self._running:
            try:
                # Block with timeout to allow checking _running flag
                item = self._queue.get(timeout=0.5)

                if item is self._STOP:
                    break

                name, doc = item
                self._call_writer(name, doc)
                self._queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logger.error("ThreadedTiledWriter queue error: {}", e)

        logger.debug("ThreadedTiledWriter background thread stopped")

    def _call_writer(self, name: str, doc: dict[str, Any]) -> None:
        """Call the underlying writer with error handling.

        Args:
            name: Document name.
            doc: Document dict.
        """
        try:
            self._writer(name, doc)
            with self._lock:
                self._processed_count += 1
        except Exception as e:
            with self._lock:
                self._error_count += 1

            # Log at debug level to avoid spamming - the original warning
            # from the engine is sufficient
            logger.debug(
                "ThreadedTiledWriter error on '{}' document: {}",
                name,
                e,
            )

            if self._error_callback:
                try:
                    self._error_callback(name, doc, e)
                except Exception:
                    pass  # Don't let callback errors propagate

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
                "ThreadedTiledWriter queue full, dropping '{}' document",
                name,
            )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background thread.

        Args:
            timeout: Seconds to wait for thread to finish.
        """
        self._running = False

        # Send stop sentinel
        try:
            self._queue.put_nowait(self._STOP)
        except queue.Full:
            pass

        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("ThreadedTiledWriter thread did not stop cleanly")

        logger.debug(
            "ThreadedTiledWriter stopped: processed={}, errors={}, dropped={}",
            self._processed_count,
            self._error_count,
            self._dropped_count,
        )

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

    def __del__(self) -> None:
        """Cleanup on deletion."""
        self.stop(timeout=1.0)
