"""Tests for backfill/lib/postprocess.py — HTML post-processing pipeline.

IMPORTANT: These tests encode what the CORRECT behaviour should be, determined
by human judgement — NOT by observing what the code currently does. If a test
fails, the CODE is wrong, not the test. Fix the implementation, not the test.

Test data lives in backfill/tests/fixtures/postprocess.json — open that file
to review or update the ground-truth data.

Run: pytest backfill/tests/test_postprocess.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.lib.postprocess import (
    strip_title,
    strip_subtitle,
    strip_authors,
    strip_abstract,
    strip_keywords,
    strip_start_bleed,
    strip_end_bleed,
    postprocess_article,
    verify_postprocessed,
    RUNNING_HEADER_RE,
    PAGE_NUMBER_RE,
    _clean,
    _strip_tags,
    _find_first_body_heading,
    _text_to_regex,
    _title_in_text,
    _find_block_by_text,
    _fix_bio_contact_spacing_soup,
    _parse,
)
from backfill.lib.citations import is_citation_like

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def load():
    with open(os.path.join(FIXTURES, 'postprocess.json')) as f:
        return json.load(f)


DATA = load()


# ===============================================================
# strip_title
# ===============================================================

class TestStripTitle:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['strip_title']
    ])
    def test_strip_title(self, case_name):
        case = DATA['strip_title'][case_name]
        result = strip_title(case['html'], case['title'])

        if 'should_contain' in case:
            assert case['should_contain'] in _strip_tags(result), \
                f'{case_name}: should contain "{case["should_contain"]}"'
        if 'should_not_contain' in case:
            assert case['should_not_contain'] not in _strip_tags(result), \
                f'{case_name}: should NOT contain "{case["should_not_contain"]}"'
        if 'should_not_contain_tag' in case:
            assert case['should_not_contain_tag'] not in result, \
                f'{case_name}: should NOT contain tag "{case["should_not_contain_tag"]}"'


# ===============================================================
# strip_authors
# ===============================================================

class TestStripAuthors:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['strip_authors']
    ])
    def test_strip_authors(self, case_name):
        case = DATA['strip_authors'][case_name]
        result = strip_authors(case['html'], case['authors'])

        if 'should_contain' in case:
            assert case['should_contain'] in _strip_tags(result), \
                f'{case_name}: should contain "{case["should_contain"]}"'
        if 'should_not_contain' in case:
            assert case['should_not_contain'] not in _strip_tags(result), \
                f'{case_name}: should NOT contain "{case["should_not_contain"]}"'


# ===============================================================
# strip_abstract
# ===============================================================

class TestStripAbstract:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['strip_abstract']
    ])
    def test_strip_abstract(self, case_name):
        case = DATA['strip_abstract'][case_name]
        result = strip_abstract(case['html'], case['abstract'])

        if 'should_contain' in case:
            assert case['should_contain'] in _strip_tags(result), \
                f'{case_name}: should contain "{case["should_contain"]}"'
        if 'should_not_contain' in case:
            assert case['should_not_contain'] not in _strip_tags(result), \
                f'{case_name}: should NOT contain "{case["should_not_contain"]}"'


# ===============================================================
# strip_keywords
# ===============================================================

class TestStripKeywords:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['strip_keywords']
    ])
    def test_strip_keywords(self, case_name):
        case = DATA['strip_keywords'][case_name]
        result = strip_keywords(case['html'])

        if 'should_contain' in case:
            assert case['should_contain'] in _strip_tags(result), \
                f'{case_name}: should contain "{case["should_contain"]}"'
        if 'should_not_contain' in case:
            assert case['should_not_contain'] not in _strip_tags(result), \
                f'{case_name}: should NOT contain "{case["should_not_contain"]}"'


# ===============================================================
# strip_start_bleed
# ===============================================================

class TestStripStartBleed:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['strip_start_bleed']
    ])
    def test_strip_start_bleed(self, case_name):
        case = DATA['strip_start_bleed'][case_name]
        result = strip_start_bleed(case['html'], case['own_title'])

        if 'should_contain' in case:
            assert case['should_contain'] in _strip_tags(result), \
                f'{case_name}: should contain "{case["should_contain"]}"'
        if 'should_not_contain' in case:
            assert case['should_not_contain'] not in _strip_tags(result), \
                f'{case_name}: should NOT contain "{case["should_not_contain"]}"'


# ===============================================================
# strip_end_bleed
# ===============================================================

class TestStripEndBleed:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['strip_end_bleed']
    ])
    def test_strip_end_bleed(self, case_name):
        case = DATA['strip_end_bleed'][case_name]
        result = strip_end_bleed(case['html'], case['next_title'])

        if 'should_contain' in case:
            assert case['should_contain'] in _strip_tags(result), \
                f'{case_name}: should contain "{case["should_contain"]}"'
        if 'should_not_contain' in case:
            assert case['should_not_contain'] not in _strip_tags(result), \
                f'{case_name}: should NOT contain "{case["should_not_contain"]}"'


# ===============================================================
# postprocess_article (full pipeline)
# ===============================================================

class TestPostprocessArticle:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['postprocess_article']
    ])
    def test_full_pipeline(self, case_name):
        case = DATA['postprocess_article'][case_name]
        result = postprocess_article(case['html'], case['article'])
        result_text = _strip_tags(result)

        if 'should_contain' in case:
            for text in case['should_contain']:
                assert text in result_text, \
                    f'{case_name}: should contain "{text}"'
        if 'should_not_contain' in case:
            for text in case['should_not_contain']:
                assert text not in result_text, \
                    f'{case_name}: should NOT contain "{text}"'

    def test_auto_extracted_passthrough_preserves_comment(self):
        """AUTO-EXTRACTED HTML should pass through completely unchanged."""
        html = '<!-- AUTO-EXTRACTED: pymupdf -->\n<p>Raw text.</p>'
        article = {'title': 'Title', 'section': 'Articles'}
        result = postprocess_article(html, article)
        assert result == html


# ===============================================================
# Helper functions
# ===============================================================

class TestClean:
    def test_basic(self):
        assert _clean('Hello, World!') == 'hello world'

    def test_collapses_whitespace(self):
        assert _clean('  hello   world  ') == 'hello world'

    def test_strips_html_entities(self):
        assert _clean('café résumé') == 'caf rsum'


class TestStripTags:
    def test_basic(self):
        assert _strip_tags('<p>Hello <em>world</em></p>') == 'Hello world'

    def test_no_tags(self):
        assert _strip_tags('plain text') == 'plain text'


class TestFindFirstBodyHeading:
    def test_finds_introduction(self):
        html = '<h2>Abstract</h2><p>abs</p><h2>Introduction</h2><p>body</p>'
        pos = _find_first_body_heading(html)
        assert html[pos:].startswith('<h2>Introduction')

    def test_skips_abstract(self):
        html = '<h2>Abstract</h2><p>abs</p><h2>Method</h2><p>body</p>'
        pos = _find_first_body_heading(html)
        assert html[pos:].startswith('<h2>Method')

    def test_returns_end_when_no_heading(self):
        html = '<p>Just paragraphs.</p>'
        assert _find_first_body_heading(html) == len(html)


class TestTextToRegex:
    def test_builds_pattern(self):
        rx = _text_to_regex('Being and Time')
        assert rx is not None
        assert rx.search('being   and   time')

    def test_none_for_empty(self):
        assert _text_to_regex('') is None


class TestTitleInText:
    def test_positive(self):
        assert _title_in_text('Being Sexual', 'the paper being sexual revisited')

    def test_negative(self):
        assert not _title_in_text('Completely Different', 'being sexual revisited')

    def test_empty_title(self):
        assert _title_in_text('', 'anything')


class TestFindBlockByText:
    def test_finds_matching_block(self):
        html = '<p>First paragraph.</p><p>Target text here.</p><p>Third.</p>'
        start, end = _find_block_by_text(html, 'Target text here')
        assert start is not None
        assert 'Target text' in html[start:end]

    def test_returns_none_when_not_found(self):
        html = '<p>First paragraph.</p><p>Second paragraph.</p>'
        start, end = _find_block_by_text(html, 'Nonexistent text that is not here')
        assert start is None


# ===============================================================
# strip_subtitle
# ===============================================================

class TestStripSubtitle:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['strip_subtitle']
    ])
    def test_strip_subtitle(self, case_name):
        case = DATA['strip_subtitle'][case_name]
        result = strip_subtitle(case['html'], case['subtitle'])

        if 'should_contain' in case:
            assert case['should_contain'] in _strip_tags(result), \
                f'{case_name}: should contain "{case["should_contain"]}"'
        if 'should_not_contain' in case:
            assert case['should_not_contain'] not in _strip_tags(result), \
                f'{case_name}: should NOT contain "{case["should_not_contain"]}"'


# ===============================================================
# strip_running_headers / page numbers
# ===============================================================

class TestStripRunningHeaders:

    @pytest.mark.parametrize('case_name', [
        k for k in DATA['strip_running_headers']
    ])
    def test_strip_running_headers(self, case_name):
        case = DATA['strip_running_headers'][case_name]
        html = case['html']
        result = RUNNING_HEADER_RE.sub('', html)
        result = PAGE_NUMBER_RE.sub('', result)

        if 'should_contain' in case:
            targets = case['should_contain'] if isinstance(case['should_contain'], list) else [case['should_contain']]
            for target in targets:
                assert target in result, \
                    f'{case_name}: should contain "{target}"'
        if 'should_not_contain' in case:
            assert case['should_not_contain'] not in result, \
                f'{case_name}: should NOT contain "{case["should_not_contain"]}"'


class TestVerifyPostprocessed:
    def test_no_warnings_for_good_output(self):
        raw = '<h1>My Title</h1><p>Body text with enough content to pass the threshold check easily. This paragraph needs to be long enough to exceed the minimum content threshold of 100 characters.</p>'
        final = '<p>Body text with enough content to pass the threshold check easily. This paragraph needs to be long enough to exceed the minimum content threshold of 100 characters.</p>'
        article = {'title': 'My Title', 'section': 'Articles'}
        warnings = verify_postprocessed(raw, final, article)
        assert warnings == []

    def test_warns_on_empty_output(self):
        raw = '<h1>Title</h1><p>Body.</p>'
        final = '<p>X</p>'
        article = {'title': 'Title', 'section': 'Articles'}
        warnings = verify_postprocessed(raw, final, article)
        assert any('EMPTY_OUTPUT' in w for w in warnings)


# ===============================================================
# _fix_bio_contact_spacing_soup (QA #9796)
# ===============================================================

class TestFixBioContactSpacing:

    def test_separates_email_and_orcid(self):
        """Email immediately followed by <br/> then ORCID URL gets '. ' separator."""
        soup = _parse('<p>Contact: user@example.com<br/>https://orcid.org/0009-0007-1502-7192</p>')
        _fix_bio_contact_spacing_soup(soup)
        text = soup.get_text()
        assert 'user@example.com. https://orcid.org/' in text

    def test_preserves_br_without_email(self):
        """<br/> not preceded by email is left alone."""
        html = '<p>Some text<br/>More text</p>'
        soup = _parse(html)
        _fix_bio_contact_spacing_soup(soup)
        assert soup.find('br') is not None

    def test_preserves_br_without_url(self):
        """<br/> after email but before non-URL text is left alone."""
        html = '<p>Contact: user@example.com<br/>Some other text</p>'
        soup = _parse(html)
        _fix_bio_contact_spacing_soup(soup)
        assert soup.find('br') is not None

    def test_real_example_from_qa(self):
        """Exact pattern from QA #9796."""
        html = ('<p><strong>Sheba Boakye-Duah</strong> is a doctoral candidate. '
                'Contact: SB2967@live.mdx.ac.uk<br/>https://orcid.org/0009-0007-1502-7192</p>')
        soup = _parse(html)
        _fix_bio_contact_spacing_soup(soup)
        text = soup.get_text()
        assert 'SB2967@live.mdx.ac.uk. https://orcid.org/' in text
        assert soup.find('br') is None  # <br> should be replaced


