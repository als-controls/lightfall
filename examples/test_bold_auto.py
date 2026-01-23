#!/usr/bin/env python3
"""Automated test for bold formatting across paragraphs."""

import sys
from loguru import logger
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor, QTextDocument

# Configure logging
logger.remove()
logger.add(sys.stderr, level="DEBUG")

from ncs.logbook.block_mapper import BlockMapper
from ncs.logbook.converter import MarkdownConverter
from ncs.logbook.protection import ProtectionManager
from ncs.logbook.editors.richtext_editor import RichTextEditor


def test_bold_paragraphs():
    """Test bolding each paragraph in a 3-paragraph document."""
    app = QApplication(sys.argv)

    # Create editor
    protection = ProtectionManager()
    converter = MarkdownConverter()
    editor = RichTextEditor(protection, converter)

    # Initial content: 3 paragraphs
    initial_md = "Test\n\nTest\n\nTest"
    print(f"=== INITIAL ===")
    print(f"Markdown: {repr(initial_md)}")

    editor.render_markdown(initial_md)

    print(f"Visual: {repr(editor.toPlainText())}")
    print(f"\nBlock mappings:")
    for m in editor._block_mapper.mappings:
        print(f"  Block {m.visual_block_num}: md_chars {m.md_char_start}-{m.md_char_end}, "
              f"text='{m.visual_text}'")

    # Bold each paragraph one at a time
    for para_idx in range(3):
        print(f"\n=== BOLD PARAGRAPH {para_idx + 1} ===")

        # Refresh markdown
        current_md = editor.get_markdown()
        print(f"Before: {repr(current_md)}")

        # Find the block
        doc = editor.document()
        block = doc.findBlockByNumber(para_idx)
        if not block.isValid():
            print(f"Block {para_idx} not found!")
            continue

        # Select the entire block
        cursor = editor.textCursor()
        cursor.setPosition(block.position())
        cursor.setPosition(block.position() + len(block.text()), QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)

        print(f"Selection: block {para_idx}, pos {block.position()}, len {len(block.text())}")
        print(f"Selected text: '{cursor.selectedText()}'")

        # Get block-relative coords
        start_cursor = QTextCursor(cursor)
        start_cursor.setPosition(cursor.selectionStart())
        end_cursor = QTextCursor(cursor)
        end_cursor.setPosition(cursor.selectionEnd())

        start_block = start_cursor.blockNumber()
        start_offset = start_cursor.positionInBlock()
        end_block = end_cursor.blockNumber()
        end_offset = end_cursor.positionInBlock()

        print(f"Block coords: ({start_block},{start_offset}) to ({end_block},{end_offset})")

        # Get markdown positions
        md_start = editor._block_mapper.visual_to_md_pos(start_block, start_offset)
        md_end = editor._block_mapper.visual_to_md_pos(end_block, end_offset)
        print(f"Markdown positions: {md_start} to {md_end}")

        if md_start < len(current_md) and md_end <= len(current_md):
            print(f"MD text at [{md_start}:{md_end}]: '{current_md[md_start:md_end]}'")

        # Apply bold
        editor.set_bold(True)

        # Show result
        new_md = editor.get_markdown()
        new_visual = editor.toPlainText()
        print(f"After markdown: {repr(new_md)}")
        print(f"After visual: {repr(new_visual)}")

        # Check if bold was applied correctly
        expected_md = current_md[:md_start] + "**" + current_md[md_start:md_end] + "**" + current_md[md_end:]
        if new_md == expected_md:
            print("PASS: Bold applied correctly!")
        else:
            print(f"FAIL: Expected {repr(expected_md)}")

    print("\n=== FINAL STATE ===")
    print(f"Markdown: {repr(editor.get_markdown())}")
    print(f"Visual: {repr(editor.toPlainText())}")


if __name__ == "__main__":
    test_bold_paragraphs()
