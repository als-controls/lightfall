"""Memory status plugin for the Lightfall status bar (developer indicator).

Shows the process's resident memory (RSS) and, in the tooltip, Python's pending
cyclic-garbage counts. Cyclic GC runs on a GUI-thread timer (see
``crash_diagnostics.install_gui_thread_gc``), so garbage can pile up between ticks;
the indicator turns to the warning color when generation 0 is far past its threshold.
Clicking runs a full collection on the GUI thread and reports what it freed.

Hidden unless ``LIGHTFALL_SHOW_MEMORY=1`` or the ``show_memory_status`` preference is
true. One ``psutil`` RSS read every few seconds costs microseconds.
"""

from __future__ import annotations

import gc
import os
from typing import ClassVar

import psutil
from PySide6.QtCore import QTimer

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.utils.logging import logger

SHOW_ENV = "LIGHTFALL_SHOW_MEMORY"
SHOW_PREF = "show_memory_status"
REFRESH_MS = 2000
# Warn when generation 0 holds this many times its collection threshold.
BACKLOG_FACTOR = 10


def format_bytes(n: float) -> str:
    """Whole MB below 1 GiB, two decimals above (e.g. "412 MB", "1.37 GB")."""
    mb = n / 2**20
    return f"{mb:.0f} MB" if mb < 1024 else f"{mb / 1024:.2f} GB"


def should_show() -> bool:
    if os.environ.get(SHOW_ENV, "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        return bool(PreferencesManager.get_instance().get(SHOW_PREF, False))
    except Exception:  # noqa: BLE001 - no preferences yet: stay hidden
        return False


class MemoryStatusPlugin(StatusBarPlugin):
    """Status bar indicator for process memory and pending cyclic garbage."""

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.memory",
        name="Memory",
        description="Developer indicator: process memory (RSS) and pending Python garbage",
        priority=95,
        position="permanent",
        tooltip="Process memory",
    )

    def __init__(self, process: psutil.Process | None = None) -> None:
        super().__init__()
        self._process = process or psutil.Process()
        self._timer: QTimer | None = None
        self._peak = 0
        self._last_collect: str = ""

    @property
    def name(self) -> str:
        return "memory_status"

    # -- wiring ---------------------------------------------------------------

    def connect_signals(self) -> None:
        shown = should_show()
        self.set_visible(shown)
        if not shown:
            return
        self._timer = QTimer()
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.update)
        self._timer.start()
        self.update()

    def disconnect_signals(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # -- display --------------------------------------------------------------

    def rss(self) -> int:
        return int(self._process.memory_info().rss)

    def backlogged(self) -> bool:
        threshold0 = gc.get_threshold()[0]
        return threshold0 > 0 and gc.get_count()[0] > BACKLOG_FACTOR * threshold0

    def update(self) -> None:
        try:
            rss = self.rss()
        except (psutil.Error, OSError) as e:
            logger.debug("MemoryStatus: could not read RSS: {}", e)
            self.set_text("? MB")
            return
        self._peak = max(self._peak, rss)
        counts = gc.get_count()
        thresholds = gc.get_threshold()
        collections = sum(s.get("collections", 0) for s in gc.get_stats())
        self.set_text(format_bytes(rss))
        lines = [
            f"Resident memory: {format_bytes(rss)} (peak {format_bytes(self._peak)})",
            "Pending garbage (gen 0/1/2): "
            + " / ".join(f"{c} of {t}" for c, t in zip(counts, thresholds, strict=True)),
            f"Automatic GC: {'on' if gc.isenabled() else 'off (GUI-thread timer)'}; "
            f"{collections} collections so far",
            "Click to collect garbage now.",
        ]
        if self._last_collect:
            lines.append(self._last_collect)
        self.set_tooltip("\n".join(lines))
        self.set_color(self._warn_color() if self.backlogged() else None)

    def _warn_color(self) -> str | None:
        try:
            from lightfall.ui.theme import ThemeManager

            return ThemeManager.get_instance().colors.warning
        except Exception:  # noqa: BLE001 - no theme yet
            return "#e2a23b"

    def on_clicked(self) -> None:
        before = self.rss()
        freed = gc.collect()
        after = self.rss()
        self._last_collect = (f"Last manual collection: {freed} objects freed, "
                              f"RSS {format_bytes(before)} → {format_bytes(after)}")
        logger.info("MemoryStatus: {}", self._last_collect)
        self.update()
