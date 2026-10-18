"""The setup step protocol (spec section 3).

``detect``, ``validate`` and ``apply`` contain no Qt, so W2's headless runner can reuse
them; only ``widget`` builds Qt. ``detect`` blocks (network, agy): the wizard calls it
off the GUI thread.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from lightfall.setup.context import SetupContext


class StepStatus(Enum):
    SATISFIED = "satisfied"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class StepState:
    """A step's status plus a one-line value for the rail ("Via SOCKS proxy")."""

    status: StepStatus
    value: str = ""

    @property
    def satisfied(self) -> bool:
        return self.status is StepStatus.SATISFIED


class StepError(Exception):
    """``apply`` could not finish; the message is shown on the step."""


@runtime_checkable
class SetupStep(Protocol):
    id: str
    title: str

    def detect(self, ctx: SetupContext) -> StepState: ...

    def widget(self, ctx: SetupContext) -> QWidget: ...

    def validate(self, ctx: SetupContext) -> str | None: ...

    def apply(self, ctx: SetupContext) -> None: ...
