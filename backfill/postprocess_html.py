"""
Deterministic post-processing pipeline for HTML galleys.

Takes raw HTML from Haiku (full text extraction) and trims it to
body-only content suitable for OJS. All content decisions are here,
not in the Haiku prompt.

Pipeline steps for articles (in order):
1. Strip start bleed (previous article's content at top)
2. Strip article title (may span multiple HTML elements)
3. Strip authors/byline
4. Strip conference note ("Based on presentation...")
5. Strip abstract heading and paragraph(s)
6. Strip keywords line
7. Strip end bleed (next article's content at bottom)

For book reviews: extract just the target review from full-page HTML.

Uses BeautifulSoup4 for all HTML parsing and manipulation. Regex is
only used for text-level operations (word matching, prefix stripping).
"""

import os
import re
import sys

from bs4 import BeautifulSoup, Tag, NavigableString

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.citations import (
    normalise_allcaps, normalise_for_overlap, REFERENCE_HEADING_RE,
    PUBLISHER_NAMES, is_provenance, looks_like_person_name,
)

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# Minimum abstract length worth stripping. Shortest real abstract in dataset
# is 152 chars. Below 30 chars, a toc.json "abstract" is likely a fragment
# or metadata artefact — stripping it risks removing real body content.
MIN_ABSTRACT_LENGTH = 30
#
# Final HTML shorter than this is flagged as empty/broken. Shortest
# legitimate book review in the dataset is ~80 chars of body text.
# Shortest legitimate article is ~200 chars. 100 catches broken
# extractions while allowing very short book reviews.
SHORT_CONTENT_THRESHOLD = 100
# Avoid matching single words or fragments
MIN_TARGET_TEXT_LEN = 5
# Title block should be roughly title-length, not longer
TITLE_BLOCK_MAX_RATIO = 2
# Require matching at least half the title words
TITLE_WORD_MATCH_RATIO = 0.5
# Abstract overlap: fraction of abstract words that must appear in a
# candidate paragraph to consider it a match. Tolerant of OCR errors
# (e.g. "clinicians" → "citizenicians").
ABSTRACT_OVERLAP_THRESHOLD = 0.7
# Fuzzy title verification: fraction of title words (>2 chars) that must
# appear in text. Tolerant of PDF extraction artefacts (fused words,
# reordered fragments).
TITLE_FUZZY_MATCH_THRESHOLD = 0.8
# Use shared normalise_for_overlap as _clean (keeps digits for content matching)
_clean = normalise_for_overlap

# Running header text pattern (plain text, not HTML)
_RUNNING_HEADER_TEXT_RE = re.compile(
    r'^\s*(?:Existential\s+Analysis\s*:\s*)?'
    r'Journal\s+of\s+(?:The\s+|the\s+)?Society\s+for\s+Existential\s+Analysis\s*$',
    re.IGNORECASE
)
# Keep the HTML-level regexes for backward compatibility (used by test imports)
RUNNING_HEADER_RE = re.compile(
    r'^\s*<p[^>]*>\s*(?:<em>|<i>)?\s*(?:Existential\s+Analysis\s*:\s*)?'
    r'Journal\s+of\s+(?:The\s+|the\s+)?Society\s+for\s+Existential\s+Analysis'
    r'\s*(?:</em>|</i>)?\s*</p>\s*$',
    re.IGNORECASE | re.MULTILINE
)
PAGE_NUMBER_RE = re.compile(
    r'^\s*<p[^>]*>\s*\d{1,4}\s*</p>\s*$',
    re.MULTILINE
)

# Element tag sets
BLOCK_TAGS = frozenset({'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'})
HEADING_TAGS = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})
_KEYWORDS_RE = re.compile(r'^Key\s*[Ww]ords?$|^KEYWORDS?$', re.IGNORECASE)
_KEYWORDS_INLINE_RE = re.compile(r'^\s*(?:Key\s*[Ww]ords?|KEYWORDS?)\s*:', re.IGNORECASE)
_PUB_MARKERS_RE = re.compile(
    r'(?:\d{4}|pp\.?\s*\d|ISBN|\$|£|'
    + PUBLISHER_NAMES +
    r'|Oxford|Cambridge|London|New York)', re.IGNORECASE)


# ---------------------------------------------------------------
# BS4 helpers
# ---------------------------------------------------------------

def _parse(html):
    """Parse HTML fragment into BS4 soup."""
    return BeautifulSoup(html, 'html.parser')


def _serialize(soup):
    """Serialize soup back to HTML string."""
    return str(soup)


