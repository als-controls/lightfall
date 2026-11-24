"""Resolve an agent's target (a ref, or role + name + within) to exactly one node.

Strict: two matches is an error that lists both, so the agent narrows with ``within``
instead of the surface guessing. The search runs over the same tree the snapshot shows,
fully expanded, so anything the agent can see it can target.
"""

from __future__ import annotations

from typing import Any

from lightfall.ui.surface.snapshot import find_scope
from lightfall.ui.surface.tree import REFS, Node, SurfaceError, build_all
from lightfall.utils.crash_diagnostics import gui_thread_only

#: Words agents use for roles, mapped to the surface's role names.
ROLE_ALIASES = {
    "panel": "dock", "textbox": "field", "input": "field", "text field": "field",
    "spinbox": "field", "dropdown": "combo", "combobox": "combo", "select": "combo",
    "menu item": "menuitem", "menu_item": "menuitem", "row": "item", "option": "item",
    "check box": "checkbox", "toggle button": "toggle", "tab bar": "tabs",
}

MAX_CANDIDATES = 8


class LocateError(SurfaceError):
    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        self.candidates = list(candidates or [])
        if self.candidates:
            message += ": " + "; ".join(self.candidates)
        super().__init__(message)


def _is_ref(text: str) -> bool:
    return len(text) >= 2 and text[0] in "pdwte" and text[1:].isdigit()


def _norm_role(role: str | None) -> str | None:
    if not role:
        return None
    r = role.strip().casefold()
    return ROLE_ALIASES.get(r, r)


def _matches(nodes: list[Node], role: str | None, name: str | None) -> list[Node]:
    pool = [n for root in nodes for n in root.walk() if role is None or n.role == role]
    if name is None:
        return pool
    wanted = name.strip().casefold()
    exact = [n for n in pool if n.name.casefold() == wanted]
    if exact:
        return exact
    return [n for n in pool if n.name.casefold().startswith(wanted)]


@gui_thread_only
def locate(target: str | dict[str, Any]) -> Node:
    """A ref (``"e14"``) or ``{"role", "name", "within"}`` -> one node."""
    if isinstance(target, str):
        text = target.strip()
        if _is_ref(text):
            return REFS.resolve(text)
        target = {"name": text}
    if not isinstance(target, dict):
        raise LocateError("target must be a ref like 'e12' or an object with role/name/within")
    role = _norm_role(target.get("role"))
    name = target.get("name")
    within = target.get("within")
    if role is None and name is None:
        raise LocateError("target needs a role, a name, or both")

    nodes = build_all()
    if within:
        scope = find_scope(nodes, str(within))
        nodes = [scope]
    found = _matches(nodes, role, name)
    # A ref-less container match (e.g. a dock) also matches its own landmark node.
    if len(found) == 1:
        return found[0]
    what = f'{role or "element"}' + (f' "{name}"' if name is not None else "")
    where = f' in "{within}"' if within else ""
    if not found:
        same_role = [n.label for root in nodes for n in root.walk()
                     if role is not None and n.role == role][:MAX_CANDIDATES]
        if same_role:
            raise LocateError(f"no {what}{where}; {role}s here", same_role)
        raise LocateError(f"no {what}{where}; take a ui_snapshot to see what is on screen")
    raise LocateError(f"{len(found)} matches for {what}{where}; add 'within' or use a ref",
                      [n.label for n in found[:MAX_CANDIDATES]])
