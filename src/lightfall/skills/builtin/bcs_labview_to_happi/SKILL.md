---
name: bcs-labview-to-happi
description: "Onboard a beamline's BCS LabVIEW motors and AIs into Lightfall as a happi JSON device database plus a happi row in device settings. Use when a beamline runs BCS (LabVIEW) and you need Lightfall devices built from its 'Motor Setup.json' / 'AI Setup.json' config files, e.g. ahead of the BCS EPICS PVs going live."
---
# BCS LabVIEW configs → happi device database

**Status: DRAFT, partly verified.** Written 2026-10-29 from the 12.0.2.1 CHiPPS onboarding. PV naming and record types were verified against the live BCS IOC on 2026-11-04 (see Evidence). The `Motor Used?` / `Channel Used?` flag meanings are still **unverified**. Update this skill as testing goes on. It may later become a happi-row "Import from BCS" action or a `bcs-config` backend type.

## When this applies
- The beamline runs BCS (LabVIEW), and its config directory is at
  `C:\Beamline Controls\<BL> <NAME>\<BL> <NAME> Specific\Support Files\`
  (for example `C:\Beamline Controls\12.0.2.1 CHiPPS\12.0.2.1 CHiPPS Specific\Support Files`).
- The user wants Lightfall devices for the BCS motors and AIs. BCS publishes these over EPICS with a prefix like `BCS<BL without dots>:` (e.g. `BCS12021:`).
- This is not the live `bcs` backend type, which talks to a running BCS server over TCP. This skill builds a static happi file instead.

## Source files (in Support Files)
| File | What to use |
|---|---|
| `Motor Setup.json` | `Motors[]`: `Name`, `General Parameters` (Type, Driver Name, Units, SW limits, Precision, Motor Used?), `Parameters` (controller IP, Axis) |
| `AI Setup.json` | `AIs[]`: `Name`, `General Settings` (AI Type Name, Units, Channel Used?), plus one `*Parameters` block that depends on the driver (CALab `PV Name`, DAQmx `DAQmx Physical Channel`, UDP `IP Address`/`AI Name`) |
| `*.txt` twins | LabVIEW XML versions of the same data. Use the `.json` files. In the CHiPPS case the `.txt` lists the same 4 AIs. |

Gotchas in the source files:
- `Motor Setup.json` contains bare `Infinity` / `-Infinity` for unset soft limits. Python's `json` module accepts these. Map them to `None` (no limit) in the happi output. Strict parsers will reject the file.
- **`Motor Used?` appears to be stored inverted: `false` means the motor is in use.** This comes from the beamline scientist (R. Pandolfi, 2026-10-29) and is not yet verified. Every CHiPPS motor, including the `Old Sample *` stages, had `false`. Don't filter motors on this flag. Record the raw value and the inverted reading (`bcs_motor_used_raw`, `bcs_in_use`). `Channel Used?` on AIs probably works the same way. Record it raw.
- `Type: "Derived Element"` (driver `Slit`) marks motors that BCS calculates from other motors, e.g. the JJ slit Pos/Size motors. Keep them in a separate group.
- A UDP AI's `AIs` array lists every AI on the **remote** BCS server it reads from. At CHiPPS that server is a 4.0.2 server. These are not this beamline's AIs, so don't import them unless the user asks.
- Names can contain non-ASCII characters (`Energy Å`).

## Steps
1. **Inspect** the two JSON files. Count motors and AIs, and list Name / Type / Driver / Units. Show the user anything that looks surprising, such as legacy `Old *` stages or remote AI lists.
2. **Confirm the PV naming** with the user. Rule (verified live at 12.0.2.1, 2026-11-04): strip the BCS name of everything except `[A-Za-z0-9_]` (case kept, `Å`→`A`), then
   - **Motors:** `BCS<BL>:<Name>`. This is an EPICS `motor` record (`.RTYP` = `motor`, `.RBV` connects). Example: `Sample X` → `BCS12021:SampleX`.
   - **AIs:** `BCS<BL>:<Name>:ai-AI`. This is an EPICS `ai` record. Example: `Sample TEY` → `BCS12021:SampleTEY:ai-AI`, `M4 current` → `BCS12021:M4current:ai-AI`. The bare `BCS<BL>:<Name>` does **not** exist for AIs.
   If the IOC is reachable, spot-check a motor and an AI with `caget` (or `EpicsSignalRO(...).get()` in the console) before you generate.
3. **Generate** the happi JSON with the script below. Save it to `~/lightfall/happi/bcs_<bl>_<name>.json` and keep the script in `~/lightfall/scripts/`. Don't put scripts in the Support Files directory.
   - Item names must match happi's `^[a-z][a-z_0-9]{2,78}$`, so use snake_case. Put the original name in `display_name` (Lightfall reads it) and in `bcs_name`.
   - Motors use `ophyd.EpicsMotor`, AIs use `ophyd.EpicsSignalRO`, with `args: ["{{prefix}}"]` and `kwargs: {"name": "{{name}}"}`. Both were verified against live records.
   - Groups: `BCS Motors`, `BCS Derived Motors`, `BCS AIs`. Copy the BCS settings in as extra fields: units, high/low limit, precision, driver, controller IP/axis, AI source channel. Lightfall lifts these into device metadata.
4. **Validate** in the Lightfall IPython console:
   `happi.Client(path=...)`, then check that the item count matches.
5. **Add the settings row.** Use the Settings → Devices UI, or run this in the console:
   ```python
   from lightfall.ui.preferences.manager import PreferencesManager
   from lightfall.devices.backend_types.rows import load_rows, save_rows, BackendRow
   p = PreferencesManager.get_instance(); rows = load_rows(p.get)
   rows.append(BackendRow(type="happi", name="bcs-<bl>-<name>", enabled=False,
                          config={"path": r"<json path>", "beamline": ""}))
   save_rows(p, rows)
   ```
   Leave it **disabled** while the PVs aren't live, so Lightfall doesn't keep searching for dozens of dead PVs. Rows written this way take effect on the next Settings save or restart. Enabling the row in the UI applies it immediately.
6. **When the PVs go live** (integration testing):
   - Enable the row. Use `lightfall_get_device_state` to check connection on a few motors and AIs.
   - Confirm the PV names, the record type (motor record or not), and the limits, units and precision compared with BCS. Read before you move. The first test move should be small and on a safe axis, with the user's OK.
   - Edit the generator rather than the JSON by hand, and re-run it. Then record the findings in this skill.

## Generator script
Usage: `python -I bcs_to_happi.py "<Support Files dir>" <out.json> --prefix BCS12021: --beamline 12.0.2.1`
```python
"""Generate a happi JSON db from BCS LabVIEW 'Motor Setup.json' / 'AI Setup.json'."""
import argparse, json, math, re
from datetime import datetime
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("out")
ap.add_argument("--prefix", required=True)    # e.g. BCS12021:
ap.add_argument("--beamline", required=True)  # e.g. 12.0.2.1
a_ = ap.parse_args()
src, out = Path(a_.src), Path(a_.out)
now = datetime.now().strftime("%a %b %d %H:%M:%S %Y")