def _strip_tags(html):
    """Remove HTML tags, returning plain text."""
    return BeautifulSoup(html, 'html.parser').get_text()


def _el_text(el):
    """Get plain text content from a BS4 element."""
    return el.get_text()


def _el_clean_text(el):
    """Get cleaned (normalised) text from a BS4 element."""
    return _clean(_el_text(el))


def _is_block(el):
    """Check if a BS4 element is a block-level element."""
    return isinstance(el, Tag) and el.name in BLOCK_TAGS


def _is_heading(el):
    """Check if a BS4 element is a heading."""
    return isinstance(el, Tag) and el.name in HEADING_TAGS


def _remove_preceding(el):
    """Remove all content before an element (previous siblings)."""
    for sibling in list(el.previous_siblings):
        sibling.extract()


def _remove_from(el):
    """Remove an element and all following siblings."""
    for sibling in list(el.next_siblings):
        sibling.extract()
    el.extract()


def _top_level_blocks(soup):
    """Yield top-level block elements in document order."""
    for el in soup.children:
        if _is_block(el):
            yield el


# ---------------------------------------------------------------
# Text matching (pure text, no HTML)
# ---------------------------------------------------------------

def _text_to_regex(text):
    """Build a regex from text: words in order, flexible non-alpha gaps between.

    Strips toc.json prefixes (Book Review:, Obituary:, etc.) and trailing
    parentheticals before building. Returns compiled regex or None.
    """
    text = _strip_toc_prefixes(text)
    words = _clean(text).split()
    if not words:
        return None
    # Words must appear in order; gaps allow any non-alphanumeric chars
    # (whitespace, punctuation, line breaks, HTML residue)
    gap = r'[^a-z0-9]*'
    pattern = gap.join(re.escape(w) for w in words)
    return re.compile(pattern, re.IGNORECASE)


def _title_in_text(title, text):
    """Check if title appears in text as an ordered word sequence.

    No threshold — either the words appear in order or they don't.
    """
    if not title:
        return True
    rx = _text_to_regex(title)
    if rx is None:
        return True
    clean_text = _clean(_strip_tags(text))
    return bool(rx.search(clean_text))


def _find_block_by_text(html, target_text, search_start=0, search_end=None):
    """Find the HTML block whose text matches target_text.

    Uses ordered word-sequence matching (no threshold).
    Returns (start_pos, end_pos) of the matching block, or (None, None).

    Note: This function returns character positions for backward compatibility
    with callers that do string slicing. New code should use _find_block_in_soup.
    """
    if not target_text or len(target_text) < MIN_TARGET_TEXT_LEN:
        return None, None
    if search_end is None:
        search_end = len(html)
    region = html[search_start:search_end]
    rx = _text_to_regex(target_text)
    if rx is None:
        return None, None

    soup = _parse(region)
    for el in soup.find_all(list(BLOCK_TAGS)):
        block_text = _clean(_el_text(el))
        if rx.search(block_text):
            # Find the element's position in the region string
            el_str = str(el)
            pos = region.find(el_str)
            if pos >= 0:
                return (search_start + pos, search_start + pos + len(el_str))

    return None, None


def _find_block_in_soup(soup, target_text):
    """Find a block element in soup whose text matches target_text.

    Returns the BS4 element, or None.
    """
    if not target_text or len(target_text) < MIN_TARGET_TEXT_LEN:
        return None
    rx = _text_to_regex(target_text)
    if rx is None:
        return None
    for el in soup.find_all(list(BLOCK_TAGS)):
        if rx.search(_el_clean_text(el)):
            return el
    return None


def _find_first_body_heading(html):
    """Find the position of the first body content heading (Introduction, etc.).

    Returns the character position, or len(html) if not found.
    Skips "Abstract", "Keywords" headings, and person-name headings
    (author bylines that Haiku sometimes renders as <h2>).
    """
    skip_headings = {'abstract', 'keywords', 'key words'}
    soup = _parse(html)
    for el in soup.find_all(['h2', 'h3']):
        heading_text = _el_text(el).strip()
        if heading_text.lower() in skip_headings:
            continue
        if looks_like_person_name(heading_text):
            continue
        # Find position in original HTML string
        el_str = str(el)
        pos = html.find(el_str)
        if pos >= 0:
            return pos
    return len(html)


def _find_first_body_heading_soup(soup):
    """Find the first body content heading element in soup.

    Returns the BS4 element, or None.
    """
    skip_headings = {'abstract', 'keywords', 'key words'}
    for el in soup.find_all(['h2', 'h3']):
        heading_text = _el_text(el).strip()
        if heading_text.lower() in skip_headings:
            continue
        if looks_like_person_name(heading_text):
            continue
        return el
    return None


