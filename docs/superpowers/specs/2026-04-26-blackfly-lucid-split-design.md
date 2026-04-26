# Blackfly observer → lucid + endstation split (Spec B)

**Status:** approved (brainstorm 2026-04-25; refined 2026-04-26 after Spec A merged at `5877a63`).

**Companion docs:**
- `2026-04-25-lucid-sdk-native-plugins-design.md` (Spec A — the `AgentPlugin` rails this skill rides on; merged).
- `~/PycharmProjects/blackfly_observer/` (current home of the code being split — local-only repo, no remote).

## 1. Goal

Refactor the standalone `blackfly_observer` package into two existing repos plus one new agent plugin:

1. The generic observer-camera abstraction (`CameraBase` ABC + pyqtgraph `CameraImageView`) moves into `lucid` core under `lucid.ui.widgets.observers`. This becomes the reusable surface for any future non-ophyd hardware-camera transport.
2. The Blackfly-specific transport stack (GVCP/GVSP/discovery/registers/pixel formats + `BlackflyCamera`) moves into `lucid-endstation-7011` under `lucid_endstation_7011.observers.blackfly`. It's beamline-specific FLIR support and does not belong in lucid core.
3. A new **Blackfly skill** ships from `lucid-endstation-7011` as a `type_name="agent"` plugin (`BlackflyAgent`). It teaches the embedded Claude agent how to discover Blackfly cameras and wire them into a user `PanelPlugin`, exposing one MCP tool (`discover_blackfly_cameras`) and reusing the existing `panel_builder` agent for file-writing.

After the migration the `blackfly_observer` repo is archived (tarball into `~/Downloads/`, working tree deleted). It has no git remote, so nothing else needs deletion elsewhere.

## 2. Vocabulary

The term **Device** in lucid is reserved for **ophyd Devices**. The Blackfly is not ophyd-backed, so its abstraction lives under `observers/`, not `devices/`. The `lucid.ui.widgets.camera` package (which already exists, holding `OphydImageView` and friends) is the ophyd-flavored peer; `lucid.ui.widgets.observers` is the new non-ophyd peer.

## 3. Final layout

### 3.1 lucid (`~/PycharmProjects/ncs/ncs/`)

```
src/lucid/ui/widgets/observers/
  __init__.py           # exports CameraBase, CameraImageView
  camera.py             # CameraBase ABC (lifted from blackfly_observer/camera.py;
                        # BlackflyCamera and Geometry are NOT included here — they
                        # move with the transport in step 2)
  image_view.py         # CameraImageView (lifted from blackfly_observer/widgets.py;
                        # qtpy imports replaced with PySide6)

tests/ui/widgets/observers/
  __init__.py
  test_camera_base.py   # lifted from blackfly_observer/tests/test_camera_base.py
  test_image_view.py    # renamed from test_widgets.py
```

### 3.2 lucid-endstation-7011 (`~/PycharmProjects/ncs/lucid-endstation-7011/`)

```
src/lucid_endstation_7011/observers/
  __init__.py
  blackfly/
    __init__.py         # exports BlackflyCamera, discover, DeviceInfo
    camera.py           # BlackflyCamera (subclasses lucid.ui.widgets.observers.CameraBase),
                        # plus the Geometry dataclass
    discovery.py
    gvcp.py
    gvcp_transport.py
    gvsp.py
    pixel_formats.py
    registers.py
    skill.py            # BlackflyAgent(AgentPlugin)
    references/
      panel_template.py # canonical PanelPlugin template; the skill points the agent here
    scripts/
      __init__.py
      discover.py       # bfly-discover entry point

tests/observers/blackfly/
  __init__.py
  test_camera_live.py   # 4 hw tests + transport-level integration (gated by BLACKFLY_TEST_IP)
  test_discovery.py
  test_gvcp.py
  test_gvcp_transport.py
  test_gvsp.py
  test_pixel_formats.py
  test_registers.py
  test_skill.py         # NEW — instantiates BlackflyAgent, asserts non-empty prompt
                        #       and exactly one MCP tool named discover_blackfly_cameras
```

### 3.3 Manifest and console-script wiring

`src/lucid_endstation_7011/manifest.py` gains one entry:

```python
PluginEntry(
    type_name="agent",
    name="blackfly",
    import_path="lucid_endstation_7011.observers.blackfly.skill:BlackflyAgent",
    metadata={"priority": 30},
),
```

`pyproject.toml` (endstation) gains one console script:

```toml
[project.scripts]
bfly-discover = "lucid_endstation_7011.observers.blackfly.scripts.discover:main"
```

## 4. Blackfly skill shape

### 4.1 `BlackflyAgent` class

