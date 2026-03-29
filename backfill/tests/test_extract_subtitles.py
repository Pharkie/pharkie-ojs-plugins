"""Tests for backfill/extract_subtitles.py — subtitle detection and title splitting.

IMPORTANT: These tests encode what the CORRECT behaviour should be, determined
by human judgement — NOT by observing what the code currently does. If a test
fails, the CODE is wrong, not the test. Fix the implementation, not the test.

Run: pytest backfill/tests/test_extract_subtitles.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.extract_subtitles import (
    detect_subtitle,
    split_title,
    parse_author_names,
    text_matches_author,
    is_section_heading,
    is_book_review_metadata,
    is_provenance_note,
    _clean_split_title,
)


# ===============================================================
# Title/subtitle splitting
#
# Ground truth: given a concatenated toc title and a known subtitle,
# where should the split be? Determined by human reading.
# ===============================================================

class TestSplitTitle:

    @pytest.mark.parametrize('toc_title,subtitle,expected_title', [
        # Colon separator
        ('Being With Cyril: Can Heideggerian language do justice to psychotherapy?',
         'Can Heideggerian language do justice to psychotherapy?',
         'Being With Cyril'),
        # Dash separator
        ('Existential Therapy — A New Approach for Our Time',
         'A New Approach for Our Time',
         'Existential Therapy'),
        # No separator, just concatenated
        ('The Courage to Be An Existential Ontology of Courage',
         'An Existential Ontology of Courage',
         'The Courage to Be'),
        # Question mark in title preserved
        ('Why Dasein? Exploring Heidegger\'s Terminology',
         'Exploring Heidegger\'s Terminology',
         'Why Dasein?'),
        # Parenthetical subtitle
        ('Freedom and Responsibility (A Phenomenological Investigation)',
         'A Phenomenological Investigation',
         'Freedom and Responsibility'),
    ])
    def test_splits_correctly(self, toc_title, subtitle, expected_title):
        result = split_title(toc_title, subtitle)
        assert result == expected_title

    def test_returns_none_when_subtitle_not_found(self):
        assert split_title('Completely Different Title', 'Not Present At All') is None

    def test_returns_none_for_empty_subtitle(self):
        assert split_title('Some Title', '') is None


class TestCleanSplitTitle:
    """After splitting, trailing separator punctuation should be removed."""

    @pytest.mark.parametrize('raw,expected', [
        ('Being With Cyril:', 'Being With Cyril'),
        ('Being With Cyril -', 'Being With Cyril'),
        ('Being With Cyril –', 'Being With Cyril'),
        ('Being With Cyril —', 'Being With Cyril'),
        ('Being With Cyril (', 'Being With Cyril'),
        # Question mark is meaningful — keep it
        ('Why Dasein?', 'Why Dasein?'),
        # Orphaned quote stripped
        ("Being With Cyril: '", 'Being With Cyril'),
        # Trailing whitespace
        ('Being With Cyril  ', 'Being With Cyril'),
    ])
    def test_cleans_trailing_punctuation(self, raw, expected):
        assert _clean_split_title(raw) == expected


# ===============================================================
# Subtitle detection from raw HTML
#
# Ground truth: given this HTML structure, should there be a subtitle?
# A subtitle is text between the title heading and the author byline
# that isn't a section heading, body text, author name, or metadata.
# ===============================================================

class TestDetectSubtitle:

    def test_detects_subtitle_between_title_and_author(self):
        html = '<h1>Being With Cyril</h1><p>Can Heideggerian language do justice?</p><h2>Titos Florides</h2>'
        result = detect_subtitle(html, ['Titos Florides'])
        assert result is not None
        title, subtitle = result
        assert title == 'Being With Cyril'
        assert subtitle == 'Can Heideggerian language do justice?'

    def test_no_subtitle_when_author_follows_title(self):
        """Author directly after title = no subtitle."""
        html = '<h1>Being With Cyril</h1><h2>Titos Florides</h2><p>Body text here.</p>'
        result = detect_subtitle(html, ['Titos Florides'])
        assert result is None

    def test_no_subtitle_for_body_text_opener(self):
        """Text starting with 'The/This/In/etc.' is body text, not subtitle."""
        html = '<h1>Anxiety and Freedom</h1><p>The existential approach has gained traction in recent years.</p>'
        result = detect_subtitle(html, ['Author Name'])
        assert result is None

    def test_question_is_valid_subtitle(self):
        """Questions after the title are valid subtitles even if they start with common words."""
        html = '<h1>Being and Nothingness</h1><p>What does Sartre really mean?</p><h2>John Smith</h2>'
        result = detect_subtitle(html, ['John Smith'])
        assert result is not None
        _, subtitle = result
        assert subtitle == 'What does Sartre really mean?'

    def test_no_subtitle_for_book_reviews(self):
        html = '<h1>Book Review</h1><p>Routledge Press, 2020, pp. 234.</p>'
        result = detect_subtitle(html, ['Reviewer'], section='Book Reviews')
        assert result is None

    def test_no_subtitle_for_section_heading(self):
        """'Abstract' after title is a section heading, not subtitle."""
        html = '<h1>Existential Themes</h1><p>Abstract</p>'
        result = detect_subtitle(html, ['Author Name'])
        assert result is None

    def test_no_subtitle_when_person_name_follows(self):
        """A person name after the title is the author, not a subtitle."""
        html = '<h1>On Anxiety</h1><p>Kirk J Schneider</p><p>Body text.</p>'
        result = detect_subtitle(html, [])
        assert result is None

    def test_no_subtitle_for_too_long_text(self):
        """Text > 200 chars is body text, not subtitle."""
        long_text = 'A detailed exploration of ' + 'the existential themes ' * 10
        html = f'<h1>Title</h1><p>{long_text.strip()}</p>'
        result = detect_subtitle(html, ['Author Name'])
        assert result is None

    def test_no_subtitle_for_provenance_note(self):
        html = '<h1>Existential Themes</h1><p>This paper was presented at the 2019 conference.</p>'
        result = detect_subtitle(html, ['Author Name'])
        assert result is None

    def test_no_subtitle_when_no_heading(self):
        """No <h1>/<h2> = can't detect subtitle."""
        html = '<p>Some text</p><p>More text</p>'
        result = detect_subtitle(html, ['Author Name'])
        assert result is None


