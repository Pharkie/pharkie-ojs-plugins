#!/usr/bin/env python3
"""
Reprocess HTML galleys from raw Haiku extraction.

Reads .raw.html files and reruns the deterministic post-processing pipeline
to produce .html files. No API calls — instant, free, repeatable.

Use after fixing post-processing bugs to update all articles at once.

Usage:
    python backfill/reprocess_html.py backfill/private/output/*/toc.json     # all issues
    python backfill/reprocess_html.py backfill/private/output/36.1/toc.json  # one issue
    python backfill/reprocess_html.py backfill/private/output/*/toc.json --article=8  # one article
    python backfill/reprocess_html.py backfill/private/output/*/toc.json --verify     # reprocess + verify
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from postprocess_html import postprocess_article, verify_postprocessed


def main():
    parser = argparse.ArgumentParser(description='Reprocess HTML galleys from raw extraction')
    parser.add_argument('toc_json', nargs='+', help='toc.json file(s)')
    parser.add_argument('--article', type=int, default=None,
                        help='Process only this article (1-indexed)')
    parser.add_argument('--verify', action='store_true',
                        help='Run verification checks after reprocessing')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be reprocessed without writing')
    args = parser.parse_args()

    total = 0
    reprocessed = 0
    skipped = 0
    warnings = []

    for toc_path in args.toc_json:
        if not os.path.exists(toc_path):
            print(f"ERROR: {toc_path} not found", file=sys.stderr)
            continue
        vol_dir = os.path.dirname(toc_path)
        vol_iss = os.path.basename(vol_dir)

        with open(toc_path) as f:
            toc = json.load(f)
        articles = toc.get('articles', [])

        for idx, art in enumerate(articles):
            if args.article is not None and (idx + 1) != args.article:
                continue
            sp = art.get('split_pdf', '')
            if not sp:
                continue
            stem = os.path.splitext(os.path.basename(sp))[0]
            raw_path = os.path.join(vol_dir, f'{stem}.raw.html')
            final_path = os.path.join(vol_dir, f'{stem}.post.html')

            if not os.path.exists(raw_path):
                skipped += 1
                continue

            total += 1

            # Enrich with prev/next metadata
            if idx > 0:
                art['_prev_title'] = articles[idx - 1].get('title', '')
            if idx < len(articles) - 1:
                nxt = articles[idx + 1]
                art['_next_title'] = nxt.get('title', '')
                art['_next_page_start'] = nxt.get('pdf_page_start')
                art['_next_page_end'] = nxt.get('pdf_page_end')

            if args.dry_run:
                print(f"  {vol_iss} #{idx+1} {art.get('title', '')[:50]}")
                continue

            with open(raw_path) as f:
                raw = f.read()

            final = postprocess_article(raw, art)

            if args.verify:
                w = verify_postprocessed(raw, final, art)
                if w:
                    for x in w:
                        warnings.append(f"{vol_iss} #{idx+1}: {x}")

            with open(final_path, 'w') as f:
                f.write(final)
            reprocessed += 1

    if args.dry_run:
        print(f"\nWould reprocess: {total} articles ({skipped} skipped, no .raw.html)")
        return

    print(f"Reprocessed: {reprocessed} articles ({skipped} skipped)")
    if args.verify:
        print(f"Verification: {reprocessed - len(warnings)} OK, {len(warnings)} warnings")
        for w in warnings:
            print(f"  ⚠ {w}")

    return 0 if not warnings else 1


if __name__ == '__main__':
    sys.exit(main() or 0)
