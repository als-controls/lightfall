"""NXsas converter — exports run data as NXsas-compliant HDF5."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from lightfall.exporter.converters import register_converter
from lightfall.exporter.converters.base import Converter


@register_converter
class NxsasConverter(Converter):
    """Export run image data as NXsas-compliant HDF5 with optional ROI cropping."""

    name = "nxsas"

    async def export(
        self,
        run_client: Any,
        run_uid: str,
        params: dict[str, Any],
        output_dir: Path,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{run_uid}.h5"

        if progress_cb:
            progress_cb("Reading image data")

        stream = run_client["primary"]
        fields = list(stream.keys())
        if not fields:
            raise ValueError(f"No fields found in primary stream for run {run_uid}")

        image_field = fields[0]
        image_data = np.asarray(stream[image_field].read())

        roi = params.get("roi")
        if roi:
            x, y = roi["x"], roi["y"]
            w, h = roi["width"], roi["height"]
            if image_data.ndim == 3:
                image_data = image_data[:, y : y + h, x : x + w]
            elif image_data.ndim == 2:
                image_data = image_data[y : y + h, x : x + w]

        if progress_cb:
            progress_cb("Writing HDF5")

        metadata = getattr(run_client, "metadata", {})
        start_doc = metadata.get("start", {})

        with h5py.File(out_file, "w") as f:
            entry = f.create_group("entry")
            entry.attrs["NX_class"] = "NXentry"
            data_group = entry.create_group("data")
            data_group.attrs["NX_class"] = "NXdata"
            data_group.create_dataset("data", data=image_data, compression="gzip")
            if start_doc:
                entry.attrs["run_uid"] = start_doc.get("uid", run_uid)
                if "plan_name" in start_doc:
                    entry.attrs["plan_name"] = start_doc["plan_name"]
                if "time" in start_doc:
                    entry.attrs["start_time"] = start_doc["time"]

        if progress_cb:
            progress_cb(f"Written to {out_file.name}")
