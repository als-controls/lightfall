"""Local override of ``bluesky_tiled_plugins.TiledWriter`` that builds the
SQL appendable-table schema from the bluesky descriptor's ``data_keys``
instead of inferring it from ``pyarrow.Table.from_pylist(data_cache)``.

Why this exists
---------------

The upstream writer (``bluesky_tiled_plugins.writing.tiled_writer._RunWriter._write_internal_data``)
materialises the SQL table at first-flush time with a schema inferred
from whatever ``pyarrow`` decided each column should be, given the
sample of rows in the first batch. Every subsequent batch is then cast
to that frozen schema by ``tiled.adapters.sql.append_partition``::

    table = table.cast(self.structure().arrow_schema_decoded)

If the first batch happens to contain integer-looking values for a
column whose actual stream dtype is float (e.g. a motor at the
literally-integer ``start`` position emitted before any float-valued
intermediate position), pyarrow infers ``int64``, and a later
``-4.5`` raises ``ArrowInvalid: Float value … was truncated converting
to int64``. Stamina retries 10x, the writer's ``stop`` never reaches
``update_metadata({"stop": ...})``, and the run on Tiled is left
``exit_status: "unknown", stop_time: null``.

bluesky descriptors already carry ``data_keys[name]["dtype_numpy"]``
(the upstream code itself populates that field at
``tiled_writer.RunNormalizer.descriptor`` lines 466-471). Those types
are authoritative; the writer simply doesn't consult them when building
the table schema. This subclass closes that gap.

This is a workaround until upstream accepts a fix. When updating
bluesky_tiled_plugins, re-check
``_RunWriter._write_internal_data`` for upstream changes.

Pinned-against version: bluesky_tiled_plugins as of 2026-05.
"""

from __future__ import annotations

import collections.abc
import copy
import math
from typing import Any

import numpy
import pyarrow
from bluesky_tiled_plugins.writing.tiled_writer import (
    BATCH_SIZE,
)
from bluesky_tiled_plugins.writing.tiled_writer import (
    TiledWriter as _UpstreamTiledWriter,
)
from bluesky_tiled_plugins.writing.tiled_writer import (
    _RunWriter as _UpstreamRunWriter,
)

from lightfall.utils.logging import logger

# JSON's exact-integer range. Values outside it are not safe to store: Tiled's
# PostgreSQL catalog keeps numbers in ``jsonb`` losslessly, but the wire
# encoders that read them back (msgpack by default, orjson for JSON) are bound
# to 64 bits, so an oversized integer 500s on read.
_JSON_INT_MIN = 1 - 2**53
_JSON_INT_MAX = 2**53 - 1


