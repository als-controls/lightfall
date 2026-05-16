"""Trigger framework — engine-agnostic dispatch for pipeline submissions."""
from lucid.acquire.triggers.base import Trigger
from lucid.acquire.triggers.filter import FilterPredicate
from lucid.acquire.triggers.manager import TriggerManager

__all__ = ["Trigger", "FilterPredicate", "TriggerManager"]
