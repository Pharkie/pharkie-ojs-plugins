#!/usr/bin/env python3
"""
Generate HTML galleys from split article PDFs using Claude Haiku API.

For each article in toc.json with a split_pdf, renders pages as images,
sends to Haiku for structured HTML extraction, saves body-only HTML
next to the split PDF.

Resumable: skips articles that already have an .html file.

Usage:
    python backfill/html_pipeline/pipe1_haiku_html.py backfill/private/output/10.1/toc.json                    # one issue
    python backfill/html_pipeline/pipe1_haiku_html.py backfill/private/output/*/toc.json                       # all issues
    python backfill/html_pipeline/pipe1_haiku_html.py backfill/private/output/10.1/toc.json --dry-run          # cost estimate
    python backfill/html_pipeline/pipe1_haiku_html.py backfill/private/output/10.1/toc.json --article=3        # single article (1-indexed)
    python backfill/html_pipeline/pipe1_haiku_html.py backfill/private/output/*/toc.json --overwrite           # regenerate all, even existing
    python backfill/html_pipeline/pipe1_haiku_html.py backfill/private/output/*/toc.json --from-list regen.txt # only articles listed in file
    python backfill/html_pipeline/pipe1_haiku_html.py backfill/private/output/10.1/toc.json --workers=5        # concurrent API calls

--from-list file format: one article per line, format "vol.iss/seq" (0-padded seq).
Examples: "36.2/02", "1/04", "10.1/13". Lines starting with # are ignored.
Single-issue volumes (1-5) use just the volume number: "1/04" not "1.1/04".

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.postprocess import postprocess_article, verify_postprocessed
from split_pipeline.split2_split_pdf import title_in_split_pdf
from lib.pdf_utils import (extract_pdf_back_matter, build_back_matter_prompt,
                           BACK_MATTER_HEADINGS, NOTES_HEADINGS, RUNNING_TEXT_RE)

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
PROMPT_VERSION = 5  # Bump when prompt changes; tracked in toc.json _html_prompt_version

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
Include EVERYTHING you see on these pages. Do not skip anything.

""" + FORMATTING_RULES + """

Include ALL of the following — title, authors, abstract, keywords, body
paragraphs, section headings, block quotes, footnotes, endnotes,
references, bibliography, author bio, contact details, ORCID.

If references appear in multiple scripts (e.g. Cyrillic then transliterated
Latin), include BOTH sets — they are not duplicates."""

EXTRACTION_PROMPT = ARTICLE_PROMPT  # Same prompt for all article types including book reviews


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
    """Return the .post.html path for a given split PDF."""
    return os.path.splitext(split_pdf_path)[0] + '.post.html'


def raw_output_path(split_pdf_path):
    """Return the .raw.html path for a given split PDF."""
    return os.path.splitext(split_pdf_path)[0] + '.raw.html'


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


def build_prompt(back_matter_sections=None):
    """Return the extraction prompt with optional back-matter appendix.

    Haiku extracts everything — all content decisions (trimming title,
    abstract, keywords, start/end bleed) happen in post-processing.
    """
    prompt = ARTICLE_PROMPT
    if back_matter_sections:
        prompt += build_back_matter_prompt(back_matter_sections)
    return prompt


MAX_CONTINUATIONS = 5  # Max continuation requests when output is truncated


