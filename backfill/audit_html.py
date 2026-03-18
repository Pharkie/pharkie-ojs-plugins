#!/usr/bin/env python3
"""
Audit HTML galleys for quality and consistency across all sections.

Checks content quality, reviewer bylines, structural issues, and
formatting consistency across the full backfill dataset.

Usage:
    python backfill/audit_html.py                              # Audit all
    python backfill/audit_html.py backfill/output/30.1         # Single volume
    python backfill/audit_html.py --fix                        # Auto-fix safe issues
    python backfill/audit_html.py --fix --dry-run              # Preview fixes
"""

import sys
import os
import re
import json
import argparse
import glob

# Reuse utilities from fix_html_bleed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fix_html_bleed import (
    normalize_for_match, normalize_name, text_contains,
    strip_running_headers, RUNNING_HEADER_PATTERNS,
    EMBEDDED_HEADER_PATTERNS, BACK_MATTER_HTML_PATTERNS,
    _write_html_with_backup,
)


# --- Constants ---

# Minimum content size (bytes) for non-trivial articles
MIN_CONTENT_SIZE = 200
# Minimum chars per page for articles > 2 pages
MIN_CHARS_PER_PAGE = 300
# Max length of a reviewer byline element
BYLINE_MAX_LENGTH = 80

EDITORIAL_SECTIONS = {'editorial', 'book review editorial'}


# --- Checks ---

def find_html_byline(html):
    """Find a reviewer byline near the end of HTML.

    Looks for <p><strong>Name</strong></p> pattern in the last 20% of content.
    Returns the name text if found, else None.
    """
    # Search in last 30% of HTML for byline
    search_start = int(len(html) * 0.7)
    tail = html[search_start:]

    # <p><strong>Name</strong></p> or with trailing affiliation/bio text
    # Use [^<]* instead of .*? to avoid matching across tags with DOTALL
    matches = list(re.finditer(
        r'<p[^>]*>\s*<(?:strong|b)[^>]*>([^<]*)</(?:strong|b)>\s*(?:<br\s*/?>.*?)?</p>',
        tail, re.IGNORECASE | re.DOTALL
    ))
    if not matches:
        # Also check for <p><strong>Name</strong> trailing text...</p>
        matches = list(re.finditer(
            r'<p[^>]*>\s*<(?:strong|b)[^>]*>([^<]*)</(?:strong|b)>[^<]{0,80}</p>',
            tail, re.IGNORECASE
        ))
    if not matches:
        return None

    # Take the last match (closest to end)
    last = matches[-1]
    name_text = re.sub(r'<[^>]+>', '', last.group(1)).strip()

    # Validate it looks like a name (2-5 words, reasonable length)
    if len(name_text) > BYLINE_MAX_LENGTH:
        return None
    words = name_text.split()
    if len(words) < 2 or len(words) > 6:
        return None
    # Must contain at least one uppercase letter
    if not any(c.isupper() for c in name_text):
        return None

    return name_text


