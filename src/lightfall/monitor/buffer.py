"""Thread-safe rolling buffer of inline event scalars for the monitor.

Subscribe-compatible (``__call__(name, doc)``) like LiveDataBuffer
(src/lightfall/acquire/buffer.py), but lock-guarded so the GUI thread can
take a consistent snapshot while the engine worker thread appends. Holds
only inline scalar columns — never external image assets."""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

from lightfall.monitor.data_window import DataWindow


class RollingBuffer:
    def __init__(self, max_points: int = 10000) -> None:
        self._max = max_points
        self._lock = threading.Lock()
        self._uid: str = ""
        self._stopped: bool = False
        self._fields: dict[str, deque[Any]] = {}
        self._seq: deque[int] = deque(maxlen=max_points)
        self._ts: deque[float] = deque(maxlen=max_points)

    def __call__(self, name: str, doc: dict[str, Any]) -> None:
        with self._lock:
            if name == "start":
                self._reset(doc)
            elif name == "event":
                self._append_event(doc)
            elif name == "stop":
                self._stopped = True

    def _reset(self, doc: dict[str, Any]) -> None:
        self._uid = doc.get("uid", "")
        self._stopped = False
        self._fields.clear()
        self._seq.clear()
        self._ts.clear()

    def _append_event(self, doc: dict[str, Any]) -> None:
        self._seq.append(int(doc.get("seq_num", 0)))
        self._ts.append(float(doc.get("time", 0.0)))
        for key, value in (doc.get("data") or {}).items():
            buf = self._fields.get(key)
            if buf is None:
                buf = deque(maxlen=self._max)
                self._fields[key] = buf
            buf.append(value)

    def snapshot(self, now: float) -> DataWindow:
        with self._lock:
            ts = list(self._ts)
            age = (now - ts[-1]) if ts else None
            return DataWindow(
                run_uid=self._uid,
                events={k: list(v) for k, v in self._fields.items()},
                seq_nums=list(self._seq),
                timestamps=ts,
                event_count=len(ts),
                age_s=age,
            )

    @property
    def active_uid(self) -> str:
        return self._uid

    @property
    def stopped(self) -> bool:
        return self._stopped
