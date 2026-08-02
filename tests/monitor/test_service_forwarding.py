import pytest

from lightfall.agents.bus import AgentBus
from lightfall.monitor.models import Observation, severity_at_least
from lightfall.monitor.service import MonitorService
from tests.agents.test_bus import FakeEndpoint


def _obs(severity="warn"):
    return Observation(severity=severity, feed_name="acquisition_health", run_uid="u",
                       title="t", message="m", state_key=f"k:{severity}")


@pytest.fixture()
def service(monkeypatch):
    MonitorService.reset_instance()
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    AgentBus.reset_instance()
    svc = MonitorService.get_instance()
    yield svc
    MonitorService.reset_instance()
    AgentBus.reset_instance()


def _prime_observer_spec(monkeypatch, floor="warn"):
    from lightfall.agents.registry import AgentSpecRegistry
    from lightfall.agents.spec import AgentSpec
    from pathlib import Path
    spec = AgentSpec(name="observer", description="d", prompt="p", scope="core",
                     source_path=Path("observer.md"), forward_min_severity=floor)
    monkeypatch.setattr(AgentSpecRegistry.get_instance(), "get",
                        lambda name: spec if name == "observer" else None)


def test_severity_at_least_ordering():
    assert severity_at_least("critical", "warn")
    assert severity_at_least("warn", "warn")
    assert not severity_at_least("info", "warn")


def test_warn_batch_forwards_to_lightfall(service, monkeypatch, qtbot):
    _prime_observer_spec(monkeypatch, floor="warn")
    ep = FakeEndpoint("lightfall")
    AgentBus.get_instance().register("lightfall", ep)
    service._advisor_batch = [_obs("warn"), _obs("info")]
    monkeypatch.setattr(service, "_advisor_enabled", lambda: True)
    service._advise_async = False
    service.set_advisor(type("A", (), {"advise": lambda self, b: "check the shutter"})())
    service._flush_advisor()
    assert len(ep.received) == 1
    sender, text = ep.received[0]
    assert sender == "observer"
    assert "check the shutter" in text


def test_info_batch_does_not_forward(service, monkeypatch):
    _prime_observer_spec(monkeypatch, floor="warn")
    ep = FakeEndpoint("lightfall")
    AgentBus.get_instance().register("lightfall", ep)
    service._advisor_batch = [_obs("info")]
    monkeypatch.setattr(service, "_advisor_enabled", lambda: True)
    service._advise_async = False
    service.set_advisor(type("A", (), {"advise": lambda self, b: "minor note"})())
    service._flush_advisor()
    assert ep.received == []


def test_missing_target_degrades_to_log(service, monkeypatch):
    _prime_observer_spec(monkeypatch, floor="warn")
    service._advisor_batch = [_obs("critical")]
    monkeypatch.setattr(service, "_advisor_enabled", lambda: True)
    service._advise_async = False
    service.set_advisor(type("A", (), {"advise": lambda self, b: "urgent"})())
    service._flush_advisor()  # no endpoint registered — must not raise


def test_panel_and_toast_behavior_unchanged(service, monkeypatch, qtbot):
    _prime_observer_spec(monkeypatch)
    service._advisor_batch = [_obs("warn")]
    monkeypatch.setattr(service, "_advisor_enabled", lambda: True)
    service._advise_async = False
    service.set_advisor(type("A", (), {"advise": lambda self, b: "note"})())
    with qtbot.waitSignal(service.observation, timeout=1000):
        service._flush_advisor()
