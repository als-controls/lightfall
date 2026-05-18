"""Adaptive experiment plan — coordinates with Tsuchinoko over NATS.

Tsuchinoko sends measurement targets via NATS; this plan measures them
and signals back when each point is done. The plan opens a bluesky run at
start and closes it when stopped (via UI stop button or timeout).

See: docs/superpowers/specs/2026-04-12-plan-ui-adaptive-plan-design.md
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Generator
from typing import TYPE_CHECKING, Annotated, Any

from bluesky import plan_stubs as bps
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from lucid.acquire.plan_ui import PlanState, PlanUI, plan_with_ui
from lucid.ui.annotations import DeviceFilter
from lucid.utils.logging import logger

if TYPE_CHECKING:
    Motor = Any
    Detector = Any
else:
    Motor = Any
    Detector = Any


class AdaptivePlanState(PlanState):
    """State specific to the adaptive experiment plan."""

    iteration_changed = Signal(int)
    targets_received = Signal(int)

    current_iteration: int = 0


# Module-level singleton — shared between plan and UI
_state = AdaptivePlanState()


class AdaptiveExperimentPanel(PlanUI):
    """UI for the adaptive experiment plan.

    Reads state from the module-level _state instance.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Status labels
        self._iteration_label = QLabel("Iteration: 0")
        self._targets_label = QLabel("Last batch: 0")
        self._status_label = QLabel("Status: waiting for targets")

        # Control buttons
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop_clicked)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setCheckable(True)
        self._pause_btn.toggled.connect(self._on_pause_toggled)

        # Layout
        button_row = QHBoxLayout()
        button_row.addWidget(self._stop_btn)
        button_row.addWidget(self._pause_btn)
        button_row.addStretch()

        self._layout.addWidget(self._iteration_label)
        self._layout.addWidget(self._targets_label)
        self._layout.addWidget(self._status_label)
        self._layout.addLayout(button_row)
        self._layout.addStretch()

        # Connect to module-level state
        _state.iteration_changed.connect(self._on_iteration_changed)
        _state.targets_received.connect(self._on_targets_received)
        _state.status_changed.connect(self._on_status_changed)

    def _on_iteration_changed(self, i: int) -> None:
        self._iteration_label.setText(f"Iteration: {i}")

    def _on_targets_received(self, n: int) -> None:
        self._targets_label.setText(f"Last batch: {n}")

    def _on_status_changed(self, msg: str) -> None:
        self._status_label.setText(f"Status: {msg}")

    def _on_stop_clicked(self) -> None:
        _state.stop_requested = True
        self._stop_btn.setEnabled(False)
        self._status_label.setText("Status: stopping\u2026")

    def _on_pause_toggled(self, checked: bool) -> None:
        _state.pause_requested = checked
        self._pause_btn.setText("Resume" if checked else "Pause")


def _get_tiled_credentials() -> tuple[str, str | None, str | None]:
    """Pull Tiled URL, API key, and proxy URL from LUCID services.

    The API key is the LUCID-minted Tiled session key sourced from
    :class:`SessionManager`'s per-service cache.

    Returns:
        (tiled_url, tiled_api_key, proxy_url) — any may be empty/None.
    """
    tiled_url = ""
    tiled_api_key: str | None = None
    proxy_url = None

    try:
        from lucid.core.services import ServiceRegistry
        from lucid.services.tiled_service import TiledService
        registry = ServiceRegistry.get_instance()
        ts = registry.get(TiledService, None)
        if ts and ts.config:
            tiled_url = ts.config.url or ""
    except Exception:
        pass

    try:
        from lucid.auth.session import SessionManager
        tiled_api_key = SessionManager.get_instance().get_api_key("tiled")
    except Exception:
        pass

    # Check if URL needs proxy (*.lbl.gov → SOCKS proxy)
    if tiled_url and ".lbl.gov" in tiled_url:
        proxy_url = "socks5://localhost:1080"

    return tiled_url, tiled_api_key, proxy_url


def _get_lucid_prefix() -> str:
    """Pull the NATS topic prefix from LUCID's IPCService."""
    try:
        from lucid.ipc.service import get_ipc_service
        ipc = get_ipc_service()
        if ipc and ipc._topic_prefix:
            return ipc._topic_prefix
    except Exception:
        pass
    return ""


