"""AgentBus — in-process message bus singleton routing messages between agent endpoints."""

from __future__ import annotations

import threading
from typing import Any, Protocol

from PySide6.QtCore import QObject, Signal

from lightfall.utils.logging import logger


class BusEndpoint(Protocol):
    """Protocol for agent endpoints receiving messages via the bus."""

    name: str
    description: str

    def is_busy(self) -> bool:
        """Return True if the endpoint is currently processing a message."""
        ...

    def deliver(self, sender: str, message: str) -> str:
        """Deliver a message from sender. Returns 'delivered' or 'queued'."""
        ...


class AgentBus(QObject):
    """Singleton in-process message bus routing messages between registered agent endpoints.

    Use AgentBus.get_instance() to access. reset_instance() is for tests.
    """

    agents_changed = Signal()

    _instance: AgentBus | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self._endpoints: dict[str, BusEndpoint] = {}

    @classmethod
    def get_instance(cls) -> AgentBus:
        """Get or create the singleton instance using double-checked lock pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for tests)."""
        with cls._lock:
            cls._instance = None

    def register(self, name: str, endpoint: BusEndpoint) -> str:
        """Register an endpoint with the bus.

        On name collision, appends #2, #3, ... to create a unique name.
        Returns the actual registered name.
        Emits agents_changed.
        """
        candidate = name
        i = 2
        while candidate in self._endpoints:
            candidate = f"{name}#{i}"
            i += 1

        self._endpoints[candidate] = endpoint
        self.agents_changed.emit()
        return candidate

    def unregister(self, name: str) -> bool:
        """Unregister an endpoint by registered name.

        Returns True if the endpoint was found and unregistered, False otherwise.
        Emits agents_changed on successful unregister.
        """
        if name in self._endpoints:
            del self._endpoints[name]
            self.agents_changed.emit()
            return True
        return False

    def send(self, sender: str, to: str, message: str) -> dict[str, Any]:
        """Send a message from sender to target endpoint.

        Returns a dict with 'status' and 'detail' keys:
        - On success: {"status": <deliver's return>, "detail": ""}
        - On missing target: {"status": "error", "detail": "agent '<to>' not running; running agents: [...]"}
        - On endpoint exception: {"status": "error", "detail": "delivery to '<to>' failed: <exc>"}

        Exceptions from endpoint.deliver() are logged and never propagated.
        """
        if to not in self._endpoints:
            agent_list = sorted(self._endpoints.keys())
            return {
                "status": "error",
                "detail": f"agent '{to}' not running; running agents: {agent_list}",
            }

        endpoint = self._endpoints[to]
        try:
            status = endpoint.deliver(sender, message)
            return {"status": status, "detail": ""}
        except Exception as e:
            logger.exception("delivery to '{}' failed", to)
            return {"status": "error", "detail": f"delivery to '{to}' failed: {e}"}

    def list_agents(self) -> list[dict[str, Any]]:
        """List all registered agents.

        Returns a list of dicts with 'name', 'description', and 'busy' keys,
        sorted by name.
        """
        agents = []
        for name in sorted(self._endpoints.keys()):
            endpoint = self._endpoints[name]
            agents.append({
                "name": name,
                "description": endpoint.description,
                "busy": endpoint.is_busy(),
            })
        return agents
