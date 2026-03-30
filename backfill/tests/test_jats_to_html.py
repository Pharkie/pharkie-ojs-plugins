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

from jats_to_html import jats_to_html


def _make_jats(back_content, body='<p>Body text.</p>'):
    """Build a minimal JATS XML string with given back-matter content."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<article>
  <body>{body}</body>
  <back>{back_content}</back>
</article>"""


def _html_from_back(back_content):
    """Generate HTML from a JATS fragment and return it."""
    jats_xml = _make_jats(back_content)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(jats_xml)
        f.flush()
        try:
            return jats_to_html(f.name)
        finally:
            os.unlink(f.name)


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
        # Should NOT have a single container wrapping both
        assert html.count('</div>\n\n<div class="jats-bios">') >= 1 or \
               html.count('</div>\n<div class="jats-bios">') >= 1

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
