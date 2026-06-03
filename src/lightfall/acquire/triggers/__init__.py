"""Trigger framework — engine-agnostic dispatch for pipeline submissions."""
from lightfall.acquire.triggers.base import Trigger
from lightfall.acquire.triggers.filter import FilterPredicate
from lightfall.acquire.triggers.manager import TriggerManager
from lightfall.acquire.triggers.manual import ManualTrigger
from lightfall.acquire.triggers.run_end import RunEndTrigger
from lightfall.acquire.triggers.run_start import RunStartTrigger

__all__ = [
    "Trigger",
    "FilterPredicate",
    "TriggerManager",
    "RunStartTrigger",
    "RunEndTrigger",
    "ManualTrigger",
]
