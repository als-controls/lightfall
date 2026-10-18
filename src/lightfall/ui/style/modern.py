"""Modern style: tokens and QSS builders.

Follows docs/superpowers/notes/design/2026-10-17-lightfall-visual-language.md. Every rule
is scoped to a container with ``setProperty("modern", "surface")``; widgets inside opt in
with dynamic properties (``modern="primary"`` for the one accent button, ``"link"`` for
quiet text links, ``"tile"``, ``"note"`` + ``kind``, ``"rail-item"`` + ``railState``, ...).
QSS has no uppercase or letter-spacing: callers upper-case kicker text themselves.
The setup wizard is the first user; other UI can opt in later.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from lightfall.ui.theme import ThemeColors

SCOPE = 'QFrame[modern="surface"]'
NOTE_KINDS = ("ok", "warn", "error", "info")
RAIL_STATES = ("done", "current", "warn", "todo", "skipped", "checking")

DARK: dict[str, str] = {
    "bg": "#0e1117", "pane": "#181d27", "rail": "#141923", "line": "#283042",
    "field": "#10141c", "txt": "#e7eaf0", "dim": "#8b94a7", "acc": "#5aa9ff",
    "acc_txt": "#061322", "ok": "#3fb97c", "ok_txt": "#04210f", "warn": "#e2a23b",
    "err": "#ef5350", "cur": "#1f2738", "seg_on": "#22314a",
}
LIGHT: dict[str, str] = {
    "bg": "#eef1f6", "pane": "#ffffff", "rail": "#f4f6fa", "line": "#d6dce6",
    "field": "#f8f9fb", "txt": "#1b2230", "dim": "#5d6778", "acc": "#1f6fd1",
    "acc_txt": "#ffffff", "ok": "#1f8f57", "ok_txt": "#ffffff", "warn": "#b7791f",
    "err": "#c62828", "cur": "#e6ecf5", "seg_on": "#dde8f8",
}


def tokens(theme: str, colors: ThemeColors | None = None) -> dict[str, str]:
    """Token dict for ``"dark"`` (the notes' values) or ``"light"`` (from ThemeManager)."""
    if theme not in ("dark", "light"):
        raise ValueError(f"unknown theme {theme!r}")
    t = dict(DARK if theme == "dark" else LIGHT)
    if colors is not None and theme == "light":
        t.update(pane=colors.background, bg=colors.surface, txt=colors.text,
                 dim=colors.text_secondary, line=colors.border, acc=colors.primary,
                 ok=colors.success, warn=colors.warning, err=colors.error)
    elif colors is not None:
        t["err"] = colors.error
    return t


def current_tokens() -> dict[str, str]:
    from lightfall.ui.theme import ThemeManager

    manager = ThemeManager.get_instance()
    return tokens("dark" if manager.is_dark else "light", manager.colors)


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {round(alpha * 255)})"


def primary_button(t: dict[str, str]) -> str:
    sel = f'{SCOPE} QPushButton[modern="primary"]'
    return (
        f'{sel} {{ background: {t["acc"]}; color: {t["acc_txt"]}; border: none; '
        f'border-radius: 9px; padding: 8px 18px; font-weight: 600; }}\n'
        f'{sel}:hover {{ border: 1px solid {rgba(t["txt"], 0.35)}; }}\n'
        f'{sel}:disabled {{ background: {t["line"]}; color: {t["dim"]}; }}'
    )


def link_button(t: dict[str, str]) -> str:
    sel = f'{SCOPE} QPushButton[modern="link"]'
    return (
        f'{sel} {{ background: transparent; border: none; color: {t["dim"]}; '
        f'padding: 4px 2px; }}\n'
        f'{sel}:hover {{ color: {t["txt"]}; }}\n'
        f'{sel}:disabled {{ color: {t["line"]}; }}'
    )


def segment(t: dict[str, str]) -> str:
    sel = f'{SCOPE} QPushButton[modern="segment"]'
    return (
        f'{sel} {{ background: transparent; color: {t["dim"]}; border: 1px solid {t["line"]}; '
        f'padding: 7px 14px; border-radius: 0px; }}\n'
        f'{sel}[segPos="first"] {{ border-top-left-radius: 9px; '
        f'border-bottom-left-radius: 9px; }}\n'
        f'{sel}[segPos="last"] {{ border-top-right-radius: 9px; '
        f'border-bottom-right-radius: 9px; }}\n'
        f'{sel}:checked {{ background: {t["seg_on"]}; color: {t["txt"]}; }}'
    )


def tile(t: dict[str, str]) -> str:
    sel = f'{SCOPE} QFrame[modern="tile"]'
    return (
        f'{sel} {{ border: 1px solid {t["line"]}; border-radius: 12px; '
        f'background: transparent; }}\n'
        f'{sel}[selected="true"] {{ border: 1px solid {t["acc"]}; '
        f'background: {rgba(t["acc"], 0.06)}; }}\n'
        f'{SCOPE} QLabel[tileRole="title"] {{ color: {t["txt"]}; font-size: 14px; '
        f'font-weight: 600; }}\n'
        f'{SCOPE} QLabel[tileRole="subtitle"] {{ color: {t["dim"]}; font-size: 12px; }}\n'
        f'{SCOPE} QLabel[tileRole="avatar"] {{ background: {t["seg_on"]}; '
        f'border-radius: 9px; font-size: 15px; }}'
    )


