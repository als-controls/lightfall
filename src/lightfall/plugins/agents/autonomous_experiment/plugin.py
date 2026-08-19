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

    #: A skill that must exist under gpCAM's skills dir for it to count as valid.
    _SENTINEL_SKILL = Path("experiment-designer") / "SKILL.md"

    def get_references_dir(self) -> Path | None:
        """Locate gpCAM's design skills, wherever the installed gpCAM keeps them.

        gpCAM ships its ``experiment-designer`` (and sibling) skills at the
        repository root, not inside the importable package. So we try, in
        order:

        1. ``gpcam.skills`` as a package resource — the preferred layout if a
           future gpCAM release packages the skills. This path is picked up
           automatically the day that lands, with no change here.
        2. A ``skills/`` directory sibling to the ``gpcam`` package — the
           layout of a source / editable checkout today.

        Either way the skills are read *from the installed gpCAM*; nothing is
        vendored into Lightfall. Returns ``None`` when gpCAM is absent or its
        skills can't be found (the prompt then tells the user how to recover).
        """
        for candidate in self._candidate_skill_dirs():
            if (candidate / self._SENTINEL_SKILL).is_file():
                return candidate
        return None

    def _candidate_skill_dirs(self):
        # 1) Packaged as gpcam.skills (future gpCAM).
        try:
            import importlib.resources as ir
            yield Path(str(ir.files("gpcam.skills")))
        except (ImportError, ModuleNotFoundError, FileNotFoundError, TypeError):
            pass
        # 2) Sibling of the gpcam package in a source / editable install.
        try:
            import gpcam
            yield Path(gpcam.__file__).resolve().parent.parent / "skills"
        except Exception:
            pass

    def get_extra_skill_dirs(self) -> dict[str, Any]:
        """Expose gpCAM's design skills (experiment-designer + siblings) as
        independently loadable skills, sourced from the installed gpCAM.

        Materialized into the session under their own names so the Skill tool
        can lazy-load them — the workflow's Step 1 relies on this. Empty when
        gpCAM (or its skills) can't be found.
        """
        root = self.get_references_dir()
        if root is None:
            return {}
        return {
            child.name: child
            for child in sorted(Path(root).iterdir())
            if (child / "SKILL.md").is_file()
        }
