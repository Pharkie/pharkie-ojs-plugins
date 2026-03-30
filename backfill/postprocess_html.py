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
# Running headers from print layout (journal name, volume info)
RUNNING_HEADER_RE = re.compile(
    r'^\s*<p[^>]*>\s*(?:<em>|<i>)?\s*(?:Existential\s+Analysis\s*:\s*)?'
    r'Journal\s+of\s+(?:The\s+|the\s+)?Society\s+for\s+Existential\s+Analysis'
    r'\s*(?:</em>|</i>)?\s*</p>\s*$',
    re.IGNORECASE | re.MULTILINE
)
# Bare page numbers from print layout (standalone number in a <p>)
PAGE_NUMBER_RE = re.compile(
    r'^\s*<p[^>]*>\s*\d{1,4}\s*</p>\s*$',
    re.MULTILINE
)
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


def _strip_tags(html):
    """Remove HTML tags."""
    return re.sub(r'<[^>]+>', '', html)



def _find_first_body_heading(html):
    """Find the position of the first body content heading (Introduction, etc.).

    Returns the character position, or len(html) if not found.
    Skips "Abstract", "Keywords" headings, and person-name headings
    (author bylines that Haiku sometimes renders as <h2>).
    """
    skip_headings = {'abstract', 'keywords', 'key words'}
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL | re.IGNORECASE):
        heading_text = _strip_tags(m.group(1)).strip()
        if heading_text.lower() in skip_headings:
            continue
        # Skip person-name headings (author bylines rendered as h2/h3)
        if looks_like_person_name(heading_text):
            continue
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
    if not target_text or len(target_text) < MIN_TARGET_TEXT_LEN:
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

        # Skip provenance notes — they sit between title and author
        # and must be preserved for extraction as JATS provenance.
        raw_text = _strip_tags(block.group()).strip()
        if is_provenance(raw_text):
            continue

        # Only consume if: block has title word overlap AND is short
        # enough to be a title/subtitle element (not a body paragraph).
        # A title block should be roughly title-length, not 10x longer.
        is_title_sized = len(block_text) <= title_len * TITLE_BLOCK_MAX_RATIO
        is_heading = block.group().startswith('<h')

        if overlap and (is_title_sized or is_heading):
            matched_words |= overlap
            best_end = block.end()
        else:
            break

    if matched_words and len(matched_words) > len(title_words) * TITLE_WORD_MATCH_RATIO:
        html = html[best_end:].lstrip()

    return html


def strip_subtitle(html, subtitle):
    """Remove the article's subtitle from the HTML body.

    When subtitles are extracted into toc.json, they also need stripping
    from the HTML body to avoid duplication (subtitle appears as metadata
    AND in the body text).
    """
    if not subtitle:
        return html
    subtitle_clean = _clean(subtitle)
    if not subtitle_clean:
        return html

    subtitle_words = set(subtitle_clean.split())

    # Look at the first few blocks — subtitle is typically right at the top
    # (title heading already stripped by strip_title before this runs)
    blocks = list(re.finditer(r'<(h[1-6]|p)[^>]*>.*?</\1>', html, re.DOTALL))

    for block in blocks[:5]:  # only check first 5 blocks
        block_text = _clean(_strip_tags(block.group()))
        block_words = set(block_text.split())
        overlap = subtitle_words & block_words

        if overlap and len(overlap) > len(subtitle_words) * TITLE_WORD_MATCH_RATIO:
            # Found the subtitle block — remove it
            html = html[:block.start()] + html[block.end():]
            html = html.lstrip()
            break
        elif block_words - subtitle_words:
            # Non-subtitle content found — stop looking
            break

    return html


def _author_name_variants(authors):
    """Generate matching variants for an author name string.

    Handles cases where HTML drops middle names/initials that appear in
    toc.json. E.g. "Luis M. Rodriguez" → also try "Luis Rodriguez",
    "Edgar Agrela Correia" → also try "Edgar Correia".

    Returns a list of name strings to try, from most specific to least.
    """
    variants = [authors]
    # Split individual authors (comma or "and" separated)
    # Then for each, try dropping middle names/initials
    # Build a first-name + last-name only variant
    parts = re.split(r',\s*|\s+and\s+', authors)
    short_parts = []
    for part in parts:
        words = part.strip().split()
        if len(words) > 2:
            # Try first + last only (drop middle names/initials)
            short_parts.append(f'{words[0]} {words[-1]}')
        else:
            short_parts.append(part.strip())
    short_variant = ', '.join(short_parts) if ',' in authors else ' and '.join(short_parts) if ' and ' in authors else short_parts[0]
    if short_variant != authors:
        variants.append(short_variant)
    return variants


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
    # Try full name first, then variants with middle names dropped
    for variant in _author_name_variants(authors):
        start, end = _find_block_by_text(html, variant, search_end=search_end)
        if start is not None:
            html = html[:start] + html[end:]
            html = html.lstrip()
            break
    return html