# ---------------------------------------------------------------
# Soup-based strip functions (mutate soup in place)
# ---------------------------------------------------------------

def _strip_start_bleed_soup(soup, own_title):
    """Remove content before the article's own title."""
    if not own_title:
        return
    el = _find_block_in_soup(soup, own_title)
    if el is not None:
        _remove_preceding(el)


def _strip_title_soup(soup, title):
    """Remove title elements from the top of the soup.

    Consumes blocks that have word overlap with the title and are
    title-sized (short) or headings. Stops at provenance notes or
    non-matching blocks.
    """
    if not title:
        return
    title_clean = _clean(title)
    if not title_clean:
        return

    title_words = set(title_clean.split())
    title_len = len(title_clean)

    to_remove = []
    matched_words = set()

    for el in _top_level_blocks(soup):
        block_text = _el_clean_text(el)
        block_words = set(block_text.split())
        overlap = title_words & block_words

        # Stop at provenance notes
        raw_text = _el_text(el).strip()
        if is_provenance(raw_text):
            break

        is_title_sized = len(block_text) <= title_len * TITLE_BLOCK_MAX_RATIO
        is_head = _is_heading(el)

        if overlap and (is_title_sized or is_head):
            matched_words |= overlap
            to_remove.append(el)
        else:
            break

    if matched_words and len(matched_words) > len(title_words) * TITLE_WORD_MATCH_RATIO:
        for el in to_remove:
            el.extract()


def _strip_subtitle_soup(soup, subtitle):
    """Remove the subtitle element from the top of the soup."""
    if not subtitle:
        return
    subtitle_clean = _clean(subtitle)
    if not subtitle_clean:
        return

    subtitle_words = set(subtitle_clean.split())

    for i, el in enumerate(_top_level_blocks(soup)):
        if i >= 5:
            break
        block_text = _el_clean_text(el)
        block_words = set(block_text.split())
        overlap = subtitle_words & block_words

        if overlap and len(overlap) > len(subtitle_words) * TITLE_WORD_MATCH_RATIO:
            el.extract()
            return
        elif block_words - subtitle_words:
            return


def _strip_authors_soup(soup, authors):
    """Remove author byline element from the soup."""
    if not authors:
        return
    # Search up to Abstract heading or first body heading
    boundary = _find_first_body_heading_soup(soup)
    # Also check for Abstract heading
    abstract_heading = soup.find(['h2', 'h3'], string=re.compile(r'^\s*Abstract\s*$', re.IGNORECASE))
    if abstract_heading:
        boundary = abstract_heading

    for variant in _author_name_variants(authors):
        rx = _text_to_regex(variant)
        if rx is None:
            continue
        for el in soup.find_all(list(BLOCK_TAGS)):
            # Stop searching past boundary
            if boundary and el == boundary:
                break
            if boundary and _el_comes_after(el, boundary):
                break
            if rx.search(_el_clean_text(el)):
                el.extract()
                return


def _el_comes_after(el, boundary):
    """Check if el appears after boundary in document order."""
    # Walk forward from boundary's next siblings
    for sibling in boundary.next_elements:
        if sibling is el:
            return True
    return False


def _strip_abstract_soup(soup, abstract):
    """Remove abstract heading and paragraph from the soup."""
    if not abstract or len(abstract) < MIN_ABSTRACT_LENGTH:
        return

    # Remove "Abstract" heading
    for heading in soup.find_all(['h2', 'h3']):
        if re.match(r'^\s*Abstract\s*$', _el_text(heading), re.IGNORECASE):
            heading.extract()
            break

    # Find and remove abstract paragraph using fuzzy matching
    boundary = _find_first_body_heading_soup(soup)
    abs_clean = _clean(abstract)
    abs_words = set(abs_clean.split())
    if not abs_words:
        return

    for el in soup.find_all('p'):
        # Stop at first body heading
        if boundary and (el == boundary or _el_comes_after(el, boundary)):
            break
        block_text = _el_clean_text(el)
        block_words = set(block_text.split())
        if not block_words:
            continue
        overlap = len(abs_words & block_words) / len(abs_words)
        if overlap > ABSTRACT_OVERLAP_THRESHOLD:
            el.extract()
            return


