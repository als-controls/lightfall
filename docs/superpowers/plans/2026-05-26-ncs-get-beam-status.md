# `ncs_get_beam_status` MCP Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing `ALSBeamStatusService` to the embedded agent as an MCP tool `ncs_get_beam_status`, so the agent can report ALS ring current, shutter/beam availability, energy, lifetime, and ops comment.

**Architecture:** A thin module-level helper (`_beam_status_payload`) wraps `ALSBeamStatusService.get_instance().get_introspection_data()`, and a new `@tool` in the existing `EngineToolsAgent` returns it. No new plugin, no new service, no new PV plumbing — the service already polls `https://controls.als.lbl.gov/als-beamstatus/curvals` and exposes `get_introspection_data()`.

**Tech Stack:** Python, claude_agent_sdk `@tool`, existing `lightfall.services.als_beam_status.ALSBeamStatusService`, pytest.

**Repo / branch:** `ncs/ncs`. Create a branch `feature/beam-status-tool` from `master` before starting.

**Test interpreter:** `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest <args>`

**Related spec:** `lightfall-endstation-7011/docs/superpowers/specs/2026-05-26-reflection-alignment-design.md` (§D1). This tool is independently useful; the reflection-alignment skill is its first consumer.

---

## File Structure

- Modify: `src/lightfall/plugins/agents/engine_tools.py` — add `_beam_status_payload` helper (module level) and the `ncs_get_beam_status` tool inside `EngineToolsAgent.create_tools`.
- Create: `tests/plugins/agents/test_beam_status.py`.

---

### Task 1: Add the `ncs_get_beam_status` tool

**Files:**
- Modify: `src/lightfall/plugins/agents/engine_tools.py`
- Test: `tests/plugins/agents/test_beam_status.py`

- [ ] **Step 0: Create the working branch**

```bash
git checkout master && git checkout -b feature/beam-status-tool
```

- [ ] **Step 1: Write the failing tests**

`tests/plugins/agents/test_beam_status.py`:
```python
"""Tests for the ncs_get_beam_status MCP tool helper."""
from __future__ import annotations

import lightfall.plugins.agents.engine_tools as et
from lightfall.services import als_beam_status


class _FakeService:
    def __init__(self):
        self.refreshed = False

    def force_refresh(self):
        self.refreshed = True

    def get_introspection_data(self):
        return {
            "is_connected": True,
            "beam_current_mA": 500.2,
            "beam_available": True,
            "beam_energy_GeV": 1.9,
        }


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(
        als_beam_status.ALSBeamStatusService,
        "get_instance",
        classmethod(lambda cls: fake),
    )


def test_beam_status_payload(monkeypatch):
    fake = _FakeService()
    _patch_service(monkeypatch, fake)
    out = et._beam_status_payload()
    assert out["success"] is True
    assert out["beam_current_mA"] == 500.2
    assert out["beam_available"] is True
    assert fake.refreshed is False


def test_beam_status_payload_force_refresh(monkeypatch):
    fake = _FakeService()
    _patch_service(monkeypatch, fake)
    out = et._beam_status_payload(force_refresh=True)
    assert fake.refreshed is True


def test_engine_tools_registers_beam_status():
    tools = et.EngineToolsAgent().create_tools()
    if not tools:
        import pytest

        pytest.skip("claude_agent_sdk not available")
    names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools}
    assert "ncs_get_beam_status" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/plugins/agents/test_beam_status.py -v`
Expected: FAIL with `module 'lightfall.plugins.agents.engine_tools' has no attribute '_beam_status_payload'`.

- [ ] **Step 3: Add the module-level helper**

In `src/lightfall/plugins/agents/engine_tools.py`, after the imports and before the `EngineToolsAgent` class definition, add:
```python
def _beam_status_payload(force_refresh: bool = False) -> dict[str, Any]:
    """Read the ALS beam status via the polling service.

    Values reflect the service's most recent successful poll. ``force_refresh``
    kicks an immediate background poll; freshly-polled values may not be
    reflected until that poll completes, so the returned snapshot can lag by
    one cycle.
    """
    from lightfall.services.als_beam_status import ALSBeamStatusService

    service = ALSBeamStatusService.get_instance()
    if force_refresh:
        service.force_refresh()
    return {"success": True, **service.get_introspection_data()}
```

- [ ] **Step 4: Add the tool inside `create_tools`**

In `EngineToolsAgent.create_tools`, add this tool definition just before the `return [...]` statement:
```python
        @tool(
            name="ncs_get_beam_status",
            description=(
                "Get ALS storage-ring beam status: ring current (mA), beam/shutter "
                "availability, energy (GeV), lifetime (hours), beam-position stability, "
                "and the operations comment. Use this to explain why a beamline diode "
                "reads no beam (ring dump vs shutter closed vs mis-steering). Set "
                "force_refresh=true to trigger an immediate poll (values may lag one cycle)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Trigger an immediate background poll before reading.",
                        "default": False,
                    },
                },
            },
        )
        async def get_beam_status(args: dict) -> dict[str, Any]:
            from lightfall.claude._internal.threading import run_on_main_thread

            force = args.get("force_refresh", False)

            def _get():
                try:
                    return mcp_result(_beam_status_payload(force_refresh=force))
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_get)
```
Then add `get_beam_status` to the returned list:
```python
        return [
            get_run_status,
            pause_plan,
            resume_plan,
            abort_plan,
            get_run_history,
            get_scan_data,
            get_last_run,
            show_run,
            get_beam_status,
        ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/plugins/agents/test_beam_status.py -v`
Expected: `test_beam_status_payload` and `test_beam_status_payload_force_refresh` PASS; `test_engine_tools_registers_beam_status` PASSES if `claude_agent_sdk` is installed, otherwise SKIPS.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/plugins/agents/engine_tools.py tests/plugins/agents/test_beam_status.py
git commit -m "feat(agents): add ncs_get_beam_status MCP tool"
```

---

## Self-Review

- **Spec coverage (§D1):** wraps `ALSBeamStatusService.get_introspection_data()` ✓; optional `force_refresh` ✓; added to `EngineToolsAgent` ✓; no new plugin registration ✓.
- **Placeholder scan:** none — concrete code and commands throughout.
- **Type consistency:** `mcp_result` and `Any` are already imported at the top of `engine_tools.py`; the helper returns a `dict[str, Any]`; tool name `ncs_get_beam_status` matches between implementation and the registration test.
