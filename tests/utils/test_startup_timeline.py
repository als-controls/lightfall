"""Tests for startup timeline profiling (lightfall.utils.startup_timeline)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from lightfall.utils import startup_timeline


@pytest.fixture(autouse=True)
def _reset_timeline(monkeypatch: pytest.MonkeyPatch):
    """Ensure each test starts and ends with a clean startup_timeline state."""
    monkeypatch.delenv("LIGHTFALL_STARTUP_PROFILE", raising=False)
    startup_timeline.mark_origin()
    yield
    monkeypatch.delenv("LIGHTFALL_STARTUP_PROFILE", raising=False)
    startup_timeline.mark_origin()


def test_disabled_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When LIGHTFALL_STARTUP_PROFILE is unset, 0, or empty, profiling is a no-op."""
    assert startup_timeline.enabled() is False

    for val in ("0", ""):
        monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", val)
        assert startup_timeline.enabled() is False

    startup_timeline.mark_origin()
    with startup_timeline.phase("alpha"):
        with startup_timeline.phase("beta"):
            startup_timeline.mark("checkpoint")

    assert startup_timeline.get_records() == []
    result = startup_timeline.finish(tmp_path)
    assert result is None
    assert list(tmp_path.iterdir()) == []


def test_enabled_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any non-empty value other than '0' enables startup profiling."""
    for val in ("1", "true", "cprofile", "yes"):
        monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", val)
        assert startup_timeline.enabled() is True


def test_nesting_depth_and_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested phases increment depth per thread and restore depth on exit."""
    monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", "1")
    startup_timeline.mark_origin()

    with startup_timeline.phase("outer"):
        with startup_timeline.phase("middle"):
            with startup_timeline.phase("inner"):
                time.sleep(0.002)

            def _worker() -> None:
                with startup_timeline.phase("worker_root"):
                    with startup_timeline.phase("worker_child"):
                        pass

            t = threading.Thread(target=_worker, name="test-worker")
            t.start()
            t.join()

    with startup_timeline.phase("after_outer"):
        pass

    records = startup_timeline.get_records()
    by_name = {r["name"]: r for r in records}

    assert by_name["outer"]["depth"] == 0
    assert by_name["middle"]["depth"] == 1
    assert by_name["inner"]["depth"] == 2
    assert by_name["after_outer"]["depth"] == 0

    assert by_name["worker_root"]["depth"] == 0
    assert by_name["worker_root"]["thread"] == "test-worker"
    assert by_name["worker_child"]["depth"] == 1
    assert by_name["worker_child"]["thread"] == "test-worker"

    for rec in records:
        assert rec["start_s"] >= 0.0
        assert rec["end_s"] >= rec["start_s"]


