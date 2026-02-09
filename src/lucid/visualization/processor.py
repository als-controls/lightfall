"""Document processor for extracting data characteristics.

DocumentProcessor is a Bluesky callback that analyzes start and descriptor
documents to extract DataCharacteristics for visualization selection.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from PySide6.QtCore import QObject, Signal

from lucid.visualization.spec import DataCharacteristics, FieldInfo


class DocumentProcessor(QObject):
    """Bluesky callback that extracts data characteristics for visualization.

    DocumentProcessor subscribes to the RunEngine and analyzes start and
    descriptor documents to build DataCharacteristics. Once characteristics
    are ready (after descriptor), it emits a signal for visualization setup.

    Signals:
        characteristics_ready(DataCharacteristics): Emitted when characteristics
            are fully extracted from start + descriptor documents.
        run_started(dict): Emitted on start document.
        run_stopped(dict): Emitted on stop document.

    Example:
        >>> processor = DocumentProcessor()
        >>> RE.subscribe(processor)
        >>> processor.characteristics_ready.connect(on_characteristics)
    """

    characteristics_ready = Signal(object)  # DataCharacteristics
    run_started = Signal(dict)
    run_stopped = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the document processor.

        Args:
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self._characteristics: DataCharacteristics | None = None
        self._start_doc: dict[str, Any] | None = None
        self._primary_descriptor: dict[str, Any] | None = None
        self._has_emitted = False

    def __call__(self, name: str, doc: dict[str, Any]) -> None:
        """Handle a Bluesky document.

        Args:
            name: Document type ('start', 'descriptor', 'event', 'stop').
            doc: Document dictionary.
        """
        if name == "start":
            self._handle_start(doc)
        elif name == "descriptor":
            self._handle_descriptor(doc)
        elif name == "stop":
            self._handle_stop(doc)

    def _handle_start(self, doc: dict[str, Any]) -> None:
        """Process start document.

        Args:
            doc: Start document.
        """
        self._start_doc = doc
        self._primary_descriptor = None
        self._characteristics = None
        self._has_emitted = False

        # Extract initial characteristics from start doc
        self._characteristics = self._extract_from_start(doc)

        self.run_started.emit(doc)
        logger.debug("Processing start document: {}", doc.get("uid", "")[:8])

    def _handle_descriptor(self, doc: dict[str, Any]) -> None:
        """Process descriptor document.

        Args:
            doc: Descriptor document.
        """
        stream_name = doc.get("name", "primary")

        # Only process primary stream for visualization selection
        if stream_name != "primary":
            logger.debug("Skipping non-primary descriptor: {}", stream_name)
            return

        if self._primary_descriptor is not None:
            logger.debug("Already have primary descriptor, skipping")
            return

        self._primary_descriptor = doc

        # Merge descriptor info into characteristics
        if self._characteristics:
            self._merge_descriptor_info(doc)

            # Emit characteristics ready signal
            if not self._has_emitted:
                self._has_emitted = True
                self.characteristics_ready.emit(self._characteristics)
                logger.debug(
                    "Characteristics ready: ndim={}, deps={}",
                    self._characteristics.ndim,
                    self._characteristics.dep_fields,
                )

    def _handle_stop(self, doc: dict[str, Any]) -> None:
        """Process stop document.

        Args:
            doc: Stop document.
        """
        self.run_stopped.emit(doc)
        logger.debug(
            "Run stopped: status={}",
            doc.get("exit_status", "unknown"),
        )

    def _extract_from_start(self, doc: dict[str, Any]) -> DataCharacteristics:
        """Extract characteristics from start document.

        Args:
            doc: Start document.

        Returns:
            Partially populated DataCharacteristics.
        """
        # Get hints section
        hints = doc.get("hints", {})

        # Extract dimensions from hints
        dimensions = hints.get("dimensions", [])
        dim_fields = self._parse_dimensions(dimensions)
        ndim = len(dim_fields) if dim_fields else 1

        # Get shape and extents
        shape = tuple(doc.get("shape", []))
        extents = self._parse_extents(doc.get("extents", []))

        # Get gridding info
        gridding = hints.get("gridding")
        is_rectilinear = gridding == "rectilinear"

        # Get num_points
        num_points = doc.get("num_points")

        # Fallback: use motors as dim_fields
        if not dim_fields:
            motors = doc.get("motors", [])
            if motors:
                dim_fields = list(motors)
                ndim = 1 if len(motors) == 1 else len(motors)

        return DataCharacteristics(
            ndim=ndim,
            dim_fields=dim_fields,
            dep_fields=[],  # Filled from descriptor
            num_points=num_points,
            shape=shape,
            extents=extents,
            is_rectilinear=is_rectilinear,
            gridding=gridding,
            field_info={},
            plan_name=doc.get("plan_name", ""),
            run_uid=doc.get("uid", ""),
            metadata=doc,
        )

    def _parse_dimensions(
        self, dimensions: list[Any]
    ) -> list[str]:
        """Parse dimensions hint into field names.

        The dimensions hint format is:
            [[field_list], stream_name]
        or for multiple dimensions:
            [[[field1], stream], [[field2], stream], ...]

        Args:
            dimensions: Dimensions hint from start document.

        Returns:
            List of dimension field names.
        """
        dim_fields = []

        if not dimensions:
            return dim_fields

        for dim in dimensions:
            if isinstance(dim, (list, tuple)) and len(dim) >= 1:
                # Format: [[fields], stream] or [fields]
                fields = dim[0]
                if isinstance(fields, (list, tuple)):
                    dim_fields.extend(str(f) for f in fields)
                elif isinstance(fields, str):
                    dim_fields.append(fields)

        return dim_fields

    def _parse_extents(
        self, extents: list[Any]
    ) -> tuple[tuple[float, float], ...]:
        """Parse extents into tuple of (min, max) pairs.

        Args:
            extents: Extents from start document.

        Returns:
            Tuple of (min, max) tuples for each dimension.
        """
        result = []
        for ext in extents:
            if isinstance(ext, (list, tuple)) and len(ext) >= 2:
                result.append((float(ext[0]), float(ext[1])))
        return tuple(result)

    def _merge_descriptor_info(self, doc: dict[str, Any]) -> None:
        """Merge descriptor information into characteristics.

        Args:
            doc: Descriptor document.
        """
        if not self._characteristics:
            return

        data_keys = doc.get("data_keys", {})
        hints = doc.get("hints", {})

        # Build FieldInfo for each data key
        for key, info in data_keys.items():
            shape = tuple(info.get("shape", []))
            dtype = info.get("dtype", "number")
            source = info.get("source", "")
            units = info.get("units", "")

            # Check if field is hinted
            is_hinted = False
            for device_hints in hints.values():
                if isinstance(device_hints, dict):
                    fields = device_hints.get("fields", [])
                    if key in fields:
                        is_hinted = True
                        break

            field_info = FieldInfo(
                name=key,
                dtype=dtype,
                shape=shape,
                units=units,
                source=source,
                is_hinted=is_hinted,
            )
            self._characteristics.field_info[key] = field_info

        # Determine dependent fields
        self._characteristics.dep_fields = self._determine_dep_fields(
            data_keys, hints
        )

    def _determine_dep_fields(
        self,
        data_keys: dict[str, Any],
        hints: dict[str, Any],
    ) -> list[str]:
        """Determine dependent (signal) fields.

        Uses hints to identify "interesting" fields. Falls back to
        heuristics based on field properties.

        Args:
            data_keys: Data keys from descriptor.
            hints: Hints from descriptor.

        Returns:
            List of dependent field names.
        """
        # Get hinted fields from all devices
        hinted_fields = []
        for _device_name, device_hints in hints.items():
            if isinstance(device_hints, dict):
                fields = device_hints.get("fields", [])
                hinted_fields.extend(fields)

        # Filter to only include fields in data_keys
        dep_fields = [f for f in hinted_fields if f in data_keys]

        if dep_fields:
            return dep_fields

        # Fallback: exclude dimension fields and pick remaining numeric fields
        dim_fields = set(self._characteristics.dim_fields if self._characteristics else [])

        for key, info in data_keys.items():
            if key in dim_fields:
                continue
            dtype = info.get("dtype", "")
            if dtype in ("number", "integer", "array"):
                dep_fields.append(key)

        return dep_fields

    # === Public API ===

    def get_characteristics(self) -> DataCharacteristics | None:
        """Get the current data characteristics.

        Returns:
            DataCharacteristics or None if not yet available.
        """
        return self._characteristics

    def clear(self) -> None:
        """Clear all stored state."""
        self._characteristics = None
        self._start_doc = None
        self._primary_descriptor = None
        self._has_emitted = False

    @property
    def is_ready(self) -> bool:
        """Check if characteristics are ready.

        Returns:
            True if both start and primary descriptor have been processed.
        """
        return (
            self._characteristics is not None
            and self._primary_descriptor is not None
        )

    @property
    def current_run_uid(self) -> str | None:
        """Get the UID of the current run.

        Returns:
            Run UID or None if no run in progress.
        """
        return self._start_doc.get("uid") if self._start_doc else None

    @property
    def current_plan_name(self) -> str:
        """Get the plan name of the current run.

        Returns:
            Plan name or empty string.
        """
        return self._start_doc.get("plan_name", "") if self._start_doc else ""