@plan_with_ui(AdaptiveExperimentPanel)
def adaptive_experiment(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motors: Annotated[list[Motor], DeviceFilter(category="motor")],
    exhaust_first: bool = False,
    timeout: float = 300.0,
    poll_interval: float = 0.1,
) -> Generator[Any, Any, Any]:
    """GP-driven adaptive measurement plan.

    Waits for measurement targets from Tsuchinoko via NATS, executes them,
    and signals back when each point is measured. Opens a single bluesky
    Run for the entire experiment.

    Tiled credentials and LUCID's NATS prefix are pulled automatically
    from LUCID's services and forwarded to Tsuchinoko via the
    ``bind_run`` NATS handshake.

    Args:
        detectors: Detectors to read at each target.
        motors: Motors to move. Target tuples align with motor order.
        exhaust_first: If True, measure all targets in a batch before
            publishing adaptive.measured. If False (default), publish after
            each measurement so Tsuchinoko can update its GP per-point.
        timeout: Seconds to wait for new targets before aborting.
        poll_interval: Seconds between NATS queue polls.

    Yields:
        Bluesky plan messages.
    """
    from lucid.acquire.nats_bridge import NATSPlanBridge
    from lucid.ipc.service import get_ipc_service

    # Reset module-level state for this run
    _state.stop_requested = False
    _state.pause_requested = False
    _state.current_iteration = 0

    ipc = get_ipc_service()
    if ipc is None:
        raise RuntimeError("NATS not available \u2014 adaptive plan requires IPC")

    lucid_prefix = _get_lucid_prefix()
    experiment_id = str(uuid.uuid4())

    bridge = NATSPlanBridge(ipc)
    bridge.subscribe("tsuchinoko.targets")

    try:
        md = {"tsuchinoko": {"experiment_id": experiment_id}}
        run_uid = yield from bps.open_run(md=md)

        # Pull Tiled credentials from LUCID and forward to Tsuchinoko.
        # NOTE: payload field renamed auth_token → tiled_api_key as part of
        # LUCID Auth v2. The tsuchinoko-side executor still consumes the
        # old field name; it migrates in a separate repo plan, so adaptive
        # jobs are broken until that ships — coordinated cutover required.
        tiled_url, tiled_api_key, proxy_url = _get_tiled_credentials()
        bridge.publish("tsuchinoko.experiment.bind_run", {
            "run_uid": run_uid,
            "tiled_url": tiled_url,
            "tiled_api_key": tiled_api_key,
            "proxy_url": proxy_url,
            "lucid_prefix": lucid_prefix,
            "motor_names": [m.name for m in motors],
            "detector_name": detectors[0].name if detectors else "det",
        })
        # Brief pause for Tsuchinoko to process bind_run before we proceed
        yield from bps.sleep(0.5)

        deadline = time.monotonic() + timeout

        while not _state.stop_requested:
            while _state.pause_requested and not _state.stop_requested:
                yield from bps.sleep(poll_interval)

            msg = bridge.try_get("tsuchinoko.targets")
            if msg is None:
                if time.monotonic() > deadline:
                    _state.status_changed.emit("Timeout waiting for targets")
                    logger.warning("adaptive_experiment: timeout")
                    break
                yield from bps.sleep(poll_interval)
                continue

            targets = msg.get("targets", [])
            iteration = msg.get("iteration", _state.current_iteration + 1)
            _state.current_iteration = iteration
            _state.iteration_changed.emit(iteration)
            _state.targets_received.emit(len(targets))

            for target in targets:
                if _state.stop_requested:
                    break
                # Interleave motors and target values for bps.mv
                args: list[Any] = []
                for motor, value in zip(motors, target, strict=False):
                    args.append(motor)
                    args.append(value)
                yield from bps.mv(*args)
                yield from bps.trigger_and_read(list(motors) + list(detectors), name="primary")

                if not exhaust_first:
                    bridge.publish(f"{lucid_prefix}.adaptive.measured", {
                        "iteration": iteration,
                        "n_new_points": 1,
                    })

            if exhaust_first and not _state.stop_requested:
                bridge.publish(f"{lucid_prefix}.adaptive.measured", {
                    "iteration": iteration,
                    "n_new_points": len(targets),
                })

            deadline = time.monotonic() + timeout

        yield from bps.close_run()
    finally:
        bridge.cleanup()
