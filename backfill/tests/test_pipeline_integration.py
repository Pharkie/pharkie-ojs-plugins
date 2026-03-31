"""Integration test: exercises the pipeline chain from TOC data through XML generation.

Uses a synthetic toc.json (no real PDF needed) to verify that the full chain
of generate_xml produces valid, well-structured OJS Native XML with correct
sections, articles, authors, DOIs, and access status.
"""

import os
import sys
import json
import tempfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.html_pipeline.pipe6_ojs_xml import generate_xml, SECTIONS
from backfill.split_pipeline.split3_verify import extract_title_words, verify_split

# Namespace for OJS XML
NS = {'pkp': 'http://pkp.sfu.ca'}


def make_toc_data(articles=None):
    """Build a minimal toc_data dict for testing."""
    if articles is None:
        articles = [
            {
                'title': 'Editorial',
                'authors': 'Jane Smith',
                'section': 'Editorial',
                'journal_page_start': 3,
                'journal_page_end': 4,
                'pdf_page_start': 5,
                'pdf_page_end': 6,
            },
            {
                'title': 'Existential Therapy in Practice: A Clinical Perspective',
                'authors': 'John Doe & Emmy van Deurzen',
                'section': 'Articles',
                'journal_page_start': 5,
                'journal_page_end': 20,
                'pdf_page_start': 7,
                'pdf_page_end': 22,
                'abstract': 'This article explores existential therapy.',
                'keywords': ['existential therapy', 'clinical practice', 'phenomenology'],
            },
            {
                'title': 'Obituary: A Notable Figure (1930-2025)',
                'authors': 'Alice Brown',
                'section': 'Editorial',
                'journal_page_start': 21,
                'journal_page_end': 22,
                'pdf_page_start': 23,
                'pdf_page_end': 24,
            },
            {
                'title': 'Book Reviews',
                'authors': 'Bob Green',
                'section': 'Book Review Editorial',
                'journal_page_start': 23,
                'journal_page_end': 24,
                'pdf_page_start': 25,
                'pdf_page_end': 26,
            },
            {
                'title': 'Book Review: The Meaning of Life Revisited',
                'authors': 'Carol White',
                'section': 'Book Reviews',
                'journal_page_start': 25,
                'journal_page_end': 28,
                'pdf_page_start': 27,
                'pdf_page_end': 30,
                'book_title': 'The Meaning of Life Revisited',
                'book_author': 'D. Philosopher',
                'book_year': 2024,
                'publisher': 'London: Academic Press',
            },
        ]
    return {
        'source_pdf': '/tmp/test-issue.pdf',
        'volume': 37,
        'issue': 1,
        'date': 'January 2026',
        'page_offset': 2,
        'total_pdf_pages': 35,
        'articles': articles,
    }