def _strip_keywords_soup(soup):
    """Remove keywords heading + paragraph, or standalone keywords paragraph."""
    # Pattern 1: Heading (h2/h3 with "Keywords" text) + following paragraph
    for heading in soup.find_all(['h2', 'h3']):
        if _KEYWORDS_RE.match(_el_text(heading).strip()):
            # Remove the heading and the following <p> sibling
            next_p = heading.find_next_sibling('p')
            heading.extract()
            if next_p:
                next_p.extract()
            return

    # Pattern 2: Standalone paragraph starting with "Keywords:"
    for p in soup.find_all('p'):
        if _KEYWORDS_INLINE_RE.match(_el_text(p)):
            p.extract()
            return


def _strip_end_bleed_soup(soup, next_title):
    """Remove content from the next article at the end."""
    if not next_title:
        return
    # Find last back-matter heading
    last_backmatter = None
    for heading in soup.find_all(['h2', 'h3']):
        if REFERENCE_HEADING_RE.match(_el_text(heading).strip()):
            last_backmatter = heading

    # Search for next title from last back-matter heading onwards
    rx = _text_to_regex(next_title)
    if rx is None:
        return

    if last_backmatter:
        # Search all block elements at or after the last backmatter heading
        search_els = []
        start_searching = False
        for el in soup.find_all(list(BLOCK_TAGS)):
            if el == last_backmatter:
                start_searching = True
            if start_searching:
                search_els.append(el)
    else:
        # No backmatter heading: only match in headings to avoid false
        # positives in body text that merely mentions the next title
        search_els = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])

    for el in search_els:
        if rx.search(_el_clean_text(el)):
            _remove_from(el)
            return


def _strip_running_headers_soup(soup):
    """Remove running headers and bare page numbers from print layout."""
    for p in list(soup.find_all('p')):
        text = _el_text(p).strip()
        # Running header: "Journal of The Society for Existential Analysis"
        if _RUNNING_HEADER_TEXT_RE.match(text):
            p.decompose()
            continue
        # Bare page number: standalone 1-4 digit number
        if re.match(r'^\d{1,4}$', text):
            p.decompose()


def _strip_heading_sups_soup(soup):
    """Strip footnote superscripts from headings."""
    for heading in soup.find_all(list(HEADING_TAGS)):
        for sup in heading.find_all('sup'):
            sup.decompose()


def _normalise_headings_soup(soup):
    """Normalise ALL CAPS headings to title case."""
    for heading in soup.find_all(list(HEADING_TAGS)):
        # Only process headings with simple text content (no nested tags
        # that would be lost by replacing .string)
        if heading.string is not None:
            heading.string = normalise_allcaps(heading.string)
        else:
            # Mixed content (e.g. <h2><em>TEXT</em></h2>) — process each
            # text node individually
            for text_node in heading.find_all(string=True):
                if text_node.strip():
                    text_node.replace_with(normalise_allcaps(str(text_node)))


_NOTES_HEADING_RE = re.compile(r'^(Notes?|Endnotes?|Footnotes?)\s*$', re.IGNORECASE)


def _splice_notes_soup(soup, back_matter_sections):
    """Replace notes <ol> with PyMuPDF-extracted notes if Haiku dropped any.

    Compares <li> count in the existing notes section against the expected
    count from PyMuPDF PDF extraction. If the HTML has fewer notes, replaces
    the entire <ol> (and ensures an <h2> heading exists).
    """
    notes_sections = [s for s in back_matter_sections
                      if _NOTES_HEADING_RE.match(s['heading'])
                      and s['is_numbered']]
    if not notes_sections:
        return

    expected_notes = notes_sections[0]['items']
    expected_count = len(expected_notes)
    heading_text = notes_sections[0]['heading']

    # Find the Notes heading in the soup
    notes_h2 = None
    refs_h2 = None
    for h2 in soup.find_all('h2'):
        text = _el_text(h2).strip()
        if not notes_h2 and _NOTES_HEADING_RE.match(text):
            notes_h2 = h2
        if not refs_h2 and REFERENCE_HEADING_RE.match(text):
            refs_h2 = h2

    # Find the <ol> that belongs to the notes section
    notes_ol = None
    if notes_h2:
        # Look for the first <ol> after the notes heading
        for sib in notes_h2.next_siblings:
            if isinstance(sib, Tag):
                if sib.name == 'ol':
                    notes_ol = sib
                    break
                # Stop at next heading or references
                if sib.name in HEADING_TAGS:
                    break
    else:
        # No heading — look for a bare <ol> before References
        if refs_h2:
            for el in refs_h2.previous_siblings:
                if isinstance(el, Tag) and el.name == 'ol':
                    notes_ol = el
                    break
                if isinstance(el, Tag) and el.name in HEADING_TAGS:
                    break

    # Count existing notes
    actual_count = len(notes_ol.find_all('li')) if notes_ol else 0

    if actual_count >= expected_count:
        return

    # Build replacement <ol> with PyMuPDF notes (may contain <em> tags)
    new_ol = soup.new_tag('ol')
    for note in expected_notes:
        li = soup.new_tag('li')
        # Parse note HTML (may contain <em> tags) into the <li>
        note_soup = BeautifulSoup(note, 'html.parser')
        for child in list(note_soup.children):
            li.append(child)
        new_ol.append(li)

    if notes_ol:
        notes_ol.replace_with(new_ol)
    elif notes_h2:
        notes_h2.insert_after(new_ol)
    else:
        # No heading exists — create one and insert before refs or at end
        new_h2 = soup.new_tag('h2')
        new_h2.string = heading_text
        if refs_h2:
            refs_h2.insert_before(new_h2)
            new_h2.insert_after(new_ol)
        else:
            soup.append(new_h2)
            soup.append(new_ol)