def generate_html_for_article(client, split_pdf_path, model_name=DEFAULT_MODEL, max_retries=8,
                              article=None):
    """Send all pages of a split PDF to Claude for full text extraction.

    Returns (html, input_tokens, output_tokens, num_pages, truncated).
    Haiku extracts EVERYTHING — post-processing handles trimming.
    Retries with exponential backoff on rate limit errors.
    When the model hits max_tokens, sends continuation requests with the
    assistant turn prefilled so the model picks up where it left off.
    """
    # Pre-extract back matter from PDF text to help Haiku
    title = article.get('title') if article else None
    authors = article.get('authors') if article else None
    back_matter = extract_pdf_back_matter(split_pdf_path, title=title, authors=authors)

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
        'text': build_prompt(back_matter),
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

            # Continuation loop: when output is truncated, prefill the
            # assistant turn with what we have and ask to continue.
            continuations = 0
            while stop_reason == 'max_tokens' and continuations < MAX_CONTINUATIONS:
                continuations += 1
                html_so_far = ''.join(html_parts)
                print(f"    Continuation {continuations} "
                      f"({output_tokens:,} tokens so far)...",
                      file=sys.stderr)
                with client.messages.stream(
                    model=model_name,
                    max_tokens=50000,
                    messages=[
                        {'role': 'user', 'content': content},
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

            truncated = stop_reason == 'max_tokens'
            return html, input_tokens, output_tokens, num_pages, truncated, continuations
        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                wait = 2 ** attempt + 1 + random.uniform(0, 1)
                time.sleep(wait)
            else:
                raise
        except (anthropic.BadRequestError, anthropic.APIStatusError) as e:
            if 'content filtering' in str(e).lower():
                print(f"  FILTERED: {os.path.basename(split_pdf_path)}", file=sys.stderr)
                return None, 0, 0, num_pages, False, 0
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
            # Prev/next article titles for boundary context
            if idx > 0:
                article['_prev_title'] = all_articles[idx - 1].get('title', '')
            if idx < len(all_articles) - 1:
                nxt = all_articles[idx + 1]
                article['_next_title'] = nxt.get('title', '')
                article['_next_page_start'] = nxt.get('pdf_page_start')
                article['_next_page_end'] = nxt.get('pdf_page_end')
            articles.append((toc_path, vol, iss, idx, article))
    return articles


def estimate_cost(articles, model_name=DEFAULT_MODEL, overwrite=False):
    """Estimate API cost for generating HTML for the given articles."""
    pricing = MODEL_PRICING.get(model_name, MODEL_PRICING[DEFAULT_MODEL])
    total_pages = 0
    skip_count = 0
    for _, _, _, _, article in articles:
        raw_path = raw_output_path(article['split_pdf'])
        if not overwrite and os.path.exists(raw_path):
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
    parser.add_argument('--overwrite', action='store_true',
                        help='Regenerate even if HTML exists (still skips _manual_html)')
    parser.add_argument('--from-list', metavar='FILE',
                        help='Read article list from file (one per line: vol.iss/seq, e.g. 36.2/02)')
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
    est = estimate_cost(articles, model_name, overwrite=args.overwrite)
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
    completed_articles = []  # (toc_path, idx) for prompt version tracking
    bad_split_articles = []  # (toc_path, idx) for batch toc.json update
    lock = threading.Lock()

    # Parse --from-list filter if provided
    from_list_filter = None
    if args.from_list:
        with open(args.from_list) as f:
            from_list_filter = set()
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # Accept "vol.iss/seq" (e.g. "36.2/02") or just "vol.iss/seq"
                from_list_filter.add(line)

    # Filter to only articles that need processing
    to_process = []
    manual_skipped = 0
    for item in articles:
        _, vol, iss, idx, article = item
        out_path = html_output_path(article['split_pdf'])

        # Always skip _manual_html
        if article.get('_manual_html') and os.path.exists(out_path):
            manual_skipped += 1
            skipped += 1
            continue

        # --from-list filter: only process articles in the list
        if from_list_filter is not None:
            dir_name = str(vol) if isinstance(vol, int) and vol <= 5 and iss == 1 else f"{vol}.{iss}"
            key = f"{dir_name}/{idx + 1:02d}"
            if key not in from_list_filter:
                skipped += 1
                continue

        # Skip if raw extraction already done (check .raw.html, not .html)
        raw_path = raw_output_path(article['split_pdf'])
        if not args.overwrite and os.path.exists(raw_path):
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
            # Deterministic bad-split check (free, no API call)
            if not title_in_split_pdf(split_pdf, article.get('title', '')):
                print(f"  ⚠ BAD_SPLIT {label}: title not found on page 1 of split PDF — skipping", flush=True)
                with lock:
                    bad_split_articles.append((toc_path, idx))
                    failed += 1
                return

            html, inp_tok, out_tok, num_pages, truncated, continuations = generate_html_for_article(
                client, split_pdf, model_name, article=article)

            if html is None:
                # Content filtered — try PyMuPDF fallback
                fallback = fallback_html_from_pdf(split_pdf)
                raw_path = raw_output_path(split_pdf)
                if fallback:
                    with open(raw_path, 'w', encoding='utf-8') as f:
                        f.write(fallback)
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

            # Save raw HTML (full extraction, before any trimming)
            raw_path = raw_output_path(split_pdf)
            with open(raw_path, 'w', encoding='utf-8') as f:
                f.write(html)

            # Post-processing: deterministic trimming
            # (lib/postprocess.py handles title/abstract/keywords/bleed stripping)
            raw_html = html
            html = postprocess_article(raw_html, article, split_pdf)

            # Verify post-processing output
            pp_warnings = verify_postprocessed(raw_html, html, article)
            if pp_warnings:
                for w in pp_warnings:
                    print(f"  ⚠ {label}: {w}", flush=True)

            # Save final trimmed HTML
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(html)

            with lock:
                total_input += inp_tok
                total_output += out_tok
                total_pages += num_pages
                completed += 1
                completed_articles.append((toc_path, idx, continuations, truncated))
                cost_so_far = (total_input / 1_000_000 * pricing['input'] +
                               total_output / 1_000_000 * pricing['output'])
                cont_flag = f' ({continuations} continuations)' if continuations else ''
                trunc_flag = ' TRUNCATED' if truncated else ''
                print(f"  [{completed}/{total_to_do}] {label} ({basename}) "
                      f"{num_pages}pp ${cost_so_far:.3f}{cont_flag}{trunc_flag}", flush=True)
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

    # Batch-update toc.json with _bad_split flags (thread-safe: written after pool completes)
    bad_split_updates = {}  # toc_path -> set of article indices
    for toc_path, idx in bad_split_articles:
        bad_split_updates.setdefault(toc_path, set()).add(idx)
    for toc_path, indices in bad_split_updates.items():
        with open(toc_path) as f:
            toc_data = json.load(f)
        for idx in indices:
            toc_data['articles'][idx]['_bad_split'] = True
        with open(toc_path, 'w') as f:
            json.dump(toc_data, f, indent=2, ensure_ascii=False)

    # Batch-update toc.json with prompt version for completed articles
    toc_updates = {}  # toc_path -> {idx: (continuations, truncated)}
    for toc_path, idx, cont, trunc in completed_articles:
        toc_updates.setdefault(toc_path, {})[idx] = (cont, trunc)
    for toc_path, articles in toc_updates.items():
        with open(toc_path) as f:
            toc_data = json.load(f)
        for idx, (cont, trunc) in articles.items():
            toc_data['articles'][idx]['_html_prompt_version'] = PROMPT_VERSION
            if cont > 0:
                toc_data['articles'][idx]['_continuations'] = cont
            elif '_continuations' in toc_data['articles'][idx]:
                del toc_data['articles'][idx]['_continuations']
            if trunc:
                toc_data['articles'][idx]['_truncated'] = True
            elif '_truncated' in toc_data['articles'][idx]:
                del toc_data['articles'][idx]['_truncated']
        with open(toc_path, 'w') as f:
            json.dump(toc_data, f, indent=2, ensure_ascii=False)

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
