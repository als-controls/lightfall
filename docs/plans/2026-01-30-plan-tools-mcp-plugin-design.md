# MCP Plan Tools Plugin Design

**Date:** 2026-01-30
**Status:** Approved

## Overview

Add an MCP tool plugin that allows Claude to create LUCID user plans programmatically. This enables Claude to write Python plan code and save it directly to the user's plans directory, making it immediately available in the Plan Runner.

## Tool Interface

### `ncs_create_user_plan`

Creates a LUCID user plan from Python code.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | string | Yes | Plan name (becomes filename, e.g., 'my_scan' -> my_scan.py) |
| `code` | string | Yes | Complete Python source code for the plan file |
| `description` | string | Yes | Brief description of what the plan does |
| `overwrite` | boolean | No | If true, overwrite existing plan with same name (default: false) |

**Returns:**

```json
// Success
{"success": true, "message": "Plan 'my_scan' created successfully", "path": "/home/user/lucid/plans/my_scan.py"}

// Failure
{"success": false, "error": "Syntax error at line 5: invalid syntax"}
```

**Valid Plan Format:**

A valid LUCID plan file must:
- Be valid Python syntax
- Export a callable named `plan` (generator function)
- Include a module docstring describing the plan
- Use type hints for parameters (recommended)

```python
"""my_scan - A simple scan plan."""
from typing import Any, Generator
import bluesky.plans as bp

def plan(motor, start: float, stop: float, num: int = 10) -> Generator[Any, Any, Any]:
    """Scan a motor."""
    yield from bp.scan([], motor, start, stop, num)
```

## Validation Logic

Before writing to disk, the tool validates code in-memory:

1. **Syntax check** - Compile the code to catch syntax errors
2. **Execution check** - Execute in isolated namespace to catch import errors
3. **Plan callable check** - Verify `plan` exists and is callable
4. **Generator check** - Verify `plan` is a generator function

This approach:
- Keeps the plans directory clean (no invalid files)
- Provides actionable error messages for Claude to fix and retry
- Prevents `UserPlanService` from emitting error signals

## File Operations

1. Sanitize name (must match `^[a-zA-Z_][a-zA-Z0-9_]*$`)
2. Validate code in-memory
3. Check for existing file (fail if exists and `overwrite=false`)
4. Write to `~/lucid/plans/{name}.py`
5. `UserPlanService` file watcher auto-detects and loads the plan

## Plugin Structure

### Consolidation

Move existing MCP tool plugins from `ui/panels/claude/` to `plugins/tools/` to mirror the skills pattern:

**Before:**
```
lucid/plugins/
├── mcp_tool.py
├── skill_plugin.py
└── skills/
    └── ...

lucid/ui/panels/claude/
├── device_tools.py    # MCPToolPlugin impl
├── ncs_tools.py       # MCPToolPlugin impl
└── tool_registry.py
```

**After:**
```
lucid/plugins/
├── mcp_tool.py
├── skill_plugin.py
├── skills/
│   └── ...
└── tools/              # NEW
    ├── __init__.py
    ├── device_tools.py
    ├── ncs_tools.py
    └── plan_tools.py   # NEW

lucid/ui/panels/claude/
└── tool_registry.py    # Stays (tied to UI)
```

### New Plugin Class

```python
class PlanToolPlugin(MCPToolPlugin):
    @property
    def name(self) -> str:
        return "plan_tools"

    @property
    def tool_description(self) -> str:
        return "Tools for creating and managing user plans"

    def create_tools(self) -> list[Any]:
        # Returns [create_user_plan] tool
        ...
```

## Implementation Steps

1. Create `lucid/plugins/tools/` directory with `__init__.py`
2. Move `device_tools.py` from `ui/panels/claude/` to `plugins/tools/`
3. Move `ncs_tools.py` from `ui/panels/claude/` to `plugins/tools/`
4. Update any relative imports in moved files
5. Create `plan_tools.py` with `PlanToolPlugin` and `ncs_create_user_plan` tool
6. Update `builtin_manifest.py` with new import paths for all three plugins
7. Delete old files from `ui/panels/claude/`
8. Test that all tools register and function correctly

## Future Extensions

The `PlanToolPlugin` can later include additional tools:
- `ncs_list_user_plans` - List available user plans
- `ncs_get_user_plan` - Read plan source code
- `ncs_delete_user_plan` - Remove a user plan
- `ncs_validate_plan_code` - Validate without saving