def test_exception_closes_phase_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception inside a phase records end_s and error=True, restores depth, and re-raises."""
    monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", "1")
    startup_timeline.mark_origin()

    class CustomBoomError(RuntimeError):
        pass

    with pytest.raises(CustomBoomError, match="kaboom"):
        with startup_timeline.phase("outer_ok"):
            with startup_timeline.phase("failing_inner"):
                raise CustomBoomError("kaboom")

    with startup_timeline.phase("subsequent_root"):
        pass

    records = startup_timeline.get_records()
    by_name = {r["name"]: r for r in records}

    assert by_name["failing_inner"]["error"] is True
    assert isinstance(by_name["failing_inner"]["end_s"], float)
    assert by_name["failing_inner"]["end_s"] >= by_name["failing_inner"]["start_s"]
    assert by_name["failing_inner"]["depth"] == 1

    assert by_name["outer_ok"]["error"] is True
    assert isinstance(by_name["outer_ok"]["end_s"], float)
    assert by_name["outer_ok"]["depth"] == 0

    assert by_name["subsequent_root"]["depth"] == 0
    assert "error" not in by_name["subsequent_root"]


def test_mark_records_instant_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """mark() records an instant event with start_s == end_s and instant=True."""
    monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", "1")
    startup_timeline.mark_origin()

    with startup_timeline.phase("setup"):
        startup_timeline.mark("inside_setup")
    startup_timeline.mark("event_loop_enter")

    records = startup_timeline.get_records()
    by_name = {r["name"]: r for r in records}

    inside = by_name["inside_setup"]
    assert inside["instant"] is True
    assert inside["start_s"] == inside["end_s"]
    assert inside["depth"] == 1

    loop_mark = by_name["event_loop_enter"]
    assert loop_mark["instant"] is True
    assert loop_mark["start_s"] == loop_mark["end_s"]
    assert loop_mark["depth"] == 0


def test_finish_writes_json_and_escaped_html(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """finish() writes valid JSON and self-contained HTML with escaped phase names."""
    monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", "1")
    startup_timeline.mark_origin()

    tricky_phase = 'phase <script>&"quote"</script>'
    tricky_mark = "mark <instant & 'tick'>"

    with startup_timeline.phase("phase_one"):
        time.sleep(0.005)
        with startup_timeline.phase(tricky_phase):
            time.sleep(0.002)
    startup_timeline.mark(tricky_mark)

    out_dir = tmp_path / "diagnostics"
    html_path = startup_timeline.finish(out_dir)

    assert html_path is not None
    assert html_path.exists()
    assert html_path.suffix == ".html"
    assert html_path.name.startswith("startup-")

    json_path = html_path.with_suffix(".json")
    assert json_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "total_s" in payload
    assert payload["total_s"] > 0
    names = [r["name"] for r in payload["records"]]
    assert names == ["phase_one", tricky_phase, tricky_mark]

    html_text = html_path.read_text(encoding="utf-8")
    assert "<svg" in html_text
    assert "<style>" in html_text
    assert "phase_one" in html_text
    # Raw <script> must be escaped, never injected literally
    assert "<script>&" not in html_text
    assert "phase &lt;script&gt;&amp;&quot;quote&quot;&lt;/script&gt;" in html_text
    assert "mark &lt;instant &amp; &#x27;tick&#x27;&gt;" in html_text
    assert " ms" in html_text


def test_finish_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Second finish() call returns the same Path and writes no additional files."""
    monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", "1")
    startup_timeline.mark_origin()

    with startup_timeline.phase("initial_phase"):
        pass

    first_path = startup_timeline.finish(tmp_path)
    assert first_path is not None
    files_after_first = sorted(tmp_path.iterdir())
    first_mtime = first_path.stat().st_mtime_ns

    with startup_timeline.phase("late_phase"):
        pass

    second_path = startup_timeline.finish(tmp_path)
    assert second_path == first_path
    assert sorted(tmp_path.iterdir()) == files_after_first
    assert first_path.stat().st_mtime_ns == first_mtime


def test_cprofile_mode_writes_prof_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LIGHTFALL_STARTUP_PROFILE=cprofile also writes startup-<ts>.prof."""
    monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", "cprofile")
    startup_timeline.mark_origin()

    with startup_timeline.phase("compute"):
        total = sum(i * i for i in range(1000))
        assert total > 0

    html_path = startup_timeline.finish(tmp_path)
    assert html_path is not None
    prof_path = html_path.with_suffix(".prof")
    json_path = html_path.with_suffix(".json")

    assert html_path.exists()
    assert json_path.exists()
    assert prof_path.exists()
    assert prof_path.stat().st_size > 0


def test_main_helpers_record_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify main.py helper functions record expected startup_timeline phases."""
    from unittest.mock import MagicMock

    import lightfall.main as main_mod

    monkeypatch.setenv("LIGHTFALL_STARTUP_PROFILE", "1")
    startup_timeline.mark_origin()

    loader = MagicMock()
    session_manager = MagicMock()
    session_manager.is_authenticated = False

    fire = main_mod._arm_post_login_plugin_load(loader, session_manager)
    fire()
    # Second call is idempotent and must not record a second phase
    fire()

    records = startup_timeline.get_records()
    assert [r["name"] for r in records] == ["post_login_plugin_wave"]

