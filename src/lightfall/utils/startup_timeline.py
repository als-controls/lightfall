"""Opt-in startup timeline profiler and HTML/JSON report generator."""

from __future__ import annotations

import cProfile
import html
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

from lightfall.utils.logging import logger

__all__ = [
    "ENV_VAR",
    "enabled",
    "mark_origin",
    "phase",
    "mark",
    "finish",
    "get_records",
]

ENV_VAR = "LIGHTFALL_STARTUP_PROFILE"

_lock = threading.Lock()
_origin: float = time.perf_counter()
_records: list[dict[str, Any]] = []
_local = threading.local()
_finished_path: Path | None = None
_profiler: cProfile.Profile | None = None
_NOOP: AbstractContextManager[None] = nullcontext()


def enabled() -> bool:
    """Return True when LIGHTFALL_STARTUP_PROFILE is non-empty and not '0'."""
    val = os.environ.get(ENV_VAR, "").strip()
    return bool(val and val != "0")


def mark_origin() -> None:
    """Reset the timeline origin, clear recorded phases, and start cProfile if requested."""
    global _origin, _local, _finished_path, _profiler
    with _lock:
        if _profiler is not None:
            try:
                _profiler.disable()
            except Exception:
                pass
            _profiler = None
        _origin = time.perf_counter()
        _records.clear()
        _local = threading.local()
        _finished_path = None
        if os.environ.get(ENV_VAR, "").strip().lower() == "cprofile":
            _profiler = cProfile.Profile()
            _profiler.enable()


def get_records() -> list[dict[str, Any]]:
    """Return a shallow copy of the recorded timeline events."""
    with _lock:
        return [dict(r) for r in _records]


@contextmanager
def _active_phase(name: str) -> Iterator[None]:
    """Record start, end, nesting depth, thread, and error status for a phase."""
    depth = int(getattr(_local, "depth", 0))
    start_s = time.perf_counter() - _origin
    rec: dict[str, Any] = {
        "name": name,
        "start_s": start_s,
        "end_s": start_s,
        "depth": depth,
        "thread": threading.current_thread().name,
    }
    with _lock:
        _records.append(rec)
    _local.depth = depth + 1
    try:
        yield
    except BaseException:
        rec["error"] = True
        raise
    finally:
        rec["end_s"] = time.perf_counter() - _origin
        _local.depth = depth


def phase(name: str) -> AbstractContextManager[None]:
    """Context manager that records a timed startup phase when enabled."""
    if not enabled():
        return _NOOP
    return _active_phase(name)


def mark(name: str) -> None:
    """Record an instant (zero-duration) event on the timeline when enabled."""
    if not enabled():
        return
    t = time.perf_counter() - _origin
    depth = int(getattr(_local, "depth", 0))
    rec: dict[str, Any] = {
        "name": name,
        "start_s": t,
        "end_s": t,
        "depth": depth,
        "thread": threading.current_thread().name,
        "instant": True,
    }
    with _lock:
        _records.append(rec)


