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

    def _get_catalog(self):
        """Get the device catalog instance."""
        from lucid.devices import DeviceCatalog

        return DeviceCatalog.get_instance()

    def _resolve_plan_params(
        self, plan_info: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve string device names to actual ophyd device objects.

        Inspects plan parameters and resolves string device names
        via the DeviceCatalog.

        Args:
            plan_info: PlanInfo with parameter metadata.
            params: Raw parameters dict (device names as strings).

        Returns:
            Dict with device strings replaced by ophyd device objects.
        """
        resolved = dict(params)

        try:
            catalog = self._get_catalog()
        except Exception:
            return resolved

        for p_info in plan_info.parameters:
            if p_info.name not in resolved:
                continue

            val = resolved[p_info.name]

            # List of strings → resolve each as a device
            if isinstance(val, list) and all(isinstance(v, str) for v in val):
                devices = []
                for dev_name in val:
                    dev = catalog.get_ophyd_device(dev_name)
                    if dev is None:
                        raise ValueError(f"Device '{dev_name}' not found in catalog")
                    devices.append(dev)
                resolved[p_info.name] = devices
            elif isinstance(val, str):
                # Try to resolve as a device; if not found, leave as string
                dev = catalog.get_ophyd_device(val)
                if dev is not None:
                    resolved[p_info.name] = dev

        return resolved

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

        @tool(
            name="ncs_list_plans",
            description="""List all registered plans available in the LUCID plan registry.

Returns plan names, categories, descriptions, and parameter signatures.
Use this to discover what plans are available before running one with ncs_run_plan.

Optionally filter by category (e.g., "scan", "count", "alignment", "user").""",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Optional category filter (e.g., 'scan', 'count', 'alignment', 'user', 'general')",
                    },
                },
            },
        )
        async def list_plans(args: dict) -> dict[str, Any]:
            """List all registered plans."""
            import json

            from lucid.acquire.plans.registry import get_registry

            registry = get_registry()
            category = args.get("category")
            plans = registry.list_plans(category=category)

            result = []
            for plan_info in plans:
                params = []
                for p in plan_info.parameters:
                    param = {
                        "name": p.name,
                        "type": p.type_name,
                        "required": p.required,
                    }
                    if p.description:
                        param["description"] = p.description
                    if not p.required and p.default is not inspect.Parameter.empty:
                        param["default"] = repr(p.default)
                    params.append(param)

                result.append({
                    "name": plan_info.name,
                    "display_name": plan_info.get_display_name(),
                    "category": plan_info.category,
                    "description": plan_info.description,
                    "parameters": params,
                })

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"plans": result, "total": len(result)}, indent=2),
                }]
            }

        @tool(
            name="ncs_run_plan",
            description="""Run a registered plan from the LUCID plan registry by name.

Use ncs_list_plans first to see available plans and their parameters.
The plan is submitted to the RunEngine queue and executed asynchronously.

Parameters are passed as a JSON object. Device parameters should use device
names as strings — they will be resolved from the device registry automatically.

Example:
  ncs_run_plan(plan_name="scan", params={"detectors": ["det1"], "motor": "motor1", "start": -5, "stop": 5, "num": 21})
  ncs_run_plan(plan_name="count", params={"detectors": ["det1"], "num": 5, "delay": 1.0})""",
            input_schema={
                "type": "object",
                "properties": {
                    "plan_name": {
                        "type": "string",
                        "description": "Name of the registered plan to run (e.g., 'scan', 'count', 'rel_scan')",
                    },
                    "params": {
                        "type": "object",
                        "description": "Parameters to pass to the plan function. Device names (strings) are resolved automatically.",
                    },
                },
                "required": ["plan_name"],
            },
        )
        async def run_plan(args: dict) -> dict[str, Any]:
            """Run a registered plan by name."""
            import json

            from lucid.acquire.engine import get_engine
            from lucid.acquire.plans.registry import get_registry

            plan_name = args["plan_name"]
            params = args.get("params", {})

            # Look up the plan
            registry = get_registry()
            plan_info = registry.get_plan(plan_name)
            if plan_info is None:
                available = registry.plan_names
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": f"Plan '{plan_name}' not found",
                            "available_plans": available,
                        }),
                    }],
                    "is_error": True,
                }

            # Resolve device names to device objects
            resolved_params = {}
            try:
                resolved_params = self._resolve_plan_params(plan_info, params)
            except Exception as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": f"Parameter resolution error: {e}",
                        }),
                    }],
                    "is_error": True,
                }

            # Create the plan generator and submit
            try:
                engine = get_engine()
                plan_generator = plan_info.func(**resolved_params)
                proc_id = engine.submit(plan_generator, name=plan_name)
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": True,
                            "message": f"Plan '{plan_name}' submitted to RunEngine",
                            "procedure_id": proc_id,
                            "engine_state": engine.state_name,
                        }),
                    }]
                }
            except Exception as e:
                logger.error("Failed to submit plan '{}': {}", plan_name, e)
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": f"Failed to submit plan: {e}",
                        }),
                    }],
                    "is_error": True,
                }

        @tool(
            name="ncs_run_plan_code",
            description="""Run arbitrary Python code as a Bluesky plan in the LUCID RunEngine.

The code string is executed in an isolated namespace with common imports available.
The code MUST define a generator (using yield from) that produces Bluesky messages.

The code is wrapped in a function and executed — you write the body of a generator function.

Pre-imported in the execution namespace:
- bluesky.plans as bp
- bluesky.plan_stubs as bps
- All devices from the device registry (by name)
- numpy as np

Example code strings:
  "yield from bp.scan([det], motor1, -5, 5, 21)"
  "yield from bp.count([det], num=5, delay=1.0)"
  "for i in range(3):\\n    yield from bp.scan([det], motor1, -i, i, 11)"

WARNING: This executes arbitrary code in the RunEngine context. Use with caution.""",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code string that yields Bluesky plan messages. Written as the body of a generator function.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what this plan does (for logging and queue display)",
                    },
                },
                "required": ["code"],
            },
        )
        async def run_plan_code(args: dict) -> dict[str, Any]:
            """Run arbitrary code as a Bluesky plan."""
            import json
            import textwrap

            from lucid.acquire.engine import get_engine

            code = args["code"]
            description = args.get("description", "ad-hoc plan")

            # Build the execution namespace with common imports
            namespace: dict[str, Any] = {}
            try:
                import numpy as np
                namespace["np"] = np
                namespace["numpy"] = np
            except ImportError:
                pass

            try:
                import bluesky.plan_stubs as bps
                import bluesky.plans as bp
                namespace["bp"] = bp
                namespace["bps"] = bps
            except ImportError:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": "bluesky is not installed",
                        }),
                    }],
                    "is_error": True,
                }

            # Inject all ophyd devices from the device catalog
            try:
                catalog = self._get_catalog()
                for dev_name, dev_obj in catalog.get_all_ophyd_devices().items():
                    namespace[dev_name] = dev_obj
            except Exception as e:
                logger.debug("Could not inject devices into namespace: {}", e)

            # Wrap user code in a generator function
            indented_code = textwrap.indent(code, "    ")
            wrapper = f"def _plan():\n{indented_code}\n"

            # Compile and execute to define the function
            try:
                compiled = compile(wrapper, "<plan_code>", "exec")
                exec(compiled, namespace)
            except SyntaxError as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": f"Syntax error at line {e.lineno}: {e.msg}",
                        }),
                    }],
                    "is_error": True,
                }
            except Exception as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": f"Code execution error: {type(e).__name__}: {e}",
                        }),
                    }],
                    "is_error": True,
                }

            # Get the plan generator
            plan_func = namespace.get("_plan")
            if plan_func is None:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": "Internal error: plan function not created",
                        }),
                    }],
                    "is_error": True,
                }

            # Submit to engine
            try:
                engine = get_engine()
                plan_generator = plan_func()
                proc_id = engine.submit(plan_generator, name=description)
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": True,
                            "message": f"Plan '{description}' submitted to RunEngine",
                            "procedure_id": proc_id,
                            "engine_state": engine.state_name,
                        }),
                    }]
                }
            except Exception as e:
                logger.error("Failed to submit plan code: {}", e)
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": f"Failed to submit plan: {e}",
                        }),
                    }],
                    "is_error": True,
                }

        @tool(
            name="ncs_get_user_plan",
            description="Read the source code of a user plan by name.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plan name (without .py extension)",
                    },
                },
                "required": ["name"],
            },
        )
        async def get_user_plan(args: dict) -> dict[str, Any]:
            """Read source code of a user plan."""
            from lucid.acquire.plans.user_plans import UserPlanService
            from lucid.plugins.tools._mcp_helpers import mcp_result

            name = args["name"]

            try:
                service = UserPlanService.get_instance()
                plans_dir = service.get_plans_directory()
            except Exception as e:
                return mcp_result({"success": False, "error": f"Failed to access user plans service: {e}"}, is_error=True)

            file_path = plans_dir / f"{name}.py"
            if not file_path.exists():
                return mcp_result({"success": False, "error": f"Plan '{name}' not found at {file_path}"}, is_error=True)

            try:
                code = file_path.read_text(encoding="utf-8")
                return mcp_result({
                    "success": True,
                    "name": name,
                    "path": str(file_path),
                    "code": code,
                })
            except Exception as e:
                return mcp_result({"success": False, "error": f"Failed to read plan: {e}"}, is_error=True)

        @tool(
            name="ncs_delete_user_plan",
            description="Delete a user plan by name. Requires confirm=true.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plan name (without .py extension)",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to confirm deletion",
                    },
                },
                "required": ["name", "confirm"],
            },
        )
        async def delete_user_plan(args: dict) -> dict[str, Any]:
            """Delete a user plan."""
            from lucid.acquire.plans.user_plans import UserPlanService
            from lucid.plugins.tools._mcp_helpers import mcp_result

            name = args["name"]
            confirm = args.get("confirm", False)

            if not confirm:
                return mcp_result({"success": False, "error": "Deletion not confirmed. Set confirm=true to delete."}, is_error=True)

            try:
                service = UserPlanService.get_instance()
                plans_dir = service.get_plans_directory()
            except Exception as e:
                return mcp_result({"success": False, "error": f"Failed to access user plans service: {e}"}, is_error=True)

            file_path = plans_dir / f"{name}.py"
            if not file_path.exists():
                return mcp_result({"success": False, "error": f"Plan '{name}' not found at {file_path}"}, is_error=True)

            try:
                file_path.unlink()
                logger.info("Deleted user plan '{}'", name)
                return mcp_result({
                    "success": True,
                    "message": f"Plan '{name}' deleted",
                    "path": str(file_path),
                })
            except Exception as e:
                return mcp_result({"success": False, "error": f"Failed to delete plan: {e}"}, is_error=True)

        return [create_user_plan, list_plans, run_plan, run_plan_code, get_user_plan, delete_user_plan]
