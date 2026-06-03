"""
Markdown to HTML renderer for the Claude assistant chat widget.

Uses mistune for parsing and optionally Pygments for syntax highlighting.
Output is Qt-compatible HTML suitable for QTextEdit.
"""

from __future__ import annotations

import html as html_module


def _highlight_code(code: str, info: str | None = None) -> str:
    """Syntax-highlight a code block using Pygments if available."""
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name, guess_lexer
        from pygments.util import ClassNotFound

        lang = info or ""
        try:
            lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
        except ClassNotFound:
            lexer = get_lexer_by_name("text")

        # Detect dark theme
        dark = _is_dark_theme()
        style = "monokai" if dark else "default"
        formatter = HtmlFormatter(noclasses=True, nowrap=False, style=style)
        return highlight(code, lexer, formatter)
    except ImportError:
        escaped = html_module.escape(code)
        return f"<pre><code>{escaped}</code></pre>"


def _is_dark_theme() -> bool:
    """Detect if the application is using a dark theme."""
    try:
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return False
        palette = app.palette()
        bg = palette.color(QPalette.ColorRole.Window)
        luminance = 0.299 * bg.redF() + 0.587 * bg.greenF() + 0.114 * bg.blueF()
        return luminance < 0.5
    except Exception:
        return False


# Module-level renderer singleton
_md_renderer = None


def _get_renderer():
    global _md_renderer
    if _md_renderer is None:
        import mistune

        _md_renderer = mistune.create_markdown(
            plugins=["strikethrough", "table"],
        )
    return _md_renderer


def render_markdown(text: str) -> str:
    """
    Render markdown text to Qt-compatible HTML.

    Args:
        text: Raw markdown string.

    Returns:
        HTML string suitable for QTextEdit/QLabel.
    """
    try:
        md = _get_renderer()
        result = md(text)

        # Post-process code blocks with syntax highlighting
        # mistune outputs <pre><code class="language-X">...</code></pre>
        # We replace these with Pygments-highlighted versions
        import re

        def _replace_code_block(match):
            lang = match.group(1) or ""
            code = html_module.unescape(match.group(2))
            return _highlight_code(code, lang if lang else None)

        result = re.sub(
            r'<pre><code(?:\s+class="language-(\w+)")?>(.*?)</code></pre>',
            _replace_code_block,
            result,
            flags=re.DOTALL,
        )

        return result
    except Exception:
        # Fallback: escape and preserve newlines
        return html_module.escape(text).replace("\n", "<br>")
