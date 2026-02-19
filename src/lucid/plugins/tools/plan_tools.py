"""MCP tools for user plan management.

Provides tools for Claude to create and manage user-defined Bluesky plans.
Plans are saved to ~/lucid/plans/ and automatically loaded by the UserPlanService.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from lucid.plugins.mcp_tool import MCPToolPlugin
from lucid.utils.logging import logger


class PlanToolPlugin(MCPToolPlugin):
    """MCP tools for creating and managing user plans.

    This plugin provides tools for Claude to:
    - Create new user plans from Python code
    - (Future) List, get, and delete user plans
    """

    @property
    def name(self) -> str:
        """Plugin name."""
        return "plan_tools"

    @property
    def description(self) -> str:
        """Human-readable description of what this plugin provides."""
        return "Tools for creating and managing user plans"

    @property
    def category(self) -> str:
        """Category for grouping in settings UI."""
        return "acquisition"

    def _validate_plan_code(self, code: str, name: str) -> tuple[bool, str | None]:
        """Validate plan code without writing to disk.

        Performs in-memory validation:
        1. Syntax check via compile()
        2. Execution in isolated namespace to check imports
        3. Verify 'plan' callable exists
        4. Verify 'plan' is a generator function

        Args:
            code: Python source code for the plan.
            name: Plan name (for error messages).

        Returns:
            Tuple of (is_valid, error_message). error_message is None if valid.
        """
        # 1. Syntax check
        try:
            compile(code, f"{name}.py", "exec")
        except SyntaxError as e:
            return False, f"Syntax error at line {e.lineno}: {e.msg}"

        # 2. Execute in isolated namespace to check for import errors
        namespace: dict[str, Any] = {}
        try:
            exec(code, namespace)
        except Exception as e:
            return False, f"Execution error: {type(e).__name__}: {e}"

        # 3. Check for 'plan' callable
        if "plan" not in namespace:
            return False, "Missing required 'plan' function"

        if not callable(namespace["plan"]):
            return False, "'plan' must be a callable (function)"

        # 4. Check it's a generator function
        if not inspect.isgeneratorfunction(namespace["plan"]):
            return False, "'plan' must be a generator function (use 'yield from')"

        return True, None

    def create_tools(self) -> list[Any]:
        """Create plan management MCP tools.

        Returns:
            List of tool functions.
        """
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, plan tools disabled")
            return []

        @tool(
            name="ncs_create_user_plan",
            description="""Create a LUCID user plan from Python code.

A valid LUCID plan file must:
- Be valid Python syntax
- Export a callable named `plan` (generator function)
- Include a module docstring describing the plan
- Use proper type hints for UI widget generation (see below)

## Type Hints for UI Generation

LUCID auto-generates parameter UI from type hints. Use `typing.Annotated` with
annotations from `lucid.ui.annotations` for proper device selection widgets.

### Required imports:
```python
from __future__ import annotations
from typing import Annotated, Any, Generator
import bluesky.plans as bp
from lucid.ui.annotations import DeviceFilter, DeviceDefault, Unit, Decimals, Range
```

### Device Parameters (IMPORTANT for device selection UI):

Single device (motor/positioner) - use parameter name OR DeviceFilter:
```python
motor: Annotated[Any, DeviceFilter(category="motor")]
# Or with pre-selection:
motor: Annotated[Any, DeviceFilter(category="motor"), DeviceDefault("sample_x")]
```

Multiple devices (detectors) - use list type:
```python
detectors: Annotated[list[Any], DeviceFilter(category="detector")]
# Or filter by device class:
detectors: Annotated[list[Any], DeviceFilter(device_class="AreaDetector")]
```

DeviceFilter options:
- category: "motor", "detector", "sensor", "positioner", etc.
- device_class: specific class like "EpicsMotor", "AreaDetector"
- group: tag group like "areadetectors"
- name_pattern: regex pattern for device names

### Numeric Parameters (for formatted input):
```python
energy: Annotated[float, Unit("eV")]
position: Annotated[float, Unit("mm"), Decimals(3)]
num_points: Annotated[int, Range(min=1, max=1000)]
exposure: Annotated[float, Unit("s"), Range(min=0.001, max=60.0)] = 1.0
```

