"""Headless GUI-thread stall benchmark for the logbook and the threads monitor.

A 20 ms QTimer heartbeat runs on the main thread; any gap over 100 ms between
ticks is recorded as a stall. ``busy`` adds the lateness of every gap over
40 ms (twice the period, above Windows timer jitter), so it also counts the
many 40-100 ms hitches that never reach the stall threshold. Scenarios, each
replayed on a fresh panel:

* ``logbook`` -- a LogbookPanel showing an entry with ~60 existing fragments is
  fed a realistic scan through ``EventListener``: start, descriptor, 21 events
  (each event accompanied by one ``device_change`` action-group update, as a
  motor step logs), stop, plus three sync pulls (``LogbookPanel._on_pull``),
  paced like an 8 s scan.
* ``threads`` -- a visible ThreadsPanel with a handful of running
  QThreadFutures and ~70 idle threads, for the same 8 s. ``threads-hidden`` repeats it with the
  panel created but never shown (the auto-hide sidebar default).

Run from the repo root (offscreen, against the working tree)::

    PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/Scripts/python scripts/bench_gui_stalls.py

Pass scenario names to run a subset: ``... bench_gui_stalls.py logbook``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QElapsedTimer, QEventLoop, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

HEARTBEAT_MS = 20
STALL_MS = 100
BUSY_MS = 40
SCAN_POINTS = 21
SCAN_SECONDS = 8.0


class Heartbeat:
    """Records gaps between 20 ms ticks on the GUI thread."""

    def __init__(self) -> None:
        self._clock = QElapsedTimer()
        self._last = 0
        self.stalls: list[int] = []
        self.busy_ms = 0  # sum of (gap - 20 ms) over gaps > BUSY_MS
        self._timer = QTimer()
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(HEARTBEAT_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._clock.start()
        self._last = 0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = self._clock.elapsed()
        gap = now - self._last
        self._last = now
        if gap > BUSY_MS:
            self.busy_ms += gap - HEARTBEAT_MS
        if gap > STALL_MS:
            self.stalls.append(gap)

    def summary(self) -> str:
        n = len(self.stalls)
        total = sum(self.stalls)
        worst = max(self.stalls, default=0)
        return (f"stalls={n:3d}  total={total:6d} ms  max={worst:5d} ms  "
                f"busy={self.busy_ms:6d} ms")


def _spin(app: QApplication, seconds: float) -> None:  # noqa: ARG001
    """Run the real event loop for *seconds* (no polling-loop jitter)."""
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def _run_timeline(app: QApplication, steps: list[tuple[float, callable]], tail_s: float = 1.0) -> Heartbeat:
    """Run *steps* (offset_s, fn) on the GUI thread under a heartbeat."""
    hb = Heartbeat()
    for offset, fn in steps:
        QTimer.singleShot(int(offset * 1000), fn)
    hb.start()
    _spin(app, max(o for o, _ in steps) + tail_s)
    hb.stop()
    return hb


# -- logbook -----------------------------------------------------------------


def _seed_entry(client, logbook_id: str) -> str:
    """An entry as it looks mid-shift: notes, earlier scans, device changes."""
    eid = client.create_entry(logbook_id, title="Shift notes")
    note = (
        "## Alignment\n\nChecked the **mono** offsets; see table:\n\n"
        "| axis | before | after |\n|---|---|---|\n| pitch | 0.12 | 0.03 |\n| roll | 1.1 | 0.9 |\n\n"
        "```python\nRE(qerlin_focus_scan(marana, sm3, mono, -1, 1, 21))\n```\n"
    )
    for block in range(6):
        client.add_fragment(eid, kind="text", content=f"{note}\nBlock {block}.")
        uid = str(uuid.uuid4())
        client.add_fragment(
            eid, kind="readonly", subtype="bluesky_plan",
            content=f"Plan: qerlin_focus_scan ({uid[:8]}) — success",
            data={"plan_name": "qerlin_focus_scan", "uid": uid, "exit_status": "success",
                  "num_events": {"primary": SCAN_POINTS},
                  "params": {"num": SCAN_POINTS, "detectors": ["marana"], "motors": ["sm3_pitch"]}},
        )
        for k in range(8):
            client.add_fragment(
                eid, kind="readonly", subtype="device_change",
                content=f"sm3_pitch: {k / 10} → {(k + 1) / 10}",
                data={"device_name": "sm3_pitch", "old_value": str(k / 10),
                      "new_value": str((k + 1) / 10), "action_type": "set", "unit": "mrad"},
            )
    return eid


def _make_logbook_panel(app: QApplication, tmp: Path):
    from lightfall.logbook.client import LogbookClient
    from lightfall.logbook.event_listener import EventListener
    from lightfall.ui.panels.logbook_panel import LogbookPanel

    client = LogbookClient()
    client._db_path = tmp / "logbook.db"
    client._load_preferences = lambda: None
    client._offline_only = True  # never start a real sync
    client.init()
    LogbookClient._instance = client

    logbook_id = client.get_or_create_logbook("bench")
    eid = _seed_entry(client, logbook_id)

    LogbookPanel._deferred_init = lambda self: None
    panel = LogbookPanel()
    panel.resize(900, 1000)
    panel.show()
    panel._client = client
    panel._logbook_id = logbook_id
    panel._load_entries()

    listener = EventListener()  # private instance; RunEngine not connected
    EventListener._instance = listener
    listener.current_entry_id = eid
    listener.fragment_injected.connect(panel._on_fragment_injected)
    _spin(app, 0.5)
    return panel, listener, eid


def scenario_logbook(app: QApplication, tmp: Path) -> Heartbeat:
    panel, listener, _eid = _make_logbook_panel(app, tmp)

    run_uid = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    actions: list = []
    dt = SCAN_SECONDS / (SCAN_POINTS + 2)

    def doc(name, body):
        return lambda: listener._on_run_document(name, body)

    def move(i):
        def _f():
            actions.append(SimpleNamespace(device_name="sm3_pitch", old_value=-1 + (i - 1) * 0.1,
                                           new_value=-1 + i * 0.1, action_type="set", unit="mrad"))
            listener._on_action_group_updated(SimpleNamespace(id=group_id, actions=list(actions)))
        return _f

    steps: list[tuple[float, callable]] = [
        (0.05, doc("start", {"uid": run_uid, "plan_name": "qerlin_focus_scan", "time": time.time(),
                             "num_points": SCAN_POINTS, "motors": ["sm3_pitch"], "detectors": ["marana"]})),
        (0.10, doc("descriptor", {"uid": str(uuid.uuid4()), "run_start": run_uid, "name": "primary"})),
    ]
    for i in range(SCAN_POINTS):
        t = 0.2 + (i + 1) * dt
        steps.append((t, move(i)))
        steps.append((t + 0.02, doc("event", {"uid": str(uuid.uuid4()), "seq_num": i + 1,
                                              "data": {"sm3_pitch": -1 + i * 0.1}})))
        if i in (6, 13, 20):
            steps.append((t + 0.05, panel._on_pull))  # sync pulled > 0
    t_end = 0.2 + (SCAN_POINTS + 1) * dt
    steps.append((t_end, doc("stop", {"uid": str(uuid.uuid4()), "run_start": run_uid,
                                      "exit_status": "success", "num_events": {"primary": SCAN_POINTS}})))
    steps.append((t_end + 0.1, panel._on_pull))

    # Count fragment-card constructions: a full rebuild re-creates every card.
    from lightfall.logbook import fragment_widgets as fw

    built = [0]
    orig_init = fw.ReadonlyFragmentWidget.__init__

    def counting_init(self, *a, **kw):
        built[0] += 1
        orig_init(self, *a, **kw)

    fw.ReadonlyFragmentWidget.__init__ = counting_init
    try:
        hb = _run_timeline(app, steps)
    finally:
        fw.ReadonlyFragmentWidget.__init__ = orig_init
    n_frag = len(panel._entry_widget.entry.fragments)
    print(f"    (entry now has {n_frag} fragments; {built[0]} readonly cards built during the scan)")
    panel.close()
    panel.deleteLater()
    _spin(app, 0.2)
    return hb


# -- threads monitor ---------------------------------------------------------


def _busy_threads(n: int, stop: threading.Event) -> list:
    from lightfall.utils.threads import QThreadFuture

    def work(i):
        while not stop.is_set():
            sum(range(2000))
            time.sleep(0.01)

    futures = []
    for i in range(n):
        f = QThreadFuture(work, i, name=f"bench-worker-{i}")
        f.start()
        futures.append(f)
    return futures


def _scenario_threads(app: QApplication, visible: bool) -> Heartbeat:
    from lightfall.ui.panels.threads_panel import ThreadsPanel

    stop = threading.Event()
    futures = _busy_threads(6, stop)
    # The live app runs ~80 OS threads (bluesky, ophyd/caproto, NATS, Tiled,
    # pools); psutil.Process.threads() cost on Windows grows with that count.
    idle = [threading.Thread(target=stop.wait, daemon=True) for _ in range(70)]
    for t in idle:
        t.start()
    panel = ThreadsPanel()
    panel.resize(800, 400)
    if visible:
        panel.show()
    _spin(app, 0.3)
    hb = _run_timeline(app, [(SCAN_SECONDS, lambda: None)], tail_s=0.0)
    stop.set()
    for f in futures:
        f.wait(2000)
    panel.close()
    panel.deleteLater()
    _spin(app, 0.3)
    return hb


def scenario_threads(app: QApplication, _tmp: Path) -> Heartbeat:
    return _scenario_threads(app, visible=True)


def scenario_threads_hidden(app: QApplication, _tmp: Path) -> Heartbeat:
    return _scenario_threads(app, visible=False)


SCENARIOS = {
    "logbook": scenario_logbook,
    "threads": scenario_threads,
    "threads-hidden": scenario_threads_hidden,
}


def main(argv: list[str]) -> int:
    names = argv or list(SCENARIOS)
    app = QApplication.instance() or QApplication([])
    from lightfall.utils.threads import initialize_main_thread_invoker

    initialize_main_thread_invoker()
    with tempfile.TemporaryDirectory() as td:
        for name in names:
            sub = Path(td) / name
            sub.mkdir()
            hb = SCENARIOS[name](app, sub)
            print(f"{name:15s} {hb.summary()}", flush=True)
        from lightfall.logbook.client import LogbookClient

        inst = LogbookClient._instance
        if inst is not None and inst._db is not None:
            inst._db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
