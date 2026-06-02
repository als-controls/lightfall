"""Dynamic plan configuration widget.

Generates UI from plan function signatures using pyqtgraph's ParameterTree
for automatic parameter editing, with custom DeviceParameter for device selection.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from enum import Enum, StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin

from loguru import logger
from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.utils.editor_launcher import CodeEditor, get_editor_from_string, open_in_editor

try:
    from pyqtgraph.parametertree import Parameter, ParameterTree
    from pyqtgraph.parametertree.parameterTypes import GroupParameter

    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False
    Parameter = None
    ParameterTree = None
    GroupParameter = None

if TYPE_CHECKING:
    from lightfall.acquire.plans import PlanInfo
    from lightfall.devices import DeviceCatalog


# Parameter type categories for special handling
class ParamCategory(StrEnum):
    """Categories of plan parameters for UI generation."""

    BASIC = "basic"  # int, float, str, bool
    DEVICE = "device"  # Single device (motor, positioner)
    DEVICES = "devices"  # Multiple devices (detectors)
    ENUM = "enum"  # Enumeration type
    LIST = "list"  # Generic list
    OTHER = "other"  # Unknown type


# Mapping from Python types to pyqtgraph parameter types
PARAM_TYPE_MAP = {
    "int": "int",
    "float": "float",
    "str": "str",
    "bool": "bool",
    "list": "list",
    "tuple": "list",
}


def get_param_category(name: str, annotation: Any) -> ParamCategory:
    """Determine the category of a parameter based on name and type.

    Args:
        name: Parameter name.
        annotation: Type annotation.

    Returns:
        ParamCategory for the parameter.
    """
    name_lower = name.lower()

    # Check by name first (most reliable for bluesky plans)
    if name_lower == "detectors" or name_lower == "dets":
        return ParamCategory.DEVICES
    if name_lower in ("motor", "signal", "positioner", "obj"):
        return ParamCategory.DEVICE

    # Check by annotation
    if annotation is None or annotation is inspect.Parameter.empty:
        return ParamCategory.OTHER

    # Check if the type is a collection (list, tuple, etc.)
    origin = get_origin(annotation)
    is_collection = origin in (list, tuple)

    type_str = str(annotation).lower()

    # Device types - check if it's a collection to determine single vs multi
    if "detector" in type_str or "readable" in type_str:
        return ParamCategory.DEVICES if is_collection else ParamCategory.DEVICE
    if "motor" in type_str or "positioner" in type_str or "movable" in type_str:
        return ParamCategory.DEVICE

    # Enum
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return ParamCategory.ENUM

    # Basic types
    type_name = getattr(annotation, "__name__", "").lower()
    if type_name in PARAM_TYPE_MAP:
        return ParamCategory.BASIC

    # List/tuple
    origin = get_origin(annotation)
    if origin in (list, tuple):
        return ParamCategory.LIST

    return ParamCategory.OTHER


# Ensure DeviceParameter is registered when this module is imported
def _ensure_device_parameter_registered():
    """Ensure the DeviceParameter type is registered with pyqtgraph."""
    if HAS_PYQTGRAPH:
        # Import triggers registration
        import lightfall.ui.widgets.device_selector  # noqa: F401


_ensure_device_parameter_registered()


def resolve_string_annotation(annotation: Any, func: Callable | None = None) -> Any:
    """Resolve a string annotation to its actual type.

    When using `from __future__ import annotations`, annotations are stored
    as strings. This function evaluates them to get the actual type objects.

    Args:
        annotation: The annotation (possibly a string).
        func: The function the annotation belongs to (for namespace resolution).

    Returns:
        The resolved annotation type.
    """
    if not isinstance(annotation, str):
        return annotation

    # Build namespace for eval
    namespace: dict[str, Any] = {}

    # Add typing module contents
    import typing
    namespace.update(vars(typing))

    # Add common types
    namespace["Any"] = Any
    namespace["Annotated"] = Annotated

    # Add annotation classes
    try:
        from lightfall.ui.annotations import (
            Decimals,
            Default,
            DeviceDefault,
            DeviceFilter,
            DeviceFilterAny,
            DeviceIcon,
            Range,
            Unit,
        )
        namespace.update({
            "Unit": Unit,
            "Decimals": Decimals,
            "Range": Range,
            "Default": Default,
            "DeviceFilter": DeviceFilter,
            "DeviceFilterAny": DeviceFilterAny,
            "DeviceDefault": DeviceDefault,
            "DeviceIcon": DeviceIcon,
        })
    except ImportError:
        pass

    # Add function's module namespace if available
    if func is not None:
        module = inspect.getmodule(func)
        if module is not None:
            namespace.update(vars(module))

    try:
        return eval(annotation, namespace)
    except Exception as e:
        logger.debug(f"Failed to resolve annotation '{annotation}': {e}")
        return annotation


def extract_annotated_metadata(annotation: Any, func: Callable | None = None) -> tuple[Any, list[Any]]:
    """Extract base type and metadata from Annotated type hints.

    Handles both actual type objects and string annotations (from
    `from __future__ import annotations`).

    Args:
        annotation: A type annotation, possibly Annotated[T, meta1, meta2, ...].
        func: Optional function for resolving string annotations.

    Returns:
        Tuple of (base_type, [metadata_items]).
        If not an Annotated type, returns (annotation, []).
    """
    # Resolve string annotation if needed
    resolved = resolve_string_annotation(annotation, func)

    origin = get_origin(resolved)
    if origin is Annotated:
        args = get_args(resolved)
        if args:
            return args[0], list(args[1:])
    return resolved, []


def annotation_to_param_type(annotation: Any) -> tuple[str, dict[str, Any]]:
    """Convert a type annotation to pyqtgraph parameter type.

    Args:
        annotation: Type annotation from function signature.

    Returns:
        Tuple of (param_type, extra_opts) for Parameter creation.
    """
    extra_opts: dict[str, Any] = {}

    if annotation is None or annotation is inspect.Parameter.empty:
        return "str", extra_opts

    # Handle string annotations
    if isinstance(annotation, str):
        annotation_name = annotation.lower()
        if annotation_name in PARAM_TYPE_MAP:
            return PARAM_TYPE_MAP[annotation_name], extra_opts
        return "str", extra_opts

    # Get the base type for generic types
    origin = get_origin(annotation)
    args = get_args(annotation)

    # Handle Optional[X] (Union[X, None])
    if origin is type(None) or (hasattr(origin, "__name__") and origin.__name__ == "Union"):
        # Get the non-None type
        non_none_args = [a for a in args if a is not type(None)]
        if non_none_args:
            return annotation_to_param_type(non_none_args[0])

    # Handle list/tuple
    if origin in (list, tuple):
        return "text", extra_opts  # Use text for entering list values

    # Handle Enum
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        extra_opts["limits"] = [e.value for e in annotation]
        return "list", extra_opts

    # Handle basic types
    type_name = getattr(annotation, "__name__", str(annotation)).lower()
    if type_name in PARAM_TYPE_MAP:
        return PARAM_TYPE_MAP[type_name], extra_opts

    # Special handling for common ophyd/bluesky types
    type_str = str(annotation).lower()
    if "motor" in type_str or "positioner" in type_str or "movable" in type_str:
        return "str", {"tip": "Device name from catalog"}
    if "detector" in type_str or "readable" in type_str:
        return "str", {"tip": "Detector name(s) from catalog"}

    return "str", extra_opts


def signature_to_parameters(
    sig: inspect.Signature,
    param_docs: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Convert a function signature to pyqtgraph Parameter specifications.

    Args:
        sig: Function signature.
        param_docs: Optional mapping of param names to descriptions.

    Returns:
        List of parameter dictionaries for Parameter.create().
    """
    param_docs = param_docs or {}
    param_specs = []

    for name, param in sig.parameters.items():
        # Skip *args and **kwargs
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        param_type, extra_opts = annotation_to_param_type(param.annotation)

        spec = {
            "name": name,
            "type": param_type,
        }

        # Add default value
        if param.default is not inspect.Parameter.empty:
            # Convert list/tuple to string for text parameters
            if param_type == "text" and isinstance(param.default, (list, tuple)):
                spec["value"] = ", ".join(str(v) for v in param.default)
            else:
                spec["value"] = param.default
        else:
            # Set sensible defaults for required parameters
            if param_type == "int":
                spec["value"] = 0
            elif param_type == "float":
                spec["value"] = 0.0
            elif param_type == "bool":
                spec["value"] = False
            elif param_type in ("str", "text"):
                spec["value"] = ""

        # Add tooltip from docstring
        if name in param_docs:
            spec["tip"] = param_docs[name]

        # Merge extra options
        spec.update(extra_opts)

        param_specs.append(spec)

    return param_specs


