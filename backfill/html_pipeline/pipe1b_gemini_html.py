#!/usr/bin/env python3
"""
Re-generate HTML galleys for PyMuPDF fallback articles using Gemini Flash.

These 39 articles were content-filtered by Haiku during the original htmlgen
run. This script sends the same page images to Gemini 2.5 Flash, which has
no content filter for academic text.

Usage:
    python3 backfill/htmlgen_gemini.py --dry-run    # List files, estimate cost
    python3 backfill/htmlgen_gemini.py --apply       # Process all fallbacks
    python3 backfill/htmlgen_gemini.py --apply --article=3  # Process one (1-indexed)

Requires GOOGLE_API_KEY in .env or environment.
"""

import sys
import os
import re
import json
import glob
import argparse
import time
import base64

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF required. pip install PyMuPDF", file=sys.stderr)
    sys.exit(1)

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("ERROR: google-genai required. pip install google-genai", file=sys.stderr)
    sys.exit(1)

MODEL = 'gemini-2.5-flash'

# Same prompts as htmlgen.py
FORMATTING_RULES = """Return ONLY the content for inside <body> tags — no DOCTYPE, html, head,
or body tags. Do NOT wrap output in markdown code fences.

Formatting:
- <p> for paragraphs
- <em> for italics (common: foreign terms, book titles, emphasis)
- <strong> for bold
- <h2> for section headings (e.g. "Introduction", "Method", "Discussion")
- <blockquote> for block quotes
- <ol>/<ul> for lists if present
- Rejoin hyphenated line breaks (e.g. "existen-\\ntial" → "existential")
- Preserve foreign terms in their original language (German, French, Greek)

Always SKIP: page numbers, running headers ("Existential Analysis: Journal
of..."), running footers, volume/issue identifiers, section-level headings
like "EDITORIAL", "ARTICLES", "BOOK REVIEWS", "Book Review Editorial"."""

ARTICLE_PROMPT = """Convert these journal article pages into clean, well-structured HTML.
This is an academic article from "Existential Analysis: Journal of the Society
for Existential Analysis", a peer-reviewed psychotherapy journal. You are helping
digitise their 30-year print archive for their own journal platform.

""" + FORMATTING_RULES + """

SKIP the article title and author name at the TOP of the first page (already
in OJS metadata). Start from the first paragraph of body text or abstract.

KEEP: Abstract, Keywords, all body text, footnotes/endnotes, References.
Include ALL content through to the very last reference/footnote — do not
stop early."""

BOOK_REVIEW_PROMPT = """Convert this book review into clean, well-structured HTML.
This is from "Existential Analysis: Journal of the Society for Existential
Analysis", a peer-reviewed psychotherapy journal. You are helping digitise
their 30-year print archive for their own journal platform.
The PDF pages may contain multiple reviews — extract ONLY this one.

""" + FORMATTING_RULES + """

This is a BOOK REVIEW, not a regular article. The rules are different:

DO NOT skip any standalone person's name in the text. In book reviews,
a person's name on its own line near the end is the REVIEWER — it is
critical content that MUST appear in the output as
<p><strong>Name</strong></p>. This is NOT an "author byline" to skip.

What to SKIP: only the book's publication details at the very top (title,
author, publisher, year — these are already in OJS metadata).

What to INCLUDE (in order):
  1. All review body paragraphs
  2. The reviewer's name (standalone name near the end)
  3. References section if present after the reviewer's name

BOUNDARIES — the PDF may have multiple reviews on the same pages:
- STARTING: if the top of the first page has text that ends with a
  different person's name followed by this review's book title, skip
  everything before the book title.
- ENDING: after the reviewer's name and any References, STOP. If you
  see another book title/publisher line, that is the NEXT review.
  Do not include it."""


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('GOOGLE_API_KEY=') and not line.startswith('#'):
                    os.environ.setdefault('GOOGLE_API_KEY', line.split('=', 1)[1].strip())


def render_page_to_png(page, dpi=150):
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    return pix.tobytes("png")


def find_fallback_articles():
    """Find all HTML files with AUTO-EXTRACTED marker and their toc.json entries."""
    results = []
    for html_path in sorted(glob.glob('backfill/private/output/*/*.post.html')):
        with open(html_path) as f:
            first_line = f.readline()
        if 'AUTO-EXTRACTED' not in first_line:
            continue

        issue_dir = os.path.dirname(html_path)
        stem = os.path.splitext(os.path.basename(html_path))[0]
        toc_path = os.path.join(issue_dir, 'toc.json')
        if not os.path.exists(toc_path):
            continue

        toc = json.load(open(toc_path))
        for art in toc['articles']:
            sp = art.get('split_pdf', '')
            if sp and os.path.splitext(os.path.basename(sp))[0] == stem:
                pdf_path = os.path.join(issue_dir, os.path.basename(sp))
                if os.path.exists(pdf_path):
                    results.append({
                        'html_path': html_path,
                        'pdf_path': pdf_path,
                        'article': art,
                        'issue_dir': issue_dir,
                    })
                break

    return results


def extract_text_from_pdf(pdf_path):
    """Extract raw text from PDF using PyMuPDF."""
    doc = fitz.open(pdf_path)
    text = ''
    for page in doc:
        page_text = page.get_text()
        if page_text.strip():
            text += page_text + '\n\n'
    doc.close()
    return text


