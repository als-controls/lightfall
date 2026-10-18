"""Ordered registry of setup steps. W1 registers Facility, Network, Sign in, Assistant."""
from __future__ import annotations

from lightfall.setup.step import SetupStep


class SetupRegistry:
    def __init__(self) -> None:
        self._steps: list[SetupStep] = []

    def register(self, step: SetupStep) -> None:
        if not isinstance(step, SetupStep):
            raise TypeError(f"{step!r} is not a SetupStep")
        if any(existing.id == step.id for existing in self._steps):
            raise ValueError(f"setup step {step.id!r} is already registered")
        self._steps.append(step)

    def steps(self) -> list[SetupStep]:
        return list(self._steps)

    def __len__(self) -> int:
        return len(self._steps)
