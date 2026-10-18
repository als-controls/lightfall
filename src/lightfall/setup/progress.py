"""Setup completion flag and saved progress (user config layer)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lightfall.config.manager import ConfigManager

SETUP_COMPLETED_KEY = "setup.completed"
SETUP_PROGRESS_KEY = "setup.progress"


@dataclass
class SetupProgress:
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def mark_completed(self, step_id: str) -> None:
        if step_id in self.skipped:
            self.skipped.remove(step_id)
        if step_id not in self.completed:
            self.completed.append(step_id)

    def mark_skipped(self, step_id: str) -> None:
        if step_id in self.completed:
            self.completed.remove(step_id)
        if step_id not in self.skipped:
            self.skipped.append(step_id)

    def to_dict(self) -> dict[str, list[str]]:
        return {"completed": list(self.completed), "skipped": list(self.skipped)}


def _ids(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def is_setup_completed(config: ConfigManager) -> bool:
    return config.get(SETUP_COMPLETED_KEY) is True


def mark_setup_completed(config: ConfigManager) -> None:
    config.set(SETUP_COMPLETED_KEY, True, persist=True)


def load_progress(config: ConfigManager) -> SetupProgress:
    raw = config.get(SETUP_PROGRESS_KEY)
    if not isinstance(raw, dict):
        return SetupProgress()
    return SetupProgress(completed=_ids(raw.get("completed")), skipped=_ids(raw.get("skipped")))


def save_progress(config: ConfigManager, progress: SetupProgress) -> None:
    config.set(SETUP_PROGRESS_KEY, progress.to_dict(), persist=True)
