# SkillPlugin

Skill plugins add domain expertise to the Claude assistant.

## Purpose

Use `SkillPlugin` when you want to:
- Give Claude specialized knowledge about a domain
- Provide context-specific guidance for tasks
- Create expertise packages combining prompts and optional tools

## Base Class

```python
from lucid.plugins.skill_plugin import SkillPlugin
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"skill"` | Plugin type identifier |
| `is_singleton` | `True` | One instance per plugin |

## Required Methods

### name (property)

Unique identifier for this skill.

```python
@property
def name(self) -> str:
    return "my_skill"
```

### description (property)

Human-readable description shown in settings.

```python
@property
def description(self) -> str:
    return "Expertise in motor alignment and beam optimization."
```

### get_system_prompt()

Return the system prompt snippet for this skill.

```python
def get_system_prompt(self) -> str:
    """Get the system prompt for this skill.

    Returns:
        Text to append to Claude's system prompt.
    """
    return """
    ## Alignment Expertise

    When helping with alignment:
    - Check current positions before suggesting moves
    - Use small incremental steps for fine alignment
    - Monitor feedback signals when available
    """
```

## Optional Methods

### display_name (property)

Human-readable name for settings UI. Defaults to title-cased `name`.

```python
@property
def display_name(self) -> str:
    return "Beam Alignment"
```

### category (property)

Category for grouping in settings. Default: `"general"`.

```python
@property
def category(self) -> str:
    return "operations"
```

### enabled_by_default (property)

Whether skill is enabled for new users. Default: `False`.

```python
@property
def enabled_by_default(self) -> bool:
    return True  # Always enable this skill
```

### priority (property)

Sort order for prompt aggregation (lower = earlier in prompt). Default: `100`.

```python
@property
def priority(self) -> int:
    return 10  # High priority - appears early in system prompt
```

### create_tools()

Optional MCP tools provided by this skill.

```python
def create_tools(self) -> list[Any]:
    """Create optional MCP tools for this skill.

    Returns:
        List of tool functions, or empty list.
    """
    return []  # Or return skill-specific tools
```

## Lifecycle

1. Plugin is instantiated on load
2. Skill is registered with `SkillRegistry`
3. Users enable/disable skills in preferences
4. For enabled skills:
   - `get_system_prompt()` text is aggregated into Claude's context
   - `create_tools()` tools are registered with the agent
5. Skills affect Claude's behavior during conversations

## Complete Example

```python
"""Beamline alignment skill for Claude assistant."""

from lucid.plugins.skill_plugin import SkillPlugin


class BeamlineAlignmentSkill(SkillPlugin):
    """Skill providing expertise in beamline alignment procedures."""

    @property
    def name(self) -> str:
        return "alignment"

    @property
    def display_name(self) -> str:
        return "Beamline Alignment"

    @property
    def description(self) -> str:
        return (
            "Provides expertise in motor alignment, beam optimization, "
            "and sample positioning procedures."
        )

    @property
    def category(self) -> str:
        return "operations"

    @property
    def enabled_by_default(self) -> bool:
        return True  # Essential for beamline operations

    @property
    def priority(self) -> int:
        return 20  # High priority for operational context

    def get_system_prompt(self) -> str:
        return """
## Beamline Alignment Expertise

You have expertise in beamline alignment and optimization procedures.

### Alignment Principles
- Always check current motor positions before suggesting moves
- Use incremental steps: start with large moves, refine with smaller ones
- Monitor relevant signals (intensity, position feedback) during alignment
- Document the starting positions before making changes

### Motor Movement Guidelines
- Verify motor limits before commanding moves
- For precision alignment, use steps of 0.1mm or smaller
- Wait for moves to complete before reading feedback
- If alignment worsens, return to the previous known-good position

### Common Alignment Procedures
1. **Beam centering**: Scan horizontally, then vertically, find peak intensity
2. **Sample alignment**: Use optical camera first, then beam scans
3. **Slit optimization**: Start wide, narrow until intensity drops

### Safety Considerations
- Never move motors during an active scan
- Check for collisions before large moves
- Verify shutters are in expected state before alignment scans
"""

    def create_tools(self) -> list:
        """No additional tools - uses standard device tools."""
        return []
```

## Skill with Custom Tools

