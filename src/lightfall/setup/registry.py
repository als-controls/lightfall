"""Ordered registry of setup steps. W1 registers Facility, Network, Sign in, Shortcut, Assistant."""
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
    """W1 steps in order: Facility, Network, Sign in, Shortcut, Assistant (plugins come
    in W3). Shortcut sits before Assistant so the slow Assistant check stays last (see
    ``SetupWizard._landing_index``); it is left out where shortcuts aren't supported
    (macOS) or there is no ``lightfall-gui`` to point at (e.g. a non-installed checkout)."""
    from lightfall.setup import shortcuts
    from lightfall.setup.steps.assistant import AssistantStep
    from lightfall.setup.steps.facility import FacilityStep
    from lightfall.setup.steps.network import NetworkStep
    from lightfall.setup.steps.shortcut import ShortcutStep
    from lightfall.setup.steps.signin import SignInStep

    registry = SetupRegistry()
    steps: list[SetupStep] = [FacilityStep(), NetworkStep(), SignInStep()]
    if shortcuts.is_supported_platform() and shortcuts.find_gui_script() is not None:
        steps.append(ShortcutStep())
    steps.append(AssistantStep())
    for step in steps:
        registry.register(step)
    return registry
