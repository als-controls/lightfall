"""ManualTrigger — invoked from the Data Browser, no engine subscription."""
from __future__ import annotations

from typing import Any

from lucid.acquire.triggers.base import Trigger


class ManualTrigger(Trigger):
    """A handle for direct, user-initiated pipeline submissions."""

    def __init__(self) -> None:
        self._manager = None

    def attach(self, manager) -> None:
        self._manager = manager

    def detach(self) -> None:
        self._manager = None

    def invoke(
        self,
        *,
        pipeline: str,
        run_uid: str,
        parameters: dict[str, Any],
        input_access_blob: dict[str, Any] | None = None,
    ) -> None:
        if self._manager is None:
            raise RuntimeError("ManualTrigger not attached to a TriggerManager")
        self._manager.fire(
            pipeline=pipeline,
            run_uid=run_uid,
            parameters=parameters,
            input_access_blob=input_access_blob,
        )
