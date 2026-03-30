"""Test that bio extraction groups bio + contact paragraphs together."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.extract_citations import extract_from_jats
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
        """)
        with tempfile.NamedTemporaryFile(suffix='.jats.xml', mode='w', delete=False) as f:
            f.write(jats)
            f.flush()
            result = extract_from_jats(Path(f.name))
        os.unlink(f.name)
        assert len(result['bios']) == 1, f"Expected 1 bio, got {len(result['bios'])}: {result['bios']}"
        assert 'Paul Gordon is a member' in result['bios'][0]
        assert 'psgordon@talk21.com' in result['bios'][0]