class TestFullXmlGeneration:
    """Golden-file style test: generate XML from toc_data, parse it, verify structure."""

    def setup_method(self):
        self.toc_data = make_toc_data()
        self.xml_str = generate_xml(self.toc_data, )
        self.root = ET.fromstring(self.xml_str)

    def test_xml_is_valid(self):
        assert self.root.tag == '{http://pkp.sfu.ca}issues'

    def test_issue_identification(self):
        issue = self.root.find('.//pkp:issue', NS)
        assert issue is not None
        ident = issue.find('pkp:issue_identification', NS)
        assert ident.find('pkp:volume', NS).text == '37'
        assert ident.find('pkp:number', NS).text == '1'
        assert ident.find('pkp:year', NS).text == '2026'

    def test_date_published(self):
        issue = self.root.find('.//pkp:issue', NS)
        date = issue.find('pkp:date_published', NS)
        assert date.text == '2026-01-01'

    def test_sections_only_used_ones(self):
        sections = self.root.findall('.//pkp:section', NS)
        refs = [s.get('ref') for s in sections]
        assert 'ED' in refs
        assert 'ART' in refs
        assert 'bookeditorial' in refs
        assert 'BR' in refs

    def test_article_count(self):
        articles = self.root.findall('.//pkp:article', NS)
        assert len(articles) == 5

    def test_editorial_is_open_access(self):
        articles = self.root.findall('.//pkp:article', NS)
        editorial_pub = articles[0].find('.//pkp:publication', NS)
        assert editorial_pub.get('access_status') == '1'
        assert editorial_pub.get('section_ref') == 'ED'

    def test_article_is_paywalled(self):
        articles = self.root.findall('.//pkp:article', NS)
        art_pub = articles[1].find('.//pkp:publication', NS)
        assert art_pub.get('access_status') == '0'
        assert art_pub.get('section_ref') == 'ART'

    def test_article_has_abstract(self):
        articles = self.root.findall('.//pkp:article', NS)
        abstract = articles[1].find('.//pkp:abstract', NS)
        assert abstract is not None
        assert 'existential therapy' in abstract.text

    def test_article_has_keywords(self):
        articles = self.root.findall('.//pkp:article', NS)
        keywords = articles[1].findall('.//pkp:keyword', NS)
        kw_texts = [k.text for k in keywords]
        assert 'phenomenology' in kw_texts

    def test_multiple_authors_parsed(self):
        articles = self.root.findall('.//pkp:article', NS)
        authors = articles[1].findall('.//pkp:author', NS)
        assert len(authors) == 2
        # First author: John Doe
        given0 = authors[0].find('pkp:givenname', NS)
        family0 = authors[0].find('pkp:familyname', NS)
        assert given0.text == 'John'
        assert family0.text == 'Doe'
        # Second author: Emmy van Deurzen
        given1 = authors[1].find('pkp:givenname', NS)
        family1 = authors[1].find('pkp:familyname', NS)
        assert given1.text == 'Emmy'
        assert family1.text == 'van Deurzen'

    def test_book_review_section(self):
        articles = self.root.findall('.//pkp:article', NS)
        br_pub = articles[4].find('.//pkp:publication', NS)
        assert br_pub.get('section_ref') == 'BR'
        assert br_pub.get('access_status') == '1'

    def test_book_review_editorial_is_open(self):
        articles = self.root.findall('.//pkp:article', NS)
        bre_pub = articles[3].find('.//pkp:publication', NS)
        assert bre_pub.get('access_status') == '1'
        assert bre_pub.get('section_ref') == 'bookeditorial'

    def test_copyright_holder_set(self):
        articles = self.root.findall('.//pkp:article', NS)
        copyright_holder = articles[1].find('.//pkp:copyrightHolder', NS)
        assert copyright_holder is not None
        assert 'John Doe' in copyright_holder.text

    def test_copyright_year(self):
        articles = self.root.findall('.//pkp:article', NS)
        year = articles[1].find('.//pkp:copyrightYear', NS)
        assert year.text == '2026'

    def test_no_galleys_without_pdfs(self):
        """Without split_pdf paths, no galleys should be generated."""
        articles = self.root.findall('.//pkp:article', NS)
        for article in articles:
            galley = article.find('.//pkp:article_galley', NS)
            assert galley is None

    def test_obituary_classified_as_editorial(self):
        articles = self.root.findall('.//pkp:article', NS)
        obit_pub = articles[2].find('.//pkp:publication', NS)
        assert obit_pub.get('section_ref') == 'ED'
        assert obit_pub.get('access_status') == '1'


