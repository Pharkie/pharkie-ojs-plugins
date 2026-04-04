#!/usr/bin/env python3
"""
Re-extract content-filtered articles using Claude with text input.

The original pipe1 sends PDF page images to Haiku, which triggers the
content recitation filter on ~39 articles. This script extracts raw text
with PyMuPDF and sends it to Claude as text — bypassing the image filter.

Writes to .raw.html (replacing the PyMuPDF fallback), so the standard
pipeline (pipe2→pipe6) works normally afterwards.

Usage:
    python3 backfill/html_pipeline/pipe1c_claude_text_html.py --dry-run          # list articles, estimate cost
    python3 backfill/html_pipeline/pipe1c_claude_text_html.py --apply            # process all
    python3 backfill/html_pipeline/pipe1c_claude_text_html.py --apply --article=3  # one article (1-indexed)
    python3 backfill/html_pipeline/pipe1c_claude_text_html.py --apply --model=claude-sonnet-4-5-20250929  # use Sonnet

Requires ANTHROPIC_API_KEY environment variable (or backfill/.env).
"""

import sys
import os
import re
import json
import glob
import argparse
import time
import random
import threading
import concurrent.futures

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF required. pip install PyMuPDF", file=sys.stderr)
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic required. pip install anthropic", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.pdf_utils import extract_pdf_back_matter, build_back_matter_prompt

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'

# Pricing per million tokens
MODEL_PRICING = {
    'claude-haiku-4-5-20251001': {'input': 1.00, 'output': 5.00},
    'claude-sonnet-4-5-20250929': {'input': 3.00, 'output': 15.00},
    'claude-sonnet-4-6': {'input': 3.00, 'output': 15.00},
}

# Estimated tokens per page for text input (~800 input, ~600 output)
EST_INPUT_TOKENS_PER_PAGE = 800
EST_OUTPUT_TOKENS_PER_PAGE = 600

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

TEXT_FORMAT_PROMPT = """Format this raw text extraction from a journal article into clean, well-structured HTML.
The text was extracted from a PDF using PyMuPDF and has lost all formatting.
Add appropriate HTML structure based on context clues (capitalisation, line
breaks, indentation patterns).

""" + FORMATTING_RULES + """

Include ALL of the following — title, authors, abstract, keywords, body
paragraphs, section headings, block quotes, footnotes, endnotes,
references, bibliography, author bio, contact details, ORCID.

If references appear in multiple scripts (e.g. Cyrillic then transliterated
Latin), include BOTH sets — they are not duplicates.

RAW TEXT:
"""

TEXT_FORMAT_BOOK_REVIEW_PROMPT = """Format this raw text extraction from a book review into clean, well-structured HTML.
The text was extracted from a PDF using PyMuPDF and has lost all formatting.

""" + FORMATTING_RULES + """

This is a BOOK REVIEW. The rules are different:
- A standalone person's name near the end is the REVIEWER — format as <p><strong>Name</strong></p>.
- Include References if present after the reviewer's name.

Include ALL of the following — title, authors, all body paragraphs,
reviewer name, references.

RAW TEXT:
"""

MAX_CONTINUATIONS = 5


def load_env():
    """Load ANTHROPIC_API_KEY from .env if not already set."""
    if os.environ.get('ANTHROPIC_API_KEY'):
        return
    # Check backfill/.env and project root .env
    candidates = [
        os.path.join(os.path.dirname(__file__), '..', '.env'),
        os.path.join(os.path.dirname(__file__), '..', '..', '.env'),
    ]
    for env_path in candidates:
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('ANTHROPIC_API_KEY=') and not line.startswith('#'):
                        os.environ['ANTHROPIC_API_KEY'] = line.split('=', 1)[1].strip()
                        return


def strip_code_fences(html):
    """Strip markdown code fences if the model wrapped the output."""
    html = html.strip()
    if html.startswith('```'):
        first_newline = html.index('\n')
        html = html[first_newline + 1:]
    if html.endswith('```'):
        html = html[:-3].rstrip()
    return html


def dedup_paragraphs(html):
    """Remove consecutive duplicate paragraphs/blocks from HTML."""
    blocks = re.split(r'\n\n+', html.strip())
    deduped = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if deduped and stripped == deduped[-1].strip():
            continue
        deduped.append(block)
    return '\n\n'.join(deduped)


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


