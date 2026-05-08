"""Tests for the in-process log buffer used by the embedded agent."""

from __future__ import annotations

import time

import pytest
from loguru import logger

from lucid.utils.log_buffer import LogBuffer, get_log_buffer


@pytest.fixture
def buf() -> LogBuffer:
    b = LogBuffer.get_instance()
    b.uninstall()
    b.clear()
    b.install(level="DEBUG", max_records=50)
    yield b
    b.uninstall()
    b.clear()


def test_singleton_identity() -> None:
    assert LogBuffer.get_instance() is LogBuffer.get_instance()
    assert get_log_buffer() is LogBuffer.get_instance()


def test_install_is_idempotent(buf: LogBuffer) -> None:
    sink_before = buf._sink_id
    buf.install(level="DEBUG", max_records=50)
    assert buf._sink_id == sink_before


def test_captures_records_at_or_above_level(buf: LogBuffer) -> None:
    logger.debug("debug message")
    logger.info("info message")
    logger.warning("warning message")
    logger.error("error message")

    records = buf.get_records()
    messages = [r.message for r in records]
    assert "debug message" in messages
    assert "info message" in messages
    assert "warning message" in messages
    assert "error message" in messages


def test_level_filter_excludes_lower(buf: LogBuffer) -> None:
    logger.debug("noise")
    logger.warning("actually interesting")
    logger.error("very interesting")

    records = buf.get_records(level="WARNING")
    levels = {r.level for r in records}
    assert "DEBUG" not in levels
    assert "WARNING" in levels
    assert "ERROR" in levels


def test_newest_first_ordering(buf: LogBuffer) -> None:
    logger.info("first")
    logger.info("second")
    logger.info("third")

    records = buf.get_records(contains="first|second|third")
    # contains is substring (no regex), so do per-message filter:
    records = [r for r in buf.get_records() if r.message in {"first", "second", "third"}]
    assert [r.message for r in records[:3]] == ["third", "second", "first"]


def test_contains_filter(buf: LogBuffer) -> None:
    logger.info("hello world")
    logger.info("goodbye world")
    logger.info("hello again")

    records = buf.get_records(contains="HELLO")
    messages = {r.message for r in records}
    assert "hello world" in messages
    assert "hello again" in messages
    assert "goodbye world" not in messages


def test_name_prefix_filter(buf: LogBuffer) -> None:
    # Direct-write a synthetic record to bypass logger.bind which loguru
    # doesn't expose via name.
    fake = LogBuffer.get_instance()
    from datetime import UTC, datetime

    from lucid.utils.log_buffer import LogRecord

    fake._records.append(LogRecord(
        timestamp=datetime.now(UTC),
        level="INFO",
        level_no=20,
        name="lucid.devices.motor",
        function="move",
        line=10,
        thread="MainThread",
        message="moving motor",
    ))
    fake._records.append(LogRecord(
        timestamp=datetime.now(UTC),
        level="INFO",
        level_no=20,
        name="lucid.acquire.run",
        function="start",
        line=20,
        thread="MainThread",
        message="starting run",
    ))

    records = fake.get_records(name_prefix="lucid.devices")
    names = [r.name for r in records]
    assert all(n.startswith("lucid.devices") for n in names)


def test_max_count_truncates(buf: LogBuffer) -> None:
    for i in range(10):
        logger.info(f"line-{i}")

    records = buf.get_records(max_count=3)
    assert len(records) == 3


def test_since_seconds_excludes_old(buf: LogBuffer) -> None:
    logger.info("old message")
    time.sleep(0.05)
    logger.info("recent message")

    records = buf.get_records(since_seconds=0.02)
    messages = {r.message for r in records}
    assert "recent message" in messages
    assert "old message" not in messages


def test_ring_buffer_evicts_oldest(buf: LogBuffer) -> None:
    for i in range(60):  # capacity is 50 in fixture
        logger.info(f"msg-{i}")

    records = buf.get_records(max_count=200)
    assert len(records) == 50
    messages = {r.message for r in records}
    assert "msg-0" not in messages
    assert "msg-59" in messages


def test_exception_traceback_captured(buf: LogBuffer) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("caught it")

    records = buf.get_records(contains="caught it")
    assert records
    rec = records[0]
    assert rec.exception_info is not None
    assert "ValueError" in rec.exception_info
    assert "boom" in rec.exception_info


def test_clear(buf: LogBuffer) -> None:
    logger.info("something")
    assert len(buf) > 0
    buf.clear()
    assert len(buf) == 0
