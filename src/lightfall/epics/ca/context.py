"""
Shared CA context management for the application.

Provides a singleton-like shared context that widgets can use to connect to PVs.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from caproto.threading.client import Context

if TYPE_CHECKING:
    from caproto.threading.client import PV as CaprotoPV


class SharedContext:
    """
    Manages a shared caproto threading context for all widgets.

    This class provides a singleton pattern for the CA context to avoid
    creating multiple contexts and to share connections efficiently.

    Attributes:
        context: The underlying caproto threading Context instance.

    Example:
        >>> ctx = SharedContext.get_instance()
        >>> pv = ctx.get_pv("MY:PV:NAME")
    """

    _instance: SharedContext | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._context: Context | None = None
        self._pvs: dict[str, CaprotoPV] = {}

    @classmethod
    def get_instance(cls) -> SharedContext:
        """
        Get the singleton SharedContext instance.

        Returns:
            The shared context instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def context(self) -> Context:
        """
        Get the underlying caproto Context, creating it if necessary.

        Returns:
            The caproto threading Context.
        """
        if self._context is None:
            self._context = Context()
        return self._context

    def get_pv(self, pv_name: str) -> CaprotoPV:
        """
        Get or create a PV connection.

        Args:
            pv_name: The name of the PV to connect to.

        Returns:
            The caproto PV object.
        """
        if pv_name not in self._pvs:
            (pv,) = self.context.get_pvs(pv_name)
            self._pvs[pv_name] = pv
        return self._pvs[pv_name]

    def clear(self) -> None:
        """
        Clear all cached PVs and reset the context.

        Useful for testing or when reconfiguring the connection.
        """
        self._pvs.clear()
        self._context = None

    @classmethod
    def reset(cls) -> None:
        """
        Reset the singleton instance entirely.

        Primarily used for testing to ensure a clean state.
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance.clear()
            cls._instance = None