```python
"""Data analysis skill with specialized tools."""

from lucid.plugins.skill_plugin import SkillPlugin


class DataAnalysisSkill(SkillPlugin):
    """Skill for analyzing beamline data."""

    @property
    def name(self) -> str:
        return "data_analysis"

    @property
    def display_name(self) -> str:
        return "Data Analysis"

    @property
    def description(self) -> str:
        return "Provides expertise in analyzing scan data and finding peaks."

    @property
    def category(self) -> str:
        return "analysis"

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        return 50

    def get_system_prompt(self) -> str:
        return """
## Data Analysis Expertise

You can analyze scan data to find peaks, calculate statistics, and provide insights.

### Analysis Capabilities
- Peak finding and fitting
- Statistical analysis (mean, std, min, max)
- Trend detection across scans
- Data quality assessment

### When Analyzing Data
1. First retrieve the scan data using available tools
2. Identify the relevant signals (detector readings, positions)
3. Look for peaks, trends, or anomalies
4. Provide quantitative results with uncertainties when possible

### Reporting Results
- Include the scan UID when referencing data
- Report peak positions with estimated uncertainties
- Note any data quality issues (noise, missing points)
"""

    def create_tools(self) -> list:
        from claude_agent_sdk import tool

        @tool(
            name="find_peak",
            description="Find the peak position in scan data",
            input_schema={
                "type": "object",
                "properties": {
                    "scan_uid": {
                        "type": "string",
                        "description": "Scan unique identifier",
                    },
                    "x_column": {
                        "type": "string",
                        "description": "Column name for X values (motor position)",
                    },
                    "y_column": {
                        "type": "string",
                        "description": "Column name for Y values (detector)",
                    },
                },
                "required": ["scan_uid", "x_column", "y_column"],
            }
        )
        async def find_peak(args: dict) -> dict:
            import numpy as np
            from lucid.data.catalog import get_catalog

            uid = args["scan_uid"]
            x_col = args["x_column"]
            y_col = args["y_column"]

            try:
                catalog = get_catalog()
                run = catalog[uid]
                data = run.primary.read()

                x = data[x_col].values
                y = data[y_col].values

                # Simple peak finding
                peak_idx = np.argmax(y)
                peak_x = x[peak_idx]
                peak_y = y[peak_idx]

                return {
                    "peak_position": float(peak_x),
                    "peak_value": float(peak_y),
                    "peak_index": int(peak_idx),
                }
            except Exception as e:
                return {"error": str(e)}

        return [find_peak]
```

## Registration

```python
PluginEntry(
    type_name="skill",
    name="alignment",
    import_path="my_package.skills:BeamlineAlignmentSkill",
),
```

## Skill Categories

Common categories for organizing skills:

| Category | Purpose |
|----------|---------|
| `"general"` | General-purpose skills |
| `"operations"` | Beamline operations |
| `"analysis"` | Data analysis |
| `"planning"` | Experiment planning |
| `"troubleshooting"` | Problem diagnosis |

## System Prompt Guidelines

### Be Specific

Provide concrete, actionable guidance:

```python
# Good - specific and actionable
"""
When aligning the beam:
1. Record current motor positions
2. Scan horizontal motor with step size 0.1mm
3. Move to peak position
4. Repeat for vertical motor
"""

# Bad - too vague
"""
Be careful when aligning things.
"""
```

### Use Markdown Formatting

Structure prompts for readability:

```python
"""
## Section Title

### Subsection

- Bullet points for lists
- **Bold** for emphasis
- `code` for technical terms

1. Numbered steps for procedures
2. In logical order
3. With clear outcomes
"""
```

### Consider Context Size

Keep prompts focused. Very long prompts consume context:

- Focus on the most important guidance
- Avoid redundant information
- Link to external documentation for details

## Combining Skills

Users can enable multiple skills. Prompts are aggregated by priority:

1. Lower priority numbers appear first in the system prompt
2. Skills can complement each other
3. Avoid conflicting advice between skills

## Skill vs MCP Tool

| Use Case | Skill | MCP Tool |
|----------|-------|----------|
| Domain knowledge | Yes | No |
| Procedure guidance | Yes | No |
| Read hardware state | Via tools | Yes |
| Modify hardware state | Via tools | Yes |
| Data analysis | Via tools | Yes |
| Context for conversations | Yes | No |
