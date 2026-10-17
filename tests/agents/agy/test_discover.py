"""discover: console_python, find_agy, agy_signed_in (no real agy)."""
from __future__ import annotations

import sys

from lightfall.agents.backends.agy import discover


def test_console_python_swaps_pythonw(tmp_path, monkeypatch):
    (tmp_path / "pythonw.exe").write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(discover.sys, "executable", str(tmp_path / "pythonw.exe"))
    assert discover.console_python() == str(tmp_path / "python.exe")


def test_console_python_keeps_pythonw_without_sibling(tmp_path, monkeypatch):
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(discover.sys, "executable", str(tmp_path / "pythonw.exe"))
    assert discover.console_python() == str(tmp_path / "pythonw.exe")


def test_console_python_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(discover.sys, "executable", str(tmp_path / "python.exe"))
    assert discover.console_python() == str(tmp_path / "python.exe")


def test_find_agy_order(tmp_path, monkeypatch):
    pref = tmp_path / "pref-agy.exe"
    pref.write_bytes(b"")
    monkeypatch.setattr(discover.shutil, "which", lambda name: "C:/on/path/agy.exe")
    assert discover.find_agy(str(pref)) == str(pref)
    assert discover.find_agy(str(tmp_path / "missing.exe")) == "C:/on/path/agy.exe"
    monkeypatch.setattr(discover.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert discover.find_agy(None) is None
    default = tmp_path / "agy" / "bin" / "agy.exe"
    default.parent.mkdir(parents=True)
    default.write_bytes(b"")
    assert discover.find_agy(None) == str(default)


def _script(tmp_path, body):
    p = tmp_path / "fake_models.py"
    p.write_text(body, encoding="utf-8")
    return [sys.executable, str(p)]


def test_agy_signed_in_counts_models(tmp_path):
    agy = _script(tmp_path, "import os, sys\nassert sys.argv[1] == 'models'\n"
                            "assert os.environ['USERPROFILE'] == os.environ['HOME']\n"
                            "print('Fetching available models...')\n"
                            "print('gemini-4-argon-high\\tGemini 4 Argon (High)')\n"
                            "print('gemini-3.8-flash-low\\tGemini 3.8 Flash (Low)')\n")
    assert discover.agy_signed_in(agy, tmp_path) == (True, "2 models")


def test_agy_signed_in_failure_reports_stderr(tmp_path):
    agy = _script(tmp_path, "import sys\nsys.stderr.write('not signed in\\n')\nsys.exit(1)\n")
    assert discover.agy_signed_in(agy, tmp_path) == (False, "not signed in")


def test_agy_signed_in_no_models(tmp_path):
    agy = _script(tmp_path, "print('Fetching available models...')\n")
    ok, msg = discover.agy_signed_in(agy, tmp_path)
    assert ok is False and msg


def test_package_import_stays_cheap_for_hook_client():
    # agy spawns `python -I -m ...agy.hook_client` per tool call; the package
    # __init__ must not pull in the backend (uvicorn/mcp) eagerly.
    import os
    import subprocess

    import lightfall

    src = os.path.dirname(os.path.dirname(lightfall.__file__))
    code = ("import sys, lightfall.agents.backends.agy.hook_client\n"
            "heavy = [m for m in ('uvicorn', 'mcp', 'lightfall.agents.backends.agy.backend')"
            " if m in sys.modules]\n"
            "print(heavy)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": src}, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
    from lightfall.agents.backends.agy import AgyCliBackend  # lazy export still works
    assert AgyCliBackend.__name__ == "AgyCliBackend"
