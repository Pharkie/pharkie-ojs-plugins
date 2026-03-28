#!/usr/bin/env python3
"""
Step 2b: Split an issue PDF into individual article PDFs.

Takes the TOC JSON see docs/backfill-toc-guide.md and the source PDF, outputs one PDF per article.

Usage:
    python backfill/split.py <toc.json> [--output-dir ./split-output]

Output structure:
    split-output/
        37.1/
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


def _clean_for_match(text):
    """Lowercase, strip non-alphanumeric, collapse whitespace. For title matching."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', text.lower())).strip()


def title_on_first_page(pdf_path, title):
    """Check if the article title appears on page 1 of the split PDF.

    Returns True if found, False if not. Uses two strategies:
    1. Exact substring match (cleaned text)
    2. Word overlap — at least 80% of significant title words found on page 1
       (handles line breaks splitting the title across lines in PDF text)
    """
    if not title:
        return True  # Can't check without a title
    doc = fitz.open(pdf_path)
    if len(doc) == 0:
        doc.close()
        return False
    page1_text = doc[0].get_text()
    doc.close()

    # Strip toc.json prefixes that don't appear in the PDF
    title = re.sub(r'^(Book Review|Film Review|Exhibition Report|Poem|Personally Speaking|Obituary|Essay Review|Letter to the Editors?):\s*', '', title)
    clean_title = _clean_for_match(title)
    clean_page = _clean_for_match(page1_text)

    # Strategy 1: exact substring
    if clean_title in clean_page:
        return True

    # Strategy 2: word overlap (handles line breaks in title)
    title_words = [w for w in clean_title.split() if len(w) > 2]
    if not title_words:
        return True
    page_words = set(clean_page.split())
    # Check both exact word match AND substring match (PDF extraction
    # often fuses words across line breaks: "2002knowing" instead of
    # "2002 knowing")
    found = 0
    for w in title_words:
        if w in page_words:
            found += 1
        elif w in clean_page:  # substring of the full text
            found += 1
    return found / len(title_words) >= 0.8


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

    # Single-issue volumes (1-5) use just the volume number as dir name
    dir_name = str(vol) if vol <= 5 and iss == 1 else f"{vol}.{iss}"
    issue_dir = os.path.join(output_dir, dir_name)
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

        # Verify article title appears on first page of split PDF
        if not title_on_first_page(filepath, article.get('title', '')):
            print(f"  ⚠ {filename} ({pages}pp) — WARNING: title not found on first page", file=sys.stderr)
            article['_split_warning'] = True
        else:
            article.pop('_split_warning', None)
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
    parser.add_argument('toc_json', help='TOC JSON file see docs/backfill-toc-guide.md')
    parser.add_argument('--output-dir', '-o', default='./backfill/private/output',
                        help='Output directory (default: ./backfill/private/output)')
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
