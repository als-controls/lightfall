"""Trigger abstract base class.

A Trigger is something that, on some criterion, asks the TriggerManager to
fire a pipeline submission. Concrete subclasses determine the criterion
(run-start doc match, run-stop doc match, manual invocation).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightfall.acquire.triggers.manager import TriggerManager


class Trigger(ABC):
    """Base class for pipeline triggers."""

    @abstractmethod
    def attach(self, manager: TriggerManager) -> None:
        """Called when added to a manager. Subscribe to engine docs here."""

    @abstractmethod
    def detach(self) -> None:
        """Called when removed. Unsubscribe from the engine."""
