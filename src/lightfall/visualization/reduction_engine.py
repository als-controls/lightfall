"""Progressive, cancellable per-point reduction.

``iter_point_values`` is the pure synchronous core: for each scan point it
fetches the point's ``(n_frames, H, W)`` sub-cube (via the injected
``fetch_point`` callable) and applies the operator's ``point_scalar``. Failures
yield NaN and the walk continues.

``ReductionEngine`` runs that walk in a background thread and emits results on
the main thread, superseding any in-flight walk when ``start`` is called again.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator

import numpy as np
from PySide6.QtCore import QObject, Signal

from lightfall.utils.threads import QThreadFutureIterator
from lightfall.visualization.reductions import ReductionOperator


def iter_point_values(
    n_points: int,
    fetch_point: Callable[[int], np.ndarray],
    operator: ReductionOperator,
) -> Iterator[tuple[int, float]]:
    """Yield ``(point_index, scalar)`` for each scan point.

    A fetch or reduction error for a point yields ``(point, nan)``.
    """
    for p in range(n_points):
        try:
            cube = fetch_point(p)
            value = float(operator.point_scalar(cube))
        except Exception:
            value = float("nan")
        yield (p, value)


class ReductionEngine(QObject):
    """Runs :func:`iter_point_values` in the background.

    Signals (all delivered on the main thread):
        pointComputed(int, float): a point's reduced scalar is ready.
        progress(int, int): (points done, total).
        finished(): the current walk completed (not emitted when superseded).
    """

    pointComputed = Signal(int, float)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._future: QThreadFutureIterator | None = None
        self._done = 0

    def cancel(self) -> None:
        """Supersede any in-flight walk.

        Non-blocking: bumps the generation counter and requests interruption
        but does not wait for the worker thread to finish. A slow ``fetch_point``
        may run briefly after this returns; the generation guard makes any late
        ``pointComputed``/``finished`` callbacks harmless no-ops.
        """
        self._generation += 1
        if self._future is not None:
            self._future.cancel(timeout_ms=0)
            self._future = None

    def start(
        self,
        n_points: int,
        fetch_point: Callable[[int], np.ndarray],
        operator: ReductionOperator,
    ) -> None:
        """Begin a fresh walk, cancelling any previous one."""
        self.cancel()
        gen = self._generation
        self._done = 0

        def work() -> Iterator[tuple[int, float]]:
            yield from iter_point_values(n_points, fetch_point, operator)

        def on_yield(item: tuple[int, float], _gen: int = gen) -> None:
            if _gen != self._generation:
                return
            p, value = item
            self._done += 1
            self.pointComputed.emit(int(p), float(value))
            self.progress.emit(self._done, n_points)

        def on_done(_result: object = None, _gen: int = gen) -> None:
            if _gen == self._generation:
                self.finished.emit()

        self._future = QThreadFutureIterator(
            work,
            yield_slot=on_yield,
            callback_slot=on_done,
            register=False,
        )
        self._future.start()
