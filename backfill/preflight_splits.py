#!/usr/bin/env python3
"""
Preflight check: verify article titles appear on page 1 of split PDFs.

Iterates all toc.json files and checks every article (including book reviews).
Reports any where the title is not found on the first page — possible bad split.

Usage:
    python backfill/preflight_splits.py                    # all issues
    python backfill/preflight_splits.py --issue 36.2       # single issue
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from split import title_on_first_page

BACKFILL_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BACKFILL_DIR, 'private', 'output')


def main():
    parser = argparse.ArgumentParser(description='Check split PDFs for title on first page')
    parser.add_argument('--issue', help='Single issue (e.g. 36.2)')
    args = parser.parse_args()

    total = 0
    ok = 0
    warnings = []

    for toc_path in sorted(glob.glob(os.path.join(OUTPUT_DIR, '*/toc.json'))):
        vol_dir = os.path.dirname(toc_path)
        vol_iss = os.path.basename(vol_dir)

        if args.issue and vol_iss != args.issue:
            continue

        with open(toc_path) as f:
            toc = json.load(f)

        for idx, art in enumerate(toc.get('articles', [])):
            sp = art.get('split_pdf', '')
            if not sp:
                continue
            pdf_path = sp[2:] if sp.startswith('./') else sp
            if not os.path.exists(pdf_path):
                continue

            title = art.get('title', '')
            total += 1

            if title_on_first_page(pdf_path, title):
                ok += 1
            else:
                section = art.get('section', '')
                warnings.append({
                    'issue': vol_iss,
                    'seq': idx + 1,
                    'title': title,
                    'section': section,
                    'pdf': os.path.basename(sp),
                })

    print(f'Checked: {total} split PDFs')
    print(f'  OK:       {ok}')
    print(f'  Warnings: {len(warnings)}')

    if warnings:
        print(f'\nArticles where title NOT found on page 1:')
        for w in warnings:
            print(f"  {w['issue']} #{w['seq']:02d} [{w['section']}] {w['title'][:60]}")
            print(f"    PDF: {w['pdf']}")

    return 0 if not warnings else 1


if __name__ == '__main__':
    sys.exit(main())