def strip_abstract(html, abstract):
    """Remove abstract heading and paragraph(s) from the HTML.

    Uses fuzzy word-overlap matching (not ordered-word regex) because
    Haiku OCR may introduce errors in the abstract text (e.g. "clinicians"
    → "citizenicians"). 80% word overlap is tolerant of OCR errors.
    """
    if not abstract or len(abstract) < MIN_ABSTRACT_LENGTH:
        return html

    # Remove "Abstract" heading if present
    html = re.sub(r'<h[23][^>]*>\s*Abstract\s*</h[23]>\s*', '', html, count=1, flags=re.IGNORECASE)

    # Search for the abstract paragraph in early blocks using fuzzy matching
    search_end = _find_first_body_heading(html)
    abs_clean = _clean(abstract)
    abs_words = set(abs_clean.split())
    if not abs_words:
        return html

    region = html[:search_end]
    for m in re.finditer(r'<p[^>]*>.*?</p>', region, re.DOTALL):
        block_text = _clean(_strip_tags(m.group()))
        block_words = set(block_text.split())
        if not block_words:
            continue
        overlap = len(abs_words & block_words) / len(abs_words)
        if overlap > ABSTRACT_OVERLAP_THRESHOLD:
            html = html[:m.start()] + html[m.end():]
            html = html.lstrip()
            break

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
    # Handle multi-book reviews: "Title A / Title B" — find earliest part
    book_parts = [p.strip() for p in clean_book.split('/') if p.strip()]
    rx_parts = [_text_to_regex(p) for p in book_parts if p]
    rx_parts = [r for r in rx_parts if r is not None]
    if not rx_parts:
        return None, None

    # Publication detail markers: year, page count, price, or publisher name.
    # Publisher names imported from citations.py (single source of truth).
    pub_markers = re.compile(
        r'(?:\d{4}|pp\.?\s*\d|ISBN|\$|£|'
        + PUBLISHER_NAMES +
        r'|Oxford|Cambridge|London|New York)', re.IGNORECASE)

    # Find the earliest block matching ANY part with publication details
    earliest = None
    for m in re.finditer(r'<(p|h[1-6])[^>]*>.*?</\1>', html[search_start:], re.DOTALL):
        block_text = _clean(_strip_tags(m.group()))
        raw_block = _strip_tags(m.group())
        has_title = any(rx.search(block_text) for rx in rx_parts)
        if not has_title:
            continue
        has_pub = bool(pub_markers.search(raw_block))
        is_short_heading = len(block_text.split()) <= 10
        if has_pub or is_short_heading:
            earliest = (search_start + m.start(), search_start + m.end())
            break  # first match = earliest

    if earliest:
        return earliest

    # Fallback: find earliest block matching any part without pub markers
    for m in re.finditer(r'<(p|h[1-6])[^>]*>.*?</\1>', html[search_start:], re.DOTALL):
        block_text = _clean(_strip_tags(m.group()))
        if any(rx.search(block_text) for rx in rx_parts):
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
        html = strip_subtitle(html, article.get('subtitle', ''))
        html = strip_authors(html, article.get('authors', ''))
        # Conference/presentation notes are preserved in the body — they flow
        # into JATS and are extracted as provenance by extract_citations.py.
        html = strip_abstract(html, article.get('abstract', ''))
        html = strip_keywords(html)
        # Second pass: Haiku sometimes renders the title twice (h1 + h2).
        # After stripping abstract/keywords, a duplicate title heading may
        # now be at the top.
        html = strip_title(html, article.get('title', ''))
        html = strip_subtitle(html, article.get('subtitle', ''))
        html = strip_end_bleed(html, article.get('_next_title', ''))

    # Strip footnote superscripts from headings. PDF extraction preserves
    # <sup>1</sup> etc. which are endnote markers, not heading content.
    # Also strip bare trailing digits that result from <sup> tag stripping.
    html = re.sub(
        r'(<h[1-6][^>]*>)(.*?)(</h[1-6]>)',
        lambda m: m.group(1) + re.sub(r'<sup>\d+</sup>', '', m.group(2)) + m.group(3),
        html, flags=re.DOTALL
    )

    # Normalise ALL CAPS headings to title case. Older issues used
    # ALL CAPS as a print styling convention — not intentional emphasis.
    def _normalise_heading(m):
        tag_open = m.group(1)
        content = m.group(2)
        tag_close = m.group(3)
        return tag_open + normalise_allcaps(content) + tag_close

    html = re.sub(r'(<h[1-6][^>]*>)(.*?)(</h[1-6]>)', _normalise_heading, html, flags=re.DOTALL)

    # Strip running headers and bare page numbers from print layout.
    # These are artefacts from PDF→HTML conversion that appear throughout
    # the body (e.g. "Existential Analysis: Journal of The Society...")
    html = RUNNING_HEADER_RE.sub('', html)
    html = PAGE_NUMBER_RE.sub('', html)

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

    # CHECK 1: Title must appear in raw HTML (Haiku extracted the right content)
    if stripped_title and not _title_in_text_fuzzy(stripped_title, raw_text):
        # Fallback for letters/editorials: accept section heading
        if not any(h in raw_text.lower() for h in _SECTION_HEADINGS):
            warnings.append(f'TITLE_NOT_IN_RAW: "{stripped_title[:50]}" not found in raw HTML')

    # CHECK 2: For book reviews, the book title SHOULD be in final HTML.
    # For multi-book titles ("Title A / Title B"), any part matching is sufficient.
    if is_book_review and stripped_title:
        parts = [p.strip() for p in stripped_title.split('/') if p.strip()]
        if not any(_title_in_text_fuzzy(p, final_text) for p in parts):
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
