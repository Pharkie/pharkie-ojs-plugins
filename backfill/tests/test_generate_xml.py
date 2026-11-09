"""Tests for pipe6_ojs_xml.py — OJS Native XML generation.

The JATS half moved to Harbour with pipe3 on 2026-08-18; it is
scripts/pipeline/tests/test_generate_jats.py there. What is left tests the
OJS import XML, which has no consumer outside this repo."""

import os
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.html_pipeline.pipe6_ojs_xml import (
    parse_date,
    split_author_name,
    generate_xml,
    load_jats_galley,
    SECTIONS,
)


class TestParseDate:
    def test_january_2026(self):
        assert parse_date('January 2026') == '2026-01-01'

    def test_july_2024(self):
        assert parse_date('July 2024') == '2024-07-01'

    def test_december_1999(self):
        assert parse_date('December 1999') == '1999-12-01'

    def test_none_returns_today(self):
        result = parse_date(None)
        # Should be a valid date string
        assert len(result) == 10
        assert result[4] == '-'

    def test_empty_returns_today(self):
        result = parse_date('')
        assert len(result) == 10

    def test_invalid_format_returns_today(self):
        result = parse_date('not a date')
        assert len(result) == 10


class TestSplitAuthorName:
    def test_simple_two_word(self):
        result = split_author_name('Kim Loliya')
        assert result == [('Kim', 'Loliya')]

    def test_particle_van(self):
        result = split_author_name('Emmy van Deurzen')
        assert result == [('Emmy', 'van Deurzen')]

    def test_particle_von(self):
        result = split_author_name('Carl von Weizsacker')
        assert result == [('Carl', 'von Weizsacker')]

    def test_middle_initial(self):
        result = split_author_name('Michael R. Montgomery')
        assert result == [('Michael R.', 'Montgomery')]

    def test_multiple_authors_ampersand(self):
        result = split_author_name('Sheba Boakye-Duah & Neresia Osbourne')
        assert len(result) == 2
        assert result[0] == ('Sheba', 'Boakye-Duah')
        assert result[1] == ('Neresia', 'Osbourne')

    def test_explicit_splits(self):
        assert split_author_name('Wilfried Ver Eecke') == [('Wilfried', 'Ver Eecke')]
        assert split_author_name('Yaqui Andrés Martínez Robles') == [('Yaqui Andrés', 'Martínez Robles')]
        assert split_author_name('Jo Bloggs & Wilfried Ver Eecke') == [('Jo', 'Bloggs'), ('Wilfried', 'Ver Eecke')]

    def test_ver_is_not_a_particle(self):
        assert split_author_name('Anna Ver Smith') == [('Anna Ver', 'Smith')]

    def test_single_name(self):
        result = split_author_name('Plato')
        assert result == [('', 'Plato')]

    def test_empty_string(self):
        result = split_author_name('')
        assert result == [('', '')]

    def test_none(self):
        result = split_author_name(None)
        assert result == [('', '')]


class TestSectionAccessStatus:
    def test_editorial_is_free(self):
        assert SECTIONS['Editorial']['access_status'] == '1'

    def test_articles_are_paywalled(self):
        assert SECTIONS['Articles']['access_status'] == '0'

    def test_book_review_editorial_is_free(self):
        assert SECTIONS['Book Review Editorial']['access_status'] == '1'

    def test_book_reviews_are_free(self):
        assert SECTIONS['Book Reviews']['access_status'] == '1'


