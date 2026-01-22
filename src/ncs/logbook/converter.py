"""
Markdown to HTML converter for the logbook widget.

Provides one-way conversion from markdown to Qt-compatible HTML.
Since markdown is the source of truth, HTML→MD conversion is not needed.
"""

from __future__ import annotations

import html
import re
from typing import Any

import mistune
from loguru import logger

from ncs.logbook.style import get_qt_html_stylesheet


# Pattern to match protected region markers
PROTECTED_START_PATTERN = re.compile(r"<!--\s*PROTECTED:(\S+)\s*-->")
PROTECTED_END_PATTERN = re.compile(r"<!--\s*/PROTECTED:(\S+)\s*-->")

# Pattern to match action group metadata
ACTION_GROUP_METADATA_PATTERN = re.compile(
    r"<!--\s*ACTION_GROUP:"
    r"count=(\d+):"
    r"start=([^:]+):"
    r"end=([^:]+):"
    r"collapsed=(true|false)\s*-->"
)

# Pattern to match details/summary (for action groups)
DETAILS_PATTERN = re.compile(
    r"<details[^>]*>\s*<summary>(.+?)</summary>(.*?)</details>",
    re.DOTALL,
)


class QtHtmlRenderer(mistune.HTMLRenderer):
    """
    Custom mistune renderer that outputs Qt-compatible HTML.

    Qt's QTextEdit supports a limited HTML subset, so this renderer
    produces compatible output with inline styles where needed.
    """

    def __init__(self) -> None:
        super().__init__(escape=False)
        self._in_protected = False
        self._protected_id: str | None = None
        self._in_action_group = False
        self._action_group_collapsed = True
        self._action_group_count = 0

    def text(self, text: str) -> str:
        """Render plain text, checking for protected and action group markers."""
        result_parts = []
        remaining = text

        # Check for protected start marker
        start_match = PROTECTED_START_PATTERN.search(remaining)
        if start_match:
            self._in_protected = True
            self._protected_id = start_match.group(1)
            # Check if this is an action group (starts with "action-")
            is_action = self._protected_id.startswith("action-")
            css_class = "action-group protected" if is_action else "protected"
            # Remove the marker from output but add a span
            remaining = PROTECTED_START_PATTERN.sub("", remaining)
            result_parts.append(
                f'<span class="{css_class}" data-region="{self._protected_id}">'
            )

        # Check for action group metadata marker
        action_match = ACTION_GROUP_METADATA_PATTERN.search(remaining)
        if action_match:
            self._in_action_group = True
            self._action_group_count = int(action_match.group(1))
            self._action_group_collapsed = action_match.group(4) == "true"
            # Remove the metadata marker (it's just for parsing, not display)
            remaining = ACTION_GROUP_METADATA_PATTERN.sub("", remaining)

        # Check for protected end marker
        end_match = PROTECTED_END_PATTERN.search(remaining)
        if end_match:
            self._in_protected = False
            self._in_action_group = False
            self._protected_id = None
            remaining = PROTECTED_END_PATTERN.sub("", remaining)
            result_parts.append(html.escape(remaining))
            result_parts.append("</span>")
            return "".join(result_parts)

        result_parts.append(html.escape(remaining))
        return "".join(result_parts)

    def html_block(self, text: str) -> str:
        """Render raw HTML block, handling details/summary for action groups."""
        # Check for details/summary pattern (action groups)
        details_match = DETAILS_PATTERN.search(text)
        if details_match:
            summary_content = details_match.group(1)
            details_content = details_match.group(2)

            # Render as a clickable summary with expand indicator
            # The full content is in a data attribute for the dialog
            region_id = self._protected_id or ""
            return (
                f'<div class="action-group-summary" data-region="{region_id}" '
                f'data-collapsed="true">'
                f'<span class="expand-icon">[+]</span> {summary_content}'
                f'</div>\n'
            )

        # For other HTML blocks, pass through (but escape for safety in Qt)
        return f"<p>{html.escape(text)}</p>\n"

    def raw_html(self, text: str) -> str:
        """Handle inline raw HTML."""
        # Check for details/summary
        if "<details" in text or "</details>" in text:
            # Will be handled by html_block
            return ""
        if "<summary" in text or "</summary>" in text:
            return ""
        return html.escape(text)

    def paragraph(self, text: str) -> str:
        """Render a paragraph."""
        return f"<p>{text}</p>\n"

    def heading(self, text: str, level: int, **attrs: Any) -> str:
        """Render a heading."""
        return f"<h{level}>{text}</h{level}>\n"

    def thematic_break(self) -> str:
        """Render a horizontal rule."""
        return "<hr>\n"

    def block_quote(self, text: str) -> str:
        """Render a blockquote."""
        return f"<blockquote>{text}</blockquote>\n"

    def list(self, text: str, ordered: bool, **attrs: Any) -> str:
        """Render a list."""
        tag = "ol" if ordered else "ul"
        return f"<{tag}>\n{text}</{tag}>\n"

    def list_item(self, text: str, **attrs: Any) -> str:
        """Render a list item."""
        return f"<li>{text}</li>\n"

    def codespan(self, text: str) -> str:
        """Render inline code."""
        return f"<code>{html.escape(text)}</code>"

    def block_code(self, code: str, info: str | None = None) -> str:
        """Render a code block."""
        escaped = html.escape(code)
        if info:
            return f'<pre><code class="language-{info}">{escaped}</code></pre>\n'
        return f"<pre><code>{escaped}</code></pre>\n"

    def emphasis(self, text: str) -> str:
        """Render emphasized (italic) text."""
        return f"<em>{text}</em>"

    def strong(self, text: str) -> str:
        """Render strong (bold) text."""
        return f"<strong>{text}</strong>"

    def strikethrough(self, text: str) -> str:
        """Render strikethrough text."""
        return f"<s>{text}</s>"

    def link(self, text: str, url: str, title: str | None = None) -> str:
        """Render a link."""
        title_attr = f' title="{html.escape(title)}"' if title else ""
        return f'<a href="{html.escape(url)}"{title_attr}>{text}</a>'

    def image(self, alt: str, url: str, title: str | None = None) -> str:
        """Render an image."""
        title_attr = f' title="{html.escape(title)}"' if title else ""
        return f'<img src="{html.escape(url)}" alt="{html.escape(alt)}"{title_attr}>'

    def linebreak(self) -> str:
        """Render a line break."""
        return "<br>\n"

    def softbreak(self) -> str:
        """Render a soft break (typically just a space)."""
        return "\n"

    def blank_line(self) -> str:
        """Render a blank line."""
        return ""


