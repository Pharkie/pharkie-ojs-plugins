#!/usr/bin/env python3
"""Export backfill pipeline results (toc.json from each issue) to Google Sheets.

Usage:
    python3 backfill/sheets_export.py [--dry-run]

Reads all backfill/output/*/toc.json files and publishes a flat row-per-article
spreadsheet to the configured Google Sheet.
"""

import argparse
import json
import os
import sys
from pathlib import Path

SHEET_KEY = '189pMpS12ZuxYtMS2iLYiHKhwp6N972nZSTuNrea8Mos'
CREDS_FILE = os.path.join(os.path.dirname(__file__), '..', 'data export',
                          'sea-journal-87a19feadadd.json')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')

HEADERS = [
    'Volume', 'Issue', 'Date', 'Section', 'Title', 'Authors',
    'Journal Page Start', 'Journal Page End', 'PDF Page Start', 'PDF Page End',
    'Split Pages', 'Split PDF',
    # Book review fields
    'Book Title', 'Book Author', 'Book Year', 'Reviewer',
]


def load_all_tocs(output_dir):
    """Load and sort all toc.json files by volume/issue.

    Deduplicates by (volume, issue) — if both '<vol>/' and '<vol>.1/' exist
    for single-issue volumes, only the first one found is used.
    """
    rows = []
    seen_vol_iss = set()
    output_path = Path(output_dir)
    for toc_file in sorted(output_path.glob('*/toc.json')):
        with open(toc_file) as f:
            toc = json.load(f)
        vol = toc.get('volume', 0)
        iss = toc.get('issue', 0)
        key = (vol, iss)
        if key in seen_vol_iss:
            print(f"  Skipping duplicate vol {vol} iss {iss} from {toc_file.parent.name}/",
                  file=sys.stderr)
            continue
        seen_vol_iss.add(key)
        date = toc.get('date', '')
        for article in toc.get('articles', []):
            row = [
                vol, iss, date,
                article.get('section', ''),
                article.get('title', ''),
                article.get('authors', '') or '',
                article.get('journal_page_start', ''),
                article.get('journal_page_end', ''),
                article.get('pdf_page_start', ''),
                article.get('pdf_page_end', ''),
                article.get('split_pages', ''),
                os.path.basename(article.get('split_pdf', '') or ''),
                # Book review fields
                article.get('book_title', '') or '',
                article.get('book_author', '') or '',
                article.get('book_year', '') or '',
                article.get('reviewer', '') or '',
            ]
            rows.append((vol, iss, row))
    # Sort by volume then issue
    rows.sort(key=lambda x: (x[0], x[1]))
    return [r[2] for r in rows]


def export_to_sheets(rows, dry_run=False):
    """Push rows to Google Sheets."""
    all_data = [HEADERS] + rows
    print(f"Total rows: {len(rows)} articles/reviews across all issues")

    if dry_run:
        print("\n-- DRY RUN (first 10 rows) --")
        for row in all_data[:11]:
            print('\t'.join(str(c) for c in row))
        return

    import gspread
    gc = gspread.service_account(filename=CREDS_FILE)
    sh = gc.open_by_key(SHEET_KEY)
    ws = sh.sheet1

    # Clear and write
    ws.clear()
    ws.update(range_name='A1', values=all_data)

    # Bold header row + freeze
    ws.format('A1:P1', {'textFormat': {'bold': True}})
    ws.freeze(rows=1)

    print(f"Published {len(rows)} rows to: https://docs.google.com/spreadsheets/d/{SHEET_KEY}")


def main():
    parser = argparse.ArgumentParser(description='Export backfill results to Google Sheets')
    parser.add_argument('--dry-run', action='store_true', help='Print rows without uploading')
    args = parser.parse_args()

    if not os.path.isdir(OUTPUT_DIR):
        print(f"Error: output directory not found: {OUTPUT_DIR}", file=sys.stderr)
        sys.exit(1)

    rows = load_all_tocs(OUTPUT_DIR)
    if not rows:
        print("No toc.json files found in output/", file=sys.stderr)
        sys.exit(1)

    export_to_sheets(rows, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