class TestGenerateXmlStructure:
    """Test generate_xml() produces valid XML with correct structure."""

    def _minimal_toc(self):
        return {
            'source_pdf': '/tmp/test.pdf',
            'volume': 37,
            'issue': 1,
            'date': 'January 2026',
            'page_offset': 1,
            'total_pdf_pages': 100,
            'articles': [
                {
                    'title': 'Editorial',
                    'authors': None,
                    'section': 'Editorial',
                    'journal_page_start': 3,
                    'journal_page_end': 6,
                    'pdf_page_start': 4,
                    'pdf_page_end': 7,
                },
                {
                    'title': 'Test Article',
                    'authors': 'John Doe',
                    'section': 'Articles',
                    'journal_page_start': 7,
                    'journal_page_end': 20,
                    'pdf_page_start': 8,
                    'pdf_page_end': 21,
                    'abstract': 'This is a test abstract.',
                    'keywords': ['existentialism', 'therapy'],
                },
            ],
        }

    def test_valid_xml(self):
        xml_str = generate_xml(self._minimal_toc())
        # Should parse without errors
        root = ET.fromstring(xml_str)
        assert root.tag == '{http://pkp.sfu.ca}issues'

    def test_issue_metadata(self):
        xml_str = generate_xml(self._minimal_toc())
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}

        issue = root.find('ojs:issue', ns)
        assert issue is not None

        ident = issue.find('ojs:issue_identification', ns)
        assert ident.find('ojs:volume', ns).text == '37'
        assert ident.find('ojs:number', ns).text == '1'
        assert ident.find('ojs:year', ns).text == '2026'

    def test_sections_present(self):
        xml_str = generate_xml(self._minimal_toc())
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}

        sections = root.findall('.//ojs:section', ns)
        # Should have Editorial and Articles sections (the ones used)
        assert len(sections) == 2

    def test_articles_present(self):
        xml_str = generate_xml(self._minimal_toc())
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}

        articles = root.findall('.//ojs:article', ns)
        assert len(articles) == 2

    def test_article_title(self):
        xml_str = generate_xml(self._minimal_toc())
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}

        titles = root.findall('.//ojs:publication/ojs:title', ns)
        title_texts = [t.text for t in titles]
        assert 'Editorial' in title_texts
        assert 'Test Article' in title_texts

    def test_date_published(self):
        xml_str = generate_xml(self._minimal_toc())
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}

        date = root.find('.//ojs:date_published', ns)
        assert date.text == '2026-01-01'

    def test_only_used_sections_included(self):
        """Sections not referenced by any article should be omitted."""
        toc = self._minimal_toc()
        # Only Editorial and Articles used — no Book Reviews
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}

        section_titles = [s.find('ojs:title', ns).text
                          for s in root.findall('.//ojs:section', ns)]
        assert 'Book Reviews' not in section_titles
        assert 'Book Review Editorial' not in section_titles


class TestXmlEscaping:
    def test_ampersand_in_title(self):
        toc = {
            'volume': 2, 'issue': 1, 'date': 'January 2020',
            'articles': [{
                'title': 'Love & Death',
                'authors': None,
                'section': 'Articles',
                'journal_page_start': 1,
                'journal_page_end': 10,
                'pdf_page_start': 1,
                'pdf_page_end': 10,
            }],
        }
        xml_str = generate_xml(toc)
        # Should be valid XML (ampersand escaped)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        title = root.find('.//ojs:publication/ojs:title', ns)
        assert title.text == 'Love & Death'

    def test_angle_brackets_in_title(self):
        toc = {
            'volume': 2, 'issue': 1, 'date': 'January 2020',
            'articles': [{
                'title': 'The <Other> Problem',
                'authors': None,
                'section': 'Articles',
                'journal_page_start': 1,
                'journal_page_end': 10,
                'pdf_page_start': 1,
                'pdf_page_end': 10,
            }],
        }
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        title = root.find('.//ojs:publication/ojs:title', ns)
        assert title.text == 'The <Other> Problem'

    def test_quotes_in_title(self):
        toc = {
            'volume': 2, 'issue': 1, 'date': 'January 2020',
            'articles': [{
                'title': 'On "Being" and "Nothingness"',
                'authors': None,
                'section': 'Editorial',
                'journal_page_start': 1,
                'journal_page_end': 5,
                'pdf_page_start': 1,
                'pdf_page_end': 5,
            }],
        }
        xml_str = generate_xml(toc)
        # Should parse without error
        root = ET.fromstring(xml_str)


