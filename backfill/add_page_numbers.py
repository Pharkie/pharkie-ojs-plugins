#!/usr/bin/env python3
"""
Auto-detect printed page numbers from source PDFs and populate
journal_page_start / journal_page_end in toc.json files.

The journal uses printed page numbers (not 0-based PDF indices).
The offset between PDF page index and printed page number varies
per issue:
  - Issue .1 (first in volume): typically starts at printed page 1 or 3
  - Issue .2 (second in volume): continues from where .1 left off

Detection strategy:
  1. Open the source PDF for each issue
  2. Starting from the first article's pdf_page_start, scan pages for
     a standalone digit in the header (top 5 lines) or footer (last 3 lines)
  3. Calculate offset = printed_number - detected_pdf_page
  4. Apply: journal_page_start = pdf_page_start + offset (for all articles)

Usage:
    python backfill/add_page_numbers.py [--dry-run] [--issue VOL.ISS]
"""

import argparse
import glob
import json
import os
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print('ERROR: PyMuPDF (fitz) required. Install with: pip install PyMuPDF', file=sys.stderr)
    sys.exit(1)

BACKFILL_DIR = os.path.join(os.path.dirname(__file__))
INPUT_DIR = os.path.join(BACKFILL_DIR, 'private', 'input')
OUTPUT_DIR = os.path.join(BACKFILL_DIR, 'private', 'output')


def detect_page_offset(pdf_path: str, first_pdf_page: int, scan_range: int = 5) -> int | None:
    """
    Detect the offset between PDF page indices and printed page numbers.

    Scans up to `scan_range` pages starting from `first_pdf_page`, looking
    for a standalone integer in the header or footer text.

    Returns offset such that: printed_page = pdf_page + offset
    Returns None if no page number could be detected.
    """
    doc = fitz.open(pdf_path)
    try:
        for pg in range(first_pdf_page, min(first_pdf_page + scan_range, doc.page_count)):
            page = doc[pg]
            lines = [l.strip() for l in page.get_text().split('\n') if l.strip()]
            if not lines:
                continue

            # Strategy 1: standalone digit in top 5 lines (vol 21+ header style)
            for line in lines[:5]:
                if line.isdigit():
                    return int(line) - pg

            # Strategy 2: standalone digit in last 3 lines (early issue footer style)
            for line in lines[-3:]:
                if line.isdigit():
                    return int(line) - pg

        return None
    finally:
        doc.close()


def process_issue(toc_path: str, dry_run: bool = False, force: bool = False) -> dict:
    """
    Process a single toc.json file. Returns a summary dict.
    """
    vol_iss = os.path.basename(os.path.dirname(toc_path))

    with open(toc_path) as f:
        data = json.load(f)

    articles = data.get('articles', [])
    if not articles:
        return {'issue': vol_iss, 'status': 'skip', 'reason': 'no articles'}

    # Check if already populated
    if not force and all('journal_page_start' in a for a in articles):
        return {'issue': vol_iss, 'status': 'skip', 'reason': 'already populated'}

    # Find source PDF
    pdf_path = os.path.join(INPUT_DIR, f'{vol_iss}.pdf')
    if not os.path.exists(pdf_path):
        return {'issue': vol_iss, 'status': 'error', 'reason': f'PDF not found: {pdf_path}'}

    first_pdf_page = articles[0].get('pdf_page_start')
    if first_pdf_page is None:
        return {'issue': vol_iss, 'status': 'error', 'reason': 'no pdf_page_start on first article'}

    # Detect offset
    offset = detect_page_offset(pdf_path, first_pdf_page)
    if offset is None:
        return {'issue': vol_iss, 'status': 'error', 'reason': 'could not detect page number'}

    # Apply to all articles
    changes = []
    for art in articles:
        ps = art.get('pdf_page_start')
        pe = art.get('pdf_page_end')
        if ps is None or pe is None:
            continue

        jp_start = ps + offset
        jp_end = pe + offset
        old_start = art.get('journal_page_start')
        old_end = art.get('journal_page_end')

        if old_start != jp_start or old_end != jp_end:
            changes.append({
                'title': art.get('title', '?')[:60],
                'pdf': f'{ps}-{pe}',
                'old': f'{old_start}-{old_end}' if old_start is not None else None,
                'new': f'{jp_start}-{jp_end}',
            })

        art['journal_page_start'] = jp_start
        art['journal_page_end'] = jp_end

    if not dry_run and changes:
        with open(toc_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')

    return {
        'issue': vol_iss,
        'status': 'updated' if changes else 'unchanged',
        'offset': offset,
        'first_printed': first_pdf_page + offset,
        'articles': len(articles),
        'changes': len(changes),
        'details': changes if changes else None,
    }


def main():
    parser = argparse.ArgumentParser(description='Add printed page numbers to toc.json files')
    parser.add_argument('--dry-run', action='store_true', help='Show what would change without writing')
    parser.add_argument('--issue', help='Process only this issue (e.g. 35.2)')
    parser.add_argument('--force', action='store_true', help='Re-detect even if already populated')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show per-article details')
    args = parser.parse_args()

    if args.dry_run:
        print('=== DRY RUN (no files will be modified) ===\n')

    # Find toc.json files
    if args.issue:
        toc_files = [os.path.join(OUTPUT_DIR, args.issue, 'toc.json')]
        if not os.path.exists(toc_files[0]):
            print(f'ERROR: {toc_files[0]} not found', file=sys.stderr)
            sys.exit(1)
    else:
        toc_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, '*/toc.json')))

    stats = {'updated': 0, 'unchanged': 0, 'skip': 0, 'error': 0}

    for toc_path in toc_files:
        result = process_issue(toc_path, dry_run=args.dry_run, force=args.force)
        status = result['status']
        stats[status] = stats.get(status, 0) + 1

        if status == 'error':
            print(f"  ERROR {result['issue']}: {result['reason']}")
        elif status == 'skip':
            if args.verbose:
                print(f"  skip  {result['issue']}: {result['reason']}")
        elif status == 'unchanged':
            if args.verbose:
                print(f"  ok    {result['issue']}: offset={result['offset']:+d}, "
                      f"starts at p.{result['first_printed']}, no changes needed")
        elif status == 'updated':
            action = 'would update' if args.dry_run else 'updated'
            print(f"  {action} {result['issue']}: offset={result['offset']:+d}, "
                  f"starts at p.{result['first_printed']}, "
                  f"{result['changes']}/{result['articles']} articles")
            if args.verbose and result.get('details'):
                for ch in result['details']:
                    old = f' (was {ch["old"]})' if ch['old'] else ''
                    print(f"         {ch['new']:>7s}  pdf {ch['pdf']:<7s}{old}  {ch['title']}")

    print(f'\nSummary: {stats["updated"]} updated, {stats["unchanged"]} unchanged, '
          f'{stats["skip"]} skipped, {stats["error"]} errors')


if __name__ == '__main__':
    main()
