"""
Unit tests for scripts/ojs/build_similar_articles.py

Covers the pure functions: text-blob construction, section rule, and
neighbour selection (self-exclude, score thresholds, review restriction,
max-results cap). Does NOT hit the OJS DB — fetch_submissions() and
write_bulk() are integration-tested by running the script against dev,
not here.

Run:  python3 -m pytest scripts/ojs/test_build_similar_articles.py -v
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


# Load the module — its filename has hyphens/underscores that work, but load
# explicitly so pytest doesn't have to deal with sys.path.
SCRIPT_DIR = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location(
    'build_similar_articles',
    SCRIPT_DIR / 'build_similar_articles.py',
)
bsa = importlib.util.module_from_spec(SPEC)
sys.modules['build_similar_articles'] = bsa
SPEC.loader.exec_module(bsa)


# ---- Fixtures ----

def _sub(submission_id: int, **kwargs) -> dict:
    """Build a minimal submission dict with sensible defaults."""
    return {
        'submission_id': submission_id,
        'publication_id': submission_id + 10000,
        'title': kwargs.get('title', f'Article {submission_id}'),
        'abstract': kwargs.get('abstract', ''),
        'section_title': kwargs.get('section_title', 'Articles'),
        'section_abbrev': kwargs.get('section_abbrev', 'ART'),
        'keywords': kwargs.get('keywords', []),
    }


# ---- strip_html ----

class TestStripHtml:
    def test_passthrough_plain_text(self):
        assert bsa.strip_html('hello world') == 'hello world'

    def test_strips_tags(self):
        assert bsa.strip_html('<p>hello <em>world</em></p>') == 'hello world'

    def test_collapses_whitespace(self):
        # BeautifulSoup joins with the separator we pass (' ')
        assert bsa.strip_html('<p>a</p><p>b</p>') == 'a b'

    def test_empty_input(self):
        assert bsa.strip_html('') == ''
        assert bsa.strip_html('   ') == ''


# ---- build_corpus_text ----

class TestBuildCorpusText:
    def test_keywords_repeated_by_weight(self):
        sub = _sub(1, keywords=['Heidegger', 'Sartre'], title='A paper', abstract='')
        text = bsa.build_corpus_text(sub)
        # KEYWORD_WEIGHT=3 by default: the keyword list appears 3 times
        assert text.count('Heidegger') == bsa.KEYWORD_WEIGHT
        assert text.count('Sartre') == bsa.KEYWORD_WEIGHT

    def test_no_keywords_produces_title_plus_abstract(self):
        sub = _sub(1, keywords=[], title='A title', abstract='<p>An abstract.</p>')
        text = bsa.build_corpus_text(sub)
        assert 'title' in text.lower()
        assert 'abstract' in text.lower()
        assert '<' not in text  # HTML stripped

    def test_empty_submission(self):
        sub = _sub(1, keywords=[], title='', abstract='')
        assert bsa.build_corpus_text(sub) == ''

    def test_keywords_contribute_tf_signal(self):
        # A keyword-only submission and a title-only one with the same token
        # should both produce non-empty text containing that token.
        kw_sub = _sub(1, keywords=['phenomenology'], title='', abstract='')
        title_sub = _sub(2, keywords=[], title='phenomenology', abstract='')
        assert 'phenomenology' in bsa.build_corpus_text(kw_sub).lower()
        assert 'phenomenology' in bsa.build_corpus_text(title_sub).lower()

    def test_title_repeated_by_weight(self):
        sub = _sub(1, title='Beauvoir Sartre', keywords=[], abstract='')
        text = bsa.build_corpus_text(sub)
        # TITLE_WEIGHT repetitions — each title word should appear that many times
        assert text.count('Beauvoir') == bsa.TITLE_WEIGHT
        assert text.count('Sartre') == bsa.TITLE_WEIGHT


# ---- is_review (section rule) ----

class TestIsReview:
    def test_br_matches(self):
        assert bsa.is_review(_sub(1, section_abbrev='BR'))

    def test_articles_does_not(self):
        assert not bsa.is_review(_sub(1, section_abbrev='ART'))

    def test_editorial_does_not(self):
        assert not bsa.is_review(_sub(1, section_abbrev='ED'))

    def test_book_review_editorial_does_not(self):
        # "bookeditorial" contains "book" and "review" in its full name but
        # is not a review section per design — editorial about reviews, not
        # a review itself.
        assert not bsa.is_review(_sub(1, section_abbrev='bookeditorial'))

    def test_missing_abbrev(self):
        sub = _sub(1)
        sub['section_abbrev'] = None
        assert not bsa.is_review(sub)

    def test_whitespace_tolerated(self):
        assert bsa.is_review(_sub(1, section_abbrev=' BR '))


# ---- pick_neighbours ----

class TestPickNeighbours:
    def _build_sims(self, n: int, pattern: dict[tuple[int, int], float]) -> np.ndarray:
        """Build an n×n symmetric similarity matrix with given (i,j)→score overrides."""
        m = np.zeros((n, n))
        for (i, j), score in pattern.items():
            m[i, j] = score
            m[j, i] = score
        np.fill_diagonal(m, 1.0)
        return m

    def test_self_excluded(self):
        sims = self._build_sims(3, {(0, 1): 0.5, (0, 2): 0.3})
        subs = [_sub(i) for i in range(3)]
        result = bsa.pick_neighbours(sims, subs, src_idx=0)
        ids = [r[0] for r in result]
        assert subs[0]['submission_id'] not in ids

    def test_ordered_by_similarity_desc(self):
        sims = self._build_sims(4, {
            (0, 1): 0.2,
            (0, 2): 0.6,
            (0, 3): 0.4,
        })
        subs = [_sub(i) for i in range(4)]
        result = bsa.pick_neighbours(sims, subs, src_idx=0)
        scores = [r[1] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_caps_at_max_results(self):
        # 10 candidates all with good scores — should only return MAX_RESULTS
        n = 12
        pattern = {(0, j): 0.5 + j * 0.01 for j in range(1, n)}
        sims = self._build_sims(n, pattern)
        subs = [_sub(i) for i in range(n)]
        result = bsa.pick_neighbours(sims, subs, src_idx=0)
        assert len(result) == bsa.MAX_RESULTS

    def test_min_score_filters_noise(self):
        # Scores below MIN_SCORE should be excluded
        sims = self._build_sims(4, {
            (0, 1): bsa.MIN_SCORE + 0.01,   # passes
            (0, 2): bsa.MIN_SCORE - 0.01,   # filtered
            (0, 3): 0.0,                    # filtered
        })
        subs = [_sub(i) for i in range(4)]
        result = bsa.pick_neighbours(sims, subs, src_idx=0)
        ids = [r[0] for r in result]
        assert subs[1]['submission_id'] in ids
        assert subs[2]['submission_id'] not in ids
        assert subs[3]['submission_id'] not in ids

    def test_max_score_filters_duplicates(self):
        # Score 1.0 (exact match, duplicate import) and >= MAX_SCORE excluded
        sims = self._build_sims(4, {
            (0, 1): 1.0,                    # duplicate, filtered
            (0, 2): bsa.MAX_SCORE + 0.01,   # near-duplicate, filtered
            (0, 3): bsa.MAX_SCORE - 0.01,   # just below, passes
        })
        subs = [_sub(i) for i in range(4)]
        result = bsa.pick_neighbours(sims, subs, src_idx=0)
        ids = [r[0] for r in result]
        assert subs[1]['submission_id'] not in ids
        assert subs[2]['submission_id'] not in ids
        assert subs[3]['submission_id'] in ids

    def test_review_source_sees_only_reviews(self):
        # Source is BR; candidates are mix of BR and ART. Expect only BR returned.
        sims = self._build_sims(5, {
            (0, 1): 0.8,
            (0, 2): 0.7,
            (0, 3): 0.6,
            (0, 4): 0.5,
        })
        subs = [
            _sub(0, section_abbrev='BR'),   # source
            _sub(1, section_abbrev='BR'),
            _sub(2, section_abbrev='ART'),
            _sub(3, section_abbrev='BR'),
            _sub(4, section_abbrev='ART'),
        ]
        result = bsa.pick_neighbours(sims, subs, src_idx=0)
        ids = {r[0] for r in result}
        assert ids == {subs[1]['submission_id'], subs[3]['submission_id']}

    def test_article_source_sees_anything(self):
        # Source is ART; should see both ART and BR candidates
        sims = self._build_sims(3, {
            (0, 1): 0.8,
            (0, 2): 0.7,
        })
        subs = [
            _sub(0, section_abbrev='ART'),   # source
            _sub(1, section_abbrev='BR'),
            _sub(2, section_abbrev='ART'),
        ]
        result = bsa.pick_neighbours(sims, subs, src_idx=0)
        ids = {r[0] for r in result}
        assert ids == {subs[1]['submission_id'], subs[2]['submission_id']}

    def test_empty_when_no_matches_above_threshold(self):
        sims = self._build_sims(3, {
            (0, 1): 0.05,   # below MIN_SCORE
            (0, 2): 0.08,   # below MIN_SCORE
        })
        subs = [_sub(i) for i in range(3)]
        result = bsa.pick_neighbours(sims, subs, src_idx=0)
        assert result == []


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
