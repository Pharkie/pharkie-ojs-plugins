"""Tests for jats_to_html back-matter rendering.

Verifies that JATS back-matter elements (bios, notes, provenance) produce
correct HTML structure. Fixtures are inline JATS fragments — no external
files needed.

Run: pytest backfill/tests/test_jats_to_html.py -v
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from html_pipeline.pipe5_galley_html import jats_to_html


def _make_jats(back_content, body='<p>Body text.</p>', front_content=''):
    """Build a minimal JATS XML string with given back-matter content."""
    front = f'<front><article-meta>{front_content}</article-meta></front>' if front_content else ''
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<article>
  {front}
  <body>{body}</body>
  <back>{back_content}</back>
</article>"""


def _html_from_jats(back_content='', front_content='', body='<p>Body text.</p>'):
    """Generate HTML from a JATS fragment and return it."""
    jats_xml = _make_jats(back_content, body=body, front_content=front_content)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(jats_xml)
        f.flush()
        try:
            return jats_to_html(f.name)
        finally:
            os.unlink(f.name)


def _html_from_back(back_content):
    """Generate HTML from a JATS fragment and return it."""
    return _html_from_jats(back_content=back_content)


# ── Author bios ──

class TestBioRendering:
    """Each <bio> must render as a separate <div class="jats-bios">."""

    def test_single_bio(self):
        html = _html_from_back('<bio><p>Alice is a researcher.</p></bio>')
        divs = re.findall(r'<div class="jats-bios">', html)
        assert len(divs) == 1
        assert 'Alice is a researcher.' in html

    def test_two_bios_separate_divs(self):
        html = _html_from_back(
            '<bio><p>Alice is a researcher.</p></bio>'
            '<bio><p>Bob is a clinician.</p></bio>'
        )
        divs = re.findall(r'<div class="jats-bios">', html)
        assert len(divs) == 2, f'Expected 2 bio divs, got {len(divs)}'
        assert 'Alice is a researcher.' in html
        assert 'Bob is a clinician.' in html

    def test_three_bios_separate_divs(self):
        html = _html_from_back(
            '<bio><p>Alice.</p></bio>'
            '<bio><p>Bob.</p></bio>'
            '<bio><p>Carol.</p></bio>'
        )
        divs = re.findall(r'<div class="jats-bios">', html)
        assert len(divs) == 3

    def test_bio_not_nested_in_container(self):
        """Each bio div should be a top-level back-matter element, not nested."""
        html = _html_from_back(
            '<bio><p>Alice.</p></bio>'
            '<bio><p>Bob.</p></bio>'
        )
        # Should have two separate bio divs, not one container wrapping both
        assert html.count('<div class="jats-bios">') == 2

    def test_empty_bio_skipped(self):
        html = _html_from_back(
            '<bio><p>Alice.</p></bio>'
            '<bio><p>  </p></bio>'
        )
        divs = re.findall(r'<div class="jats-bios">', html)
        assert len(divs) == 1


# ── Notes ──

class TestNotesRendering:
    """Notes should render in a single <div class="jats-notes"> with <ol>."""

    def test_notes_as_ordered_list(self):
        html = _html_from_back(
            '<fn-group>'
            '<fn><p>First note.</p></fn>'
            '<fn><p>Second note.</p></fn>'
            '</fn-group>'
        )
        assert '<div class="jats-notes">' in html
        assert '<h2>Notes</h2>' in html
        assert '<ol>' in html
        assert html.count('<li>') == 2

    def test_empty_notes_skipped(self):
        html = _html_from_back('<fn-group></fn-group>')
        assert 'jats-notes' not in html


# ── Provenance ──

class TestProvenanceRendering:
    """Provenance notes render in <div class="jats-provenance">."""

    def test_provenance_rendered(self):
        html = _html_from_back(
            '<notes notes-type="provenance"><p>Based on a talk given at...</p></notes>'
        )
        assert '<div class="jats-provenance">' in html
        assert 'Based on a talk given at...' in html

    def test_non_provenance_notes_skipped(self):
        html = _html_from_back(
            '<notes notes-type="other"><p>Something else.</p></notes>'
        )
        assert 'jats-provenance' not in html


# ── Ordering ──

class TestBackMatterOrder:
    """Back matter should render in order: notes, bios, provenance."""

    def test_order_provenance_bios_notes(self):
        """Matches PDF layout: provenance → bios → notes."""
        html = _html_from_back(
            '<fn-group><fn><p>A note.</p></fn></fn-group>'
            '<bio><p>Author bio.</p></bio>'
            '<notes notes-type="provenance"><p>Provenance.</p></notes>'
        )
        prov_pos = html.index('jats-provenance')
        bios_pos = html.index('jats-bios')
        notes_pos = html.index('jats-notes')
        assert prov_pos < bios_pos < notes_pos


