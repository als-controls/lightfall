"""Tests for the get_recent_logs MCP tool."""

from __future__ import annotations

import asyncio
import json

import pytest
from loguru import logger

from lightfall.claude.tools.logs import create_logs_tool
from lightfall.utils.log_buffer import LogBuffer


@pytest.fixture
def installed_buffer() -> LogBuffer:
    buf = LogBuffer.get_instance()
    buf.uninstall()
    buf.clear()
    buf.install(level="DEBUG", max_records=200)
    yield buf
    buf.uninstall()
    buf.clear()


def _call_tool(tool, args: dict) -> dict:
    return asyncio.run(tool.handler(args))


def test_tool_returns_filtered_records(installed_buffer: LogBuffer) -> None:
    logger.debug("noise debug line")
    logger.info("noise info line")
    logger.warning("interesting warning")
    logger.error("interesting error")

    tool = create_logs_tool()
    result = _call_tool(tool, {"level": "WARNING", "since_seconds": 60})

    assert "is_error" not in result or not result["is_error"]
    payload = json.loads(result["content"][0]["text"])
    levels = {r["level"] for r in payload["records"]}
    assert "WARNING" in levels
    assert "ERROR" in levels
    assert "DEBUG" not in levels
    assert "INFO" not in levels


def test_tool_default_level_is_warning(installed_buffer: LogBuffer) -> None:
    logger.info("info-only")
    logger.error("err-only")

    tool = create_logs_tool()
    result = _call_tool(tool, {})

    payload = json.loads(result["content"][0]["text"])
    messages = {r["message"] for r in payload["records"]}
    assert "err-only" in messages
    assert "info-only" not in messages


def test_tool_caps_max_count(installed_buffer: LogBuffer) -> None:
    for i in range(50):
        logger.warning(f"w-{i}")

    tool = create_logs_tool()
    result = _call_tool(tool, {"max_count": 10_000})  # request way over the cap

    payload = json.loads(result["content"][0]["text"])
    assert payload["filter"]["max_count"] == 500  # _HARD_MAX_COUNT
    assert len(payload["records"]) <= 500


def test_tool_includes_exception_by_default(installed_buffer: LogBuffer) -> None:
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        logger.exception("trapped")

    tool = create_logs_tool()
    result = _call_tool(tool, {"level": "ERROR", "since_seconds": 60})

    payload = json.loads(result["content"][0]["text"])
    rec = next(r for r in payload["records"] if r["message"] == "trapped")
    assert "exception" in rec
    assert "RuntimeError" in rec["exception"]


def test_tool_can_omit_exception(installed_buffer: LogBuffer) -> None:
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        logger.exception("trapped")

    tool = create_logs_tool()
    result = _call_tool(tool, {
        "level": "ERROR",
        "since_seconds": 60,
        "include_exception": False,
    })

    payload = json.loads(result["content"][0]["text"])
    rec = next(r for r in payload["records"] if r["message"] == "trapped")
    assert "exception" not in rec
