"""Read-only facility/beamline/branch settings from YAML files (spec section 5, local-only mode).

For machines without a logbook server (standalone, dev, offline installs). Files live in
``%PROGRAMDATA%/ncs/scopes/`` (``/etc/ncs/scopes/`` elsewhere), one per scope:
``facility.yaml``, ``beamline-7.0.1.yaml``, ``branch-7.0.1.1.yaml``::

    settings:
      tiled_url: https://tiled.example
    locks:
      tiled_url: set by facility staff
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from lightfall.scopes.client import ChainInfo, Snapshot
from lightfall.scopes.identity import beamline_of
from lightfall.scopes.keys import is_local_only
from lightfall.scopes.state import FetchedState
from lightfall.utils.logging import logger


def local_scopes_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "ncs" / "scopes"
    return Path("/etc/ncs/scopes")


def local_chain(bind: str | None) -> tuple[str, ...]:
    if not bind or bind == "facility":
        return ("facility",)
    kind, _, _name = bind.partition(":")
    if kind == "beamline":
        return ("facility", bind)
    beamline = beamline_of(bind)
    return ("facility", f"beamline:{beamline}", bind) if beamline else ("facility",)


def _file_for(directory: Path, scope: str) -> Path:
    return directory / f"{scope.replace(':', '-')}.yaml"


def _read(path: Path) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Ignoring unreadable scope file {}: {}", path, e)
        return {}
    if not isinstance(data, Mapping):
        logger.warning("Ignoring scope file {}: not a mapping", path)
        return {}
    return data


def _mapping(data: Mapping[str, Any], field: str, path: Path) -> dict[str, Any]:
    value = data.get(field) or {}
    if not isinstance(value, Mapping):
        logger.warning("Ignoring {} in {}: not a mapping", field, path)
        return {}
    return {str(k): v for k, v in value.items() if not is_local_only(str(k))}


def load_local_state(directory: Path, bind: str | None) -> FetchedState | None:
    """The local chain's snapshots, or None when no scope file exists at all."""
    chain = local_chain(bind)
    snapshots: dict[str, Snapshot] = {}
    found = False
    for scope in chain:
        path = _file_for(Path(directory), scope)
        found = found or path.is_file()
        data = _read(path)
        locks = {k: {"locked_by": "local file", "reason": str(v or ""), "locked_at": ""}
                 for k, v in _mapping(data, "locks", path).items()}
        snapshots[scope] = Snapshot(scope=scope, settings=_mapping(data, "settings", path),
                                    locks=locks)
    if not found:
        return None
    return FetchedState(chain=ChainInfo(chain=chain, roles=dict.fromkeys(chain), entitled=(),
                                        facility_label=""),
                        snapshots=snapshots, offline=True, cached_at=None)