class PlanConfigWidget(QWidget):
    """Dynamically generated UI for configuring plan parameters.

    Uses pyqtgraph's ParameterTree for all parameters, including custom
    DeviceParameter type for device selection. Provides validation
    and Run button.

    Signals:
        run_requested(PlanInfo, dict): Emitted when Run is clicked with plan and kwargs.
        values_changed(dict): Emitted when any parameter value changes.

    Example:
        >>> from lightfall.acquire.plans import get_registry
        >>> registry = get_registry()
        >>> plan_info = registry.get_plan("scan")
        >>> config = PlanConfigWidget()
        >>> config.set_catalog(device_catalog)
        >>> config.set_plan(plan_info)
        >>> config.run_requested.connect(run_plan)
    """

    run_requested = Signal(object, dict)  # (PlanInfo, kwargs)
    values_changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the config widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._plan: PlanInfo | None = None
        self._root_param: Parameter | None = None
        self._catalog: DeviceCatalog | None = None
        self._values_cache: dict[str, dict[str, Any]] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header
        self._header_label = QLabel("No plan selected")
        self._header_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        layout.addWidget(self._header_label)

        self._desc_label = QLabel("")
        self._desc_label.setWordWrap(True)
        layout.addWidget(self._desc_label)

        # Parameter tree for all parameters
        if HAS_PYQTGRAPH:
            self._param_tree = ParameterTree(showHeader=False)
            layout.addWidget(self._param_tree, 1)
        else:
            self._param_tree = None
            self._fallback_label = QLabel(
                "pyqtgraph not available.\n"
                "Install with: pip install pyqtgraph"
            )
            layout.addWidget(self._fallback_label, 1)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setToolTip("Reset to default values")
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        self._reset_btn.setEnabled(False)
        button_layout.addWidget(self._reset_btn)

        self._edit_btn = QPushButton("Edit")
        self._edit_btn.setToolTip("Open plan source file in editor")
        self._edit_btn.clicked.connect(self._on_edit_clicked)
        self._edit_btn.setEnabled(False)
        button_layout.addWidget(self._edit_btn)

        self._run_btn = QPushButton("Run")
        self._run_btn.setToolTip("Run the plan with current parameters")
        self._run_btn.clicked.connect(self._on_run_clicked)
        self._run_btn.setEnabled(False)
        button_layout.addWidget(self._run_btn)

        layout.addLayout(button_layout)

    def set_catalog(self, catalog: DeviceCatalog) -> None:
        """Set the device catalog for device selection.

        Args:
            catalog: DeviceCatalog instance.
        """
        self._catalog = catalog

    def _build_param_spec(
        self,
        name: str,
        annotation: Any,
        default: Any,
        doc: str | None,
        func: Callable | None = None,
    ) -> dict[str, Any]:
        """Build a parameter spec from annotation with metadata.

        Extracts annotation metadata (Unit, Decimals, Range, DeviceFilter, etc.)
        and applies them to the parameter specification.

        Args:
            name: Parameter name.
            annotation: Type annotation (possibly Annotated[...]).
            default: Default value from function signature.
            doc: Documentation string for tooltip.
            func: The plan function (for resolving string annotations).

        Returns:
            Parameter specification dict for pyqtgraph Parameter.create().
        """
        from lightfall.ui.annotations import (
            Decimals,
            Default,
            DeviceDefault,
            DeviceFilter,
            DeviceFilterAny,
            Range,
            Unit,
        )

        # Extract base type and metadata from Annotated[T, meta1, meta2, ...]
        base_type, metadata = extract_annotated_metadata(annotation, func)

        # Determine parameter category and type using the base type
        category = get_param_category(name, base_type)

        # Check if metadata contains DeviceFilter - this overrides category detection
        # This handles cases like `Annotated[Any, DeviceFilter(...)]` where the base
        # type doesn't indicate it's a device but the annotation metadata does
        has_device_filter = any(
            isinstance(meta, (DeviceFilter, DeviceFilterAny)) for meta in metadata
        )
        if has_device_filter and category not in (ParamCategory.DEVICE, ParamCategory.DEVICES):
            # Determine single vs multi-select based on whether base type is a list
            origin = get_origin(base_type)
            if origin in (list, tuple):
                category = ParamCategory.DEVICES
            else:
                category = ParamCategory.DEVICE

        # Start building the spec
        spec: dict[str, Any] = {"name": name}

        if category in (ParamCategory.DEVICE, ParamCategory.DEVICES):
            from lightfall.devices.model import DeviceCategory as DC
            from lightfall.ui.annotations import DeviceDefault, DeviceIcon

            spec["type"] = "device"
            spec["value"] = []
            spec["catalog"] = self._catalog
            spec["multi_select"] = category == ParamCategory.DEVICES

            for meta in metadata:
                if isinstance(meta, DeviceFilter):
                    # Translate category to set[DeviceCategory]
                    if meta.category is not None:
                        if isinstance(meta.category, set):
                            spec["categories"] = {DC(c) for c in meta.category}
                        else:
                            spec["categories"] = {DC(meta.category)}
                    if meta.device_class is not None:
                        spec.setdefault("filter_func_parts", []).append(
                            lambda m, dc=meta.device_class: (
                                m["device_info"] is not None
                                and (
                                    m["device_info"].device_class == dc
                                    or m["device_info"].device_class.rsplit(".", 1)[-1] == dc
                                )
                            )
                        )
                    if meta.group is not None:
                        spec.setdefault("filter_func_parts", []).append(
                            lambda m, g=meta.group: (
                                m["device_info"] is not None
                                and g in m["device_info"].tags
                            )
                        )
                    if meta.name_pattern is not None:
                        import re as _re
                        spec.setdefault("filter_func_parts", []).append(
                            lambda m, p=meta.name_pattern: bool(
                                _re.match(p, m["name"], _re.IGNORECASE)
                            )
                        )
                elif isinstance(meta, DeviceFilterAny):
                    # Collect all categories from sub-filters
                    cats: set[DC] = set()
                    for flt in meta.filters:
                        if flt.category is not None:
                            if isinstance(flt.category, set):
                                cats.update(DC(c) for c in flt.category)
                            else:
                                cats.add(DC(flt.category))
                    if cats:
                        spec["categories"] = cats
                elif isinstance(meta, DeviceDefault):
                    if meta.names:
                        spec["value"] = list(meta.names)
                elif isinstance(meta, DeviceIcon):
                    spec["icon"] = meta.name

            # Combine filter_func_parts into a single filter_func
            parts = spec.pop("filter_func_parts", [])
            if parts:
                spec["filter_func"] = lambda m, _parts=parts: all(f(m) for f in _parts)
        else:
            # Use standard parameter type
            param_type, extra_opts = annotation_to_param_type(base_type)
            spec["type"] = param_type
            spec.update(extra_opts)

            # Add default value from signature
            if default is not inspect.Parameter.empty:
                # Convert list/tuple to string for text parameters
                if param_type == "text" and isinstance(default, (list, tuple)):
                    # Format as comma-separated values for user editing
                    spec["value"] = ", ".join(str(v) for v in default)
                else:
                    spec["value"] = default
            else:
                # Set sensible defaults for required parameters
                if param_type == "int":
                    spec["value"] = 0
                elif param_type == "float":
                    spec["value"] = 0.0
                elif param_type == "bool":
                    spec["value"] = False
                elif param_type in ("str", "text"):
                    spec["value"] = ""

            # Process metadata for numeric/other parameters
            for meta in metadata:
                if isinstance(meta, Unit):
                    spec["suffix"] = meta.suffix
                elif isinstance(meta, Decimals):
                    spec["decimals"] = meta.places
                elif isinstance(meta, Range):
                    limits = []
                    if meta.min is not None or meta.max is not None:
                        # pyqtgraph expects (min, max) tuple
                        limits = [meta.min, meta.max]
                        # Replace None with appropriate defaults
                        if limits[0] is None:
                            limits[0] = float("-inf") if param_type == "float" else -(2**31)
                        if limits[1] is None:
                            limits[1] = float("inf") if param_type == "float" else 2**31 - 1
                        spec["limits"] = tuple(limits)
                elif isinstance(meta, Default):
                    # Convert list/tuple to string for text parameters
                    if param_type == "text" and isinstance(meta.value, (list, tuple)):
                        spec["value"] = ", ".join(str(v) for v in meta.value)
                    else:
                        spec["value"] = meta.value

        # Add tooltip from docstring
        if doc:
            spec["tip"] = doc

        return spec

    def set_plan(self, plan_info: PlanInfo) -> None:
        """Configure UI for a new plan.

        Supports Annotated type hints with metadata for:
        - Unit: Display suffix (e.g., "eV", "s", "mm")
        - Decimals: Float precision (number of decimal places)
        - Range: Min/max limits for numeric inputs
        - DeviceFilter/DeviceFilterAny: Device selection filtering
        - DeviceDefault: Pre-selected devices

        Args:
            plan_info: Plan to configure.
        """
        # Cache current values before switching
        if self._plan is not None and self._root_param is not None:
            self._values_cache[self._plan.name] = self.get_kwargs()

        self._plan = plan_info
        # Use display name for header
        self._header_label.setText(plan_info.get_display_name())
        self._desc_label.setText(plan_info.description)

        if not HAS_PYQTGRAPH or self._param_tree is None:
            logger.warning("pyqtgraph not available, cannot configure plan")
            return

        # Build parameter docs from PlanInfo
        param_docs = {p.name: p.description for p in plan_info.parameters}

        # Convert all parameters to parameter specs
        param_specs = []
        for p in plan_info.parameters:
            # Skip *args and **kwargs
            if p.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue

            spec = self._build_param_spec(
                name=p.name,
                annotation=p.annotation,
                default=p.default,
                doc=param_docs.get(p.name),
                func=plan_info.func,
            )
            param_specs.append(spec)

        # Create root parameter group
        self._root_param = Parameter.create(
            name=plan_info.name,
            type="group",
            children=param_specs,
        )

        # Connect change signal
        self._root_param.sigTreeStateChanged.connect(self._on_param_changed)

        # Set in tree
        self._param_tree.setParameters(self._root_param, showTop=False)

        # Restore cached values if we've seen this plan before
        cached = self._values_cache.get(plan_info.name)
        if cached:
            self.set_values(cached)

        self._reset_btn.setEnabled(True)
        self._edit_btn.setEnabled(True)
        self._run_btn.setEnabled(True)

        logger.debug(f"Configured plan: {plan_info.name} with {len(param_specs)} params")

    def clear(self) -> None:
        """Clear the current plan configuration."""
        self._plan = None
        self._root_param = None
        self._header_label.setText("No plan selected")
        self._desc_label.setText("")

        if self._param_tree is not None:
            self._param_tree.clear()

        self._reset_btn.setEnabled(False)
        self._edit_btn.setEnabled(False)
        self._run_btn.setEnabled(False)

    def get_kwargs(self) -> dict[str, Any]:
        """Get current parameter values as kwargs.

        Returns:
            Dictionary of parameter name to value.
        """
        kwargs = {}

        if self._root_param is not None:
            for child in self._root_param.children():
                value = child.value()
                # Skip empty values for optional params
                if value is None:
                    continue
                if isinstance(value, str) and value == "":
                    continue
                if isinstance(value, list) and len(value) == 0:
                    continue
                kwargs[child.name()] = value

        return kwargs

    def set_values(self, values: dict[str, Any]) -> None:
        """Set parameter values.

        Args:
            values: Dictionary of parameter name to value.
        """
        if self._root_param is None:
            return

        for name, value in values.items():
            child = self._root_param.child(name)
            if child is not None:
                try:
                    child.setValue(value)
                except Exception as e:
                    logger.warning(f"Failed to set {name}={value}: {e}")

    def validate(self) -> tuple[bool, list[str]]:
        """Validate current parameter values.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        errors = []

        if self._plan is None:
            errors.append("No plan selected")
            return False, errors

        if self._root_param is None:
            errors.append("Parameters not configured")
            return False, errors

        # Check required parameters
        for param_info in self._plan.parameters:
            if param_info.required:
                child = self._root_param.child(param_info.name)
                if child is None:
                    continue

                value = child.value()
                is_empty = (
                    value is None
                    or (isinstance(value, str) and value == "")
                    or (isinstance(value, list) and len(value) == 0)
                )
                if is_empty:
                    errors.append(f"Required parameter '{param_info.name}' is empty")

        return len(errors) == 0, errors

    @property
    def current_plan(self) -> PlanInfo | None:
        """Get the currently configured plan."""
        return self._plan

    # === Slots ===

    @Slot()
    def _on_reset_clicked(self) -> None:
        """Handle reset button click."""
        if self._plan is not None:
            # Clear cached values so set_plan restores defaults
            self._values_cache.pop(self._plan.name, None)
            self.set_plan(self._plan)

    def _get_plan_file_path(self) -> Path | None:
        """Get the file path for the current plan.

        For user plans, retrieves from UserPlanService._loaded_plans.
        For built-in plans, uses inspect.getfile() on the plan function.

        Returns:
            Path to the plan's source file, or None if not found.
        """
        if self._plan is None:
            return None

        # Try user plans first
        from lightfall.acquire.plans.user_plans import UserPlanService

        service = UserPlanService.get_instance()
        if self._plan.name in service._loaded_plans:
            return service._loaded_plans[self._plan.name]

        # Fall back to inspect for built-in plans
        try:
            return Path(inspect.getfile(self._plan.func))
        except (TypeError, OSError):
            return None

    @Slot()
    def _on_edit_clicked(self) -> None:
        """Open the plan's source file in the configured external editor."""
        from lightfall.ui.preferences.manager import PreferencesManager
        from lightfall.ui.toast import ToastManager

        if self._plan is None:
            return

        # Get file path for the plan
        file_path = self._get_plan_file_path()
        if file_path is None:
            toast = ToastManager.get_instance()
            toast.error("Cannot Edit", f"Could not locate source file for plan '{self._plan.name}'")
            return

        # Get configured editor from preferences
        prefs = PreferencesManager.get_instance()
        editor_str = prefs.get("code_editor", "vscode")
        editor = get_editor_from_string(editor_str)

        if editor is None:
            editor = CodeEditor.VSCODE  # Fallback

        # Open file at line 1
        success = open_in_editor(str(file_path), 1, editor)
        if not success:
            toast = ToastManager.get_instance()
            toast.error("Editor Error", f"Failed to open '{file_path.name}' in {editor.value}")

    @Slot()
    def _on_run_clicked(self) -> None:
        """Handle run button click."""
        if self._plan is None:
            return

        is_valid, errors = self.validate()
        if not is_valid:
            logger.warning(f"Validation failed: {errors}")
            from lightfall.ui.toast import ToastManager
            toast = ToastManager.get_instance()
            toast.warning("Missing Parameters", "\n".join(errors))
            return

        kwargs = self.get_kwargs()
        self.run_requested.emit(self._plan, kwargs)
        logger.info(f"Run requested: {self._plan.name} with {kwargs}")

    @Slot(object, object)
    def _on_param_changed(self, param: Parameter, changes: list) -> None:
        """Handle parameter value changes.

        Args:
            param: The root parameter.
            changes: List of changes.
        """
        self.values_changed.emit(self.get_kwargs())


