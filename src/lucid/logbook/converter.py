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

from lucid.logbook.style import get_qt_html_stylesheet
from lucid.logbook.visual_protection import PROTECTED_START, PROTECTED_END


# Pattern to match protected region markers
PROTECTED_START_PATTERN = re.compile(r"<!--\s*PROTECTED:(\S+)\s*-->")
PROTECTED_END_PATTERN = re.compile(r"<!--\s*/PROTECTED:(\S+)\s*-->")

# Pattern to match action group metadata (strip the whole comment)
# Note: timestamps contain colons, so we match until the closing -->
ACTION_GROUP_METADATA_PATTERN = re.compile(
    r"<!--\s*ACTION_GROUP:[^>]*-->"
)

# Pattern to match run metadata
RUN_METADATA_PATTERN = re.compile(
    r"<!--\s*RUN:[^>]*-->"
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
        """Render plain text.

        Note: Protected region markers are handled in post-processing
        (_inject_protection_markers) since mistune passes HTML comments
        through directly without calling this method.
        """
        return html.escape(text)

    def html_block(self, text: str) -> str:
        """Render raw HTML block."""
        # For HTML blocks, pass through (but escape for safety in Qt)
        return f"<p>{html.escape(text)}</p>\n"

    def raw_html(self, text: str) -> str:
        """Handle inline raw HTML."""
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

    def markdown_to_html(self, markdown: str) -> str:
        """
        Convert markdown to Qt-compatible HTML.

        Protected region markers are converted to spans with zero-width
        Unicode markers for precise cursor position tracking.

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

            # Parse markdown to HTML
            body = self._parser(markdown)

            # Post-process: Convert HTML comment markers to spans with zero-width markers
            # This handles the case where mistune passes through HTML comments directly
            body = self._inject_protection_markers(body)

            # Wrap in a complete HTML document with stylesheet
            stylesheet = get_qt_html_stylesheet()
            # Normalize whitespace: remove newlines between tags to avoid extra spaces
            # in rendered text. Qt interprets whitespace between block elements as spaces.
            body = body.strip()
            body = re.sub(r">\s+<", "><", body)
            html_doc = f"""<!DOCTYPE html>
<html>
<head>
<style>
{stylesheet}
</style>
</head>
<body>{body}</body>
</html>"""
            return html_doc

        except Exception as e:
            logger.error(f"Error converting markdown to HTML: {e}")
            # Return escaped plain text as fallback
            return f"<pre>{html.escape(markdown)}</pre>"

    def _inject_protection_markers(self, html_body: str) -> str:
        """
        Post-process HTML to inject zero-width markers at protected region boundaries.

        Converts HTML comment markers like:
            <!-- PROTECTED:id -->content<!-- /PROTECTED:id -->
        To:
            <span class="protected" data-region="id">\u200Bcontent\u200C</span>

        Args:
            html_body: The HTML body from mistune parsing.

        Returns:
            HTML with protection markers converted to spans with zero-width chars.
        """
        result = html_body

        # Pattern to match the full protected region including the markers
        # Handles both inline and block-level markers
        full_region_pattern = re.compile(
            r"<!--\s*PROTECTED:(\S+)\s*-->"  # Opening marker
            r"(.*?)"  # Content (non-greedy)
            r"<!--\s*/PROTECTED:\1\s*-->",  # Closing marker (backreference)
            re.DOTALL,
        )

        def replace_region(match: re.Match) -> str:
            region_id = match.group(1)
            content = match.group(2)

            # Strip metadata comments if present
            content = ACTION_GROUP_METADATA_PATTERN.sub("", content)
            content = RUN_METADATA_PATTERN.sub("", content)

            # Check if content looks like raw markdown (not already HTML)
            # If it has markdown formatting markers, parse it
            if "**" in content or "*" in content or "[" in content:
                # Parse the content as markdown to convert to HTML
                # Use the same parser instance
                content_html = self._parser(content.strip())
                # Remove wrapping <p> tags if present for inline display
                content_html = re.sub(r"^<p>(.*)</p>\s*$", r"\1", content_html.strip(), flags=re.DOTALL)
                content = content_html

            # Determine CSS class - system entries (actions, runs) get highlighted styling
            is_system_entry = region_id.startswith("action-") or region_id.startswith("run-")
            css_class = "system-entry protected" if is_system_entry else "protected"

            # Wrap with span and inject zero-width markers
            return (
                f'<span class="{css_class}" data-region="{region_id}">'
                f"{PROTECTED_START}{content}{PROTECTED_END}"
                f"</span>"
            )

        result = full_region_pattern.sub(replace_region, result)

        return result