class TestXmlWithEnrichment:
    """Test subjects, disciplines, pages, and citations in XML output."""

    def test_subjects_emitted(self):
        toc_data = make_toc_data()
        toc_data['articles'][1]['subjects'] = ['Existential Therapy', 'Clinical Practice']
        xml_str = generate_xml(toc_data, )
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        subjects = articles[1].findall('.//pkp:subject', NS)
        assert len(subjects) == 2
        assert subjects[0].text == 'Existential Therapy'
        assert subjects[1].text == 'Clinical Practice'

    def test_disciplines_emitted(self):
        toc_data = make_toc_data()
        toc_data['articles'][1]['disciplines'] = ['Psychotherapy', 'Philosophy']
        xml_str = generate_xml(toc_data, )
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        disciplines = articles[1].findall('.//pkp:discipline', NS)
        assert len(disciplines) == 2
        assert disciplines[0].text == 'Psychotherapy'

    def test_pages_emitted(self, tmp_path):
        toc_data = make_toc_data()
        # Create a JATS file with page numbers for article[1]
        jats_content = '<?xml version="1.0"?><article><front><article-meta><fpage>5</fpage><lpage>20</lpage></article-meta></front></article>'
        jats_file = tmp_path / '02-article.jats.xml'
        jats_file.write_text(jats_content)
        # Point split_pdf at a matching path so _load_jats_tree finds the JATS
        pdf_file = tmp_path / '02-article.pdf'
        pdf_file.write_bytes(b'')
        toc_data['articles'][1]['split_pdf'] = str(pdf_file)
        xml_str = generate_xml(toc_data, )
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        pages = articles[1].find('.//pkp:pages', NS)
        assert pages is not None
        assert pages.text == '5-20'

    def test_no_subjects_without_data(self):
        toc_data = make_toc_data()
        xml_str = generate_xml(toc_data, )
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        # Article 0 (editorial) has no subjects
        subjects = articles[0].findall('.//pkp:subject', NS)
        assert len(subjects) == 0

    def test_citations_from_jats(self):
        """Citations from JATS XML files appear in OJS XML."""
        import tempfile
        toc_data = make_toc_data()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake split PDF path so generate_xml can find the JATS file
            pdf_path = os.path.join(tmpdir, 'article.pdf')
            with open(pdf_path, 'w') as f:
                f.write('')  # empty placeholder
            toc_data['articles'][1]['split_pdf'] = pdf_path

            # Create a JATS file with references
            jats_path = os.path.join(tmpdir, 'article.jats.xml')
            with open(jats_path, 'w') as f:
                f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
                f.write('<article><back><ref-list>\n')
                f.write('<ref id="ref1"><mixed-citation>Heidegger, M. (1927). Being and Time.</mixed-citation></ref>\n')
                f.write('</ref-list></back></article>\n')

            toc_path = os.path.join(tmpdir, 'toc.json')
            with open(toc_path, 'w') as f:
                json.dump(toc_data, f)

            xml_str = generate_xml(toc_data, toc_json_path=toc_path)
            root = ET.fromstring(xml_str)
            articles = root.findall('.//pkp:article', NS)
            citations = articles[1].findall('.//pkp:citation', NS)
            assert len(citations) == 1
            assert 'Heidegger' in citations[0].text
            assert '1927' in citations[0].text


class TestXmlWithDois:
    """Test DOI reading from JATS through XML generation."""

    def test_doi_from_jats_in_xml(self, tmp_path):
        """DOI in JATS file should appear in generated XML."""
        toc_data = make_toc_data()
        # Create a JATS file with a DOI for the second article
        pdf_name = '02-existential-therapy-in-practice.pdf'
        pdf_path = tmp_path / pdf_name
        pdf_path.write_bytes(b'%PDF-fake')
        jats_path = tmp_path / '02-existential-therapy-in-practice.jats.xml'
        jats_path.write_text(
            '<?xml version="1.0"?>'
            '<article><front><article-meta>'
            '<article-id pub-id-type="doi">10.65828/test-doi-123</article-id>'
            '</article-meta></front></article>')
        toc_data['articles'][1]['split_pdf'] = str(pdf_path)
        xml_str = generate_xml(toc_data)
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        pub = articles[1].find('.//pkp:publication', NS)
        doi_ids = [e for e in pub.findall('pkp:id', NS) if e.get('type') == 'doi']
        assert len(doi_ids) == 1
        assert doi_ids[0].text == '10.65828/test-doi-123'
        assert doi_ids[0].get('advice') == 'update'

    def test_no_doi_when_no_jats(self):
        """No DOI when no JATS file exists."""
        toc_data = make_toc_data()
        xml_str = generate_xml(toc_data)
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        for article in articles:
            pub = article.find('.//pkp:publication', NS)
            doi_ids = [e for e in pub.findall('pkp:id', NS) if e.get('type') == 'doi']
            assert len(doi_ids) == 0



