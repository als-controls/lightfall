"""Qt message bridge levels (crash_diagnostics._qt_message_handler).

Qt WebEngine's default ``QWebEnginePage.javaScriptConsoleMessage`` forwards a
web page's console output through Qt's message handler in the ``js``
category, with ``console.error`` (and Chromium's own CSP parse errors) as
``QtCriticalMsg``. Those come from remote pages such as login.lbl.gov, not
from Lightfall, so they must not reach Sentry as ERROR (GlitchTip LIGHTFALL-E).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QtMsgType

from lightfall.utils import crash_diagnostics as cd
from lightfall.utils.logging import logger

CSP_MESSAGE = (
    "The source list for the Content Security Policy directive 'script-src-elem' "
    "contains an invalid source: ''nonce-$nonce''. It will be ignored."
)


@pytest.fixture
def records():
    captured: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda m: captured.append((m.record["level"].name, m.record["message"])),
        level="DEBUG",
    )
    yield captured
    logger.remove(sink_id)


def _context(category: str, file: str | None = None, line: int = 0):
    return SimpleNamespace(category=category, file=file, line=line)


def test_web_page_console_error_is_logged_as_warning(records):
    url = "https://login.lbl.gov/idp/profile/SAML2/Redirect/SSO?execution=e1s3"
    cd._qt_message_handler(QtMsgType.QtCriticalMsg, _context("js", url), CSP_MESSAGE)

    assert records == [("WARNING", f"Qt: {CSP_MESSAGE} [{url}:0]")]


def test_web_page_console_info_keeps_its_level(records):
    cd._qt_message_handler(QtMsgType.QtInfoMsg, _context("js"), "hello")

    assert records == [("INFO", "Qt: hello")]


def test_qt_critical_outside_js_category_stays_error(records):
    cd._qt_message_handler(QtMsgType.QtCriticalMsg, _context("default"), "QObject: bad thing")

    assert records == [("ERROR", "Qt: QObject: bad thing")]
