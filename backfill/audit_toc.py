#!/usr/bin/env python3
"""
Audit all toc.json files against source PDFs for known error patterns.

Checks learned from Vol 1-3 manual review:
  - Back matter (ISSN, Publications Received) included in last article
  - Page gaps between consecutive articles (missing content)
  - Single-page book reviews (often wrong pdf_page_end)
  - Book reviews > 10 pages (may contain multiple reviews)
  - Missing book review fields (reviewer, book_title, book_author)
  - Identical page ranges (combined reviews — may be correct)
  - Page coverage gaps or overlaps
  - Last article ending too close/far from total_pdf_pages
  - Section field validation
  - Reviewer name verification against PDF text

Usage:
    python backfill/audit_toc.py                    # Audit all issues
    python backfill/audit_toc.py --fix              # Auto-fix safe issues (back matter)
    python backfill/audit_toc.py backfill/output/4  # Audit single issue
"""

import sys
import os
import re
import json
import argparse
import glob

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
    sys.exit(1)


VALID_SECTIONS = {"Editorial", "Articles", "Book Reviews", "Book Review Editorial"}

BACK_MATTER_PATTERNS = [
    re.compile(r'ISSN\s+\d{4}[- ]?\d{3}[\dXx]', re.IGNORECASE),
    re.compile(r'publications?\s+(and\s+films?\s+)?received\s+for\s+(possible\s+)?review', re.IGNORECASE),
    re.compile(r'membership\s+of\s+the\s+society\s+for\s+existential\s+analysis', re.IGNORECASE),
    re.compile(r'the\s+aim\s+of\s+the\s+society\s+for\s+existential\s+analysis', re.IGNORECASE),
    re.compile(r'advertising\s+rates', re.IGNORECASE),
    re.compile(r'membership\s+(form|of\s+the)', re.IGNORECASE),
    re.compile(r'subscription\s+(rates|info)', re.IGNORECASE),
    re.compile(r'back\s+issues\s+and\s+publications', re.IGNORECASE),
    re.compile(r'information\s+for\s+contributors', re.IGNORECASE),
    re.compile(r'notes\s+for\s+contributors', re.IGNORECASE),
]


def load_toc(issue_dir):
    """Load toc.json from an issue directory."""
    toc_path = os.path.join(issue_dir, 'toc.json')
    if not os.path.exists(toc_path):
        return None, toc_path
    with open(toc_path) as f:
        return json.load(f), toc_path


def get_issue_label(toc_data):
    """Get human-readable label like 'Vol 4' or 'Vol 23.1'."""
    v = toc_data.get('volume', '?')
    i = toc_data.get('issue', 1)
    if v in (1, 2, 3, 4, 5) or i == 1 and v <= 5:
        return f"Vol {v}"
    return f"Vol {v}.{i}"


def get_pdf_path(toc_data):
    """Get the source PDF path."""
    return toc_data.get('source_pdf', '')


