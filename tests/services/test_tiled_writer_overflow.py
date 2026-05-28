"""Regression tests for numeric-overflow sanitization in tiled_writer_patch.

Root cause (see project memory tiled-ctrl-limit-overflow-500): an unbounded
EPICS axis reports +/-inf control limits. Upstream ``truncate_json_overflow``
clamps +/-inf to the float ``1.7976e308``, which PostgreSQL ``jsonb`` round-trips
into a ~309-digit integer that neither msgpack nor orjson can encode -- so the
Tiled server returns 500 on every read of that stream's metadata.

The fix clamps overflowing / non-finite numbers (and numpy scalars) into the
JSON-safe integer range so that nothing written into catalog metadata can ever
expand beyond 64 bits, regardless of which wire encoder reads it back.
"""
from __future__ import annotations

import math

import msgpack
import numpy
import orjson

JSON_SAFE_MAX = 2**53 - 1


def _max_int_magnitude(obj):
    """Largest ``abs(int(x))`` over every real number nested in ``obj``.

    Returns ``math.inf`` if any non-finite float survives -- that alone is a
    failure, since it cannot be serialized at all.
    """
    worst = 0
    stack = [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, bool):
            continue
        if isinstance(x, float) and not math.isfinite(x):
            return math.inf
        if isinstance(x, (int, float)):
            worst = max(worst, abs(int(x)))
        elif isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
    return worst


def test_positive_inf_clamped_into_json_safe_range():
    from lucid.services.tiled_writer_patch import safe_truncate_json_overflow

    out = safe_truncate_json_overflow(float("inf"))
    assert abs(int(out)) <= JSON_SAFE_MAX


def test_negative_inf_clamped_into_json_safe_range():
    from lucid.services.tiled_writer_patch import safe_truncate_json_overflow

    out = safe_truncate_json_overflow(float("-inf"))
    assert abs(int(out)) <= JSON_SAFE_MAX


def test_nan_becomes_none():
    from lucid.services.tiled_writer_patch import safe_truncate_json_overflow

    assert safe_truncate_json_overflow(float("nan")) is None


def test_numpy_uint64_is_clamped_and_de_numpied():
    from lucid.services.tiled_writer_patch import safe_truncate_json_overflow

    out = safe_truncate_json_overflow(numpy.uint64(2**64 - 1))
    assert not isinstance(out, numpy.generic)
    assert abs(int(out)) <= JSON_SAFE_MAX


def test_safe_values_pass_through_unchanged():
    from lucid.services.tiled_writer_patch import safe_truncate_json_overflow

    payload = {"a": 5, "b": -500.0, "c": "Micron", "d": [1, 2, 3], "e": None}
    assert safe_truncate_json_overflow(payload) == payload


def test_descriptor_with_inf_limits_is_wire_safe():
    """The actual failure shape: +/-inf ctrl_limits inside data_keys."""
    from lucid.services.tiled_writer_patch import safe_truncate_json_overflow

    desc = {
        "data_keys": {
            "sample_lift": {
                "dtype": "number",
                "lower_ctrl_limit": float("-inf"),
                "upper_ctrl_limit": float("inf"),
            }
        }
    }
    out = safe_truncate_json_overflow(desc)

    # Nothing can expand beyond the JSON-safe range (the jsonb -> int trap)...
    assert _max_int_magnitude(out) <= JSON_SAFE_MAX
    # ...and both wire encoders the server uses accept it.
    msgpack.packb(out)
    orjson.dumps(out)


def test_patch_is_installed_on_upstream_writer_module():
    """Importing the patch module must fix the very function that the inherited
    ``_RunWriter.descriptor`` / ``start`` / ``stop`` call when writing metadata.
    """
    import bluesky_tiled_plugins.writing.tiled_writer as tw

    import lucid.services.tiled_writer_patch  # noqa: F401  (installs the patch)

    out = tw.truncate_json_overflow(float("inf"))
    assert abs(int(out)) <= JSON_SAFE_MAX