def check_article(html, article, vol, iss):
    """Check a single HTML file. Returns list of (severity, code, message) tuples."""
    issues = []
    section = (article.get('section') or '').lower()
    title = article.get('title', '?')[:60]
    is_editorial = section in EDITORIAL_SECTIONS
    is_book_review = section == 'book reviews'
    is_auto_extracted = '<!-- AUTO-EXTRACTED' in html

    # --- Universal checks ---

    # Size check
    if len(html) < MIN_CONTENT_SIZE:
        issues.append(('HIGH', 'tiny-file', f'Only {len(html)} bytes'))

    # Full-document tags
    if '<!DOCTYPE' in html or '<html' in html.lower() or '<head' in html.lower():
        issues.append(('HIGH', 'doc-tags', 'Contains full document tags'))

    # h1 tags
    if re.search(r'<h1[^>]*>', html, re.IGNORECASE):
        issues.append(('LOW', 'h1-tag', 'Contains <h1> (should use <h2>+)'))

    # Starts with References
    first_content = html.strip()[:500]
    if re.search(r'^<(?:h[1-6]|p)[^>]*>\s*(?:<[^>]+>\s*)*(?:References?|Bibliography)\b',
                 first_content, re.IGNORECASE):
        issues.append(('HIGH', 'starts-with-refs', 'Starts with References section (body missing)'))

    # Residual running headers
    _, header_count = strip_running_headers(html)
    if header_count:
        issues.append(('MEDIUM', 'running-headers', f'{header_count} running header(s) remain'))

    # Back matter in non-editorial
    if not is_editorial:
        for pattern in BACK_MATTER_HTML_PATTERNS:
            if pattern.search(html):
                issues.append(('MEDIUM', 'back-matter', 'Contains back matter content'))
                break

    # Content per page ratio (for articles > 2 pages)
    # Skip book reviews — shared pages mean the page range often includes
    # editorial or adjacent review content that isn't in this HTML
    page_start = article.get('pdf_page_start', 0)
    page_end = article.get('pdf_page_end', 0)
    page_count = page_end - page_start + 1 if page_end > page_start else 1
    content_len = len(re.sub(r'<[^>]+>', '', html))
    if not is_book_review and page_count > 2 and content_len < MIN_CHARS_PER_PAGE * page_count:
        issues.append(('MEDIUM', 'short-content',
                       f'{content_len} chars for {page_count} pages '
                       f'({content_len // page_count} chars/page)'))

    # Markdown code fences
    if '```' in html:
        issues.append(('LOW', 'markdown-fence', 'Contains markdown code fences'))

    # --- Book review checks ---
    if is_book_review:
        reviewer = article.get('reviewer', '')
        html_byline = find_html_byline(html)

        if html_byline:
            # Compare with toc.json reviewer
            reviewer_norm = normalize_name(reviewer)
            byline_norm = normalize_name(html_byline)
            if reviewer_norm and byline_norm:
                if not text_contains(byline_norm, reviewer_norm) and \
                   not text_contains(reviewer_norm, byline_norm):
                    # Check if surnames match (looser check)
                    rev_surname = reviewer_norm.split()[-1] if reviewer_norm.split() else ''
                    byline_surname = byline_norm.split()[-1] if byline_norm.split() else ''
                    if rev_surname != byline_surname:
                        issues.append(('HIGH', 'reviewer-mismatch',
                                       f'HTML byline "{html_byline}" ≠ toc.json "{reviewer}"'))
        elif not is_auto_extracted:
            issues.append(('MEDIUM', 'no-byline',
                           f'No reviewer byline found (expected "{reviewer}")'))

    return issues


def audit_volume(vol_dir, fix=False, dry_run=False):
    """Audit all HTML files in a volume directory. Returns (results, fixes_applied)."""
    toc_path = os.path.join(vol_dir, 'toc.json')
    if not os.path.exists(toc_path):
        return [], 0

    with open(toc_path) as f:
        toc = json.load(f)

    vol = toc.get('volume', '?')
    iss = toc.get('issue', '?')
    results = []
    fixes = 0

    for idx, article in enumerate(toc.get('articles', [])):
        split_pdf = article.get('split_pdf', '')
        if not split_pdf:
            continue
        html_path = os.path.splitext(split_pdf)[0] + '.html'
        if not os.path.exists(html_path):
            continue

        with open(html_path, encoding='utf-8') as f:
            html = f.read()

        issues = check_article(html, article, vol, iss)

        if issues:
            results.append({
                'vol': vol,
                'iss': iss,
                'idx': idx,
                'title': article.get('title', '?')[:70],
                'section': article.get('section', '?'),
                'html_path': html_path,
                'issues': issues,
            })

        # --- Auto-fixes ---
        if fix:
            modified = False

            # Fix h1 → h2
            if any(code == 'h1-tag' for _, code, _ in issues):
                html = re.sub(r'<h1([^>]*)>', r'<h2\1>', html)
                html = re.sub(r'</h1>', '</h2>', html)
                modified = True

            # Fix running headers
            if any(code == 'running-headers' for _, code, _ in issues):
                html, _ = strip_running_headers(html)
                modified = True

            # Fix doc tags (strip everything outside body content)
            if any(code == 'doc-tags' for _, code, _ in issues):
                body_match = re.search(r'<body[^>]*>(.*)</body>', html, re.DOTALL | re.IGNORECASE)
                if body_match:
                    html = body_match.group(1).strip()
                    modified = True

            if modified:
                label = f"Vol {vol}.{iss} #{idx+1}"
                codes = [code for _, code, _ in issues if code in ('h1-tag', 'running-headers', 'doc-tags')]
                if dry_run:
                    print(f"  [DRY RUN] {label}: would fix {', '.join(codes)}")
                else:
                    _write_html_with_backup(html_path, html)
                    print(f"  Fixed {label}: {', '.join(codes)}")
                fixes += 1

    return results, fixes