class PlanExecutionWidget(QWidget):
    """Combined widget for plan selection, configuration, and execution.

    Combines PlanSelectorWidget and PlanConfigWidget with a Run button
    that executes the plan on the RunEngine.

    Example:
        >>> from lightfall.acquire import get_run_engine
        >>> from lightfall.acquire.plans import get_registry
        >>> widget = PlanExecutionWidget()
        >>> widget.set_registry(get_registry())
        >>> widget.set_run_engine(get_run_engine())
    """

    plan_started = Signal(str)  # plan name
    plan_finished = Signal(str, str)  # plan name, exit_status

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the execution widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._re = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        from lightfall.ui.widgets.plan_selector import PlanSelectorWidget

        layout = QHBoxLayout(self)

        # Plan selector on left
        self._selector = PlanSelectorWidget()
        self._selector.plan_selected.connect(self._on_plan_selected)
        layout.addWidget(self._selector, 1)

        # Config on right
        self._config = PlanConfigWidget()
        self._config.run_requested.connect(self._on_run_requested)
        layout.addWidget(self._config, 1)

    def set_registry(self, registry) -> None:
        """Set the plan registry.

        Args:
            registry: PlanRegistry instance.
        """
        self._selector.set_registry(registry)

    def set_run_engine(self, re) -> None:
        """Set the RunEngine for execution.

        Args:
            re: QRunEngine instance.
        """
        self._re = re

    @Slot(object)
    def _on_plan_selected(self, plan_info: PlanInfo) -> None:
        """Handle plan selection.

        Args:
            plan_info: Selected plan.
        """
        self._config.set_plan(plan_info)

    @Slot(object, dict)
    def _on_run_requested(self, plan_info: PlanInfo, kwargs: dict) -> None:
        """Handle run request.

        Args:
            plan_info: Plan to run.
            kwargs: Parameter values.
        """
        if self._re is None:
            logger.error("No RunEngine configured")
            return

        # TODO: Resolve device names to actual devices from catalog
        # For now, just pass kwargs directly

        try:
            # Create the plan generator
            plan = plan_info.func(**kwargs)

            # Submit to RunEngine
            self._re(plan)
            self.plan_started.emit(plan_info.name)

            logger.info(f"Submitted plan: {plan_info.name}")
        except Exception as e:
            logger.error(f"Failed to run plan {plan_info.name}: {e}")
