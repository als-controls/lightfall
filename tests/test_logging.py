"""Tests for the logging module."""

import time

from lucid.utils.logging import (
    get_cumulative_stats,
    log_time,
    reset_cumulative_stats,
)


def test_log_time_basic(capfd) -> None:
    """Test basic log_time functionality."""
    with log_time("Test operation"):
        time.sleep(0.01)

    captured = capfd.readouterr()
    assert "Test operation" in captured.err
    assert "elapsed:" in captured.err


def test_log_time_cumulative() -> None:
    """Test cumulative timing statistics."""
    reset_cumulative_stats()

    for _ in range(3):
        with log_time("Repeated op", cumulative_key="test_op"):
            time.sleep(0.01)

    stats = get_cumulative_stats("test_op")
    assert "test_op" in stats
    assert stats["test_op"]["count"] == 3.0
    assert stats["test_op"]["total_ms"] >= 30.0  # At least 30ms total
    assert stats["test_op"]["avg_ms"] >= 10.0  # At least 10ms average


def test_reset_cumulative_stats() -> None:
    """Test resetting cumulative stats."""
    reset_cumulative_stats()

    with log_time("Op 1", cumulative_key="key1"):
        pass
    with log_time("Op 2", cumulative_key="key2"):
        pass

    assert "key1" in get_cumulative_stats()
    assert "key2" in get_cumulative_stats()

    reset_cumulative_stats("key1")
    stats = get_cumulative_stats()
    assert "key1" not in stats
    assert "key2" in stats

    reset_cumulative_stats()
    assert get_cumulative_stats() == {}