def check_back_matter(toc_data, doc):
    """Check if the last article includes back matter pages.

    Scans backwards from the last article's pdf_page_end looking for
    back matter (ISSN, Publications Received, Society blurb, ads, etc.).
    """
    issues = []
    articles = toc_data.get('articles', [])
    if not articles:
        return issues

    last = articles[-1]
    last_start = last.get('pdf_page_start', 0)
    last_end = last.get('pdf_page_end', 0)

    # Scan backwards from last_end to find earliest back matter page
    earliest_back_matter = None
    for page_idx in range(last_end, max(last_start - 1, last_end - 15), -1):
        if page_idx >= len(doc) or page_idx < 0:
            continue
        text = doc[page_idx].get_text()
        # Strip running headers (e.g., "Existential Analysis: Journal of...")
        lines = [l.strip() for l in text.split('\n') if l.strip()
                 and not l.strip().startswith('Existential Analysis:')
                 and not l.strip().isdigit()]
        content = ' '.join(lines)

        is_back = False
        matched_pattern = None
        for pattern in BACK_MATTER_PATTERNS:
            if pattern.search(content):
                # Don't flag if the page has substantial article content
                # (a Society blurb footer on a content page is not back matter)
                non_header_lines = [l for l in lines if len(l) > 10]
                if len(non_header_lines) < 8:
                    is_back = True
                    matched_pattern = pattern.pattern[:50]
                break

        if is_back:
            earliest_back_matter = (page_idx, matched_pattern)
        else:
            # Found a content page — stop scanning backwards
            break

    if earliest_back_matter:
        bm_page, bm_pattern = earliest_back_matter
        if bm_page > last_start:  # Don't suggest removing entire article
            issues.append({
                'type': 'back_matter',
                'severity': 'auto_fixable',
                'article_idx': len(articles) - 1,
                'article_title': last.get('title', ''),
                'page': bm_page,
                'pattern': bm_pattern,
                'current_end': last_end,
                'suggested_end': bm_page - 1,
                'detail': f"Pages {bm_page}-{last_end} are back matter (pattern: {bm_pattern})"
            })

    return issues


def check_page_gaps(toc_data):
    """Check for page gaps between consecutive articles."""
    issues = []
    articles = toc_data.get('articles', [])

    for i in range(len(articles) - 1):
        curr = articles[i]
        nxt = articles[i + 1]
        curr_end = curr.get('pdf_page_end', 0)
        nxt_start = nxt.get('pdf_page_start', 0)
        gap = nxt_start - curr_end

        # Gap of 2+ pages means there's content between articles that's not catalogued
        if gap >= 3:
            issues.append({
                'type': 'page_gap',
                'severity': 'warning',
                'article_idx': i,
                'article_title': curr.get('title', ''),
                'next_title': nxt.get('title', ''),
                'gap_pages': gap - 1,
                'curr_end': curr_end,
                'next_start': nxt_start,
                'detail': f"Gap of {gap - 1} pages between #{i + 1} '{curr.get('title', '')[:40]}' (ends {curr_end}) and #{i + 2} '{nxt.get('title', '')[:40]}' (starts {nxt_start})"
            })
        # Overlap (not shared page) — where next starts BEFORE current ends
        elif gap < 0:
            # Shared page (gap == 0) is normal. But gap < 0 means overlap of more than 1 page
            if gap < 0:
                issues.append({
                    'type': 'page_overlap',
                    'severity': 'warning',
                    'article_idx': i,
                    'article_title': curr.get('title', ''),
                    'next_title': nxt.get('title', ''),
                    'overlap_pages': abs(gap),
                    'detail': f"Overlap of {abs(gap)} pages between #{i + 1} and #{i + 2}"
                })

    return issues