class MarkdownConverter:
    """
    Markdown to HTML converter for Qt widgets.

    This class provides one-way conversion from markdown to Qt-compatible HTML.
    Since markdown is the source of truth for the logbook editor, HTML→MD
    conversion is not needed.

    Example:
        >>> converter = MarkdownConverter()
        >>> html = converter.markdown_to_html("# Hello\\n\\nWorld")
        >>> print(html)
        <h1>Hello</h1>
        <p>World</p>
    """

    def __init__(self) -> None:
        """Initialize the converter with a custom renderer."""
        self._renderer = QtHtmlRenderer()
        self._parser = mistune.create_markdown(
            renderer=self._renderer,
            plugins=["strikethrough", "table"],
        )

    def _preprocess_details(self, markdown: str) -> str:
        """
        Convert <details>/<summary> blocks to Qt-compatible HTML.

        Qt's QTextEdit doesn't support the <details> element, so we convert
        it to a styled div with a [+] indicator that can be clicked to show
        a dialog with the full content.

        Args:
            markdown: The markdown content.

        Returns:
            Markdown with <details> replaced by styled divs.
        """
        # Find the protected region ID for the current action group
        region_match = PROTECTED_START_PATTERN.search(markdown)
        region_id = region_match.group(1) if region_match else ""

        def replace_details(match: re.Match) -> str:
            summary = match.group(1)
            # Clean up the summary - remove markdown bold markers for display
            clean_summary = summary.replace("**", "")
            return (
                f'<div class="action-group-summary" data-region="{region_id}">'
                f'<span class="expand-icon">[+]</span> <b>{clean_summary}</b>'
                f'</div>'
            )

        return DETAILS_PATTERN.sub(replace_details, markdown)

    def markdown_to_html(self, markdown: str) -> str:
        """
        Convert markdown to Qt-compatible HTML.

        Protected region markers are preserved as span elements with
        class="protected" and data-region attributes.

        Args:
            markdown: The markdown content to convert.

        Returns:
            HTML string suitable for QTextEdit.
        """
        try:
            # Reset renderer state
            self._renderer._in_protected = False
            self._renderer._protected_id = None
            self._renderer._in_action_group = False
            self._renderer._action_group_collapsed = True
            self._renderer._action_group_count = 0

            # Preprocess: Convert <details>/<summary> to Qt-compatible HTML
            # Qt doesn't support <details>, so we convert to a clickable summary div
            markdown = self._preprocess_details(markdown)

            # Parse markdown to HTML
            body = self._parser(markdown)

            # Wrap in a complete HTML document with stylesheet
            stylesheet = get_qt_html_stylesheet()
            html_doc = f"""<!DOCTYPE html>
<html>
<head>
<style>
{stylesheet}
</style>
</head>
<body>
{body}
</body>
</html>"""
            return html_doc

        except Exception as e:
            logger.error(f"Error converting markdown to HTML: {e}")
            # Return escaped plain text as fallback
            return f"<pre>{html.escape(markdown)}</pre>"
