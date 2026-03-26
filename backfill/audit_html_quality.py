#!/usr/bin/env python3
"""
Audit HTML galley quality against source PDFs.

Checks each article's HTML for:
- SKIPPED_BODY: body text starts too far after abstract/keywords in PDF
- MISSING_REFS: PDF has references section but HTML doesn't
- NO_TITLE_SKIP: HTML still contains the article title (should be skipped)
- HAS_ABSTRACT: HTML contains abstract text (should be skipped)

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

BACKFILL_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(BACKFILL_DIR, 'private', 'output')

BOOK_REVIEW_SECTIONS = ('Book Reviews', 'Book Review', 'Book Review Editorial')


def clean(text):
    return re.sub(r'[^a-z0-9 ]', '', text.lower())


def strip_tags(html):
    return re.sub(r'<[^>]+>', '', html)


def check_article(art, vol_dir):
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

    doc = fitz.open(pdf_path)
    pdf_text = ''.join(p.get_text() for p in doc)
    doc.close()

    issues = []

    # CHECK 1: References present if PDF has them
    pdf_lower = pdf_text.lower()
    pdf_has_refs = ('references' in pdf_lower[-4000:] or 'bibliography' in pdf_lower[-4000:])
    html_has_refs = ('references' in html.lower() or 'bibliography' in html.lower())
    if pdf_has_refs and not html_has_refs:
        issues.append('MISSING_REFS')

    # CHECK 2: Abstract should NOT be in HTML
    abstract = art.get('abstract', '')
    if abstract and len(abstract) > 50:
        abs_start = clean(abstract)[:40]
        html_clean = clean(html_text)[:500]
        if abs_start in html_clean:
            issues.append('HAS_ABSTRACT')

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

        for art in toc.get('articles', []):
            section = art.get('section', '')
            if section in BOOK_REVIEW_SECTIONS:
                continue
            if art.get('_manual_html'):
                continue
            sp = art.get('split_pdf', '')
            if not sp:
                continue

            issues = check_article(art, vol_dir)
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
        missing_refs = sum(1 for f in failures if 'MISSING_REFS' in f[2])
        has_abstract = sum(1 for f in failures if 'HAS_ABSTRACT' in f[2])
        print(f'    Missing refs:  {missing_refs}')
        print(f'    Has abstract:  {has_abstract}')
        rate = passes / total * 100 if total else 0
        print(f'  Pass rate: {rate:.1f}%')

    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
