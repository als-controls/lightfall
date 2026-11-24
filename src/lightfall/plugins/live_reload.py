"""Live reload for plugins loaded from manifests (installed packages and core).

User plugins (single files in ``~/lightfall/plugins``) have their own reload in
``UserPluginService``. This module covers the rest: entries of any
``PluginManifest`` the loader processed, e.g. ``lightfall-vibe`` or Lightfall's
own ``lightfall.builtin``.

Only the plugin kinds that can be swapped cleanly are reloaded:

* **panel** -- the modules are re-imported, the new panel class is registered
  over the old one, the cached singleton instance is dropped, and a panel that
  was open is rebuilt in the same dock area (shown again if it was showing).
* **settings** -- a fresh page instance replaces the old one and its specs are
  re-registered; the Preferences dialog builds its pages when it opens, so the
  change shows the next time it is opened. ``on_loaded()`` is NOT run again.

Every other kind (services, tools, device backends, themes, ...) is reported
as needing a restart: they hold state or registrations elsewhere in the app
that a module re-import would not update.

Modules are re-imported with ``importlib.reload``, which re-executes them in
place. Only the modules that define the reloaded entries are re-imported, plus
any ``extra_modules`` the caller names explicitly (dependencies first). A
module that holds shared state (a singleton, a running worker) should not be
named there: re-executing it creates a second copy of that state.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from typing import Any

from lightfall.utils.logging import logger

RELOADABLE_TYPES = ("panel", "settings")

# Panels whose rebuild would tear down the caller: the Assistant panel hosts
# the agent session that is asking for the reload.
PROTECTED_PANELS = frozenset({"lightfall.panels.claude"})

_AUTO = object()  # window default: look the main window up


class LiveReloadError(Exception):
    """The target could not be resolved; nothing was changed."""


@dataclass
class ReloadReport:
    """What a reload did, for the agent tool's reply."""

    target: str
    manifest: str = ""
    modules: list[str] = field(default_factory=list)
    reloaded: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors and bool(self.reloaded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "target": self.target,
            "manifest": self.manifest,
            "modules_reloaded": self.modules,
            "reloaded": self.reloaded,
            "skipped": self.skipped,
            "warnings": self.warnings,
            "errors": self.errors,
        }


# --- lookups -------------------------------------------------------------------


def _default_plugin_registry():
    from lightfall.core.services import ServiceRegistry
    from lightfall.plugins.registry import PluginRegistry

    return ServiceRegistry.get_instance().get(PluginRegistry)


def _default_panel_registry():
    from lightfall.ui.panels.registry import PanelRegistry

    return PanelRegistry.get_instance()


def find_main_window():
    """The live LFMainWindow, or None (headless, early startup)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return None
    for widget in app.topLevelWidgets():
        if widget.__class__.__name__ == "LFMainWindow":
            return widget
    return None


def _panel_id_of(info) -> str | None:
    try:
        return info.instance.get_panel_class().panel_metadata.id
    except Exception:  # noqa: BLE001 - not loaded / not a panel plugin
        return None


def resolve_target(target: str, plugin_registry) -> tuple[str, list]:
    """Map ``target`` to (manifest name, [PluginInfo]).

    ``target`` is one of:
      * a manifest name -- every entry of that manifest;
      * ``"<manifest>:<entry>"`` or ``"<manifest>:<type>:<entry>"`` -- one entry;
      * a registered panel id -- the panel entry providing it.
    """
    manifests = list(plugin_registry.get_manifests())
    if target in manifests:
        return target, list(plugin_registry.get_plugins_from_manifest(target))

    manifest, sep, rest = target.partition(":")
    if sep and manifest in manifests:
        type_name, sep2, name = rest.rpartition(":")
        infos = [
            i for i in plugin_registry.get_plugins_from_manifest(manifest)
            if i.name == name and (not sep2 or i.type_name == type_name)
        ]
        if not infos:
            raise LiveReloadError(f"No entry '{rest}' in manifest '{manifest}'")
        if len(infos) > 1:
            kinds = ", ".join(f"{manifest}:{i.type_name}:{i.name}" for i in infos)
            raise LiveReloadError(f"'{target}' is ambiguous; use one of: {kinds}")
        return manifest, infos

    for manifest in manifests:
        for info in plugin_registry.get_plugins_from_manifest(manifest):
            if info.type_name == "panel" and _panel_id_of(info) == target:
                return manifest, [info]

    raise LiveReloadError(
        f"'{target}' is not a manifest, manifest entry or panel id. "
        f"Manifests: {', '.join(sorted(manifests))}"
    )


# --- module reload ------------------------------------------------------------------


def _modules_to_reload(infos, extra_modules) -> list[str]:
    """Explicit extras first (dependencies), then each entry's defining modules."""
    order: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in order:
            order.append(name)

    for name in extra_modules:
        add(name)
    for info in infos:
        if info.type_name == "panel":
            try:  # the panel class may live apart from its PanelPlugin
                add(info.instance.get_panel_class().__module__)
            except Exception:  # noqa: BLE001
                pass
        add(info.import_path.rsplit(":", 1)[0])
    return order


def _reload_modules(names: list[str], report: ReloadReport) -> bool:
    for name in names:
        module = sys.modules.get(name)
        try:
            if module is None:
                importlib.import_module(name)
            else:
                importlib.reload(module)
        except Exception as e:  # noqa: BLE001 - report syntax/import errors
            report.errors.append(f"Re-importing {name} failed: {type(e).__name__}: {e}")
            return False
        report.modules.append(name)
    return True


