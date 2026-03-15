"""Tests for backfill/parse_toc.py — TOC parsing logic."""

import re
from unittest.mock import MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.parse_toc import (
    parse_toc_text,
    classify_entry,
    find_toc_page,
    find_page_offset,
    parse_book_reviews,
    extract_reviewer_name,
    _strip_markers,
    _try_split_trailing_author,
    _get_known_authors,
    _strip_publisher_from_author,
    _validate_review_titles,
    _is_name_like,
    _is_reviewer_name_like,
    _match_known_author,
    SECTION_EDITORIAL,
    SECTION_ARTICLES,
    SECTION_BOOK_REVIEW_EDITORIAL,
    SECTION_BOOK_REVIEWS,
)


def _make_mock_doc(pages_text):
    """Create a mock PyMuPDF doc with given page texts."""
    doc = MagicMock()
    doc.__len__ = MagicMock(return_value=len(pages_text))
    mock_pages = []
    for text in pages_text:
        page = MagicMock()
        page.get_text.return_value = text
        mock_pages.append(page)
    doc.__getitem__ = MagicMock(side_effect=lambda i: mock_pages[i])
    return doc


class TestParseTocText:
    """Test parse_toc_text() with synthetic PyMuPDF-style output."""

    def test_simple_entries(self):
        """Basic entries: title with tab, then page number on next line."""
        toc_text = (
            "CONTENTS\n"
            "Editorial\t\n"
            "3\n"
            "Some Article Title\t\n"
            "7\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 2
        assert entries[0]['title'] == 'Editorial'
        assert entries[0]['page'] == 3
        assert entries[0]['author'] is None
        assert entries[1]['title'] == 'Some Article Title'
        assert entries[1]['page'] == 7

    def test_entry_with_author(self):
        """Entry followed by author name (no tab, not a number)."""
        toc_text = (
            "CONTENTS\n"
            "Therapy for the Revolution\t\n"
            "7\n"
            "Kim Loliya\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        assert entries[0]['title'] == 'Therapy for the Revolution'
        assert entries[0]['page'] == 7
        assert entries[0]['author'] == 'Kim Loliya'

    def test_multi_line_title(self):
        """Title spans multiple tab-lines before the page number."""
        toc_text = (
            "CONTENTS\n"
            "A Very Long Title That\t\n"
            "Spans Two Lines\t\n"
            "15\n"
            "Jane Smith\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        assert entries[0]['title'] == 'A Very Long Title That Spans Two Lines'
        assert entries[0]['page'] == 15
        assert entries[0]['author'] == 'Jane Smith'

    def test_title_overflow_after_page(self):
        """Title overflow text after page number, then author."""
        toc_text = (
            "CONTENTS\n"
            "Main Title Part\t\n"
            "26\n"
            "overflow part\n"
            "John Doe\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        # 'overflow part' is title overflow, 'John Doe' is author
        assert entries[0]['title'] == 'Main Title Part overflow part'
        assert entries[0]['author'] == 'John Doe'
        assert entries[0]['page'] == 26

    def test_multiple_entries(self):
        """Multiple entries in sequence."""
        toc_text = (
            "CONTENTS\n"
            "Editorial\t\n"
            "3\n"
            "First Article\t\n"
            "7\n"
            "Author One\n"
            "Second Article\t\n"
            "22\n"
            "Author Two\n"
            "Book Reviews\t\n"
            "45\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 4
        assert entries[0]['title'] == 'Editorial'
        assert entries[1]['author'] == 'Author One'
        assert entries[2]['author'] == 'Author Two'
        assert entries[3]['title'] == 'Book Reviews'
        assert entries[3]['page'] == 45

    def test_no_contents_heading(self):
        """Returns empty list when CONTENTS heading is missing."""
        toc_text = "Some random text\nNo TOC here\n"
        entries = parse_toc_text(toc_text)
        assert entries == []

    def test_blank_lines_ignored(self):
        """Blank lines between entries are skipped."""
        toc_text = (
            "CONTENTS\n"
            "\n"
            "Editorial\t\n"
            "\n"
            "3\n"
            "\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        assert entries[0]['title'] == 'Editorial'

    def test_author_with_ampersand_treated_as_single_author_field(self):
        """Author field with & is stored as-is (splitting is done elsewhere)."""
        toc_text = (
            "CONTENTS\n"
            "Collaborative Work\t\n"
            "10\n"
            "Alice Brown & Bob White\n"
        )
        entries = parse_toc_text(toc_text)
        assert entries[0]['author'] == 'Alice Brown & Bob White'

    def test_tab_continuation_after_page_with_author(self):
        """Q1: Tab-line after page number followed by author is title continuation."""
        toc_text = (
            "CONTENTS\n"
            "Meetings With A Remarkable Man \u2013 A Personal Recollection Of \t\n"
            "4\n"
            "Professor Maurice Friedman\t\n"
            "Simon du Plock\n"
            "Next Article Title\t\n"
            "48\n"
            "Jane Smith\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 2
        assert 'Professor Maurice Friedman' in entries[0]['title']
        assert entries[0]['author'] == 'Simon du Plock'
        assert entries[0]['page'] == 4
        assert entries[1]['title'] == 'Next Article Title'
        assert entries[1]['author'] == 'Jane Smith'

    def test_tab_title_after_page_followed_by_page_is_new_entry(self):
        """Tab-line after page followed by another page is a real new entry."""
        toc_text = (
            "CONTENTS\n"
            "First Article\t\n"
            "3\n"
            "Second Article\t\n"
            "7\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 2
        assert entries[0]['title'] == 'First Article'
        assert entries[1]['title'] == 'Second Article'

    def test_dot_leader_title_continuation_after_conjunction(self):
        """Q5: Dot-leader text after conjunction is title continuation, not author."""
        toc_text = (
            "CONTENTS\n"
            "Existentialism, Existential Psychotherapy and\n"
            "African Philosophy ........................................................................138\n"
        )
        entries = parse_toc_text(toc_text)
        assert len(entries) == 1
        assert entries[0]['title'] == 'Existentialism, Existential Psychotherapy and African Philosophy'
        assert entries[0]['author'] is None
        assert entries[0]['page'] == 138


class TestClassifyEntry:
    """Test classify_entry() section assignment."""

    def test_editorial(self):
        assert classify_entry('Editorial') == (SECTION_EDITORIAL, False)

    def test_editorial_case_insensitive(self):
        assert classify_entry('editorial') == (SECTION_EDITORIAL, False)
        assert classify_entry('EDITORIAL') == (SECTION_EDITORIAL, False)

    def test_book_reviews(self):
        assert classify_entry('Book Reviews') == (SECTION_BOOK_REVIEW_EDITORIAL, False)

    def test_articles(self):
        """Anything not 'editorial' or 'book reviews' is classified as Articles."""
        assert classify_entry('Therapy for the Revolution') == (SECTION_ARTICLES, False)
        assert classify_entry('Some Research Paper') == (SECTION_ARTICLES, False)

    def test_book_reviews_case(self):
        assert classify_entry('book reviews') == (SECTION_BOOK_REVIEW_EDITORIAL, False)
        assert classify_entry('BOOK REVIEWS') == (SECTION_BOOK_REVIEW_EDITORIAL, False)

    def test_obituary(self):
        assert classify_entry('Obituary') == (SECTION_EDITORIAL, False)
        assert classify_entry('obituary') == (SECTION_EDITORIAL, False)

    def test_obituary_with_name(self):
        assert classify_entry('Obituary: John Smith') == (SECTION_EDITORIAL, False)

    def test_erratum(self):
        assert classify_entry('Erratum') == (SECTION_EDITORIAL, False)
        assert classify_entry('ERRATA') == (SECTION_EDITORIAL, False)

    def test_correspondence(self):
        assert classify_entry('Correspondence') == (SECTION_ARTICLES, False)
        assert classify_entry('Letters') == (SECTION_ARTICLES, False)

    def test_contributors(self):
        assert classify_entry('Contributors') == (SECTION_EDITORIAL, True)
        assert classify_entry('Notes on Contributors') == (SECTION_EDITORIAL, True)


class TestFindTocPage:
    """Test find_toc_page() with mock fitz doc."""

    def test_toc_on_page_2(self):
        doc = _make_mock_doc([
            "Cover page text",
            "Some other page",
            "CONTENTS\nEditorial\t\n3\n",
            "Article text",
        ])
        assert find_toc_page(doc) == 2

    def test_toc_not_found(self):
        doc = _make_mock_doc([
            "Cover page",
            "No table of contents here",
            "Just text",
        ])
        assert find_toc_page(doc) is None

    def test_toc_on_first_page(self):
        doc = _make_mock_doc([
            "CONTENTS\nEditorial\t\n3\n",
        ])
        assert find_toc_page(doc) == 0


class TestFindPageOffset:
    """Test find_page_offset() — editorial detection strategy."""

    def test_editorial_on_expected_page(self):
        """EDITORIAL on pdf page 4 => journal page 3, offset = 4 - 3 = 1."""
        pages = [
            "Cover",              # 0
            "Inside cover",       # 1
            "CONTENTS\n...",      # 2 (toc)
            "Ad page",           # 3
            "EDITORIAL\nSome editorial text...",  # 4 (journal page 3)
            "More content",      # 5
        ]
        doc = _make_mock_doc(pages)
        offset = find_page_offset(doc, toc_page_idx=2)
        assert offset == 1  # pdf_index 4 - journal_page 3 = 1

    def test_fallback_to_printed_page_number(self):
        """When no EDITORIAL found, falls back to printed page numbers."""
        pages = [
            "Cover",              # 0
            "CONTENTS\n...",      # 1 (toc)
            "No editorial here",  # 2
            "5\tSome article starting on journal page 5",  # 3
            "More content",       # 4
        ]
        doc = _make_mock_doc(pages)
        offset = find_page_offset(doc, toc_page_idx=1)
        # pdf_index 3 - printed_page 5 = -2
        assert offset == -2

    def test_fallback_returns_none(self):
        """When nothing found, returns None so caller can handle it."""
        pages = [
            "Cover",
            "CONTENTS\n...",
            "Plain text no markers",
            "More plain text",
        ]
        doc = _make_mock_doc(pages)
        offset = find_page_offset(doc, toc_page_idx=1)
        assert offset is None


class TestParseBookReviews:
    """Test parse_book_reviews() with mock fitz docs."""

    def test_two_book_reviews(self):
        """Parse two book reviews across two pages."""
        pages = [
            # Page 0: first book review
            (
                "Book Reviews\n"
                "\n"
                "The Art of Existence\n"
                "John Author. (2023). London: Academic Press.\n"
                "\n"
                "This is the review text for the first book.\n"
                "It continues with discussion of themes.\n"
                "\n"
                "Sarah Reviewer\n"
            ),
            # Page 1: second book review
            (
                "\n"
                "Living Authentically\n"
                "Jane Writer. (2024). New York: Big Publisher.\n"
                "\n"
                "This is the review text for the second book.\n"
                "More discussion follows here.\n"
                "\n"
                "Tom Critic\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviews = parse_book_reviews(doc, br_start_pdf=0, br_end_pdf=1)
        assert len(reviews) == 2
        assert reviews[0]['book_title'] == 'The Art of Existence'
        assert reviews[0]['book_author'] == 'John Author'
        assert reviews[0]['book_year'] == 2023
        assert reviews[0]['section'] == SECTION_BOOK_REVIEWS
        assert reviews[0]['title'] == 'Book Review: The Art of Existence'
        assert reviews[0]['pdf_page_start'] == 0
        assert reviews[1]['book_title'] == 'Living Authentically'
        assert reviews[1]['book_author'] == 'Jane Writer'
        assert reviews[1]['book_year'] == 2024
        assert reviews[1]['pdf_page_start'] == 1

    def test_three_book_reviews(self):
        """Parse three book reviews across three pages."""
        pages = [
            (
                "Meaning and Method\n"
                "Alice Scholar. (2020). Cambridge: University Press.\n"
                "\n"
                "Review text here.\n"
                "\n"
                "Bob Reviewer\n"
            ),
            (
                "Being and Time Revisited\n"
                "Carol Thinker. (2021). Oxford: Clarendon.\n"
                "\n"
                "Review text here.\n"
                "\n"
                "Dan Critic\n"
            ),
            (
                "Existential Perspectives\n"
                "Eve Author. (2022). Berlin: Springer.\n"
                "\n"
                "Review text here.\n"
                "\n"
                "Fay Reader\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviews = parse_book_reviews(doc, br_start_pdf=0, br_end_pdf=2)
        assert len(reviews) == 3
        assert reviews[0]['book_title'] == 'Meaning and Method'
        assert reviews[1]['book_title'] == 'Being and Time Revisited'
        assert reviews[2]['book_title'] == 'Existential Perspectives'

    def test_backmatter_boundary_stops_parsing(self):
        """Stops parsing at 'Information for Contributors'."""
        pages = [
            (
                "The Art of Existence\n"
                "John Author. (2023). London: Academic Press.\n"
                "\n"
                "This is the review text.\n"
                "\n"
                "Sarah Reviewer\n"
            ),
            (
                "Information for Contributors\n"
                "\n"
                "Please submit manuscripts to...\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviews = parse_book_reviews(doc, br_start_pdf=0, br_end_pdf=1)
        assert len(reviews) == 1
        assert reviews[0]['book_title'] == 'The Art of Existence'
        # End page should be set before the backmatter page
        assert reviews[0]['pdf_page_end'] == 0

    def test_subscription_rates_stops_parsing(self):
        """Stops parsing at 'Subscription Rates'."""
        pages = [
            (
                "The Art of Existence\n"
                "John Author. (2023). London: Academic Press.\n"
                "\n"
                "Review text.\n"
                "\n"
                "Sarah Reviewer\n"
            ),
            (
                "Advertising Rates and other info\n"
                "\n"
                "Contact us for details.\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviews = parse_book_reviews(doc, br_start_pdf=0, br_end_pdf=1)
        assert len(reviews) == 1
        assert reviews[0]['pdf_page_end'] == 0


class TestExtractReviewerName:
    """Test extract_reviewer_name() finding a name at end of review pages."""

    def test_finds_reviewer_at_end_of_page(self):
        """Reviewer name as the last meaningful line."""
        pages = [
            (
                "This is some review text discussing the book.\n"
                "More analysis of themes and arguments.\n"
                "\n"
                "Sarah Thompson\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer == 'Sarah Thompson'

    def test_skips_references(self):
        """Should skip lines containing (year) references."""
        pages = [
            (
                "This is review text.\n"
                "\n"
                "Tom Reviewer\n"
                "\n"
                "References\n"
                "Smith, J. (2020). Some book. London: Press.\n"
                "Jones, A. (2021). Another book. Oxford: Press.\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer == 'Tom Reviewer'

    def test_returns_none_when_no_name_found(self):
        """Returns None when no standalone name line exists."""
        pages = [
            (
                "This is just some body text that goes on and on without any name.\n"
                "And more text here about the review content that is quite long.\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer is None

    def test_skips_intentionally_left_blank(self):
        """Fix 3c: 'page intentionally left blank' should not be returned."""
        pages = [
            (
                "This is review text.\n"
                "\n"
                "Jane Critic\n"
                "\n"
                "This page intentionally left blank\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer == 'Jane Critic'

    def test_uses_is_name_like_instead_of_strict_regex(self):
        """Fix 5: _is_name_like is more permissive than _REVIEWER_NAME_RE."""
        pages = [
            (
                "This is some review discussion that goes on for a while.\n"
                "More text about the book and its arguments and themes here.\n"
                "\n"
                "Greg Madison\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer == 'Greg Madison'

    def test_consecutive_long_lines_needed_to_stop(self):
        """Fix 5: Need 3+ consecutive long lines to stop, not just 1."""
        pages = [
            (
                "Short intro.\n"
                "This is a single long body text line that exceeds fifty characters in length easily.\n"
                "\n"
                "Mike Reviewer\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer == 'Mike Reviewer'


class TestStripMarkers:
    """Fix 2: Strip asterisks/stars from book titles."""

    def test_strip_leading_asterisk(self):
        assert _strip_markers('*The Art of Being') == 'The Art of Being'

    def test_strip_trailing_asterisk(self):
        assert _strip_markers('The Art of Being*') == 'The Art of Being'

    def test_strip_double_asterisks(self):
        assert _strip_markers('**Title Here**') == 'Title Here'

    def test_strip_star_character(self):
        assert _strip_markers('\u2605Title Here\u2605') == 'Title Here'

    def test_no_markers(self):
        assert _strip_markers('Normal Title') == 'Normal Title'

    def test_none_passthrough(self):
        assert _strip_markers(None) is None

    def test_empty_string(self):
        assert _strip_markers('') == ''


class TestClassifyEntryIndex:
    """Fix 3d: Index entries should be skipped."""

    def test_index_skipped(self):
        section, skip = classify_entry('Index')
        assert skip is True

    def test_journal_index_skipped(self):
        section, skip = classify_entry('Journal Index')
        assert skip is True

    def test_index_case_insensitive(self):
        _, skip = classify_entry('INDEX')
        assert skip is True


class TestStripPublisherFromAuthor:
    """Fix 4: Publisher/location leaking into book_author."""

    def test_known_publisher_stripped(self):
        reviews = [{'book_author': 'John Smith. London: Routledge'}]
        _strip_publisher_from_author(reviews)
        assert reviews[0]['book_author'] == 'John Smith'

    def test_standalone_publisher_stripped(self):
        reviews = [{'book_author': 'John Smith Erlbaum Associates'}]
        _strip_publisher_from_author(reviews)
        assert reviews[0]['book_author'] == 'John Smith'

    def test_series_name_stripped(self):
        reviews = [{'book_author': 'Michael Inwood Past Masters'}]
        _strip_publisher_from_author(reviews)
        assert reviews[0]['book_author'] == 'Michael Inwood'

    def test_city_without_period_stripped(self):
        reviews = [{'book_author': 'Ian Parker London'}]
        _strip_publisher_from_author(reviews)
        assert reviews[0]['book_author'] == 'Ian Parker'

    def test_state_abbreviation_stripped(self):
        reviews = [{'book_author': 'Some Press, Boulder, CO.'}]
        _strip_publisher_from_author(reviews)
        assert reviews[0]['book_author'] == 'Some Press, Boulder'


class TestTrySplitTrailingAuthor:
    """Fix 6: Author names embedded in article titles."""

    def test_no_split_short_title(self):
        """Don't split if remaining title would be too short."""
        title, author = _try_split_trailing_author('Short Jane Smith')
        assert author is None
        assert title == 'Short Jane Smith'

    def test_split_with_name_like_suffix(self):
        """Split when trailing words look like a name."""
        title, author = _try_split_trailing_author(
            'Therapy for the Revolution and Its Discontents Greg Madison'
        )
        assert author == 'Greg Madison'
        assert 'Therapy' in title

    def test_no_split_when_all_title(self):
        """Don't split genuine titles that happen to have capitalized words."""
        title, author = _try_split_trailing_author(
            'Being and Time Revisited'
        )
        assert author is None


class TestIsReviewerNameLike:
    """Test _is_reviewer_name_like() permissive reviewer name check."""

    def test_simple_name(self):
        assert _is_reviewer_name_like('John Smith') is True

    def test_dr_prefix(self):
        assert _is_reviewer_name_like('Dr John Smith') is True

    def test_prof_prefix(self):
        assert _is_reviewer_name_like('Prof Sarah Jones') is True

    def test_professor_prefix(self):
        assert _is_reviewer_name_like('Professor Michael Brown') is True

    def test_single_word_name(self):
        assert _is_reviewer_name_like('Thompson') is True

    def test_long_name_with_credentials(self):
        """Names with credentials like MA PhD should be allowed up to 10 words."""
        assert _is_reviewer_name_like('Dr John Smith MA PhD UKCP') is True

    def test_rejects_body_text(self):
        assert _is_reviewer_name_like('This is clearly a sentence about something.') is False

    def test_rejects_colon(self):
        assert _is_reviewer_name_like('Title: Subtitle') is False

    def test_rejects_question_mark(self):
        assert _is_reviewer_name_like('What is Being?') is False

    def test_rejects_title_starter(self):
        assert _is_reviewer_name_like('The Art of Being') is False

    def test_rejects_empty(self):
        assert _is_reviewer_name_like('') is False

    def test_rejects_references(self):
        assert _is_reviewer_name_like('References') is False


class TestExtractReviewerNameImproved:
    """Test improved extract_reviewer_name() with three-pass strategy."""

    def test_finds_name_after_many_long_lines(self):
        """Pass 1/2: reviewer name below multiple long body-text lines."""
        pages = [
            (
                "This review examines the themes of existential anxiety and authentic living.\n"
                "The author provides a compelling analysis of Heidegger's contribution to modern therapy.\n"
                "Drawing on extensive clinical examples, the work demonstrates practical applications.\n"
                "The integration of philosophical concepts with therapeutic practice is masterfully done.\n"
                "Each chapter builds upon the previous, creating a cohesive theoretical framework.\n"
                "\n"
                "Sarah Thompson\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer == 'Sarah Thompson'

    def test_finds_name_on_last_page_of_multipage_review(self):
        """Name on last page, after long body text on earlier pages."""
        pages = [
            (
                "This is a very long review that spans multiple pages discussing various themes.\n"
                "The author provides extensive analysis of phenomenological approaches to therapy.\n"
                "More text that is quite long and detailed about existential concepts here.\n"
                "Additional long paragraphs discussing the philosophical underpinnings of the work.\n"
            ),
            (
                "Continuing the review with more detailed analysis and discussion of the text.\n"
                "Further examination of how the ideas presented apply to clinical practice.\n"
                "The reviewer finds that the book makes a significant contribution to the field.\n"
                "\n"
                "Jane Critic\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=1)
        assert reviewer == 'Jane Critic'

    def test_finds_name_with_dr_prefix(self):
        """Reviewer with Dr prefix should be found."""
        pages = [
            (
                "This review covers important themes in existential therapy and clinical practice.\n"
                "The arguments presented are well-structured and convincing throughout the book.\n"
                "\n"
                "Dr Michael Brown\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer == 'Dr Michael Brown'

    def test_name_before_references_section(self):
        """Reviewer name should be found before References, not after."""
        pages = [
            (
                "This is a detailed book review with thorough analysis of the content.\n"
                "The text explores many important themes in psychotherapy and philosophy.\n"
                "\n"
                "Tom Reviewer\n"
                "\n"
                "References\n"
                "Smith, J. (2020). Some book title. London: Routledge.\n"
                "Jones, A. (2021). Another important book. Oxford: Oxford University Press.\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer == 'Tom Reviewer'

    def test_returns_none_for_no_name(self):
        """No reviewer name — all body text."""
        pages = [
            (
                "This is just body text about a book that goes on and on without any name.\n"
                "More body text here about the review content that is quite detailed and long.\n"
                "Even more analysis of themes that fills the entire page without attribution.\n"
            ),
        ]
        doc = _make_mock_doc(pages)
        reviewer = extract_reviewer_name(doc, start_pdf=0, end_pdf=0)
        assert reviewer is None


class TestValidateReviewTitlesGarbage:
    """BRE garbage rejection in _validate_review_titles()."""

    def test_rejects_bare_editors_as_author(self):
        """Reject when entire book_author is just '(Editors)'."""
        reviews = [{'book_title': 'Some Title', 'book_author': '(Editors)'}]
        assert _validate_review_titles(reviews) == []

    def test_keeps_author_with_eds(self):
        """Authors with (Eds) notation are legitimate."""
        reviews = [{'book_title': 'Some Title', 'book_author': 'Smith and Jones (Eds)'}]
        assert len(_validate_review_titles(reviews)) == 1

    def test_rejects_hardback_in_author(self):
        reviews = [{'book_title': 'Some Title', 'book_author': 'Hardback Edition'}]
        assert _validate_review_titles(reviews) == []

    def test_rejects_edited_by_in_author(self):
        reviews = [{'book_title': 'Some Title', 'book_author': 'edited by Smith'}]
        assert _validate_review_titles(reviews) == []

    def test_rejects_year_dot_in_author(self):
        reviews = [{'book_title': 'Some Title', 'book_author': 'Smith (2020). London'}]
        assert _validate_review_titles(reviews) == []

    def test_rejects_film_as_author(self):
        reviews = [{'book_title': 'Some Film Title', 'book_author': 'Film'}]
        assert _validate_review_titles(reviews) == []

    def test_keeps_valid_review(self):
        reviews = [{'book_title': 'Valid Book', 'book_author': 'John Smith'}]
        assert len(_validate_review_titles(reviews)) == 1


class TestMatchKnownAuthor:
    """Test _match_known_author() with normalized lookup."""

    def test_exact_match(self):
        known = _get_known_authors()
        if known:
            name = next(iter(known))
            assert _match_known_author(name) == name

    def test_whitespace_tolerance(self):
        known = _get_known_authors()
        if known:
            name = next(iter(known))
            assert _match_known_author(f'  {name}  ') == name

    def test_no_match(self):
        assert _match_known_author('Xyzzy Nonexistent Person') is None
