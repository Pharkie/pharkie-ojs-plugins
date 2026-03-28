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
"""

import os
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
from citations import REFERENCE_HEADING_RE

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


def _clean(text):
    """Lowercase, strip non-alphanumeric, collapse whitespace."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', text.lower())).strip()


def _strip_tags(html):
    """Remove HTML tags."""
    return re.sub(r'<[^>]+>', '', html)



def _find_first_body_heading(html):
    """Find the position of the first body content heading (Introduction, etc.).

    Returns the character position, or len(html) if not found.
    Skips "Abstract" and "Keywords" headings.
    """
    skip_headings = {'abstract', 'keywords', 'key words'}
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL | re.IGNORECASE):
        heading_text = _strip_tags(m.group(1)).strip().lower()
        if heading_text not in skip_headings:
            return m.start()
    return len(html)


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
    """
    if not target_text or len(target_text) < 5:
        return None, None
    if search_end is None:
        search_end = len(html)
    region = html[search_start:search_end]
    rx = _text_to_regex(target_text)
    if rx is None:
        return None, None

    for m in re.finditer(r'<(p|h[1-6]|blockquote)[^>]*>.*?</\1>', region, re.DOTALL):
        block_text = _clean(_strip_tags(m.group()))
        if rx.search(block_text):
            return (search_start + m.start(), search_start + m.end())

    return None, None


# ---------------------------------------------------------------
# Article post-processing steps
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
    start, _ = _find_block_by_text(html, own_title)
    if start is not None and start > 0:
        html = html[start:]
    return html


def strip_title(html, title):
    """Remove the article's own title from the HTML.

    Handles multi-element titles (h1/h2 + subtitle paragraphs). Only
    consumes blocks from the top that are short enough to be title/subtitle
    elements (headings or short paragraphs). Stops at the first long
    body paragraph regardless of word overlap.
    """
    if not title:
        return html
    title_clean = _clean(title)
    if not title_clean:
        return html

    title_words = set(title_clean.split())
    title_len = len(title_clean)

    # Look at blocks from the start of the HTML
    blocks = list(re.finditer(r'<(h[1-6]|p)[^>]*>.*?</\1>', html, re.DOTALL))
    if not blocks:
        return html

    best_end = 0
    matched_words = set()

    for block in blocks:
        block_text = _clean(_strip_tags(block.group()))
        block_words = set(block_text.split())
        overlap = title_words & block_words

        # Only consume if: block has title word overlap AND is short
        # enough to be a title/subtitle element (not a body paragraph).
        # A title block should be roughly title-length, not 10x longer.
        is_title_sized = len(block_text) <= title_len * 2
        is_heading = block.group().startswith('<h')

        if overlap and (is_title_sized or is_heading):
            matched_words |= overlap
            best_end = block.end()
        else:
            break

    if matched_words and len(matched_words) > len(title_words) // 2:
        html = html[best_end:].lstrip()

    return html


def strip_authors(html, authors):
    """Remove author byline from the HTML.

    Authors appear near the top, after the title. Search up to the
    Abstract heading or first body heading.
    """
    if not authors:
        return html
    # Search up to Abstract heading or first body heading
    abstract_pos = re.search(r'<h[23][^>]*>\s*Abstract\s*</h[23]>', html, re.IGNORECASE)
    search_end = abstract_pos.start() if abstract_pos else _find_first_body_heading(html)
    if search_end == 0:
        search_end = len(html)
    start, end = _find_block_by_text(html, authors, search_end=search_end)
    if start is not None:
        html = html[:start] + html[end:]
        html = html.lstrip()
    return html


def strip_conference_note(html):
    """Remove 'Based on (a) presentation/keynote...' lines."""
    html = re.sub(
        r'<p[^>]*>\s*(?:<em>)?\s*Based on (?:a )?(?:keynote )?presentation\s.*?</p>\s*',
        '', html, count=1, flags=re.DOTALL | re.IGNORECASE
    )
    return html


def strip_abstract(html, abstract):
    """Remove abstract heading and paragraph(s) from the HTML."""
    if not abstract or len(abstract) < MIN_ABSTRACT_LENGTH:
        return html

    # Remove "Abstract" heading if present
    html = re.sub(r'<h[23][^>]*>\s*Abstract\s*</h[23]>\s*', '', html, count=1, flags=re.IGNORECASE)

    # Search up to the first body heading (recalculate since we may have removed Abstract heading)
    search_end = _find_first_body_heading(html)
    start, end = _find_block_by_text(html, abstract, search_end=search_end)
    if start is not None:
        html = html[:start] + html[end:]
        html = html.lstrip()

    return html


def strip_keywords(html):
    """Remove the Keywords / Key Words line from the HTML.

    Handles both:
    - <p>Keywords: term1, term2, ...</p>
    - <h2>Key Words</h2>\\n<p>term1, term2, ...</p>
    """
    # Heading + following paragraph pattern
    html = re.sub(
        r'<h[23][^>]*>\s*(?:Key\s*[Ww]ords?|KEYWORDS?)\s*</h[23]>\s*<p[^>]*>.*?</p>\s*',
        '', html, count=1, flags=re.DOTALL | re.IGNORECASE
    )
    # Standalone paragraph pattern
    html = re.sub(
        r'<p[^>]*>\s*(?:Key\s*[Ww]ords?|KEYWORDS?)\s*:?\s*.*?</p>\s*',
        '', html, count=1, flags=re.DOTALL | re.IGNORECASE
    )
    return html


def strip_end_bleed(html, next_title):
    """Remove content from the next article at the end of the HTML."""
    if not next_title:
        return html
    # Search from the last back-matter heading onwards (references section and after)
    last_backmatter = len(html)
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL):
        heading_text = _strip_tags(m.group(1)).strip()
        if REFERENCE_HEADING_RE.match(heading_text):
            last_backmatter = m.start()
    # Search from last back-matter heading to end
    start, end = _find_block_by_text(html, next_title, search_start=last_backmatter)
    if start is not None:
        html = html[:start].rstrip()
    return html


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
    # Handle multi-book reviews: "Title A / Title B" — match either part
    book_parts = [p.strip() for p in clean_book.split('/') if p.strip()]
    rx_parts = [_text_to_regex(p) for p in book_parts if p]
    rx_parts = [r for r in rx_parts if r is not None]
    if not rx_parts:
        return None, None

    # Publication detail markers: publisher names, year in parens, pp, price
    pub_markers = re.compile(
        r'(?:\d{4}|pp\.?\s*\d|ISBN|\$|£|Routledge|Sage|Springer|Press|'
        r'Publisher|Continuum|Wiley|Penguin|Karnac|Palgrave|Norton|'
        r'Oxford|Cambridge|London|New York|Duckworth|Blackwell)', re.IGNORECASE)

    for m in re.finditer(r'<(p|h[1-6])[^>]*>.*?</\1>', html[search_start:], re.DOTALL):
        block_text = _clean(_strip_tags(m.group()))
        raw_block = _strip_tags(m.group())
        # Does this block contain the book title?
        has_title = any(rx.search(block_text) for rx in rx_parts)
        if not has_title:
            continue
        # Does it also have publication markers (or is it very short like a heading)?
        has_pub = bool(pub_markers.search(raw_block))
        is_short_heading = len(block_text.split()) <= 10
        if has_pub or is_short_heading:
            return (search_start + m.start(), search_start + m.end())

    # Fallback: just find the title block without requiring pub markers
    for part_rx in rx_parts:
        for m in re.finditer(r'<(p|h[1-6])[^>]*>.*?</\1>', html[search_start:], re.DOTALL):
            block_text = _clean(_strip_tags(m.group()))
            if part_rx.search(block_text):
                return (search_start + m.start(), search_start + m.end())

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

    # Find the actual review start (publication details, not editorial mention)
    start, _ = _find_book_publication_details(html, book_title)

    if start is None:
        return html

    # Find where this review ends
    review_end = len(html)
    if next_book_title and not is_combined_review:
        next_start, _ = _find_book_publication_details(html, next_book_title, search_start=start + 1)
        if next_start is not None:
            review_end = next_start

    return html[start:review_end].strip()


# ---------------------------------------------------------------
# Editorial post-processing
# ---------------------------------------------------------------

def postprocess_editorial(html, article):
    """Post-process an editorial: strip title, keep body."""
    html = strip_start_bleed(html, article.get('title', ''))
    html = strip_title(html, article.get('title', ''))
    html = strip_authors(html, article.get('authors', ''))
    html = strip_end_bleed(html, article.get('_next_title', ''))
    return html


def postprocess_book_review_editorial(html, article):
    """Post-process a book review editorial (section intro).

    Strip the "BOOK REVIEWS" heading, keep the editorial body.
    """
    html = strip_start_bleed(html, article.get('title', ''))
    # Remove "BOOK REVIEWS" heading
    html = re.sub(
        r'<h[1-3][^>]*>\s*BOOK\s+REVIEWS?\s*</h[1-3]>\s*',
        '', html, count=1, flags=re.IGNORECASE
    )
    html = strip_end_bleed(html, article.get('_next_title', ''))
    return html


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
        # Detect combined reviews: consecutive book review entries with identical
        # page ranges. These are one review covering multiple books —
        # don't cut at the next entry's title.
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
    elif section == 'Book Review Editorial':
        html = postprocess_book_review_editorial(html, article)
    elif section == 'Editorial':
        html = postprocess_editorial(html, article)
    else:
        # Standard article
        html = strip_start_bleed(html, article.get('title', ''))
        html = strip_title(html, article.get('title', ''))
        html = strip_authors(html, article.get('authors', ''))
        html = strip_conference_note(html)
        html = strip_abstract(html, article.get('abstract', ''))
        html = strip_keywords(html)
        html = strip_end_bleed(html, article.get('_next_title', ''))

    html = re.sub(r'\n{3,}', '\n\n', html).strip()
    return html


# ---------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------

# Section headings that satisfy the title check as a fallback
# (letters/editorials may not have their toc.json title in the PDF)
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
        r'|Prof\.?|Professor)\s*:?\s*',
        '', title, flags=re.IGNORECASE
    ).strip()
    title = re.sub(
        r'^(Professor|Prof\.?)\s*:?\s*',
        '', title, flags=re.IGNORECASE
    ).strip()
    title = re.sub(r'\s*\([^)]+\)\s*$', '', title)
    return title


def _title_in_text(title, text):
    """Check if title appears in text using fuzzy matching.

    Substring match or 80% word overlap (handles line breaks, fused words).
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
    return found / len(title_words) >= 0.8


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

    # CHECK 1: Title must appear in raw HTML (Haiku extracted the right content)
    if stripped_title and not _title_in_text(stripped_title, raw_text):
        # Fallback for letters/editorials: accept section heading
        if not any(h in raw_text.lower() for h in _SECTION_HEADINGS):
            warnings.append(f'TITLE_NOT_IN_RAW: "{stripped_title[:50]}" not found in raw HTML')

    # CHECK 2: For book reviews, the book title SHOULD be in final HTML
    if is_book_review and stripped_title:
        if not _title_in_text(stripped_title, final_text):
            warnings.append(f'BOOK_TITLE_MISSING: "{stripped_title[:50]}" not in final HTML')

    # CHECK 3: Final HTML should not be empty
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
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL):
        heading_text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if REFERENCE_HEADING_RE.match(heading_text):
            return True
    for m in re.finditer(r'<p>\s*<strong>(.*?)</strong>\s*</p>', html, re.DOTALL):
        heading_text = m.group(1).strip()
        if REFERENCE_HEADING_RE.match(heading_text):
            return True
    return False


def check_missing_refs(html, pdf_path):
    """Return True if PDF has formal References heading but HTML is missing them."""
    return pdf_has_formal_refs(pdf_path) and not html_has_refs(html)