def check_book_reviews(toc_data):
    """Check book review specific patterns."""
    issues = []
    articles = toc_data.get('articles', [])
    reviews = [(i, a) for i, a in enumerate(articles) if a.get('section') == 'Book Reviews']

    for idx, (i, review) in enumerate(reviews):
        pages = review.get('pdf_page_end', 0) - review.get('pdf_page_start', 0) + 1

        # Single-page review
        if pages == 1:
            issues.append({
                'type': 'single_page_review',
                'severity': 'warning',
                'article_idx': i,
                'article_title': review.get('title', ''),
                'detail': f"Single-page book review #{i + 1}: '{review.get('title', '')[:50]}'"
            })

        # Very long review (> 10 pages)
        if pages > 10:
            issues.append({
                'type': 'long_review',
                'severity': 'warning',
                'article_idx': i,
                'article_title': review.get('title', ''),
                'pages': pages,
                'detail': f"Book review #{i + 1} is {pages} pages: '{review.get('title', '')[:50]}'"
            })

        # Missing fields
        for field in ('reviewer', 'book_title', 'book_author'):
            if not review.get(field):
                issues.append({
                    'type': 'missing_field',
                    'severity': 'error',
                    'article_idx': i,
                    'article_title': review.get('title', ''),
                    'field': field,
                    'detail': f"Book review #{i + 1} missing '{field}': '{review.get('title', '')[:50]}'"
                })

        # Gaps between consecutive book reviews
        if idx < len(reviews) - 1:
            next_i, next_review = reviews[idx + 1]
            gap = next_review.get('pdf_page_start', 0) - review.get('pdf_page_end', 0)
            if gap >= 3:
                issues.append({
                    'type': 'review_gap',
                    'severity': 'warning',
                    'article_idx': i,
                    'article_title': review.get('title', ''),
                    'next_title': next_review.get('title', ''),
                    'gap_pages': gap - 1,
                    'detail': f"Gap of {gap - 1} pages between reviews #{i + 1} and #{next_i + 1} — possible missing review"
                })

    # Identical page ranges
    range_map = {}
    for i, review in reviews:
        key = (review.get('pdf_page_start'), review.get('pdf_page_end'))
        if key in range_map:
            prev_i = range_map[key]
            issues.append({
                'type': 'identical_ranges',
                'severity': 'info',
                'article_idx': i,
                'article_title': review.get('title', ''),
                'other_idx': prev_i,
                'detail': f"Reviews #{prev_i + 1} and #{i + 1} have identical page ranges (combined review?)"
            })
        range_map[key] = i

    return issues


def check_sections(toc_data):
    """Check section field validity."""
    issues = []
    for i, article in enumerate(toc_data.get('articles', [])):
        section = article.get('section', '')
        if section not in VALID_SECTIONS:
            issues.append({
                'type': 'invalid_section',
                'severity': 'error',
                'article_idx': i,
                'article_title': article.get('title', ''),
                'section': section,
                'detail': f"Article #{i + 1} has invalid section '{section}'"
            })
    return issues


def check_page_arithmetic(toc_data):
    """Check split_pages matches page range."""
    issues = []
    for i, article in enumerate(toc_data.get('articles', [])):
        start = article.get('pdf_page_start', 0)
        end = article.get('pdf_page_end', 0)
        expected = end - start + 1
        actual = article.get('split_pages')

        if end < start:
            issues.append({
                'type': 'negative_range',
                'severity': 'error',
                'article_idx': i,
                'article_title': article.get('title', ''),
                'detail': f"Article #{i + 1} has negative page range: {start}-{end}"
            })

        if actual is not None and actual != expected:
            issues.append({
                'type': 'split_pages_mismatch',
                'severity': 'error',
                'article_idx': i,
                'article_title': article.get('title', ''),
                'detail': f"Article #{i + 1} split_pages={actual} but range is {start}-{end} ({expected} pages)"
            })
    return issues


def check_coverage(toc_data):
    """Check that articles cover the PDF reasonably."""
    issues = []
    articles = toc_data.get('articles', [])
    total = toc_data.get('total_pdf_pages', 0)

    if not articles:
        issues.append({
            'type': 'no_articles',
            'severity': 'error',
            'detail': 'No articles in toc.json'
        })
        return issues

    first_start = articles[0].get('pdf_page_start', 0)
    last_end = articles[-1].get('pdf_page_end', 0)

    # First article should start within first 10 pages
    if first_start > 10:
        issues.append({
            'type': 'late_start',
            'severity': 'warning',
            'detail': f"First article starts at page {first_start} (expected < 10)"
        })

    # Last article should end within 3 pages of total
    gap_to_end = (total - 1) - last_end
    if gap_to_end > 3:
        issues.append({
            'type': 'early_end',
            'severity': 'warning',
            'detail': f"Last article ends at page {last_end} but PDF has {total} pages (gap of {gap_to_end})"
        })

    return issues