def _strip_book_reviews_heading_soup(soup):
    """Remove 'BOOK REVIEWS' heading."""
    for heading in soup.find_all(['h1', 'h2', 'h3']):
        if re.match(r'^\s*BOOK\s+REVIEWS?\s*$', _el_text(heading), re.IGNORECASE):
            heading.extract()
            return


def _strip_contents_section_soup(soup):
    """Remove a CONTENTS/TOC section that lists all articles in an issue.

    Some raw editorial HTML begins with a full table of contents before the
    actual article body. The TOC ends at an <hr> tag or the first real
    section heading (EDITORIAL, BOOK REVIEWS, etc.).
    """
    contents_heading = None
    for heading in soup.find_all(['h1', 'h2']):
        if re.match(r'^\s*CONTENTS\s*$', _el_text(heading).strip(), re.IGNORECASE):
            contents_heading = heading
            break
    if contents_heading is None:
        return

    _SECTION_HEADINGS_RE = re.compile(
        r'^\s*(EDITORIAL|BOOK\s+REVIEWS?)\s*$', re.IGNORECASE)

    to_remove = [contents_heading]
    for sibling in list(contents_heading.next_siblings):
        if not hasattr(sibling, 'name'):
            to_remove.append(sibling)
            continue
        if sibling.name == 'hr':
            to_remove.append(sibling)
            break
        if sibling.name in ('h1', 'h2') and \
                _SECTION_HEADINGS_RE.match(_el_text(sibling).strip()):
            break
        to_remove.append(sibling)

    for el in to_remove:
        el.extract()


def _strip_book_review_editorial_tail_soup(soup):
    """Remove individual book reviews after the editorial intro.

    For Book Review Editorial articles, the raw HTML contains the editorial
    intro paragraphs followed by individual review headings and content.
    Strip from the first <h2> that isn't a section-level heading.
    """
    _SECTION_RE = re.compile(
        r'^\s*(BOOK\s+REVIEWS?|EDITORIAL)\s*$', re.IGNORECASE)
    for heading in soup.find_all(['h2', 'h3']):
        text = _el_text(heading).strip()
        if _SECTION_RE.match(text):
            continue
        # This is the first individual review heading — remove from here
        _remove_from(heading)
        return


# ---------------------------------------------------------------
# Public string-based API (backward compatible)
# ---------------------------------------------------------------

def strip_start_bleed(html, own_title):
    """Remove content from the previous article at the start of the HTML.

    Strategy: find this article's own title. Everything before it is
    bleed from the previous article. This is more reliable than searching
    for the previous article's title, because the bleed is the TAIL of
    the previous article (body text, refs) — not its title.
    """
    if not own_title:
        return html
    soup = _parse(html)
    _strip_start_bleed_soup(soup, own_title)
    return _serialize(soup)


def strip_title(html, title):
    """Remove the article's own title from the HTML.

    Handles multi-element titles (h1/h2 + subtitle paragraphs). Only
    consumes blocks from the top that are short enough to be title/subtitle
    elements (headings or short paragraphs). Stops at the first long
    body paragraph regardless of word overlap.
    """
    if not title:
        return html
    soup = _parse(html)
    _strip_title_soup(soup, title)
    return _serialize(soup)


def strip_subtitle(html, subtitle):
    """Remove the article's subtitle from the HTML body."""
    if not subtitle:
        return html
    soup = _parse(html)
    _strip_subtitle_soup(soup, subtitle)
    return _serialize(soup)


