#!/usr/bin/env python3
"""
Step 2b: Split an issue PDF into individual article PDFs.

Takes the TOC JSON from parse_toc.py and the source PDF, outputs one PDF per article.

Usage:
    python backfill/split.py <toc.json> [--output-dir ./split-output]

Output structure:
    split-output/
        vol37-iss1/
            01-editorial.pdf
            02-therapy-for-the-revolution.pdf
            03-all-those-useless-passions.pdf
            ...
            15-book-review-editorial.pdf
            16-book-review-why-in-the-world-not.pdf
"""

import sys
import os
import re
import json
import argparse
import tempfile
import fitz  # PyMuPDF


def slugify(text, max_len=80):
    """Convert title to a filesystem-safe slug."""
    # Remove "Book Review: " prefix for cleaner filenames
    text = re.sub(r'^Book Review:\s*', 'book-review-', text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')
    return text[:max_len]


def split_pdf(toc_data, output_dir):
    """Split the source PDF into individual article PDFs."""
    source_pdf = toc_data['source_pdf']
    vol = toc_data.get('volume', 0)
    iss = toc_data.get('issue', 0)

    issue_dir = os.path.join(output_dir, f"vol{vol:02d}-iss{iss}")
    os.makedirs(issue_dir, exist_ok=True)

    doc = fitz.open(source_pdf)
    articles = toc_data['articles']
    created = []

    for idx, article in enumerate(articles):
        start = article['pdf_page_start']
        end = article['pdf_page_end']

        # Sanity checks
        if start >= len(doc):
            print(f"  SKIP: {article['title']} — start page {start} beyond doc length {len(doc)}", file=sys.stderr)
            continue
        end = min(end, len(doc) - 1)
        if end < start:
            print(f"  SKIP: {article['title']} — end page {end} < start {start}", file=sys.stderr)
            continue

        # Build filename
        num = f"{idx + 1:02d}"
        slug = slugify(article['title'])
        filename = f"{num}-{slug}.pdf"
        filepath = os.path.join(issue_dir, filename)

        # Extract pages
        out_doc = fitz.open()
        out_doc.insert_pdf(doc, from_page=start, to_page=end)
        out_doc.save(filepath, garbage=3, deflate=1, clean=1)
        out_doc.close()

        pages = end - start + 1
        print(f"  ✓ {filename} ({pages}pp)", file=sys.stderr)

        article['split_pdf'] = filepath
        article['split_pages'] = pages
        created.append(filepath)

    doc.close()

    total = len(articles)
    skipped = total - len(created)
    if skipped > 0:
        print(f"WARNING: {skipped}/{total} articles have no split PDF (skipped due to bad page ranges)", file=sys.stderr)

    # Save updated TOC with split file paths (atomic write)
    toc_output = os.path.join(issue_dir, 'toc.json')
    tmp_fd, tmp_path = tempfile.mkstemp(dir=issue_dir, suffix='.json.tmp')
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            json.dump(toc_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, toc_output)
    except BaseException:
        os.unlink(tmp_path)
        raise
    print(f"\nUpdated TOC written to {toc_output}", file=sys.stderr)

    return created


def main():
    parser = argparse.ArgumentParser(description='Split issue PDF into article PDFs')
    parser.add_argument('toc_json', help='TOC JSON file from parse_toc.py')
    parser.add_argument('--output-dir', '-o', default='./backfill/output',
                        help='Output directory (default: ./backfill/output)')
    args = parser.parse_args()

    with open(args.toc_json) as f:
        toc_data = json.load(f)

    print(f"Splitting: Vol {toc_data.get('volume')}.{toc_data.get('issue')}", file=sys.stderr)
    print(f"Source: {toc_data['source_pdf']}", file=sys.stderr)
    print(f"Articles: {len(toc_data['articles'])}", file=sys.stderr)
    print(f"Output: {args.output_dir}", file=sys.stderr)
    print(file=sys.stderr)

    created = split_pdf(toc_data, args.output_dir)
    print(f"\nCreated {len(created)} PDFs", file=sys.stderr)


if __name__ == '__main__':
    main()