def pv_suffix(name):  # "Sample X" -> "SampleX", "Energy Å" -> "EnergyA" (verified 2026-11-04)
    return re.sub(r"[^A-Za-z0-9_]", "", name.replace("Å", "A"))

AI_SUFFIX = ":ai-AI"  # BCS AI PVs are <prefix><Name>:ai-AI (ai record); motors have no suffix

def ident(name):  # happi requires ^[a-z][a-z_0-9]{2,78}$
    s = re.sub(r"[^a-z0-9]+", "_", name.replace("Å", "A").lower()).strip("_")
    return s if s[:1].isalpha() else "m_" + s

def lim(v):
    return None if (isinstance(v, float) and math.isinf(v)) else v

db = {}
def add(key, display, cls, prefix, group, extra):
    assert key not in db, f"name collision: {key}"
    db[key] = {
        "_id": key, "name": key, "type": "OphydItem", "active": True,
        "device_class": cls, "args": ["{{prefix}}"], "kwargs": {"name": "{{name}}"},
        "prefix": prefix, "beamline": a_.beamline, "display_name": display, "group": group,
        "documentation": f"Generated from BCS LabVIEW setup ({extra['bcs_source']}).",
        "creation": now, "last_edit": now, **extra,
    }

for m in json.loads((src / "Motor Setup.json").read_text(encoding="utf-8"))["Motors"]:
    g, p = m["General Parameters"], m.get("Parameters", {})
    add(ident(m["Name"]), m["Name"], "ophyd.EpicsMotor", a_.prefix + pv_suffix(m["Name"]),
        "BCS Derived Motors" if g["Type"] != "Controller" else "BCS Motors",
        {"bcs_source": "Motor Setup.json", "bcs_name": m["Name"], "bcs_type": g["Type"],
         "bcs_driver": g["Driver Name"], "units": g["Units"],
         "high_limit": lim(g["Forward SW Limit (Units)"]), "low_limit": lim(g["Reverse SW Limit (Units)"]),
         "precision": g["Precision"],
         # "Motor Used?" believed inverted (false = in use) - unverified
         "bcs_motor_used_raw": g.get("Motor Used?"), "bcs_in_use": not g.get("Motor Used?", False),
         "bcs_controller": {k: p[k] for k in ("Controller IP Address", "Axis") if k in p} or None})