# ── Reviewed work (product) ──

PRODUCT_FULL = (
    '<product>'
    '<person-group person-group-type="author">'
    '<name><surname>Chopra</surname><given-names>Samir</given-names></name>'
    '</person-group>'
    '<year>2024</year>'
    '<source>Anxiety: A philosophical guide</source>'
    '<publisher-loc>Princeton</publisher-loc>'
    '<publisher-name>Princeton University Press</publisher-name>'
    '</product>'
)

PRODUCT_EDITORS = (
    '<product>'
    '<person-group person-group-type="editor">'
    '<name><surname>van Deurzen</surname><given-names>Emmy</given-names></name>'
    '<name><surname>Iacovou</surname><given-names>Susan</given-names></name>'
    '</person-group>'
    '<year>2013</year>'
    '<source>Existential Perspectives on Relationship Therapy</source>'
    '<publisher-loc>Basingstoke</publisher-loc>'
    '<publisher-name>Palgrave Macmillan</publisher-name>'
    '</product>'
)


class TestProductRendering:
    """<product> renders as <div class="jats-reviewed-work"> before body."""

    def test_full_product_renders(self):
        html = _html_from_jats(front_content=PRODUCT_FULL)
        assert '<div class="jats-reviewed-work">' in html
        assert 'Samir Chopra (2024).' in html
        assert '<em>Anxiety: A philosophical guide.</em>' in html
        assert 'Princeton: Princeton University Press' in html

    def test_product_before_body(self):
        html = _html_from_jats(front_content=PRODUCT_FULL)
        product_pos = html.index('jats-reviewed-work')
        body_pos = html.index('Body text.')
        assert product_pos < body_pos

    def test_multiple_authors(self):
        html = _html_from_jats(front_content=PRODUCT_EDITORS)
        assert 'Emmy van Deurzen, Susan Iacovou' in html

    def test_no_product_no_div(self):
        html = _html_from_jats(front_content='<title-group><article-title>Test</article-title></title-group>')
        assert 'jats-reviewed-work' not in html

    def test_no_front_no_div(self):
        html = _html_from_back('<bio><p>Author bio.</p></bio>')
        assert 'jats-reviewed-work' not in html

    def test_question_mark_title_no_double_period(self):
        product = (
            '<product>'
            '<person-group person-group-type="author">'
            '<name><surname>Cox</surname><given-names>Gary</given-names></name>'
            '</person-group>'
            '<year>2024</year>'
            '<source>Is hell other people?</source>'
            '</product>'
        )
        html = _html_from_jats(front_content=product)
        assert 'Is hell other people?</em>' in html
        assert 'people?.' not in html

class TestSubheadingRendering:
    """h3 subheadings must survive the JATS round-trip (pipe3 → pipe5)."""

    def test_h3_preserved_as_nested_sec(self):
        """h3 inside an h2 section produces a nested <sec> in JATS."""
        body = (
            '<sec><title>Main Section</title>'
            '<sec><title>Subsection</title>'
            '<p>Content under subsection.</p>'
            '</sec></sec>'
        )
        html = _html_from_jats(body=body)
        assert '<h2>Main Section</h2>' in html
        assert '<h3>Subsection</h3>' in html
        assert 'Content under subsection.' in html

    def test_multiple_h3_in_section(self):
        """Multiple subsections within one section all render as h3."""
        body = (
            '<sec><title>Poems</title>'
            '<sec><title>Inspiration</title><p>Text A.</p></sec>'
            '<sec><title>Audience response</title><p>Text B.</p></sec>'
            '</sec>'
        )
        html = _html_from_jats(body=body)
        assert '<h3>Inspiration</h3>' in html
        assert '<h3>Audience response</h3>' in html
        assert '<h2>Poems</h2>' in html

    def test_h3_not_promoted_to_h2(self):
        """Nested sec must NOT render as h2."""
        body = (
            '<sec><title>Top</title>'
            '<sec><title>Nested</title><p>Inner.</p></sec>'
            '</sec>'
        )
        html = _html_from_jats(body=body)
        assert html.count('<h2>') == 1  # only "Top"
        assert '<h3>Nested</h3>' in html


    def test_publisher_without_location(self):
        product = (
            '<product>'
            '<year>2020</year>'
            '<source>Some Book</source>'
            '<publisher-name>Open Press</publisher-name>'
            '</product>'
        )
        html = _html_from_jats(front_content=product)
        assert 'Open Press' in html
        assert ': Open Press' not in html
