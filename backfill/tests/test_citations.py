"""Tests for backfill/lib/citations.py — citation classification and name detection.

IMPORTANT: These tests encode what the CORRECT behaviour should be, determined
by human judgement — NOT by observing what the code currently does. If a test
fails, the CODE is wrong, not the test. Fix the implementation, not the test.

Test data lives in backfill/tests/fixtures/*.json — open those files to review
or update the ground-truth data for each category.

Run: pytest backfill/tests/test_citations.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.lib.citations import (
    looks_like_person_name,
    is_author_bio,
    is_author_contact,
    is_provenance,
    is_reference,
    is_note,
    is_junk,
    is_citation_like,
    is_section_sublabel,
    classify,
    strip_html,
    _count_sentences,
)

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def load(filename):
    with open(os.path.join(FIXTURES, filename)) as f:
        return json.load(f)


# ---------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------

def _all_true_names():
    """Flatten all 'true' name groups into (group_name, name) pairs."""
    data = load('names.json')['true']
    for group, names in data.items():
        for name in names:
            yield pytest.param(name, id=f'{group}:{name}')


def _all_false_names():
    """Flatten all 'false' name groups into (group_name, text) pairs."""
    data = load('names.json')['false']
    for group, texts in data.items():
        for text in texts:
            yield pytest.param(text, id=f'{group}:{text[:40]}')


# ===============================================================
# Person name detection
# ===============================================================

class TestLooksLikePersonName:

    @pytest.mark.parametrize('name', list(_all_true_names()))
    def test_is_name(self, name):
        assert looks_like_person_name(name), f'Should be a person name: {name}'

    @pytest.mark.parametrize('text', list(_all_false_names()))
    def test_is_not_name(self, text):
        assert not looks_like_person_name(text), f'Should NOT be a person name: {text}'


# ===============================================================
# Author bio detection
# ===============================================================

class TestIsAuthorBio:

    _data = load('bios.json')

    @pytest.mark.parametrize('text', _data['true'])
    def test_is_bio(self, text):
        assert is_author_bio(text), f'Should be bio: {text[:60]}'

    @pytest.mark.parametrize('text', _data['false'])
    def test_is_not_bio(self, text):
        assert not is_author_bio(text), f'Should NOT be bio: {text[:60]}'


# ===============================================================
# Contact detection
# ===============================================================

class TestIsAuthorContact:

    _data = load('contacts.json')

    @pytest.mark.parametrize('text', _data['true'])
    def test_is_contact(self, text):
        assert is_author_contact(text)

    @pytest.mark.parametrize('text', _data['false'])
    def test_is_not_contact(self, text):
        assert not is_author_contact(text)


# ===============================================================
# Provenance detection
# ===============================================================

class TestIsProvenance:

    _data = load('provenance.json')

    @pytest.mark.parametrize('text', _data['true'])
    def test_is_provenance(self, text):
        assert is_provenance(text), f'Should be provenance: {text[:60]}'

    @pytest.mark.parametrize('text', _data['false'])
    def test_is_not_provenance(self, text):
        assert not is_provenance(text)


# ===============================================================
# Reference detection
# ===============================================================

class TestIsReference:

    _data = load('references.json')

    @pytest.mark.parametrize('text', _data['true'])
    def test_is_reference(self, text):
        assert is_reference(text), f'Should be reference: {text[:70]}'

    @pytest.mark.parametrize('text', _data['false'])
    def test_is_not_reference(self, text):
        assert not is_reference(text), f'Should NOT be reference: {text[:70]}'


# ===============================================================
# Note detection
# ===============================================================

class TestIsNote:

    _data = load('notes.json')

    @pytest.mark.parametrize(
        'text,expected_reason',
        [(c['text'], c['reason']) for c in _data['with_reason']],
    )
    def test_detects_with_reason(self, text, expected_reason):
        result = is_note(text)
        assert result is not None, f'Should be a note: {text[:60]}'
        assert result == expected_reason, f'Expected "{expected_reason}", got "{result}"'

    @pytest.mark.parametrize('text', _data['classify_as_note'])
    def test_classify_as_note(self, text):
        assert classify(text) == 'note', f'Should classify as note: {text[:60]}'

    @pytest.mark.parametrize('text', _data['not_notes'])
    def test_not_a_note(self, text):
        assert is_note(text) is None, f'Should NOT be a note: {text[:60]}'


# ===============================================================
# Classify (combined reference vs note)
# ===============================================================

class TestClassify:

    _data = load('classify.json')

    @pytest.mark.parametrize(
        'text,expected',
        [(c['text'], c['expected']) for c in _data['cases']],
    )
    def test_classifies(self, text, expected):
        assert classify(text) == expected, f'"{text[:50]}" should be {expected}'


# ===============================================================
# Junk detection
# ===============================================================

class TestIsJunk:

    @pytest.mark.parametrize('text', [
        'Short',
        'Yours sincerely.',
        'Kind regards',
    ])
    def test_is_junk(self, text):
        assert is_junk(text)

    def test_reference_is_not_junk(self):
        assert not is_junk(
            'Kierkegaard, S. (1849). The Sickness Unto Death. Princeton University Press.')


# ===============================================================
# Citation-like detection
# ===============================================================

class TestIsCitationLike:

    @pytest.mark.parametrize('text', [
        'Kierkegaard, S. (1849). The Sickness Unto Death. Princeton University Press.',
        'Smith, J. (2005). Journal of Psychotherapy, 16, pp. 45-67.',
    ])
    def test_is_citation_like(self, text):
        assert is_citation_like(text)

    @pytest.mark.parametrize('text', [
        'This is a general commentary with no bibliographic markers.',
        'Short text',
    ])
    def test_is_not_citation_like(self, text):
        assert not is_citation_like(text)


# ===============================================================
# Section sublabel
# ===============================================================

class TestIsSectionSublabel:

    @pytest.mark.parametrize('text', [
        'English-language references:',
        'Secondary sources:',
        'Russian-language references:',
    ])
    def test_is_sublabel(self, text):
        assert is_section_sublabel(text)

    @pytest.mark.parametrize('text', [
        'Kierkegaard, S. (1849). The Sickness Unto Death.',
        'This is a longer text that does not end with a colon and is prose.',
    ])
    def test_is_not_sublabel(self, text):
        assert not is_section_sublabel(text)


# ===============================================================
# Helpers
# ===============================================================

class TestStripHtml:
    def test_strips_tags(self):
        assert strip_html('<p>Hello <em>world</em></p>') == 'Hello world'

    def test_empty(self):
        assert strip_html('') == ''

    def test_plain_text(self):
        assert strip_html('no tags here') == 'no tags here'


class TestCountSentences:
    def test_single_sentence(self):
        assert _count_sentences('This is one sentence.') == 0

    def test_two_sentences(self):
        assert _count_sentences('First sentence. Second sentence.') == 1

    def test_abbreviations_not_counted(self):
        assert _count_sentences('Dr. Smith went to Vol. 3 of the work.') == 0

    def test_initials_not_counted(self):
        assert _count_sentences('R.D. Laing wrote about it. Then he continued.') == 1
