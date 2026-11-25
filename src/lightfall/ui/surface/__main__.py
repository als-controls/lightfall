"""``python -m lightfall.ui.surface audit [--panel NAME ...] [--skip NAME ...] [--out report.md]``

Builds each registered panel (from the built-in manifest and installed plugin manifests)
in its own offscreen window, audits it, and prints a Markdown report.

The panels run in a child process. Panels built outside the app leave worker threads,
kernels and native resources behind, and tearing those down can crash the process after
the report is complete; the parent judges the run by the finished report, not by how the
child exits.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DONE_MARKER = "<!-- audit complete -->"
DEFAULT_SKIP = ("ipython",)  # its embedded kernel is not worth auditing offscreen


def _panel_plugins():
    from lightfall.plugins.builtin_manifest import builtin_manifest

    manifests = [builtin_manifest]
    try:
        from importlib.metadata import entry_points

        from lightfall.plugins.loader import PluginLoader

        for ep in entry_points(group=PluginLoader.ENTRY_POINT_GROUP):
            try:
                manifests.append(ep.load())
            except Exception:  # noqa: BLE001 - a broken plugin package is not our audit
                continue
    except Exception:  # noqa: BLE001
        pass
    for m in manifests:
        for entry in getattr(m, "plugins", []) or []:
            if getattr(entry, "type_name", "") == "panel":
                yield entry


def _load_panel_class(entry):
    import importlib

    module, _, attr = entry.import_path.partition(":")
    plugin = getattr(importlib.import_module(module), attr)()
    return plugin.get_panel_class()


def _worker(panels: list[str], skip: list[str], out: Path) -> None:
    """Build and audit the panels; write the report and the completion marker."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("OPHYD_CONTROL_LAYER", "dummy")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow

    from lightfall.ui.surface.audit import audit_window, report

    app = QApplication.instance() or QApplication(sys.argv[:1])

    def pump(seconds: float = 0.2) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    results, failures = {}, {}
    for entry in _panel_plugins():
        if panels and entry.name not in panels:
            continue
        if not panels and entry.name in skip:
            continue
        try:
            cls = _load_panel_class(entry)
            title = getattr(getattr(cls, "panel_metadata", None), "name", entry.name)
            host = QMainWindow()
            host.setWindowTitle(f"Audit: {title}")
            dock = QDockWidget(title, host)
            dock.setWidget(cls())
            host.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
            host.resize(1200, 900)
            host.show()
            pump(0.5)
            results[title] = audit_window(host, process_events=lambda: pump(0.1))
            host.close()
            host.deleteLater()
            pump(0.1)
        except Exception as e:  # noqa: BLE001 - report and carry on with the next panel
            failures[entry.name] = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
    out.write_text(report(results, failures) + "\n" + DONE_MARKER + "\n", encoding="utf-8")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lightfall.ui.surface")
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit", help="list widgets the agent UI surface cannot name")
    a.add_argument("--panel", action="append", default=[], help="panel plugin name (repeatable)")
    a.add_argument("--skip", action="append", default=None,
                   help=f"panel plugin name to skip (repeatable; default: {', '.join(DEFAULT_SKIP)})")
    a.add_argument("--out", help="also write the Markdown report here")
    a.add_argument("--worker-out", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    skip = list(args.skip) if args.skip is not None else list(DEFAULT_SKIP)

    if args.worker_out:
        _worker(args.panel, skip, Path(args.worker_out))
        return 0  # not reached

    with tempfile.TemporaryDirectory(prefix="lightfall-audit-") as tmp:
        report_path = Path(tmp) / "report.md"
        cmd = [sys.executable, "-m", "lightfall.ui.surface", "audit",
               "--worker-out", str(report_path)]
        for p in args.panel:
            cmd += ["--panel", p]
        for s in skip:
            cmd += ["--skip", s]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    if DONE_MARKER not in text:
        sys.stderr.write(f"audit did not finish (exit {proc.returncode}):\n"
                         + "\n".join(proc.stderr.splitlines()[-30:]) + "\n")
        return 1
    text = text.replace(DONE_MARKER, "").rstrip() + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
