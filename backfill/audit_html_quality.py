#!/usr/bin/env python3
"""
Audit HTML galley quality against source PDFs.

Checks ALL article types with section-specific standards:

Articles:
- MISSING_REFS: PDF has a back-matter heading but HTML doesn't
- HAS_ABSTRACT: first <p> is predominantly the toc.json abstract text
- TITLE_IN_BODY: article title still present at the start of the HTML
- EMPTY: HTML body is too short

Book Reviews:
- BOOK_TITLE_MISSING: book title not found in HTML
- EMPTY: HTML body is too short

Editorial:
- TITLE_IN_BODY: editorial title still present at the start
- EMPTY: HTML body is too short

Book Review Editorial:
- HEADING_NOT_STRIPPED: "BOOK REVIEWS" heading still present
- EMPTY: HTML body is too short

Usage:
    python backfill/audit_html_quality.py                    # all articles
    python backfill/audit_html_quality.py --issue 36.2       # single issue
    python backfill/audit_html_quality.py --section Articles  # one section type
"""

import argparse
import glob
import json
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from citations import REFERENCE_HEADING_RE
from postprocess_html import (
    _clean, _strip_toc_prefixes, _title_in_text_fuzzy,
    SHORT_CONTENT_THRESHOLD,
)

BACKFILL_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(BACKFILL_DIR, 'private', 'output')


def strip_tags(html):
    """Remove HTML tags."""
    return re.sub(r'<[^>]+>', '', html)


def pdf_has_formal_refs(pdf_path):
    """Check if PDF has a standalone back-matter heading."""
    doc = fitz.open(pdf_path)
    pdf_text = ''.join(p.get_text() for p in doc)
    doc.close()
    for line in pdf_text.split('\n'):
        if REFERENCE_HEADING_RE.match(line.strip()):
            return True
    return False


def html_has_refs(html):
    """Check if HTML contains a back-matter section heading."""
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL):
        heading_text = strip_tags(m.group(1)).strip()
        if REFERENCE_HEADING_RE.match(heading_text):
            return True
    for m in re.finditer(r'<p>\s*<strong>(.*?)</strong>\s*</p>', html, re.DOTALL):
        heading_text = m.group(1).strip()
        if REFERENCE_HEADING_RE.match(heading_text):
            return True
    return False


def _title_at_start(html, title):
    """Check if the article title is still at the start of the HTML as a heading.

    Only flags if the first block is a heading tag (h1-h6) whose text
    matches the title. Body paragraphs that happen to contain title words
    are not flagged.
    """
    if not title:
        return False
    stripped = _strip_toc_prefixes(title)
    if not stripped:
        return False
    # Only match if the first element is a heading containing the title
    m = re.match(r'<h[1-6][^>]*>(.*?)</h[1-6]>', html, re.DOTALL)
    if not m:
        return False
    heading_text = strip_tags(m.group(1)).strip()
    # Use ordered word sequence — title words must appear in order
    from postprocess_html import _text_to_regex
    rx = _text_to_regex(stripped)
    if rx is None:
        return False
    return bool(rx.search(_clean(heading_text)))


# ---------------------------------------------------------------
# Section-specific checks
# ---------------------------------------------------------------