class TestDoiInXml:
    """Test that DOIs appear correctly in generated XML (read from JATS)."""

    def test_doi_from_jats(self, tmp_path):
        """DOI in JATS file should appear in generated XML."""
        # Create a split PDF and JATS file
        pdf_path = tmp_path / '01-test-article.pdf'
        pdf_path.write_bytes(b'%PDF-fake')
        jats_path = tmp_path / '01-test-article.jats.xml'
        jats_path.write_text(
            '<?xml version="1.0"?>'
            '<article><front><article-meta>'
            '<article-id pub-id-type="publisher-id">999</article-id>'
            '<article-id pub-id-type="doi">10.65828/test123</article-id>'
            '</article-meta></front></article>')

        toc = {
            'volume': 37, 'issue': 1, 'date': 'January 2026',
            'articles': [{
                'title': 'Test Article',
                'authors': 'John Doe',
                'section': 'Articles',
                'split_pdf': str(pdf_path),
                'journal_page_start': 1,
                'journal_page_end': 10,
                'pdf_page_start': 1,
                'pdf_page_end': 10,
            }],
        }
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        ids = root.findall('.//ojs:publication/ojs:id', ns)
        doi_ids = [i for i in ids if i.get('type') == 'doi']
        assert len(doi_ids) == 1
        assert doi_ids[0].text == '10.65828/test123'
        assert doi_ids[0].get('advice') == 'update'

    def test_no_doi_when_no_jats(self):
        """No DOI when no JATS file exists."""
        toc = {
            'volume': 1, 'issue': 1, 'date': 'January 1990',
            'articles': [{
                'title': 'Old Article',
                'authors': None,
                'section': 'Articles',
                'journal_page_start': 1,
                'journal_page_end': 10,
                'pdf_page_start': 1,
                'pdf_page_end': 10,
            }],
        }
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        ids = root.findall('.//ojs:publication/ojs:id', ns)
        doi_ids = [i for i in ids if i.get('type') == 'doi']
        assert len(doi_ids) == 0

    def test_publisher_id_not_in_xml(self, tmp_path):
        """Publisher-ID in JATS should NOT appear in OJS XML (restore_ids.py handles it)."""
        pdf_path = tmp_path / '01-test.pdf'
        pdf_path.write_bytes(b'%PDF-fake')
        jats_path = tmp_path / '01-test.jats.xml'
        jats_path.write_text(
            '<?xml version="1.0"?>'
            '<article><front><article-meta>'
            '<article-id pub-id-type="publisher-id">1234</article-id>'
            '</article-meta></front></article>')
        toc = {
            'volume': 37, 'issue': 1, 'date': 'January 2026',
            'articles': [{
                'title': 'Test', 'authors': 'A B', 'section': 'Articles',
                'split_pdf': str(pdf_path),
                'pdf_page_start': 1, 'pdf_page_end': 1,
            }],
        }
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        # Internal IDs should be placeholders with advice="ignore", not publisher-id
        all_ids = root.findall('.//ojs:id[@type="internal"]', ns)
        for aid in all_ids:
            assert aid.get('advice') == 'ignore'
            assert aid.text != '1234'  # publisher-id should not leak into XML

    def test_no_publisher_id_advice_ignore(self):
        """Without publisher-ID, advice should be 'ignore'."""
        toc = {
            'volume': 1, 'issue': 1, 'date': 'January 1990',
            'articles': [{
                'title': 'Old', 'authors': None, 'section': 'Articles',
                'pdf_page_start': 1, 'pdf_page_end': 1,
            }],
        }
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        all_ids = root.findall('.//ojs:article/ojs:id[@type="internal"]', ns)
        assert len(all_ids) >= 1
        for aid in all_ids:
            assert aid.get('advice') == 'ignore'

    def test_issue_doi_from_toc(self):
        """Issue DOI in toc_data should appear in XML."""
        toc = {
            'volume': 36, 'issue': 2, 'date': 'July 2025',
            'issue_doi': '10.65828/test-issue-doi',
            'articles': [{
                'title': 'Article', 'authors': 'A B', 'section': 'Articles',
                'pdf_page_start': 1, 'pdf_page_end': 1,
            }],
        }
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        issue_ids = root.findall('.//ojs:issue/ojs:id[@type="doi"]', ns)
        assert len(issue_ids) == 1
        assert issue_ids[0].text == '10.65828/test-issue-doi'
        assert issue_ids[0].get('advice') == 'update'

    def test_issue_id_from_toc(self):
        """Issue ID in toc_data should appear with advice='ignore' (OJS assigns its own)."""
        toc = {
            'volume': 36, 'issue': 2, 'date': 'July 2025',
            'issue_id': 475,
            'articles': [{
                'title': 'Article', 'authors': 'A B', 'section': 'Articles',
                'pdf_page_start': 1, 'pdf_page_end': 1,
            }],
        }
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        issue_ids = root.findall('.//ojs:issue/ojs:id[@type="internal"]', ns)
        assert len(issue_ids) == 1
        assert issue_ids[0].text == '475'
        assert issue_ids[0].get('advice') == 'ignore'


