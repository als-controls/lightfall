# EnginePlugin

Engine plugins provide execution backends for running plans.

## Purpose

Use `EnginePlugin` when you want to:
- Add a new execution backend (e.g., hardware interface)
- Provide a mock/simulation engine for testing
- Implement specialized execution logic

## Base Class

```python
from lucid.plugins.engine_plugin import EnginePlugin
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"engine"` | Plugin type identifier |
| `is_singleton` | `True` | One instance per plugin |

## Required Methods

### name (property)

Unique identifier for this engine.

```python
@property
def name(self) -> str:
    return "my_engine"
```

### create_engine(**kwargs)

Create and return an engine instance.

```python
def create_engine(self, **kwargs: Any) -> BaseEngine:
    """Create the engine instance.

    Args:
        **kwargs: Engine-specific configuration.

    Returns:
        A BaseEngine subclass instance.
    """
    return MyEngine(**kwargs)
```

## Optional Methods

### display_name (property)

Human-readable name for UI display. Defaults to title-cased `name`.

```python
@property
def display_name(self) -> str:
    return "My Custom Engine"
```

### engine_description (property)

Description of the engine's capabilities.

```python
@property
def engine_description(self) -> str:
    return "A custom engine for specialized hardware control."
```

## Lifecycle

1. Plugin is instantiated on load
2. Plugin is registered with `EngineRegistry`
3. User selects engine in preferences
4. `create_engine()` is called when engine is activated
5. Engine handles plan execution

## BaseEngine Interface

Engine plugins create `BaseEngine` subclasses. The engine interface includes:

```python
from lucid.acquire.engine.base import BaseEngine, EngineState

class MyEngine(BaseEngine):
    """Custom execution engine."""

    @property
    def name(self) -> str:
        return "my_engine"

    @property
    def state(self) -> EngineState:
        """Current engine state (IDLE, RUNNING, PAUSED, etc.)."""
        return self._state

    def start(self, plan) -> None:
        """Start executing a plan."""
        ...

    def pause(self) -> None:
        """Pause execution."""
        ...

    def resume(self) -> None:
        """Resume paused execution."""
        ...

    def stop(self) -> None:
        """Stop execution gracefully."""
        ...

    def abort(self) -> None:
        """Abort execution immediately."""
        ...
```

## Complete Example

### Engine Class

```python
# my_package/engines/custom_engine.py
"""Custom execution engine."""

from enum import Enum, auto
from typing import Any, Generator

from loguru import logger
from PySide6.QtCore import QObject, Signal

from lucid.acquire.engine.base import BaseEngine, EngineState


class CustomEngine(BaseEngine, QObject):
    """A custom engine with specialized execution logic."""

    # Signals
    state_changed = Signal(object)  # EngineState
    progress_updated = Signal(int, int)  # current, total
    error_occurred = Signal(str)

    def __init__(self, config: dict | None = None):
        QObject.__init__(self)
        self._state = EngineState.IDLE
        self._config = config or {}
        self._current_plan = None

    @property
    def name(self) -> str:
        return "custom"

    @property
    def state(self) -> EngineState:
        return self._state

    def _set_state(self, state: EngineState):
        self._state = state
        self.state_changed.emit(state)

    def start(self, plan: Generator[Any, Any, Any]) -> None:
        """Start executing a plan."""
        if self._state != EngineState.IDLE:
            raise RuntimeError(f"Cannot start: engine is {self._state.name}")

        self._current_plan = plan
        self._set_state(EngineState.RUNNING)

        try:
            for msg in plan:
                if self._state == EngineState.STOPPING:
                    break
                self._process_message(msg)

            self._set_state(EngineState.IDLE)

        except Exception as e:
            logger.error("Engine error: {}", e)
            self.error_occurred.emit(str(e))
            self._set_state(EngineState.IDLE)

    def _process_message(self, msg):
        """Process a Bluesky message."""
        # Handle different message types
        command = msg.command if hasattr(msg, "command") else msg[0]
        logger.debug("Processing: {}", command)

    def pause(self) -> None:
        """Pause execution."""
        if self._state == EngineState.RUNNING:
            self._set_state(EngineState.PAUSED)

    def resume(self) -> None:
        """Resume paused execution."""
        if self._state == EngineState.PAUSED:
            self._set_state(EngineState.RUNNING)

    def stop(self) -> None:
        """Stop execution gracefully."""
        self._set_state(EngineState.STOPPING)

    def abort(self) -> None:
        """Abort execution immediately."""
        self._set_state(EngineState.IDLE)
        self._current_plan = None
```

### Plugin Class

```python
# my_package/plugins/custom_engine_plugin.py
"""Custom engine plugin."""

from typing import TYPE_CHECKING, Any

from lucid.plugins.engine_plugin import EnginePlugin

if TYPE_CHECKING:
    from lucid.acquire.engine.base import BaseEngine


class CustomEnginePlugin(EnginePlugin):
    """Plugin providing the custom engine."""

    @property
    def name(self) -> str:
        return "custom"

    @property
    def display_name(self) -> str:
        return "Custom Engine"

    @property
    def engine_description(self) -> str:
        return "Specialized engine for custom hardware control."

    def create_engine(self, **kwargs: Any) -> BaseEngine:
        from my_package.engines.custom_engine import CustomEngine
        return CustomEngine(config=kwargs)
```

## Registration

### Built-in Manifest

```python
PluginEntry(
    type_name="engine",
    name="custom",
    import_path="my_package.plugins.custom_engine_plugin:CustomEnginePlugin",
),
```

## Built-in Engines

LUCID includes these engines by default:

| Engine | Description |
|--------|-------------|
| `bluesky` | Real Bluesky RunEngine for production |
| `mock` | Simulated engine for testing |

## Mock Engine Example

For testing without hardware:

```python
class MockEnginePlugin(EnginePlugin):
    """Mock engine for testing."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock Engine"

    @property
    def engine_description(self) -> str:
        return "Simulated engine for testing without hardware."

    def create_engine(self, **kwargs) -> BaseEngine:
        from lucid.acquire.engine.mock import MockEngine
        return MockEngine(
            delay=kwargs.get("delay", 0.1),  # Simulated delay per step
            noise=kwargs.get("noise", 0.01),  # Simulated noise level
        )
```

## Engine Selection

Users select the active engine in preferences. The selected engine name is stored and used to create the engine instance when needed:

```python
# Getting the active engine
from lucid.acquire.engine.registry import EngineRegistry

registry = EngineRegistry.get_instance()
engine = registry.get_active_engine()

# Running a plan
engine.start(my_plan())
```
