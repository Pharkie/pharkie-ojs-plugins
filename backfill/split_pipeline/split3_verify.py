#!/usr/bin/env python3
"""
Step 3b: Verify split PDFs match their TOC titles.

Extracts text from the first page of each split PDF and checks that the
TOC title words appear there. Catches page-offset errors (wrong article
assigned to wrong title) and TOC parsing mistakes.

Usage:
    python backfill/verify_split.py <toc.json>

Exit codes:
    0 — all articles matched (or skipped gracefully)
    1 — one or more articles failed verification
"""

import sys
import os
import re
import json
import argparse

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


# Words too common or short to be meaningful for matching
STOP_WORDS = {
    'a', 'an', 'and', 'the', 'of', 'in', 'on', 'to', 'for', 'is', 'it',
    'by', 'at', 'or', 'as', 'from', 'with', 'its', 'not', 'be', 'are',
    'was', 'but', 'no', 'if', 'we', 'our', 'has', 'had', 'how', 'when',
    'what', 'who', 'which', 'that', 'this', 'my', 'me', 'can',
    # Common in book review titles
    'book', 'review', 'reviews', 'editorial', 'obituary',
}


def extract_title_words(title):
    """Extract significant words from a title for matching."""
    # Strip prefixes like "Book Review: " or "Obituary: "
    title = re.sub(r'^(Book Review|Obituary)\s*:\s*', '', title, flags=re.IGNORECASE)
    # Lowercase, split on non-word chars
    words = re.findall(r'[a-z]+', title.lower())
    # Filter stop words and very short words
    return [w for w in words if w not in STOP_WORDS and len(w) > 2]


def extract_pdf_first_page_text(pdf_path, max_pages=2):
    """Extract text from the first page(s) of a PDF."""
    doc = fitz.open(pdf_path)
    text = ''
    for i in range(min(max_pages, len(doc))):
        text += doc[i].get_text() + ' '
    doc.close()
    return text.lower()


def verify_title_in_pdf(title, pdf_path):
    """Check that a title's significant words appear in the PDF's first page.

    Returns (matched, total, matched_words, missing_words).
    """
    title_words = extract_title_words(title)
    if not title_words:
        # No significant words (e.g., "Editorial") — skip
        return len(title_words), len(title_words), title_words, []

    pdf_text = extract_pdf_first_page_text(pdf_path)

    matched = []
    missing = []
    for word in title_words:
        if word in pdf_text:
            matched.append(word)
        else:
            missing.append(word)

    return len(matched), len(title_words), matched, missing


def verify_split(toc_data):
    """Verify all split PDFs match their TOC titles.

    Returns (passed, warned, failed, results).
    """
    articles = toc_data.get('articles', [])
    passed = 0
    warned = 0
    failed = 0
    results = []

    for article in articles:
        title = article['title']
        pdf_path = article.get('split_pdf')

        if not pdf_path or not os.path.exists(pdf_path):
            results.append({
                'title': title,
                'status': 'skip',
                'detail': 'no split PDF',
            })
            continue

        title_words = extract_title_words(title)
        if not title_words:
            # Titles like "Editorial" or "Book Reviews" have no meaningful
            # words after filtering — skip gracefully
            print(f"  SKIP  {title[:60]} (no matchable words)", file=sys.stderr)
            results.append({
                'title': title,
                'status': 'skip',
                'detail': 'no significant words to match',
            })
            passed += 1
            continue

        matched_count, total, matched_words, missing_words = verify_title_in_pdf(
            title, pdf_path
        )

        if total == 0:
            ratio = 1.0
        else:
            ratio = matched_count / total

        if ratio >= 0.6:
            print(f"  OK    {title[:60]} ({matched_count}/{total})", file=sys.stderr)
            results.append({
                'title': title,
                'status': 'pass',
                'matched': matched_count,
                'total': total,
            })
            passed += 1
        elif ratio >= 0.3:
            print(f"  WARN  {title[:60]} ({matched_count}/{total}, missing: {missing_words})",
                  file=sys.stderr)
            results.append({
                'title': title,
                'status': 'warn',
                'matched': matched_count,
                'total': total,
                'missing': missing_words,
            })
            warned += 1
        else:
            print(f"  FAIL  {title[:60]} ({matched_count}/{total}, missing: {missing_words})",
                  file=sys.stderr)
            results.append({
                'title': title,
                'status': 'fail',
                'matched': matched_count,
                'total': total,
                'missing': missing_words,
            })
            failed += 1

    return passed, warned, failed, results


def main():
    parser = argparse.ArgumentParser(
        description='Verify split PDFs match their TOC titles')
    parser.add_argument('toc_json', help='TOC JSON file (after split.py)')
    args = parser.parse_args()

    if fitz is None:
        print("ERROR: PyMuPDF (fitz) not installed", file=sys.stderr)
        sys.exit(1)

    with open(args.toc_json) as f:
        toc_data = json.load(f)

    print(f"Verifying {len(toc_data['articles'])} articles...", file=sys.stderr)

    passed, warned, failed, results = verify_split(toc_data)

    print(f"\nVerify split: {passed} ok, {warned} warn, {failed} fail",
          file=sys.stderr)

    if failed > 0:
        print("\nFailed articles may have wrong page assignments. "
              "Check TOC parsing and page offsets.", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
