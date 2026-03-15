#!/usr/bin/env python3
"""
Re-OCR scanned PDFs using Claude API (Haiku) for clean text extraction.

Renders each page as an image, sends to Claude for OCR, then replaces
the text layer in the PDF. Original image content is preserved.

Usage:
    python backfill/reocr.py backfill/prepared/6.1.pdf [--dry-run] [--model haiku]
    python backfill/reocr.py --all [--dry-run]

Requires ANTHROPIC_API_KEY environment variable.
"""

import sys
import os
import re
import json
import argparse
import time
import random
import shutil
import threading
import concurrent.futures
import fitz  # PyMuPDF
import anthropic
import base64

# Scanned (image-based) PDFs that need re-OCR
SCANNED_PDFS = ['2.pdf', '6.1.pdf', '6.2.pdf', '13.1.pdf', '13.2.pdf', '14.1.pdf', '14.2.pdf']

# Model pricing per million tokens (as of 2025)
PRICING = {
    'haiku': {'input': 0.80, 'output': 4.00, 'name': 'claude-haiku-4-5-20251001'},
    'sonnet': {'input': 3.00, 'output': 15.00, 'name': 'claude-sonnet-4-5-20241022'},
}

# Estimated tokens per page (image input ~1600 tokens, text output ~400 tokens)
EST_INPUT_TOKENS_PER_PAGE = 1600
EST_OUTPUT_TOKENS_PER_PAGE = 584

OCR_PROMPT = """Extract ALL text from this scanned journal page exactly as printed.

Rules:
- Preserve paragraph structure (separate paragraphs with blank lines)
- Rejoin hyphenated line breaks: if a word is split across lines with a hyphen, rejoin it (e.g. "contempo-\\nrary" → "contemporary")
- Keep real hyphens (e.g. "well-known", "self-awareness")
- Preserve italics markers if visible
- Include page numbers, headers, footers
- For the CONTENTS/TOC page: preserve the exact title, author, and page number for each entry, one per line
- Do NOT add any commentary, just output the text"""


def estimate_cost(pdf_paths, model='haiku'):
    """Estimate API cost for re-OCR'ing the given PDFs.

    Accounts for cached pages in progress files — only uncached pages cost money.
    """
    pricing = PRICING[model]
    total_pages = 0
    cached_pages = 0
    for path in pdf_paths:
        doc = fitz.open(path)
        total_pages += len(doc)
        doc.close()
        progress_path = path + '.reocr.progress.json'
        if os.path.exists(progress_path):
            with open(progress_path) as f:
                cached_pages += len(json.load(f))

    pages_to_ocr = total_pages - cached_pages
    input_cost = (pages_to_ocr * EST_INPUT_TOKENS_PER_PAGE / 1_000_000) * pricing['input']
    output_cost = (pages_to_ocr * EST_OUTPUT_TOKENS_PER_PAGE / 1_000_000) * pricing['output']
    total_cost = input_cost + output_cost

    return {
        'pages': total_pages,
        'cached_pages': cached_pages,
        'pages_to_ocr': pages_to_ocr,
        'model': pricing['name'],
        'est_input_tokens': pages_to_ocr * EST_INPUT_TOKENS_PER_PAGE,
        'est_output_tokens': pages_to_ocr * EST_OUTPUT_TOKENS_PER_PAGE,
        'est_cost_usd': total_cost,
    }


def render_page_to_png(page, dpi=150):
    """Render a PDF page to PNG bytes."""
    # 150 DPI is enough for OCR without being too large
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    return pix.tobytes("png")


class ContentFilterError(Exception):
    """Raised when Claude's content filter blocks a page."""
    pass


