import pytest

from lightfall.agents.bus import AgentBus


class FakeEndpoint:
    def __init__(self, name, busy=False, mode="delivered", raise_on_deliver=False):
        self.name = name
        self.description = f"d-{name}"
        self._busy = busy
        self._mode = mode
        self._raise = raise_on_deliver
        self.received: list[tuple[str, str]] = []

    def is_busy(self):
        return self._busy

    def deliver(self, sender, message):
        if self._raise:
            raise RuntimeError("boom")
        self.received.append((sender, message))
        return self._mode


@pytest.fixture()
def bus():
    AgentBus.reset_instance()
    yield AgentBus.get_instance()
    AgentBus.reset_instance()


def test_register_send_deliver(bus):
    ep = FakeEndpoint("lightfall")
    assert bus.register("lightfall", ep) == "lightfall"
    result = bus.send("observer", "lightfall", "beam looks soft")
    assert result["status"] == "delivered"
    assert ep.received == [("observer", "beam looks soft")]


def test_duplicate_names_get_suffix(bus):
    bus.register("saxs", FakeEndpoint("saxs"))
    assert bus.register("saxs", FakeEndpoint("saxs")) == "saxs#2"
    names = {a["name"] for a in bus.list_agents()}
    assert names == {"saxs", "saxs#2"}


def test_send_to_missing_agent_errors_with_roster(bus):
    bus.register("lightfall", FakeEndpoint("lightfall"))
    result = bus.send("observer", "nope", "hi")
    assert result["status"] == "error"
    assert "lightfall" in result["detail"]


def test_deliver_exception_is_contained(bus):
    bus.register("lightfall", FakeEndpoint("lightfall", raise_on_deliver=True))
    result = bus.send("observer", "lightfall", "hi")
    assert result["status"] == "error"
    assert "failed" in result["detail"]


def test_unregister_and_signal(bus, qtbot):
    ep = FakeEndpoint("x")
    with qtbot.waitSignal(bus.agents_changed, timeout=1000):
        bus.register("x", ep)
    with qtbot.waitSignal(bus.agents_changed, timeout=1000):
        assert bus.unregister("x") is True
    assert bus.list_agents() == []


def test_list_agents_reports_busy(bus):
    bus.register("a", FakeEndpoint("a", busy=True))
    assert bus.list_agents() == [{"name": "a", "description": "d-a", "busy": True}]
