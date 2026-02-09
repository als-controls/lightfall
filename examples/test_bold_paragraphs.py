#!/usr/bin/env python3
"""Test script to debug bold formatting across paragraphs."""

import sys

from loguru import logger
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

# Configure logging
logger.remove()
logger.add(sys.stderr, level="DEBUG")

from lucid.logbook import LogbookWidget  # noqa: E402


class TestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bold Paragraph Test")
        self.setMinimumSize(800, 600)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Instructions
        self.label = QLabel("1. Type 'Test' in 3 paragraphs\n2. Click buttons to bold each")
        layout.addWidget(self.label)

        # Logbook widget
        self.logbook = LogbookWidget()
        self.logbook.set_content("Test\n\nTest\n\nTest")
        layout.addWidget(self.logbook)

        # Test buttons
        for i in range(3):
            btn = QPushButton(f"Bold Paragraph {i+1}")
            btn.clicked.connect(lambda checked, idx=i: self.bold_paragraph(idx))
            layout.addWidget(btn)

        # Show state button
        show_btn = QPushButton("Show State")
        show_btn.clicked.connect(self.show_state)
        layout.addWidget(show_btn)

        self.show_state()

    def bold_paragraph(self, para_idx: int):
        """Select and bold the specified paragraph."""
        editor = self.logbook._rich_editor

        # Get the document
        doc = editor.document()

        # Find the block
        block = doc.findBlockByNumber(para_idx)
        if not block.isValid():
            print(f"Block {para_idx} not found!")
            return

        # Create selection for the entire block
        cursor = editor.textCursor()
        cursor.setPosition(block.position())
        cursor.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)

        print(f"\n=== BOLDING PARAGRAPH {para_idx + 1} ===")
        print(f"Block {para_idx}: position={block.position()}, length={block.length()}, text='{block.text()}'")
        print(f"Selection: {cursor.selectionStart()} to {cursor.selectionEnd()}")
        print(f"Selected text: '{cursor.selectedText()}'")

        # Get block-relative info
        start_cursor = QTextCursor(cursor)
        start_cursor.setPosition(cursor.selectionStart())
        end_cursor = QTextCursor(cursor)
        end_cursor.setPosition(cursor.selectionEnd())

        print(f"Start: block={start_cursor.blockNumber()}, offset={start_cursor.positionInBlock()}")
        print(f"End: block={end_cursor.blockNumber()}, offset={end_cursor.positionInBlock()}")

        # Show markdown position mapping
        mapper = editor._block_mapper
        md_start = mapper.visual_to_md_pos(start_cursor.blockNumber(), start_cursor.positionInBlock())
        md_end = mapper.visual_to_md_pos(end_cursor.blockNumber(), end_cursor.positionInBlock())
        print(f"Markdown positions: {md_start} to {md_end}")

        markdown = editor.get_markdown()
        print(f"Markdown before: {repr(markdown)}")
        if md_start < len(markdown) and md_end <= len(markdown):
            print(f"MD text at [{md_start}:{md_end}]: '{markdown[md_start:md_end]}'")

        # Apply bold
        editor.toggle_bold()

        self.show_state()

    def show_state(self):
        """Show current markdown and visual state."""
        editor = self.logbook._rich_editor
        markdown = editor.get_markdown()
        visual = editor.toPlainText()

        print("\n=== CURRENT STATE ===")
        print(f"Markdown ({len(markdown)} chars): {repr(markdown)}")
        print(f"Visual ({len(visual)} chars): {repr(visual)}")

        # Show block mappings
        print("\nBlock mappings:")
        for m in editor._block_mapper.mappings:
            print(f"  Block {m.visual_block_num}: md_line={m.md_line_start}, "
                  f"chars={m.md_char_start}-{m.md_char_end}, "
                  f"text='{m.visual_text[:20]}...'")


def main():
    app = QApplication(sys.argv)
    window = TestWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
