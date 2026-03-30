#!/usr/bin/env python3
"""
Comprehensive backfill quality audit.

Runs all checks across all 68 issues: toc.json integrity, HTML galley quality,
abstract/keyword cleanliness, cross-file consistency, and content checks.

Usage:
    python3 backfill/audit_comprehensive.py              # Full audit
    python3 backfill/audit_comprehensive.py --json report.json  # Save JSON
"""

import sys
import os
import re
import json
import glob
import argparse
from html import unescape
from collections import defaultdict


def strip_tags(html):
    text = re.sub(r'<[^>]+>', '', html)
    return unescape(text).strip()


def load_toc(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


class AuditResult:
    def __init__(self):
        self.issues = []  # (severity, category, file, message)
        self.stats = defaultdict(int)

    def add(self, severity, category, file, message):
        self.issues.append((severity, category, file, message))
        self.stats[f'{severity}_{category}'] += 1

    def error(self, category, file, message):
        self.add('ERROR', category, file, message)

    def warn(self, category, file, message):
        self.add('WARN', category, file, message)

    def info(self, category, file, message):
        self.add('INFO', category, file, message)


def audit_toc_integrity(result):
    """Check all toc.json files for structural issues."""
    toc_files = sorted(glob.glob('backfill/private/output/*/toc.json'))
    result.stats['toc_files'] = len(toc_files)
    total_articles = 0

    for toc_path in toc_files:
        issue_name = os.path.basename(os.path.dirname(toc_path))
        try:
            toc = load_toc(toc_path)
        except json.JSONDecodeError as e:
            result.error('toc_json', toc_path, f'Invalid JSON: {e}')
            continue

        articles = toc.get('articles', [])
        total_articles += len(articles)

        if not articles:
            result.error('toc_json', toc_path, 'No articles')
            continue

        # Required top-level fields
        for field in ['volume', 'issue', 'date']:
            if field not in toc:
                result.error('toc_json', toc_path, f'Missing top-level field: {field}')

        prev_end = None
        for i, art in enumerate(articles):
            prefix = f'{issue_name}/article[{i}] "{art.get("title", "?")[:50]}"'

            # Required fields
            for field in ['title', 'authors', 'section']:
                if not art.get(field):
                    result.error('toc_fields', toc_path, f'{prefix}: missing {field}')

            # Valid section
            valid_sections = {'Editorial', 'Articles', 'Book Reviews', 'Book Review Editorial'}
            section = art.get('section', '')
            if section and section not in valid_sections:
                result.error('toc_fields', toc_path, f'{prefix}: invalid section "{section}"')

            # Page ranges
            start = art.get('pdf_page_start')
            end = art.get('pdf_page_end')
            if start is not None and end is not None:
                if end < start:
                    result.error('toc_pages', toc_path, f'{prefix}: end < start ({end} < {start})')
                if end - start > 50:
                    result.warn('toc_pages', toc_path, f'{prefix}: very long article ({end - start + 1} pages)')

            # Page gaps/overlaps with previous
            if prev_end is not None and start is not None:
                gap = start - prev_end
                if gap > 3:
                    result.warn('toc_pages', toc_path, f'{prefix}: {gap}-page gap after previous article')
                elif gap < -5:
                    # Large overlaps are suspicious; small overlaps are shared pages (normal for book reviews)
                    result.warn('toc_pages', toc_path, f'{prefix}: large {abs(gap)}-page overlap with previous article')
            prev_end = end

            # Abstract quality
            abstract = art.get('abstract', '')
            if abstract:
                if len(abstract) > 3000:
                    result.warn('toc_abstract', toc_path, f'{prefix}: abstract very long ({len(abstract)} chars)')
                if re.search(r'<[a-z/][^>]*>', abstract):
                    result.error('toc_abstract', toc_path, f'{prefix}: HTML tags in abstract')
                if re.search(r'&(amp|lt|gt|nbsp);', abstract):
                    result.error('toc_abstract', toc_path, f'{prefix}: HTML entities in abstract')
                # Page number / author junk at end
                if re.search(r'\d{2,3}\s+[A-Z][a-z]+\s+(and\s+)?[A-Z]', abstract[-80:]):
                    result.warn('toc_abstract', toc_path, f'{prefix}: possible page number/author junk at end of abstract')
                # Broken hyphens (but not intentional like "her- or")
                broken = re.findall(r'[a-z]- [a-z]', abstract)
                # Filter out known intentional patterns
                real_broken = [b for b in broken if not re.search(r'- (and|or|to|in|a|the) ', abstract[abstract.index(b):abstract.index(b)+20])]
                if real_broken:
                    result.warn('toc_abstract', toc_path, f'{prefix}: possible broken hyphens: {real_broken[:3]}')

            # Keyword quality
            keywords = art.get('keywords', [])
            for kw in keywords:
                if len(kw) > 80:
                    result.error('toc_keywords', toc_path, f'{prefix}: keyword too long ({len(kw)} chars): {kw[:60]}...')
                if not kw.strip():
                    result.error('toc_keywords', toc_path, f'{prefix}: empty keyword')
                if re.search(r'<[a-z/]', kw):
                    result.error('toc_keywords', toc_path, f'{prefix}: HTML in keyword: {kw[:60]}')
            if len(keywords) != len(set(keywords)):
                dupes = [k for k in keywords if keywords.count(k) > 1]
                result.error('toc_keywords', toc_path, f'{prefix}: duplicate keywords: {set(dupes)}')

            # Book review specific
            if section == 'Book Reviews':
                if not art.get('reviewer'):
                    result.warn('toc_book_review', toc_path, f'{prefix}: book review missing reviewer')
                if not art.get('book_title'):
                    result.warn('toc_book_review', toc_path, f'{prefix}: book review missing book_title')

    result.stats['total_articles'] = total_articles


def audit_html_quality(result):
    """Check all HTML galley files for quality issues."""
    html_files = sorted(glob.glob('backfill/private/output/*/*.post.html'))
    result.stats['html_files'] = len(html_files)

    for html_path in html_files:
        issue_name = os.path.basename(os.path.dirname(html_path))
        html_name = os.path.basename(html_path)
        prefix = f'{issue_name}/{html_name}'

        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()

        text = strip_tags(content)
        file_size = len(content)

        # --- Critical checks ---

        # Abstract heading still present
        if '<h2>Abstract</h2>' in content:
            result.error('html_abstract', html_path, f'{prefix}: still contains <h2>Abstract</h2>')

        # Too small (< 200 bytes = missing content)
        if file_size < 200:
            result.error('html_size', html_path, f'{prefix}: very small ({file_size} bytes)')

        # Full document tags (should be body-only)
        if '<!DOCTYPE' in content or '<html' in content:
            result.error('html_structure', html_path, f'{prefix}: contains document tags (should be body-only)')

        # Empty file
        if not text.strip():
            result.error('html_size', html_path, f'{prefix}: empty content')
            continue

        # --- Quality checks ---

        # Running headers (standalone paragraphs with just journal name + vol/issue)
        running_headers = re.findall(
            r'<p>(?:<strong>)?(?:\d+[a-z]*\s+)?Existential Analysis[\s:]+\d+\.\d[^<]*(?:</strong>)?</p>',
            content)
        if running_headers:
            result.warn('html_running_header', html_path,
                         f'{prefix}: {len(running_headers)} running header(s)')

        # Page numbers in body (standalone numbers)
        page_nums = re.findall(r'<p>\s*\d{1,3}\s*</p>', content)
        if len(page_nums) > 2:
            result.warn('html_page_numbers', html_path, f'{prefix}: {len(page_nums)} standalone page numbers')

        # Markdown code fences
        if '```' in content:
            result.error('html_structure', html_path, f'{prefix}: markdown code fences in HTML')

        # h1 tags (should use h2+)
        if '<h1>' in content or '<h1 ' in content:
            result.warn('html_structure', html_path, f'{prefix}: contains <h1> (should use <h2>+)')

        # Content density check (articles > 2 pages should have decent content)
        # Get page count from toc.json
        toc_path = os.path.join(os.path.dirname(html_path), 'toc.json')
        if os.path.exists(toc_path):
            toc = load_toc(toc_path)
            stem = os.path.splitext(html_name)[0]
            for art in toc['articles']:
                sp = art.get('split_pdf', '')
                if sp and os.path.splitext(os.path.basename(sp))[0] == stem:
                    pages = art.get('pdf_page_end', 0) - art.get('pdf_page_start', 0) + 1
                    if pages > 2 and len(text) < pages * 200:
                        result.warn('html_density', html_path,
                                    f'{prefix}: low content density ({len(text)} chars for {pages} pages, '
                                    f'{len(text)//max(pages,1)} chars/page)')
                    break

        # Keywords section still present (not as h2 but as bold paragraph)
        # Only check if it appears near the top of the file (first 500 chars)
        first_500 = content[:500]
        if re.search(r'<p><strong>Key\s*[Ww]ords', first_500):
            result.info('html_keywords', html_path, f'{prefix}: keywords paragraph near top (may be intentional preamble)')


def audit_cross_consistency(result):
    """Check consistency between toc.json and HTML files."""
    toc_files = sorted(glob.glob('backfill/private/output/*/toc.json'))

    for toc_path in toc_files:
        issue_dir = os.path.dirname(toc_path)
        issue_name = os.path.basename(issue_dir)
        toc = load_toc(toc_path)

        for i, art in enumerate(toc['articles']):
            split_pdf = art.get('split_pdf', '')
            if not split_pdf:
                continue

            stem = os.path.splitext(os.path.basename(split_pdf))[0]
            html_path = os.path.join(issue_dir, stem + '.post.html')
            prefix = f'{issue_name}/{stem}'

            # HTML file exists?
            if not os.path.exists(html_path):
                result.warn('cross_missing_html', toc_path, f'{prefix}: no HTML galley file')
                continue

            # PDF file exists?
            pdf_path = os.path.join(issue_dir, os.path.basename(split_pdf))
            if not os.path.exists(pdf_path):
                result.info('cross_missing_pdf', toc_path, f'{prefix}: no split PDF (expected if gitignored)')


def audit_known_artefacts(result):
    """Scan for known Haiku/PyMuPDF artefacts across all HTML files."""
    html_files = sorted(glob.glob('backfill/private/output/*/*.post.html'))

    # Known OCR errors to scan for
    artefact_patterns = [
        (r'citizenicians', 'Haiku OCR: citizenicians (should be clinicians)'),
        (r'AUTO-EXTRACTED', 'PyMuPDF fallback marker (content-filtered article)'),
    ]

    for html_path in html_files:
        issue_name = os.path.basename(os.path.dirname(html_path))
        html_name = os.path.basename(html_path)
        prefix = f'{issue_name}/{html_name}'

        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()

        for pattern, desc in artefact_patterns:
            if re.search(pattern, content):
                result.warn('artefact', html_path, f'{prefix}: {desc}')

    # Also check toc.json abstracts
    for toc_path in sorted(glob.glob('backfill/private/output/*/toc.json')):
        issue_name = os.path.basename(os.path.dirname(toc_path))
        toc = load_toc(toc_path)
        for art in toc['articles']:
            abstract = art.get('abstract', '')
            if 'citizenicians' in abstract:
                result.error('artefact', toc_path,
                             f'{issue_name}/"{art["title"][:40]}": citizenicians in abstract')


def audit_duplicate_content(result):
    """Check for content that appears in both abstract and HTML body (duplicate display risk)."""
    toc_files = sorted(glob.glob('backfill/private/output/*/toc.json'))

    for toc_path in toc_files:
        issue_dir = os.path.dirname(toc_path)
        issue_name = os.path.basename(issue_dir)
        toc = load_toc(toc_path)

        for art in toc['articles']:
            abstract = art.get('abstract', '')
            if not abstract or len(abstract) < 50:
                continue

            split_pdf = art.get('split_pdf', '')
            if not split_pdf:
                continue

            stem = os.path.splitext(os.path.basename(split_pdf))[0]
            html_path = os.path.join(issue_dir, stem + '.post.html')
            if not os.path.exists(html_path):
                continue

            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()

            html_text = strip_tags(html_content)

            # Check if first 100 chars of abstract appear in HTML body
            abstract_start = re.sub(r'\s+', ' ', abstract[:100]).strip()
            html_text_norm = re.sub(r'\s+', ' ', html_text).strip()

            if abstract_start in html_text_norm:
                result.warn('duplicate_content', html_path,
                            f'{issue_name}/{stem}: abstract text appears in HTML body (possible duplicate display)')


def audit_section_stats(result):
    """Aggregate statistics for reporting."""
    toc_files = sorted(glob.glob('backfill/private/output/*/toc.json'))
    section_counts = defaultdict(int)
    with_abstract = 0
    with_keywords = 0
    total = 0

    for toc_path in toc_files:
        toc = load_toc(toc_path)
        for art in toc['articles']:
            total += 1
            section_counts[art.get('section', '(none)')] += 1
            if art.get('abstract'):
                with_abstract += 1
            if art.get('keywords'):
                with_keywords += 1

    result.stats['articles_with_abstract'] = with_abstract
    result.stats['articles_with_keywords'] = with_keywords
    result.stats['section_counts'] = dict(section_counts)


def main():
    parser = argparse.ArgumentParser(description='Comprehensive backfill quality audit')
    parser.add_argument('--json', help='Save JSON report to file')
    args = parser.parse_args()

    result = AuditResult()

    print("=" * 70)
    print("COMPREHENSIVE BACKFILL AUDIT")
    print("=" * 70)

    checks = [
        ("toc.json integrity", audit_toc_integrity),
        ("HTML galley quality", audit_html_quality),
        ("Cross-file consistency", audit_cross_consistency),
        ("Known artefacts", audit_known_artefacts),
        ("Duplicate content risk", audit_duplicate_content),
        ("Section statistics", audit_section_stats),
    ]

    for name, fn in checks:
        print(f"\n--- {name} ---")
        fn(result)
        errors = sum(1 for s, _, _, _ in result.issues if s == 'ERROR')
        warns = sum(1 for s, _, _, _ in result.issues if s == 'WARN')
        print(f"  Running total: {errors} errors, {warns} warnings")

    # --- Summary ---
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  toc.json files:        {result.stats.get('toc_files', 0)}")
    print(f"  Total articles:        {result.stats.get('total_articles', 0)}")
    print(f"  HTML files:            {result.stats.get('html_files', 0)}")
    print(f"  Articles with abstract:{result.stats.get('articles_with_abstract', 0)}")
    print(f"  Articles with keywords:{result.stats.get('articles_with_keywords', 0)}")
    print(f"  Sections: {result.stats.get('section_counts', {})}")

    errors = [(s, c, f, m) for s, c, f, m in result.issues if s == 'ERROR']
    warns = [(s, c, f, m) for s, c, f, m in result.issues if s == 'WARN']
    infos = [(s, c, f, m) for s, c, f, m in result.issues if s == 'INFO']

    print(f"\n  ERRORS:   {len(errors)}")
    print(f"  WARNINGS: {len(warns)}")
    print(f"  INFO:     {len(infos)}")

    if errors:
        print(f"\n{'=' * 70}")
        print("ERRORS (must fix)")
        print("=" * 70)
        for s, c, f, m in errors:
            print(f"  [{c}] {m}")

    if warns:
        print(f"\n{'=' * 70}")
        print(f"WARNINGS ({len(warns)} total)")
        print("=" * 70)
        # Group by category
        by_cat = defaultdict(list)
        for s, c, f, m in warns:
            by_cat[c].append(m)
        for cat, msgs in sorted(by_cat.items()):
            print(f"\n  [{cat}] ({len(msgs)})")
            for m in msgs[:10]:
                print(f"    {m}")
            if len(msgs) > 10:
                print(f"    ... and {len(msgs) - 10} more")

    if args.json:
        report = {
            'stats': {k: v for k, v in result.stats.items()},
            'errors': [{'category': c, 'file': f, 'message': m} for s, c, f, m in errors],
            'warnings': [{'category': c, 'file': f, 'message': m} for s, c, f, m in warns],
            'info': [{'category': c, 'file': f, 'message': m} for s, c, f, m in infos],
        }
        with open(args.json, 'w') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nJSON report saved to {args.json}")

    print(f"\nExit code: {1 if errors else 0}")
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
