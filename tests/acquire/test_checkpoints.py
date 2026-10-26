"""Position checkpoints: capture, persistence, comparison, restore plans, tools, panel."""

from __future__ import annotations

import asyncio
import json

import pytest
from bluesky.simulators import RunEngineSimulator
from ophyd import Component as Cpt
from ophyd import Device, SoftPositioner
from ophyd.sim import det

from lightfall.acquire import checkpoints as cpmod
from lightfall.acquire.checkpoints import (
    CheckpointStore,
    compare,
    restore_plan,
    snapshot_positions,
)


class Stage(Device):
    x = Cpt(SoftPositioner, init_pos=1.0)
    y = Cpt(SoftPositioner, init_pos=2.0)


@pytest.fixture
def devices():
    return {
        "stage": Stage(name="stage"),
        "th": SoftPositioner(name="th", init_pos=10.0),
        "det": det,
    }


@pytest.fixture
def store(tmp_path, monkeypatch, devices):
    CheckpointStore.reset_instance()
    s = CheckpointStore(path=tmp_path / "cp.jsonl")
    monkeypatch.setattr(CheckpointStore, "_instance", s)
    monkeypatch.setattr(cpmod, "_catalog_devices", lambda: devices)
    yield s
    CheckpointStore.reset_instance()


def test_snapshot_includes_sub_devices_and_skips_detectors(devices):
    positions, skipped = snapshot_positions(devices)
    assert positions == {"stage.x": 1.0, "stage.y": 2.0, "th": 10.0}
    assert skipped == []