def ocr_page(client, png_bytes, model_name, page_num, total_pages, max_retries=8):
    """Send a page image to Claude for OCR, return extracted text.

    Retries with exponential backoff + jitter on rate limit errors.
    Raises ContentFilterError for content policy blocks (non-retryable).
    """
    b64 = base64.standard_b64encode(png_bytes).decode('ascii')

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model_name,
                max_tokens=4096,
                messages=[{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': 'image/png',
                                'data': b64,
                            }
                        },
                        {
                            'type': 'text',
                            'text': OCR_PROMPT,
                        }
                    ]
                }]
            )

            text = response.content[0].text
            usage = response.usage
            return text, usage.input_tokens, usage.output_tokens
        except anthropic.BadRequestError as e:
            if 'content filtering' in str(e).lower():
                raise ContentFilterError(
                    f"Page {page_num}: content filter blocked OCR"
                ) from e
            raise
        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                wait = 2 ** attempt + 1 + random.uniform(0, 1)
                time.sleep(wait)
            else:
                raise


def reocr_pdf(client, pdf_path, model='haiku', dry_run=False, max_workers=3):
    """Re-OCR a single PDF, replacing its text layer.

    Uses concurrent API calls (max_workers threads) for speed.
    Failed pages are retried sequentially after the concurrent pass.
    """
    pricing = PRICING[model]
    model_name = pricing['name']

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    basename = os.path.basename(pdf_path)

    output_path = pdf_path
    temp_path = pdf_path + '.reocr.tmp'
    backup_path = pdf_path + '.bak'

    # Track progress file for resuming interrupted runs
    progress_path = pdf_path + '.reocr.progress.json'
    progress = {}
    if os.path.exists(progress_path):
        with open(progress_path) as f:
            progress = json.load(f)
        print(f"  Resuming: {len(progress)}/{total_pages} pages cached")

    # Pre-render all pages that need OCR
    pages_to_ocr = {}
    pages_text = {}
    for page_idx in range(total_pages):
        page_key = str(page_idx)
        if page_key in progress:
            pages_text[page_idx] = progress[page_key]
        else:
            pages_to_ocr[page_idx] = render_page_to_png(doc[page_idx])

    if dry_run:
        doc.close()
        return 0, 0

    if not pages_to_ocr:
        print(f"  All {total_pages} pages already cached")
    else:
        print(f"  OCR'ing {len(pages_to_ocr)} pages ({len(progress)} cached) "
              f"with {max_workers} workers...")

    total_input = 0
    total_output = 0
    completed = len(progress)
    failed_pages = []
    filtered_pages = []  # pages blocked by content filter (non-retryable)
    lock = threading.Lock()

    def process_page(page_idx):
        nonlocal total_input, total_output, completed
        png_bytes = pages_to_ocr[page_idx]
        text, inp_tok, out_tok = ocr_page(
            client, png_bytes, model_name,
            page_idx + 1, total_pages
        )
        with lock:
            total_input += inp_tok
            total_output += out_tok
            pages_text[page_idx] = text
            progress[str(page_idx)] = text
            completed += 1
            # Save progress every 5 pages, print every page
            print(f"  {basename}: {completed}/{total_pages}", flush=True)
            if completed % 5 == 0 or completed == total_pages:
                with open(progress_path, 'w') as f:
                    json.dump(progress, f)
                cost_so_far = (total_input / 1_000_000 * pricing['input'] +
                              total_output / 1_000_000 * pricing['output'])
                print(f"  {basename}: {completed}/{total_pages} pages  "
                      f"cost: ${cost_so_far:.3f}", flush=True)
        return page_idx

    if pages_to_ocr:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_page, idx): idx
                      for idx in sorted(pages_to_ocr.keys())}
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except ContentFilterError:
                    page_idx = futures[future]
                    print(f"  FILTERED page {page_idx + 1}: content filter "
                          f"(keeping existing text)", file=sys.stderr)
                    filtered_pages.append(page_idx)
                except Exception as e:
                    page_idx = futures[future]
                    print(f"  ERROR page {page_idx + 1}: {e}", file=sys.stderr)
                    failed_pages.append(page_idx)

        # Final progress save
        with open(progress_path, 'w') as f:
            json.dump(progress, f)

    # Retry failed pages sequentially (one at a time, no concurrency pressure)
    # Content-filtered pages are NOT retried — they'll keep existing text
    if failed_pages:
        print(f"  Retrying {len(failed_pages)} failed pages sequentially...")
        still_failed = []
        for page_idx in sorted(failed_pages):
            try:
                png_bytes = pages_to_ocr[page_idx]
                text, inp_tok, out_tok = ocr_page(
                    client, png_bytes, model_name,
                    page_idx + 1, total_pages
                )
                total_input += inp_tok
                total_output += out_tok
                pages_text[page_idx] = text
                progress[str(page_idx)] = text
                completed += 1
                print(f"  {basename}: retry page {page_idx + 1} OK", flush=True)
                with open(progress_path, 'w') as f:
                    json.dump(progress, f)
            except ContentFilterError:
                print(f"  FILTERED page {page_idx + 1}: content filter "
                      f"(keeping existing text)", file=sys.stderr)
                filtered_pages.append(page_idx)
            except Exception as e:
                print(f"  FATAL page {page_idx + 1}: {e}", file=sys.stderr)
                still_failed.append(page_idx)

        if still_failed:
            print(f"\n  FAILED: {len(still_failed)} pages could not be OCR'd: "
                  f"{[p + 1 for p in still_failed]}", file=sys.stderr)
            print(f"  Progress saved — re-run to retry.", file=sys.stderr)
            doc.close()
            raise RuntimeError(
                f"{basename}: {len(still_failed)} pages failed OCR: "
                f"{[p + 1 for p in still_failed]}"
            )

    # For content-filtered pages, keep existing text from the PDF
    if filtered_pages:
        print(f"  {len(filtered_pages)} pages kept existing text "
              f"(content filter): {[p + 1 for p in sorted(filtered_pages)]}")
        for page_idx in filtered_pages:
            existing_text = doc[page_idx].get_text()
            if existing_text.strip():
                pages_text[page_idx] = existing_text
            else:
                pages_text[page_idx] = ""  # blank page — nothing to do

    # Verify we have text for every page
    missing = [i + 1 for i in range(total_pages) if i not in pages_text]
    if missing:
        doc.close()
        raise RuntimeError(f"{basename}: missing text for pages {missing}")

    # Backup original before overwriting
    if not os.path.exists(backup_path):
        shutil.copy2(pdf_path, backup_path)
        print(f"  Backup: {backup_path}")

    # Now rebuild PDF with new text layer
    # Strategy: for each page, clear existing text, insert new text as invisible overlay
    for page_idx in range(total_pages):
        text = pages_text.get(page_idx)
        if not text:
            continue

        page = doc[page_idx]

        # Remove existing text by redacting all text content
        # This preserves images but removes the (bad) OCR text layer
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") == 0:  # text block
                rect = fitz.Rect(block["bbox"])
                page.add_redact_annot(rect)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        # Insert new text as invisible overlay
        # Use a very small font so text doesn't visually interfere with the scan
        tw = fitz.TextWriter(page.rect)
        font = fitz.Font("helv")
        fontsize = 1  # tiny — invisible but searchable

        # Split text into lines and position across the page
        lines = text.split('\n')
        y = 10
        line_height = 2
        for line in lines:
            if y > page.rect.height - 10:
                break
            if line.strip():
                try:
                    tw.append((10, y), line.strip(), font=font, fontsize=fontsize)
                except Exception:
                    pass  # skip lines with unsupported characters
            y += line_height

        tw.write_text(page, color=(1, 1, 1))  # white = invisible

    # Save to temp file first
    doc.save(temp_path, garbage=4, deflate=True)
    doc.close()

    # Verify output PDF before replacing original
    try:
        verify_doc = fitz.open(temp_path)
        if len(verify_doc) != total_pages:
            verify_doc.close()
            raise RuntimeError(
                f"Output PDF has {len(verify_doc)} pages, expected {total_pages}"
            )
        verify_doc.close()
    except Exception as e:
        print(f"  ERROR: Output PDF verification failed: {e}", file=sys.stderr)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    # Replace original
    os.replace(temp_path, output_path)

    # Clean up progress file only after verified output exists
    if os.path.exists(progress_path):
        os.remove(progress_path)

    return total_input, total_output