### Complete Example:
```python
\"\"\"grid_scan - 2D grid scan with detector.\"\"\"
from __future__ import annotations
from typing import Annotated, Any, Generator
import bluesky.plans as bp
from lucid.ui.annotations import DeviceFilter, DeviceDefault, Unit, Range

def plan(
    detectors: Annotated[list[Any], DeviceFilter(category="detector")],
    motor_x: Annotated[Any, DeviceFilter(category="motor")],
    motor_y: Annotated[Any, DeviceFilter(category="motor")],
    x_start: Annotated[float, Unit("mm")] = -5.0,
    x_stop: Annotated[float, Unit("mm")] = 5.0,
    y_start: Annotated[float, Unit("mm")] = -5.0,
    y_stop: Annotated[float, Unit("mm")] = 5.0,
    num_x: Annotated[int, Range(min=1, max=100)] = 10,
    num_y: Annotated[int, Range(min=1, max=100)] = 10,
) -> Generator[Any, Any, Any]:
    \"\"\"Perform a 2D grid scan.

    Args:
        detectors: Detectors to read at each point.
        motor_x: Motor for X axis.
        motor_y: Motor for Y axis.
        x_start: X axis start position.
        x_stop: X axis end position.
        y_start: Y axis start position.
        y_stop: Y axis end position.
        num_x: Number of X points.
        num_y: Number of Y points.
    \"\"\"
    yield from bp.grid_scan(detectors, motor_y, y_start, y_stop, num_y,
                            motor_x, x_start, x_stop, num_x)
```

Plans are saved to ~/lucid/plans/ and immediately available in the Plan Runner.""",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plan name (becomes filename, e.g., 'my_scan' -> my_scan.py). Must be a valid Python identifier.",
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete Python source code for the plan file",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what the plan does (for logging/confirmation)",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "If true, overwrite existing plan with same name. Default: false",
                        "default": False,
                    },
                },
                "required": ["name", "code", "description"],
            },
        )
        async def create_user_plan(args: dict) -> dict[str, Any]:
            """Create a user plan from Python code."""
            from lucid.acquire.plans.user_plans import UserPlanService

            name = args["name"]
            code = args["code"]
            description = args["description"]
            overwrite = args.get("overwrite", False)

            # Validate name is a valid Python identifier
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
                return {
                    "success": False,
                    "error": "Invalid plan name. Must be a valid Python identifier "
                    "(letters, numbers, underscore; must start with letter or underscore).",
                }

            # Validate code in-memory before writing
            is_valid, error = self._validate_plan_code(code, name)
            if not is_valid:
                return {
                    "success": False,
                    "error": error,
                }

            # Get plans directory from UserPlanService
            try:
                service = UserPlanService.get_instance()
                plans_dir = service.get_plans_directory()
            except Exception as e:
                logger.error("Failed to get UserPlanService: {}", e)
                return {
                    "success": False,
                    "error": f"Failed to access user plans service: {e}",
                }

            file_path = plans_dir / f"{name}.py"

            # Check for existing file
            if file_path.exists() and not overwrite:
                return {
                    "success": False,
                    "error": f"Plan '{name}' already exists. Set overwrite=true to replace it.",
                }

            # Write the file
            try:
                file_path.write_text(code, encoding="utf-8")
            except Exception as e:
                logger.error("Failed to write plan file {}: {}", file_path, e)
                return {
                    "success": False,
                    "error": f"Failed to write plan file: {e}",
                }

            # The UserPlanService's file watcher will auto-detect and load the plan.
            # We can also explicitly trigger a load to ensure it's available immediately.
            try:
                service.load_plan_from_file(file_path)
            except Exception as e:
                logger.warning("Plan file written but failed to load: {}", e)
                # File was written successfully, so we return success
                # The file watcher should pick it up eventually

            logger.info(
                "Created user plan '{}': {} (overwrite={})",
                name,
                description,
                overwrite,
            )

            return {
                "success": True,
                "message": f"Plan '{name}' created successfully",
                "path": str(file_path),
                "description": description,
            }

        return [create_user_plan]
