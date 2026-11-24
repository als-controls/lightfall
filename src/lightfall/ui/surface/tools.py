"""Agent tools over the UI surface: ui_snapshot, ui_act, ui_wait.

Served from the assistant's ``qt`` tool server (``lightfall.claude.tools``). Handlers run
on the agent's event loop; blocking work goes to a thread so the loop stays responsive.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from PySide6.QtWidgets import QWidget

from lightfall.ui.surface.act import ACTIONS, act
from lightfall.ui.surface.tree import SurfaceError
from lightfall.ui.surface.wait import CONDITIONS, wait


def _text(text: str, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["is_error"] = True
    return out


def _error(e: Exception) -> dict[str, Any]:
    message = str(e)
    if isinstance(e, RuntimeError) and "\n" in message:  # run_on_main_thread traceback
        message = message.split("\n", 1)[0]
    return _text(message, is_error=True)


def _target(args: dict) -> str | dict | None:
    if args.get("ref"):
        return str(args["ref"])
    target = {k: args[k] for k in ("role", "name", "within") if args.get(k)}
    return target or None


TARGET_PROPS = {
    "ref": {"type": "string", "description": "A ref from the latest ui_snapshot, e.g. 'e14'. "
                                             "Refs go stale at the next snapshot."},
    "role": {"type": "string", "description": "Role of the target: button, toggle, checkbox, "
                                              "radio, field, combo, list, table, tree, item, "
                                              "tab, menuitem, dock, dialog, plot, ..."},
    "name": {"type": "string", "description": "Its name as shown in the snapshot (exact, "
                                              "case-insensitive; a unique prefix also works)."},
    "within": {"type": "string", "description": "Panel, dialog or group name (or ref) to search "
                                                "in, when a name appears more than once."},
}


def create_surface_tools(target_window: QWidget | None = None) -> tuple:
    """The three surface tools. ``target_window`` is unused: the surface sees every
    visible Lightfall window, including dialogs the main window opens."""
    from lightfall.agents.tooling import tool

    @tool(
        name="ui_snapshot",
        description=(
            "What a person sees in Lightfall's windows right now, as an outline of roles and "
            "names (buttons, fields with their values, lists with their items, tabs, dialogs, "
            "toasts) with [refs] for ui_act. Panels other than the focused one are collapsed "
            "to a count; pass scope=<panel or dialog name, or a ref> to see one part in full."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "Panel/dialog/group name or ref."},
                "max_lines": {"type": "number", "description": "Default 400."},
            },
        },
    )
    async def ui_snapshot(args: dict) -> dict[str, Any]:
        from lightfall.claude._internal.threading import run_on_main_thread
        from lightfall.ui.surface.snapshot import snapshot

        try:
            text = await asyncio.to_thread(
                run_on_main_thread, snapshot, args.get("scope") or None,
                int(args.get("max_lines") or 400))
            return _text(text)
        except (SurfaceError, RuntimeError) as e:
            return _error(e)

    @tool(
        name="ui_act",
        description=(
            "Operate Lightfall's UI with real mouse and keyboard input, as a person would. "
            "Target by ref, or by role + name (+ within). Actions: click, double_click, "
            "right_click, check, uncheck, fill (value = text; commits with Enter), select "
            "(value = combo option, list item, tab name, or 'Menu > Entry'), press (value = "
            "keys like 'Return' or 'Ctrl+S'), scroll_to, open_panel (value = panel name), "
            "close. Returns what changed: dialogs opened or closed, toasts, the target's new "
            "value. Prefer role+name targets after something changed on screen."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                **TARGET_PROPS,
                "value": {"type": "string", "description": "Text, option, keys or panel name."},
            },
            "required": ["action"],
        },
    )
    async def ui_act(args: dict) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(act, str(args.get("action", "")), _target(args),
                                             args.get("value"))
            return _text(json.dumps(result, default=str), is_error=not result.get("ok", True))
        except (SurfaceError, RuntimeError) as e:
            return _error(e)

    @tool(
        name="ui_wait",
        description=(
            "Wait until something is true on screen. Conditions: idle (no plan running and the "
            "UI settled), element (role/name[/within] appears, or disappears with gone=true), "
            "toast (a notification whose title or text contains `text`, optional kind "
            "info|success|warning|error; also catches ones that already faded), run_finished "
            "(a run started and the engine is idle again). A timeout returns met=false."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "condition": {"type": "string", "enum": list(CONDITIONS)},
                "timeout_s": {"type": "number", "description": "Default 30."},
                "role": TARGET_PROPS["role"], "name": TARGET_PROPS["name"],
                "within": TARGET_PROPS["within"],
                "gone": {"type": "boolean"},
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": ["info", "success", "warning", "error"]},
            },
            "required": ["condition"],
        },
    )
    async def ui_wait(args: dict) -> dict[str, Any]:
        kw = {k: args[k] for k in ("role", "name", "within", "gone", "text", "kind") if k in args}
        try:
            result = await asyncio.to_thread(wait, str(args.get("condition", "")),
                                             float(args.get("timeout_s") or 30), **kw)
            return _text(json.dumps(result, default=str))
        except (SurfaceError, RuntimeError) as e:
            return _error(e)

    return ui_snapshot, ui_act, ui_wait


def widget_for_scope(scope: str) -> QWidget:
    """The widget a screenshot scope names (panel/dialog name or ref). GUI thread."""
    from lightfall.ui.surface.locate import locate
    from lightfall.ui.surface.snapshot import find_scope
    from lightfall.ui.surface.tree import build_all

    text = scope.strip()
    if len(text) >= 2 and text[0] in "pdwte" and text[1:].isdigit():
        node = locate(text)
    else:
        node = find_scope(build_all(), text)
    if node.widget is None:
        raise SurfaceError(f"'{scope}' has no widget to capture")
    return node.widget
