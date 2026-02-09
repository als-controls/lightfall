#!/usr/bin/env python3
"""Test script to debug position mapping between visual and markdown."""

import sys

from loguru import logger
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication

# Configure logging
logger.remove()
logger.add(sys.stderr, level="DEBUG")

from lucid.logbook.block_mapper import BlockMapper  # noqa: E402
from lucid.logbook.converter import MarkdownConverter  # noqa: E402

# Simple test markdown with multiple paragraphs
TEST_MARKDOWN = """\
First paragraph here.

Second paragraph here.

Third paragraph here.

Fourth paragraph here.
"""

def main():
    _app = QApplication(sys.argv)  # noqa: F841

    # Convert markdown to HTML
    converter = MarkdownConverter()
    html = converter.markdown_to_html(TEST_MARKDOWN)

    # Create a QTextDocument and load HTML
    doc = QTextDocument()
    doc.setHtml(html)

    print("\n=== MARKDOWN SOURCE ===")
    print(repr(TEST_MARKDOWN))
    print(f"Length: {len(TEST_MARKDOWN)}")

    print("\n=== VISUAL PLAIN TEXT ===")
    visual_text = doc.toPlainText()
    print(repr(visual_text))
    print(f"Length: {len(visual_text)}")

    print("\n=== MARKDOWN LINES ===")
    for i, line in enumerate(TEST_MARKDOWN.split('\n')):
        # Calculate offset of this line
        offset = sum(len(ln) + 1 for ln in TEST_MARKDOWN.split('\n')[:i])
        print(f"  Line {i}: offset={offset}, content='{line}'")

    print("\n=== VISUAL BLOCKS ===")
    block = doc.begin()
    while block.isValid():
        print(f"  Block {block.blockNumber()}: '{block.text()}'")
        block = block.next()

    print("\n=== BUILD MAPPINGS ===")
    mapper = BlockMapper()
    mapper.build_mappings(TEST_MARKDOWN, doc)

    print("\n=== POSITION MAPPING TEST ===")
    # Test position at start of each paragraph
    # In visual, each block is a paragraph
    block = doc.begin()
    while block.isValid():
        block_num = block.blockNumber()
        block_text = block.text()

        # Test offset 0 (start of block) and offset 5 (somewhere in block)
        for offset in [0, 5]:
            if offset < len(block_text):
                md_pos = mapper.visual_to_md_pos(block_num, offset)

                # What character is at this position in markdown?
                md_char = TEST_MARKDOWN[md_pos] if md_pos < len(TEST_MARKDOWN) else "<END>"

                # What character should it be (from visual)?
                vis_char = block_text[offset] if offset < len(block_text) else "<END>"

                match = "OK" if md_char == vis_char else "MISMATCH"
                print(f"  Block {block_num}, offset {offset}: md_pos={md_pos}, "
                      f"md_char='{md_char}', vis_char='{vis_char}' {match}")

        block = block.next()

    print("\n=== DONE ===")

if __name__ == "__main__":
    main()