def check_reviewer_in_pdf(toc_data, doc):
    """Check if reviewer names appear on the review's pages in the PDF.

    Searches ALL pages in the review's range plus 2 pages past the end
    (to catch cases where pdf_page_end is slightly too short).
    """
    issues = []
    articles = toc_data.get('articles', [])

    for i, article in enumerate(articles):
        if article.get('section') != 'Book Reviews':
            continue
        reviewer = article.get('reviewer', '')
        if not reviewer:
            continue

        start = article.get('pdf_page_start', 0)
        end = article.get('pdf_page_end', 0)

        found = False
        # Try exact surname match
        surname = reviewer.split()[-1].lower() if reviewer.split() else ''
        # Normalize apostrophes for matching
        surname_norm = surname.replace('\u2019', "'").replace('\u2018', "'")

        # Search all pages in range + 2 pages past end
        for page_idx in range(start, min(end + 3, len(doc))):
            if page_idx >= len(doc):
                continue
            text = doc[page_idx].get_text().lower()
            text_norm = text.replace('\u2019', "'").replace('\u2018', "'")
            if surname_norm and (surname_norm in text_norm):
                found = True
                break

        if not found and surname_norm:
            issues.append({
                'type': 'reviewer_not_in_pdf',
                'severity': 'warning',
                'article_idx': i,
                'article_title': article.get('title', ''),
                'reviewer': reviewer,
                'pages_checked': f"{start}-{min(end + 2, len(doc) - 1)}",
                'detail': f"Reviewer '{reviewer}' surname not found on pages {start}-{min(end + 2, len(doc) - 1)}"
            })

    return issues


def check_book_title_in_pdf(toc_data, doc):
    """Check if book titles appear on the review's first page."""
    issues = []
    articles = toc_data.get('articles', [])

    for i, article in enumerate(articles):
        if article.get('section') != 'Book Reviews':
            continue
        book_title = article.get('book_title', '')
        if not book_title:
            continue

        start = article.get('pdf_page_start', 0)
        if start >= len(doc):
            continue

        # Get text from first 2 pages
        found = False
        # Extract significant words from title (skip short words)
        title_words = [w.lower() for w in re.findall(r'\w+', book_title) if len(w) > 3]
        if not title_words:
            continue

        for page_idx in range(start, min(start + 2, len(doc))):
            text = doc[page_idx].get_text().lower()
            # Check if majority of significant title words appear
            matches = sum(1 for w in title_words if w in text)
            if matches >= len(title_words) * 0.6:
                found = True
                break

        if not found:
            issues.append({
                'type': 'book_title_not_on_start_page',
                'severity': 'warning',
                'article_idx': i,
                'article_title': article.get('title', ''),
                'book_title': book_title,
                'start_page': start,
                'detail': f"Book title '{book_title[:50]}' not found on pages {start}-{min(start + 1, len(doc) - 1)}"
            })

    return issues


