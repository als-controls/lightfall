"""Trigger framework — engine-agnostic dispatch for pipeline submissions."""
from lucid.acquire.triggers.base import Trigger
from lucid.acquire.triggers.filter import FilterPredicate
from lucid.acquire.triggers.manager import TriggerManager
from lucid.acquire.triggers.manual import ManualTrigger
from lucid.acquire.triggers.run_end import RunEndTrigger
from lucid.acquire.triggers.run_start import RunStartTrigger

__all__ = [
    "Trigger",
    "FilterPredicate",
    "TriggerManager",
    "RunStartTrigger",
    "RunEndTrigger",
    "ManualTrigger",
]
