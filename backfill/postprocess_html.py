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

# Minimum word overlap ratio for fuzzy text matching
MATCH_THRESHOLD = 0.6
# Prefix length for substring matching (avoids matching on tiny fragments)
SUBSTRING_PREFIX_LEN = 50
# Minimum abstract length worth stripping (shorter abstracts risk false matches)
MIN_ABSTRACT_LENGTH = 30
# Final HTML shorter than this (in chars) is flagged as empty/broken
SHORT_CONTENT_THRESHOLD = 100


def _clean(text):
    """Lowercase, strip non-alphanumeric, collapse whitespace."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', text.lower())).strip()


def _strip_tags(html):
    """Remove HTML tags."""
    return re.sub(r'<[^>]+>', '', html)


def _overlap_ratio(a, b):
    """Word overlap ratio between two strings (0.0–1.0)."""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))


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


def _find_block_by_text(html, target_text, search_start=0, search_end=None):
    """Find the HTML block whose text best matches target_text.

    Returns (start_pos, end_pos) of the matching block, or (None, None).
    """
    if not target_text or len(target_text) < 5:
        return None, None
    if search_end is None:
        search_end = len(html)
    region = html[search_start:search_end]
    target_clean = _clean(target_text)

    best_match = None
    best_ratio = 0

    for m in re.finditer(r'<(p|h[1-6]|blockquote)[^>]*>.*?</\1>', region, re.DOTALL):
        block_text = _strip_tags(m.group()).strip()
        block_clean = _clean(block_text)
        if not block_clean:
            continue

        if target_clean[:SUBSTRING_PREFIX_LEN] in block_clean or block_clean in target_clean:
            ratio = 1.0
        else:
            ratio = _overlap_ratio(target_clean, block_clean)

        if ratio > best_ratio and ratio >= MATCH_THRESHOLD:
            best_ratio = ratio
            best_match = (search_start + m.start(), search_start + m.end())

    return best_match if best_match else (None, None)


# ---------------------------------------------------------------
# Article post-processing steps
# ---------------------------------------------------------------

def strip_start_bleed(html, prev_title):
    """Remove content from the previous article at the start of the HTML."""
    if not prev_title:
        return html
    # Search up to the first body heading (structural landmark)
    search_end = _find_first_body_heading(html)
    start, end = _find_block_by_text(html, prev_title, search_end=search_end)
    if start is not None:
        html = html[end:].lstrip()
    return html


def strip_title(html, title):
    """Remove the article's own title from the HTML.

    Handles multi-element titles (h1/h2 + subtitle paragraphs). Removes
    consecutive blocks from the top that collectively match the title words.
    Stops when it hits a block with no title-word overlap.
    """
    if not title:
        return html
    title_clean = _clean(title)
    if not title_clean:
        return html

    # Look at blocks from the start of the HTML — title is always first
    blocks = list(re.finditer(r'<(h[1-6]|p)[^>]*>.*?</\1>', html, re.DOTALL))
    if not blocks:
        return html

    title_words = set(title_clean.split())
    best_end = 0
    matched_words = set()

    for block in blocks:
        block_text = _clean(_strip_tags(block.group()))
        block_words = set(block_text.split())
        overlap = title_words & block_words
        if overlap:
            matched_words |= overlap
            best_end = block.end()
        else:
            # Non-matching block — stop consuming
            break

    if matched_words and len(matched_words) / len(title_words) >= MATCH_THRESHOLD:
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

def extract_book_review(html, book_title, next_book_title=None, reviewer=None):
    """Extract a single book review from full-page HTML extraction."""
    if not book_title:
        return html

    # Strip "Book Review:" prefix for matching
    clean_book = re.sub(r'^Book Review:\s*', '', book_title, flags=re.IGNORECASE)

    # Find the target book title
    start, _ = _find_block_by_text(html, clean_book)
    if start is None:
        # Try harder — search for first few significant words
        words = [w for w in _clean(clean_book).split() if len(w) > 3][:4]
        if words:
            pattern = r'<(p|h[1-6])[^>]*>[^<]*' + r'[^<]*'.join(re.escape(w) for w in words) + r'[^<]*</\1>'
            m = re.search(pattern, html.lower())
            if m:
                start = m.start()

    if start is None:
        return html

    # Find where this review ends
    review_end = len(html)
    if next_book_title:
        clean_next = re.sub(r'^Book Review:\s*', '', next_book_title, flags=re.IGNORECASE)
        # Search for next book after the current title block (skip past it)
        _, current_end = _find_block_by_text(html, clean_book, search_start=start)
        search_after = current_end if current_end else start + len(clean_book)
        next_start, _ = _find_block_by_text(html, clean_next, search_start=search_after)
        if next_start is not None:
            review_end = next_start

    return html[start:review_end].strip()


# ---------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------

def postprocess_article(html, article, pdf_path=None):
    """Run the full post-processing pipeline on raw HTML.

    Args:
        html: raw HTML from Haiku (full extraction)
        article: toc.json article dict
        pdf_path: path to split PDF (for ref verification)
    """
    is_book_review = article.get('section', '') in ('Book Reviews', 'Book Review')

    if is_book_review:
        html = extract_book_review(
            html,
            book_title=article.get('title', ''),
            next_book_title=article.get('_next_title', ''),
            reviewer=article.get('reviewer', ''),
        )
    else:
        html = strip_start_bleed(html, article.get('_prev_title', ''))
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
