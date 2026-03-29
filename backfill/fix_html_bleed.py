#!/usr/bin/env python3
"""
Detect and fix HTML bleed in book review galleys.

Book reviews share PDF pages — split PDFs include full pages, so Haiku
may extract text from adjacent reviews. This script audits and trims bleed.

Usage:
    python backfill/fix_html_bleed.py --report backfill/private/output/*/toc.json
    python backfill/fix_html_bleed.py --trim --dry-run backfill/private/output/*/toc.json
    python backfill/fix_html_bleed.py --trim backfill/private/output/*/toc.json
    python backfill/fix_html_bleed.py --clean-headers backfill/private/output/*/toc.json
"""

import sys
import os
import re
import json
import argparse
import shutil
from html import unescape


# --- Constants ---

# Byline detection: max length of a byline element (multiple of name length + offset)
BYLINE_LENGTH_MULTIPLIER = 3
BYLINE_LENGTH_OFFSET = 30
# Minimum normalized title length to attempt matching
MIN_TITLE_LENGTH = 5
# Minimum substring length when trying shorter title matches
TITLE_SUBSTRING_MIN = 15
# Position threshold: bleed must be past this fraction of the HTML to count
BLEED_POSITION_THRESHOLD = 0.3
# Minimum chars of preamble/tail to flag as bleed
MIN_PREAMBLE_FOR_BLEED = 50
MIN_PREAMBLE_LARGE = 200
MIN_PREV_BYLINE_PREAMBLE = 20
MIN_TAIL_FOR_BLEED = 50
# Safety: don't trim more than this fraction of content
MAX_TRIM_FRACTION = 0.7

# PDF running headers that Haiku may extract into HTML (standalone <p> tags)
RUNNING_HEADER_PATTERNS = [
    re.compile(r'<p[^>]*>\s*Journal\s+of\s+the\s+Society\s+for\s+Existential\s+Analysis\s*</p>', re.IGNORECASE),
    re.compile(r'<p[^>]*>\s*Existential\s+Analysis:?\s+Journal\s+of\s+[Tt]he\s+Society\s+for\s+Existential\s+Analysis\s*</p>', re.IGNORECASE),
    re.compile(r'<p[^>]*>\s*Existential\s+Analysis\s*</p>', re.IGNORECASE),
    re.compile(r'<p[^>]*>\s*Book\s+Reviews?\s*</p>', re.IGNORECASE),
    re.compile(r'<p[^>]*>\s*\d{1,3}\s*</p>'),  # Bare page numbers
    # Issue identifier headers: "Existential Analysis 23.1: January 2012" (bold or plain)
    re.compile(r'<p[^>]*>\s*(?:<strong[^>]*>\s*)?Existential\s+Analysis\s+\d+\.\d+\s*:\s*\w+\s+\d{4}\s*(?:</strong>\s*)?</p>', re.IGNORECASE),
    # Same wrapped in <div class="article-header">...</div>
    re.compile(r'<div\s+class="article-header">\s*<p[^>]*>\s*(?:<strong[^>]*>\s*)?Existential\s+Analysis\s+\d+\.\d+\s*:\s*\w+\s+\d{4}\s*(?:</strong>\s*)?</p>\s*(?:<h[1-6][^>]*>.*?</h[1-6]>\s*)*</div>', re.IGNORECASE | re.DOTALL),
]

# Embedded running headers — (pattern, replacement) tuples for headers mixed into content
EMBEDDED_HEADER_PATTERNS = [
    # Start of <p>: page number + journal header → keep just <p>
    (re.compile(
        r'(<p[^>]*>)\s*\d{1,3}\s+(?:Existential Analysis:?\s*)?'
        r'Journal of [Tt]he Society for Existential Analysis\s+',
        re.IGNORECASE
    ), r'\1'),
    # Start of <p>: journal header without page number → keep just <p>
    (re.compile(
        r'(<p[^>]*>)\s*(?:Existential Analysis:?\s*)?'
        r'Journal of [Tt]he Society for Existential Analysis\s+',
        re.IGNORECASE
    ), r'\1'),
    # Start of <p>: "Book Reviews" followed by content → keep just <p>
    (re.compile(r'(<p[^>]*>)\s*Book Reviews\s+(?=[A-Z])'), r'\1'),
    # Mid-paragraph: journal header inline → single space
    (re.compile(
        r'\s+(?:Existential Analysis:?\s*)?'
        r'Journal of [Tt]he Society for Existential Analysis\s+',
        re.IGNORECASE
    ), ' '),
    # Mid-paragraph: "Book Reviews" followed by content → single space
    (re.compile(r'\s+Book Reviews\s+(?=[A-Z])'), ' '),
    # Trailing page numbers: "text 47</p>" → "text</p>"
    (re.compile(r'\s+\d{1,3}\s*</p>'), '</p>'),
    # Leading page numbers: "<p>48 text" → "<p>text"
    (re.compile(r'(<p[^>]*>)\s*\d{1,3}\s+'), r'\1'),
]