def note(t: dict[str, str], kind: str) -> str:
    color = {"ok": t["ok"], "warn": t["warn"], "error": t["err"], "info": t["acc"]}[kind]
    return (
        f'{SCOPE} QFrame[modern="note"][kind="{kind}"] {{ background: {rgba(color, 0.10)}; '
        f'border: 1px solid {rgba(color, 0.35)}; border-radius: 10px; }}'
    )


_RAIL_ICON = {
    "done": "background: {ok}; border: 2px solid {ok}; color: {ok_txt}; font-weight: 700;",
    "current": "background: transparent; border: 2px solid {acc}; color: {acc};",
    "warn": "background: transparent; border: 2px solid {warn}; color: {warn}; "
            "font-weight: 700;",
    "todo": "background: transparent; border: 2px solid {line}; color: {dim};",
    "skipped": "background: transparent; border: 2px solid {line}; color: {dim};",
    "checking": "background: transparent; border: 2px solid {line}; color: {dim};",
}


def rail_item(t: dict[str, str], state: str) -> str:
    icon = _RAIL_ICON[state].format(**t)
    title = t["dim"] if state in ("todo", "skipped", "checking") else t["txt"]
    item = f'{SCOPE} QFrame[modern="rail-item"][railState="{state}"]'
    return (
        f'{item} QLabel[railRole="icon"] {{ {icon} border-radius: 9px; min-width: 18px; '
        f'max-width: 18px; min-height: 18px; max-height: 18px; font-size: 10px; }}\n'
        f'{item} QLabel[railRole="title"] {{ color: {title}; font-size: 13px; }}'
    )


def field(t: dict[str, str]) -> str:
    inputs = f"{SCOPE} QLineEdit, {SCOPE} QComboBox, {SCOPE} QSpinBox"
    return (
        f'{inputs} {{ background: {t["field"]}; color: {t["txt"]}; '
        f'border: 1px solid {t["line"]}; border-radius: 8px; padding: 6px 10px; }}\n'
        f'{SCOPE} QLineEdit:focus, {SCOPE} QComboBox:focus, {SCOPE} QSpinBox:focus '
        f'{{ border: 1px solid {t["acc"]}; }}\n'
        f'{SCOPE} QCheckBox {{ color: {t["txt"]}; spacing: 8px; }}\n'
        f'{SCOPE} QCheckBox::indicator {{ width: 30px; height: 17px; border-radius: 8px; '
        f'background: {t["line"]}; }}\n'
        f'{SCOPE} QCheckBox::indicator:checked {{ background: {t["acc"]}; }}'
    )


def group(t: dict[str, str]) -> str:
    return (
        f'{SCOPE} QGroupBox {{ border: none; margin-top: 16px; padding-top: 4px; '
        f'color: {t["dim"]}; }}\n'
        f'{SCOPE} QGroupBox::title {{ subcontrol-origin: margin; left: 0px; padding: 0px; '
        f'color: {t["dim"]}; }}'
    )


def stylesheet(t: dict[str, str]) -> str:
    """The whole modern style for a ``modern="surface"`` container."""
    parts = [
        f'{SCOPE} {{ background: {t["pane"]}; border: 1px solid {t["line"]}; '
        f'border-radius: 16px; }}',
        f'{SCOPE} QFrame#setupRail {{ background: {t["rail"]}; border: none; '
        f'border-right: 1px solid {t["line"]}; border-top-left-radius: 16px; '
        f'border-bottom-left-radius: 16px; }}',
        f'{SCOPE} QFrame#setupMain {{ background: transparent; border: none; }}',
        f'{SCOPE} QLabel {{ color: {t["txt"]}; background: transparent; border: none; }}',
        f'{SCOPE} QLabel[modern="brand"] {{ font-size: 13px; font-weight: 600; }}',
        f'{SCOPE} QLabel[modern="kicker"] {{ color: {t["acc"]}; font-size: 11px; }}',
        f'{SCOPE} QLabel[modern="title"] {{ font-size: 21px; font-weight: 600; }}',
        f'{SCOPE} QLabel[modern="lead"] {{ color: {t["dim"]}; font-size: 13px; }}',
        f'{SCOPE} QLabel[modern="muted"] {{ color: {t["dim"]}; }}',
        f'{SCOPE} QLabel[railRole="value"] {{ color: {t["dim"]}; font-size: 11px; }}',
        f'{SCOPE} QFrame[modern="rail-item"] {{ background: transparent; border: none; '
        f'border-radius: 9px; }}',
        f'{SCOPE} QFrame[modern="rail-item"][railCurrent="true"] {{ background: {t["cur"]}; }}',
        primary_button(t), link_button(t), segment(t), tile(t), field(t), group(t),
        *(note(t, kind) for kind in NOTE_KINDS),
        *(rail_item(t, state) for state in RAIL_STATES),
    ]
    return "\n".join(parts) + "\n"


def repolish(widget: QWidget) -> None:
    """Re-apply the style after a dynamic property used by a selector changed."""
    style = widget.style()
    for w in (widget, *widget.findChildren(QWidget)):
        style.unpolish(w)
        style.polish(w)
    widget.update()
