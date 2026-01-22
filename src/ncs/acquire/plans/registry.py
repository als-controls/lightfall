"""Central registry of available Bluesky plans.

Provides a registry for plan discovery and UI generation.
Plans are registered with metadata extracted from function
signatures and docstrings.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Generator

from loguru import logger


@dataclass
class ParameterInfo:
    """Information about a plan parameter.

    Attributes:
        name: Parameter name.
        annotation: Type annotation.
        default: Default value (inspect.Parameter.empty if none).
        kind: Parameter kind (POSITIONAL_ONLY, VAR_POSITIONAL, etc.).
        description: Extracted description from docstring.
        required: Whether the parameter is required.
    """

    name: str
    annotation: Any = None
    default: Any = field(default_factory=lambda: inspect.Parameter.empty)
    kind: inspect._ParameterKind = inspect.Parameter.POSITIONAL_OR_KEYWORD
    description: str = ""

    @property
    def required(self) -> bool:
        """Check if parameter is required (no default)."""
        return self.default is inspect.Parameter.empty

    @property
    def type_name(self) -> str:
        """Get a display-friendly type name."""
        if self.annotation is None or self.annotation is inspect.Parameter.empty:
            return "any"
        if hasattr(self.annotation, "__name__"):
            return self.annotation.__name__
        return str(self.annotation)


@dataclass
class PlanInfo:
    """Metadata about a registered plan.

    Attributes:
        name: Plan name (used for lookup).
        func: The plan generator function.
        signature: Function signature.
        description: Plan description (from docstring).
        category: Plan category (e.g., "scan", "count", "alignment").
        parameters: List of parameter info.
        examples: Example usage strings.
    """

    name: str
    func: Callable[..., Generator[Any, Any, Any]]
    signature: inspect.Signature
    description: str = ""
    category: str = "general"
    parameters: list[ParameterInfo] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    @classmethod
    def from_function(
        cls,
        name: str,
        func: Callable[..., Generator[Any, Any, Any]],
        category: str = "general",
    ) -> PlanInfo:
        """Create PlanInfo from a function.

        Extracts signature, docstring, and parameter information.

        Args:
            name: Plan name.
            func: The plan function.
            category: Plan category.

        Returns:
            PlanInfo instance.
        """
        sig = inspect.signature(func)
        doc = func.__doc__ or ""

        # Parse description from docstring (first paragraph)
        description = ""
        if doc:
            lines = doc.strip().split("\n\n")
            if lines:
                description = lines[0].strip().replace("\n", " ")

        # Extract parameter info
        param_docs = cls._parse_param_docs(doc)
        parameters = []
        for param_name, param in sig.parameters.items():
            param_info = ParameterInfo(
                name=param_name,
                annotation=param.annotation,
                default=param.default,
                kind=param.kind,
                description=param_docs.get(param_name, ""),
            )
            parameters.append(param_info)

        # Extract examples from docstring
        examples = cls._parse_examples(doc)

        return cls(
            name=name,
            func=func,
            signature=sig,
            description=description,
            category=category,
            parameters=parameters,
            examples=examples,
        )

    @staticmethod
    def _parse_param_docs(docstring: str) -> dict[str, str]:
        """Parse parameter descriptions from docstring.

        Handles both Google-style and NumPy-style docstrings.

        Args:
            docstring: Function docstring.

        Returns:
            Dictionary mapping param names to descriptions.
        """
        param_docs: dict[str, str] = {}
        if not docstring:
            return param_docs

        lines = docstring.split("\n")
        in_params = False
        current_param = None
        current_desc: list[str] = []

        for line in lines:
            stripped = line.strip()

            # Detect parameter section
            if stripped.lower() in ("parameters:", "args:", "arguments:"):
                in_params = True
                continue
            elif stripped.lower() in (
                "returns:",
                "yields:",
                "raises:",
                "examples:",
                "example:",
            ):
                in_params = False
                if current_param:
                    param_docs[current_param] = " ".join(current_desc).strip()
                current_param = None
                continue

            if in_params:
                # Check for new parameter line (indented with name : type or name (type))
                if stripped and not line.startswith("        "):
                    # Save previous
                    if current_param:
                        param_docs[current_param] = " ".join(current_desc).strip()

                    # Parse new parameter
                    if ":" in stripped:
                        parts = stripped.split(":", 1)
                        current_param = parts[0].strip().split()[0]
                        current_desc = [parts[1].strip()] if len(parts) > 1 else []
                    else:
                        current_param = stripped.split()[0]
                        current_desc = []
                elif current_param and stripped:
                    current_desc.append(stripped)

        # Don't forget last parameter
        if current_param:
            param_docs[current_param] = " ".join(current_desc).strip()

        return param_docs

    @staticmethod
    def _parse_examples(docstring: str) -> list[str]:
        """Extract examples from docstring.

        Args:
            docstring: Function docstring.

        Returns:
            List of example strings.
        """
        examples: list[str] = []
        if not docstring:
            return examples

        lines = docstring.split("\n")
        in_examples = False
        current_example: list[str] = []

        for line in lines:
            stripped = line.strip()

            if stripped.lower() in ("example:", "examples:"):
                in_examples = True
                continue

            if in_examples:
                if stripped.startswith(">>>"):
                    if current_example:
                        examples.append("\n".join(current_example))
                    current_example = [stripped]
                elif current_example and (
                    stripped.startswith("...") or not stripped or line.startswith("   ")
                ):
                    current_example.append(stripped)
                elif stripped and not line.startswith("   "):
                    # End of examples section
                    if current_example:
                        examples.append("\n".join(current_example))
                    break

        if current_example:
            examples.append("\n".join(current_example))

        return examples

    def get_required_params(self) -> list[ParameterInfo]:
        """Get list of required parameters.

        Returns:
            List of parameters without defaults.
        """
        return [p for p in self.parameters if p.required]

    def get_optional_params(self) -> list[ParameterInfo]:
        """Get list of optional parameters.

        Returns:
            List of parameters with defaults.
        """
        return [p for p in self.parameters if not p.required]


class PlanRegistry:
    """Registry of available Bluesky plans.

    Plans are registered with metadata for UI generation and discovery.
    Supports categorization, search, and introspection.

    Example:
        >>> registry = PlanRegistry()
        >>> registry.register("my_scan", my_scan_func, category="custom")
        >>> plan = registry.get_plan("my_scan")
        >>> plan.func(detectors, motor, -10, 10, 21)
    """

    _instance: ClassVar[PlanRegistry | None] = None

    def __init__(self) -> None:
        """Initialize the registry."""
        self._plans: dict[str, PlanInfo] = {}
        self._categories: set[str] = set()

    @classmethod
    def get_instance(cls) -> PlanRegistry:
        """Get the singleton instance.

        Returns:
            The PlanRegistry singleton.
        """
        if cls._instance is None:
            cls._instance = create_default_registry()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        cls._instance = None

    def register(
        self,
        name: str,
        func: Callable[..., Generator[Any, Any, Any]],
        category: str = "general",
    ) -> PlanInfo:
        """Register a plan.

        Args:
            name: Plan name (must be unique).
            func: The plan generator function.
            category: Plan category for grouping.

        Returns:
            The created PlanInfo.

        Raises:
            ValueError: If a plan with this name already exists.
        """
        if name in self._plans:
            raise ValueError(f"Plan '{name}' is already registered")

        plan_info = PlanInfo.from_function(name, func, category)
        self._plans[name] = plan_info
        self._categories.add(category)

        logger.debug(f"Registered plan: {name} (category: {category})")
        return plan_info

    def register_decorator(
        self, name: str | None = None, category: str = "general"
    ) -> Callable[[Callable], Callable]:
        """Decorator to register a plan function.

        Args:
            name: Optional plan name (defaults to function name).
            category: Plan category.

        Returns:
            Decorator function.

        Example:
            >>> @registry.register_decorator(category="custom")
            ... def my_custom_scan(detectors, motor, start, stop, num):
            ...     yield from bp.scan(detectors, motor, start, stop, num)
        """

        def decorator(func: Callable) -> Callable:
            plan_name = name or func.__name__
            self.register(plan_name, func, category)
            return func

        return decorator

    def unregister(self, name: str) -> bool:
        """Unregister a plan.

        Args:
            name: Plan name.

        Returns:
            True if plan was unregistered.
        """
        if name in self._plans:
            del self._plans[name]
            logger.debug(f"Unregistered plan: {name}")
            return True
        return False

    def get_plan(self, name: str) -> PlanInfo | None:
        """Get a plan by name.

        Args:
            name: Plan name.

        Returns:
            PlanInfo or None if not found.
        """
        return self._plans.get(name)

    def list_plans(self, category: str | None = None) -> list[PlanInfo]:
        """List all registered plans.

        Args:
            category: Optional category filter.

        Returns:
            List of PlanInfo objects.
        """
        if category is None:
            return list(self._plans.values())
        return [p for p in self._plans.values() if p.category == category]

    def get_categories(self) -> list[str]:
        """Get list of plan categories.

        Returns:
            Sorted list of category names.
        """
        return sorted(self._categories)

    def search(self, query: str) -> list[PlanInfo]:
        """Search plans by name or description.

        Args:
            query: Search string.

        Returns:
            List of matching plans.
        """
        query_lower = query.lower()
        results = []
        for plan in self._plans.values():
            if query_lower in plan.name.lower() or query_lower in plan.description.lower():
                results.append(plan)
        return results

    @property
    def plan_names(self) -> list[str]:
        """Get list of registered plan names."""
        return list(self._plans.keys())

    def __contains__(self, name: str) -> bool:
        """Check if a plan is registered."""
        return name in self._plans

    def __len__(self) -> int:
        """Get number of registered plans."""
        return len(self._plans)


def create_default_registry() -> PlanRegistry:
    """Create a registry with standard Bluesky plans.

    Returns:
        PlanRegistry with standard plans registered.
    """
    registry = PlanRegistry()

    try:
        from bluesky import plans as bp

        # Scan plans
        if hasattr(bp, "scan"):
            registry.register("scan", bp.scan, "scan")
        if hasattr(bp, "rel_scan"):
            registry.register("rel_scan", bp.rel_scan, "scan")
        if hasattr(bp, "grid_scan"):
            registry.register("grid_scan", bp.grid_scan, "scan")
        if hasattr(bp, "rel_grid_scan"):
            registry.register("rel_grid_scan", bp.rel_grid_scan, "scan")
        if hasattr(bp, "list_scan"):
            registry.register("list_scan", bp.list_scan, "scan")
        if hasattr(bp, "rel_list_scan"):
            registry.register("rel_list_scan", bp.rel_list_scan, "scan")
        if hasattr(bp, "adaptive_scan"):
            registry.register("adaptive_scan", bp.adaptive_scan, "scan")

        # Count plans
        if hasattr(bp, "count"):
            registry.register("count", bp.count, "count")

        # Alignment plans
        if hasattr(bp, "tune_centroid"):
            registry.register("tune_centroid", bp.tune_centroid, "alignment")
        if hasattr(bp, "spiral"):
            registry.register("spiral", bp.spiral, "alignment")
        if hasattr(bp, "spiral_fermat"):
            registry.register("spiral_fermat", bp.spiral_fermat, "alignment")

        # Fly scan plans
        if hasattr(bp, "fly"):
            registry.register("fly", bp.fly, "fly")

        # Multi-dimensional plans
        if hasattr(bp, "inner_product_scan"):
            registry.register("inner_product_scan", bp.inner_product_scan, "scan")
        if hasattr(bp, "outer_product_scan"):
            registry.register("outer_product_scan", bp.outer_product_scan, "scan")

        logger.info(f"Registered {len(registry)} standard Bluesky plans")

    except ImportError:
        logger.warning("bluesky not available, no standard plans registered")

    return registry


def get_registry() -> PlanRegistry:
    """Get the global plan registry.

    Returns:
        The singleton PlanRegistry instance.
    """
    return PlanRegistry.get_instance()
