"""ThreadedTiledWriter.wait_until_flushed: per-run persistence signal."""
from __future__ import annotations

import threading
import time

from lightfall.services.threaded_tiled_writer import ThreadedTiledWriter


def _stop(uid: str) -> dict:
    return {"run_start": uid, "exit_status": "success"}


def test_fast_writer_signals_after_stop_doc(qapp):
    writer = ThreadedTiledWriter(lambda name, doc: None)
    try:
        writer("start", {"uid": "u1"})
        writer("stop", _stop("u1"))
        assert writer.wait_until_flushed(uid="u1", timeout=5.0) is True
        # Already persisted: a later call returns immediately.
        assert writer.wait_until_flushed(uid="u1", timeout=0.0) is True
    finally:
        writer.stop()


def test_slow_writer_blocks_until_stop_written(qapp):
    release = threading.Event()

    def slow(name, doc):
        if name == "stop":
            release.wait(5)

    writer = ThreadedTiledWriter(slow)
    try:
        writer("start", {"uid": "u2"})
        writer("stop", _stop("u2"))
        assert writer.wait_until_flushed(uid="u2", timeout=0.3) is False
        threading.Timer(0.2, release.set).start()
        t0 = time.monotonic()
        assert writer.wait_until_flushed(uid="u2", timeout=5.0) is True
        assert time.monotonic() - t0 < 4.0
    finally:
        release.set()
        writer.stop()


def test_other_uid_and_failed_stop_do_not_signal(qapp):
    def flaky(name, doc):
        if name == "stop" and doc["run_start"] == "bad":
            raise RuntimeError("upload failed")

    writer = ThreadedTiledWriter(flaky)
    try:
        writer("stop", _stop("bad"))
        writer("stop", _stop("good"))
        assert writer.wait_until_flushed(uid="good", timeout=5.0) is True
        assert writer.wait_until_flushed(uid="bad", timeout=0.2) is False
        assert writer.wait_until_flushed(uid="never", timeout=0.1) is False
    finally:
        writer.stop()


def test_no_uid_waits_for_queue_drain(qapp):
    writer = ThreadedTiledWriter(lambda name, doc: None)
    try:
        writer("event", {})
        assert writer.wait_until_flushed(timeout=5.0) is True
    finally:
        writer.stop()
