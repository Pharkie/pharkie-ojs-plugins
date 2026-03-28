#!/usr/bin/env python3
"""
Audit HTML galley quality against source PDFs.

Checks each article's HTML for:
- MISSING_REFS: PDF has a formal "References" heading but HTML doesn't
- HAS_ABSTRACT: HTML body starts with the abstract text (should be skipped)
- END_BLEED: HTML contains the title of the next article (leaked content)

Usage:
    python backfill/audit_html_quality.py                    # all non-book-review articles
    python backfill/audit_html_quality.py --issue 36.2       # single issue
"""

import argparse
import glob
import json
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
from citations import REFERENCE_HEADING_RE

BACKFILL_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(BACKFILL_DIR, 'private', 'output')

BOOK_REVIEW_SECTIONS = ('Book Reviews', 'Book Review', 'Book Review Editorial')


def clean(text):
    """Lowercase, strip non-alphanumeric."""
    return re.sub(r'[^a-z0-9 ]', '', text.lower())


def strip_tags(html):
    """Remove HTML tags."""
    return re.sub(r'<[^>]+>', '', html)


def pdf_has_formal_refs(pdf_path):
    """Check if PDF has a standalone back-matter heading (References, Notes, Bibliography, etc.)."""
    doc = fitz.open(pdf_path)
    pdf_text = ''.join(p.get_text() for p in doc)
    doc.close()
    for line in pdf_text.split('\n'):
        if REFERENCE_HEADING_RE.match(line.strip()):
            return True
    return False


def html_has_refs(html):
    """Check if HTML contains a back-matter section (References, Notes, Bibliography, etc.)."""
    # Check for headings that match the shared regex
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL):
        heading_text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if REFERENCE_HEADING_RE.match(heading_text):
            return True
    # Also check for strong-wrapped headings: <p><strong>References</strong></p>
    for m in re.finditer(r'<p>\s*<strong>(.*?)</strong>\s*</p>', html, re.DOTALL):
        heading_text = m.group(1).strip()
        if REFERENCE_HEADING_RE.match(heading_text):
            return True
    return False


def check_article(art, vol_dir, all_articles, idx):
    """Check one article. Returns list of issue strings, or empty if OK."""
    sp = art.get('split_pdf', '')
    if not sp:
        return []
    slug = os.path.splitext(os.path.basename(sp))[0]
    html_path = os.path.join(vol_dir, f'{slug}.html')
    if not os.path.exists(html_path):
        return ['NO_HTML']

    pdf_path = sp[2:] if sp.startswith('./') else sp
    if not os.path.exists(pdf_path):
        return ['NO_PDF']

    with open(html_path) as f:
        html = f.read()
    html_text = strip_tags(html).strip()

    issues = []

    # CHECK 1: References — does PDF have a formal heading that HTML is missing?
    if pdf_has_formal_refs(pdf_path) and not html_has_refs(html):
        issues.append('MISSING_REFS')

    # CHECK 2: Abstract leak — is the toc.json abstract text in the HTML body start?
    abstract = art.get('abstract', '')
    if abstract and len(abstract) > 50:
        abs_words = clean(abstract)[:40]
        html_start = clean(html_text[:len(abstract) + 200])
        if abs_words in html_start:
            issues.append('HAS_ABSTRACT')

    # CHECK 3: End bleed — does the HTML contain the next article's title?
    if idx < len(all_articles) - 1:
        next_title = all_articles[idx + 1].get('title', '')
        if next_title and len(next_title) > 15:
            # Check the last portion of HTML for the next title
            html_tail = strip_tags(html).lower()
            next_clean = next_title.lower()
            # Only flag if the next title appears after the last heading
            last_h2 = html.lower().rfind('</h2>')
            check_from = last_h2 if last_h2 > 0 else len(html) // 2
            if next_clean in strip_tags(html[check_from:]).lower():
                issues.append('END_BLEED')

    return issues


def main():
    parser = argparse.ArgumentParser(description='Audit HTML galley quality')
    parser.add_argument('--issue', help='Single issue (e.g. 36.2)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    total = 0
    passes = 0
    failures = []

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
            if section in BOOK_REVIEW_SECTIONS:
                continue
            if art.get('_manual_html'):
                continue
            sp = art.get('split_pdf', '')
            if not sp:
                continue

            issues = check_article(art, vol_dir, articles, idx)
            if issues == ['NO_HTML'] or issues == ['NO_PDF']:
                continue

            total += 1
            slug = os.path.splitext(os.path.basename(sp))[0]
            if issues:
                failures.append((vol_iss, slug, issues))
                if args.verbose:
                    print(f'  FAIL {vol_iss}/{slug[:55]}: {", ".join(issues)}')
            else:
                passes += 1

    print(f'Checked: {total} articles')
    print(f'  PASS: {passes}')
    print(f'  FAIL: {len(failures)}')
    if failures:
        counts = {}
        for _, _, issues in failures:
            for i in issues:
                counts[i] = counts.get(i, 0) + 1
        for issue_type, count in sorted(counts.items()):
            print(f'    {issue_type}: {count}')
        rate = passes / total * 100 if total else 0
        print(f'  Pass rate: {rate:.1f}%')

    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
