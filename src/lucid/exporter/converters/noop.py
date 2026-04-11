"""NoOp converter — exports raw data arrays to numpy files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from lucid.exporter.converters.base import Converter


class NoOpConverter(Converter):
    """Export run data as raw numpy arrays, one .npy file per field."""

    name = "noop"

    async def export(
        self,
        run_client: Any,
        run_uid: str,
        params: dict[str, Any],
        output_dir: Path,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        run_dir = output_dir / run_uid
        run_dir.mkdir(parents=True, exist_ok=True)

        stream = run_client["primary"]
        fields = list(stream.keys())

        for field_name in fields:
            if progress_cb:
                progress_cb(f"Saving {field_name}")
            data = np.asarray(stream[field_name].read())
            np.save(run_dir / f"{field_name}.npy", data)