# ── JATS XML galley ──


class TestJatsGalley:
    """Test that JATS XML is included as a third galley alongside PDF and HTML."""

    def _make_article_files(self, tmp_path):
        """Create minimal PDF, HTML galley, and JATS files for testing."""
        pdf_path = tmp_path / '01-test-article.pdf'
        pdf_path.write_bytes(b'%PDF-fake')
        html_path = tmp_path / '01-test-article.galley.html'
        html_path.write_text('<p>Test content</p>')
        jats_path = tmp_path / '01-test-article.jats.xml'
        jats_path.write_text(
            '<?xml version="1.0"?>'
            '<article><front><article-meta>'
            '<article-id pub-id-type="doi">10.65828/test123</article-id>'
            '</article-meta></front><body><p>Test</p></body></article>')
        return str(pdf_path)

    def _make_toc(self, pdf_path):
        return {
            'volume': 37, 'issue': 1, 'date': 'January 2026',
            'articles': [{
                'title': 'Test Article',
                'authors': 'John Doe',
                'section': 'Articles',
                'split_pdf': pdf_path,
                'journal_page_start': 1,
                'journal_page_end': 10,
                'pdf_page_start': 1,
                'pdf_page_end': 10,
            }],
        }

    def test_jats_galley_present(self, tmp_path):
        """JATS XML should appear as a galley when .jats.xml file exists."""
        pdf_path = self._make_article_files(tmp_path)
        xml_str = generate_xml(self._make_toc(pdf_path))
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        galleys = root.findall('.//ojs:article_galley', ns)
        galley_names = [g.find('ojs:name', ns).text for g in galleys]
        assert 'JATS XML' in galley_names

    def test_jats_galley_sequence(self, tmp_path):
        """JATS XML galley should have seq=2 (after PDF=0 and Full Text=1)."""
        pdf_path = self._make_article_files(tmp_path)
        xml_str = generate_xml(self._make_toc(pdf_path))
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        galleys = root.findall('.//ojs:article_galley', ns)
        jats_galley = [g for g in galleys
                       if g.find('ojs:name', ns).text == 'JATS XML'][0]
        assert jats_galley.find('ojs:seq', ns).text == '2'

    def test_three_galleys_when_all_files_exist(self, tmp_path):
        """Should produce PDF, Full Text, and JATS XML galleys."""
        pdf_path = self._make_article_files(tmp_path)
        xml_str = generate_xml(self._make_toc(pdf_path))
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        galleys = root.findall('.//ojs:article_galley', ns)
        galley_names = sorted(g.find('ojs:name', ns).text for g in galleys)
        assert galley_names == ['Full Text', 'JATS XML', 'PDF']

    def test_jats_submission_file_present(self, tmp_path):
        """JATS should have a corresponding submission_file with xml extension."""
        pdf_path = self._make_article_files(tmp_path)
        xml_str = generate_xml(self._make_toc(pdf_path))
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        sub_files = root.findall('.//ojs:submission_file', ns)
        file_names = [sf.find('ojs:name', ns).text for sf in sub_files]
        jats_files = [n for n in file_names if n.endswith('.jats.xml')]
        assert len(jats_files) == 1
        assert jats_files[0] == '01-test-article.jats.xml'

    def test_jats_file_extension_is_xml(self, tmp_path):
        """The embedded file should have extension='xml'."""
        pdf_path = self._make_article_files(tmp_path)
        xml_str = generate_xml(self._make_toc(pdf_path))
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        sub_files = root.findall('.//ojs:submission_file', ns)
        for sf in sub_files:
            name = sf.find('ojs:name', ns).text
            if name.endswith('.jats.xml'):
                file_el = sf.find('ojs:file', ns)
                assert file_el.get('extension') == 'xml'
                break
        else:
            raise AssertionError('No JATS submission_file found')

    def test_no_jats_galley_without_jats_file(self, tmp_path):
        """No JATS galley when .jats.xml file doesn't exist."""
        pdf_path = tmp_path / '01-test.pdf'
        pdf_path.write_bytes(b'%PDF-fake')
        toc = {
            'volume': 1, 'issue': 1, 'date': 'January 1990',
            'articles': [{
                'title': 'Old Article', 'authors': None,
                'section': 'Articles',
                'split_pdf': str(pdf_path),
                'journal_page_start': 1, 'journal_page_end': 10,
                'pdf_page_start': 1, 'pdf_page_end': 10,
            }],
        }
        xml_str = generate_xml(toc)
        root = ET.fromstring(xml_str)
        ns = {'ojs': 'http://pkp.sfu.ca'}
        galleys = root.findall('.//ojs:article_galley', ns)
        galley_names = [g.find('ojs:name', ns).text for g in galleys]
        assert 'JATS XML' not in galley_names