def find_fallback_articles():
    """Find all .raw.html files with AUTO-EXTRACTED marker and their toc.json entries."""
    results = []
    for raw_path in sorted(glob.glob('backfill/private/output/*/*.raw.html')):
        with open(raw_path) as f:
            first_line = f.readline()
        if 'AUTO-EXTRACTED' not in first_line:
            continue

        issue_dir = os.path.dirname(raw_path)
        # stem: e.g. "08-pain-is-not-pathology" from "08-pain-is-not-pathology.raw.html"
        basename = os.path.basename(raw_path)
        stem = basename.replace('.raw.html', '')

        toc_path = os.path.join(issue_dir, 'toc.json')
        if not os.path.exists(toc_path):
            continue

        with open(toc_path) as f:
            toc = json.load(f)

        for art_idx, art in enumerate(toc['articles']):
            sp = art.get('split_pdf', '')
            if sp and os.path.splitext(os.path.basename(sp))[0] == stem:
                pdf_path = os.path.join(issue_dir, os.path.basename(sp))
                if os.path.exists(pdf_path):
                    results.append({
                        'raw_path': raw_path,
                        'pdf_path': pdf_path,
                        'article': art,
                        'article_idx': art_idx,
                        'issue_dir': issue_dir,
                        'toc_path': toc_path,
                    })
                break

    return results