def _render_html(records: list[dict[str, Any]], total_s: float) -> str:
    """Render a self-contained HTML Gantt timeline with inline CSS and SVG."""
    span_s = max(total_s, 1e-6)
    total_ms = total_s * 1000.0
    label_col_w = 320
    chart_w = 680
    svg_w = label_col_w + chart_w + 40
    row_h = 28
    top_pad = 32
    svg_h = max(top_pad + len(records) * row_h + 20, 80)

    svg_elements: list[str] = []
    svg_elements.append(
        f'<line x1="{label_col_w}" y1="0" x2="{label_col_w}" y2="{svg_h}" stroke="#444" stroke-width="1" />'
    )

    for idx, rec in enumerate(records):
        y = top_pad + idx * row_h
        raw_name = str(rec.get("name", ""))
        esc_name = html.escape(raw_name)
        depth = int(rec.get("depth", 0))
        start_s = float(rec.get("start_s", 0.0))
        end_s = float(rec.get("end_s", start_s))
        dur_ms = max(0.0, (end_s - start_s) * 1000.0)
        indent_x = 10 + depth * 16
        bar_x = label_col_w + (start_s / span_s) * chart_w

        if rec.get("instant"):
            label_text = f"{esc_name} (@ {start_s * 1000.0:.0f} ms)"
            svg_elements.append(
                f'<text x="{indent_x}" y="{y + 18}" class="label mark-label">{label_text}</text>'
            )
            svg_elements.append(
                f'<line x1="{bar_x:.2f}" y1="{ top_pad - 8 }" x2="{bar_x:.2f}" y2="{svg_h - 10}" class="mark-line" />'
            )
            svg_elements.append(
                f'<text x="{bar_x + 4:.2f}" y="{y + 18}" class="bar-text mark-text">{esc_name}</text>'
            )
        else:
            bar_w = max(2.0, ((end_s - start_s) / span_s) * chart_w)
            bar_class = "bar bar-error" if rec.get("error") else "bar"
            label_text = f"{esc_name} - {dur_ms:.0f} ms"
            svg_elements.append(
                f'<text x="{indent_x}" y="{y + 18}" class="label">{label_text}</text>'
            )
            svg_elements.append(
                f'<rect x="{bar_x:.2f}" y="{y + 4}" width="{bar_w:.2f}" height="{row_h - 8}" rx="3" class="{bar_class}" />'
            )
            svg_elements.append(
                f'<text x="{bar_x + 6:.2f}" y="{y + 18}" class="bar-text">{label_text}</text>'
            )

    rows_html: list[str] = []
    for rec in records:
        raw_name = str(rec.get("name", ""))
        esc_name = html.escape(raw_name)
        depth = int(rec.get("depth", 0))
        start_s = float(rec.get("start_s", 0.0))
        end_s = float(rec.get("end_s", start_s))
        dur_ms = max(0.0, (end_s - start_s) * 1000.0)
        left_pct = min(100.0, max(0.0, (start_s / span_s) * 100.0))
        width_pct = min(100.0 - left_pct, max(0.2, ((end_s - start_s) / span_s) * 100.0))
        indent_px = depth * 16
        if rec.get("instant"):
            rows_html.append(
                f'<div class="row mark-row"><div class="row-label" style="padding-left: {indent_px}px">{esc_name}</div>'
                f'<div class="row-track"><div class="mark-tick" style="left: {left_pct:.2f}%" title="{esc_name}"></div></div></div>'
            )
        else:
            err_cls = " row-bar-error" if rec.get("error") else ""
            label_text = f"{esc_name} - {dur_ms:.0f} ms"
            rows_html.append(
                f'<div class="row"><div class="row-label" style="padding-left: {indent_px}px">{label_text}</div>'
                f'<div class="row-track"><div class="row-bar{err_cls}" style="left: {left_pct:.2f}%; width: {width_pct:.2f}%"></div></div></div>'
            )

    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8" />',
        "<title>Lightfall Startup Timeline</title>",
        "<style>",
        "body { font-family: system-ui, -apple-system, sans-serif; background: #181a1f; color: #e6edf3; margin: 24px; }",
        "h1 { font-size: 20px; margin-bottom: 16px; }",
        ".summary { color: #9da7b3; margin-bottom: 16px; font-size: 14px; }",
        ".timeline-table { margin-bottom: 24px; border: 1px solid #30363d; border-radius: 6px; padding: 12px; background: #0d1117; }",
        ".row { display: flex; align-items: center; height: 26px; border-bottom: 1px solid #21262d; font-size: 13px; }",
        ".row:last-child { border-bottom: none; }",
        ".row-label { width: 320px; flex-shrink: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; box-sizing: border-box; }",
        ".row-track { position: relative; flex-grow: 1; height: 18px; background: #161b22; border-radius: 3px; }",
        ".row-bar { position: absolute; top: 2px; bottom: 2px; background: #2f81f7; border-radius: 2px; }",
        ".row-bar-error { background: #f85149; }",
        ".mark-tick { position: absolute; top: 0; bottom: 0; width: 2px; background: #d29922; }",
        "svg { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; display: block; max-width: 100%; }",
        ".label { fill: #e6edf3; font-size: 12px; font-family: monospace; }",
        ".mark-label { fill: #d29922; }",
        ".bar { fill: #2f81f7; opacity: 0.85; }",
        ".bar-error { fill: #f85149; opacity: 0.9; }",
        ".bar-text { fill: #ffffff; font-size: 11px; font-family: monospace; pointer-events: none; }",
        ".mark-line { stroke: #d29922; stroke-width: 1.5; stroke-dasharray: 4 2; }",
        ".mark-text { fill: #d29922; }",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>Lightfall Startup Timeline &mdash; Total: {total_ms:.0f} ms ({total_s:.3f} s)</h1>",
        f'<div class="summary">Total duration: {total_ms:.0f} ms ({len(records)} events)</div>',
        '<div class="timeline-table">',
        *rows_html,
        "</div>",
        f'<svg width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}" xmlns="http://www.w3.org/2000/svg">',
        *svg_elements,
        "</svg>",
        "</body>",
        "</html>",
    ]
    return "\n".join(lines) + "\n"


def finish(out_dir: Path) -> Path | None:
    """Write the startup timeline JSON and HTML files when enabled; idempotent."""
    global _finished_path, _profiler
    if not enabled():
        return None

    with _lock:
        if _finished_path is not None:
            return _finished_path

        target_dir = Path(out_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")

        if _profiler is not None:
            try:
                _profiler.disable()
                prof_path = target_dir / f"startup-{ts}.prof"
                _profiler.dump_stats(str(prof_path))
            finally:
                _profiler = None

        snapshot = [dict(r) for r in _records]
        total_s = max((float(r.get("end_s", 0.0)) for r in snapshot), default=0.0)

        json_path = target_dir / f"startup-{ts}.json"
        html_path = target_dir / f"startup-{ts}.html"

        payload = {
            "total_s": total_s,
            "records": snapshot,
        }
        json_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        html_path.write_text(
            _render_html(snapshot, total_s),
            encoding="utf-8",
            newline="\n",
        )
        _finished_path = html_path

    logger.info("Startup timeline written to {}", html_path)
    return html_path