def strip_authors(html, authors):
    """Remove author byline from the HTML.

    Authors appear near the top, after the title. Search up to the
    Abstract heading or first body heading.
    """
    if not authors:
        return html
    soup = _parse(html)
    _strip_authors_soup(soup, authors)
    return _serialize(soup)


def strip_abstract(html, abstract):
    """Remove abstract heading and paragraph(s) from the HTML.

    Uses fuzzy word-overlap matching (not ordered-word regex) because
    Haiku OCR may introduce errors in the abstract text (e.g. "clinicians"
    → "citizenicians"). 80% word overlap is tolerant of OCR errors.
    """
    if not abstract or len(abstract) < MIN_ABSTRACT_LENGTH:
        return html
    soup = _parse(html)
    _strip_abstract_soup(soup, abstract)
    return _serialize(soup)


def strip_keywords(html):
    """Remove the Keywords / Key Words line from the HTML.

    Handles both:
    - <p>Keywords: term1, term2, ...</p>
    - <h2>Key Words</h2>\\n<p>term1, term2, ...</p>
    """
    soup = _parse(html)
    _strip_keywords_soup(soup)
    return _serialize(soup)


def strip_end_bleed(html, next_title):
    """Remove content from the next article at the end of the HTML."""
    if not next_title:
        return html
    soup = _parse(html)
    _strip_end_bleed_soup(soup, next_title)
    return _serialize(soup)


# ---------------------------------------------------------------
# Book review post-processing
# ---------------------------------------------------------------

def _find_book_publication_details(html, book_title, search_start=0):
    """Find where a book review's publication details start.

    Looks for the book title in a block that also contains publication
    markers (publisher, year, page count, price). This distinguishes
    the actual review from an editorial intro that merely mentions the title.

    Returns (start_pos, end_pos) or (None, None).
    """
    clean_book = re.sub(r'^Book Review:\s*', '', book_title, flags=re.IGNORECASE)
    book_parts = [p.strip() for p in clean_book.split('/') if p.strip()]
    rx_parts = [_text_to_regex(p) for p in book_parts if p]
    rx_parts = [r for r in rx_parts if r is not None]
    if not rx_parts:
        return None, None

    region = html[search_start:]
    soup = _parse(region)

    # Find the earliest block matching ANY part with publication details
    for el in soup.find_all(list(BLOCK_TAGS - {'blockquote'})):
        block_text = _el_clean_text(el)
        raw_block = _el_text(el)
        has_title = any(rx.search(block_text) for rx in rx_parts)
        if not has_title:
            continue
        has_pub = bool(_PUB_MARKERS_RE.search(raw_block))
        is_short_heading = len(block_text.split()) <= 10
        if has_pub or is_short_heading:
            el_str = str(el)
            pos = region.find(el_str)
            if pos >= 0:
                return (search_start + pos, search_start + pos + len(el_str))

    # Fallback: find earliest block matching any part without pub markers
    for el in soup.find_all(list(BLOCK_TAGS - {'blockquote'})):
        block_text = _el_clean_text(el)
        if any(rx.search(block_text) for rx in rx_parts):
            el_str = str(el)
            pos = region.find(el_str)
            if pos >= 0:
                return (search_start + pos, search_start + pos + len(el_str))

    return None, None


def extract_book_review(html, book_title, next_book_title=None, reviewer=None,
                        is_combined_review=False):
    """Extract a single book review from full-page HTML extraction.

    Handles:
    - Editorial intros that mention book titles before the actual reviews
    - Multi-book reviews ("Title A / Title B" in one piece)
    - Combined reviews (consecutive entries sharing same pages)
    - Shared pages with adjacent reviews

    For combined reviews (is_combined_review=True), the "next" title is
    the first non-combined review, not the next entry in the same group.
    """
    if not book_title:
        return html

    start, _ = _find_book_publication_details(html, book_title)
    if start is None:
        return html

    review_end = len(html)
    if next_book_title and not is_combined_review:
        next_start, _ = _find_book_publication_details(html, next_book_title, search_start=start + 1)
        if next_start is not None:
            review_end = next_start

    return html[start:review_end].strip()


