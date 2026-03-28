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

    # CHECK 2: Abstract leak — is the first <p> block mostly the abstract?
    # Only flags if the first <p> is predominantly abstract text, not if the
    # body happens to start with the same sentence (common in Introductions).
    abstract = art.get('abstract', '')
    if abstract and len(abstract) > 50:
        # Extract first <p> content
        m = re.search(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
        if m:
            first_p = strip_tags(m.group(1)).strip()
            # Only flag if the first <p> is SHORT (abstract-length) and matches.
            # A long Introduction paragraph that starts with the abstract sentence
            # is real body content, not a leak.
            if len(first_p) < len(abstract) * 2:
                abs_clean = clean(abstract)
                p_clean = clean(first_p)
                # Check word overlap
                abs_words = set(abs_clean.split())
                p_words = set(p_clean.split())
                if abs_words and p_words:
                    overlap = len(abs_words & p_words) / len(abs_words)
                    if overlap > 0.8:
                        issues.append('HAS_ABSTRACT')

    # CHECK 3: End bleed — does the HTML contain the next article's title?
    # Only flags if the title appears as a heading or standalone paragraph,
    # NOT inside a reference citation (which commonly contains article titles).
    if idx < len(all_articles) - 1:
        next_title = all_articles[idx + 1].get('title', '')
        if next_title and len(next_title) > 15:
            next_lower = next_title.lower()
            html_lower = html.lower()
            # Check for title in a heading tag
            in_heading = next_lower in strip_tags(
                ''.join(re.findall(r'<h[23][^>]*>.*?</h[23]>', html_lower, re.DOTALL)))
            # Check for title as a standalone paragraph BEFORE any references section.
            # Titles in reference citations are not end bleed.
            # Find where references start (last back-matter heading)
            refs_start = len(html_lower)
            for heading_m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html_lower, re.DOTALL):
                heading_text = strip_tags(heading_m.group(1)).strip()
                if REFERENCE_HEADING_RE.match(heading_text):
                    refs_start = heading_m.start()
            # Also check <p><strong>References</strong></p> pattern
            for strong_m in re.finditer(r'<p>\s*<strong>(.*?)</strong>\s*</p>', html_lower, re.DOTALL):
                heading_text = strong_m.group(1).strip()
                if REFERENCE_HEADING_RE.match(heading_text):
                    refs_start = min(refs_start, strong_m.start())

            # Only check for end bleed BEFORE the references section
            pre_refs = html_lower[:refs_start]
            last_chunk = pre_refs[len(pre_refs) * 4 // 5:] if len(pre_refs) > 0 else ''
            in_standalone_p = False
            for m in re.finditer(r'<p[^>]*>(.*?)</p>', last_chunk, re.DOTALL):
                p_text = strip_tags(m.group(1)).strip().lower()
                if next_lower in p_text and len(p_text) < len(next_title) * 3:
                    in_standalone_p = True
                    break
            if in_heading or in_standalone_p:
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
