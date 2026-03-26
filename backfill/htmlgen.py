#!/usr/bin/env python3
"""
Generate HTML galleys from split article PDFs using Claude Haiku API.

For each article in toc.json with a split_pdf, renders pages as images,
sends to Haiku for structured HTML extraction, saves body-only HTML
next to the split PDF.

Resumable: skips articles that already have an .html file.

Usage:
    python backfill/htmlgen.py backfill/private/output/10.1/toc.json                    # one issue
    python backfill/htmlgen.py backfill/private/output/*/toc.json                       # all issues
    python backfill/htmlgen.py backfill/private/output/10.1/toc.json --dry-run          # cost estimate
    python backfill/htmlgen.py backfill/private/output/10.1/toc.json --article=3        # single article (1-indexed)
    python backfill/htmlgen.py backfill/private/output/10.1/toc.json --workers=5        # concurrent API calls

Requires ANTHROPIC_API_KEY environment variable (or .env file).
"""

import sys
import os
import re
import json
import argparse
import time
import random
import threading
import concurrent.futures
import base64

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF (fitz) required. Install with: pip install PyMuPDF", file=sys.stderr)
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic required. Install with: pip install anthropic", file=sys.stderr)
    sys.exit(1)

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'

# Pricing per million tokens (docs.anthropic.com/en/docs/about-claude/pricing)
MODEL_PRICING = {
    'claude-haiku-4-5-20251001': {'input': 1.00, 'output': 5.00},
    'claude-sonnet-4-5-20250929': {'input': 3.00, 'output': 15.00},
    'claude-sonnet-4-6': {'input': 3.00, 'output': 15.00},
}

# Estimated tokens per page (image input ~1600, HTML output ~600)
EST_INPUT_TOKENS_PER_PAGE = 1600
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

ARTICLE_PROMPT = """Convert these journal article pages into clean, well-structured HTML.

""" + FORMATTING_RULES + """

SKIP the article title and author name at the TOP of the first page (already
in OJS metadata). Start from the first paragraph of body text or abstract.

KEEP: Abstract, Keywords, all body text, footnotes/endnotes, References.
Include ALL content through to the very last reference/footnote — do not
stop early. If references appear in multiple scripts (e.g. Cyrillic then
transliterated Latin), include BOTH sets — they are not duplicates."""

ARTICLE_SHARED_PAGE_PROMPT = """Convert these journal article pages into clean, well-structured HTML.
The first and/or last page may contain content from an adjacent article
(articles run continuously without page breaks). Only extract THIS article.

""" + FORMATTING_RULES + """

SKIP the article title and author name at the TOP of the first page (already
in OJS metadata). Start from the first paragraph of body text or abstract.

If the first page starts with the END of a previous article, skip that
content and start from THIS article's body text.

KEEP: Abstract, Keywords, all body text, footnotes/endnotes, References.
Include ALL content through to the very last reference/footnote. If references
appear in multiple scripts (e.g. Cyrillic then transliterated Latin), include
BOTH sets — they are not duplicates. If the last page contains the START of
the next article, stop before it."""

BOOK_REVIEW_PROMPT = """Convert this book review PDF into clean, well-structured HTML.

CRITICAL: These pages may contain MULTIPLE book reviews running continuously.
You MUST extract ONLY ONE specific review. The first page may START with
text from a DIFFERENT review — if so, SKIP ALL of that text completely.
Look for the target book's title in BOLD to find where YOUR review begins.
After the reviewer's name (and any References), STOP — do not include
the next review.

Sometimes a reviewer writes a shared introduction covering several books
before reviewing each one individually. If you see such an introduction,
SKIP IT — start from where THIS specific book's review begins (usually
after its title/publication details or after the shared intro transitions
to discussing this particular book).

""" + FORMATTING_RULES + """

This is a BOOK REVIEW, not a regular article. The rules are different:

DO NOT skip any standalone person's name in the text. In book reviews,
a person's name on its own line near the end is the REVIEWER — it is
critical content that MUST appear in the output as
<p><strong>Name</strong></p>. This is NOT an "author byline" to skip.

Include EVERYTHING from this review — publication details, epigraphs,
body paragraphs, quotes, reviewer name, references. Do not skip anything.

What to INCLUDE (in order):
  1. The book title, author, year, publisher (publication details)
  2. Any epigraph or quote after the publication details
  3. All review body paragraphs
  4. The reviewer's name (standalone name near the end OF THIS REVIEW)
  5. References section if present after the reviewer's name

IMPORTANT: A name appearing BEFORE this review's book title belongs to
the PREVIOUS review's reviewer — do NOT include it. Only include the
reviewer name that appears AFTER the body of THIS review."""


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


