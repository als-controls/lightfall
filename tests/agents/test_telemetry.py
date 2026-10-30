"""Agent usage telemetry: local log, totals, worker/permission hooks, OTLP mapping."""

from __future__ import annotations

import asyncio
import json

import pytest
from PySide6.QtCore import QObject, Signal

from lightfall.agents import telemetry as tmod
from lightfall.agents.telemetry import AgentTelemetry, attach_worker, summarize_log


@pytest.fixture
def tel(tmp_path, monkeypatch):
    AgentTelemetry.reset_instance()
    inst = AgentTelemetry(path=tmp_path / "usage.jsonl")
    monkeypatch.setattr(AgentTelemetry, "_instance", inst)
    monkeypatch.setattr(tmod, "_beamline", lambda: "7.0.1.1")
    yield inst
    AgentTelemetry.reset_instance()


def _lines(tel):
    return [json.loads(x) for x in tel.path.read_text(encoding="utf-8").splitlines()]


TURN = {
    "total_cost_usd": 0.25, "input_tokens": 1000, "output_tokens": 200,
    "cache_read_input_tokens": 5000, "duration_ms": 4000, "num_turns": 3,
    "session_id": "s", "text": "never logged",
}


def test_turn_is_logged_without_text(tel):
    tel.record_turn("lightfall", "claude_code", "opus", TURN)
    (rec,) = _lines(tel)
    assert rec["event"] == "turn" and rec["agent"] == "lightfall" and rec["model"] == "opus"
    assert rec["total_cost_usd"] == 0.25 and rec["cache_read_input_tokens"] == 5000
    assert rec["beamline"] == "7.0.1.1"
    assert "text" not in rec and "session_id" not in rec


def test_summary_totals(tel):
    tel.record_turn("lightfall", "claude_code", "opus", TURN)
    tel.record_turn("observer", "claude_code", "haiku", {"total_cost_usd": 0.01, "is_error": True})
    tel.record_tool("lightfall", "lightfall_run_plan", False)
    tel.record_tool("lightfall", "lightfall_run_plan", True)
    tel.record_permission("lightfall", "lightfall_run_plan", True)
    tel.record_permission("lightfall", "Bash", False)
    s = tel.summary()
    assert s["overall"]["turns"] == 2
    assert s["overall"]["turn_errors"] == 1
    assert s["overall"]["total_cost_usd"] == pytest.approx(0.26)
    assert s["agents"]["lightfall"]["tool_errors"] == 1
    assert s["agents"]["lightfall"]["permission_denied"] == 1
    assert s["otlp_export"] is False


def test_none_cost_from_agy_is_zero(tel):
    tel.record_turn("lightfall", "agy", None, {"total_cost_usd": None, "input_tokens": 10})
    assert _lines(tel)[0]["total_cost_usd"] == 0.0


class _Worker(QObject):
    result_received = Signal(dict)
    tool_called = Signal(str, dict)
    tool_result = Signal(str, dict)


def test_attach_worker_records_turns_and_tools_in_order(tel):
    w = _Worker()
    model = {"m": "sonnet"}
    attach_worker(w, agent="lightfall", backend="claude_code", model_fn=lambda: model["m"])
    w.tool_called.emit("a_tool", {})
    w.tool_called.emit("b_tool", {})
    w.tool_result.emit("id1", {"is_error": False})
    w.tool_result.emit("id2", {"is_error": True})
    model["m"] = "opus"
    w.result_received.emit(TURN)
    recs = _lines(tel)
    assert [(r["event"], r.get("tool"), r.get("is_error")) for r in recs[:2]] == [
        ("tool", "a_tool", False), ("tool", "b_tool", True)]
    assert recs[2]["event"] == "turn" and recs[2]["model"] == "opus"


def test_permission_manager_records_answers(tel, qtbot):
    from lightfall.claude.permission_manager import PermissionManager

    pm = PermissionManager()
    pm.telemetry_agent = "lightfall"
    pm.permission_requested.connect(lambda rid, *_: pm.respond(rid, False, message="no"))

    async def ask():
        return await pm.request_permission("Bash", {"command": "rm -rf /"})

    assert asyncio.run(ask())[0] is False
    (rec,) = _lines(tel)
    assert rec == {**rec, "event": "permission", "tool": "Bash", "allowed": False}
    assert "command" not in json.dumps(rec)


