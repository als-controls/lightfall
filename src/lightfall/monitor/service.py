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
        from PySide6.QtCore import QTimer
        self._advisor = None
        self._advisor_batch: list[Observation] = []
        self._advise_async = True
        self._advisor_timer = QTimer(self)
        self._advisor_timer.setSingleShot(True)
        self._advisor_timer.setInterval(5000)  # debounce window (ms)
        self._advisor_timer.timeout.connect(self._flush_advisor)

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
        if obs.feed_name != "advisor" and self._advisor_enabled():
            self._advisor_batch.append(obs)
            self._advisor_timer.start()  # (re)arm debounce

    def set_advisor(self, advisor) -> None:
        self._advisor = advisor

    def _advisor_enabled(self) -> bool:
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            return bool(PreferencesManager.get_instance().get("monitor_advisor_enabled", False))
        except Exception:  # noqa: BLE001
            return False

    def _ensure_advisor(self):
        if self._advisor is None:
            from lightfall.monitor.advisor import MonitorAdvisor
            self._advisor = MonitorAdvisor()
        return self._advisor

    def _flush_advisor(self) -> None:
        batch, self._advisor_batch = self._advisor_batch, []
        if not batch or not self._advisor_enabled():
            return
        advisor = self._ensure_advisor()
        if self._advise_async:
            from lightfall.utils.threads import QThreadFuture
            QThreadFuture(advisor.advise, batch,
                          callback_slot=self._on_advisor_reply,
                          key="monitor:advisor").start()
        else:
            self._on_advisor_reply(advisor.advise(batch))

    def _on_advisor_reply(self, reply: str) -> None:
        reply = (reply or "").strip()
        if not reply or reply.lower() == "nothing to report":
            return
        import time
        obs = Observation(
            severity="info", feed_name="advisor",
            run_uid=self._recent[-1].run_uid if self._recent else "",
            title="Advisor", message=reply, state_key="advisor:summary",
            ts=time.time(),
        )
        self._recent.append(obs)
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
