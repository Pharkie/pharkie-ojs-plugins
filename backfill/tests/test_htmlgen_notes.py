"""Tests for htmlgen note extraction completeness.

Verifies that raw HTML from htmlgen contains ALL numbered notes present
in the source PDF. Uses PyMuPDF to extract ground-truth notes from the
PDF, then compares against <li> items in the raw HTML <ol>.

IMPORTANT: These tests encode what the CORRECT behaviour should be.
If a test fails, the raw HTML is incomplete — re-run htmlgen with an
improved prompt. Do NOT weaken these tests to match broken output.

Run: pytest backfill/tests/test_htmlgen_notes.py -v
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

PRIVATE_OUTPUT = os.path.join(os.path.dirname(__file__), '..', 'private', 'output')

# Running headers/footers that appear in PDF text extraction and must be
# stripped from note content.
RUNNING_TEXT = re.compile(
    r'(Anthony Stadlen|The Madhouse of Being|'
    r'Existential Analysis.*?Journal of)',
    re.IGNORECASE,
)


def extract_pdf_notes(pdf_path):
    """Extract numbered notes from a PDF's Notes section using PyMuPDF.

    Returns dict mapping note number (int) to note text (str).
    Handles multi-line notes by joining continuation lines.
    Strips running headers/footers and page numbers.
    """
    doc = fitz.open(pdf_path)
    notes_text = ''
    in_notes = False

    for i in range(len(doc)):
        text = doc[i].get_text()
        if not in_notes:
            # Look for Notes heading (standalone line)
            lines = text.split('\n')
            for j, line in enumerate(lines):
                if line.strip() == 'Notes':
                    notes_text += '\n'.join(lines[j:])
                    in_notes = True
                    break
        else:
            if 'References' in text:
                # Find References heading and stop before it
                lines = text.split('\n')
                for j, line in enumerate(lines):
                    if line.strip() == 'References':
                        notes_text += '\n'.join(lines[:j])
                        in_notes = False
                        break
                if not in_notes:
                    break
                else:
                    notes_text += text
            else:
                notes_text += text
    doc.close()

    # Parse numbered notes
    lines = notes_text.split('\n')
    notes = {}
    current_num = None
    current_text = ''

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip standalone page numbers
        if re.match(r'^\d{2,3}$', line):
            continue
        # Skip running headers/footers
        if RUNNING_TEXT.fullmatch(line.strip()):
            continue

        m = re.match(r'^(\d+)\s+(.+)', line)
        if m and int(m.group(1)) == (current_num or 0) + 1:
            if current_num is not None:
                notes[current_num] = _clean_note(current_text)
            current_num = int(m.group(1))
            current_text = m.group(2)
        elif m and current_num is None and int(m.group(1)) == 1:
            current_num = 1
            current_text = m.group(2)
        else:
            if current_num is not None:
                current_text += ' ' + line

    if current_num is not None:
        notes[current_num] = _clean_note(current_text)

    return notes


def _clean_note(text):
    """Remove running headers/footers from note text."""
    text = RUNNING_TEXT.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def extract_html_notes(raw_html_path):
    """Extract <li> items from the Notes <ol> in a raw HTML file.

    Returns list of note texts (stripped of HTML tags).
    """
    with open(raw_html_path, encoding='utf-8') as f:
        html = f.read()

    # Find the Notes section
    notes_match = re.search(r'<h2>Notes</h2>', html, re.IGNORECASE)
    if not notes_match:
        return []

    # Find the end boundary (References heading or end of file)
    refs_match = re.search(r'<h2>References</h2>', html[notes_match.end():],
                           re.IGNORECASE)
    if refs_match:
        end = notes_match.end() + refs_match.start()
    else:
        end = len(html)

    notes_section = html[notes_match.start():end]
    lis = re.findall(r'<li>(.*?)</li>', notes_section, re.DOTALL)
    return [re.sub(r'<[^>]+>', '', li).strip() for li in lis]


def extract_highest_superscript(raw_html_path):
    """Find the highest <sup>N</sup> number in the body text.

    Only looks at body text before the Notes heading.
    """
    with open(raw_html_path, encoding='utf-8') as f:
        html = f.read()

    notes_match = re.search(r'<h2>Notes</h2>', html, re.IGNORECASE)
    body = html[:notes_match.start()] if notes_match else html

    sups = re.findall(r'<sup>(\d+)</sup>', body)
    if not sups:
        return 0
    return max(int(s) for s in sups)


def _normalize(text):
    """Normalize text for comparison: lowercase, strip non-alphanumeric."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(fitz is None, reason='PyMuPDF not installed')
class TestArticle8994Notes:
    """Test note extraction for article 8994 (The Madhouse of Being, 18.1)."""

    PDF_PATH = os.path.join(PRIVATE_OUTPUT, '18.1',
                            '11-the-madhouse-of-being.pdf')
    RAW_HTML_PATH = os.path.join(PRIVATE_OUTPUT, '18.1',
                                 '11-the-madhouse-of-being.raw.html')

    @pytest.fixture(autouse=True)
    def setup(self):
        if not os.path.exists(self.PDF_PATH):
            pytest.skip(f'PDF not found: {self.PDF_PATH}')
        if not os.path.exists(self.RAW_HTML_PATH):
            pytest.skip(f'Raw HTML not found: {self.RAW_HTML_PATH}')
        self.pdf_notes = extract_pdf_notes(self.PDF_PATH)
        self.html_notes = extract_html_notes(self.RAW_HTML_PATH)
        self.highest_sup = extract_highest_superscript(self.RAW_HTML_PATH)

    def test_pdf_has_177_notes(self):
        """Sanity check: PDF should have 177 numbered notes."""
        assert len(self.pdf_notes) == 177
        assert max(self.pdf_notes.keys()) == 177

    def test_html_note_count_matches_superscripts(self):
        """Number of <li> items must match highest <sup>N</sup> in body."""
        assert len(self.html_notes) == self.highest_sup, (
            f'HTML has {len(self.html_notes)} notes but body has '
            f'superscripts up to {self.highest_sup}'
        )

    def test_html_note_count_matches_pdf(self):
        """Number of <li> items must match number of PDF notes."""
        assert len(self.html_notes) == len(self.pdf_notes), (
            f'HTML has {len(self.html_notes)} notes but PDF has '
            f'{len(self.pdf_notes)}'
        )

    def test_each_pdf_note_present_in_html(self):
        """Every PDF note should have a matching <li> in the HTML.

        Matches by normalized text prefix (first 20 alphanumeric chars).
        Notes are matched in sequential order.
        """
        PREFIX_LEN = 20
        html_norms = [_normalize(n)[:PREFIX_LEN] for n in self.html_notes]
        missing = []

        html_idx = 0
        for num in sorted(self.pdf_notes.keys()):
            pdf_prefix = _normalize(self.pdf_notes[num])[:PREFIX_LEN]
            if not pdf_prefix:
                continue

            # Try to find this note at or after current html position
            found = False
            for j in range(html_idx, len(html_norms)):
                if html_norms[j][:PREFIX_LEN] == pdf_prefix:
                    html_idx = j + 1
                    found = True
                    break

            if not found:
                missing.append((num, self.pdf_notes[num][:80]))

        assert not missing, (
            f'{len(missing)} PDF notes missing from HTML:\n' +
            '\n'.join(f'  [{n}] {t}' for n, t in missing[:10])
        )
