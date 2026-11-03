"""Declarative types describing a generated preferences page."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from lightfall_settings import SettingSpec

if TYPE_CHECKING:
    from qtpy.QtWidgets import QWidget

    from lightfall.ui.preferences.form.editors import FieldEditor


class FormValues(Protocol):
    """Pending values of a form, as seen by predicates and validators."""

    scope: str

    def get(self, key: str) -> Any:
        """Pending value at the selected scope; None means not set."""
        ...

    def effective(self, key: str, default: Any = None) -> Any:
        """Value that would apply: the edit at this scope if any, else the resolved value."""
        ...

    def is_dirty(self, key: str) -> bool:
        """Whether the user has an unsaved edit to ``key`` at this scope."""
        ...


Predicate = Callable[["FormValues"], bool]


@dataclass(frozen=True)
class CatalogItem:
    """One discovered item (plugin, tool, monitor) listed by a Catalog field."""

    id: str
    label: str
    default_enabled: bool = True
    protected: bool = False  # can never be set Off (shown read-only, fixed On)
    columns: tuple[str, ...] = ()  # extra read-only text, one per Catalog column
    default: Any = None  # Mapping only: the code default value, shown as "<value> (default)"; None = none
    # Why the item has no effect right now (e.g. its monitor plugin is off): its text is drawn
    # muted and this is added to its tooltip; the row stays editable. "" = active.
    inactive_reason: str = ""


@dataclass(frozen=True)
class EditorSpec:
    """Which editor widget to build for a field, and its parameters."""

    # "toggle" | "choice" | "integer" | "number" | "text" | "url" | "secret" | "path" | "numberlist"
    # | "catalog" | "mapping" | "custom"
    kind: str
    options: Sequence[tuple[Any, str]] | Callable[[], Sequence[tuple[Any, str]]] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    schemes: tuple[str, ...] = ("http", "https")  # url only
    require_host: bool = True  # url only
    mode: str = "file"  # path only: "file" or "dir"
    file_filter: str = ""  # path only: QFileDialog name filter, e.g. "Models (*.bin)"
    # text/url/path only: a blank (whitespace-only) entry means Not set (None) instead of the value "".
    blank_is_unset: bool = False
    items: Callable[[], Sequence[CatalogItem]] | None = None  # catalog only
    columns: tuple[str, ...] = ()  # catalog/mapping only: headers of the items' extra text columns
    value: EditorSpec | None = None  # mapping only: the editor of each item's value
    factory: Callable[[SettingField], FieldEditor] | None = None  # custom only: builds the editor


def Toggle() -> EditorSpec:  # noqa: N802
    return EditorSpec("toggle")


def Choice(options: Sequence[tuple[Any, str]] | Callable[[], Sequence[tuple[Any, str]]]) -> EditorSpec:  # noqa: N802
    return EditorSpec("choice", options=options)


def Integer(minimum: float | None = None, maximum: float | None = None, step: float | None = 1) -> EditorSpec:  # noqa: N802
    return EditorSpec("integer", minimum=minimum, maximum=maximum, step=step)


def Number(minimum: float | None = None, maximum: float | None = None, step: float | None = None) -> EditorSpec:  # noqa: N802
    return EditorSpec("number", minimum=minimum, maximum=maximum, step=step)


def Text(blank_is_unset: bool = False) -> EditorSpec:  # noqa: N802
    return EditorSpec("text", blank_is_unset=blank_is_unset)


def Url(  # noqa: N802
    schemes: tuple[str, ...] = ("http", "https"), require_host: bool = True, blank_is_unset: bool = False
) -> EditorSpec:
    return EditorSpec("url", schemes=tuple(schemes), require_host=require_host, blank_is_unset=blank_is_unset)


def Secret() -> EditorSpec:  # noqa: N802
    return EditorSpec("secret")


def Path(mode: str = "file", filter: str = "", blank_is_unset: bool = True) -> EditorSpec:  # noqa: N802, A002
    """A file (``mode="file"``) or folder (``mode="dir"``) path: text with a Browse button.

    ``filter`` is the file dialog's name filter. The value is the stripped text.
    """
    if mode not in ("file", "dir"):
        raise ValueError(f"Path mode must be 'file' or 'dir', not {mode!r}")
    return EditorSpec("path", mode=mode, file_filter=filter, blank_is_unset=blank_is_unset)


def NumberList(minimum: float | None = None) -> EditorSpec:  # noqa: N802
    """A ``list[float]`` typed as comma-separated numbers; blank is Not set."""
    return EditorSpec("numberlist", minimum=minimum)


def Catalog(items: Callable[[], Sequence[CatalogItem]], columns: Sequence[str] = ()) -> EditorSpec:  # noqa: N802
    """An item-merged ``{id: bool}`` setting, shown as a table of ``items()`` with extra ``columns``."""
    return EditorSpec("catalog", items=items, columns=tuple(columns))


def Mapping(  # noqa: N802
    items: Callable[[], Sequence[CatalogItem]], value: EditorSpec, columns: Sequence[str] = ()
) -> EditorSpec:
    """An item-merged ``{id: value}`` setting: a table of ``items()`` with a ``value`` editor per row.

    The field's ``unit`` is shown in the "Here" column header and after inherited values.
    Code defaults come from ``CatalogItem.default``.
    """
    return EditorSpec("mapping", items=items, columns=tuple(columns), value=value)


def Custom(factory: Callable[[SettingField], FieldEditor]) -> EditorSpec:  # noqa: N802
    """An editor built by ``factory(field)``, for settings no stock editor fits."""
    return EditorSpec("custom", factory=factory)


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
    # Extra keys whose values this field's editor owns at the same scope. Its editor then
    # implements companion_values() -> {key: value or None} and set_companion_values(values)
    # (no ``changed``). Companions are written, reported and cleared with the field but get no row.
    companions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Action:
    """A button that runs ``run`` on a worker thread and shows its status text.

    ``run`` returns the text to show; an exception shows ``str(exc)`` as an
    error. ``run`` gets a snapshot of the page's own fields taken at the
    click: ``get``/``effective`` of any other key raise KeyError. ``scopes`` are scope kinds ("user", "local", "beamline", ...);
    None shows the action at every scope. Actions write nothing, so
    read-only scopes never disable them.

    ``run_on_show`` starts the action once each time its section is built for
    a scope, or, if it is hidden or disabled then, when an edit first makes it
    visible and enabled. After an edit to a key in ``refresh_on`` such an
    action runs again if it is visible, enabled and not already running.
    """

    label: str
    run: Callable[[FormValues], str]
    enabled_when: Predicate | None = None
    scopes: frozenset[str] | None = None
    visible_when: Predicate | None = None
    run_on_show: bool = False
    refresh_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class Info:
    """A muted status line; a callable is re-evaluated after every edit (GUI thread).

    ``field`` names the setting the line describes; the line then lines up
    with the field labels instead of the section edge. ``visible_when`` false
    hides the line (and its text is not evaluated).
    """

    text: Callable[[FormValues], str] | str
    scopes: frozenset[str] | None = None
    field: str | None = None
    visible_when: Predicate | None = None


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
