"""discover: console_python, find_agy, agy_signed_in (no real agy)."""
from __future__ import annotations

import sys

import pytest

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
    # agy spawns `python -I <path>/hook_client.py` per tool call; the package
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


def test_hook_command_plain_path_unquoted(tmp_path):
    py = tmp_path / "python.exe"
    script = tmp_path / "hook_client.py"
    py.write_bytes(b"")
    script.write_bytes(b"")
    cmd = discover.hook_command(py, script)
    assert '"' not in cmd and "\\" not in cmd
    assert cmd == f"{discover._hook_path(py)} -I {discover._hook_path(script)}"
    if sys.platform == "win32":
        assert cmd == f"{py.resolve().as_posix()} -I {script.resolve().as_posix()}"


def test_hook_command_space_uses_short_name_on_windows(tmp_path):
    if sys.platform != "win32":
        pytest.skip("8.3 short names are Windows only")
    d = tmp_path / "with space dir"
    d.mkdir()
    f = d / "hook_client.py"
    f.write_bytes(b"")
    short = discover._get_short_path_name(str(f.resolve()))
    if short == str(f.resolve()):
        pytest.skip("8.3 short names disabled")
    out = discover._hook_path(f)
    assert " " not in out and "\\" not in out


def test_hook_command_space_without_short_name_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(discover.sys, "platform", "win32")
    monkeypatch.setattr(discover, "_get_short_path_name", lambda p: p)
    d = tmp_path / "a b"
    d.mkdir()
    with pytest.raises(ValueError, match="unsafe for cmd"):
        discover.hook_command(d / "python.exe", d / "x.py")


def test_hook_path_posix_quotes_only_when_needed(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("POSIX path handling")
    monkeypatch.setattr(discover.sys, "platform", "linux")
    plain = tmp_path / "ab"
    assert discover._hook_path(plain) == str(plain.resolve())
    assert discover._hook_path(tmp_path / "a b").startswith("'")


@pytest.mark.parametrize("ch", ["&", "%"])
def test_hook_path_cmd_metachar_short_name_or_error(tmp_path, monkeypatch, ch):
    monkeypatch.setattr(discover.sys, "platform", "win32")
    d = tmp_path / f"a{ch}b"
    d.mkdir()
    f = d / "x.py"
    f.write_bytes(b"")
    monkeypatch.setattr(discover, "_get_short_path_name", lambda p: p)
    with pytest.raises(ValueError, match="unsafe for cmd"):
        discover.hook_command(f, f)
    monkeypatch.setattr(discover, "_get_short_path_name", lambda p: str(tmp_path / "SHORT~1" / "x.py"))
    out = discover._hook_path(f)
    assert ch not in out and "SHORT~1" in out
