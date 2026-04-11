"""Base converter interface for the exporter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable


class Converter(ABC):
    """Abstract base for export converters.

    Subclasses must set a ``name`` class attribute and implement ``export``.
    """

    name: str

    @abstractmethod
    async def export(
        self,
        run_client: Any,
        run_uid: str,
        params: dict[str, Any],
        output_dir: Path,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        """Export a single run.

        Args:
            run_client: Tiled run container (the entry for this run).
            run_uid: UID of the run being exported.
            params: Export parameters (converter-specific).
            output_dir: Directory to write output files.
            progress_cb: Optional callback for status detail strings.

        Raises:
            Exception: On export failure.
        """