def safe_truncate_json_overflow(data: Any) -> Any:
    """Recursively clamp numbers into the JSON/msgpack-safe integer range.

    Drop-in replacement for ``bluesky_tiled_plugins``'
    ``truncate_json_overflow`` that closes two defects which let un-encodable
    values reach Tiled's catalog and then 500 on every metadata read:

    * Upstream clamps ``+/-inf`` to the float ``1.7976e308``. That value's
      integer expansion is ~309 digits, so once PostgreSQL stores it as a
      ``jsonb`` ``numeric`` and a client reads it back as a Python ``int``,
      neither msgpack nor orjson can encode it. We clamp non-finite and
      out-of-range floats into ``+/-(2**53-1)`` instead (matching how the
      integer path already behaves). Triggered in practice by unbounded EPICS
      axes whose ``*_ctrl_limit`` reads as ``+/-inf``.
    * Upstream only inspects Python ``int``/``float``, so ``numpy`` integer
      scalars (common in ophyd ``configuration`` readings) pass through
      untouched. We coerce ``numpy`` scalars via ``.item()`` first.

    ``NaN`` becomes ``None`` (JSON has no NaN and orjson rejects it); ``bool``
    is preserved; strings, ``None`` and in-range numbers pass through unchanged.
    """
    if isinstance(data, numpy.generic):
        # numpy scalar -> native Python scalar so the checks below apply
        data = data.item()

    if isinstance(data, collections.abc.Mapping):
        return {k: safe_truncate_json_overflow(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [safe_truncate_json_overflow(item) for item in data]

    # bool is a subclass of int; never coerce True/False into numbers
    if isinstance(data, bool):
        return data

    if isinstance(data, int):
        return max(min(data, _JSON_INT_MAX), _JSON_INT_MIN)

    if isinstance(data, float):
        if math.isnan(data):
            return None
        if not (_JSON_INT_MIN <= data <= _JSON_INT_MAX):
            # Covers +/-inf and any finite magnitude past the safe range.
            return float(max(min(data, _JSON_INT_MAX), _JSON_INT_MIN))
        return data

    return data


def _install_overflow_patch() -> None:
    """Point the upstream writer at :func:`safe_truncate_json_overflow`.

    The inherited ``_RunWriter.descriptor`` / ``start`` / ``stop`` sanitize
    their metadata by calling ``truncate_json_overflow`` resolved from the
    ``bluesky_tiled_plugins.writing.tiled_writer`` module namespace, so
    rebinding it there fixes the descriptor ``data_keys`` (where the oversized
    ctrl-limits live) without reimplementing those handlers. Idempotent.
    """
    import bluesky_tiled_plugins.writing.tiled_writer as _tw

    if getattr(_tw.truncate_json_overflow, "_lightfall_overflow_safe", False):
        return
    safe_truncate_json_overflow._lightfall_overflow_safe = True  # type: ignore[attr-defined]
    _tw.truncate_json_overflow = safe_truncate_json_overflow
    logger.debug(
        "Installed safe truncate_json_overflow on bluesky_tiled_plugins writer"
    )


_install_overflow_patch()


def _override_schema_from_data_keys(
    inferred_schema: pyarrow.Schema,
    data_keys: dict[str, Any],
) -> pyarrow.Schema:
    """Return a copy of ``inferred_schema`` with each field's type replaced by
    the type declared in ``data_keys[field.name]["dtype_numpy"]`` when present.

    Columns absent from ``data_keys`` (``seq_num``, ``time``, ``ts_*``) keep
    their inferred types. Null and list-of-null columns get the upstream
    string-fallback treatment so empty event streams don't error out.
    """
    schema = copy.copy(inferred_schema)
    for i, field in enumerate(inferred_schema):
        # Upstream's null-type fallbacks (preserved verbatim — empty columns
        # would otherwise serialize to a Tiled-incompatible all-null type).
        if pyarrow.types.is_null(field.type):
            schema = schema.set(i, field.with_type(pyarrow.string()))
            continue
        if pyarrow.types.is_list(field.type) and pyarrow.types.is_null(
            field.type.value_type
        ):
            schema = schema.set(
                i, field.with_type(pyarrow.list_(pyarrow.string()))
            )
            continue

        col_meta = data_keys.get(field.name)
        if not col_meta:
            continue

        # Array-valued keys (e.g. AreaDetector ROI time-series waveforms) declare
        # a *scalar* ``dtype_numpy`` (the element type, e.g. ``<f8``) alongside
        # ``dtype: "array"``. Upstream only routes a key to the separate zarr
        # array path when ``0 <= max_array_size < sum(shape)``, so an array whose
        # ``shape`` sums to 0 (an empty/idle time-series buffer) stays in the
        # tabular table. Coercing such a column to ``from_numpy_dtype(dtype_numpy)``
        # would freeze a *scalar* Arrow type for list-valued data; the
        # server-side ``table.cast`` in ``append_partition`` (list -> scalar)
        # then raises and surfaces as a 500, orphaning the run. Leave these
        # columns at their inferred list type — the null-list fallbacks above
        # already normalise empty arrays.
        if col_meta.get("dtype") == "array":
            continue

        dtype_numpy = col_meta.get("dtype_numpy")
        if not dtype_numpy:
            continue

        try:
            declared = pyarrow.from_numpy_dtype(numpy.dtype(dtype_numpy))
        except (ValueError, TypeError):
            # Unrecognised dtype string — leave the inferred type alone
            # rather than guess. Logged at debug because some descriptor
            # specs use exotic dtype strings the writer just doesn't model.
            logger.debug(
                "tiled_writer_patch: unrecognised dtype_numpy={!r} for column "
                "{!r}; falling back to inferred type {!s}",
                dtype_numpy, field.name, field.type,
            )
            continue

        if declared != field.type:
            schema = schema.set(i, field.with_type(declared))
    return schema


class _DescribedSchemaRunWriter(_UpstreamRunWriter):
    """``_RunWriter`` whose internal-table schema honours descriptor dtypes."""

    def _write_internal_data(
        self,
        data_cache: list[dict[str, Any]],
        desc_node: Any,
    ) -> None:
        # Reimplements upstream `_RunWriter._write_internal_data` byte-for-byte
        # except the schema-construction block. Keep this method aligned with
        # upstream when bluesky_tiled_plugins updates.
        desc_name = desc_node.item["id"]

        # 1. Internal *array* data (zarr) — delegate would be cleaner, but
        # upstream packs everything in one method. Replicate verbatim.
        for key in self._int_array_keys[desc_name]:
            arr_lst = [row.pop(key) for row in data_cache if key in row]

            min_len, max_len = (
                min(len(row) for row in arr_lst),
                max(len(row) for row in arr_lst),
            )
            if min_len != max_len:
                arr_lst = [
                    row + [numpy.nan] * (max_len - len(row)) for row in arr_lst
                ]
                msg = (
                    f"Array lengths for key '{key}' in stream '{desc_name}' "
                    f"are not consistent: min={min_len}, max={max_len}; "
                    f"the arrays are padded with NaNs."
                )
                logger.warning(msg)
                self.notes.append(msg)

            if not (arr_client := self._internal_arrays.get(f"{desc_name}/{key}")):
                metadata = safe_truncate_json_overflow(self.data_keys.get(key, {}))
                arr_client = desc_node.write_array(
                    numpy.array(arr_lst),
                    key=key,
                    metadata=metadata,
                    dims=("time", "dim_1"),
                    access_tags=self.access_tags,
                )
                self._internal_arrays[f"{desc_name}/{key}"] = arr_client
                self.notes.append(
                    f"Internal array data for '{key}' in stream "
                    f"'{desc_name}' written as zarr."
                )
            else:
                arr_client.patch(
                    numpy.array(arr_lst),
                    offset=arr_client.shape[:1],
                    extend=True,
                )

        # 2. Internal *tabular* data — schema construction is the part that
        # diverges from upstream.
        if not (table := pyarrow.Table.from_pylist(data_cache)):
            return

        if not (df_client := self._internal_tables.get(desc_name)):
            metadata = {
                k: v for k, v in self.data_keys.items() if k in table.column_names
            }
            metadata = safe_truncate_json_overflow(metadata)
            schema = _override_schema_from_data_keys(table.schema, self.data_keys)
            df_client = desc_node.create_appendable_table(
                schema=schema,
                key="internal",
                metadata=metadata,
                access_tags=self.access_tags,
            )
            self._internal_tables[desc_name] = df_client

        df_client.append_partition(0, table)


class TiledWriter(_UpstreamTiledWriter):
    """``TiledWriter`` that uses ``_DescribedSchemaRunWriter`` per run.

    Drop-in replacement for ``bluesky_tiled_plugins.TiledWriter`` — the only
    behavioural delta is the schema-from-descriptor construction inside
    ``_write_internal_data``. All other knobs (``batch_size``,
    ``max_array_size``, ``backup_directory``, ``patches``, ``validate``)
    behave identically.
    """

    def _factory(self, name: str, doc: dict[str, Any]) -> tuple[list[Any], list[Any]]:
        # Mirrors upstream `TiledWriter._factory` but instantiates our
        # subclass. Keep aligned with upstream.
        from bluesky_tiled_plugins.writing._json_writer import JSONLinesWriter
        from bluesky_tiled_plugins.writing.tiled_writer import _ConditionalBackup

        cb = run_writer = _DescribedSchemaRunWriter(
            self.client,
            batch_size=self._batch_size,
            max_array_size=self._max_array_size,
            validate=self._validate,
        )

        if self._normalizer:
            cb = self._normalizer(
                patches=self.patches, spec_to_mimetype=self.spec_to_mimetype
            )
            cb.subscribe(run_writer)

        if self.backup_directory:
            cb = _ConditionalBackup(cb, [JSONLinesWriter(self.backup_directory)])

        return [cb], []


__all__ = ["TiledWriter", "BATCH_SIZE"]
