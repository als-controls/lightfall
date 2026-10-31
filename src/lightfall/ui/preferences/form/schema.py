"""Declarative types describing a generated preferences page."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from lightfall_settings import SettingSpec


class FormValues(Protocol):
    """Pending values of a form, as seen by predicates and validators."""

    scope: str

    def get(self, key: str) -> Any:
        """Pending value at the selected scope; None means not set."""
        ...


Predicate = Callable[["FormValues"], bool]


@dataclass(frozen=True)
class EditorSpec:
    """Which editor widget to build for a field, and its parameters."""

    kind: str  # "toggle" | "choice" | "integer" | "number" | "text"
    options: Sequence[tuple[Any, str]] | Callable[[], Sequence[tuple[Any, str]]] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


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
class Section:
    """A titled group of fields."""

    title: str
    items: Sequence[SettingField]
    description: str = ""


def field_label(field: SettingField, spec: SettingSpec) -> str:
    """Label for a field: its own, else the spec's, else the key."""
    return field.label or spec.label or field.key


def fields_at(
    sections: Sequence[Section], scope: str, allowed: Callable[[str, str], bool]
) -> list[SettingField]:
    """Fields of all sections, in order, whose key may be set at ``scope``."""
    return [f for s in sections for f in s.items if allowed(f.key, scope)]
