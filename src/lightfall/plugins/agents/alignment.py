"""Beamline alignment skill plugin.

Provides Claude with expertise for motor alignment and beam optimization tasks.
"""

from __future__ import annotations

from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin


class BeamlineAlignmentAgent(AgentPlugin):
    """Skill for beamline alignment and beam optimization.

    This skill provides Claude with domain expertise for:
    - Motor alignment procedures
    - Beam optimization strategies
    - Safe movement practices
    - Feedback signal interpretation
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this skill."""
        return "alignment"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return "Beamline Alignment"

    @property
    def description(self) -> str:
        """Return description of this skill's capabilities."""
        return "Expertise in motor alignment and beam optimization procedures"

    @property
    def category(self) -> str:
        """Return category for grouping in settings UI."""
        return "operations"

    @property
    def enabled_by_default(self) -> bool:
        """Return whether this skill is enabled by default."""
        return True

    @property
    def priority(self) -> int:
        """Return priority (lower = higher in prompt order)."""
        return 10

    def get_system_prompt(self) -> str:
        """Return the system prompt snippet for alignment expertise."""
        return """
## Beamline Alignment Expertise

When helping with motor alignment and beam optimization:

### Safety First
- Always check current motor positions before suggesting moves
- Recommend small incremental moves for fine alignment (typically 0.1-1% of range)
- Warn about potential collisions or limit violations
- Suggest monitoring beam intensity feedback during moves

### Alignment Procedures
- Start with coarse alignment using larger step sizes
- Progressively reduce step size as you approach optimal position
- Use systematic approaches: align upstream components before downstream
- Consider the beam path and component dependencies

### Optimization Strategies
- For maximizing flux: use peak-finding algorithms when available
- For beam centering: scan both horizontal and vertical axes
- Monitor multiple feedback signals when possible (flux, position, profile)
- Document the alignment procedure and final positions

### Common Patterns
- Monochromator alignment: typically pitch and roll adjustments
- Mirror alignment: height and angle (pitch/yaw) adjustments
- Slit alignment: gap and center position
- Sample alignment: x, y, z positioning plus rotation as needed

### Communication
- Explain the rationale for each suggested move
- Report current positions and target positions clearly
- Indicate confidence level in alignment quality
- Suggest verification methods after alignment
"""

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
