"""Live Crossref API integration tests for DOI matching.

These tests hit the real Crossref API to verify end-to-end matching.
Run with: python3 -m pytest backfill/tests/test_crossref_live.py -v

Each test represents one reference from article 35.1/02-who-do-we-think-we-are
and asserts the expected matching outcome.
"""

import pytest

from backfill.lib.crossref import (
    TIER_MATCHED,
    TIER_NO_MATCH,
    query_crossref,
    score_match,
)

EMAIL = 'backfill-test@existentialanalysis.org.uk'


def _match_ref(ref_text):
    """Query Crossref and score the best candidate.

    Same ranking as pipe4b: matched tier first, then similarity, then score.
    """
    results = query_crossref(ref_text, EMAIL)
    if not results:
        return TIER_NO_MATCH, None, {}

    _TIER_RANK = {TIER_MATCHED: 1, TIER_NO_MATCH: 0}
    best_rank = (-1, -1, -1)
    best = (TIER_NO_MATCH, 0, {})
    for candidate in results:
        tier, sim, details = score_match(candidate, ref_text)
        cr_score = details.get('crossref_score', 0)
        rank = (_TIER_RANK.get(tier, 0), sim, cr_score)
        if rank > best_rank:
            best_rank = rank
            best = (tier, sim, details)

    return best


# -- ref1: Barnett, When Death Enters the Therapeutic Space --
def test_ref1_barnett_when_death_enters():
    tier, sim, details = _match_ref(
        'Barnett, L. (2009). When Death Enters the Therapeutic Space. '
        'London: Routledge.'
    )
    assert tier == TIER_MATCHED
    assert '10.4324' in details['matched_doi']


# -- ref2: Barnett, The Heart of Therapy --
def test_ref2_barnett_heart_of_therapy():
    tier, sim, details = _match_ref(
        'Barnett, L. (2023). The Heart of Therapy: Developing compassion, '
        'understanding and boundaries. London: Routledge.'
    )
    assert tier == TIER_MATCHED
    assert '10.4324' in details['matched_doi']


# -- ref3: Fried & Polt, Translators' introduction in Introduction to Metaphysics --
def test_ref3_fried_polt_translators_intro():
    tier, sim, details = _match_ref(
        'Fried, G. & Polt, R. (eds.) (2000). Translators\u2019 introduction. '
        'In Heidegger, M. Introduction to Metaphysics. New Haven: Yale '
        'University Press.'
    )
    # This should match the book "Introduction to Metaphysics"
    # DOI: 10.12987/9780300161434 (Yale UP)
    assert tier == TIER_MATCHED
    assert 'introduction to metaphysics' in details.get('crossref_title', '').lower()


# -- ref4: Heidegger, Letter on Humanism in Pathmarks --
def test_ref4_heidegger_letter_on_humanism():
    tier, sim, details = _match_ref(
        'Heidegger, M. (1998 [1947]). Letter on Humanism. Trans. Capuzzi, F. '
        '(1967). In Heidegger, M. (1998 [1976 2nd ed. revised and expanded]). '
        'Pathmarks. McNeill, W. (ed.), Cambridge: Cambridge University Press.'
    )
    assert tier == TIER_MATCHED
    assert 'humanism' in details.get('crossref_title', '').lower()


# -- ref5: Heidegger, Introduction to Metaphysics --
def test_ref5_heidegger_introduction_to_metaphysics():
    tier, sim, details = _match_ref(
        'Heidegger, M. (2000 [1953]). Introduction to Metaphysics. (Lecture '
        'delivered 1935). Trans. Fried, G. & Polt, R. New Haven: Yale '
        'University Press.'
    )
    # DOI: 10.12987/9780300161434 (Yale UP)
    assert tier == TIER_MATCHED
    assert 'introduction to metaphysics' in details.get('crossref_title', '').lower()


# -- ref6: Heidegger, Only a god can save us (Spiegel interview) --
def test_ref6_heidegger_only_a_god():
    tier, sim, details = _match_ref(
        'Heidegger, M. (2003 [1966]). Only a god can save us. Der Spiegel '
        'Interview, Trans. Alter, M. & Caputo, J.D. In Stassen, M. (ed.). '
        'Martin Heidegger: Philosophical and political writings. New York: '
        'Continuum.'
    )
    assert tier == TIER_MATCHED
    assert 'god' in details.get('crossref_title', '').lower()


# -- ref7: Rowley, Tête-à-Tête --
def test_ref7_rowley_tete_a_tete():
    """Book by Rowley. Crossref only has a journal review, not the book.
    No book DOI exists — correct outcome is review or no_match."""
    tier, sim, details = _match_ref(
        'Rowley, H. (2007 [2006]). Tête-à-Tête: The lives and loves of '
        'Simone de Beauvoir and Jean-Paul Sartre. London: Vintage.'
    )
    # No book DOI exists; the journal-article review should be caught
    assert tier in (TIER_NO_MATCH, TIER_NO_MATCH)


# -- ref8: Sartre, Les Mots --
def test_ref8_sartre_les_mots():
    """French paperback. Unlikely to have a DOI."""
    tier, sim, details = _match_ref(
        'Sartre, J-P. (2003 [1964]). Les Mots. Paris: Edition Gallimard.'
    )
    assert tier in (TIER_NO_MATCH, TIER_NO_MATCH)


# -- ref9: Springora, Le Consentement --
def test_ref9_springora_le_consentement():
    """French book. Crossref only has journal reviews."""
    tier, sim, details = _match_ref(
        'Springora, V. (2020). Le Consentement. Paris: Grasset et Fasquelle.'
    )
    assert tier in (TIER_NO_MATCH, TIER_NO_MATCH)
