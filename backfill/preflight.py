#!/usr/bin/env python3
"""
Step 1: Preflight — inventory, validate, and quality-check issue PDFs.

Usage:
    python backfill/preflight.py /path/to/pdf-folder
    python backfill/preflight.py /path/to/single-issue.pdf

Checks:
- PDF is readable and not corrupted
- Text is extractable (not scanned images)
- Page count is plausible for a journal issue
- TOC page is detectable
- Proposes consistent renaming (vol##-iss#.pdf)

Outputs a JSON report to stdout.
"""

import sys
import os
import json
import re
import fitz  # PyMuPDF


def detect_toc_page(doc):
    """Find the page containing 'CONTENTS' heading. Returns 0-based page index."""
    for i in range(min(10, len(doc))):  # TOC should be in first 10 pages
        text = doc[i].get_text()
        if re.search(r'^[-\s]*Contents[-\s]*$', text, re.IGNORECASE | re.MULTILINE):
            return i
    return None


def check_text_extractable(doc, sample_pages=5):
    """Check if meaningful text can be extracted from the PDF."""
    total_chars = 0
    pages_checked = 0
    # Check pages from the middle of the document (skip cover/TOC)
    start = min(5, len(doc) - 1)
    for i in range(start, min(start + sample_pages, len(doc))):
        text = doc[i].get_text()
        total_chars += len(text.strip())
        pages_checked += 1
    avg_chars = total_chars / max(pages_checked, 1)
    return avg_chars > 100  # At least 100 chars per page on average


def extract_vol_issue(doc):
    """Try to extract volume and issue number from cover page."""
    # Check first 3 pages for volume/issue info
    for i in range(min(3, len(doc))):
        text = doc[i].get_text()
        # Pattern: "37.1" or "Vol. 37 No. 1" or "Volume 37, Issue 1"
        m = re.search(r'(\d{1,2})\.(\d{1,2})', text)
        if m:
            vol, iss = int(m.group(1)), int(m.group(2))
            if 1 <= vol <= 50 and 1 <= iss <= 4:
                return vol, iss
        m = re.search(r'Vol(?:ume)?\.?\s*(\d+)\s*(?:No|Issue|Iss)\.?\s*(\d+)', text, re.IGNORECASE)
        if m:
            return int(m.group(1)), int(m.group(2))
    # Single-issue format (Vol 1-5): "ANALYSIS  1" or "Analysis\n2\n"
    for i in range(min(3, len(doc))):
        text = doc[i].get_text()
        m = re.search(r'Analysis\s+(\d{1,2})\s', text, re.IGNORECASE)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 50:
                return v, 1
    return None, None


def extract_date(doc):
    """Try to extract publication date from cover/TOC page."""
    months = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
    for i in range(min(5, len(doc))):
        text = doc[i].get_text()
        m = re.search(rf'({months})\s+(\d{{4}})', text)
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return None


def analyse_pdf(filepath):
    """Run all preflight checks on a single PDF."""
    result = {
        'file': os.path.basename(filepath),
        'path': os.path.abspath(filepath),
        'size_mb': round(os.path.getsize(filepath) / (1024 * 1024), 1),
        'errors': [],
        'warnings': [],
    }

    try:
        doc = fitz.open(filepath)
    except Exception as e:
        result['errors'].append(f"Cannot open PDF: {e}")
        return result

    result['pages'] = len(doc)

    # Page count sanity check
    if len(doc) < 20:
        result['warnings'].append(f"Only {len(doc)} pages — unusually short for a journal issue")
    if len(doc) > 400:
        result['warnings'].append(f"{len(doc)} pages — unusually long, might be multiple issues")

    # Text extraction check
    text_ok = check_text_extractable(doc)
    result['text_extractable'] = text_ok
    if not text_ok:
        result['errors'].append("Low text content — PDF may be scanned images, OCR needed")

    # TOC detection
    toc_page = detect_toc_page(doc)
    result['toc_page'] = toc_page
    if toc_page is None:
        result['warnings'].append("No CONTENTS page found — manual TOC input may be needed")

    # Volume/issue extraction
    vol, iss = extract_vol_issue(doc)
    result['volume'] = vol
    result['issue'] = iss
    if vol is None:
        result['warnings'].append("Could not detect volume/issue number from cover")

    # Date extraction
    date = extract_date(doc)
    result['date'] = date

    # Suggested filename
    if vol is not None and iss is not None:
        result['suggested_name'] = f"{vol}.{iss}.pdf"
    else:
        result['suggested_name'] = None

    doc.close()

    result['status'] = 'error' if result['errors'] else ('warning' if result['warnings'] else 'ok')
    return result


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <pdf-or-folder>", file=sys.stderr)
        sys.exit(1)

    target = sys.argv[1]

    if os.path.isfile(target):
        files = [target]
    elif os.path.isdir(target):
        files = sorted(
            os.path.join(target, f)
            for f in os.listdir(target)
            if f.lower().endswith('.pdf')
        )
    else:
        print(f"Error: {target} is not a file or directory", file=sys.stderr)
        sys.exit(1)

    if not files:
        print("No PDF files found", file=sys.stderr)
        sys.exit(1)

    results = []
    for f in files:
        print(f"Checking: {os.path.basename(f)}...", file=sys.stderr)
        results.append(analyse_pdf(f))

    # Summary
    ok = sum(1 for r in results if r['status'] == 'ok')
    warn = sum(1 for r in results if r['status'] == 'warning')
    err = sum(1 for r in results if r['status'] == 'error')
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Preflight: {len(results)} PDFs — {ok} ok, {warn} warnings, {err} errors", file=sys.stderr)
    for r in results:
        icon = '✓' if r['status'] == 'ok' else ('⚠' if r['status'] == 'warning' else '✗')
        name = r.get('suggested_name') or r['file']
        vol_iss = f"Vol {r.get('volume', '?')}.{r.get('issue', '?')}"
        print(f"  {icon} {r['file']} → {name} ({vol_iss}, {r.get('pages', '?')}pp, {r['size_mb']}MB)", file=sys.stderr)
        for e in r.get('errors', []):
            print(f"    ✗ {e}", file=sys.stderr)
        for w in r.get('warnings', []):
            print(f"    ⚠ {w}", file=sys.stderr)

    # Machine-readable output
    json.dump(results, sys.stdout, indent=2)


if __name__ == '__main__':
    main()
