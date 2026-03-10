"""Tests for backfill/parse_toc.py — TOC parsing logic."""

import re
from unittest.mock import MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.parse_toc import (
    parse_toc_text,
    classify_entry,
    find_toc_page,
    find_page_offset,
    SECTION_EDITORIAL,
    SECTION_ARTICLES,
    SECTION_BOOK_REVIEW_EDITORIAL,
)


class TestParseTocText:
    """Test parse_toc_text() with synthetic PyMuPDF-style output."""

    def test_simple_entries(self):
        """Basic entries: title with tab, then page number on next line."""
        toc_text = (
            "CONTENTS\n"
            "Editorial\t\n"
            "3\n"
            "Some Article Title\t\n"
            "7\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 2
        assert entries[0]['title'] == 'Editorial'
        assert entries[0]['page'] == 3
        assert entries[0]['author'] is None
        assert entries[1]['title'] == 'Some Article Title'
        assert entries[1]['page'] == 7

    def test_entry_with_author(self):
        """Entry followed by author name (no tab, not a number)."""
        toc_text = (
            "CONTENTS\n"
            "Therapy for the Revolution\t\n"
            "7\n"
            "Kim Loliya\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        assert entries[0]['title'] == 'Therapy for the Revolution'
        assert entries[0]['page'] == 7
        assert entries[0]['author'] == 'Kim Loliya'

    def test_multi_line_title(self):
        """Title spans multiple tab-lines before the page number."""
        toc_text = (
            "CONTENTS\n"
            "A Very Long Title That\t\n"
            "Spans Two Lines\t\n"
            "15\n"
            "Jane Smith\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        assert entries[0]['title'] == 'A Very Long Title That Spans Two Lines'
        assert entries[0]['page'] == 15
        assert entries[0]['author'] == 'Jane Smith'

    def test_title_overflow_after_page(self):
        """Title overflow text after page number, then author."""
        toc_text = (
            "CONTENTS\n"
            "Main Title Part\t\n"
            "26\n"
            "overflow part\n"
            "John Doe\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        # 'overflow part' is title overflow, 'John Doe' is author
        assert entries[0]['title'] == 'Main Title Part overflow part'
        assert entries[0]['author'] == 'John Doe'
        assert entries[0]['page'] == 26

    def test_multiple_entries(self):
        """Multiple entries in sequence."""
        toc_text = (
            "CONTENTS\n"
            "Editorial\t\n"
            "3\n"
            "First Article\t\n"
            "7\n"
            "Author One\n"
            "Second Article\t\n"
            "22\n"
            "Author Two\n"
            "Book Reviews\t\n"
            "45\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 4
        assert entries[0]['title'] == 'Editorial'
        assert entries[1]['author'] == 'Author One'
        assert entries[2]['author'] == 'Author Two'
        assert entries[3]['title'] == 'Book Reviews'
        assert entries[3]['page'] == 45

    def test_no_contents_heading(self):
        """Returns empty list when CONTENTS heading is missing."""
        toc_text = "Some random text\nNo TOC here\n"
        entries = parse_toc_text(toc_text)
        assert entries == []

    def test_blank_lines_ignored(self):
        """Blank lines between entries are skipped."""
        toc_text = (
            "CONTENTS\n"
            "\n"
            "Editorial\t\n"
            "\n"
            "3\n"
            "\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        assert entries[0]['title'] == 'Editorial'

    def test_author_with_ampersand_treated_as_single_author_field(self):
        """Author field with & is stored as-is (splitting is done elsewhere)."""
        toc_text = (
            "CONTENTS\n"
            "Collaborative Work\t\n"
            "10\n"
            "Alice Brown & Bob White\n"
        )
        entries = parse_toc_text(toc_text)
        assert entries[0]['author'] == 'Alice Brown & Bob White'


class TestClassifyEntry:
    """Test classify_entry() section assignment."""

    def test_editorial(self):
        assert classify_entry('Editorial') == SECTION_EDITORIAL

    def test_editorial_case_insensitive(self):
        assert classify_entry('editorial') == SECTION_EDITORIAL
        assert classify_entry('EDITORIAL') == SECTION_EDITORIAL

    def test_book_reviews(self):
        assert classify_entry('Book Reviews') == SECTION_BOOK_REVIEW_EDITORIAL

    def test_articles(self):
        """Anything not 'editorial' or 'book reviews' is classified as Articles."""
        assert classify_entry('Therapy for the Revolution') == SECTION_ARTICLES
        assert classify_entry('Some Research Paper') == SECTION_ARTICLES

    def test_book_reviews_case(self):
        assert classify_entry('book reviews') == SECTION_BOOK_REVIEW_EDITORIAL
        assert classify_entry('BOOK REVIEWS') == SECTION_BOOK_REVIEW_EDITORIAL


class TestFindTocPage:
    """Test find_toc_page() with mock fitz doc."""

    def _make_mock_doc(self, pages_text):
        """Create a mock PyMuPDF doc with given page texts."""
        doc = MagicMock()
        doc.__len__ = MagicMock(return_value=len(pages_text))
        mock_pages = []
        for text in pages_text:
            page = MagicMock()
            page.get_text.return_value = text
            mock_pages.append(page)
        doc.__getitem__ = MagicMock(side_effect=lambda i: mock_pages[i])
        return doc

    def test_toc_on_page_2(self):
        doc = self._make_mock_doc([
            "Cover page text",
            "Some other page",
            "CONTENTS\nEditorial\t\n3\n",
            "Article text",
        ])
        assert find_toc_page(doc) == 2

    def test_toc_not_found(self):
        doc = self._make_mock_doc([
            "Cover page",
            "No table of contents here",
            "Just text",
        ])
        assert find_toc_page(doc) is None

    def test_toc_on_first_page(self):
        doc = self._make_mock_doc([
            "CONTENTS\nEditorial\t\n3\n",
        ])
        assert find_toc_page(doc) == 0


class TestFindPageOffset:
    """Test find_page_offset() — editorial detection strategy."""

    def _make_mock_doc(self, pages_text):
        doc = MagicMock()
        doc.__len__ = MagicMock(return_value=len(pages_text))
        mock_pages = []
        for text in pages_text:
            page = MagicMock()
            page.get_text.return_value = text
            mock_pages.append(page)
        doc.__getitem__ = MagicMock(side_effect=lambda i: mock_pages[i])
        return doc

    def test_editorial_on_expected_page(self):
        """EDITORIAL on pdf page 4 => journal page 3, offset = 4 - 3 = 1."""
        pages = [
            "Cover",              # 0
            "Inside cover",       # 1
            "CONTENTS\n...",      # 2 (toc)
            "Ad page",           # 3
            "EDITORIAL\nSome editorial text...",  # 4 (journal page 3)
            "More content",      # 5
        ]
        doc = self._make_mock_doc(pages)
        offset = find_page_offset(doc, toc_page_idx=2)
        assert offset == 1  # pdf_index 4 - journal_page 3 = 1

    def test_fallback_to_printed_page_number(self):
        """When no EDITORIAL found, falls back to printed page numbers."""
        pages = [
            "Cover",              # 0
            "CONTENTS\n...",      # 1 (toc)
            "No editorial here",  # 2
            "5\tSome article starting on journal page 5",  # 3
            "More content",       # 4
        ]
        doc = self._make_mock_doc(pages)
        offset = find_page_offset(doc, toc_page_idx=1)
        # pdf_index 3 - printed_page 5 = -2
        assert offset == -2

    def test_fallback_default(self):
        """When nothing found, default to toc_page_idx - 1."""
        pages = [
            "Cover",
            "CONTENTS\n...",
            "Plain text no markers",
            "More plain text",
        ]
        doc = self._make_mock_doc(pages)
        offset = find_page_offset(doc, toc_page_idx=1)
        assert offset == 0  # toc_page_idx(1) - 1 = 0
