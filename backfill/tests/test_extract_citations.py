"""Tests for extract_citations.py — section-level extraction logic.

Tests the routing decision: when a JATS body has both "Notes" and
"References" sections, items under "Notes" must ALL become notes
regardless of whether they look citation-like.

IMPORTANT: These tests encode what the CORRECT behaviour should be.
If a test fails, the CODE is wrong. Fix the implementation, not the test.

Run: pytest backfill/tests/test_extract_citations.py -v
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.extract_citations import extract_from_jats

# Minimal JATS skeleton. {body_sections} is replaced with test-specific
# <sec> elements.
JATS_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<article xmlns="http://jats.nlm.nih.gov/publishing/1.3/">
<front>
  <article-meta>
    <contrib-group>
      <contrib contrib-type="author">
        <name><given-names>Test</given-names><surname>Author</surname></name>
      </contrib>
    </contrib-group>
  </article-meta>
</front>
<body>
  <sec><title>Introduction</title>
    <p>Body text with references to notes.</p>
  </sec>
  {body_sections}
</body>
<back/>
</article>
"""


def _make_jats(body_sections: str) -> Path:
    """Write a JATS file with the given body sections, return its path."""
    xml = JATS_TEMPLATE.format(body_sections=body_sections)
    tmp = tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w',
                                      delete=False, encoding='utf-8')
    tmp.write(xml)
    tmp.close()
    return Path(tmp.name)


# Example notes that look citation-like (have author + year) but should
# stay as notes when a separate References section exists.
CITATION_LIKE_NOTES = [
    'Stadlen 2003a: 163–166, n. 77.',
    'Heidegger 1987: 6; 2001 [1987]: 5.',
    'Freud GW 4: 179; SE 6: 162; my translation.',
    'Boss 1963a [1957b]: 92.',
    'See Esterson 1972; 1976a.',
]

# Pure notes (commentary, ibid, etc.)
PURE_NOTES = [
    'Ibid.',
    'See Midrash, Bereshit Rabbah, XIX, 3.',
    'I do not know any commentator who mentions this.',
]

# Proper references (author-year-title-publisher)
REFERENCES = [
    'Heidegger, M. (1927). Being and Time. Oxford: Blackwell.',
    'Freud, S. (1900). The Interpretation of Dreams. London: Hogarth.',
    'Boss, M. (1963). Psychoanalysis and Daseinsanalysis. New York: Basic Books.',
]


class TestNotesWithSeparateReferences:
    """When both Notes and References sections exist, ALL Notes items
    must stay as notes — never promoted to citations."""

    def test_citation_like_notes_stay_as_notes(self):
        """Notes that look like citations must remain notes when a
        separate References section exists."""
        notes_items = '\n'.join(f'<p>{n}</p>' for n in
                                CITATION_LIKE_NOTES + PURE_NOTES)
        ref_items = '\n'.join(f'<p>{r}</p>' for r in REFERENCES)

        jats_path = _make_jats(f"""
        <sec><title>Notes</title>
          {notes_items}
        </sec>
        <sec><title>References</title>
          {ref_items}
        </sec>
        """)

        try:
            result = extract_from_jats(jats_path)
            all_notes = CITATION_LIKE_NOTES + PURE_NOTES

            # Every note-section item must be in notes, not citations
            for note in all_notes:
                assert note in result['notes'], (
                    f'Note should NOT be promoted to citation: {note!r}'
                )

            # References must be in citations
            for ref in REFERENCES:
                assert ref in result['citations'], (
                    f'Reference should be in citations: {ref!r}'
                )

            # No notes should appear in citations
            for note in all_notes:
                assert note not in result['citations'], (
                    f'Note wrongly promoted to citation: {note!r}'
                )
        finally:
            os.unlink(jats_path)

    def test_references_still_extracted(self):
        """Items under References heading must be extracted as citations."""
        notes_items = '<p>Ibid.</p>'
        ref_items = '\n'.join(f'<p>{r}</p>' for r in REFERENCES)

        jats_path = _make_jats(f"""
        <sec><title>Notes</title>
          {notes_items}
        </sec>
        <sec><title>References</title>
          {ref_items}
        </sec>
        """)

        try:
            result = extract_from_jats(jats_path)
            assert len(result['citations']) == len(REFERENCES)
            for ref in REFERENCES:
                assert ref in result['citations']
        finally:
            os.unlink(jats_path)


class TestNotesWithoutSeparateReferences:
    """When only a Notes section exists (no separate References),
    citation-like items ARE promoted to citations (existing behaviour)."""

    def test_citation_like_items_promoted_when_no_refs_section(self):
        """Without a separate References section, citation-like items
        under Notes should become citations (refs might be mixed in)."""
        all_items = CITATION_LIKE_NOTES + PURE_NOTES
        items_html = '\n'.join(f'<p>{n}</p>' for n in all_items)

        jats_path = _make_jats(f"""
        <sec><title>Notes</title>
          {items_html}
        </sec>
        """)

        try:
            result = extract_from_jats(jats_path)

            # Citation-like items should be promoted to citations
            # (at least some of them — they pass is_citation_like)
            promoted = [n for n in CITATION_LIKE_NOTES
                        if n in result['citations']]
            assert len(promoted) > 0, (
                'Without a separate References section, citation-like notes '
                'should be promoted to citations'
            )

            # Pure notes should stay as notes
            for note in PURE_NOTES:
                assert note in result['notes'], (
                    f'Pure note should remain a note: {note!r}'
                )
        finally:
            os.unlink(jats_path)