def check_article(art, html, pdf_path, all_articles, idx):
    """Check a standard article."""
    issues = []
    html_text = strip_tags(html).strip()

    # MISSING_REFS: PDF has refs but HTML doesn't
    if pdf_has_formal_refs(pdf_path) and not html_has_refs(html):
        issues.append('MISSING_REFS')

    # HAS_ABSTRACT: first <p> matches the toc.json abstract
    abstract = art.get('abstract', '')
    if abstract and len(abstract) > 50:
        m = re.search(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
        if m:
            first_p = strip_tags(m.group(1)).strip()
            if len(first_p) < len(abstract) * 2:
                abs_words = set(_clean(abstract).split())
                p_words = set(_clean(first_p).split())
                if abs_words and p_words:
                    overlap = len(abs_words & p_words) / len(abs_words)
                    if overlap > 0.8:
                        issues.append('HAS_ABSTRACT')

    # TITLE_IN_BODY: title still at start
    if _title_at_start(html, art.get('title', '')):
        issues.append('TITLE_IN_BODY')

    # EMPTY
    if len(html_text) < SHORT_CONTENT_THRESHOLD:
        issues.append('EMPTY')

    return issues


def check_book_review(art, html, all_articles, idx):
    """Check a book review."""
    issues = []
    html_text = strip_tags(html).strip()

    # BOOK_TITLE_MISSING: book title not in HTML
    title = art.get('title', '')
    stripped = _strip_toc_prefixes(title)
    if stripped:
        parts = [p.strip() for p in stripped.split('/') if p.strip()]
        if not any(_title_in_text_fuzzy(p, html_text) for p in parts):
            issues.append('BOOK_TITLE_MISSING')

    # EMPTY
    if len(html_text) < SHORT_CONTENT_THRESHOLD:
        issues.append('EMPTY')

    return issues


def check_editorial(art, html):
    """Check an editorial."""
    issues = []
    html_text = strip_tags(html).strip()

    # TITLE_IN_BODY: editorial title still at start
    if _title_at_start(html, art.get('title', '')):
        issues.append('TITLE_IN_BODY')

    # EMPTY
    if len(html_text) < SHORT_CONTENT_THRESHOLD:
        issues.append('EMPTY')

    return issues


def check_book_review_editorial(art, html):
    """Check a book review editorial (section intro)."""
    issues = []
    html_text = strip_tags(html).strip()

    # HEADING_NOT_STRIPPED: "BOOK REVIEWS" heading still present
    if re.search(r'<h[1-3][^>]*>\s*BOOK\s+REVIEWS?\s*</h[1-3]>', html, re.IGNORECASE):
        issues.append('HEADING_NOT_STRIPPED')

    # EMPTY
    if len(html_text) < SHORT_CONTENT_THRESHOLD:
        issues.append('EMPTY')

    return issues


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Audit HTML galley quality')
    parser.add_argument('--issue', help='Single issue (e.g. 36.2)')
    parser.add_argument('--section', help='Filter by section type (e.g. "Articles", "Book Reviews")')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    by_section = {}  # section -> {total, pass, failures}

    for toc_path in sorted(glob.glob(os.path.join(OUTPUT_DIR, '*/toc.json'))):
        with open(toc_path) as f:
            toc = json.load(f)
        vol_dir = os.path.dirname(toc_path)
        vol_iss = os.path.basename(vol_dir)

        if args.issue and vol_iss != args.issue:
            continue

        articles = toc.get('articles', [])
        for idx, art in enumerate(articles):
            section = art.get('section', '')
            if args.section and section != args.section:
                continue
            if art.get('_manual_html'):
                continue
            sp = art.get('split_pdf', '')
            if not sp:
                continue

            slug = os.path.splitext(os.path.basename(sp))[0]
            html_path = os.path.join(vol_dir, f'{slug}.post.html')
            if not os.path.exists(html_path):
                continue
            pdf_path = sp[2:] if sp.startswith('./') else sp
            if not os.path.exists(pdf_path):
                continue

            with open(html_path) as f:
                html = f.read()

            # Content-filtered articles: skip audit (known limitation)
            if '<!-- AUTO-EXTRACTED:' in html[:100]:
                continue

            # Route to section-specific check
            if section in ('Book Reviews', 'Book Review'):
                issues = check_book_review(art, html, articles, idx)
            elif section == 'Book Review Editorial':
                issues = check_book_review_editorial(art, html)
            elif section == 'Editorial':
                issues = check_editorial(art, html)
            else:
                issues = check_article(art, html, pdf_path, articles, idx)

            # Track results by section
            if section not in by_section:
                by_section[section] = {'total': 0, 'passes': 0, 'failures': []}
            by_section[section]['total'] += 1

            if issues:
                by_section[section]['failures'].append((vol_iss, slug, issues))
                if args.verbose:
                    print(f'  FAIL {vol_iss}/{slug[:55]} [{section[:12]}]: {", ".join(issues)}')
            else:
                by_section[section]['passes'] += 1

    # Report
    grand_total = sum(s['total'] for s in by_section.values())
    grand_pass = sum(s['passes'] for s in by_section.values())
    grand_fail = sum(len(s['failures']) for s in by_section.values())

    print(f'Checked: {grand_total} articles')
    print(f'  PASS: {grand_pass}')
    print(f'  FAIL: {grand_fail}')

    if grand_fail:
        # Issue type counts across all sections
        all_counts = {}
        for s in by_section.values():
            for _, _, issues in s['failures']:
                for i in issues:
                    all_counts[i] = all_counts.get(i, 0) + 1
        for issue_type, count in sorted(all_counts.items()):
            print(f'    {issue_type}: {count}')

    print(f'\nBy section:')
    for section in sorted(by_section.keys()):
        s = by_section[section]
        fail_count = len(s['failures'])
        rate = s['passes'] / s['total'] * 100 if s['total'] else 0
        print(f'  {section}: {s["passes"]}/{s["total"]} pass ({rate:.1f}%)')
        if fail_count and args.verbose:
            section_counts = {}
            for _, _, issues in s['failures']:
                for i in issues:
                    section_counts[i] = section_counts.get(i, 0) + 1
            for issue_type, count in sorted(section_counts.items()):
                print(f'    {issue_type}: {count}')

    rate = grand_pass / grand_total * 100 if grand_total else 0
    print(f'\n  Overall: {rate:.1f}%')

    return 0 if not grand_fail else 1


if __name__ == '__main__':
    sys.exit(main())
