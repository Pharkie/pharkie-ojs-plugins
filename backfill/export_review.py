#!/usr/bin/env python3
"""
Export toc.json metadata to CSV for human review in Google Sheets.

Reads one or more toc.json files and outputs a single CSV with editable
columns (title, authors, section, abstract, keywords) and reference
columns (id, file, index, pages) that should not be edited.

Each article gets a stable `_review_id` (format: "v{vol}i{iss}a{index}")
stored in toc.json. This survives row reordering, title edits, and
re-exports.

Usage:
    python backfill/export_review.py backfill/private/output/37.1/toc.json -o review.csv
    python backfill/export_review.py backfill/private/output/*/toc.json -o review.csv
"""

import sys
import os
import csv
import json
import argparse
import re
import tempfile


CSV_COLUMNS = ['id', 'file', 'index', 'title', 'authors', 'section', 'abstract', 'keywords', 'pages', 'pdf_file']
ENRICHMENT_COLUMNS = ['subjects', 'disciplines', 'keywords_enriched']


def write_json_atomic(path, data):
    """Write JSON atomically via temp file + os.replace()."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)), suffix='.json.tmp')
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def assign_review_ids(toc_path, toc):
    """Assign _review_id to each article if not already set. Returns True if toc was modified."""
    # Extract vol/issue from toc metadata (preferred) or directory name
    vol = toc.get('volume', '0')
    iss = toc.get('issue', '0')

    modified = False
    for i, article in enumerate(toc.get('articles', [])):
        if '_review_id' not in article:
            article['_review_id'] = f"v{vol}i{iss}a{i}"
            modified = True

    return modified


def load_enrichment(toc_path):
    """Load enrichment.json from the same directory as toc.json, if it exists.

    Returns: {review_id: article_enrichment_dict} or empty dict.
    """
    enrichment_path = os.path.join(os.path.dirname(os.path.abspath(toc_path)), 'enrichment.json')
    if not os.path.exists(enrichment_path):
        return {}
    with open(enrichment_path) as f:
        data = json.load(f)
    return data.get('articles', {})


def export_toc(toc_path, toc, enrichment=None):
    """Read a toc and yield one row dict per article."""
    if enrichment is None:
        enrichment = {}

    for i, article in enumerate(toc.get('articles', [])):
        keywords = article.get('keywords', [])
        if isinstance(keywords, list):
            keywords = '; '.join(keywords)

        start = article.get('journal_page_start', '')
        end = article.get('journal_page_end', '')
        pages = f"{start}-{end}" if start and end else str(start or end or '')

        row = {
            'id': article.get('_review_id', ''),
            'file': toc_path,
            'index': i,
            'title': article.get('title', ''),
            'authors': article.get('authors', ''),
            'section': article.get('section', ''),
            'abstract': article.get('abstract', ''),
            'keywords': keywords,
            'pages': pages,
            'pdf_file': os.path.basename(article.get('split_pdf', '')),
        }

        # Add enrichment columns if enrichment data exists
        if enrichment:
            review_id = article.get('_review_id', '')
            enrich = enrichment.get(review_id, {})
            # Also check toc.json for subjects/disciplines (may have been imported from review)
            subjects = article.get('subjects', enrich.get('subjects', []))
            disciplines = article.get('disciplines', enrich.get('disciplines', []))
            keywords_enriched = article.get('keywords_enriched', enrich.get('keywords_enriched', []))
            if isinstance(subjects, list):
                subjects = '; '.join(subjects)
            if isinstance(disciplines, list):
                disciplines = '; '.join(disciplines)
            if isinstance(keywords_enriched, list):
                keywords_enriched = '; '.join(keywords_enriched)
            row['subjects'] = subjects
            row['disciplines'] = disciplines
            row['keywords_enriched'] = keywords_enriched

        yield row


def main():
    parser = argparse.ArgumentParser(description='Export toc.json metadata to review CSV')
    parser.add_argument('toc_files', nargs='+', help='TOC JSON files to export')
    parser.add_argument('-o', '--output', default='review.csv', help='Output CSV path (default: review.csv)')
    args = parser.parse_args()

    rows = []
    issues = 0
    has_enrichment = False
    for toc_path in args.toc_files:
        if not os.path.exists(toc_path):
            print(f"WARNING: {toc_path} not found, skipping", file=sys.stderr)
            continue

        with open(toc_path) as f:
            toc = json.load(f)

        if assign_review_ids(toc_path, toc):
            write_json_atomic(toc_path, toc)
            print(f"  Assigned _review_id to articles in {toc_path}", file=sys.stderr)

        enrichment = load_enrichment(toc_path)
        if enrichment:
            has_enrichment = True

        issue_rows = list(export_toc(toc_path, toc, enrichment))
        rows.extend(issue_rows)
        issues += 1

    if not rows:
        print("No articles found to export.", file=sys.stderr)
        sys.exit(1)

    columns = CSV_COLUMNS + (ENRICHMENT_COLUMNS if has_enrichment else [])
    with open(args.output, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

    enrichment_note = " (with enrichment columns)" if has_enrichment else ""
    print(f"Exported {len(rows)} articles from {issues} issue(s) → {args.output}{enrichment_note}", file=sys.stderr)


if __name__ == '__main__':
    main()