def process_article(client, item, model_name=DEFAULT_MODEL, max_retries=8):
    """Extract text from PDF, send to Claude for HTML formatting.

    Returns (html, input_tokens, output_tokens) or raises on failure.
    """
    art = item['article']
    pdf_path = item['pdf_path']
    section = art.get('section', 'Articles')
    title = art.get('title', '')
    authors = art.get('authors', '')

    raw_text = extract_text_from_pdf(pdf_path)

    # Pre-extract back matter to help Claude identify structure
    back_matter = extract_pdf_back_matter(pdf_path, title=title, authors=authors)

    # Choose prompt based on section
    if section in ('Book Reviews', 'Book Review'):
        prompt = TEXT_FORMAT_BOOK_REVIEW_PROMPT
        if art.get('book_title'):
            prompt += f"\n[Book being reviewed: {art['book_title']}"
            if art.get('book_author'):
                prompt += f" by {art['book_author']}"
            prompt += "]\n\n"
        else:
            prompt += "\n"
    else:
        prompt = TEXT_FORMAT_PROMPT

    # Append back-matter context if available
    if back_matter:
        prompt += build_back_matter_prompt(back_matter) + "\n\n"

    prompt += raw_text

    for attempt in range(max_retries):
        try:
            html_parts = []
            input_tokens = 0
            output_tokens = 0
            stop_reason = None

            with client.messages.stream(
                model=model_name,
                max_tokens=50000,
                messages=[{'role': 'user', 'content': prompt}]
            ) as stream:
                for text in stream.text_stream:
                    html_parts.append(text)
                response = stream.get_final_message()
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                stop_reason = response.stop_reason

            # Continuation loop for truncated output
            continuations = 0
            while stop_reason == 'max_tokens' and continuations < MAX_CONTINUATIONS:
                continuations += 1
                html_so_far = ''.join(html_parts)
                print(f"    Continuation {continuations} "
                      f"({output_tokens:,} tokens so far)...", file=sys.stderr)
                with client.messages.stream(
                    model=model_name,
                    max_tokens=50000,
                    messages=[
                        {'role': 'user', 'content': prompt},
                        {'role': 'assistant', 'content': html_so_far},
                        {'role': 'user', 'content': 'Continue the HTML exactly from where you stopped. Do not repeat any content already produced.'},
                    ]
                ) as stream:
                    for text in stream.text_stream:
                        html_parts.append(text)
                    response = stream.get_final_message()
                    input_tokens += response.usage.input_tokens
                    output_tokens += response.usage.output_tokens
                    stop_reason = response.stop_reason

            html = ''.join(html_parts)
            html = strip_code_fences(html)
            html = dedup_paragraphs(html)

            if not html or len(html) < 100:
                raise RuntimeError(f"Very short output ({len(html)} chars)")

            return html, input_tokens, output_tokens

        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                wait = 2 ** attempt + 1 + random.uniform(0, 1)
                print(f"    Rate limited, waiting {wait:.0f}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
        except (anthropic.BadRequestError, anthropic.APIStatusError) as e:
            if 'content filtering' in str(e).lower():
                # Even text input got filtered — this model won't work
                return None, 0, 0
            raise


def main():
    parser = argparse.ArgumentParser(
        description='Re-extract content-filtered articles using Claude with text input')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true', help='List articles and estimate cost')
    group.add_argument('--apply', action='store_true', help='Process fallback articles')
    parser.add_argument('--article', type=int, help='Process only this article (1-indexed)')
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help=f'Claude model (default: {DEFAULT_MODEL})')
    parser.add_argument('--workers', type=int, default=8,
                        help='Max concurrent API workers (default: 8)')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip cost confirmation')
    args = parser.parse_args()

    load_env()

    items = find_fallback_articles()
    if not items:
        print("No content-filtered (AUTO-EXTRACTED) articles found.")
        return

    if args.article:
        if args.article < 1 or args.article > len(items):
            print(f"ERROR: --article must be 1-{len(items)}", file=sys.stderr)
            sys.exit(1)
        items = [items[args.article - 1]]

    model_name = args.model
    pricing = MODEL_PRICING.get(model_name, MODEL_PRICING[DEFAULT_MODEL])

    # Calculate totals
    total_pages = 0
    for it in items:
        doc = fitz.open(it['pdf_path'])
        it['_pages'] = doc.page_count
        total_pages += doc.page_count
        doc.close()

    input_cost = (total_pages * EST_INPUT_TOKENS_PER_PAGE / 1_000_000) * pricing['input']
    output_cost = (total_pages * EST_OUTPUT_TOKENS_PER_PAGE / 1_000_000) * pricing['output']
    est_cost = input_cost + output_cost

    print(f"\nContent-filtered article re-extraction:")
    print(f"  Articles:      {len(items)}")
    print(f"  Total pages:   {total_pages}")
    print(f"  Model:         {model_name}")
    print(f"  Est. cost:     ${est_cost:.2f}")
    print()

    if args.dry_run:
        print("Articles:")
        for i, it in enumerate(items):
            art = it['article']
            issue = os.path.basename(it['issue_dir'])
            section = art.get('section', 'Articles')
            sect_tag = ' [BR]' if section in ('Book Reviews', 'Book Review') else ''
            print(f"  [{i+1:2d}] {issue}/{art.get('title', '?')[:55]}{sect_tag} ({it['_pages']} pp)")
        print(f"\nDry run — no API calls made.")
        return

    if not os.environ.get('ANTHROPIC_API_KEY'):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if not args.yes:
        confirm = input(f"Proceed? Estimated cost: ${est_cost:.2f} [y/N] ")
        if confirm.lower() not in ('y', 'yes'):
            print("Aborted.")
            return

    client = anthropic.Anthropic()
    start_time = time.time()

    total_input_tokens = 0
    total_output_tokens = 0
    succeeded = 0
    failed = 0
    filtered = 0
    lock = threading.Lock()
    total_to_do = len(items)

    def process_one(i, item):
        nonlocal total_input_tokens, total_output_tokens, succeeded, failed, filtered
        art = item['article']
        issue = os.path.basename(item['issue_dir'])
        pages = item['_pages']
        label = f"[{i+1}/{total_to_do}] {issue}/{art.get('title', '?')[:50]} ({pages} pp)"

        try:
            html, inp_tok, out_tok = process_article(client, item, model_name)

            if html is None:
                with lock:
                    filtered += 1
                    failed += 1
                print(f"  FILTERED {label} — try --model=claude-sonnet-4-5-20250929",
                      flush=True)
                return

            # Write to .raw.html (replacing PyMuPDF fallback)
            with open(item['raw_path'], 'w', encoding='utf-8') as f:
                f.write(html + '\n')

            with lock:
                total_input_tokens += inp_tok
                total_output_tokens += out_tok
                succeeded += 1
                cost_so_far = (total_input_tokens / 1_000_000 * pricing['input'] +
                               total_output_tokens / 1_000_000 * pricing['output'])
            print(f"  OK {label}: {len(html):,} chars, ${cost_so_far:.3f} total",
                  flush=True)

        except Exception as e:
            with lock:
                failed += 1
            print(f"  ERROR {label}: {e}", file=sys.stderr, flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_one, i, item) for i, item in enumerate(items)]
        concurrent.futures.wait(futures)

    elapsed = time.time() - start_time
    actual_cost = (total_input_tokens / 1_000_000 * pricing['input'] +
                   total_output_tokens / 1_000_000 * pricing['output'])

    print(f"\n{'=' * 60}")
    print(f"Complete!")
    print(f"  Succeeded:     {succeeded}")
    print(f"  Failed:        {failed}")
    if filtered:
        print(f"  Still filtered: {filtered} (try Sonnet)")
    print(f"  Input tokens:  {total_input_tokens:,}")
    print(f"  Output tokens: {total_output_tokens:,}")
    print(f"  Actual cost:   ${actual_cost:.3f}")
    print(f"  Time:          {elapsed:.0f}s ({elapsed/60:.1f}m)")

    if succeeded > 0:
        print(f"\nNext steps:")
        print(f"  1. Run pipe2 post-processing on affected volumes")
        print(f"  2. Continue pipeline: pipe3 → pipe6 → pipe7 → pipe8")
        print(f"  3. Remove _content_filtered from toc.json for succeeded articles")
        print(f"  4. Run pipe9c to clear content-filtered flags in OJS DB")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