class TestJatsPreservation:
    """Test that generate_jats preserves DOI and publisher-id from existing JATS."""

    def test_doi_preserved_on_regeneration(self, tmp_path):
        """Regenerating JATS should preserve existing DOI."""
        from pathlib import Path
        from backfill.html_pipeline.pipe3_generate_jats import process_toc

        # Create toc.json
        toc = {
            'volume': 99, 'issue': 1, 'date': 'January 2026',
            'articles': [{
                'title': 'Test Article',
                'authors': 'John Doe',
                'section': 'Articles',
                'split_pdf': str(tmp_path / '01-test.pdf'),
                'pdf_page_start': 1, 'pdf_page_end': 1,
            }],
        }
        toc_path = tmp_path / 'toc.json'
        import json
        toc_path.write_text(json.dumps(toc))

        # Create split PDF (needed for slug)
        (tmp_path / '01-test.pdf').write_bytes(b'%PDF-fake')

        # Create existing JATS with DOI and publisher-id
        jats_path = tmp_path / '01-test.jats.xml'
        jats_path.write_text(
            '<?xml version="1.0"?>\n'
            '<article><front><article-meta>'
            '<article-id pub-id-type="publisher-id">5678</article-id>'
            '<article-id pub-id-type="doi">10.65828/preserved</article-id>'
            '</article-meta></front></article>')

        # Create HTML file (needed for body content)
        (tmp_path / '01-test.html').write_text('<p>Test body</p>')

        # Regenerate
        stats = process_toc(Path(toc_path))

        # Read regenerated JATS
        import xml.etree.ElementTree as ET
        tree = ET.parse(jats_path)
        doi_el = tree.find('.//{*}article-id[@pub-id-type="doi"]')
        pid_el = tree.find('.//{*}article-id[@pub-id-type="publisher-id"]')
        assert doi_el is not None and doi_el.text == '10.65828/preserved'
        assert pid_el is not None and pid_el.text == '5678'

    def test_no_doi_for_new_article(self, tmp_path):
        """New article without existing JATS should have no DOI."""
        from pathlib import Path
        from backfill.html_pipeline.pipe3_generate_jats import process_toc

        toc = {
            'volume': 99, 'issue': 1, 'date': 'January 2026',
            'articles': [{
                'title': 'Brand New',
                'authors': 'Jane Doe',
                'section': 'Articles',
                'split_pdf': str(tmp_path / '01-brand-new.pdf'),
                'pdf_page_start': 1, 'pdf_page_end': 1,
            }],
        }
        toc_path = tmp_path / 'toc.json'
        import json
        toc_path.write_text(json.dumps(toc))
        (tmp_path / '01-brand-new.pdf').write_bytes(b'%PDF-fake')
        (tmp_path / '01-brand-new.html').write_text('<p>New content</p>')

        stats = process_toc(Path(toc_path))

        import xml.etree.ElementTree as ET
        jats_path = tmp_path / '01-brand-new.jats.xml'
        assert jats_path.exists()
        tree = ET.parse(jats_path)
        doi_el = tree.find('.//{*}article-id[@pub-id-type="doi"]')
        assert doi_el is None  # No DOI for brand new article


class TestVerifySplitIntegration:
    """Verify extract_title_words works correctly on the toc_data fixture titles."""

    def test_editorial_has_no_words(self):
        assert extract_title_words('Editorial') == []

    def test_article_title_extracts_words(self):
        words = extract_title_words('Existential Therapy in Practice: A Clinical Perspective')
        assert 'existential' in words
        assert 'therapy' in words
        assert 'clinical' in words
        assert 'perspective' in words
        # Stop words filtered
        assert 'in' not in words
        assert 'a' not in words

    def test_book_review_strips_prefix(self):
        words = extract_title_words('Book Review: The Meaning of Life Revisited')
        assert 'meaning' in words
        assert 'life' in words
        # 'book' and 'review' should be stripped
        assert 'book' not in words
        assert 'review' not in words

    def test_obituary_strips_prefix(self):
        words = extract_title_words('Obituary: A Notable Figure (1930-2025)')
        assert 'notable' in words
        assert 'figure' in words
        assert 'obituary' not in words


