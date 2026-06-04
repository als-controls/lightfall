#!/usr/bin/env python3
"""Fix happi database entries that reference xicam modules.

This script scans a happi JSON database and either:
1. Removes devices with xicam imports (default)
2. Attempts to map them to ophyd equivalents (--map)

Creates a timestamped backup before modifying.
"""

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

# Mapping from xicam device classes to new lightfall-endstation-7011 classes
XICAM_TO_OPHYD_MAP = {
    "xicam.Acquire.devices.diode.DetectorDiode": "lightfall_endstation_7011.devices.diode.DetectorDiode",
    "xicam.Acquire.devices.pimte3.PIMTE3": "lightfall_endstation_7011.devices.pimte3.PIMTE3",
    "xicam.Acquire.devices.andor.Andor": "lightfall_endstation_7011.devices.andor.Andor",
    "xicam.Acquire.devices.lakeshore.LakeShore336": "lightfall_endstation_7011.devices.lakeshore.LakeShore336",
    "xicam.Acquire.devices.motor.DeadbandEpicsMotor": "lightfall_endstation_7011.devices.motor.DeadbandEpicsMotor",
    # Generic fallbacks
    "xicam.Acquire.devices.areadetector.AreaDetector": "ophyd.areadetector.detectors.DetectorBase",
    "xicam.Acquire.devices.ophyd.OphydDevice": "ophyd.Device",
}


def fix_happi_db(db_path: Path, remove: bool = True, map_classes: bool = False) -> None:
    """Fix xicam references in happi database.

    Args:
        db_path: Path to happi JSON database.
        remove: If True, remove devices with xicam imports (default).
        map_classes: If True, attempt to map xicam classes to ophyd equivalents.
    """
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return

    # Create backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".{timestamp}.backup.json")
    shutil.copy2(db_path, backup_path)
    print(f"✅ Backup created: {backup_path}")

    # Load database
    with open(db_path) as f:
        data = json.load(f)

    # Handle different happi database formats
    is_dict_format = False
    device_keys = []
    
    if isinstance(data, list):
        devices = data
        is_dict_format = False
    elif isinstance(data, dict):
        if "devices" in data and data["devices"]:
            # Format: {"devices": [...]} with non-empty list
            devices = data["devices"]
            is_dict_format = False
        else:
            # Format: {"device_name": {...}, "device_name2": {...}}
            # Filter out metadata keys like "devices", "version", etc.
            devices = []
            device_keys = []
            for key, value in data.items():
                if isinstance(value, dict) and "device_class" in value:
                    devices.append(value)
                    device_keys.append(key)
            is_dict_format = True
    else:
        print(f"❌ Unknown database format")
        return

    original_count = len(devices)
    print(f"📊 Found {original_count} devices")

    # Process devices
    fixed_devices = []
    fixed_keys = []  # For dict format
    removed_count = 0
    mapped_count = 0

    for idx, device in enumerate(devices):
        device_class = device.get("device_class", "")
        name = device.get("name", "unknown")

        if "xicam" in device_class.lower():
            if remove and not map_classes:
                print(f"🗑️  Removing: {name} ({device_class})")
                removed_count += 1
                continue

            if map_classes and device_class in XICAM_TO_OPHYD_MAP:
                new_class = XICAM_TO_OPHYD_MAP[device_class]
                print(f"🔄 Mapping: {name}")
                print(f"   {device_class}")
                print(f"   → {new_class}")
                device["device_class"] = new_class
                mapped_count += 1
            else:
                print(f"⚠️  No mapping for: {name} ({device_class})")
                if remove:
                    removed_count += 1
                    continue

        fixed_devices.append(device)
        if is_dict_format:
            fixed_keys.append(device_keys[idx])

    # Write updated database
    if isinstance(data, list):
        output_data = fixed_devices
    elif is_dict_format:
        # Rebuild dictionary with device names as keys
        output_data = {key: device for key, device in zip(fixed_keys, fixed_devices)}
    else:
        data["devices"] = fixed_devices
        output_data = data

    with open(db_path, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\n✅ Database updated: {db_path}")
    print(f"📊 Original: {original_count} devices")
    print(f"📊 Final: {len(fixed_devices)} devices")
    if removed_count:
        print(f"🗑️  Removed: {removed_count} devices")
    if mapped_count:
        print(f"🔄 Mapped: {mapped_count} devices")


def main():
    import os
    
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "db_path",
        type=Path,
        nargs="?",
        help="Path to happi JSON database (default: $HAPPI_BACKEND or happi default)",
    )
    parser.add_argument(
        "--map",
        action="store_true",
        help="Try to map xicam classes to ophyd equivalents instead of removing",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep devices with unmappable xicam classes (default: remove)",
    )

    args = parser.parse_args()

    # Auto-detect happi database location
    if args.db_path is None:
        happi_backend = os.environ.get("HAPPI_BACKEND")
        if happi_backend:
            if happi_backend.startswith("json://"):
                args.db_path = Path(happi_backend[7:])
            else:
                print("❌ Only JSON happi backends are supported")
                return
        else:
            # Try default happi location
            default_path = Path.home() / ".config" / "happi" / "db.json"
            if default_path.exists():
                args.db_path = default_path
            else:
                print("❌ Could not find happi database. Set HAPPI_BACKEND or provide path.")
                return

    print(f"🔍 Processing: {args.db_path}")
    fix_happi_db(args.db_path, remove=not args.keep, map_classes=args.map)


if __name__ == "__main__":
    main()
