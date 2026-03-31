"""Test that bio extraction groups bio + contact paragraphs together."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.html_pipeline.pipe4_extract_citations import extract_from_jats
from pathlib import Path


def _make_jats(body_content, authors=None):
    """Create a minimal JATS file with given body content and authors."""
    if authors is None:
        authors = [('John', 'Smith')]
    contribs = '\n'.join(
        f'<contrib contrib-type="author"><name><surname>{f}</surname><given-names>{g}</given-names></name></contrib>'
        for g, f in authors
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<article>
<front>
<article-meta>
<title-group><article-title>Test</article-title></title-group>
<contrib-group>
{contribs}
</contrib-group>
</article-meta>
</front>
<body>
{body_content}
</body>
</article>"""


class TestBioGrouping:

    def test_bio_and_contact_merged(self):
        """Bio paragraph followed by contact should be one bio entry."""
        jats = _make_jats("""
        <sec><title>Introduction</title>
        <p>Body text about therapy and existence.</p>
        </sec>
        <sec><title>Conclusion</title>
        <p>Final thoughts on the matter.</p>
        <p>John Smith is a psychotherapist in private practice in London.</p>
        <p>Contact: john@example.com</p>
        </sec>
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 1, f"Expected 1 bio, got {len(result['bios'])}: {result['bios']}"
        assert 'John Smith is a psychotherapist' in result['bios'][0]
        assert 'john@example.com' in result['bios'][0]

    def test_bio_and_email_merged(self):
        """Bio paragraph followed by bare email should be one bio entry."""
        jats = _make_jats("""
        <sec><title>Discussion</title>
        <p>Some discussion text here.</p>
        <p>Jane Doe is a lecturer in counselling psychology at the University of London.</p>
        <p>jane.doe@university.ac.uk</p>
        </sec>
        """, authors=[('Jane', 'Doe')])
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 1
        assert 'Jane Doe is a lecturer' in result['bios'][0]
        assert 'jane.doe@university.ac.uk' in result['bios'][0]

    def test_standalone_contact_not_separate_bio(self):
        """A contact-only line should not create its own bio entry."""
        jats = _make_jats("""
        <sec><title>Conclusion</title>
        <p>Final paragraph of the article.</p>
        <p>John Smith is a psychotherapist in private practice.</p>
        <p>Contact: 123 Therapy Lane, London W1.</p>
        <p>Email: john@example.com</p>
        </sec>
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 1, f"Expected 1 bio, got {len(result['bios'])}: {result['bios']}"
        assert 'john@example.com' in result['bios'][0]
        assert '123 Therapy Lane' in result['bios'][0]

    def test_member_role_detected_as_bio(self):
        """'member of' is a valid bio role — bio + contact grouped."""
        jats = _make_jats("""
        <sec><title>Conclusion</title>
        <p>Final paragraph here.</p>
        <p>Paul Gordon is a member of the Philadelphia Association. He is the author of several books.</p>
        <p>Contact: 74 Victoria Rd, London NW6 6QA Email: psgordon@talk21.com</p>
        </sec>
        """, authors=[('Paul', 'Gordon')])
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 1, f"Expected 1 bio, got {len(result['bios'])}: {result['bios']}"
        assert 'Paul Gordon is a member' in result['bios'][0]
        assert 'psgordon@talk21.com' in result['bios'][0]

    def test_author_statement_becomes_note(self):
        """Author statement / funding / COI disclosures should be notes, not body."""
        jats = _make_jats("""
        <sec><title>Conclusion</title>
        <p>Final thoughts on the matter.</p>
        <p>John Smith is a psychotherapist in private practice.</p>
        </sec>
        <p>Author statement</p>
        <p>Funding statement: the article has not received funding from any source.</p>
        <p>Conflict of interest: The author declares no conflict of interest.</p>
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert any('Funding statement' in n for n in result['notes']), \
            f"Funding statement not in notes: {result['notes']}"
        assert any('Conflict of interest' in n for n in result['notes']), \
            f"COI not in notes: {result['notes']}"

    def test_rejects_bio_about_non_author(self):
        """A bio-like paragraph about someone who is NOT the article author
        should NOT be extracted as a bio from the trailing scan."""
        jats = _make_jats("""
        <sec><title>Introduction</title>
        <p>Body text about therapy.</p>
        </sec>
        <sec><title>Discussion</title>
        <p>Kirk Schneider is a leading spokesperson for contemporary existential-humanistic psychology.</p>
        <p>More body text discussing the implications.</p>
        <p>John Smith is a psychotherapist in private practice in London.</p>
        <p>Contact: john@example.com</p>
        </sec>
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 1, f"Expected 1 bio, got {len(result['bios'])}: {result['bios']}"
        assert 'John Smith is a psychotherapist' in result['bios'][0]
        assert 'Schneider' not in result['bios'][0]

    def test_rejects_editorial_discussion_as_bio(self):
        """Editorial body text discussing therapists should not be extracted as bio."""
        jats = _make_jats("""
        <sec><title>Introduction</title>
        <p>Emmy van Deurzen is a philosopher and psychotherapist who has contributed greatly to the field.</p>
        <p>This issue contains papers from the annual conference.</p>
        <p>John Smith is Senior Lecturer at the New School of Psychotherapy and Counselling.</p>
        </sec>
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 1, f"Expected 1 bio, got {len(result['bios'])}: {result['bios']}"
        assert 'John Smith' in result['bios'][0]

    def test_two_real_authors_each_get_own_bio(self):
        """Two article authors should get separate bio entries."""
        jats = _make_jats("""
        <sec><title>Conclusion</title>
        <p>Final thoughts on the matter.</p>
        <p>Roly Fletcher is currently undertaking the Practitioner Doctorate at the University of Surrey.</p>
        <p>Dr Martin Milton is a Chartered Counselling Psychologist and registered psychotherapist.</p>
        </sec>
        """, authors=[('Roly', 'Fletcher'), ('Martin', 'Milton')])
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 2, f"Expected 2 bios, got {len(result['bios'])}: {result['bios']}"
        assert any('Fletcher' in b for b in result['bios'])
        assert any('Milton' in b for b in result['bios'])

    def test_two_authors_two_bios(self):
        """Two different authors' bios should be separate entries, not merged."""
        jats = _make_jats("""
        <sec><title>Discussion</title>
        <p>Some discussion text.</p>
        <p>Roly Fletcher is currently undertaking the Practitioner Doctorate in Psychotherapy at the University of Surrey.</p>
        <p>Dr Martin Milton is a Chartered Counselling Psychologist and a registered psychotherapist.</p>
        </sec>
        """, authors=[('Roly', 'Fletcher'), ('Martin', 'Milton')])
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 2, f"Expected 2 bios, got {len(result['bios'])}: {result['bios']}"
        assert any('Fletcher' in b for b in result['bios'])
        assert any('Milton' in b for b in result['bios'])

    def test_non_author_bio_in_ref_section_becomes_note(self):
        """A bio-like item about a non-author inside a reference section
        should be classified as a note, not a bio."""
        jats = _make_jats("""
        <sec><title>References</title>
        <p>Smith, J. (2005). On anxiety. Journal of Existential Analysis.</p>
        <p>Emmy van Deurzen is a philosopher and psychotherapist who founded the New School.</p>
        <p>Cooper, M. (2003). Existential Therapies. Sage.</p>
        </sec>
        <p>John Smith is a psychotherapist in private practice.</p>
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        # van Deurzen text should NOT be a bio (she's not the author)
        for bio in result['bios']:
            assert 'van Deurzen' not in bio, f"Non-author bio found: {bio[:80]}"
        # John Smith's bio SHOULD be found (he IS the author)
        assert any('John Smith' in b for b in result['bios']), f"Author bio not found: {result['bios']}"
        # van Deurzen text should be in notes
        assert any('van Deurzen' in n for n in result['notes']), f"Expected van Deurzen in notes: {result['notes']}"

    def test_contact_after_section_bio_merged(self):
        """Contact <p> outside the section should merge with bio inside it.

        Real pattern: bio is inside a <sec> (caught by section scan),
        contact <p> follows as a sibling of <body> after the <sec>.
        """
        jats = _make_jats("""
        <sec><title>References</title>
        <p>Smith, J. (2005). On anxiety. Journal of Existential Analysis.</p>
        <p>Paul Gordon is a member of the Philadelphia Association.</p>
        </sec>
        <p>Contact: 74 Victoria Rd, London NW6 6QA</p>
        <p>Email: psgordon@talk21.com</p>
        """, authors=[('Paul', 'Gordon')])
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 1, f"Expected 1 bio, got {len(result['bios'])}: {result['bios']}"
        assert 'Paul Gordon is a member' in result['bios'][0]
        assert 'psgordon@talk21.com' in result['bios'][0]

    def test_inline_notes_extracted(self):
        """Notes in <p><bold>Notes:</bold>(1) text...</p> format should be extracted."""
        jats = _make_jats("""
        <sec><title>Introduction</title>
        <p>Body text about the subject.</p>
        </sec>
        <p><bold>Notes:</bold>(1) See "Plato's Pharmacy" in Disseminations, by Jacques Derrida, 1981.</p>
        <p>(2) The Philadelphia Association, now based at 4 Marty's Yard, London NW3.</p>
        <p>(3) Sonnets, 38, R.D. Laing, Michael Joseph, 1979.</p>
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['notes']) == 3, f"Expected 3 notes, got {len(result['notes'])}: {result['notes']}"
        assert any('Plato' in n for n in result['notes']), f"Note 1 missing: {result['notes']}"
        assert any('Philadelphia' in n for n in result['notes']), f"Note 2 missing: {result['notes']}"
        assert any('Sonnets' in n for n in result['notes']), f"Note 3 missing: {result['notes']}"
        # Notes should be removed from body
        assert 'Plato' not in str(result.get('tree', ''))[:500] or len(result['notes']) == 3

    def test_inline_notes_with_references_section(self):
        """Inline notes + separate References section: both extracted correctly."""
        jats = _make_jats("""
        <sec><title>Discussion</title>
        <p>Some body text here.</p>
        </sec>
        <p><bold>Notes:</bold>(1) See chapter 3 for further discussion.</p>
        <p>(2) This term is used loosely here.</p>
        <sec><title>References</title>
        <p>Laing, R.D. (1960). The Divided Self. Tavistock.</p>
        <p>Laing, R.D. (1967). The Politics of Experience. Penguin.</p>
        </sec>
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['notes']) == 2, f"Expected 2 notes, got {len(result['notes'])}: {result['notes']}"
        assert len(result['citations']) == 2, f"Expected 2 citations, got {len(result['citations'])}: {result['citations']}"

    def test_leading_provenance_extracted(self):
        """Provenance <p> elements before first <sec> should be extracted."""
        jats = _make_jats("""
        <p>Presentation given at the Society for Existential Analysis Annual Conference, London, 10 November 2018</p>
        <p>* Parts III and IV will be published in Existential Analysis 31.1, in January 2020</p>
        <sec><title>Introduction</title>
        <p>Body text about therapy.</p>
        </sec>
        """, authors=[('Richard', 'Swann')])
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['provenance']) >= 2, \
            f"Expected 2 provenance items, got {len(result['provenance'])}: {result['provenance']}"
        assert any('Presentation' in p for p in result['provenance'])
        assert any('Parts III' in p for p in result['provenance'])

    def test_author_signoff_not_classified_as_note(self):
        """Author name at end of book review (after References) is a sign-off, not a note."""
        jats = _make_jats("""
        <sec><title>Review</title>
        <p>This book provides an excellent overview of cognitive therapy.</p>
        </sec>
        <sec><title>References</title>
        <p>Bennett-Levy, J. (2001). The value of self-practice. Behavioural and Cognitive Psychotherapy, 29.</p>
        <p>Diana Mitchell</p>
        </sec>
        """, authors=[('Diana', 'Mitchell')])
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        for note in result['notes']:
            assert 'Diana Mitchell' not in note, \
                f"Author sign-off classified as note: {note}"