def audit_issue(issue_dir, fix=False):
    """Run all checks on a single issue. Returns dict of results."""
    toc_data, toc_path = load_toc(issue_dir)
    if toc_data is None:
        return {'error': f'No toc.json in {issue_dir}', 'issues': []}

    label = get_issue_label(toc_data)
    pdf_path = get_pdf_path(toc_data)

    result = {
        'label': label,
        'dir': issue_dir,
        'volume': toc_data.get('volume'),
        'issue': toc_data.get('issue'),
        'total_articles': len(toc_data.get('articles', [])),
        'book_reviews': len([a for a in toc_data.get('articles', []) if a.get('section') == 'Book Reviews']),
        'issues': [],
        'fixes_applied': [],
    }

    # Non-PDF checks first
    result['issues'].extend(check_sections(toc_data))
    result['issues'].extend(check_page_arithmetic(toc_data))
    result['issues'].extend(check_page_gaps(toc_data))
    result['issues'].extend(check_book_reviews(toc_data))
    result['issues'].extend(check_coverage(toc_data))

    # PDF-dependent checks
    if os.path.exists(pdf_path):
        try:
            doc = fitz.open(pdf_path)

            # Verify total_pdf_pages
            if len(doc) != toc_data.get('total_pdf_pages', 0):
                result['issues'].append({
                    'type': 'wrong_total_pages',
                    'severity': 'error',
                    'detail': f"toc.json says {toc_data.get('total_pdf_pages')} pages but PDF has {len(doc)}"
                })

            back_matter = check_back_matter(toc_data, doc)
            result['issues'].extend(back_matter)

            result['issues'].extend(check_reviewer_in_pdf(toc_data, doc))
            result['issues'].extend(check_book_title_in_pdf(toc_data, doc))

            # Auto-fix back matter if requested
            if fix and back_matter:
                for bm in back_matter:
                    if bm['severity'] == 'auto_fixable':
                        articles = toc_data['articles']
                        old_end = articles[-1]['pdf_page_end']
                        new_end = bm['suggested_end']
                        articles[-1]['pdf_page_end'] = new_end
                        articles[-1]['split_pages'] = new_end - articles[-1]['pdf_page_start'] + 1
                        result['fixes_applied'].append(
                            f"Fixed last article pdf_page_end: {old_end} → {new_end} (removed back matter)"
                        )

                if result['fixes_applied']:
                    with open(toc_path, 'w') as f:
                        json.dump(toc_data, f, indent=2, ensure_ascii=False)
                        f.write('\n')

            doc.close()
        except Exception as e:
            result['issues'].append({
                'type': 'pdf_error',
                'severity': 'error',
                'detail': f"Could not open PDF: {e}"
            })
    else:
        result['issues'].append({
            'type': 'no_pdf',
            'severity': 'error',
            'detail': f"Source PDF not found: {pdf_path}"
        })

    return result


def find_all_issue_dirs():
    """Find all issue directories with toc.json files."""
    dirs = []
    output_dir = os.path.join(os.path.dirname(__file__), 'output')

    for name in sorted(os.listdir(output_dir)):
        issue_dir = os.path.join(output_dir, name)
        if os.path.isdir(issue_dir) and os.path.exists(os.path.join(issue_dir, 'toc.json')):
            dirs.append(issue_dir)

    # Sort by volume.issue numerically
    def sort_key(d):
        name = os.path.basename(d)
        parts = name.split('.')
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except ValueError:
            return (999, 0)

    return sorted(dirs, key=sort_key)


def print_report(results, verbose=False):
    """Print human-readable audit report."""
    total_issues = 0
    by_severity = {'error': 0, 'warning': 0, 'info': 0, 'auto_fixable': 0}
    by_type = {}
    clean_volumes = []
    problem_volumes = []

    for r in results:
        if r.get('error'):
            print(f"  ERROR: {r['error']}")
            continue

        issues = r['issues']
        fixes = r.get('fixes_applied', [])

        if not issues and not fixes:
            clean_volumes.append(r['label'])
            continue

        real_issues = [i for i in issues if i['severity'] != 'info']
        if real_issues or fixes:
            problem_volumes.append(r['label'])

        for issue in issues:
            total_issues += 1
            sev = issue['severity']
            by_severity[sev] = by_severity.get(sev, 0) + 1
            itype = issue['type']
            by_type[itype] = by_type.get(itype, 0) + 1

    # Summary
    print("\n" + "=" * 70)
    print("AUDIT SUMMARY")
    print("=" * 70)
    print(f"Volumes audited: {len(results)}")
    print(f"Clean volumes: {len(clean_volumes)}")
    print(f"Volumes with issues: {len(problem_volumes)}")
    print(f"Total issues found: {total_issues}")
    print()

    if by_severity:
        print("By severity:")
        for sev in ('error', 'auto_fixable', 'warning', 'info'):
            if by_severity.get(sev, 0) > 0:
                print(f"  {sev}: {by_severity[sev]}")

    if by_type:
        print("\nBy type:")
        for itype, count in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"  {itype}: {count}")

    # Fixes applied
    all_fixes = [f for r in results for f in r.get('fixes_applied', [])]
    if all_fixes:
        print(f"\nFixes applied: {len(all_fixes)}")
        for r in results:
            for fix in r.get('fixes_applied', []):
                print(f"  {r['label']}: {fix}")

    # Detail per volume
    print("\n" + "-" * 70)
    print("DETAIL BY VOLUME")
    print("-" * 70)

    for r in results:
        if r.get('error'):
            continue
        issues = r['issues']
        fixes = r.get('fixes_applied', [])
        if not issues and not fixes and not verbose:
            continue

        status = "CLEAN" if not issues else f"{len(issues)} issue(s)"
        print(f"\n{r['label']} — {r['total_articles']} articles, {r['book_reviews']} reviews — {status}")

        for fix in fixes:
            print(f"  FIXED: {fix}")

        for issue in issues:
            sev_marker = {'error': 'ERROR', 'warning': 'WARN', 'info': 'INFO', 'auto_fixable': 'FIX'}
            marker = sev_marker.get(issue['severity'], '???')
            print(f"  [{marker}] {issue['detail']}")

    if clean_volumes:
        print(f"\nClean volumes ({len(clean_volumes)}): {', '.join(clean_volumes)}")