```python
# src/lucid_endstation_7011/observers/blackfly/skill.py
from pathlib import Path
from lucid.plugins.agent_plugin import AgentPlugin

class BlackflyAgent(AgentPlugin):
    @property
    def name(self) -> str: return "blackfly"
    @property
    def display_name(self) -> str: return "Blackfly Camera"
    @property
    def description(self) -> str:
        return "Discover and wire FLIR Blackfly S cameras into user PanelPlugins"
    @property
    def category(self) -> str: return "devices"
    @property
    def priority(self) -> int: return 30

    def get_system_prompt(self) -> str: ...
    def create_tools(self) -> list: ...
    def get_references_dir(self) -> Path | None:
        return Path(__file__).parent / "references"
```

### 4.2 System prompt (SKILL.md body)

≤30 lines. Three sections:

1. **When to use:** the user asks for a panel showing a Blackfly / FLIR / GigE Vision camera, or mentions a Blackfly S model.
2. **API recap:** the public import lines —
   ```python
   from lucid.ui.widgets.observers import CameraImageView
   from lucid_endstation_7011.observers.blackfly import BlackflyCamera
   ```
   plus a one-paragraph note that `BlackflyCamera` takes `device_ip` (the camera) and `bind_ip` (the host NIC the camera should send packets to).
3. **Workflow:**
   1. If the user did not supply an IP, call `discover_blackfly_cameras()`.
   2. If multiple cameras are returned, ask the user to pick one.
   3. If the user did not supply a `bind_ip`, ask for it.
   4. Read `references/panel_template.py` for the canonical `PanelPlugin` body.
   5. Substitute the chosen `device_ip` and `bind_ip` into the template, then call `mcp__panel_builder__ncs_create_user_plugin` with the substituted text.

### 4.3 MCP tool — `discover_blackfly_cameras`

- Wraps `lucid_endstation_7011.observers.blackfly.discover()`.
- Signature: `discover_blackfly_cameras(bind_ip: str | None = None, timeout_s: float = 1.0) -> list[dict]`.
- Each dict has keys `ip`, `manufacturer`, `model`, `serial`, `user_name` — all JSON-serializable. (Earlier draft of this spec listed `mac`; GVCP `DeviceInfo` does not carry MAC, so `manufacturer` and `user_name` were substituted; `user_name` is the operator-set camera label.)
- `bind_ip` defaults to the auto-detected default-route NIC (same UDP-connect idiom as the `bfly-discover` console script).
- That is the entire MCP surface of this skill. No `list_*`, `reload_*`, `unload_*` helpers — those are `panel_builder`'s job.

### 4.4 `references/panel_template.py`

A complete, syntactically valid `PanelPlugin` source file the agent reads, substitutes IPs into, and hands to `panel_builder`. Imports from the public lucid + endstation paths and constructs `CameraImageView(BlackflyCamera(device_ip="<IP>", bind_ip="<HOST>"))` inside the panel's `make_panel()`. The skill teaches the agent that `<IP>` and `<HOST>` are the only placeholders to substitute.

## 5. Camera binding pattern

`BlackflyCamera` keeps its current API: `BlackflyCamera(device_ip: str, bind_ip: str)`. No `from_device_info` classmethod. Discovery returns dicts; the agent picks the IP out of the dict and substitutes the string into the template. The `bind_ip` (host NIC) is unavoidable user input; the skill prompts for it explicitly.

## 6. Test split

Code follows code:

| Test file | Destination | Reason |
|---|---|---|
| `test_camera_base.py` | lucid, `tests/ui/widgets/observers/` | tests the `CameraBase` ABC |
| `test_widgets.py` (renamed `test_image_view.py`) | lucid, `tests/ui/widgets/observers/` | tests `CameraImageView` |
| `test_camera_live.py` | endstation, `tests/observers/blackfly/` | the 4 hw tests + transport integration |
| `test_discovery.py` | endstation | discovery is Blackfly-specific |
| `test_gvcp.py`, `test_gvcp_transport.py` | endstation | GVCP transport |
| `test_gvsp.py` | endstation | GVSP packet parsing |
| `test_pixel_formats.py` | endstation | Bayer/mono decode used only by `BlackflyCamera` |
| `test_registers.py` | endstation | FLIR register addresses |
| `test_skill.py` (NEW) | endstation | smoke-tests `BlackflyAgent` instantiation |

The 4 hw tests in `test_camera_live.py` stay tied to the BFS-PGE-122S6C at `192.168.10.81` (tsuru). They are gated by the existing `hw` pytest marker and the `BLACKFLY_TEST_IP` environment variable.

## 7. Migration order

The dependency direction is one-way (endstation imports lucid; lucid never imports endstation), so the migration sequences cleanly:

### Step 1 — lucid first (own branch, own merge)