def _author_name_variants(authors):
    """Generate matching variants for an author name string.

    Handles cases where HTML drops middle names/initials that appear in
    toc.json. E.g. "Luis M. Rodriguez" → also try "Luis Rodriguez",
    "Edgar Agrela Correia" → also try "Edgar Correia".

    Returns a list of name strings to try, from most specific to least.
    """
    variants = [authors]
    parts = re.split(r',\s*|\s+and\s+', authors)
    short_parts = []
    for part in parts:
        words = part.strip().split()
        if len(words) > 2:
            short_parts.append(f'{words[0]} {words[-1]}')
        else:
            short_parts.append(part.strip())
    short_variant = ', '.join(short_parts) if ',' in authors else ' and '.join(short_parts) if ' and ' in authors else short_parts[0]
    if short_variant != authors:
        variants.append(short_variant)
    return variants


# ---------------------------------------------------------------
# Editorial post-processing
# ---------------------------------------------------------------

def postprocess_editorial(html, article):
    """Post-process an editorial: strip title, keep body."""
    soup = _parse(html)
    _strip_start_bleed_soup(soup, article.get('title', ''))
    _strip_title_soup(soup, article.get('title', ''))
    _strip_authors_soup(soup, article.get('authors', ''))
    _strip_end_bleed_soup(soup, article.get('_next_title', ''))
    return _serialize(soup)


def postprocess_book_review_editorial(html, article):
    """Post-process a book review editorial (section intro).

    Strip the "BOOK REVIEWS" heading, keep the editorial body.
    """
    soup = _parse(html)
    _strip_contents_section_soup(soup)
    _strip_start_bleed_soup(soup, article.get('title', ''))
    _strip_book_reviews_heading_soup(soup)
    _strip_book_review_editorial_tail_soup(soup)
    return _serialize(soup)


# ---------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------

def postprocess_article(html, article, pdf_path=None):
    """Run the full post-processing pipeline on raw HTML.

    Routes to the appropriate pipeline based on article section type:
    - Articles: strip title, authors, abstract, keywords, conference note, bleed
    - Book Reviews: extract target review from shared pages
    - Editorial: strip title, keep body
    - Book Review Editorial: strip heading, keep editorial body

    Args:
        html: raw HTML from Haiku (full extraction)
        article: toc.json article dict
        pdf_path: path to split PDF (for ref verification)
    """
    # Skip post-processing for content-filtered articles (PyMuPDF fallback).
    # These have no HTML structure — the pipeline would mangle them.
    if '<!-- AUTO-EXTRACTED:' in html[:100]:
        return html

    section = article.get('section', '')

    if section in ('Book Reviews', 'Book Review'):
        is_combined = (
            article.get('pdf_page_start') is not None
            and article.get('pdf_page_start') == article.get('_next_page_start')
            and article.get('pdf_page_end') == article.get('_next_page_end')
        )
        html = extract_book_review(
            html,
            book_title=article.get('title', ''),
            next_book_title=article.get('_next_title', ''),
            reviewer=article.get('reviewer', ''),
            is_combined_review=is_combined,
        )

    # Parse once for all remaining operations
    soup = _parse(html)

    # Strip CONTENTS/TOC sections before any other processing
    _strip_contents_section_soup(soup)

    if section == 'Book Review Editorial':
        _strip_start_bleed_soup(soup, article.get('title', ''))
        _strip_book_reviews_heading_soup(soup)
        _strip_book_review_editorial_tail_soup(soup)
    elif section == 'Editorial':
        _strip_start_bleed_soup(soup, article.get('title', ''))
        _strip_title_soup(soup, article.get('title', ''))
        _strip_authors_soup(soup, article.get('authors', ''))
        _strip_end_bleed_soup(soup, article.get('_next_title', ''))
    elif section not in ('Book Reviews', 'Book Review'):
        # Standard article
        _strip_start_bleed_soup(soup, article.get('title', ''))
        _strip_title_soup(soup, article.get('title', ''))
        _strip_subtitle_soup(soup, article.get('subtitle', ''))
        _strip_authors_soup(soup, article.get('authors', ''))
        # Conference/presentation notes are preserved in the body — they flow
        # into JATS and are extracted as provenance by extract_citations.py.
        _strip_abstract_soup(soup, article.get('abstract', ''))
        _strip_keywords_soup(soup)
        # Second pass: Haiku sometimes renders the title twice (h1 + h2).
        _strip_title_soup(soup, article.get('title', ''))
        _strip_subtitle_soup(soup, article.get('subtitle', ''))
        _strip_end_bleed_soup(soup, article.get('_next_title', ''))

    # Strip footnote superscripts from headings
    _strip_heading_sups_soup(soup)

    # Normalise ALL CAPS headings to title case
    _normalise_headings_soup(soup)

    # Strip running headers and bare page numbers
    _strip_running_headers_soup(soup)

    # Splice complete notes from PyMuPDF if Haiku dropped any.
    if pdf_path and os.path.exists(pdf_path):
        from htmlgen import extract_pdf_back_matter
        back_matter = extract_pdf_back_matter(
            pdf_path, title=article.get('title'),
            authors=article.get('authors'))
        if back_matter:
            _splice_notes_soup(soup, back_matter)

    html = _serialize(soup)
    html = re.sub(r'\n{3,}', '\n\n', html).strip()

    # Strip control characters that break XML parsing downstream.
    # These occasionally appear in Haiku-extracted text from scanned PDFs.
    html = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', html)

    return html