# Back matter headings that indicate end of article content
BACK_MATTER_HTML_PATTERNS = [
    re.compile(r'<h[1-6][^>]*>\s*Publications?\s+(and\s+films?\s+)?[Rr]eceived\s+for\s+[Rr]eview\s*</h[1-6]>', re.I),
    re.compile(r'<p[^>]*>\s*<strong>\s*Publications?\s+(and\s+films?\s+)?[Rr]eceived.*?</strong>\s*</p>', re.I),
    re.compile(r'<h[1-6][^>]*>\s*Advertising\s+Rates\s*</h[1-6]>', re.I),
    re.compile(r'<h[1-6][^>]*>\s*Back\s+Issues\s+and\s+Publications\s*</h[1-6]>', re.I),
    re.compile(r'<h[1-6][^>]*>\s*Information\s+for\s+Contributors\s*</h[1-6]>', re.I),
]


# --- Text normalization ---

def normalize_for_match(text):
    """Normalize text for fuzzy matching: lowercase, collapse whitespace, strip HTML tags."""
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def normalize_name(name):
    """Normalize a reviewer name for matching."""
    if not name:
        return ''
    name = name.strip()
    name = re.sub(r'^(reviewed by|review by)\s+', '', name, flags=re.IGNORECASE)
    return normalize_for_match(name)


def text_contains(haystack_norm, needle_norm, min_len=3):
    """Check if normalized haystack contains normalized needle."""
    if not needle_norm or len(needle_norm) < min_len:
        return False
    return needle_norm in haystack_norm


# --- HTML analysis ---

def strip_running_headers(html):
    """Remove PDF running headers/footers from HTML.

    Handles both standalone header paragraphs (removed entirely) and
    headers embedded within content paragraphs (prefix/inline stripped).

    Returns (cleaned_html, count_removed).
    """
    count = 0
    # Phase 1: Remove standalone header paragraphs
    for pattern in RUNNING_HEADER_PATTERNS:
        html, n = pattern.subn('', html)
        count += n
    # Phase 2: Strip embedded headers within content paragraphs
    for pattern, replacement in EMBEDDED_HEADER_PATTERNS:
        html, n = pattern.subn(replacement, html)
        count += n
    if count:
        html = re.sub(r'\n{3,}', '\n\n', html)
    return html, count


def _write_html_with_backup(html_path, content):
    """Write HTML content, creating a .bak backup if one doesn't exist."""
    bak_path = html_path + '.bak'
    if not os.path.exists(bak_path):
        shutil.copy2(html_path, bak_path)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(content)


def find_reviewer_byline_positions(html, reviewer_name):
    """Find positions of reviewer byline in HTML.

    Returns list of (start, end) positions of paragraphs/elements that
    appear to be the reviewer's byline (standalone name in bold/strong
    or as a short paragraph).
    """
    if not reviewer_name:
        return []

    name_norm = normalize_name(reviewer_name)
    if not name_norm:
        return []

    max_len = len(name_norm) * BYLINE_LENGTH_MULTIPLIER + BYLINE_LENGTH_OFFSET
    positions = []

    # Match <p> or <strong> blocks containing the reviewer name
    for m in re.finditer(
        r'<(?:p|strong)[^>]*>([^<]*(?:<(?:strong|em|b|i)[^>]*>[^<]*</(?:strong|em|b|i)>[^<]*)*)</(?:p|strong)>',
        html, re.IGNORECASE
    ):
        block_text = normalize_for_match(m.group(0))
        if text_contains(block_text, name_norm) and len(block_text) < max_len:
            positions.append((m.start(), m.end()))

    # Also check for <p><strong>Name</strong></p> pattern
    for m in re.finditer(
        r'<p[^>]*>\s*<strong[^>]*>(.*?)</strong>\s*</p>',
        html, re.IGNORECASE | re.DOTALL
    ):
        block_text = normalize_for_match(m.group(1))
        if text_contains(block_text, name_norm) and len(block_text) < max_len:
            if (m.start(), m.end()) not in positions:
                positions.append((m.start(), m.end()))

    return positions


