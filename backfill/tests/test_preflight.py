"""Tests for backfill/preflight.py — PDF preflight checks."""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.split_pipeline.split1_preflight import detect_toc_page


class TestDetectTocPage:

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

    def test_contents_on_page_3(self):
        doc = self._make_mock_doc([
            "Cover page",
            "Blank page",
            "More info",
            "CONTENTS\nEditorial\t\n3\n",
            "Editorial text",
        ])
        assert detect_toc_page(doc) == 3

    def test_contents_on_first_page(self):
        doc = self._make_mock_doc([
            "CONTENTS\nStuff\n",
        ])
        assert detect_toc_page(doc) == 0

    def test_no_contents(self):
        doc = self._make_mock_doc([
            "Page one",
            "Page two",
            "Page three",
        ])
        assert detect_toc_page(doc) is None

    def test_contents_not_at_line_start(self):
        """CONTENTS must be at the start of a line (re.MULTILINE ^)."""
        doc = self._make_mock_doc([
            "Some text with CONTENTS in the middle",
        ])
        # Should not match because CONTENTS is not at line start
        assert detect_toc_page(doc) is None

    def test_contents_with_leading_text_on_same_line(self):
        """CONTENTS preceded by text on the same line should NOT match."""
        doc = self._make_mock_doc([
            "Table of CONTENTS\n",
        ])
        # "Table of CONTENTS" — CONTENTS is not at start of line
        assert detect_toc_page(doc) is None

    def test_contents_on_its_own_line(self):
        """CONTENTS on its own line within other text should match."""
        doc = self._make_mock_doc([
            "Header text\nCONTENTS\nEditorial\t\n3\n",
        ])
        assert detect_toc_page(doc) == 0

    def test_only_checks_first_10_pages(self):
        """TOC detection only checks first 10 pages."""
        pages = ["No contents"] * 15
        pages[12] = "CONTENTS\nStuff\n"  # On page 12 — out of range
        doc = self._make_mock_doc(pages)
        assert detect_toc_page(doc) is None