class TestLoadJatsGalley:
    """Test the load_jats_galley() helper function."""

    def test_loads_existing_file(self, tmp_path):
        pdf_path = tmp_path / 'article.pdf'
        pdf_path.write_bytes(b'%PDF-fake')
        jats_path = tmp_path / 'article.jats.xml'
        jats_path.write_text('<article>test</article>')
        result = load_jats_galley(str(pdf_path))
        assert result == '<article>test</article>'

    def test_returns_none_when_no_jats(self, tmp_path):
        pdf_path = tmp_path / 'article.pdf'
        pdf_path.write_bytes(b'%PDF-fake')
        result = load_jats_galley(str(pdf_path))
        assert result is None

    def test_returns_none_for_none_path(self):
        assert load_jats_galley(None) is None

    def test_returns_none_for_nonexistent_pdf(self):
        assert load_jats_galley('/tmp/nonexistent.pdf') is None


# ── JATS <product> generation (pipe3) ──


class TestOrcid:
    """Per-article `orcids` map: {author name as written in "authors": URL}.

    Kwon (37.2) was the first author in the corpus to carry an ORCID; going
    forward the editors request them from all authors, so the map must flow
    into the JATS contrib-id (harvesters, pipe13) and the native import XML
    (a full reimport must not drop what author_settings holds)."""

    ORCID = 'https://orcid.org/0009-0006-0635-9649'

    def _article(self, **overrides):
        article = {
            'title': 'Test Article', 'authors': 'Jun Woo Kwon',
            'section': 'Articles',
            'journal_page_start': 249, 'journal_page_end': 258,
            'orcids': {'Jun Woo Kwon': ORCID_URL},
        }
        article.update(overrides)
        return article

    def test_import_xml_orcid_element(self):
        toc = {
            'source_pdf': '/tmp/test.pdf', 'volume': 37, 'issue': 2,
            'date': 'July 2026', 'page_offset': 1, 'total_pdf_pages': 100,
            'articles': [self._article()],
        }
        root = ET.fromstring(generate_xml(toc))
        ns = {'ojs': 'http://pkp.sfu.ca'}
        author = root.find('.//ojs:author', ns)
        orcid = author.find('ojs:orcid', ns)
        assert orcid is not None and orcid.text == ORCID_URL
        # native.xsd sequence: email before orcid
        tags = [c.tag.rsplit('}', 1)[-1] for c in author]
        assert tags.index('email') < tags.index('orcid')

    def test_import_xml_no_orcid_without_map(self):
        toc = {
            'source_pdf': '/tmp/test.pdf', 'volume': 37, 'issue': 2,
            'date': 'July 2026', 'page_offset': 1, 'total_pdf_pages': 100,
            'articles': [self._article(orcids={})],
        }
        root = ET.fromstring(generate_xml(toc))
        ns = {'ojs': 'http://pkp.sfu.ca'}
        assert root.find('.//ojs:author/ojs:orcid', ns) is None


ORCID_URL = TestOrcid.ORCID
