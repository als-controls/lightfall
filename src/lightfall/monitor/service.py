"""Always-on monitor service: owns the scheduler, keeps a recent-observation
log, raises toasts for warn/critical, and routes the "discuss in assistant"
hand-off to the reactive Claude agent."""

from __future__ import annotations

import threading
from collections import deque
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from lightfall.monitor.models import Observation
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.ui.mainwindow import LFMainWindow


class MonitorService(QObject):
    observation = Signal(object)  # Observation

    _instance: MonitorService | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self._recent: deque[Observation] = deque(maxlen=200)
        self._window: LFMainWindow | None = None
        self._scheduler = self._build_scheduler()
        if self._scheduler is not None:
            self._scheduler.observation.connect(self._on_observation)

    def _build_scheduler(self):
        from lightfall.acquire.engine import get_engine
        from lightfall.monitor.scheduler import MonitorScheduler
        return MonitorScheduler(get_engine(), parent=self)

    @classmethod
    def get_instance(cls) -> MonitorService:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def set_window(self, window: LFMainWindow) -> None:
        self._window = window

    def start(self) -> None:
        if self._scheduler is not None:
            self._scheduler.start()

    def recent_observations(self) -> list[Observation]:
        return list(self._recent)

    def _on_observation(self, obs: Observation) -> None:
        self._recent.append(obs)
        if obs.severity in ("warn", "critical"):
            self._toast(obs)
        self.observation.emit(obs)

    def _toast(self, obs: Observation) -> None:
        try:
            from lightfall.ui.toast import ToastManager
            mgr = ToastManager.get_instance()
            if obs.severity == "critical":
                mgr.error(obs.title, obs.message)
            else:
                mgr.warning(obs.title, obs.message)
        except Exception:  # noqa: BLE001 — never let a toast failure break the run
            logger.exception("monitor toast failed")

    def discuss_observation(self, obs: Observation) -> None:
        win = self._window
        if win is None:
            logger.warning("discuss_observation: no main window")
            return
        try:
            win.activate_panel("lightfall.panels.claude")
            claude = win.get_panel("lightfall.panels.claude")
            if claude is not None and hasattr(claude, "submit_external_prompt"):
                claude.submit_external_prompt(self._discuss_prompt(obs))
        except Exception:  # noqa: BLE001
            logger.exception("discuss_observation hand-off failed")

    @staticmethod
    def _discuss_prompt(obs: Observation) -> str:
        rec = f"\nSuggested: {obs.recommendation}" if obs.recommendation else ""
        return (
            f"The proactive monitor flagged a [{obs.severity}] from "
            f"'{obs.feed_name}' on run {obs.run_uid}:\n"
            f"{obs.title} — {obs.message}\nMetrics: {obs.metrics}{rec}\n"
            f"Help me investigate this."
        )
