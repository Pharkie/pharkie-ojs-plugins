#!/usr/bin/env python3
"""
Detect and fix HTML bleed in book review galleys.

Book reviews share PDF pages — split PDFs include full pages, so Haiku
may extract text from adjacent reviews. This script audits and trims bleed.

Usage:
    python backfill/fix_html_bleed.py --report backfill/output/*/toc.json
    python backfill/fix_html_bleed.py --trim --dry-run backfill/output/*/toc.json
    python backfill/fix_html_bleed.py --trim backfill/output/*/toc.json
"""

import sys
import os
import re
import json
import argparse
import shutil
from html import unescape


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
    # Remove common prefixes
    name = re.sub(r'^(reviewed by|review by)\s+', '', name, flags=re.IGNORECASE)
    return normalize_for_match(name)


def text_contains(haystack_norm, needle_norm, min_len=3):
    """Check if normalized haystack contains normalized needle."""
    if not needle_norm or len(needle_norm) < min_len:
        return False
    return needle_norm in haystack_norm


def find_reviewer_byline_positions(html, reviewer_name):
    """Find positions of reviewer byline in HTML.

    Returns list of (start, end) positions of paragraphs/elements that
    appear to be the reviewer's byline (standalone name in bold/strong
    or as a paragraph near the end).
    """
    if not reviewer_name:
        return []

    name_norm = normalize_name(reviewer_name)
    if not name_norm:
        return []

    positions = []

    # Match <p> or <strong> blocks containing the reviewer name
    # Pattern: standalone paragraph or bold block that is primarily the name
    for m in re.finditer(
        r'<(?:p|strong)[^>]*>([^<]*(?:<(?:strong|em|b|i)[^>]*>[^<]*</(?:strong|em|b|i)>[^<]*)*)</(?:p|strong)>',
        html, re.IGNORECASE
    ):
        block_text = normalize_for_match(m.group(0))
        if text_contains(block_text, name_norm):
            # Check it's primarily the name (not a paragraph that mentions them)
            # Bylines are short — typically just the name, maybe with a title
            if len(block_text) < len(name_norm) * 3 + 30:
                positions.append((m.start(), m.end()))

    # Also check for name in <p><strong>Name</strong></p> pattern
    for m in re.finditer(
        r'<p[^>]*>\s*<strong[^>]*>(.*?)</strong>\s*</p>',
        html, re.IGNORECASE | re.DOTALL
    ):
        block_text = normalize_for_match(m.group(1))
        if text_contains(block_text, name_norm):
            if len(block_text) < len(name_norm) * 3 + 30:
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
    if not title_norm or len(title_norm) < 5:
        return None

    html_norm = normalize_for_match(html)
    if not text_contains(html_norm, title_norm):
        return None

    # Find it in actual HTML — look for it in any element
    # Try progressively shorter substrings if full title not found
    for try_len in [len(title_norm), max(len(title_norm) // 2, 15)]:
        search = title_norm[:try_len]
        # Walk through HTML elements to find which one contains it
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
    # Look for a references heading after the position
    ref_match = re.search(
        r'<h[1-6][^>]*>\s*(?:References?|Bibliography|Works?\s+Cited)\s*</h[1-6]>',
        html[after_pos:], re.IGNORECASE
    )
    if not ref_match:
        return None

    ref_start = after_pos + ref_match.start()

    # References section extends to the end of consecutive <p> blocks
    # or until another major heading
    rest = html[ref_start:]
    # Find next non-reference heading or end
    next_heading = re.search(r'<h[1-6][^>]*>(?!.*(?:References?|Bibliography))', rest[ref_match.end():], re.IGNORECASE)
    if next_heading:
        ref_end = ref_start + ref_match.end() + next_heading.start()
    else:
        ref_end = len(html)

    return (ref_start, ref_end)


def collect_book_reviews(toc_paths):
    """Collect all book review articles from toc.json files.

    Returns list of dicts with article info and context (prev/next review).
    """
    reviews = []

    for toc_path in toc_paths:
        with open(toc_path) as f:
            toc = json.load(f)

        vol = toc.get('volume', '?')
        iss = toc.get('issue', '?')
        articles = toc.get('articles', [])

        # Find all book reviews in this issue
        issue_reviews = []
        for idx, article in enumerate(articles):
            section = article.get('section', '')
            if section not in ('Book Reviews', 'Book Review'):
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

        # Add prev/next context
        for i, rev in enumerate(issue_reviews):
            rev['prev_review'] = issue_reviews[i - 1] if i > 0 else None
            rev['next_review'] = issue_reviews[i + 1] if i < len(issue_reviews) - 1 else None
            reviews.append(rev)

    return reviews


def classify_review(review):
    """Classify a book review's HTML bleed status.

    Returns dict with:
        category: clean|end-bleed|start-bleed|suspect-split|no-byline
        details: human-readable explanation
        trim_info: dict with trim positions if applicable
    """
    html_path = review['html_path']
    with open(html_path, encoding='utf-8') as f:
        html = f.read()

    # Skip auto-extracted (PyMuPDF fallback) files
    if '<!-- AUTO-EXTRACTED' in html:
        return {
            'category': 'auto-extracted',
            'details': 'PyMuPDF fallback (content-filtered by Haiku)',
            'trim_info': None,
        }

    html_norm = normalize_for_match(html)
    book_title = review.get('book_title', '')
    reviewer = review.get('reviewer', '')
    next_review = review.get('next_review')

    result = {
        'category': 'clean',
        'details': '',
        'trim_info': None,
        'has_book_title': False,
        'has_reviewer': False,
        'has_end_bleed': False,
        'has_start_bleed': False,
        'end_bleed_chars': 0,
        'next_title_in_tail': False,
    }

    # Check if book title is in the HTML
    title_norm = normalize_for_match(book_title) if book_title else ''
    if title_norm and text_contains(html_norm, title_norm):
        result['has_book_title'] = True

    # Check if reviewer name is in the HTML
    reviewer_norm = normalize_name(reviewer) if reviewer else ''
    if reviewer_norm and text_contains(html_norm, reviewer_norm):
        result['has_reviewer'] = True

    # Find reviewer byline positions
    byline_positions = find_reviewer_byline_positions(html, reviewer) if reviewer else []

    # Check for end-bleed: content after reviewer byline
    if byline_positions:
        last_byline_start, last_byline_end = byline_positions[-1]

        # Check for references section after byline (should be kept)
        refs = find_references_section(html, last_byline_end)
        keep_end = refs[1] if refs else last_byline_end

        tail = html[keep_end:].strip()
        # Strip trailing whitespace-only tags
        tail = re.sub(r'^(\s*</?(?:p|div|br)[^>]*>\s*)*$', '', tail).strip()

        if len(tail) > 50:
            result['has_end_bleed'] = True
            result['end_bleed_chars'] = len(tail)

            # Cross-check: does the tail contain the next review's book title?
            if next_review and next_review.get('book_title'):
                next_title_norm = normalize_for_match(next_review['book_title'])
                tail_norm = normalize_for_match(tail)
                if next_title_norm and text_contains(tail_norm, next_title_norm):
                    result['next_title_in_tail'] = True

            result['trim_info'] = {
                'byline_end': last_byline_end,
                'keep_end': keep_end,
                'tail_length': len(tail),
                'has_refs': refs is not None,
            }

    # Alternative end-bleed detection: next review's book title found in this HTML
    # (catches cases where reviewer byline is missing)
    if not result['has_end_bleed'] and next_review and next_review.get('book_title'):
        next_title_pos = find_book_title_position(html, next_review['book_title'])
        if next_title_pos and next_title_pos[0] > len(html) * 0.3:
            tail = html[next_title_pos[0]:].strip()
            if len(tail) > 50:
                result['has_end_bleed'] = True
                result['end_bleed_chars'] = len(tail)
                result['next_title_in_tail'] = True
                result['trim_info'] = result.get('trim_info') or {}
                result['trim_info']['next_title_start'] = next_title_pos[0]
                result['trim_info']['tail_length'] = len(tail)

    # Check for start-bleed: content before book title that belongs to previous review
    if result['has_book_title'] and book_title:
        title_pos = find_book_title_position(html, book_title)
        if title_pos and title_pos[0] > 100:
            preamble = html[:title_pos[0]].strip()
            preamble = re.sub(r'^(\s*</?(?:p|div|br)[^>]*>\s*)*$', '', preamble).strip()
            if len(preamble) > 50:
                # Check if preamble contains previous review's reviewer name
                prev_review = review.get('prev_review')
                prev_reviewer_in_preamble = False
                if prev_review and prev_review.get('reviewer'):
                    prev_norm = normalize_name(prev_review['reviewer'])
                    preamble_norm = normalize_for_match(preamble)
                    if text_contains(preamble_norm, prev_norm):
                        prev_reviewer_in_preamble = True

                if prev_reviewer_in_preamble or len(preamble) > 200:
                    result['has_start_bleed'] = True
                    result['trim_info'] = result.get('trim_info') or {}
                    result['trim_info']['title_start'] = title_pos[0]
                    result['trim_info']['preamble_length'] = len(preamble)
                    result['trim_info']['prev_reviewer_found'] = prev_reviewer_in_preamble

    # Alternative start-bleed detection: previous reviewer's byline near the start
    # (catches cases where book title isn't found in HTML)
    if not result['has_start_bleed']:
        prev_review = review.get('prev_review')
        if prev_review and prev_review.get('reviewer'):
            prev_byline_positions = find_reviewer_byline_positions(html, prev_review['reviewer'])
            if prev_byline_positions:
                first_prev_byline_start, first_prev_byline_end = prev_byline_positions[0]
                # Only if the previous reviewer's byline is near the start (first 30%)
                if first_prev_byline_start < len(html) * 0.3:
                    preamble = html[:first_prev_byline_end].strip()
                    if len(preamble) > 20:
                        result['has_start_bleed'] = True
                        result['trim_info'] = result.get('trim_info') or {}
                        result['trim_info']['prev_byline_end'] = first_prev_byline_end
                        result['trim_info']['preamble_length'] = len(preamble)
                        result['trim_info']['prev_reviewer_found'] = True

    # Determine category
    if not result['has_book_title'] and not result['has_reviewer']:
        result['category'] = 'suspect-split'
        result['details'] = 'Neither book title nor reviewer found in HTML — possible wrong content'
    elif result['has_end_bleed'] and result['has_start_bleed']:
        result['category'] = 'both-bleed'
        result['details'] = (f"End-bleed ({result['end_bleed_chars']} chars after byline) "
                             f"AND start-bleed ({result['trim_info'].get('preamble_length', '?')} chars before title)")
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

    # Print summary
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

    # Print details for actionable categories
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

    # Save JSON report
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


def trim_review(review, classification, dry_run=False):
    """Trim bleed from a single review's HTML.

    Returns (modified, description) or (False, reason_skipped).
    """
    html_path = review['html_path']
    with open(html_path, encoding='utf-8') as f:
        html = f.read()

    original_len = len(html)
    trim_info = classification.get('trim_info')
    if not trim_info:
        return False, 'no trim info'

    modified_html = html
    changes = []

    # Trim start-bleed
    cat = classification['category']
    if cat in ('start-bleed', 'both-bleed') and 'prev_byline_end' in trim_info:
        # Trim using previous reviewer's byline position
        prev_byline_end = trim_info['prev_byline_end']
        preamble = modified_html[:prev_byline_end].strip()
        if preamble:
            modified_html = modified_html[prev_byline_end:].lstrip()
            changes.append(f"trimmed {len(preamble)} chars of start-bleed (prev reviewer byline)")
    elif cat in ('start-bleed', 'both-bleed') and 'title_start' in trim_info:
        title_start = trim_info['title_start']
        # Find the start of the element containing the book title
        # Keep everything from the title element onward
        preamble = modified_html[:title_start].strip()
        if preamble:
            modified_html = modified_html[title_start:]
            changes.append(f"trimmed {len(preamble)} chars of start-bleed")

    # Trim end-bleed
    if cat in ('end-bleed', 'both-bleed') and 'next_title_start' in trim_info:
        # Trim using next review's book title position
        cut_pos = trim_info['next_title_start']
        # Adjust for any start-trim offset
        if 'title_start' in trim_info:
            cut_pos -= trim_info.get('title_start', 0)
        elif 'prev_byline_end' in trim_info:
            cut_pos -= trim_info.get('prev_byline_end', 0)
        if cut_pos > 0 and cut_pos < len(modified_html):
            tail = modified_html[cut_pos:].strip()
            modified_html = modified_html[:cut_pos].rstrip()
            changes.append(f"trimmed {len(tail)} chars of end-bleed (next review title)")
    elif cat in ('end-bleed', 'both-bleed') and 'keep_end' in trim_info:
        keep_end = trim_info['keep_end']
        # Adjust for any start-trim offset
        if 'title_start' in trim_info and cat == 'both-bleed':
            keep_end -= trim_info['title_start']
        tail = modified_html[keep_end:].strip()
        tail_clean = re.sub(r'^(\s*</?(?:p|div|br)[^>]*>\s*)*$', '', tail).strip()
        if len(tail_clean) > 50:
            modified_html = modified_html[:keep_end].rstrip()
            changes.append(f"trimmed {len(tail_clean)} chars of end-bleed")

    # Safety: don't trim more than 70% of content
    if len(modified_html) < original_len * 0.3:
        return False, f"would remove {100 - len(modified_html) * 100 // original_len}% of content (>70% threshold)"

    if not changes:
        return False, 'no changes needed'

    if not dry_run:
        # Create backup
        bak_path = html_path + '.bak'
        if not os.path.exists(bak_path):
            shutil.copy2(html_path, bak_path)
        # Write trimmed HTML
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(modified_html)

    return True, '; '.join(changes)


def run_trim(toc_paths, dry_run=False):
    """Trim bleed from book reviews where we can reliably detect it."""
    reviews = collect_book_reviews(toc_paths)

    if not reviews:
        print("No book reviews with HTML galleys found.")
        return

    print(f"\n{'DRY RUN — ' if dry_run else ''}Trimming bleed from {len(reviews)} book reviews...\n")

    trimmed = 0
    skipped = 0
    errors = 0

    for rev in reviews:
        classification = classify_review(rev)
        cat = classification['category']

        # Only trim categories we can handle
        if cat not in ('end-bleed', 'start-bleed', 'both-bleed'):
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

    print(f"\n{'=' * 50}")
    action = "Would trim" if dry_run else "Trimmed"
    print(f"{action}: {trimmed}")
    print(f"Skipped: {skipped}")
    if errors:
        print(f"Errors: {errors}")
    if not dry_run and trimmed:
        print(f"\nBackups saved as .html.bak files")
    print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(
        description='Detect and fix HTML bleed in book review galleys')
    parser.add_argument('toc_json', nargs='+', help='toc.json file(s)')

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--report', action='store_true',
                      help='Audit book reviews and generate bleed report')
    mode.add_argument('--trim', action='store_true',
                      help='Trim detected bleed from HTML galleys')

    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be trimmed without modifying files')

    args = parser.parse_args()

    # Verify files exist
    valid_paths = []
    for p in args.toc_json:
        if os.path.exists(p):
            valid_paths.append(p)
        else:
            print(f"WARNING: {p} not found, skipping", file=sys.stderr)

    if not valid_paths:
        print("ERROR: No valid toc.json files found", file=sys.stderr)
        sys.exit(1)

    if args.report:
        run_report(valid_paths)
    elif args.trim:
        run_trim(valid_paths, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
