# MCPToolPlugin

MCP Tool plugins add tools that the Claude assistant can call.

## Purpose

Use `MCPToolPlugin` when you want to:
- Give Claude the ability to interact with hardware
- Provide data access capabilities to the assistant
- Create automation tools callable via natural language

## Base Class

```python
from lucid.plugins.mcp_tool import MCPToolPlugin
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"mcp_tool"` | Plugin type identifier |
| `is_singleton` | `True` | One instance per plugin |

## Required Methods

### name (property)

Unique identifier for this MCP tool plugin.

```python
@property
def name(self) -> str:
    return "my_tools"
```

### create_tools()

Create and return MCP tool functions.

```python
def create_tools(self) -> list[Any]:
    """Create MCP tool functions.

    Returns:
        List of tool functions decorated with @tool.
    """
    @tool(
        name="my_function",
        description="Does something useful",
        input_schema={...}
    )
    async def my_function(args: dict) -> dict:
        # Implementation
        return {"result": "value"}

    return [my_function]
```

## Optional Methods

### tool_description (property)

Description of the tools provided. Defaults to `"Tools from {name}"`.

```python
@property
def tool_description(self) -> str:
    return "Tools for controlling beamline motors."
```

## MCP Tool Interface

Tools use the Model Context Protocol (MCP) format:

```python
from claude_agent_sdk import tool

@tool(
    name="tool_name",
    description="What this tool does",
    input_schema={
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "First parameter"},
            "param2": {"type": "number", "description": "Second parameter"},
        },
        "required": ["param1"],
    }
)
async def my_tool(args: dict) -> dict:
    param1 = args["param1"]
    param2 = args.get("param2", 0)
    # Do something
    return {"result": "success"}
```

## Lifecycle

1. Plugin is instantiated on load
2. `create_tools()` is called to get tool functions
3. Tools are registered with the Claude agent
4. Tools can be called by Claude during conversations
5. Tool results are returned to Claude for processing

## Complete Example

```python
"""Device control tools for Claude assistant."""

from lucid.plugins.mcp_tool import MCPToolPlugin


class DeviceToolPlugin(MCPToolPlugin):
    """MCP tools for device control."""

    def __init__(self):
        super().__init__()
        self._device_manager = None

    @property
    def name(self) -> str:
        return "device_tools"

    @property
    def tool_description(self) -> str:
        return "Tools for querying and controlling devices."

    def _get_device_manager(self):
        """Lazy load the device manager."""
        if self._device_manager is None:
            from lucid.devices.manager import DeviceManager
            self._device_manager = DeviceManager.get_instance()
        return self._device_manager

    def create_tools(self) -> list:
        from claude_agent_sdk import tool

        @tool(
            name="get_device_value",
            description="Get the current value of a device",
            input_schema={
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "Name of the device (e.g., 'motor1')",
                    },
                },
                "required": ["device_name"],
            }
        )
        async def get_device_value(args: dict) -> dict:
            device_name = args["device_name"]
            manager = self._get_device_manager()

            try:
                device = manager.get_device(device_name)
                if device is None:
                    return {"error": f"Device '{device_name}' not found"}

                value = device.get()
                return {
                    "device": device_name,
                    "value": value,
                    "units": getattr(device, "units", ""),
                }
            except Exception as e:
                return {"error": str(e)}

        @tool(
            name="set_device_value",
            description="Set the value of a device (moves motors, sets temperatures, etc.)",
            input_schema={
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "Name of the device",
                    },
                    "value": {
                        "type": "number",
                        "description": "Value to set",
                    },
                },
                "required": ["device_name", "value"],
            }
        )
        async def set_device_value(args: dict) -> dict:
            device_name = args["device_name"]
            value = args["value"]
            manager = self._get_device_manager()

            try:
                device = manager.get_device(device_name)
                if device is None:
                    return {"error": f"Device '{device_name}' not found"}

                # Start the move
                device.set(value)

                return {
                    "device": device_name,
                    "target": value,
                    "status": "moving",
                }
            except Exception as e:
                return {"error": str(e)}

        @tool(
            name="list_devices",
            description="List all available devices",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Filter by category (motor, detector, etc.)",
                    },
                },
            }
        )
        async def list_devices(args: dict) -> dict:
            manager = self._get_device_manager()
            category = args.get("category")

            devices = manager.list_devices(category=category)
            return {
                "devices": [
                    {
                        "name": d.name,
                        "category": d.category,
                        "connected": d.connected,
                    }
                    for d in devices
                ]
            }

        return [get_device_value, set_device_value, list_devices]
```

