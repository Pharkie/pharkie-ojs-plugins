"""Tests for backfill/verify_split.py — title-to-PDF verification."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.verify_split import extract_title_words, verify_title_in_pdf


class TestExtractTitleWords:
    def test_simple_title(self):
        words = extract_title_words('Therapy for the Revolution')
        assert 'therapy' in words
        assert 'revolution' in words
        assert 'the' not in words
        assert 'for' not in words

    def test_strips_book_review_prefix(self):
        words = extract_title_words("Book Review: Love's Labour")
        assert 'book' not in words
        assert 'review' not in words
        assert "love" in words
        assert 'labour' in words

    def test_strips_obituary_prefix(self):
        words = extract_title_words('Obituary: Andrea Sabbadini (1950-2025)')
        assert 'obituary' not in words
        assert 'andrea' in words
        assert 'sabbadini' in words

    def test_editorial_has_no_words(self):
        words = extract_title_words('Editorial')
        # 'editorial' is a stop word, so nothing left
        assert words == []

    def test_book_reviews_has_no_words(self):
        words = extract_title_words('Book Reviews')
        assert words == []

    def test_filters_short_words(self):
        words = extract_title_words('On Lying: A Look at Truth')
        assert 'lying' in words
        assert 'look' in words
        assert 'truth' in words
        # 'on' and 'at' are stop words, 'a' is too short
        assert 'on' not in words

    def test_subtitle_included(self):
        words = extract_title_words(
            'The Psychosis of Existential Training: Towards an inclusive practice'
        )
        assert 'psychosis' in words
        assert 'existential' in words
        assert 'training' in words
        assert 'towards' in words
        assert 'inclusive' in words
        assert 'practice' in words

    def test_handles_special_characters(self):
        words = extract_title_words("Simone de Beauvoir and the Ethics of Ambiguity")
        assert 'simone' in words
        assert 'beauvoir' in words
        assert 'ethics' in words
        assert 'ambiguity' in words

    def test_empty_title(self):
        assert extract_title_words('') == []

    def test_only_stop_words(self):
        assert extract_title_words('The And Of In') == []