# ===============================================================
# Author name parsing
#
# Ground truth: split author strings into individual names.
# ===============================================================

class TestParseAuthorNames:

    @pytest.mark.parametrize('raw,expected', [
        ('Titos Florides', ['Titos Florides']),
        ('Alice Smith & Bob Jones', ['Alice Smith', 'Bob Jones']),
        ('Alice Smith and Bob Jones', ['Alice Smith', 'Bob Jones']),
        ('Alice, Bob, Charlie', ['Alice', 'Bob', 'Charlie']),
        (['Pre', 'Parsed'], ['Pre', 'Parsed']),
        ('', []),
        (None, []),
    ])
    def test_parses(self, raw, expected):
        assert parse_author_names(raw) == expected


# ===============================================================
# Author text matching
# ===============================================================

class TestTextMatchesAuthor:

    def test_exact_match(self):
        assert text_matches_author('Titos Florides', ['Titos Florides'])

    def test_family_name_in_short_text(self):
        assert text_matches_author('Florides', ['Titos Florides'])

    def test_variant_with_middle_initial(self):
        assert text_matches_author('Evgenia T. Georganda', ['Evgenia Georganda'])

    def test_no_match(self):
        assert not text_matches_author('Completely Different', ['Titos Florides'])

    def test_family_name_in_long_text_rejected(self):
        """Family name appearing in a sentence should NOT match (it's not a name element)."""
        assert not text_matches_author(
            'Florides argues that existential therapy is fundamentally about encounter',
            ['Titos Florides']
        )


# ===============================================================
# Helper predicates
# ===============================================================

class TestIsSectionHeading:

    @pytest.mark.parametrize('text', [
        'Abstract', 'Introduction', 'References', 'Bibliography',
        'Conclusion', 'Discussion', 'Keywords',
    ])
    def test_section_headings(self, text):
        assert is_section_heading(text)

    @pytest.mark.parametrize('text', [
        'Being and Time', 'Existential Themes', 'Not A Heading',
    ])
    def test_non_headings(self, text):
        assert not is_section_heading(text)


class TestIsBookReviewMetadata:

    @pytest.mark.parametrize('text', [
        'Routledge Press, 2020, pp. 234, ISBN 978-0-123456-78-9',
        'London: Sage Publishing, 2019. pp. 456',
    ])
    def test_detects_metadata(self, text):
        assert is_book_review_metadata(text)

    def test_rejects_normal_text(self):
        assert not is_book_review_metadata('A thoughtful exploration of existential themes.')


class TestIsProvenanceNote:

    def test_detects(self):
        assert is_provenance_note('This paper was given at the annual conference.')

    def test_rejects(self):
        assert not is_provenance_note('Emmy van Deurzen is a psychotherapist.')
