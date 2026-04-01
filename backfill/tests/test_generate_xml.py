"""Tests for backfill/generate_xml.py — OJS Native XML generation and JATS generation."""

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
    SECTIONS,
)
from backfill.html_pipeline.pipe3_generate_jats import (
    generate_article_jats,
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


# ── JATS <product> generation (pipe3) ──

class TestJatsProduct:
    """Test that pipe3 generates <product> for book reviews."""

    def _book_review_article(self, **overrides):
        article = {
            'title': 'Book Review: Anxiety',
            'authors': 'Andrew Miller',
            'section': 'Book Reviews',
            'book_title': 'Anxiety: A philosophical guide',
            'book_author': 'Samir Chopra',
            'book_year': 2024,
            'publisher': 'Princeton: Princeton University Press',
            'journal_page_start': 201,
            'journal_page_end': 203,
        }
        article.update(overrides)
        return article

    def _parse_jats(self, article, **kwargs):
        jats_str = generate_article_jats(
            article, volume=36, issue=1,
            date_published='2025-01-01', html_path=None, doi=None,
            **kwargs,
        )
        return ET.fromstring(jats_str)

    def test_product_present_for_book_review(self):
        root = self._parse_jats(self._book_review_article())
        product = root.find('.//{*}product')
        assert product is not None

    def test_product_absent_for_regular_article(self):
        article = {
            'title': 'Test Article', 'authors': 'John Doe',
            'section': 'Articles',
            'journal_page_start': 1, 'journal_page_end': 10,
        }
        root = self._parse_jats(article)
        product = root.find('.//{*}product')
        assert product is None

    def test_product_absent_without_book_title(self):
        root = self._parse_jats(self._book_review_article(book_title=None))
        product = root.find('.//{*}product')
        assert product is None

    def test_product_source(self):
        root = self._parse_jats(self._book_review_article())
        source = root.find('.//{*}product/{*}source')
        assert source is not None
        assert source.text == 'Anxiety: A philosophical guide'

    def test_product_year(self):
        root = self._parse_jats(self._book_review_article())
        year = root.find('.//{*}product/{*}year')
        assert year is not None
        assert year.text == '2024'

    def test_product_author(self):
        root = self._parse_jats(self._book_review_article())
        pg = root.find('.//{*}product/{*}person-group')
        assert pg is not None
        assert pg.get('person-group-type') == 'author'
        surname = pg.find('.//{*}surname')
        assert surname.text == 'Chopra'

    def test_product_editor_annotation(self):
        root = self._parse_jats(self._book_review_article(
            book_author='Emmy van Deurzen & Susan Iacovou (eds.)',
        ))
        pg = root.find('.//{*}product/{*}person-group')
        assert pg.get('person-group-type') == 'editor'
        names = pg.findall('{*}name')
        assert len(names) == 2

    def test_product_publisher_split(self):
        root = self._parse_jats(self._book_review_article())
        pub_loc = root.find('.//{*}product/{*}publisher-loc')
        pub_name = root.find('.//{*}product/{*}publisher-name')
        assert pub_loc.text == 'Princeton'
        assert pub_name.text == 'Princeton University Press'

    def test_product_publisher_no_location(self):
        root = self._parse_jats(self._book_review_article(
            publisher='Open Press',
        ))
        pub_loc = root.find('.//{*}product/{*}publisher-loc')
        pub_name = root.find('.//{*}product/{*}publisher-name')
        assert pub_loc is None
        assert pub_name.text == 'Open Press'