def _new_class(info):
    module_path, class_name = info.import_path.rsplit(":", 1)
    return getattr(sys.modules[module_path], class_name)


# --- per-kind swaps ---------------------------------------------------------------


def _reload_panel(info, panel_registry, window, report: ReloadReport) -> None:
    from lightfall.plugins.panel_plugin import PanelPlugin

    new_cls = _new_class(info)
    if not (isinstance(new_cls, type) and issubclass(new_cls, PanelPlugin)):
        report.errors.append(f"{info.import_path} is no longer a PanelPlugin")
        return
    old_id = _panel_id_of(info)
    plugin = new_cls()
    panel_class = plugin.get_panel_class()
    panel_id = panel_class.panel_metadata.id

    docking = getattr(window, "_docking_manager", None) if window is not None else None
    was_open = was_visible = False
    area = None
    if docking is not None:
        widget = docking.get_dock_widget(old_id) if old_id else None
        if widget is not None:
            was_open, was_visible = True, widget.isVisible()
            area = docking._panel_areas.get(old_id)
            docking.remove_panel(old_id)

    if old_id:
        panel_registry.destroy_singleton(old_id)
        if old_id != panel_id:
            panel_registry.unregister(old_id)
            report.warnings.append(f"Panel id changed: {old_id} -> {panel_id}")
    panel_registry.register(panel_class, replace=True)
    info.plugin_class, info.instance = new_cls, plugin

    reopened = False
    if was_open and window is not None:
        reopened = window.add_panel(panel_id, area=area) is not None
        if reopened and was_visible:
            docking.show_panel(panel_id)
        if not reopened:
            report.errors.append(f"Panel {panel_id} was closed but could not be reopened")
    report.reloaded.append({
        "entry": f"{info.type_name}:{info.name}",
        "panel_id": panel_id,
        "was_open": was_open,
        "reopened": reopened,
    })


def _preferences_dialog_open() -> bool:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return app is not None and any(
        w.isVisible() and w.__class__.__name__ == "PreferencesDialog"
        for w in app.topLevelWidgets()
    )


def _reload_settings(info, report: ReloadReport) -> None:
    from lightfall.plugins.settings_plugin import SettingsPlugin

    new_cls = _new_class(info)
    if not (isinstance(new_cls, type) and issubclass(new_cls, SettingsPlugin)):
        report.errors.append(f"{info.import_path} is no longer a SettingsPlugin")
        return
    page = new_cls()
    page.register_specs()
    info.plugin_class, info.instance = new_cls, page
    note = "Shows the next time Preferences is opened; on_loaded() was not re-run"
    if _preferences_dialog_open():
        note = "Preferences is open: close and reopen it to see the new page. " + note
    report.reloaded.append({"entry": f"{info.type_name}:{info.name}", "note": note})


# --- entry point ----------------------------------------------------------------------


def reload_installed_plugin(
    target: str,
    *,
    extra_modules: tuple[str, ...] | list[str] = (),
    plugin_registry=None,
    panel_registry=None,
    window: Any = _AUTO,
) -> ReloadReport:
    """Reload the panel and settings entries ``target`` names. GUI thread only.

    Raises LiveReloadError when ``target`` cannot be resolved (nothing changed).
    If re-importing a module fails, no panel is touched and the error is in the
    report (modules re-imported before the failing one stay re-imported).
    """
    plugin_registry = plugin_registry or _default_plugin_registry()
    panel_registry = panel_registry or _default_panel_registry()
    if window is _AUTO:
        window = find_main_window()

    manifest, infos = resolve_target(target, plugin_registry)
    report = ReloadReport(target=target, manifest=manifest)

    chosen = []
    for info in infos:
        entry = f"{info.type_name}:{info.name}"
        if info.type_name not in RELOADABLE_TYPES:
            report.skipped.append({
                "entry": entry,
                "reason": f"restart needed ({info.type_name} plugins are not live-reloadable)",
            })
        elif info.instance is None:
            report.skipped.append({"entry": entry, "reason": f"not loaded ({info.status.name})"})
        elif info.type_name == "panel" and _panel_id_of(info) in PROTECTED_PANELS:
            report.skipped.append({
                "entry": entry,
                "reason": "the Assistant panel hosts this agent session; restart to reload it",
            })
        else:
            chosen.append(info)
    if not chosen:
        report.errors.append("Nothing live-reloadable in this target")
        return report

    modules = _modules_to_reload(chosen, extra_modules)
    # A re-executed module that also defines an entry we are not swapping
    # leaves that entry running the old code; say so.
    for info in infos:
        if info in chosen:
            continue
        module = info.import_path.rsplit(":", 1)[0]
        if module in modules:
            report.warnings.append(
                f"{module} also defines {info.type_name}:{info.name}, which keeps "
                "running the old code until a restart"
            )
    if not _reload_modules(modules, report):
        return report

    for info in chosen:
        try:
            if info.type_name == "panel":
                _reload_panel(info, panel_registry, window, report)
            else:
                _reload_settings(info, report)
        except Exception as e:  # noqa: BLE001 - keep going with the other entries
            logger.exception("Live reload of {}:{} failed", info.type_name, info.name)
            report.errors.append(f"{info.type_name}:{info.name}: {type(e).__name__}: {e}")
    logger.info(
        "Live-reloaded {}: {} (modules: {})",
        target, [r["entry"] for r in report.reloaded], report.modules,
    )
    return report