- Create `src/lucid/ui/widgets/observers/{__init__.py,camera.py,image_view.py}`.
- Lift `CameraBase` from `blackfly_observer/camera.py` (drop the `BlackflyCamera` class, its `Geometry` dataclass, and the `from . import gvcp, gvsp, pixel_formats, registers` import — those move in step 2).
- Lift `CameraImageView` from `blackfly_observer/widgets.py`. Replace `from qtpy import QtCore, QtWidgets` with explicit `from PySide6.QtCore import …` / `from PySide6.QtWidgets import …` (match the form used in `lucid/ui/widgets/camera/image_view.py`).
- Create `tests/ui/widgets/observers/`, lift `test_camera_base.py` and `test_widgets.py` (rename to `test_image_view.py`).
- Verify under lucid's venv: `.venv/Scripts/python -m pytest tests/ui/widgets/observers/`.
- Merge.

### Step 2 — endstation second (own branch, own merge)

- Confirm the endstation's workspace install of lucid picks up step 1's commit.
- Create `src/lucid_endstation_7011/observers/blackfly/`. Lift `camera.py`, `discovery.py`, `gvcp.py`, `gvcp_transport.py`, `gvsp.py`, `pixel_formats.py`, `registers.py` from `blackfly_observer/src/blackfly_observer/`. In the new `camera.py`, change `from .camera import CameraBase` (which no longer exists) to `from lucid.ui.widgets.observers import CameraBase`.
- Add `skill.py`, `references/panel_template.py`, `scripts/__init__.py`, `scripts/discover.py`.
- Update `manifest.py` (one new `PluginEntry`) and `pyproject.toml` (one new `[project.scripts]` entry).
- Lift transport tests into `tests/observers/blackfly/`. Add `test_skill.py`.
- Verify offline: `.venv/Scripts/python -m pytest tests/observers/blackfly/` (skips the `hw`-marked tests by default).
- Verify on tsuru: with `BLACKFLY_TEST_IP=192.168.10.81` set, the 4 hw tests pass.
- Boot lucid against this endstation, watch the Blackfly skill load in the embedded agent's settings, ask the agent to make a Blackfly panel, watch a live frame stream.
- Merge.

### Step 3 — archive `blackfly_observer`

- Verify both step 1 and step 2 are merged and the integrated lucid app has been exercised end-to-end (skill loads, `discover_blackfly_cameras` returns the camera, embedded agent creates a panel that streams).
- Tar the working tree:

  ```bash
  tar czf ~/Downloads/blackfly_observer-archive-2026-04-26.tar.gz \
      -C ~/PycharmProjects blackfly_observer
  rm -rf ~/PycharmProjects/blackfly_observer
  ```

## 8. Risks and rollback

- **Hardware regression in `BlackflyCamera` after the move.** Risk is low (mechanical import surgery), but every transport module gets touched. Mitigation: full offline + 4 hw tests on tsuru before merging step 2.
- **`qtpy` → `PySide6` substitution in `CameraImageView`.** The Signal/slot pattern is identical between the two; only import lines change. Mitigation: the existing widget tests (renamed `test_image_view.py`) cover the threaded frame-callback flow.
- **Skill auto-load.** A typo in the manifest's `import_path` produces a plugin-load error in the lucid log but does not crash the app. Mitigation: `test_skill.py` instantiates `BlackflyAgent` and asserts the tool list and prompt are non-empty.

**Rollback:** Each step is a separate merge.
- If step 2 misbehaves, revert it; lucid step 1 is harmless on its own (a couple of unused new modules).
- If step 1 misbehaves, revert it before touching the endstation.
- The tarball from step 3 is the last-resort restore for `blackfly_observer`.

## 9. Out of scope

- Multi-vendor / GenApi-XML-at-runtime support. The transport stays register-table driven against FLIR; an Allied-Vision/Basler future is a separate spec on top of `CameraBase`.
- Trigger-mode / Gain / ExposureTime control from the panel. Observation-only — the camera is configured externally (SpinView).
- Replacing `panel_builder`'s file-writing with skill-local logic. The Blackfly skill ends after it calls `mcp__panel_builder__ncs_create_user_plugin`.
- Publishing `lucid-endstation-7011` to PyPI — dev workflow remains workspace install.

## 10. Open questions

None. All open items from the standing memory (`project_blackfly_lucid_split_plan.md`) were resolved during the 2026-04-26 brainstorm:

| Item | Resolution |
|---|---|
| Fate of standalone `blackfly_observer` repo | Archive: tarball + delete local tree (no remote exists). |
| Test split mechanics | Code follows code (§6). |
| Camera binding pattern | Explicit `device_ip` / `bind_ip` only (§5). |
| Qt binding | `qtpy` → `PySide6` on the way into lucid. |
| `bfly-discover` console script | Re-home in `lucid-endstation-7011` pyproject. |
