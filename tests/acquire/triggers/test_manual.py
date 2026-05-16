"""Tests for ManualTrigger."""
from __future__ import annotations

from unittest.mock import MagicMock

from lucid.acquire.triggers.manager import TriggerManager
from lucid.acquire.triggers.manual import ManualTrigger


def test_manual_trigger_does_not_subscribe_engine():
    engine = MagicMock()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    trigger = ManualTrigger()
    mgr.add(trigger)
    engine.subscribe.assert_not_called()


def test_manual_trigger_invoke_fires_through_manager():
    engine = MagicMock(subscribe=MagicMock(return_value=1))
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    trigger = ManualTrigger()
    mgr.add(trigger)

    trigger.invoke(pipeline="reduce_saxs", run_uid="abc", parameters={"k": 1})

    submit.assert_called_once_with(
        pipeline="reduce_saxs", run_uid="abc", parameters={"k": 1}
    )
