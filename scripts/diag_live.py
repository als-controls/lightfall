"""LIVE dock diagnostic — run INSIDE the running Lightfall app.

In Lightfall's IPython panel:
    %run scripts/diag_live.py

Uses the already-running QApplication (faithful: real theme, real layout,
real panels). For every visible dock panel it reports, for the bottom-left
corner, the rendered color and which widget owns each pixel + that widget's
autofill/styled/palette info. Then it screenshots the window and a zoomed
corner. Writes scripts/_live_report.txt + scripts/_live_*.png.

Open a panel as a DOCK (left/bottom, pinned — not the center logbook) before
running, so there's a real dock to inspect.
"""

from __future__ import annotations

import io

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication.instance()
assert app is not None, "Run this inside the running Lightfall app (IPython panel)."

out = io.StringIO()

def w(*a):
    print(*a, file=out)

# Theme colors (for reference)
try:
    from lightfall.ui.theme.manager import ThemeManager
    c = ThemeManager.get_instance().colors
    w(f"theme effective surface={c.surface} sea={c.sea} bg={c.background}")
except Exception as e:
    w(f"(theme colors unavailable: {e})")

def info(widget):
    styled = widget.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    auto = widget.autoFillBackground()
    role = widget.backgroundRole()
    rn = role.name if hasattr(role, "name") else str(role)
    palc = widget.palette().color(role).name()
    return f"styled={int(styled)} auto={int(auto)} role={rn} palcol={palc}"

# Find visible dock widgets with real size
docks = [
    d for d in app.allWidgets()
    if type(d).__name__ == "PanelDockWidget"
    and d.isVisible() and d.width() > 20 and d.height() > 20
]
w(f"\nvisible dock panels: {[d.objectName() for d in docks]}")

for dock in docks:
    win = dock.window()
    img = win.grab().toImage()
    dtl = dock.mapTo(win, dock.rect().topLeft())
    dbr = dock.mapTo(win, dock.rect().bottomRight())
    w(f"\n==================== {dock.objectName()} ====================")
    w(f"window-rect: ({dtl.x()},{dtl.y()})..({dbr.x()},{dbr.y()})")

    w("--- subtree (class [name] :: paint) ---")
    def walk(widget, ind=0):
        w(f"{'  '*ind}{type(widget).__name__} [{widget.objectName() or '-'}] :: {info(widget)}")
        for ch in widget.children():
            if isinstance(ch, QWidget) and ch.isVisible():
                walk(ch, ind + 1)
    walk(dock)

    w("--- bottom-left corner: color + owning widget per offset ---")
    for off in range(0, 20, 2):
        x, y = dtl.x() + off, dbr.y() - 1 - off
        col = img.pixelColor(x, y).name()
        owner = dock.childAt(dock.mapFrom(win, QPoint(x, y)))
        oname = f"{type(owner).__name__}[{owner.objectName() or '-'}]" if owner else "None(dock bg)"
        w(f"  off={off:2d} color={col} owner={oname}")

    safe = dock.objectName().replace(".", "_").replace("/", "_")
    win.grab().save(f"scripts/_live_{safe}_full.png")
    win.grab().copy(dtl.x(), dbr.y() - 50, 50, 50).scaled(350, 350).save(
        f"scripts/_live_{safe}_corner.png"
    )
    w(f"saved scripts/_live_{safe}_full.png + _corner.png")

with open("scripts/_live_report.txt", "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print(out.getvalue())
print("=> wrote scripts/_live_report.txt and screenshots in scripts/")