for a in json.loads((src / "AI Setup.json").read_text(encoding="utf-8"))["AIs"]:
    gs = a["General Settings"]
    spec = next((v for k, v in a.items() if k.endswith("Parameters")), {})
    add(ident(a["Name"]), a["Name"], "ophyd.EpicsSignalRO", a_.prefix + pv_suffix(a["Name"]) + AI_SUFFIX, "BCS AIs",
        {"bcs_source": "AI Setup.json", "bcs_name": a["Name"], "bcs_driver": gs["AI Type Name"],
         "units": gs["Units"], "bcs_channel_used_raw": gs.get("Channel Used?"),
         "bcs_channel": {k: spec[k] for k in ("PV Name", "DAQmx Physical Channel", "IP Address", "AI Name") if k in spec}})

out.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")
print(len(db), "items ->", out)
```

## Evidence / history
- 2026-10-29, 12.0.2.1 CHiPPS: 40 motors (31 controller-driven across Galil 41x3, MCS2, Micos MMC-100 and UDP; 8 derived JJ slit motors) and 4 AIs (CALab `cmm:beam_current`, DAQmx `Dev1/ai0` and `Dev1/ai1`, UDP M4 current). This gave 44 happi items, which loaded cleanly with `happi.Client`. Output: `~/lightfall/happi/bcs_12021_chipps.json`. Row: `bcs-12021-chipps`, disabled. PVs not yet live.
- 2026-10-30: the four AI prefixes in that file were hand-corrected to `BCS12021:<Name>:ai-AI` once the IOC came up.
- 2026-11-04, live check from Lightfall: `BCS12021:{BeamCurrent,M4current,Photodiode,SampleTEY}:ai-AI` all connected (`.RTYP` = `ai`), and `BCS12021:BeamCurrent` did not connect. `BCS12021:SampleX` and `BCS12021:CCDTheta` have `.RTYP` = `motor`, and `.RBV` connects. Note: `SampleTEY:ai-AI.EGU` read `mm`, so BCS units on AIs may not be meaningful. Prefer the `units` field from `AI Setup.json`.
- 2026-11-04: the generator was updated to add the `:ai-AI` suffix, so the hand edit is no longer needed.

## Open questions (resolve during integration testing)
- ~~PV naming~~ and ~~motor record vs plain signal~~ are resolved (see Evidence). `CAEN3` (from the user's early example) is still unexplained. It may be an AI on a different beamline's config.
- Is the `Motor Used?` / `Channel Used?` inversion real?
- Should derived motors and UDP pass-through items (energy, undulator) come from BCS, or from the facility's own PVs?