# ---------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------

_SECTION_HEADINGS = frozenset({
    'letters to the editors', 'letters to the editor', 'letter to the editors',
    'letter to the editor', 'letters', 'editorial', 'book reviews',
    'book review editorial', 'obituary',
})


def _strip_toc_prefixes(title):
    """Strip toc.json prefixes that don't appear in the PDF/HTML.

    Same logic as split.py title_in_split_pdf — kept in sync.
    """
    title = re.sub(
        r'^(Book Reviews?|Film Review|Exhibition Report|Poem'
        r'|Personally Speaking|Obituary|Essay Review'
        r'|Letter to the Editors?|Responses?( to)?'
        r'|Professor|Prof\.?)\s*:?\s*',
        '', title, flags=re.IGNORECASE
    ).strip()
    title = re.sub(
        r'^(Professor|Prof\.?)\s*:?\s*',
        '', title, flags=re.IGNORECASE
    ).strip()
    title = re.sub(r'\s*\([^)]+\)\s*$', '', title)
    return title


def _title_in_text_fuzzy(title, text):
    """Check if title appears in text using fuzzy matching.

    Uses substring match or 80% word overlap — different from the ordered-
    sequence _title_in_text used elsewhere, because verification needs to
    tolerate PDF extraction artefacts (fused words, reordered fragments).
    """
    clean_title = _clean(title)
    clean_text = _clean(text)
    if not clean_title:
        return True
    if clean_title in clean_text:
        return True
    title_words = [w for w in clean_title.split() if len(w) > 2]
    if not title_words:
        return True
    found = sum(1 for w in title_words if w in clean_text)
    return found / len(title_words) >= TITLE_FUZZY_MATCH_THRESHOLD


def verify_postprocessed(raw_html, final_html, article):
    """Verify post-processing produced correct output.

    Returns list of warning strings (empty = all good).
    """
    warnings = []
    title = article.get('title', '')
    stripped_title = _strip_toc_prefixes(title)
    section = article.get('section', '')
    is_book_review = section in ('Book Reviews', 'Book Review')
    raw_text = _strip_tags(raw_html)
    final_text = _strip_tags(final_html)

    if stripped_title and not _title_in_text_fuzzy(stripped_title, raw_text):
        if not any(h in raw_text.lower() for h in _SECTION_HEADINGS):
            warnings.append(f'TITLE_NOT_IN_RAW: "{stripped_title[:50]}" not found in raw HTML')

    if is_book_review and stripped_title:
        parts = [p.strip() for p in stripped_title.split('/') if p.strip()]
        if not any(_title_in_text_fuzzy(p, final_text) for p in parts):
            warnings.append(f'BOOK_TITLE_MISSING: "{stripped_title[:50]}" not in final HTML')

    if len(final_text.strip()) < SHORT_CONTENT_THRESHOLD:
        warnings.append(f'EMPTY_OUTPUT: final HTML has only {len(final_text.strip())} chars')

    return warnings


def pdf_has_formal_refs(pdf_path):
    """Check if PDF has a standalone back-matter heading."""
    if fitz is None:
        return False
    doc = fitz.open(pdf_path)
    pdf_text = ''.join(p.get_text() for p in doc)
    doc.close()
    for line in pdf_text.split('\n'):
        if REFERENCE_HEADING_RE.match(line.strip()):
            return True
    return False


def html_has_refs(html):
    """Check if HTML contains a back-matter section."""
    soup = _parse(html)
    for heading in soup.find_all(['h2', 'h3']):
        if REFERENCE_HEADING_RE.match(_el_text(heading).strip()):
            return True
    for p in soup.find_all('p'):
        strong = p.find('strong')
        if strong and REFERENCE_HEADING_RE.match(_el_text(strong).strip()):
            return True
    return False


def check_missing_refs(html, pdf_path):
    """Return True if PDF has formal References heading but HTML is missing them."""
    return pdf_has_formal_refs(pdf_path) and not html_has_refs(html)