def test_auto_approved_tools_are_not_counted(tel):
    from lightfall.claude.permission_manager import PermissionManager

    pm = PermissionManager()
    pm.telemetry_agent = "lightfall"
    asyncio.run(pm.request_permission("Read", {}))
    assert not tel.path.exists()


def test_summarize_log_groups_by_user_agent_model(tel):
    tel.record_turn("lightfall", "claude_code", "opus", TURN)
    tel.record_turn("lightfall", "claude_code", "opus", TURN)
    tel.record_turn("observer", "claude_code", "haiku", {"total_cost_usd": 0.01})
    rows = summarize_log(tel.path)
    assert rows[0]["agent"] == "lightfall" and rows[0]["turns"] == 2
    assert rows[0]["total_cost_usd"] == pytest.approx(0.5)
    assert rows[1]["model"] == "haiku"


def test_cli_prints_table(tel, capsys):
    tel.record_turn("lightfall", "claude_code", "opus", TURN)
    assert tmod.main([str(tel.path)]) == 0
    out = capsys.readouterr().out
    assert "lightfall" in out and "0.25" in out


class _Inst:
    def __init__(self):
        self.calls = []

    def add(self, v, attrs=None):
        self.calls.append((v, attrs))

    record = add


class _Meter:
    def __init__(self):
        self.instruments = {}

    def _make(self, name, **_):
        self.instruments[name] = _Inst()
        return self.instruments[name]

    create_counter = create_histogram = _make


class _Provider:
    def __init__(self):
        self.meter = _Meter()

    def get_meter(self, _name):
        return self.meter


def test_otlp_mapping():
    from lightfall.agents.telemetry_otlp import OtlpAgentExporter

    prov = _Provider()
    ex = OtlpAgentExporter(prov)
    ex.record("turn", beamline="7.0.1.1", agent="lightfall", backend="claude_code", model="opus",
              values={"total_cost_usd": 0.25, "input_tokens": 1000, "output_tokens": 0,
                      "duration_ms": 4000}, is_error=False)
    ex.record("permission", beamline="", agent="lightfall", tool="Bash", allowed=False)
    inst = prov.meter.instruments
    assert inst["lightfall_agent_turns_total"].calls[0][1]["is_error"] == "false"
    assert inst["lightfall_agent_cost_usd_total"].calls[0][0] == 0.25
    assert inst["lightfall_agent_tokens_total"].calls == [
        (1000, {"agent": "lightfall", "beamline": "7.0.1.1", "backend": "claude_code",
                "model": "opus", "kind": "input"})]
    assert inst["lightfall_agent_turn_duration_seconds"].calls[0][0] == 4.0
    assert inst["lightfall_agent_permission_decisions_total"].calls[0][1]["outcome"] == "denied"


def test_otlp_off_without_endpoint(monkeypatch):
    from lightfall.agents import telemetry_otlp

    monkeypatch.delenv("LIGHTFALL_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(telemetry_otlp, "_pref", lambda key, default="": "")
    assert telemetry_otlp.make_exporter() is None


def test_otlp_half_credential_sends_no_header(monkeypatch):
    from lightfall.agents import telemetry_otlp

    monkeypatch.setenv("LIGHTFALL_OTLP_USERNAME", "lf_host")
    monkeypatch.delenv("LIGHTFALL_OTLP_PASSWORD", raising=False)
    assert telemetry_otlp._auth_headers() == {}


class _CancellableWorker(_Worker):
    query_cancelled = Signal()
    error_occurred = Signal(str)


def test_attach_worker_forgets_pending_tools_after_a_cancelled_turn(tel):
    w = _CancellableWorker()
    attach_worker(w, agent="lightfall", backend="claude_code", model_fn=lambda: "sonnet")
    w.tool_called.emit("orphan_tool", {})   # its result never arrives
    w.query_cancelled.emit()
    w.tool_called.emit("next_tool", {})
    w.tool_result.emit("id", {"is_error": False})
    (rec,) = _lines(tel)
    assert rec["tool"] == "next_tool"