## Data Access Tools

```python
"""Tools for accessing experiment data."""

from lucid.plugins.mcp_tool import MCPToolPlugin


class DataToolPlugin(MCPToolPlugin):
    """MCP tools for data access."""

    @property
    def name(self) -> str:
        return "data_tools"

    @property
    def tool_description(self) -> str:
        return "Tools for accessing and analyzing experiment data."

    def create_tools(self) -> list:
        from claude_agent_sdk import tool

        @tool(
            name="get_recent_scans",
            description="Get list of recent scans",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of scans to return",
                        "default": 10,
                    },
                },
            }
        )
        async def get_recent_scans(args: dict) -> dict:
            from lucid.data.catalog import get_catalog

            limit = args.get("limit", 10)
            catalog = get_catalog()

            scans = []
            for uid in catalog.keys()[-limit:]:
                run = catalog[uid]
                scans.append({
                    "uid": uid,
                    "start_time": run.metadata["start"]["time"],
                    "plan_name": run.metadata["start"].get("plan_name", "unknown"),
                })

            return {"scans": scans}

        @tool(
            name="get_scan_data",
            description="Get data from a specific scan",
            input_schema={
                "type": "object",
                "properties": {
                    "scan_uid": {
                        "type": "string",
                        "description": "Unique identifier of the scan",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific columns to retrieve (optional)",
                    },
                },
                "required": ["scan_uid"],
            }
        )
        async def get_scan_data(args: dict) -> dict:
            from lucid.data.catalog import get_catalog

            uid = args["scan_uid"]
            columns = args.get("columns")

            catalog = get_catalog()

            try:
                run = catalog[uid]
                data = run.primary.read()

                if columns:
                    data = data[columns]

                # Convert to JSON-serializable format
                return {
                    "uid": uid,
                    "columns": list(data.columns),
                    "shape": list(data.shape),
                    "data": data.to_dict(orient="records")[:100],  # Limit rows
                }
            except KeyError:
                return {"error": f"Scan '{uid}' not found"}

        return [get_recent_scans, get_scan_data]
```

## Registration

```python
PluginEntry(
    type_name="mcp_tool",
    name="device_tools",
    import_path="my_package.plugins:DeviceToolPlugin",
),
```

## Tool Design Guidelines

### Clear Descriptions

Write clear, specific descriptions:

```python
# Good
description="Move a motor to a specific position and wait for completion"

# Bad
description="Set value"
```

### Input Schema

Define complete input schemas:

```python
input_schema={
    "type": "object",
    "properties": {
        "motor_name": {
            "type": "string",
            "description": "Name of the motor (e.g., 'sample_x', 'theta')",
        },
        "position": {
            "type": "number",
            "description": "Target position in motor units (mm, degrees, etc.)",
        },
        "wait": {
            "type": "boolean",
            "description": "Whether to wait for the move to complete",
            "default": True,
        },
    },
    "required": ["motor_name", "position"],
}
```

### Error Handling

Return clear error information:

```python
async def my_tool(args: dict) -> dict:
    try:
        # Do something
        return {"result": "success"}
    except DeviceNotFoundError as e:
        return {"error": f"Device not found: {e}"}
    except PermissionError as e:
        return {"error": f"Permission denied: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}
```

### Async Operations

Tools are async functions. Use `await` for async operations:

```python
async def async_tool(args: dict) -> dict:
    result = await some_async_operation()
    return {"data": result}
```