def test_capture_persists_and_reloads(store, tmp_path):
    cp = store.capture("before scan", source="agent", reason="test")
    assert store.list()[0] is cp
    line = json.loads((tmp_path / "cp.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["positions"]["stage.x"] == 1.0
    again = CheckpointStore(path=tmp_path / "cp.jsonl")
    assert again.get(cp.id).positions == cp.positions
    assert again.get(cp.id).reason == "test"


def test_capture_emits_added(store, qtbot):
    with qtbot.waitSignal(store.added, timeout=1000) as blocker:
        store.capture("x")
    assert blocker.args[0].label == "x"


def test_compare_flags_changes(store, devices):
    cp = store.capture("start")
    devices["th"].move(12.5)
    rows = {r["name"]: r for r in compare(cp, devices)}
    assert rows["th"]["changed"] is True and rows["th"]["current"] == 12.5
    assert rows["stage.x"]["changed"] is False


def test_restore_plan_moves_back_only_named(store, devices):
    cp = store.capture("start")
    devices["th"].move(12.5)
    devices["stage"].x.move(3.0)
    msgs = RunEngineSimulator().simulate_plan(restore_plan(devices, {"th": cp.positions["th"]}))
    sets = [(m.obj.name, m.args[0]) for m in msgs if m.command == "set"]
    assert sets == [("th", 10.0)]


def test_restore_plan_rejects_unknown_names_eagerly(devices):
    with pytest.raises(KeyError):
        restore_plan(devices, {"nope": 1.0})


def test_capture_if_changed_skips_unchanged_positions(store, devices):
    first = store.capture_if_changed("count 1")
    assert first is not None
    assert store.capture_if_changed("count 2") is None
    devices["th"].move(10.5)
    assert store.capture_if_changed("scan").label == "scan"
    assert [c.label for c in store.list()] == ["scan", "count 1"]


def _run_engine_plan(store, qtbot, **submit_kwargs):
    """Drive BlueskyEngine._execute_plan with a stand-in RunEngine."""
    import bluesky.plan_stubs as bps

    from lightfall.acquire.engine.base import PrioritizedProcedure
    from lightfall.acquire.engine.bluesky import BlueskyEngine

    engine = BlueskyEngine(toast_notifications=False)
    seen = {}

    class _FakeRE:
        state = "idle"

        def __call__(self, plan, **md):
            seen.update(md)
            list(plan)

    engine._RE = _FakeRE()
    item = PrioritizedProcedure(1, bps.null(), dict(submit_kwargs), name="my plan")
    engine._execute_plan(item)
    return seen


def test_engine_checkpoints_every_plan_as_user_by_default(store, qtbot):
    md = _run_engine_plan(store, qtbot, reason="why")
    (cp,) = store.list()
    assert cp.label == "my plan" and cp.source == "user" and cp.reason == "why"
    assert md == {"reason": "why"}  # control kwargs never reach the start document


def test_engine_agent_source_and_opt_out(store, devices, qtbot):
    md = _run_engine_plan(store, qtbot, checkpoint_source="agent")
    assert store.list()[0].source == "agent" and md == {}
    devices["th"].move(11.0)
    md = _run_engine_plan(store, qtbot, checkpoint=False)
    assert len(store.list()) == 1 and md == {}


class _Engine:
    state_name = "idle"

    def __init__(self):
        self.calls = []

    def submit(self, plan, **kwargs):
        self.calls.append((list(RunEngineSimulator().simulate_plan(plan)), kwargs))
        return "proc-9"


def _tools():
    from lightfall.plugins.agents.checkpoint_tools import CheckpointToolsAgent

    return {t.name: t for t in CheckpointToolsAgent().create_tools()}


def _body(result):
    return json.loads(result["content"][0]["text"])


def test_restore_tool_restores_changed_by_default(store, devices, monkeypatch):
    engine = _Engine()
    monkeypatch.setattr("lightfall.acquire.engine.get_engine", lambda: engine)
    cp = store.capture("start")
    devices["th"].move(11.0)
    out = _body(asyncio.run(_tools()["lightfall_restore_checkpoint"].handler(
        {"checkpoint_id": cp.id, "reason": "undo"})))
    assert out == {"submitted": True, "procedure_id": "proc-9", "targets": {"th": 10.0}}
    msgs, kwargs = engine.calls[0]
    assert [(m.obj.name, m.args[0]) for m in msgs if m.command == "set"] == [("th", 10.0)]
    assert kwargs["reason"] == "undo"


def test_restore_tool_nothing_changed(store, monkeypatch):
    monkeypatch.setattr("lightfall.acquire.engine.get_engine", lambda: _Engine())
    cp = store.capture("start")
    out = _body(asyncio.run(_tools()["lightfall_restore_checkpoint"].handler({"checkpoint_id": cp.id})))
    assert out["submitted"] is False


def test_restore_tool_unknown_device(store):
    cp = store.capture("start")
    result = asyncio.run(_tools()["lightfall_restore_checkpoint"].handler(
        {"checkpoint_id": cp.id, "devices": ["ghost"]}))
    assert result["is_error"] is True


def test_list_and_create_tools(store):
    tools = _tools()
    asyncio.run(tools["lightfall_create_checkpoint"].handler({"label": "aligned"}))
    out = _body(asyncio.run(tools["lightfall_list_checkpoints"].handler({})))
    assert out["checkpoints"][0]["label"] == "aligned"
    assert out["checkpoints"][0]["positioners"] == 3


def test_panel_lists_diffs_and_restores(store, devices, qtbot, monkeypatch):
    from lightfall.ui.panels.checkpoints_panel import CheckpointsPanel

    engine = _Engine()
    monkeypatch.setattr("lightfall.acquire.engine.get_engine", lambda: engine)
    cp = store.capture("start")
    devices["th"].move(11.0)
    panel = CheckpointsPanel()
    qtbot.addWidget(panel)
    panel._show_selected()
    assert panel.selected_checkpoint().id == cp.id
    assert panel.checked_names() == ["th"]  # only the changed positioner is pre-ticked
    store.capture("later")
    assert panel._list.count() == 2
    panel._list.setCurrentRow(1)
    assert panel.restore(cp, ["th"]) == "proc-9"