def find_book_title_position(html, book_title):
    """Find the position of the book title in the HTML.

    Returns (start, end) of the element containing the title, or None.
    """
    if not book_title:
        return None

    title_norm = normalize_for_match(book_title)
    if not title_norm or len(title_norm) < MIN_TITLE_LENGTH:
        return None

    html_norm = normalize_for_match(html)
    if not text_contains(html_norm, title_norm):
        return None

    # Try full title, then shorter substring
    for try_len in [len(title_norm), max(len(title_norm) // 2, TITLE_SUBSTRING_MIN)]:
        search = title_norm[:try_len]
        for m in re.finditer(r'<(?:p|h[1-6]|strong|em)[^>]*>.*?</(?:p|h[1-6]|strong|em)>',
                             html, re.IGNORECASE | re.DOTALL):
            elem_norm = normalize_for_match(m.group(0))
            if text_contains(elem_norm, search):
                return (m.start(), m.end())

    return None


def find_references_section(html, after_pos):
    """Find a References section starting after the given position.

    Returns (start, end) of the references section, or None.
    """
    ref_match = re.search(
        r'<h[1-6][^>]*>\s*(?:References?|Bibliography|Works?\s+Cited)\s*</h[1-6]>',
        html[after_pos:], re.IGNORECASE
    )
    if not ref_match:
        return None

    ref_start = after_pos + ref_match.start()
    rest = html[ref_start + ref_match.end() - ref_start:]

    # Find next heading that is NOT a references heading
    for m in re.finditer(r'<h[1-6][^>]*>(.*?)</h[1-6]>', rest, re.IGNORECASE | re.DOTALL):
        heading_text = m.group(1).lower()
        if not any(x in heading_text for x in ['reference', 'bibliography', 'works cited']):
            return (ref_start, ref_start + (ref_match.end() - ref_match.start()) + m.start())

    return (ref_start, len(html))


# --- Review collection ---

def collect_book_reviews(toc_paths):
    """Collect all book review articles from toc.json files with prev/next context."""
    reviews = []

    for toc_path in toc_paths:
        with open(toc_path) as f:
            toc = json.load(f)

        vol = toc.get('volume', '?')
        iss = toc.get('issue', '?')

        issue_reviews = []
        for idx, article in enumerate(toc.get('articles', [])):
            if article.get('section') != 'Book Reviews':
                continue
            if not article.get('split_pdf'):
                continue

            html_path = os.path.splitext(article['split_pdf'])[0] + '.html'
            if not os.path.exists(html_path):
                continue

            issue_reviews.append({
                'toc_path': toc_path,
                'vol': vol,
                'iss': iss,
                'idx': idx,
                'article': article,
                'html_path': html_path,
                'title': article.get('title', ''),
                'book_title': article.get('book_title', ''),
                'reviewer': article.get('reviewer', ''),
                'book_author': article.get('book_author', ''),
                'pdf_page_start': article.get('pdf_page_start'),
                'pdf_page_end': article.get('pdf_page_end'),
            })

        for i, rev in enumerate(issue_reviews):
            rev['prev_review'] = issue_reviews[i - 1] if i > 0 else None
            rev['next_review'] = issue_reviews[i + 1] if i < len(issue_reviews) - 1 else None
            reviews.append(rev)

    return reviews


# --- Classification ---

def classify_review(review):
    """Classify a book review's HTML bleed status.

    Categories: clean, end-bleed, start-bleed, both-bleed, suspect-split,
    no-byline, no-title, auto-extracted
    """
    html_path = review['html_path']
    with open(html_path, encoding='utf-8') as f:
        html = f.read()

    if '<!-- AUTO-EXTRACTED' in html:
        return {'category': 'auto-extracted', 'details': 'PyMuPDF fallback', 'trim_info': None}

    html_norm = normalize_for_match(html)
    book_title = review.get('book_title', '')
    reviewer = review.get('reviewer', '')
    next_review = review.get('next_review')
    _, header_count = strip_running_headers(html)

    result = {
        'category': 'clean',
        'details': '',
        'trim_info': {},
        'has_book_title': False,
        'has_reviewer': False,
        'has_end_bleed': False,
        'has_start_bleed': False,
        'end_bleed_chars': 0,
        'next_title_in_tail': False,
        'running_headers': header_count,
    }

    # Check presence of book title and reviewer
    title_norm = normalize_for_match(book_title) if book_title else ''
    if title_norm and text_contains(html_norm, title_norm):
        result['has_book_title'] = True

    reviewer_norm = normalize_name(reviewer) if reviewer else ''
    if reviewer_norm and text_contains(html_norm, reviewer_norm):
        result['has_reviewer'] = True

    # --- End-bleed detection ---
    byline_positions = find_reviewer_byline_positions(html, reviewer) if reviewer else []

    if byline_positions:
        _, last_byline_end = byline_positions[-1]
        refs = find_references_section(html, last_byline_end)
        keep_end = refs[1] if refs else last_byline_end
        tail = re.sub(r'^(\s*</?(?:p|div|br)[^>]*>\s*)*$', '', html[keep_end:].strip()).strip()

        if len(tail) > MIN_TAIL_FOR_BLEED:
            result['has_end_bleed'] = True
            result['end_bleed_chars'] = len(tail)
            if next_review and next_review.get('book_title'):
                next_title_norm = normalize_for_match(next_review['book_title'])
                if next_title_norm and text_contains(normalize_for_match(tail), next_title_norm):
                    result['next_title_in_tail'] = True
            result['trim_info']['keep_end'] = keep_end
            result['trim_info']['tail_length'] = len(tail)
            result['trim_info']['has_refs'] = refs is not None

    # Alternative: next review's title found in this HTML
    if not result['has_end_bleed'] and next_review and next_review.get('book_title'):
        next_title_pos = find_book_title_position(html, next_review['book_title'])
        if next_title_pos and next_title_pos[0] > len(html) * BLEED_POSITION_THRESHOLD:
            tail = html[next_title_pos[0]:].strip()
            if len(tail) > MIN_TAIL_FOR_BLEED:
                result['has_end_bleed'] = True
                result['end_bleed_chars'] = len(tail)
                result['next_title_in_tail'] = True
                result['trim_info']['next_title_start'] = next_title_pos[0]
                result['trim_info']['tail_length'] = len(tail)

    # --- Start-bleed detection ---
    if result['has_book_title'] and book_title:
        title_pos = find_book_title_position(html, book_title)
        if title_pos and title_pos[0] > MIN_PREAMBLE_FOR_BLEED:
            preamble = re.sub(r'^(\s*</?(?:p|div|br)[^>]*>\s*)*$', '', html[:title_pos[0]].strip()).strip()
            if len(preamble) > MIN_PREAMBLE_FOR_BLEED:
                prev_review = review.get('prev_review')
                prev_found = False
                if prev_review and prev_review.get('reviewer'):
                    prev_norm = normalize_name(prev_review['reviewer'])
                    if text_contains(normalize_for_match(preamble), prev_norm):
                        prev_found = True

                if prev_found or len(preamble) > MIN_PREAMBLE_LARGE:
                    result['has_start_bleed'] = True
                    result['trim_info']['title_start'] = title_pos[0]
                    result['trim_info']['preamble_length'] = len(preamble)
                    result['trim_info']['prev_reviewer_found'] = prev_found

    # Alternative: previous reviewer's byline near the start
    if not result['has_start_bleed']:
        prev_review = review.get('prev_review')
        if prev_review and prev_review.get('reviewer'):
            prev_byline_positions = find_reviewer_byline_positions(html, prev_review['reviewer'])
            if prev_byline_positions:
                first_start, first_end = prev_byline_positions[0]
                if first_start < len(html) * BLEED_POSITION_THRESHOLD:
                    preamble = html[:first_end].strip()
                    if len(preamble) > MIN_PREV_BYLINE_PREAMBLE:
                        result['has_start_bleed'] = True
                        result['trim_info']['prev_byline_end'] = first_end
                        result['trim_info']['preamble_length'] = len(preamble)
                        result['trim_info']['prev_reviewer_found'] = True

    # --- Determine category ---
    if not result['has_book_title'] and not result['has_reviewer']:
        result['category'] = 'suspect-split'
        result['details'] = 'Neither book title nor reviewer found in HTML — possible wrong content'
    elif result['has_end_bleed'] and result['has_start_bleed']:
        result['category'] = 'both-bleed'
        result['details'] = (f"End-bleed ({result['end_bleed_chars']} chars) "
                             f"AND start-bleed ({result['trim_info'].get('preamble_length', '?')} chars)")
    elif result['has_end_bleed']:
        result['category'] = 'end-bleed'
        result['details'] = f"{result['end_bleed_chars']} chars after reviewer byline"
        if result['next_title_in_tail']:
            result['details'] += ' (next review title confirmed in tail)'
    elif result['has_start_bleed']:
        result['category'] = 'start-bleed'
        result['details'] = f"{result['trim_info'].get('preamble_length', '?')} chars before book title"
    elif not result['has_reviewer']:
        result['category'] = 'no-byline'
        result['details'] = 'Reviewer name not found in HTML (likely Haiku omission)'
    elif not result['has_book_title']:
        result['category'] = 'no-title'
        result['details'] = 'Book title not in HTML (Haiku may have stripped per prompt instruction)'
    else:
        result['category'] = 'clean'
        result['details'] = 'Book title and reviewer found, no bleed detected'

    return result


# --- Trimming ---

def trim_review(review, classification, dry_run=False):
    """Trim bleed from a single review's HTML.

    Returns (modified, description) or (False, reason_skipped).
    """
    html_path = review['html_path']
    with open(html_path, encoding='utf-8') as f:
        html = f.read()

    original_len = len(html)
    trim_info = classification.get('trim_info') or {}
    cat = classification['category']

    modified_html = html
    changes = []
    offset = 0  # Track cumulative offset from start-trimming

    # Strip running headers (always)
    modified_html, header_count = strip_running_headers(modified_html)
    if header_count:
        changes.append(f"removed {header_count} running header(s)")

    # Trim start-bleed
    if cat in ('start-bleed', 'both-bleed') and 'prev_byline_end' in trim_info:
        trim_pos = trim_info['prev_byline_end']
        preamble = modified_html[:trim_pos].strip()
        if preamble:
            modified_html = modified_html[trim_pos:].lstrip()
            offset += trim_pos
            changes.append(f"trimmed {len(preamble)} chars of start-bleed (prev reviewer byline)")
    elif cat in ('start-bleed', 'both-bleed') and 'title_start' in trim_info:
        trim_pos = trim_info['title_start']
        preamble = modified_html[:trim_pos].strip()
        if preamble:
            modified_html = modified_html[trim_pos:]
            offset += trim_pos
            changes.append(f"trimmed {len(preamble)} chars of start-bleed")

    # Trim end-bleed (positions adjusted for any start-trim offset)
    if cat in ('end-bleed', 'both-bleed') and 'next_title_start' in trim_info:
        cut_pos = trim_info['next_title_start'] - offset
        if 0 < cut_pos < len(modified_html):
            tail = modified_html[cut_pos:].strip()
            modified_html = modified_html[:cut_pos].rstrip()
            changes.append(f"trimmed {len(tail)} chars of end-bleed (next review title)")
    elif cat in ('end-bleed', 'both-bleed') and 'keep_end' in trim_info:
        keep_end = trim_info['keep_end'] - offset
        if 0 < keep_end < len(modified_html):
            tail_clean = re.sub(r'^(\s*</?(?:p|div|br)[^>]*>\s*)*$', '', modified_html[keep_end:].strip()).strip()
            if len(tail_clean) > MIN_TAIL_FOR_BLEED:
                modified_html = modified_html[:keep_end].rstrip()
                changes.append(f"trimmed {len(tail_clean)} chars of end-bleed")

    # Safety check
    if len(modified_html) < original_len * (1 - MAX_TRIM_FRACTION):
        return False, f"would remove {100 - len(modified_html) * 100 // original_len}% of content (>{int(MAX_TRIM_FRACTION * 100)}% threshold)"

    if not changes:
        return False, 'no changes needed'

    if not dry_run:
        _write_html_with_backup(html_path, modified_html)

    return True, '; '.join(changes)


# --- Commands ---

def run_report(toc_paths):
    """Run audit report on all book reviews."""
    reviews = collect_book_reviews(toc_paths)

    if not reviews:
        print("No book reviews with HTML galleys found.")
        return

    print(f"\nAuditing {len(reviews)} book reviews...\n")

    categories = {}
    results = []

    for rev in reviews:
        classification = classify_review(rev)
        cat = classification['category']
        categories.setdefault(cat, [])
        categories[cat].append(rev)
        results.append({
            'vol': rev['vol'],
            'iss': rev['iss'],
            'idx': rev['idx'] + 1,
            'title': rev['title'][:80],
            'book_title': rev.get('book_title', '')[:60],
            'reviewer': rev.get('reviewer', ''),
            'html_path': rev['html_path'],
            'category': cat,
            'details': classification['details'],
            'has_book_title': classification.get('has_book_title', False),
            'has_reviewer': classification.get('has_reviewer', False),
            'end_bleed_chars': classification.get('end_bleed_chars', 0),
            'next_title_in_tail': classification.get('next_title_in_tail', False),
        })

    print("=" * 60)
    print("Book Review HTML Bleed Report")
    print("=" * 60)
    print(f"\nTotal book reviews: {len(reviews)}\n")

    category_order = ['clean', 'end-bleed', 'start-bleed', 'both-bleed',
                       'no-byline', 'no-title', 'suspect-split', 'auto-extracted']
    for cat in category_order:
        items = categories.get(cat, [])
        if items:
            print(f"  {cat:20s} {len(items):4d}")
    print()

    for cat in ['suspect-split', 'both-bleed', 'start-bleed', 'end-bleed']:
        items = categories.get(cat, [])
        if not items:
            continue
        print(f"\n{'─' * 60}")
        print(f"{cat.upper()} ({len(items)} reviews)")
        print(f"{'─' * 60}")
        for rev in items:
            r = next(r for r in results if r['html_path'] == rev['html_path'])
            print(f"  Vol {r['vol']}.{r['iss']} #{r['idx']}: {r['title'][:70]}")
            print(f"    Reviewer: {r['reviewer'] or '(none)'}")
            print(f"    {r['details']}")
            print()

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'output', 'bleed-report.json')
    report = {
        'total_reviews': len(reviews),
        'summary': {cat: len(items) for cat, items in categories.items()},
        'reviews': results,
    }
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\nDetailed report: {report_path}")


def trim_back_matter(toc_paths, dry_run=False):
    """Trim back matter (publications received, advertising, etc.) from non-editorial HTML.

    Returns (trimmed_count, checked_count).
    """
    trimmed = 0
    checked = 0
    editorial_sections = {'editorial', 'book review editorial'}

    for toc_path in toc_paths:
        with open(toc_path) as f:
            toc_data = json.load(f)

        vol = toc_data.get('volume', '?')
        iss = toc_data.get('issue', '?')

        for article in toc_data.get('articles', []):
            section = (article.get('section') or '').lower()
            if section in editorial_sections:
                continue

            split_pdf = article.get('split_pdf', '')
            if not split_pdf:
                continue
            html_path = os.path.splitext(split_pdf)[0] + '.html'
            if not os.path.exists(html_path):
                continue

            checked += 1
            with open(html_path, encoding='utf-8') as f:
                html = f.read()

            # Find earliest back matter match
            earliest_pos = None
            matched_desc = None
            for pattern in BACK_MATTER_HTML_PATTERNS:
                m = pattern.search(html)
                if m and (earliest_pos is None or m.start() < earliest_pos):
                    earliest_pos = m.start()
                    matched_desc = m.group(0)[:60]

            if earliest_pos is None:
                continue

            trimmed_html = html[:earliest_pos].rstrip()

            # Safety check
            if len(trimmed_html) < len(html) * (1 - MAX_TRIM_FRACTION):
                title = article.get('title', '?')[:50]
                print(f"  SKIP Vol {vol}.{iss} '{title}': back matter trim would remove "
                      f"{100 - len(trimmed_html) * 100 // len(html)}% of content")
                continue

            removed = len(html) - len(trimmed_html)
            title = article.get('title', '?')[:50]
            prefix = "[DRY RUN] " if dry_run else ""
            print(f"  {prefix}Vol {vol}.{iss} '{title}': trimmed {removed} chars of back matter")

            if not dry_run:
                _write_html_with_backup(html_path, trimmed_html)
            trimmed += 1

    return trimmed, checked


def run_trim(toc_paths, dry_run=False):
    """Trim bleed from book reviews and back matter from all articles."""
    reviews = collect_book_reviews(toc_paths)

    if not reviews:
        print("No book reviews with HTML galleys found.")
    else:
        print(f"\n{'DRY RUN — ' if dry_run else ''}Trimming bleed from {len(reviews)} book reviews...\n")

    trimmed = 0
    skipped = 0
    errors = 0

    for rev in reviews:
        classification = classify_review(rev)
        cat = classification['category']

        # Only trim bleed categories + clean (for header stripping)
        if cat not in ('end-bleed', 'start-bleed', 'both-bleed', 'clean'):
            continue

        label = f"Vol {rev['vol']}.{rev['iss']} #{rev['idx']+1}"

        try:
            modified, description = trim_review(rev, classification, dry_run=dry_run)
            if modified:
                trimmed += 1
                prefix = "[DRY RUN] " if dry_run else ""
                print(f"  {prefix}{label}: {description}")
            else:
                skipped += 1
                if 'threshold' in description:
                    print(f"  SKIP {label}: {description}")
        except Exception as e:
            errors += 1
            print(f"  ERROR {label}: {e}", file=sys.stderr)

    # Back matter trimming pass (all non-editorial articles)
    print(f"\n{'DRY RUN — ' if dry_run else ''}Scanning for back matter...\n")
    bm_trimmed, bm_checked = trim_back_matter(toc_paths, dry_run=dry_run)

    print(f"\n{'=' * 50}")
    print(f"{'Would trim' if dry_run else 'Trimmed'} bleed: {trimmed}")
    print(f"{'Would trim' if dry_run else 'Trimmed'} back matter: {bm_trimmed}/{bm_checked} articles")
    print(f"Skipped: {skipped}")
    if errors:
        print(f"Errors: {errors}")
    if not dry_run and (trimmed or bm_trimmed):
        print(f"\nBackups saved as .html.bak files")
    print(f"{'=' * 50}")


def run_clean_headers(toc_paths, dry_run=False):
    """Strip running headers from ALL HTML galleys (not just book reviews)."""
    cleaned = 0
    checked = 0

    for toc_path in toc_paths:
        with open(toc_path) as f:
            toc_data = json.load(f)

        for article in toc_data.get('articles', []):
            split_pdf = article.get('split_pdf', '')
            if not split_pdf:
                continue
            html_path = os.path.splitext(split_pdf)[0] + '.html'
            if not os.path.exists(html_path):
                continue

            checked += 1
            with open(html_path, encoding='utf-8') as f:
                html = f.read()

            cleaned_html, count = strip_running_headers(html)
            if count:
                vol = toc_data.get('volume', '?')
                iss = toc_data.get('issue', '?')
                title = article.get('title', '?')[:50]
                prefix = "[DRY RUN] " if dry_run else ""
                print(f"  {prefix}Vol {vol}.{iss} '{title}': removed {count} header(s)")

                if not dry_run:
                    _write_html_with_backup(html_path, cleaned_html)
                cleaned += 1

    print(f"\n{'Would clean' if dry_run else 'Cleaned'} {cleaned}/{checked} HTML files")


def main():
    parser = argparse.ArgumentParser(
        description='Detect and fix HTML bleed in book review galleys')
    parser.add_argument('toc_json', nargs='+', help='toc.json file(s)')

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--report', action='store_true',
                      help='Audit book reviews and generate bleed report')
    mode.add_argument('--trim', action='store_true',
                      help='Trim detected bleed from HTML galleys')
    mode.add_argument('--clean-headers', action='store_true',
                      help='Strip running headers from ALL HTML galleys')

    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be changed without modifying files')

    args = parser.parse_args()

    valid_paths = [p for p in args.toc_json if os.path.exists(p)]
    for p in args.toc_json:
        if not os.path.exists(p):
            print(f"WARNING: {p} not found, skipping", file=sys.stderr)

    if not valid_paths:
        print("ERROR: No valid toc.json files found", file=sys.stderr)
        sys.exit(1)

    if args.report:
        run_report(valid_paths)
    elif args.trim:
        run_trim(valid_paths, dry_run=args.dry_run)
    elif args.clean_headers:
        run_clean_headers(valid_paths, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
