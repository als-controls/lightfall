"""Declarative types describing a generated preferences page."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from lightfall_settings import SettingSpec

if TYPE_CHECKING:
    from qtpy.QtWidgets import QWidget


class FormValues(Protocol):
    """Pending values of a form, as seen by predicates and validators."""

    scope: str

    def get(self, key: str) -> Any:
        """Pending value at the selected scope; None means not set."""
        ...

    def effective(self, key: str, default: Any = None) -> Any:
        """Value that would apply: the edit at this scope if any, else the resolved value."""
        ...


Predicate = Callable[["FormValues"], bool]


@dataclass(frozen=True)
class EditorSpec:
    """Which editor widget to build for a field, and its parameters."""

    kind: str  # "toggle" | "choice" | "integer" | "number" | "text" | "url" | "secret"
    options: Sequence[tuple[Any, str]] | Callable[[], Sequence[tuple[Any, str]]] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    schemes: tuple[str, ...] = ("http", "https")  # url only
    require_host: bool = True  # url only


def Toggle() -> EditorSpec:  # noqa: N802
    return EditorSpec("toggle")


def Choice(options: Sequence[tuple[Any, str]] | Callable[[], Sequence[tuple[Any, str]]]) -> EditorSpec:  # noqa: N802
    return EditorSpec("choice", options=options)


def Integer(minimum: float | None = None, maximum: float | None = None, step: float | None = 1) -> EditorSpec:  # noqa: N802
    return EditorSpec("integer", minimum=minimum, maximum=maximum, step=step)


def Number(minimum: float | None = None, maximum: float | None = None, step: float | None = None) -> EditorSpec:  # noqa: N802
    return EditorSpec("number", minimum=minimum, maximum=maximum, step=step)


def Text() -> EditorSpec:  # noqa: N802
    return EditorSpec("text")


def Url(schemes: tuple[str, ...] = ("http", "https"), require_host: bool = True) -> EditorSpec:  # noqa: N802
    return EditorSpec("url", schemes=tuple(schemes), require_host=require_host)


def Secret() -> EditorSpec:  # noqa: N802
    return EditorSpec("secret")


@dataclass(frozen=True)
class SettingField:
    """One setting shown on a form page."""

    key: str
    editor: EditorSpec
    label: str = ""
    description: str = ""
    placeholder: str = ""
    unit: str = ""
    restart_required: bool = False
    visible_when: Predicate | None = None
    enabled_when: Predicate | None = None
    validate: Callable[[Any, FormValues], str | None] | None = None


@dataclass(frozen=True)
class Action:
    """A button that runs ``run`` on a worker thread and shows its status text.

    ``run`` returns the text to show; an exception shows ``str(exc)`` as an
    error. ``run`` gets a snapshot of the page's own fields taken at the
    click: ``get``/``effective`` of any other key raise KeyError. ``scopes`` are scope kinds ("user", "local", "beamline", ...);
    None shows the action at every scope. Actions write nothing, so
    read-only scopes never disable them.
    """

    label: str
    run: Callable[[FormValues], str]
    enabled_when: Predicate | None = None
    scopes: frozenset[str] | None = None


@dataclass(frozen=True)
class Info:
    """A muted status line; a callable is re-evaluated after every edit (GUI thread)."""

    text: Callable[[FormValues], str] | str
    scopes: frozenset[str] | None = None


@dataclass(frozen=True)
class Embed:
    """A plain widget inside a section that is not a preference (e.g. a list with buttons).

    ``factory`` is called on every ``show_scope``, so the widget must load its own state.
    """

    factory: Callable[[QWidget | None], QWidget]
    scopes: frozenset[str] | None = None


Item = SettingField | Action | Info | Embed


@dataclass(frozen=True)
class Section:
    """A titled group of fields and non-preference items, shown in order."""

    title: str
    items: Sequence[Item]
    description: str = ""


def scope_kind(scope: str) -> str:
    """Kind of a scope id: "user:u1" -> "user", "local" -> "local"."""
    return scope.split(":", 1)[0]


def item_shown_at(item: Action | Info | Embed, scope: str) -> bool:
    """Whether a non-field item belongs at ``scope`` (by its ``scopes`` kinds)."""
    return item.scopes is None or scope_kind(scope) in item.scopes


def section_fields(section: Section) -> list[SettingField]:
    """The SettingFields of a section, in order (other items skipped)."""
    return [f for f in section.items if isinstance(f, SettingField)]


def field_label(field: SettingField, spec: SettingSpec) -> str:
    """Label for a field: its own, else the spec's, else the key."""
    return field.label or spec.label or field.key


def fields_at(
    sections: Sequence[Section], scope: str, allowed: Callable[[str, str], bool]
) -> list[SettingField]:
    """Fields of all sections, in order, whose key may be set at ``scope``."""
    return [f for s in sections for f in section_fields(s) if allowed(f.key, scope)]
