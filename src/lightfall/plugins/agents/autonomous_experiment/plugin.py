"""AutonomousExperimentAgent ToolPlugin."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.plugins.tool_plugin import ToolPlugin


class AutonomousExperimentAgent(ToolPlugin):
    """Embeds gpCAM's experiment-design skills and exposes a NATS bridge
    to a running Tsuchinoko instance.

    Together with the existing ``adaptive_experiment`` plan and the
    adaptive viz widgets, this plugin lets the embedded agent drive an
    end-to-end autonomous experiment from chat.
    """

    @property
    def name(self) -> str:
        return "autonomous_experiment"

    @property
    def display_name(self) -> str:
        return "Autonomous Experiment"

    @property
    def description(self) -> str:
        return "Design and run GP-driven adaptive experiments via Tsuchinoko"

    @property
    def category(self) -> str:
        return "acquisition"

    @property
    def priority(self) -> int:
        return 30

    @property
    def enabled_by_default(self) -> bool:
        return True

    def create_tools(self) -> list[Any]:
        from .nats_tools import build_tools
        return build_tools()

    def get_references_dir(self) -> Path | None:
        try:
            import importlib.resources as ir
            ref = ir.files("gpcam.skills")
        except (ImportError, ModuleNotFoundError, FileNotFoundError):
            return None
        try:
            return Path(str(ref))
        except Exception:
            return None
