#!/usr/bin/env python3
"""Reconstruct toc.json files from Google Sheet data.

Reads the backfill review spreadsheet and recreates toc.json files in
backfill/output/<vol>.<iss>/ with all available metadata.

Also derives source_pdf and total_pdf_pages from backfill/input/ PDFs.

Usage:
    python3 backfill/reconstruct_toc.py --dry-run    # Preview without writing
    python3 backfill/reconstruct_toc.py               # Reconstruct all
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / 'output'
PREPARED_DIR = SCRIPT_DIR / 'input'
CREDS_FILE = SCRIPT_DIR.parent / 'data-export' / 'sea-journal-87a19feadadd.json'
SHEET_KEY = '189pMpS12ZuxYtMS2iLYiHKhwp6N972nZSTuNrea8Mos'


def issue_dir_name(vol, iss):
    """Match the naming convention from split-issue.sh."""
    if vol <= 5 and iss == 1:
        return str(vol)
    return f'{vol}.{iss}'


def find_pdf(vol, iss):
    """Find the prepared PDF for a volume/issue."""
    dirname = issue_dir_name(vol, iss)
    # Try exact match first
    pdf = PREPARED_DIR / f'{dirname}.pdf'
    if pdf.exists():
        return str(pdf.resolve())
    # Single-issue volumes: try <vol>.pdf
    if iss == 1:
        pdf = PREPARED_DIR / f'{vol}.pdf'
        if pdf.exists():
            return str(pdf.resolve())
    return None


def get_total_pages(pdf_path):
    """Get total page count from a PDF."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return None


def load_sheet_data():
    """Load all rows from the Google Sheet."""
    import gspread
    gc = gspread.service_account(filename=str(CREDS_FILE))
    sh = gc.open_by_key(SHEET_KEY)
    ws = sh.sheet1
    data = ws.get_all_values()
    headers = data[0]
    rows = data[1:]
    return headers, rows


def reconstruct_tocs(headers, rows):
    """Group sheet rows by volume/issue and build toc.json structures."""
    # Map column names to indices
    col = {h: i for i, h in enumerate(headers)}

    # Group by (volume, issue)
    issues = defaultdict(list)
    for row in rows:
        vol = int(row[col['Volume']])
        iss = int(row[col['Issue']])
        issues[(vol, iss)].append(row)

    tocs = {}
    for (vol, iss), articles_rows in sorted(issues.items()):
        # Get date from first row
        date = articles_rows[0][col['Date']]

        # Find PDF
        pdf_path = find_pdf(vol, iss)
        total_pages = get_total_pages(pdf_path) if pdf_path else None

        articles = []
        for r in articles_rows:
            article = {
                'title': r[col['Title']],
                'authors': r[col['Authors']] or None,
                'section': r[col['Section']],
                'pdf_page_start': int(r[col['PDF Page Start']]) if r[col['PDF Page Start']] else None,
                'pdf_page_end': int(r[col['PDF Page End']]) if r[col['PDF Page End']] else None,
            }

            # Book review fields
            book_title = r[col['Book Title']] if 'Book Title' in col and r[col['Book Title']] else ''
            if book_title:
                article['book_title'] = book_title
                article['book_author'] = r[col['Book Author']] if r[col['Book Author']] else ''
                article['book_year'] = int(r[col['Book Year']]) if r[col['Book Year']] else None
                article['reviewer'] = r[col['Reviewer']] if r[col['Reviewer']] else ''

            # Publisher (optional)
            if 'Publisher' in col and r[col['Publisher']]:
                article['publisher'] = r[col['Publisher']]

            # Enrichment fields (optional — populated by enrich.py)
            def parse_list(val):
                if not val:
                    return None
                return [v.strip() for v in val.split(';') if v.strip()]

            for field, sheet_col in [
                ('subjects', 'Subjects'), ('disciplines', 'Disciplines'),
                ('themes', 'Themes'), ('thinkers', 'Thinkers'),
                ('modalities', 'Modalities'), ('keywords_enriched', 'Keywords Enriched'),
            ]:
                if sheet_col in col and r[col[sheet_col]]:
                    article[field] = parse_list(r[col[sheet_col]])

            for field, sheet_col in [
                ('methodology', 'Methodology'), ('summary', 'Summary'),
            ]:
                if sheet_col in col and r[col[sheet_col]]:
                    article[field] = r[col[sheet_col]]

            articles.append(article)

        toc = {
            'source_pdf': pdf_path or f'backfill/input/{issue_dir_name(vol, iss)}.pdf',
            'volume': vol,
            'issue': iss,
            'date': date,
            'total_pdf_pages': total_pages,
            'articles': articles,
        }
        tocs[(vol, iss)] = toc

    return tocs


def main():
    parser = argparse.ArgumentParser(description='Reconstruct toc.json from Google Sheet')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    print('Loading Google Sheet data...')
    headers, rows = load_sheet_data()
    print(f'  {len(rows)} rows, columns: {headers}')

    print('Reconstructing toc.json files...')
    tocs = reconstruct_tocs(headers, rows)
    print(f'  {len(tocs)} issues')

    written = 0
    for (vol, iss), toc in sorted(tocs.items()):
        dirname = issue_dir_name(vol, iss)
        outdir = OUTPUT_DIR / dirname
        outfile = outdir / 'toc.json'

        article_count = len(toc['articles'])
        pdf_status = 'OK' if toc['total_pdf_pages'] else 'NO PDF'

        if args.dry_run:
            print(f'  [dry-run] {dirname}/toc.json — {article_count} articles, {pdf_status}')
            continue

        outdir.mkdir(parents=True, exist_ok=True)
        with open(outfile, 'w') as f:
            json.dump(toc, f, indent=2, ensure_ascii=False)
        written += 1

    if args.dry_run:
        print(f'\nDry run: would write {len(tocs)} toc.json files')
    else:
        print(f'\nWrote {written} toc.json files to {OUTPUT_DIR}')


if __name__ == '__main__':
    main()