def render_page_to_png(page, dpi=150):
    """Render a PDF page to grayscale PNG bytes."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    return pix.tobytes("png")


def html_output_path(split_pdf_path):
    """Return the .html path for a given split PDF."""
    return os.path.splitext(split_pdf_path)[0] + '.html'


def fallback_html_from_pdf(pdf_path):
    """Extract basic HTML from PDF using PyMuPDF text extraction.

    Used as fallback when Haiku content-filters an article. Output is
    plain paragraphs only (no italics, headings, or semantic markup).
    Marked with a comment so it's distinguishable from Haiku output.
    """
    doc = fitz.open(pdf_path)
    paragraphs = []
    for page in doc:
        text = page.get_text()
        if not text.strip():
            continue
        raw = re.split(r'\n\s*\n', text.strip())
        for para in raw:
            cleaned = re.sub(r'\s*\n\s*', ' ', para.strip())
            if not cleaned:
                continue
            # Skip page numbers
            if re.match(r'^\d{1,3}$', cleaned):
                continue
            # Skip running headers
            if cleaned.startswith('Existential Analysis: Journal of'):
                continue
            paragraphs.append(cleaned)
    doc.close()
    if not paragraphs:
        return None
    from xml.sax.saxutils import escape
    body = '\n'.join(f'<p>{escape(p)}</p>' for p in paragraphs)
    return f'<!-- AUTO-EXTRACTED: PyMuPDF text only (Haiku content-filtered). No formatting. -->\n{body}'


def dedup_paragraphs(html):
    """Remove consecutive duplicate paragraphs/blocks from HTML.

    Haiku occasionally duplicates a paragraph or block when processing
    longer articles. This detects and removes exact consecutive duplicates.
    """
    # Split on blank lines (preserving the structure)
    blocks = re.split(r'\n\n+', html.strip())
    deduped = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if deduped and stripped == deduped[-1].strip():
            continue  # skip consecutive duplicate
        deduped.append(block)
    return '\n\n'.join(deduped)


def strip_code_fences(html):
    """Strip markdown code fences if Haiku wrapped the output."""
    html = html.strip()
    if html.startswith('```'):
        first_newline = html.index('\n')
        html = html[first_newline + 1:]
    if html.endswith('```'):
        html = html[:-3].rstrip()
    return html


def build_prompt(is_book_review=False, has_shared_pages=False, book_title=None, reviewer=None):
    """Select the right prompt for this article type."""
    if is_book_review:
        prompt = BOOK_REVIEW_PROMPT
        if book_title:
            prompt += f'\n\nThe book being reviewed is: "{book_title}"\nExtract ONLY the review of THIS specific book. Ignore content reviewing any other book.'
        if reviewer:
            prompt += f'\n\nThe reviewer is: {reviewer}. Your output MUST end with <p><strong>{reviewer}</strong></p> (or close variant). If it does not, you have extracted the wrong review.'
        return prompt
    elif has_shared_pages:
        return ARTICLE_SHARED_PAGE_PROMPT
    else:
        return ARTICLE_PROMPT


def generate_html_for_article(client, split_pdf_path, model_name=DEFAULT_MODEL, max_retries=8,
                               is_book_review=False, has_shared_pages=False, book_title=None,
                               reviewer=None):
    """Send all pages of a split PDF to Claude, return (html, input_tokens, output_tokens, num_pages, truncated).

    All pages sent in a single message for full article context.
    Retries with exponential backoff on rate limit errors.
    """
    doc = fitz.open(split_pdf_path)
    num_pages = len(doc)

    # Render all pages as PNGs
    content = []
    for page in doc:
        png_bytes = render_page_to_png(page)
        b64 = base64.standard_b64encode(png_bytes).decode('ascii')
        content.append({
            'type': 'image',
            'source': {
                'type': 'base64',
                'media_type': 'image/png',
                'data': b64,
            }
        })
    doc.close()

    content.append({
        'type': 'text',
        'text': build_prompt(is_book_review=is_book_review, has_shared_pages=has_shared_pages, book_title=book_title, reviewer=reviewer),
    })

    for attempt in range(max_retries):
        try:
            # Use streaming to avoid 10-minute timeout on long responses
            html_parts = []
            input_tokens = 0
            output_tokens = 0
            stop_reason = None
            with client.messages.stream(
                model=model_name,
                max_tokens=50000,
                messages=[{
                    'role': 'user',
                    'content': content,
                }]
            ) as stream:
                for text in stream.text_stream:
                    html_parts.append(text)
                response = stream.get_final_message()
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                stop_reason = response.stop_reason
            html = ''.join(html_parts)
            html = strip_code_fences(html)
            html = dedup_paragraphs(html)
            truncated = stop_reason == 'max_tokens'
            return html, input_tokens, output_tokens, num_pages, truncated
        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                wait = 2 ** attempt + 1 + random.uniform(0, 1)
                time.sleep(wait)
            else:
                raise
        except (anthropic.BadRequestError, anthropic.APIStatusError) as e:
            if 'content filtering' in str(e).lower():
                print(f"  FILTERED: {os.path.basename(split_pdf_path)}", file=sys.stderr)
                return None, 0, 0, num_pages, False
            raise


def collect_articles(toc_paths, article_filter=None):
    """Collect all articles from toc.json files. Returns list of (toc_path, article_idx, article)."""
    articles = []
    for toc_path in toc_paths:
        with open(toc_path) as f:
            toc = json.load(f)
        vol = toc.get('volume', '?')
        iss = toc.get('issue', '?')
        all_articles = toc['articles']
        for idx, article in enumerate(all_articles):
            split_pdf = article.get('split_pdf')
            if not split_pdf or not os.path.exists(split_pdf):
                continue
            if article_filter is not None and (idx + 1) != article_filter:
                continue
            # Detect shared pages with neighbors
            has_shared = False
            if idx > 0:
                prev = all_articles[idx - 1]
                if prev.get('pdf_page_end') is not None and article.get('pdf_page_start') is not None:
                    if prev['pdf_page_end'] >= article['pdf_page_start']:
                        has_shared = True
            if idx < len(all_articles) - 1:
                nxt = all_articles[idx + 1]
                if article.get('pdf_page_end') is not None and nxt.get('pdf_page_start') is not None:
                    if article['pdf_page_end'] >= nxt['pdf_page_start']:
                        has_shared = True
            article['_has_shared_pages'] = has_shared
            article['_is_book_review'] = article.get('section', '') in ('Book Reviews', 'Book Review')
            articles.append((toc_path, vol, iss, idx, article))
    return articles


def estimate_cost(articles, model_name=DEFAULT_MODEL):
    """Estimate API cost for generating HTML for the given articles."""
    pricing = MODEL_PRICING.get(model_name, MODEL_PRICING[DEFAULT_MODEL])
    total_pages = 0
    skip_count = 0
    for _, _, _, _, article in articles:
        out_path = html_output_path(article['split_pdf'])
        if os.path.exists(out_path):
            skip_count += 1
            continue
        doc = fitz.open(article['split_pdf'])
        total_pages += len(doc)
        doc.close()

    input_cost = (total_pages * EST_INPUT_TOKENS_PER_PAGE / 1_000_000) * pricing['input']
    output_cost = (total_pages * EST_OUTPUT_TOKENS_PER_PAGE / 1_000_000) * pricing['output']
    return {
        'total_articles': len(articles),
        'skip_existing': skip_count,
        'articles_to_process': len(articles) - skip_count,
        'total_pages': total_pages,
        'est_input_tokens': total_pages * EST_INPUT_TOKENS_PER_PAGE,
        'est_output_tokens': total_pages * EST_OUTPUT_TOKENS_PER_PAGE,
        'est_cost_usd': input_cost + output_cost,
    }


def main():
    load_env()

    parser = argparse.ArgumentParser(description='Generate HTML galleys using Claude API')
    parser.add_argument('toc_json', nargs='+', help='toc.json file(s)')
    parser.add_argument('--dry-run', action='store_true', help='Estimate cost without calling API')
    parser.add_argument('--article', type=int, default=None,
                        help='Process only this article (1-indexed)')
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help=f'Claude model (default: {DEFAULT_MODEL})')
    parser.add_argument('--workers', type=int, default=8,
                        help='Max concurrent API workers (default: 8)')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip cost confirmation prompt')
    args = parser.parse_args()

    # Verify files exist
    for p in args.toc_json:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    articles = collect_articles(args.toc_json, article_filter=args.article)
    if not articles:
        print("No articles with split PDFs found.")
        return

    model_name = args.model
    pricing = MODEL_PRICING.get(model_name, MODEL_PRICING[DEFAULT_MODEL])
    est = estimate_cost(articles, model_name)
    print(f"\nHTML generation estimate:")
    print(f"  toc.json files:    {len(args.toc_json)}")
    print(f"  Total articles:    {est['total_articles']}")
    if est['skip_existing']:
        print(f"  Already done:      {est['skip_existing']}")
    print(f"  Articles to do:    {est['articles_to_process']}")
    print(f"  Total pages:       {est['total_pages']}")
    print(f"  Model:             {model_name}")
    print(f"  Est. tokens:       {est['est_input_tokens'] + est['est_output_tokens']:,}")
    print(f"  Est. cost:         ${est['est_cost_usd']:.2f}")
    print()

    if args.dry_run:
        print("Dry run — no API calls made.")
        return

    if est['articles_to_process'] == 0:
        print("All articles already have HTML files. Nothing to do.")
        return

    if not os.environ.get('ANTHROPIC_API_KEY'):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if not args.yes:
        confirm = input(f"Proceed? Estimated cost: ${est['est_cost_usd']:.2f} [y/N] ")
        if confirm.lower() not in ('y', 'yes'):
            print("Aborted.")
            return

    client = anthropic.Anthropic()
    start_time = time.time()

    total_input = 0
    total_output = 0
    total_pages = 0
    completed = 0
    skipped = 0
    failed = 0
    truncated_articles = []
    filtered_articles = []
    error_articles = []
    lock = threading.Lock()

    # Filter to only articles that need processing
    to_process = []
    manual_skipped = 0
    for item in articles:
        _, _, _, _, article = item
        out_path = html_output_path(article['split_pdf'])
        if article.get('_manual_html') and os.path.exists(out_path):
            manual_skipped += 1
            skipped += 1
            continue
        if os.path.exists(out_path):
            skipped += 1
            continue
        to_process.append(item)
    if manual_skipped:
        print(f"  Skipping {manual_skipped} manually-corrected HTML(s) (delete file to force regeneration)")

    total_to_do = len(to_process)

    def process_article(item):
        nonlocal total_input, total_output, total_pages, completed, failed
        toc_path, vol, iss, idx, article = item
        split_pdf = article['split_pdf']
        out_path = html_output_path(split_pdf)
        basename = os.path.basename(split_pdf)
        label = f"Vol {vol}.{iss} #{idx+1}"

        try:
            reviewer = article.get('reviewer', '') if article.get('_is_book_review') else None
            html, inp_tok, out_tok, num_pages, truncated = generate_html_for_article(
                client, split_pdf, model_name,
                is_book_review=article.get('_is_book_review', False),
                has_shared_pages=article.get('_has_shared_pages', False),
                book_title=article.get('book_title', '') if article.get('_is_book_review') else None,
                reviewer=reviewer)

            if html is None:
                # Content filtered — try PyMuPDF fallback
                fallback = fallback_html_from_pdf(split_pdf)
                if fallback:
                    with open(out_path, 'w', encoding='utf-8') as f:
                        f.write(fallback)
                with lock:
                    failed += 1
                    filtered_articles.append({
                        'vol': vol, 'iss': iss, 'article_idx': idx + 1,
                        'file': basename, 'title': article.get('title', ''),
                        'pages': article.get('split_pages', '?'),
                        'fallback': fallback is not None,
                    })
                    fb = ' (PyMuPDF fallback saved)' if fallback else ' (no fallback)'
                    print(f"  FILTERED {label} ({basename}){fb}", flush=True)
                return

            # Validate reviewer name for book reviews (retry once if wrong)
            if reviewer and html:
                strong_names = re.findall(r'<strong>([^<]+)</strong>', html)
                last_strong = strong_names[-1].strip() if strong_names else ''
                expected = reviewer.strip().lower()
                actual = last_strong.lower()
                if expected not in actual and actual not in expected:
                    print(f"  RETRY {label}: reviewer mismatch (expected '{reviewer}', got '{last_strong}')", flush=True)
                    html2, inp2, out2, _, _ = generate_html_for_article(
                        client, split_pdf, model_name,
                        is_book_review=True, has_shared_pages=article.get('_has_shared_pages', False),
                        book_title=article.get('book_title', ''),
                        reviewer=reviewer)
                    if html2:
                        inp_tok += inp2
                        out_tok += out2
                        strong2 = re.findall(r'<strong>([^<]+)</strong>', html2)
                        last2 = strong2[-1].strip() if strong2 else ''
                        if expected in last2.lower() or last2.lower() in expected:
                            html = html2
                            print(f"  RETRY OK {label}: got '{last2}'", flush=True)
                        else:
                            print(f"  RETRY FAIL {label}: still got '{last2}', keeping first attempt", flush=True)

            # Save HTML body content
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(html)

            with lock:
                total_input += inp_tok
                total_output += out_tok
                total_pages += num_pages
                completed += 1
                cost_so_far = (total_input / 1_000_000 * pricing['input'] +
                               total_output / 1_000_000 * pricing['output'])
                trunc_flag = ' TRUNCATED' if truncated else ''
                print(f"  [{completed}/{total_to_do}] {label} ({basename}) "
                      f"{num_pages}pp ${cost_so_far:.3f}{trunc_flag}", flush=True)
                if truncated:
                    truncated_articles.append(f"{label} ({basename})")

        except Exception as e:
            with lock:
                failed += 1
                error_articles.append({
                    'vol': vol, 'iss': iss, 'article_idx': idx + 1,
                    'file': basename, 'title': article.get('title', ''),
                    'error': str(e),
                })
                print(f"  ERROR {label} ({basename}): {e}", file=sys.stderr, flush=True)

    # Process with thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_article, item) for item in to_process]
        concurrent.futures.wait(futures)

    elapsed = time.time() - start_time
    actual_cost = (total_input / 1_000_000 * pricing['input'] +
                   total_output / 1_000_000 * pricing['output'])

    print(f"\n{'='*50}")
    print(f"Complete!")
    print(f"  Articles:    {completed} done, {skipped} skipped, {failed} failed")
    if filtered_articles:
        print(f"  Filtered:    {len(filtered_articles)}")
    print(f"  Pages:       {total_pages}")
    print(f"  Input:       {total_input:,} tokens")
    print(f"  Output:      {total_output:,} tokens")
    print(f"  Actual cost: ${actual_cost:.3f}")
    print(f"  Time:        {elapsed:.0f}s ({elapsed/60:.1f}m)")
    if truncated_articles:
        print(f"\n  WARNING: {len(truncated_articles)} article(s) truncated (hit max_tokens):")
        for ta in truncated_articles:
            print(f"    - {ta}")
        print(f"  Delete these .html files and re-run to retry.")
    if filtered_articles:
        print(f"\n  Content-filtered articles ({len(filtered_articles)}):")
        for fa in filtered_articles:
            print(f"    - Vol {fa['vol']}.{fa['iss']} #{fa['article_idx']}: "
                  f"{fa['title'][:60]} ({fa['pages']}pp)")

    # Write report JSON (append to existing if present)
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'private', 'output', 'htmlgen-report.json')
    report = {}
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
    # Merge new results into existing report
    for fa in filtered_articles:
        key = f"{fa['vol']}.{fa['iss']}/{fa['file']}"
        report.setdefault('filtered', {})[key] = fa
    for ta in truncated_articles:
        report.setdefault('truncated', [])
        if ta not in report['truncated']:
            report['truncated'].append(ta)
    for ea in error_articles:
        key = f"{ea['vol']}.{ea['iss']}/{ea['file']}"
        report.setdefault('errors', {})[key] = ea
    report['last_run'] = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'completed': completed, 'skipped': skipped, 'failed': failed,
        'filtered': len(filtered_articles),
        'input_tokens': total_input, 'output_tokens': total_output,
        'cost_usd': round(actual_cost, 3),
        'elapsed_seconds': round(elapsed),
    }
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report: {report_path}")
    print(f"{'='*50}")


if __name__ == '__main__':
    main()
