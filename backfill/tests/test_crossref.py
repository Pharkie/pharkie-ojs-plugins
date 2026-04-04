"""Tests for backfill.lib.crossref module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backfill.lib.crossref import (
    TIER_MATCHED,
    TIER_NO_MATCH,
    _normalise_title,
    _title_similarity,
    has_existing_doi,
    score_match,
)

FIXTURES_DIR = Path(__file__).parent / 'fixtures'


# ---------- has_existing_doi ----------

@pytest.mark.parametrize('text, expected', [
    # Standard DOI formats
    ('Smith (2020). Title. doi:10.1234/test', '10.1234/test'),
    ('Smith (2020). Title. https://doi.org/10.1234/test', '10.1234/test'),
    ('Smith (2020). Title. DOI: 10.1234/test.foo', '10.1234/test.foo'),
    # DOI with trailing punctuation stripped
    ('Smith (2020). Title. doi:10.1234/test.', '10.1234/test'),
    ('Smith (2020). Title. (10.1234/test)', '10.1234/test'),
    # No DOI
    ('Smith (2020). Title. London: Publisher.', None),
    ('Kierkegaard, S. (1849). The Sickness Unto Death.', None),
    # Our own DOI prefix is now returned (articles deposited to Crossref)
    ('Article. 10.65828/abc123', '10.65828/abc123'),
    ('Article. doi:10.65828/xyz789', '10.65828/xyz789'),
    # Mixed: returns first DOI found (our prefix appears first)
    ('Article 10.65828/own. See also 10.1234/external.', '10.65828/own'),
])
def test_has_existing_doi(text, expected):
    assert has_existing_doi(text) == expected


# ---------- _normalise_title ----------

def test_normalise_title():
    assert _normalise_title('  Hello, World!  ') == 'hello world'
    assert _normalise_title('Being-in-the-World') == 'beingintheworld'
    assert _normalise_title('') == ''


# ---------- _title_similarity ----------

def test_title_similarity_exact_containment():
    sim = _title_similarity(
        'When Death Enters the Therapeutic Space',
        'Barnett, L. (2009). When Death Enters the Therapeutic Space. London: Routledge.',
    )
    assert sim == 1.0


def test_title_similarity_word_overlap():
    sim = _title_similarity(
        'The Sickness Unto Death',
        'Kierkegaard, S. (1849). The Sickness Unto Death. Princeton University Press.',
    )
    assert sim == 1.0


def test_title_similarity_partial():
    sim = _title_similarity(
        'Introduction to Metaphysics',
        'Heidegger, M. (2000). Intro to Metaphysics. Yale.',
    )
    # "introduction" won't match "intro" exactly, but "to" and "metaphysics" will
    assert 0.3 < sim < 1.0


def test_title_similarity_no_match():
    sim = _title_similarity(
        'Quantum Computing Basics',
        'Kierkegaard, S. (1849). The Sickness Unto Death.',
    )
    assert sim < 0.3


def test_title_similarity_empty():
    assert _title_similarity('', 'some text') == 0.0
    assert _title_similarity('some text', '') == 0.0


# ---------- score_match ----------

def _make_result(doi='10.1234/test', score=80, title='Test Title',
                 authors=None, container='', type_='journal-article'):
    """Helper to build a Crossref-style result dict."""
    return {
        'DOI': doi,
        'score': score,
        'title': [title] if title else [],
        'author': authors or [],
        'container-title': [container] if container else [],
        'type': type_,
    }


def test_score_match_matched():
    result = _make_result(
        score=90, title='The Sickness Unto Death',
        authors=[{'family': 'Kierkegaard', 'given': 'Søren'}],
    )
    ref_text = 'Kierkegaard, S. (1849). The Sickness Unto Death. Princeton.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_MATCHED
    assert sim >= 0.7
    assert details['matched_doi'] == '10.1234/test'
    assert details['author_match'] is True


def test_score_match_no_match_when_author_mismatch():
    """A high-similarity match with wrong author should be no_match."""
    result = _make_result(
        score=90, title='The Sickness Unto Death',
        authors=[{'family': 'ReviewerSmith', 'given': 'J.'}],
    )
    ref_text = 'Kierkegaard, S. (1849). The Sickness Unto Death. Princeton.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH
    assert details['author_match'] is False


def test_score_match_no_match_low_similarity():
    result = _make_result(score=50, title='Somewhat Different Title')
    ref_text = 'Smith (2020). A completely different reference. London.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH


def test_score_match_no_match():
    result = _make_result(score=10, title='Unrelated Paper')
    ref_text = 'Kierkegaard, S. (1849). The Sickness Unto Death.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH


def test_score_match_type_mismatch_book_vs_journal_article():
    """A book reference matched to a journal-article should be no_match."""
    result = _make_result(
        score=99, title='Tête-à-Tête: Simone de Beauvoir and Jean-Paul Sartre',
        authors=[{'family': 'Rowley', 'given': 'H.'}],
        type_='journal-article',
    )
    ref_text = 'Rowley, H. (2007). Tête-à-Tête. London: Vintage.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH
    assert details['type_mismatch'] is True


def test_score_match_type_mismatch_reference_entry():
    """Encyclopedia entries about an author are not the cited work."""
    result = _make_result(
        score=60, title='Thich Nhat Hanh',
        authors=[],
        type_='reference-entry',
    )
    ref_text = 'Thich Nhat Hanh. (2006). Understanding Our Mind. Berkeley: Parallax Press.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH
    assert details['type_mismatch'] is True


def test_score_match_type_mismatch_dataset():
    """APA PsycINFO records are metadata about a book, not the book itself."""
    result = _make_result(
        score=50, title='Mindfulness-Based Cognitive Therapy for Depression',
        authors=[{'family': 'Segal', 'given': 'Z.'}],
        type_='dataset',
    )
    ref_text = 'Segal, Z.V. et al. (2002). Mindfulness-Based Cognitive Therapy for Depression. Guilford Press.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH
    assert details['type_mismatch'] is True


def test_score_match_type_mismatch_standalone_book_vs_chapter():
    """A standalone book matched to a book-chapter from a different book."""
    result = _make_result(
        score=90, title='SIMONE DE BEAUVOIR AND JEAN-PAUL SARTRE',
        authors=[{'family': 'Rowley', 'given': 'H.'}],
        type_='book-chapter',
    )
    # No "In" pattern — this is a standalone book reference
    ref_text = 'Rowley, H. (2007). Tête-à-Tête: The lives and loves of Simone de Beauvoir and Jean-Paul Sartre. London: Vintage.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH
    assert details['type_mismatch'] is True


def test_score_match_reference_work_container_mismatch():
    """An entry in a dictionary/companion about the cited work is not the work."""
    result = _make_result(
        score=50, title='Notebooks for an Ethics',
        authors=[],
        container='The Sartre Dictionary',
        type_='other',
    )
    ref_text = 'Sartre, J.P. (1992). Notebooks for an Ethics. Trans. Pellauer, D. London: University of Chicago Press.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH
    assert details['type_mismatch'] is True


def test_score_match_no_type_mismatch_chapter_in_book():
    """A chapter reference with 'In' pattern matched to book-chapter is fine."""
    result = _make_result(
        score=60, title='Letter on Humanism',
        authors=[{'family': 'Heidegger', 'given': 'M.'}],
        type_='book-chapter',
    )
    ref_text = 'Heidegger, M. (1998). Letter on Humanism. In Heidegger, M. Pathmarks. Cambridge UP.'
    tier, sim, details = score_match(result, ref_text)
    assert details['type_mismatch'] is False


def test_score_match_type_mismatch_journal_ref_vs_book_chapter():
    """A journal article ref matched to a book-chapter by the same author."""
    result = _make_result(
        score=48, title='Sartre, Alienation, and the Other',
        authors=[{'family': 'Rae', 'given': 'G.'}],
        type_='book-chapter',
    )
    ref_text = 'Rae, G. (2009). Sartre & the Other. Sartre Studies International, 15(2), 54-77.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH
    assert details['type_mismatch'] is True


def test_score_match_no_type_mismatch_for_journal_ref():
    """A journal ref matched to journal-article should NOT be demoted."""
    result = _make_result(
        score=90, title='Anxiety and the Search for Meaning',
        authors=[{'family': 'Smith', 'given': 'J.'}],
        type_='journal-article',
    )
    ref_text = 'Smith, J. (2005). Anxiety and the search for meaning. Journal of Existential Analysis, 16(2), 45-67.'
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_MATCHED
    assert details['type_mismatch'] is False


def test_score_match_self_citation_boost():
    """Self-citation to our journal should be boosted when ref names our journal."""
    result = _make_result(
        doi='10.65828/ea.15.2.03',
        score=10,  # Below all normal thresholds (MIN_SCORE_EXACT_TITLE=20)
        title='Anxiety and Authenticity',
        authors=[{'family': 'Adams', 'given': 'M.'}],
        container='Existential Analysis',
        type_='journal-article',
    )
    ref_text = ('Adams, M. (2004). Anxiety and Authenticity. '
                'Existential Analysis, 15(2), 30-45.')
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_MATCHED
    assert details.get('self_citation_boost') is True


def test_score_match_no_false_self_citation_boost():
    """External journal result should NOT get boost even if ref cites our journal."""
    result = _make_result(
        doi='10.1080/external',
        score=45,
        title='Anxiety and Authenticity',
        authors=[{'family': 'Adams', 'given': 'M.'}],
        container='British Journal of Psychotherapy',
        type_='journal-article',
    )
    ref_text = ('Adams, M. (2004). Anxiety and Authenticity. '
                'Existential Analysis, 15(2), 30-45.')
    tier, sim, details = score_match(result, ref_text)
    assert details.get('self_citation_boost') is not True


def test_score_match_self_citation_still_needs_author():
    """Self-citation boost should not override author mismatch."""
    result = _make_result(
        doi='10.65828/ea.15.2.03',
        score=25,
        title='Anxiety and Authenticity',
        authors=[{'family': 'WrongAuthor', 'given': 'X.'}],
        container='Existential Analysis',
        type_='journal-article',
    )
    ref_text = ('Adams, M. (2004). Anxiety and Authenticity. '
                'Existential Analysis, 15(2), 30-45.')
    tier, sim, details = score_match(result, ref_text)
    assert tier == TIER_NO_MATCH


def test_score_match_no_title():
    result = _make_result(score=80, title='')
    ref_text = 'Some reference text.'
    tier, sim, details = score_match(result, ref_text)
    # No title means similarity=0, so shouldn't auto-accept
    assert tier != TIER_MATCHED
    assert details['title_similarity'] == 0.0


# ---------- strip_doi_from_text ----------

@pytest.mark.parametrize('text, doi, expected', [
    # doi: prefix
    ('Smith (2020). Title. Journal, 49: 9-21. doi: 10.1037/pro0000152.',
     '10.1037/pro0000152',
     'Smith (2020). Title. Journal, 49: 9-21.'),
    # doi: prefix no space
    ('Smith (2020). Title. 18(4), 189-194. doi:10.1111/j.1467-8721',
     '10.1111/j.1467-8721',
     'Smith (2020). Title. 18(4), 189-194.'),
    # DOI: https://doi.org/ prefix
    ('Smith (2020). Title. 49: 9-21. DOI: https://doi.org/10.1037/pro0000152',
     '10.1037/pro0000152',
     'Smith (2020). Title. 49: 9-21.'),
    # With [Accessed...] after DOI
    ('Smith (2020). Title. 49: 9-21. 10.1037/pro0000152. [Accessed on 30th November 2025.]',
     '10.1037/pro0000152',
     'Smith (2020). Title. 49: 9-21.'),
    # DOI not in text — no change
    ('Smith (2020). Title. London: Publisher.',
     '10.1234/test',
     'Smith (2020). Title. London: Publisher.'),
    # Bare DOI at end
    ('Smith (2020). Title. Journal, 1(4), 251-264. 10.1002/bs.123',
     '10.1002/bs.123',
     'Smith (2020). Title. Journal, 1(4), 251-264.'),
])
def test_strip_doi_from_text(text, doi, expected):
    from backfill.lib.crossref import strip_doi_from_text
    assert strip_doi_from_text(text, doi) == expected


# ---------- Fixture-based test ----------

@pytest.fixture
def crossref_response():
    fixture_path = FIXTURES_DIR / 'crossref_response.json'
    if fixture_path.exists():
        with open(fixture_path) as f:
            return json.load(f)
    return None


def test_fixture_response(crossref_response):
    """Test scoring against a recorded Crossref API response."""
    if crossref_response is None:
        pytest.skip('No crossref_response.json fixture yet')

    ref_text = crossref_response['query_text']
    items = crossref_response['items']

    assert len(items) > 0
    tier, sim, details = score_match(items[0], ref_text)
    assert tier in (TIER_MATCHED, TIER_NO_MATCH)
    assert 'matched_doi' in details
