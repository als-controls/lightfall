"""Headless evaluation environment: the COSMIC sim beamline, a real Bluesky
engine and a throwaway local Tiled, wired exactly as the app wires them.

Everything an agent touches is the production code path: the device catalog
(mock backend), ``get_engine()`` with its pre-submit hooks and checkpoints,
the Tiled writer, and Tiled reads. Only the GUI is absent (an offscreen
``QApplication`` and a hidden widget stand in for it).
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lightfall.utils.logging import logger


def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@contextmanager
def _sqlite_storage():
    """SimpleTiledServer hard-codes DuckDB tabular storage, whose ADBC driver
    has no wheel for every Python we run; the SQLite driver ships with
    bluesky-tiled-plugins' deps. Swap the storage URI while it is built."""
    import tiled.server.simple as simple

    original = simple.ensure_uri

    def ensure_uri(uri):
        return original(str(uri).replace("duckdb:///", "sqlite:///").replace(
            "storage.duckdb", "storage.sqlite"))

    simple.ensure_uri = ensure_uri
    try:
        yield
    finally:
        simple.ensure_uri = original


class SimEnvironment:
    """One evaluation session. Use as a context manager.

    Args:
        seed: ``LIGHTFALL_SIM_SEED`` for the hidden misalignments (None keeps
            the deterministic defaults).
        role: Role of the simulated signed-in operator (``"user"`` carries
            DEVICE_CONTROL and SCAN_RUN); None leaves the session anonymous
            (guest), as an unauthenticated app would be.
        workdir: Where the Tiled catalog, logs and results go (temp dir if None).
    """

    def __init__(self, seed: str | None = None, workdir: Path | None = None,
                 role: str | None = "user") -> None:
        self.seed = seed
        self.role = role
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="lightfall-eval-"))
        self.app = None
        self.catalog = None
        self.engine = None
        self.tiled_server = None
        self.tiled = None
        self.documents: list[tuple[str, dict]] = []
        self._previous_seed: str | None = None

    # --- lifecycle ---------------------------------------------------------
    def __enter__(self) -> SimEnvironment:
        self._previous_seed = os.environ.get("LIGHTFALL_SIM_SEED")
        if self.seed is None:
            os.environ.pop("LIGHTFALL_SIM_SEED", None)
        else:
            os.environ["LIGHTFALL_SIM_SEED"] = str(self.seed)
        self.app = _qapp()
        self._sign_in()
        self._start_devices()
        self._start_engine()
        self._start_tiled()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self.tiled is not None:
                self.tiled.disconnect_tiled()
        except Exception:  # noqa: BLE001
            logger.debug("eval: tiled disconnect failed", exc_info=True)
        try:
            if self.tiled_server is not None:
                self.tiled_server.close()
        except Exception:  # noqa: BLE001
            logger.debug("eval: tiled server close failed", exc_info=True)
        if self._previous_seed is None:
            os.environ.pop("LIGHTFALL_SIM_SEED", None)
        else:
            os.environ["LIGHTFALL_SIM_SEED"] = self._previous_seed

    def _sign_in(self) -> None:
        if self.role is None:
            return
        from lightfall.auth.policy import Role
        from lightfall.auth.session import Session, SessionManager, User

        user = User(username="eval-operator", display_name="Evaluation operator",
                    roles={Role[self.role.upper()]})
        SessionManager.get_instance().attach_session(Session(user=user))

    def _start_devices(self) -> None:
        from lightfall.devices.backends.mock import MockBackend
        from lightfall.devices.catalog import DeviceCatalog

        self.catalog = DeviceCatalog.get_instance()
        self.catalog.add_backend(MockBackend())
        self.catalog.connect_backends()
        if not self.pump(lambda: "DetectorDiodeCurrent" in self.catalog.get_all_ophyd_devices(), 60):
            raise RuntimeError("sim devices did not load")

    def _start_engine(self) -> None:
        from lightfall.acquire import get_run_engine
        from lightfall.acquire.engine import get_engine

        self.engine = get_engine()
        from lightfall.auth import engine_guard

        engine_guard.install(self.engine)  # same guard as the app
        try:
            get_run_engine().sigDocumentYield.connect(
                lambda name, doc: self.documents.append((name, dict(doc))))
        except Exception:  # noqa: BLE001
            logger.debug("eval: document recorder not attached", exc_info=True)

    def _start_tiled(self) -> None:
        from tiled.server import SimpleTiledServer

        from lightfall.services.tiled_service import TiledService

        with _sqlite_storage():
            self.tiled_server = SimpleTiledServer(self.workdir / "tiled")
        self.tiled = TiledService.get_instance()
        self.tiled.configure(
            url=self.tiled_server.uri.split("/api")[0],
            api_key=self.tiled_server.api_key, auth_mode="api_key",
        )
        self.tiled.connect_async()
        if not self.pump(lambda: self.tiled.is_connected, 60):
            raise RuntimeError("local Tiled did not connect")

    # --- helpers ---------------------------------------------------------
    def pump(self, condition: Callable[[], bool], timeout_s: float) -> bool:
        """Run the Qt event loop until ``condition()`` or the timeout."""
        deadline = time.monotonic() + timeout_s
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        return condition()

    @property
    def devices(self) -> dict[str, Any]:
        return self.catalog.get_all_ophyd_devices()

    def position(self, name: str) -> float:
        return float(self.devices[name].position)

    def read(self, name: str) -> float:
        dev = self.devices[name]
        dev.trigger()
        return float(next(iter(dev.read().values()))["value"])

    def run_plan(self, plan, name: str = "eval", timeout_s: float = 300.0, **md) -> None:
        """Submit through the engine (the same path agent tools use) and wait
        until that procedure has finished (``sigProcedureFinished`` for its
        id; queue/idle checks are already true before the engine picks it up)."""
        finished: dict[str, object] = {}

        def on_finished(proc_id, error):
            finished[proc_id] = error

        self.engine.sigProcedureFinished.connect(on_finished)
        try:
            proc_id = self.engine.submit(plan, name=name, skip_pre_submit=True, **md)
            if not self.pump(lambda: proc_id in finished, timeout_s):
                raise TimeoutError(f"plan '{name}' did not finish within {timeout_s}s")
        finally:
            self.engine.sigProcedureFinished.disconnect(on_finished)
        self.pump(lambda: False, 0.5)  # let the Tiled writer flush
        if finished.get(proc_id) is not None:
            raise RuntimeError(f"plan '{name}' failed: {finished[proc_id]}")

    def runs(self) -> list[dict]:
        """Start documents of every run recorded so far."""
        return [doc for name, doc in self.documents if name == "start"]