TEXT_FORMAT_PROMPT = """Format this raw text extraction from a journal article into clean HTML.
The text was extracted from a PDF using PyMuPDF and has lost all formatting.
Add appropriate HTML structure based on context clues (capitalisation, line
breaks, indentation patterns):

""" + FORMATTING_RULES + """

SKIP the article title and author name at the very top (already in metadata).
Start from the first paragraph of body text or abstract.

KEEP: Abstract, Keywords, all body text, footnotes/endnotes, References.
Include ALL content — do not stop early.

RAW TEXT:
"""

TEXT_FORMAT_BOOK_REVIEW_PROMPT = """Format this raw text extraction from a book review into clean HTML.
The text was extracted from a PDF using PyMuPDF and has lost all formatting.

""" + FORMATTING_RULES + """

This is a BOOK REVIEW. The rules are different:
- SKIP the book publication details at the top (title, author, publisher — in metadata).
- INCLUDE all review body paragraphs.
- A standalone person's name near the end is the REVIEWER — format as <p><strong>Name</strong></p>.
- Include References if present after the reviewer's name.
- The PDF may contain multiple reviews — extract ONLY this one.

RAW TEXT:
"""


def process_article(client, item):
    """Extract text from PDF, send to Gemini for HTML formatting."""
    art = item['article']
    pdf_path = item['pdf_path']
    section = art.get('section', 'Articles')

    raw_text = extract_text_from_pdf(pdf_path)

    # Choose prompt based on section
    if section == 'Book Reviews':
        prompt = TEXT_FORMAT_BOOK_REVIEW_PROMPT
        if art.get('book_title'):
            prompt += f"\n[Book being reviewed: {art['book_title']}"
            if art.get('book_author'):
                prompt += f" by {art['book_author']}"
            prompt += "]\n\n"
    else:
        prompt = TEXT_FORMAT_PROMPT

    # Call Gemini with text (not images — avoids recitation filter)
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt + raw_text,
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=65536,
        ),
    )

    if not response.text:
        reason = response.candidates[0].finish_reason if response.candidates else 'unknown'
        raise RuntimeError(f"Empty response (finish_reason={reason})")

    html = response.text.strip()

    # Strip markdown code fences if present
    if html.startswith('```html'):
        html = html[7:]
    if html.startswith('```'):
        html = html[3:]
    if html.endswith('```'):
        html = html[:-3]
    html = html.strip()

    return html


def main():
    parser = argparse.ArgumentParser(description='Re-generate HTML for PyMuPDF fallback articles using Gemini')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true', help='List files and estimate cost')
    group.add_argument('--apply', action='store_true', help='Process fallback articles')
    parser.add_argument('--article', type=int, help='Process only this article (1-indexed)')
    args = parser.parse_args()

    load_env()
    if not os.environ.get('GOOGLE_API_KEY'):
        print("ERROR: GOOGLE_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    items = find_fallback_articles()
    if not items:
        print("No PyMuPDF fallback articles found.")
        return

    if args.article:
        if args.article < 1 or args.article > len(items):
            print(f"ERROR: --article must be 1-{len(items)}", file=sys.stderr)
            sys.exit(1)
        items = [items[args.article - 1]]

    total_pages = sum(
        fitz.open(it['pdf_path']).page_count for it in items
    )

    print(f"Fallback articles: {len(items)}")
    print(f"Total pages: {total_pages}")
    # Gemini 2.5 Flash: $0.15/1M input tokens, $0.60/1M output tokens
    est_cost = total_pages * 1600 * 0.15 / 1_000_000 + total_pages * 600 * 0.60 / 1_000_000
    print(f"Estimated cost: ${est_cost:.2f}")

    if args.dry_run:
        print(f"\nArticles:")
        for i, it in enumerate(items):
            art = it['article']
            pages = fitz.open(it['pdf_path']).page_count
            issue = os.path.basename(it['issue_dir'])
            print(f"  [{i+1}] {issue}/{art['title'][:60]} ({pages} pages)")
        return

    # Process
    client = genai.Client(api_key=os.environ['GOOGLE_API_KEY'])
    succeeded = 0
    failed = 0

    for i, item in enumerate(items):
        art = item['article']
        issue = os.path.basename(item['issue_dir'])
        pages = fitz.open(item['pdf_path']).page_count
        print(f"\n[{i+1}/{len(items)}] {issue}/{art['title'][:50]} ({pages} pages)...")

        try:
            html = process_article(client, item)

            if not html or len(html) < 100:
                print(f"  WARNING: Very short output ({len(html)} chars), keeping PyMuPDF version")
                failed += 1
                continue

            # Write output
            with open(item['html_path'], 'w', encoding='utf-8') as f:
                f.write(html + '\n')
            print(f"  OK: {len(html)} chars written")
            succeeded += 1

            # Rate limit: ~15 RPM for free tier
            if i < len(items) - 1:
                time.sleep(4)

        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1
            time.sleep(5)

    print(f"\n{'=' * 60}")
    print(f"Complete: {succeeded} succeeded, {failed} failed out of {len(items)}")


if __name__ == '__main__':
    main()
