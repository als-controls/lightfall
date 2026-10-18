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


def default_registry() -> SetupRegistry:
    """W1 steps in order: Facility, Network, Sign in, Assistant (plugins come in W3)."""
    from lightfall.setup.steps.assistant import AssistantStep
    from lightfall.setup.steps.facility import FacilityStep
    from lightfall.setup.steps.network import NetworkStep
    from lightfall.setup.steps.signin import SignInStep

    registry = SetupRegistry()
    for step in (FacilityStep(), NetworkStep(), SignInStep(), AssistantStep()):
        registry.register(step)
    return registry