# ===============================================================
# is_citation_like — citation vs note/caption classification
# ===============================================================

class TestIsCitationLike:
    """Ensure is_citation_like correctly distinguishes real citations from
    captions, notes, and other text that happens to contain names + years."""

    # --- True positives: real citations ---

    def test_standard_citation(self):
        """Standard author-year citation with publisher."""
        assert is_citation_like(
            'Beauvoir, S. de (2018). The Ethics of Ambiguity. New York: Open Road Media.'
        )

    def test_citation_with_journal(self):
        """Citation with journal name."""
        assert is_citation_like(
            'Smith, J. (2015). On being. Journal of Existential Analysis, 26(1), 45-60.'
        )

    def test_citation_with_doi(self):
        """Citation with DOI."""
        assert is_citation_like(
            'Jones, A. (2020). Title. Publisher. doi:10.1234/test'
        )

    def test_citation_with_pages(self):
        """Citation with page range."""
        assert is_citation_like(
            'Adams, M.C. (2001). Practising phenomenology. Existential Analysis 12.1, pp.65-84.'
        )

    def test_citation_parenthesised_year_only(self):
        """Author + parenthesised year is enough (common short citation)."""
        assert is_citation_like(
            'Freud, S. (1914). Remembering, Repeating and Working Through.'
        )

    # --- True negatives: NOT citations ---

    def test_photo_caption_with_date(self):
        """Photo caption with names and date — NOT a citation (QA #8833)."""
        assert not is_citation_like(
            'Ann-Helen and Martti Siirala at the Inner Circle Seminar, '
            "Regent's College, London on his 80th birthday, 30 November 2002."
        )

    def test_event_description(self):
        """Event description with names and year — NOT a citation."""
        assert not is_citation_like(
            'Paper presented by John Smith at the Annual Conference, May 2019.'
        )

    def test_biographical_note(self):
        """Biographical note with year — NOT a citation."""
        assert not is_citation_like(
            'Dr Sarah Johnson joined the department in 2015 and has since led '
            'the clinical training programme.'
        )

    def test_short_note_with_year(self):
        """Short note referencing a year — NOT a citation."""
        assert not is_citation_like(
            'See my introduction to the 2002 edition.'
        )

    def test_too_short(self):
        """Very short text isn't classifiable."""
        assert not is_citation_like('See note 1.')
