"""
Markdown to HTML converter for the logbook widget.

Provides bidirectional conversion between markdown and Qt-compatible HTML,
while preserving protected region markers.
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

        Uses regex-based transformation for the Qt HTML subset.
        """
        text = html_content

        # Extract body content if wrapped in full document
        body_match = re.search(r"<body[^>]*>(.*?)</body>", text, re.DOTALL | re.IGNORECASE)
        if body_match:
            text = body_match.group(1)

        # Process protected regions - restore markers
        def restore_protected(match: re.Match) -> str:
            region_id = match.group(1)
            content = match.group(2)
            # Recursively process content
            processed = self._convert_html_to_markdown(content)
            return f"<!-- PROTECTED:{region_id} -->{processed}<!-- /PROTECTED:{region_id} -->"

        text = re.sub(
            r'<span[^>]*class="protected"[^>]*data-region="([^"]+)"[^>]*>(.*?)</span>',
            restore_protected,
            text,
            flags=re.DOTALL,
        )

        # Headers
        for i in range(6, 0, -1):
            text = re.sub(
                rf"<h{i}[^>]*>(.*?)</h{i}>",
                lambda m, lvl=i: "#" * lvl + " " + m.group(1).strip() + "\n\n",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )

        # Bold
        text = re.sub(
            r"<strong[^>]*>(.*?)</strong>",
            r"**\1**",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r"<b[^>]*>(.*?)</b>",
            r"**\1**",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Italic
        text = re.sub(
            r"<em[^>]*>(.*?)</em>",
            r"*\1*",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r"<i[^>]*>(.*?)</i>",
            r"*\1*",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Strikethrough
        text = re.sub(
            r"<s[^>]*>(.*?)</s>",
            r"~~\1~~",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Inline code
        text = re.sub(
            r"<code[^>]*>(.*?)</code>",
            r"`\1`",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Code blocks
        def convert_pre(match: re.Match) -> str:
            content = match.group(1)
            # Remove inner code tags
            content = re.sub(r"</?code[^>]*>", "", content, flags=re.IGNORECASE)
            # Unescape HTML entities
            content = html.unescape(content)
            return f"\n```\n{content.strip()}\n```\n"

        text = re.sub(
            r"<pre[^>]*>(.*?)</pre>",
            convert_pre,
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Links
        text = re.sub(
            r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            r"[\2](\1)",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Images
        text = re.sub(
            r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*/?>',
            r"![\2](\1)",
            text,
            flags=re.IGNORECASE,
        )

        # Blockquotes
        def convert_blockquote(match: re.Match) -> str:
            content = match.group(1)
            lines = content.strip().split("\n")
            return "\n".join("> " + line for line in lines) + "\n\n"

        text = re.sub(
            r"<blockquote[^>]*>(.*?)</blockquote>",
            convert_blockquote,
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Unordered lists
        def convert_ul(match: re.Match) -> str:
            content = match.group(1)
            items = re.findall(r"<li[^>]*>(.*?)</li>", content, re.DOTALL | re.IGNORECASE)
            return "\n".join("- " + item.strip() for item in items) + "\n\n"

        text = re.sub(
            r"<ul[^>]*>(.*?)</ul>",
            convert_ul,
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Ordered lists
        def convert_ol(match: re.Match) -> str:
            content = match.group(1)
            items = re.findall(r"<li[^>]*>(.*?)</li>", content, re.DOTALL | re.IGNORECASE)
            return "\n".join(f"{i+1}. {item.strip()}" for i, item in enumerate(items)) + "\n\n"

        text = re.sub(
            r"<ol[^>]*>(.*?)</ol>",
            convert_ol,
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Tables
        def convert_table(match: re.Match) -> str:
            table_content = match.group(1)
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_content, re.DOTALL | re.IGNORECASE)
            if not rows:
                return ""

            md_rows = []
            header_row = None

            for i, row in enumerate(rows):
                # Check for header cells
                header_cells = re.findall(r"<th[^>]*>(.*?)</th>", row, re.DOTALL | re.IGNORECASE)
                data_cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)

                cells = header_cells if header_cells else data_cells
                if not cells:
                    continue

                # Clean cell content (strip tags and whitespace)
                cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                md_row = "| " + " | ".join(cleaned) + " |"
                md_rows.append(md_row)

                # Add separator after header row
                if header_cells and header_row is None:
                    header_row = i
                    separator = "| " + " | ".join(["---"] * len(cells)) + " |"
                    md_rows.append(separator)

            return "\n" + "\n".join(md_rows) + "\n\n" if md_rows else ""

        text = re.sub(
            r"<table[^>]*>(.*?)</table>",
            convert_table,
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Paragraphs
        text = re.sub(
            r"<p[^>]*>(.*?)</p>",
            r"\1\n\n",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Horizontal rules
        text = re.sub(r"<hr[^>]*/?>", "\n---\n", text, flags=re.IGNORECASE)

        # Line breaks
        text = re.sub(r"<br[^>]*/?>", "  \n", text, flags=re.IGNORECASE)

        # Remove remaining HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        # Unescape HTML entities
        text = html.unescape(text)

        # Clean up whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        return text