def main():
    parser = argparse.ArgumentParser(description='Audit HTML galleys for quality and consistency')
    parser.add_argument('paths', nargs='*', default=None,
                        help='Volume directories or toc.json files (default: all)')
    parser.add_argument('--fix', action='store_true',
                        help='Auto-fix safe issues (h1→h2, running headers, doc tags)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what --fix would change without modifying files')
    parser.add_argument('--json', metavar='PATH',
                        help='Write detailed JSON report to PATH')

    args = parser.parse_args()

    # Discover volume directories
    if args.paths:
        vol_dirs = []
        for p in args.paths:
            if os.path.isdir(p):
                vol_dirs.append(p)
            elif p.endswith('toc.json'):
                vol_dirs.append(os.path.dirname(p))
    else:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
        vol_dirs = sorted(glob.glob(os.path.join(output_dir, '*')))
        vol_dirs = [d for d in vol_dirs if os.path.isdir(d)]

    all_results = []
    total_checked = 0
    total_fixes = 0

    for vol_dir in sorted(vol_dirs):
        toc_path = os.path.join(vol_dir, 'toc.json')
        if not os.path.exists(toc_path):
            continue

        with open(toc_path) as f:
            toc = json.load(f)
        html_count = sum(1 for a in toc.get('articles', [])
                         if a.get('split_pdf') and
                         os.path.exists(os.path.splitext(a['split_pdf'])[0] + '.html'))
        total_checked += html_count

        results, fixes = audit_volume(vol_dir, fix=args.fix, dry_run=args.dry_run)
        all_results.extend(results)
        total_fixes += fixes

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print(f"HTML Galley Audit Report")
    print(f"{'=' * 60}")
    print(f"Files checked: {total_checked}")
    print(f"Files with issues: {len(all_results)}")

    # Aggregate by issue code
    code_counts = {}
    for r in all_results:
        for severity, code, _ in r['issues']:
            key = (severity, code)
            code_counts[key] = code_counts.get(key, 0) + 1

    if code_counts:
        print(f"\nIssues by type:")
        for (severity, code), count in sorted(code_counts.items(),
                                               key=lambda x: ('HIGH', 'MEDIUM', 'LOW').index(x[0][0])):
            print(f"  [{severity:6s}] {code:25s} {count:4d}")

    # Detail for HIGH severity
    high_results = [r for r in all_results if any(s == 'HIGH' for s, _, _ in r['issues'])]
    if high_results:
        print(f"\n{'─' * 60}")
        print(f"HIGH severity issues ({len(high_results)} files)")
        print(f"{'─' * 60}")
        for r in high_results[:50]:  # Cap output
            high_issues = [(s, c, m) for s, c, m in r['issues'] if s == 'HIGH']
            for _, code, msg in high_issues:
                print(f"  Vol {r['vol']}.{r['iss']} #{r['idx']+1} [{code}]: {msg}")
                print(f"    {r['title']}")

    if args.fix:
        print(f"\n{'Would fix' if args.dry_run else 'Fixed'}: {total_fixes} files")

    # JSON report
    if args.json:
        report = {
            'total_checked': total_checked,
            'files_with_issues': len(all_results),
            'issue_summary': {f"{s}:{c}": n for (s, c), n in code_counts.items()},
            'results': [{
                'vol': r['vol'],
                'iss': r['iss'],
                'idx': r['idx'],
                'title': r['title'],
                'section': r['section'],
                'html_path': r['html_path'],
                'issues': [{'severity': s, 'code': c, 'message': m} for s, c, m in r['issues']],
            } for r in all_results],
        }
        with open(args.json, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\nDetailed report: {args.json}")

    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
