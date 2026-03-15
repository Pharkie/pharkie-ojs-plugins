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

from backfill.generate_xml import generate_xml, lookup_doi, SECTIONS
from backfill.verify_split import extract_title_words, verify_split

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
        self.xml_str = generate_xml(self.toc_data, doi_registry={})
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
        assert br_pub.get('access_status') == '0'

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
        xml_str = generate_xml(toc_data, doi_registry={})
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        subjects = articles[1].findall('.//pkp:subject', NS)
        assert len(subjects) == 2
        assert subjects[0].text == 'Existential Therapy'
        assert subjects[1].text == 'Clinical Practice'

    def test_disciplines_emitted(self):
        toc_data = make_toc_data()
        toc_data['articles'][1]['disciplines'] = ['Psychotherapy', 'Philosophy']
        xml_str = generate_xml(toc_data, doi_registry={})
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        disciplines = articles[1].findall('.//pkp:discipline', NS)
        assert len(disciplines) == 2
        assert disciplines[0].text == 'Psychotherapy'

    def test_pages_emitted(self):
        toc_data = make_toc_data()
        xml_str = generate_xml(toc_data, doi_registry={})
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        pages = articles[1].find('.//pkp:pages', NS)
        assert pages is not None
        assert pages.text == '5-20'

    def test_no_subjects_without_data(self):
        toc_data = make_toc_data()
        xml_str = generate_xml(toc_data, doi_registry={})
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        # Article 0 (editorial) has no subjects
        subjects = articles[0].findall('.//pkp:subject', NS)
        assert len(subjects) == 0

    def test_citations_from_enrichment(self):
        """Citations from enrichment sidecar appear in XML."""
        import tempfile
        toc_data = make_toc_data()
        toc_data['articles'][1]['_review_id'] = 'v37i1a1'

        # Create a temp dir with enrichment.json
        with tempfile.TemporaryDirectory() as tmpdir:
            toc_path = os.path.join(tmpdir, 'toc.json')
            with open(toc_path, 'w') as f:
                json.dump(toc_data, f)

            enrichment = {
                '_generated': '2026-01-01T00:00:00Z',
                '_model': 'test',
                '_version': 1,
                'articles': {
                    'v37i1a1': {
                        'references': [
                            {'author': 'Heidegger, M.', 'year': 1927, 'title': 'Being and Time', 'internal': False},
                        ],
                        'geographical_context': 'UK',
                        'era_focus': 'Contemporary',
                    }
                }
            }
            enrichment_path = os.path.join(tmpdir, 'enrichment.json')
            with open(enrichment_path, 'w') as f:
                json.dump(enrichment, f)

            xml_str = generate_xml(toc_data, doi_registry={}, toc_json_path=toc_path)
            root = ET.fromstring(xml_str)
            articles = root.findall('.//pkp:article', NS)
            citations = articles[1].findall('.//pkp:citation', NS)
            assert len(citations) == 1
            assert 'Heidegger' in citations[0].text
            assert '1927' in citations[0].text

            # Check coverage
            coverage = articles[1].find('.//pkp:coverage', NS)
            assert coverage is not None
            assert 'UK' in coverage.text
            assert 'Contemporary' in coverage.text


class TestXmlWithDois:
    """Test DOI preservation through the XML generation chain."""

    def test_doi_emitted_in_xml(self):
        toc_data = make_toc_data()
        registry = {
            ('existential therapy in practice: a clinical perspective', '37', '1'):
                '10.65828/test-doi-123',
        }
        xml_str = generate_xml(toc_data, doi_registry=registry)
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        # Article at index 1 should have the DOI
        pub = articles[1].find('.//pkp:publication', NS)
        doi_ids = [e for e in pub.findall('pkp:id', NS) if e.get('type') == 'doi']
        assert len(doi_ids) == 1
        assert doi_ids[0].text == '10.65828/test-doi-123'
        assert doi_ids[0].get('advice') == 'update'

    def test_no_doi_when_not_in_registry(self):
        toc_data = make_toc_data()
        xml_str = generate_xml(toc_data, doi_registry={})
        root = ET.fromstring(xml_str)
        articles = root.findall('.//pkp:article', NS)
        for article in articles:
            pub = article.find('.//pkp:publication', NS)
            doi_ids = [e for e in pub.findall('pkp:id', NS) if e.get('type') == 'doi']
            assert len(doi_ids) == 0

    def test_editorial_doi_lookup_variant(self):
        """'Editorial' in TOC should match '37.1 editorial' in registry."""
        registry = {
            ('37.1 editorial', '37', '1'): '10.65828/editorial-doi',
        }
        doi = lookup_doi(registry, 'Editorial', 37, 1)
        assert doi == '10.65828/editorial-doi'

    def test_book_review_prefix_stripped(self):
        registry = {
            ('the meaning of life revisited', '37', '1'): '10.65828/br-doi',
        }
        doi = lookup_doi(registry, 'Book Review: The Meaning of Life Revisited', 37, 1)
        assert doi == '10.65828/br-doi'


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


