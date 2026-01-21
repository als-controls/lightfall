"""
Markdown to HTML converter for the logbook widget.

Provides bidirectional conversion between markdown and Qt-compatible HTML,
while preserving protected region markers.
"""

from __future__ import annotations

import html
import re
from typing import Any

import markdownify
import mistune
from loguru import logger

from ncs.logbook.style import get_qt_html_stylesheet


# Pattern to match protected region markers
PROTECTED_START_PATTERN = re.compile(r"<!--\s*PROTECTED:(\S+)\s*-->")
PROTECTED_END_PATTERN = re.compile(r"<!--\s*/PROTECTED:(\S+)\s*-->")


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

    def text(self, text: str) -> str:
        """Render plain text, checking for protected markers."""
        # Check for protected start marker
        start_match = PROTECTED_START_PATTERN.search(text)
        if start_match:
            self._in_protected = True
            self._protected_id = start_match.group(1)
            # Remove the marker from output but add a span
            text = PROTECTED_START_PATTERN.sub("", text)
            return f'<span class="protected" data-region="{self._protected_id}">{html.escape(text)}'

        # Check for protected end marker
        end_match = PROTECTED_END_PATTERN.search(text)
        if end_match:
            self._in_protected = False
            self._protected_id = None
            text = PROTECTED_END_PATTERN.sub("", text)
            return f"{html.escape(text)}</span>"

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
    Bidirectional markdown to HTML converter for Qt widgets.

    This class provides conversion between markdown and Qt-compatible HTML,
    preserving protected region markers through round-trips.

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

    def html_to_markdown(self, html_content: str) -> str:
        """
        Convert Qt HTML back to markdown.

        This is inherently lossy but preserves the basic structure.
        Protected regions are restored with their markers.

        Args:
            html_content: The HTML content from QTextEdit.

        Returns:
            Markdown string.
        """
        try:
            return self._convert_html_to_markdown(html_content)
        except Exception as e:
            logger.error(f"Error converting HTML to markdown: {e}")
            # Strip all HTML tags as fallback
            return re.sub(r"<[^>]+>", "", html_content)

    def _convert_html_to_markdown(self, html_content: str) -> str:
        """
        Internal HTML to markdown conversion.

        Pre-processes Qt's inline styles to semantic tags, then uses
        markdownify library for conversion.
        """
        text = html_content

        # Extract body content if wrapped in full document
        body_match = re.search(
            r"<body[^>]*>(.*?)</body>", text, re.DOTALL | re.IGNORECASE
        )
        if body_match:
            text = body_match.group(1)

        # Extract protected regions before conversion and replace with placeholders
        protected_regions: dict[str, tuple[str, str]] = {}
        placeholder_counter = 0

        def extract_protected(match: re.Match) -> str:
            nonlocal placeholder_counter
            region_id = match.group(1)
            content = match.group(2)
            placeholder = f"__PROTECTED_{placeholder_counter}__"
            placeholder_counter += 1
            # Store both the region_id and the content to convert
            protected_regions[placeholder] = (region_id, content)
            return placeholder

        # Match protected spans - handle both data-region orders
        text = re.sub(
            r'<span[^>]*class="protected"[^>]*data-region="([^"]+)"[^>]*>(.*?)</span>',
            extract_protected,
            text,
            flags=re.DOTALL,
        )
        text = re.sub(
            r'<span[^>]*data-region="([^"]+)"[^>]*class="protected"[^>]*>(.*?)</span>',
            extract_protected,
            text,
            flags=re.DOTALL,
        )

        # Pre-process Qt's inline styles to semantic HTML tags
        text = self._convert_qt_styles_to_semantic(text)

        # Use markdownify to convert HTML to markdown
        markdown = markdownify.markdownify(
            text,
            heading_style="ATX",  # Use # style headings
            bullets="-",  # Use - for unordered lists
            strip=["script"],  # Strip script tags
        )

        # Restore protected regions with their markers
        for placeholder, (region_id, content) in protected_regions.items():
            # Convert the protected content too
            processed_content = self._convert_qt_styles_to_semantic(content)
            protected_md = markdownify.markdownify(
                processed_content,
                heading_style="ATX",
                bullets="-",
                strip=["script"],
            ).strip()
            restored = (
                f"<!-- PROTECTED:{region_id} -->\n"
                f"{protected_md}\n"
                f"<!-- /PROTECTED:{region_id} -->"
            )
            markdown = markdown.replace(placeholder, restored)

        # Clean up whitespace
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        markdown = markdown.strip()

        return markdown

    def _convert_qt_styles_to_semantic(self, html_content: str) -> str:
        """
        Convert Qt's inline style spans to semantic HTML tags.

        Qt's QTextEdit outputs formatting as inline styles like:
        - font-weight:600 or font-weight:bold -> <strong>
        - font-style:italic -> <em>
        - text-decoration:line-through -> <del>

        Args:
            html_content: HTML with Qt inline styles.

        Returns:
            HTML with semantic tags.
        """
        text = html_content

        # Convert bold spans: font-weight:600, font-weight:700, font-weight:bold
        def convert_bold(match: re.Match) -> str:
            style = match.group(1)
            content = match.group(2)
            # Check if this span has bold styling
            if re.search(r"font-weight\s*:\s*(bold|[6-9]\d{2})", style, re.IGNORECASE):
                return f"<strong>{content}</strong>"
            return match.group(0)

        text = re.sub(
            r'<span[^>]*style="([^"]*)"[^>]*>(.*?)</span>',
            convert_bold,
            text,
            flags=re.DOTALL,
        )

        # Convert italic spans
        def convert_italic(match: re.Match) -> str:
            style = match.group(1)
            content = match.group(2)
            if re.search(r"font-style\s*:\s*italic", style, re.IGNORECASE):
                return f"<em>{content}</em>"
            return match.group(0)

        text = re.sub(
            r'<span[^>]*style="([^"]*)"[^>]*>(.*?)</span>',
            convert_italic,
            text,
            flags=re.DOTALL,
        )

        # Convert strikethrough spans
        def convert_strike(match: re.Match) -> str:
            style = match.group(1)
            content = match.group(2)
            if re.search(r"text-decoration\s*:[^;]*line-through", style, re.IGNORECASE):
                return f"<del>{content}</del>"
            return match.group(0)

        text = re.sub(
            r'<span[^>]*style="([^"]*)"[^>]*>(.*?)</span>',
            convert_strike,
            text,
            flags=re.DOTALL,
        )

        return text