def load_env():
    """Load ANTHROPIC_API_KEY from .env if not already set."""
    if os.environ.get('ANTHROPIC_API_KEY'):
        return
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('ANTHROPIC_API_KEY=') and not line.startswith('#'):
                    os.environ['ANTHROPIC_API_KEY'] = line.split('=', 1)[1].strip()
                    return


def main():
    load_env()
    parser = argparse.ArgumentParser(description='Re-OCR scanned PDFs with Claude API')
    parser.add_argument('pdfs', nargs='*', help='PDF files to re-OCR')
    parser.add_argument('--all', action='store_true', help='Re-OCR all known scanned PDFs')
    parser.add_argument('--dry-run', action='store_true', help='Estimate cost without calling API')
    parser.add_argument('--model', default='haiku', choices=['haiku', 'sonnet'],
                       help='Claude model to use (default: haiku)')
    parser.add_argument('--workers', type=int, default=3,
                       help='Max concurrent API workers (default: 3)')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip cost confirmation prompt')
    args = parser.parse_args()

    if args.all:
        pdf_paths = [f'backfill/prepared/{f}' for f in SCANNED_PDFS]
    elif args.pdfs:
        pdf_paths = args.pdfs
    else:
        parser.error('Specify PDF files or use --all')

    # Verify files exist
    for p in pdf_paths:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    # Estimate cost
    estimate = estimate_cost(pdf_paths, args.model)
    print(f"\nRe-OCR estimate:")
    print(f"  PDFs:          {len(pdf_paths)}")
    print(f"  Total pages:   {estimate['pages']}")
    if estimate['cached_pages']:
        print(f"  Cached pages:  {estimate['cached_pages']}")
    print(f"  Pages to OCR:  {estimate['pages_to_ocr']}")
    print(f"  Model:         {estimate['model']}")
    print(f"  Est. tokens:   {estimate['est_input_tokens'] + estimate['est_output_tokens']:,}")
    print(f"  Est. cost:     ${estimate['est_cost_usd']:.2f}")
    print()

    if args.dry_run:
        print("Dry run — no API calls made.")
        return

    if not os.environ.get('ANTHROPIC_API_KEY'):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if not args.yes:
        confirm = input(f"Proceed with re-OCR? Estimated cost: ${estimate['est_cost_usd']:.2f} [y/N] ")
        if confirm.lower() not in ('y', 'yes'):
            print("Aborted.")
            return

    client = anthropic.Anthropic()
    grand_input = 0
    grand_output = 0
    pricing = PRICING[args.model]
    start_time = time.time()

    for pdf_path in pdf_paths:
        print(f"\n{'='*50}")
        print(f"Re-OCR: {os.path.basename(pdf_path)}")
        print(f"{'='*50}")

        inp, out = reocr_pdf(client, pdf_path, model=args.model, max_workers=args.workers)
        grand_input += inp
        grand_output += out

    elapsed = time.time() - start_time
    actual_cost = (grand_input / 1_000_000 * pricing['input'] +
                   grand_output / 1_000_000 * pricing['output'])

    print(f"\n{'='*50}")
    print(f"Complete!")
    print(f"  PDFs:        {len(pdf_paths)}")
    print(f"  Input:       {grand_input:,} tokens")
    print(f"  Output:      {grand_output:,} tokens")
    print(f"  Actual cost: ${actual_cost:.3f}")
    print(f"  Time:        {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print(f"{'='*50}")


if __name__ == '__main__':
    main()
