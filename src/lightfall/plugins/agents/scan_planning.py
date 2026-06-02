"""Scan planning skill plugin.

Provides Claude with expertise for planning and configuring data acquisition scans.
"""

from __future__ import annotations

from typing import Any

from lucid.plugins.agent_plugin import AgentPlugin


class ScanPlanningAgent(AgentPlugin):
    """Skill for planning and configuring scans.

    This skill provides Claude with domain expertise for:
    - Scan type selection and configuration
    - Parameter optimization
    - Time estimation
    - Data collection strategies
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this skill."""
        return "scan_planning"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return "Scan Planning"

    @property
    def description(self) -> str:
        """Return description of this skill's capabilities."""
        return "Expertise in planning and configuring data acquisition scans"

    @property
    def category(self) -> str:
        """Return category for grouping in settings UI."""
        return "analysis"

    @property
    def enabled_by_default(self) -> bool:
        """Return whether this skill is enabled by default."""
        return True

    @property
    def priority(self) -> int:
        """Return priority (lower = higher in prompt order)."""
        return 20

    def get_system_prompt(self) -> str:
        """Return the system prompt snippet for scan planning expertise."""
        return """
## Scan Planning Expertise

When helping with scan planning and data acquisition:

### Scan Type Selection
- **Linear scans**: Best for simple parameter sweeps, characterization
- **Grid scans**: For 2D mapping, finding optimal conditions
- **Spiral scans**: Efficient for circular regions, often faster than grids
- **Adaptive scans**: When you need to focus on regions of interest
- **Count scans**: For time-resolved measurements at fixed position

### Parameter Optimization
- Consider the trade-off between resolution (step size) and time
- Estimate total scan time: (num_points × dwell_time) + overhead
- Account for motor movement time in estimates
- Use coarse scans first to identify regions of interest

### Data Quality Considerations
- Dwell time: longer = better statistics, shorter = faster
- Typical ranges: 0.1s (fast survey) to 10s (high-quality data)
- Consider detector saturation limits
- Account for beam intensity variations over time

### Common Scan Patterns
- Energy scans: step through photon energies (XAS, RIXS)
- Position scans: map sample or beam position
- Angle scans: rocking curves, reflectivity
- Time scans: monitor stability or dynamics

### Best Practices
- Always specify: motor, start, stop, num_points, detectors
- Set appropriate metadata (sample name, conditions)
- Consider running a quick test scan first
- Plan for data backup and documentation

### Communication
- Summarize the scan configuration before starting
- Estimate total measurement time
- Suggest how to verify data quality during acquisition
- Recommend follow-up scans based on initial results
"""

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