def save_report(results, output_path):
    """Save machine-readable report."""
    report = {
        'total_volumes': len(results),
        'clean': sum(1 for r in results if not r.get('issues') and not r.get('error')),
        'with_issues': sum(1 for r in results if r.get('issues')),
        'fixes_applied': sum(len(r.get('fixes_applied', [])) for r in results),
        'by_type': {},
        'volumes': {},
    }

    for r in results:
        if r.get('error'):
            continue
        label = r['label']
        report['volumes'][label] = {
            'total_articles': r['total_articles'],
            'book_reviews': r['book_reviews'],
            'issues': r['issues'],
            'fixes_applied': r.get('fixes_applied', []),
        }
        for issue in r['issues']:
            itype = issue['type']
            report['by_type'][itype] = report['by_type'].get(itype, 0) + 1

    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write('\n')

    print(f"\nReport saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Audit toc.json files against source PDFs')
    parser.add_argument('dirs', nargs='*', help='Issue directories to audit (default: all)')
    parser.add_argument('--fix', action='store_true', help='Auto-fix safe issues (back matter)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show clean volumes too')
    parser.add_argument('--json', '-j', metavar='PATH', help='Save JSON report to file')
    args = parser.parse_args()

    if args.dirs:
        issue_dirs = args.dirs
    else:
        issue_dirs = find_all_issue_dirs()

    if not issue_dirs:
        print("No issue directories found.")
        sys.exit(1)

    print(f"Auditing {len(issue_dirs)} issues...")
    if args.fix:
        print("Auto-fix mode: will fix back matter issues")

    results = []
    for issue_dir in issue_dirs:
        issue_dir = issue_dir.rstrip('/')
        label = os.path.basename(issue_dir)
        print(f"  Checking {label}...", end='', flush=True)
        result = audit_issue(issue_dir, fix=args.fix)
        n = len(result.get('issues', []))
        nf = len(result.get('fixes_applied', []))
        status = 'clean' if n == 0 else f'{n} issues'
        if nf:
            status += f', {nf} fixed'
        print(f" {status}")
        results.append(result)

    print_report(results, verbose=args.verbose)

    report_path = args.json or os.path.join(os.path.dirname(__file__), 'output', 'audit-report.json')
    save_report(results, report_path)

    # Exit code: 1 if any errors found
    has_errors = any(
        any(i['severity'] == 'error' for i in r.get('issues', []))
        for r in results
    )
    sys.exit(1 if has_errors else 0)


if __name__ == '__main__':
    main()
