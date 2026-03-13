#!/usr/bin/env python3
"""
Deep enrichment of backfill articles using Claude API.

Extracts subjects, disciplines, themes, thinkers, modalities, methodology,
enriched keywords, summary, references, geographical context, clinical
population, and era focus from each article's full text.

Runs after split + basic human review, before XML generation:
    split-issue.sh (1-4) -> human review -> enrich.py -> generate_xml (5) -> import

Usage:
    # Enrich all issues
    python3 backfill/enrich.py backfill/output/*/toc.json

    # Dry run -- estimate cost without calling API
    python3 backfill/enrich.py backfill/output/*/toc.json --dry-run

    # Force re-enrichment
    python3 backfill/enrich.py backfill/output/EA-vol37-iss1/toc.json --force

    # Model override (default: claude-sonnet-4-20250514)
    python3 backfill/enrich.py backfill/output/*/toc.json --model=claude-opus-4-20250514

    # Concurrency (default: 8 parallel API calls)
    python3 backfill/enrich.py backfill/output/*/toc.json --concurrency=16

    # Vocabulary report -- list all unique values across corpus
    python3 backfill/enrich.py --report backfill/output/*/enrichment.json
"""

import sys
import os
import json
import argparse
import tempfile
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from collections import defaultdict

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


DEFAULT_MODEL = 'claude-sonnet-4-20250514'
DEFAULT_CONCURRENCY = 8

# Cost per million tokens (USD)
MODEL_COSTS = {
    'claude-sonnet-4-20250514': {'input': 3.0, 'output': 15.0},
    'claude-opus-4-20250514': {'input': 15.0, 'output': 75.0},
}

ENRICHMENT_VERSION = 1

# --- Controlled vocabulary (seed terms) ---

SEED_SUBJECTS = [
    "Existential Therapy", "Phenomenology", "Ethics", "Ontology",
    "Existential Philosophy", "Clinical Practice", "Psychopathology",
    "Social Justice", "Education and Training", "Supervision",
    "Research Methods", "Cross-Cultural Practice", "Death and Dying",
    "Spirituality and Religion", "Embodiment", "Relationships and Love",
    "Art and Creativity", "Children and Families", "Addiction", "Trauma",
]

SEED_DISCIPLINES = [
    "Psychotherapy", "Psychology", "Philosophy", "Psychiatry", "Sociology",
    "Counselling", "Nursing", "Social Work", "Education", "Theology",
    "Anthropology", "Political Philosophy", "Neuroscience", "Literature",
    "Art Therapy",
]

SEED_THEMES = [
    "authenticity", "being-in-the-world", "dasein", "anxiety", "freedom",
    "responsibility", "meaning", "meaninglessness", "death", "mortality",
    "finitude", "temporality", "isolation", "loneliness", "relatedness",
    "intersubjectivity", "embodiment", "thrownness", "facticity", "bad faith",
    "the Other", "being-toward-death", "existential guilt", "the uncanny",
    "nothingness", "absurdity", "transcendence", "phenomenological reduction",
    "lifeworld", "intentionality", "dialogue", "encounter", "presence",
    "therapeutic relationship", "four worlds", "Umwelt", "Mitwelt",
    "Eigenwelt", "Uberwelt",
]


def build_system_prompt():
    """Build the system prompt with controlled vocabulary."""
    subjects_list = ', '.join(SEED_SUBJECTS)
    disciplines_list = ', '.join(SEED_DISCIPLINES)
    themes_list = ', '.join(SEED_THEMES)

    return f"""You are a specialist academic librarian cataloguing articles from Existential Analysis, \
the journal of the Society for Existential Analysis. You extract structured metadata for discoverability.

For each article, extract the following fields as JSON:

- subjects: Broad topic areas. Prefer these seed terms when they fit: {subjects_list}. Add new terms only when genuinely distinct, using Title Case.
- disciplines: Academic fields. Prefer these: {disciplines_list}. Add new terms only when genuinely distinct, using Title Case.
- themes: Existential concepts and philosophical themes. Prefer these: {themes_list}. Add new terms only when genuinely distinct, using lowercase.
- thinkers: Philosophers, theorists, or clinicians substantially engaged with (not merely cited). Use standard name forms (e.g. "Heidegger", "Merleau-Ponty", "van Deurzen").
- modalities: Therapeutic approaches discussed (e.g. "Daseinsanalysis", "logotherapy", "existential-integrative therapy"). Use established names.
- methodology: Research method if applicable (e.g. "phenomenological study", "case study", "hermeneutic analysis", "literature review"). Null if not a research article.
- keywords_enriched: A superset of the original keywords plus any additional terms you would suggest for discoverability. Keep the original keywords and add only clearly relevant ones.
- summary: 2-3 sentence synopsis of the article's argument or contribution.
- references: Key works cited or substantially engaged with. For each: author (string), year (integer or null), title (string), internal (boolean -- true if it's a reference to another Existential Analysis article). Limit to the 5-10 most important references.
- geographical_context: Cultural or regional focus if any (e.g. "UK", "South Africa", "East Asia"). Null if not geographically specific.
- clinical_population: Specific groups discussed if any (e.g. "adolescents", "veterans", "people with psychosis"). Null if not population-specific.
- era_focus: Historical period if the article focuses on a specific era (e.g. "19th century", "post-war", "ancient Greek"). Null if contemporary/general.

Respond with a single JSON object. No markdown, no explanation, just the JSON."""


def build_user_prompt(article_meta, full_text):
    """Build the user prompt with article metadata and full text."""
    parts = [
        f"Title: {article_meta.get('title', '')}",
        f"Authors: {article_meta.get('authors', '')}",
        f"Section: {article_meta.get('section', '')}",
    ]
    abstract = article_meta.get('abstract', '')
    if abstract:
        parts.append(f"Abstract: {abstract}")
    keywords = article_meta.get('keywords', [])
    if keywords:
        if isinstance(keywords, list):
            keywords = '; '.join(keywords)
        parts.append(f"Keywords: {keywords}")

    parts.append(f"\n--- Full article text ---\n{full_text}")
    return '\n'.join(parts)


def extract_text_from_pdf(pdf_path):
    """Extract full text from a PDF file using PyMuPDF."""
    if not fitz:
        raise ImportError("PyMuPDF (fitz) is required for text extraction")
    doc = fitz.open(pdf_path)
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return '\n'.join(text_parts)


def estimate_tokens(text):
    """Rough token count estimate (1 token ~ 4 chars for English)."""
    return len(text) // 4


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


def call_claude(client, model, system_prompt, user_prompt, max_retries=3):
    """Call Claude API with retry on 429/500."""
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text
            # Parse JSON from response
            result = json.loads(text)
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            return result, input_tokens, output_tokens
        except (anthropic.RateLimitError, anthropic.InternalServerError) as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  Retry {attempt + 1}/{max_retries} after {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
        except json.JSONDecodeError as e:
            print(f"  WARNING: Failed to parse JSON response: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                raise


# --- Per-issue lock + enrichment state for thread-safe writes ---

class IssueState:
    """Thread-safe enrichment state for a single issue directory."""

    def __init__(self, enrichment_path, enrichment_data):
        self.path = enrichment_path
        self.data = enrichment_data
        self.lock = threading.Lock()

    def store_result(self, review_id, result, model):
        """Thread-safe: store one article's enrichment and flush to disk."""
        with self.lock:
            self.data['articles'][review_id] = result
            self.data['_generated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            self.data['_model'] = model
            self.data['_version'] = ENRICHMENT_VERSION
            write_json_atomic(self.path, self.data)


def prepare_work_items(toc_paths, model, force=False):
    """Scan all toc.json files and return (work_items, issue_states, dry_run_stats).

    work_items: list of dicts ready for enrich_article()
    issue_states: {enrichment_path: IssueState}
    """
    work_items = []
    issue_states = {}
    skipped = 0
    system_prompt = build_system_prompt()

    for toc_path in toc_paths:
        if not os.path.exists(toc_path):
            print(f"WARNING: {toc_path} not found, skipping", file=sys.stderr)
            continue

        with open(toc_path) as f:
            toc = json.load(f)

        issue_dir = os.path.dirname(os.path.abspath(toc_path))
        enrichment_path = os.path.join(issue_dir, 'enrichment.json')

        # Load or create enrichment data
        if os.path.exists(enrichment_path):
            with open(enrichment_path) as f:
                enrichment = json.load(f)
        else:
            enrichment = {
                '_generated': None,
                '_model': model,
                '_version': ENRICHMENT_VERSION,
                'articles': {},
            }

        # Create issue state (one per issue dir)
        if enrichment_path not in issue_states:
            issue_states[enrichment_path] = IssueState(enrichment_path, enrichment)

        vol = toc.get('volume', '?')
        iss = toc.get('issue', '?')

        for article in toc.get('articles', []):
            review_id = article.get('_review_id', '')
            if not review_id:
                continue

            # Skip if already enriched (unless --force)
            if review_id in enrichment.get('articles', {}) and not force:
                skipped += 1
                continue

            pdf_path = article.get('split_pdf', '')
            if not pdf_path or not os.path.exists(pdf_path):
                print(f"  SKIP {review_id}: no PDF at {pdf_path}", file=sys.stderr)
                skipped += 1
                continue

            work_items.append({
                'review_id': review_id,
                'article': article,
                'pdf_path': pdf_path,
                'enrichment_path': enrichment_path,
                'vol': vol,
                'iss': iss,
                'system_prompt': system_prompt,
            })

    return work_items, issue_states, skipped


def enrich_one_article(item, client, model, issue_states):
    """Enrich a single article. Called from thread pool.

    Returns: (review_id, input_tokens, output_tokens, error_msg_or_None)
    """
    review_id = item['review_id']
    article = item['article']
    pdf_path = item['pdf_path']
    system_prompt = item['system_prompt']

    # Extract text (CPU-bound but fast, fine in thread)
    try:
        full_text = extract_text_from_pdf(pdf_path)
    except Exception as e:
        return review_id, 0, 0, f"text extraction failed: {e}"

    user_prompt = build_user_prompt(article, full_text)

    # Call Claude API
    try:
        result, actual_input, actual_output = call_claude(
            client, model, system_prompt, user_prompt)
    except Exception as e:
        return review_id, 0, 0, f"API call failed: {e}"

    # Store result
    result['token_count_input'] = actual_input
    result['token_count_output'] = actual_output
    result['_enriched_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    issue_state = issue_states[item['enrichment_path']]
    issue_state.store_result(review_id, result, model)

    return review_id, actual_input, actual_output, None


def run_enrichment(toc_paths, model, dry_run=False, force=False, concurrency=DEFAULT_CONCURRENCY):
    """Main enrichment loop: prepare work, run concurrently, report results."""
    work_items, issue_states, skipped = prepare_work_items(toc_paths, model, force)

    if not work_items and not dry_run:
        print(f"Nothing to enrich ({skipped} already done).", file=sys.stderr)
        return 0, skipped, 0

    # Dry run: just estimate
    if dry_run:
        system_prompt = build_system_prompt()
        total_input = 0
        total_output = 0
        for item in work_items:
            try:
                full_text = extract_text_from_pdf(item['pdf_path'])
            except Exception:
                continue
            user_prompt = build_user_prompt(item['article'], full_text)
            input_tokens = estimate_tokens(system_prompt + user_prompt)
            est_output = 850
            total_input += input_tokens
            total_output += est_output
            title_short = item['article'].get('title', '')[:40]
            print(f"  ~ {item['review_id']} {title_short} (~{input_tokens} input tokens)",
                  file=sys.stderr)

        costs = MODEL_COSTS.get(model, MODEL_COSTS[DEFAULT_MODEL])
        cost = (total_input * costs['input'] + total_output * costs['output']) / 1_000_000
        print(f"\nDRY RUN: {len(work_items)} articles to enrich, {skipped} already done",
              file=sys.stderr)
        print(f"Estimated: ~{total_input:,} input tokens, ~{total_output:,} output tokens",
              file=sys.stderr)
        print(f"Estimated cost: ~${cost:.2f} ({model})", file=sys.stderr)
        return len(work_items), skipped, 0

    # Real run: concurrent API calls
    if not anthropic:
        print("ERROR: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()
    enriched = 0
    errors = 0
    total_input_tokens = 0
    total_output_tokens = 0
    start_time = time.time()

    # Cap concurrency at work item count
    actual_concurrency = min(concurrency, len(work_items))
    print(f"Enriching {len(work_items)} articles with {actual_concurrency} parallel workers...",
          file=sys.stderr)

    with ThreadPoolExecutor(max_workers=actual_concurrency) as executor:
        futures = {
            executor.submit(enrich_one_article, item, client, model, issue_states): item
            for item in work_items
        }

        for future in as_completed(futures):
            item = futures[future]
            review_id, inp_tok, out_tok, error = future.result()
            title_short = item['article'].get('title', '')[:40]

            if error:
                print(f"  ERROR {review_id}: {error}", file=sys.stderr)
                errors += 1
            else:
                total_input_tokens += inp_tok
                total_output_tokens += out_tok
                enriched += 1
                print(f"  \u2713 {review_id} {title_short} ({inp_tok}\u2192{out_tok} tokens) "
                      f"[{enriched}/{len(work_items)}]", file=sys.stderr)

    elapsed = time.time() - start_time
    costs = MODEL_COSTS.get(model, MODEL_COSTS[DEFAULT_MODEL])
    cost = (total_input_tokens * costs['input'] + total_output_tokens * costs['output']) / 1_000_000
    rate = enriched / elapsed if elapsed > 0 else 0

    print(f"\nDone in {elapsed:.0f}s ({rate:.1f} articles/sec)", file=sys.stderr)
    print(f"Total: {enriched} enriched, {skipped} skipped, {errors} errors", file=sys.stderr)
    print(f"Tokens: {total_input_tokens:,} input, {total_output_tokens:,} output", file=sys.stderr)
    print(f"Cost: ~${cost:.2f} ({model})", file=sys.stderr)

    return enriched, skipped, errors


def do_report(enrichment_paths):
    """Generate a vocabulary report across all enrichment files."""
    field_values = defaultdict(lambda: defaultdict(int))

    for path in enrichment_paths:
        if not os.path.exists(path):
            print(f"WARNING: {path} not found, skipping", file=sys.stderr)
            continue
        with open(path) as f:
            data = json.load(f)
        for review_id, article in data.get('articles', {}).items():
            for field in ['subjects', 'disciplines', 'themes', 'thinkers', 'modalities']:
                for value in article.get(field, []):
                    field_values[field][value] += 1
            methodology = article.get('methodology')
            if methodology:
                field_values['methodology'][methodology] += 1

    for field in ['subjects', 'disciplines', 'themes', 'thinkers', 'modalities', 'methodology']:
        values = field_values[field]
        if not values:
            continue
        print(f"\n{'='*60}")
        print(f"{field.upper()} ({len(values)} unique values)")
        print(f"{'='*60}")
        for value, count in sorted(values.items(), key=lambda x: (-x[1], x[0])):
            print(f"  {count:4d}  {value}")

    # Flag near-duplicates
    print(f"\n{'='*60}")
    print("POTENTIAL DUPLICATES")
    print(f"{'='*60}")
    found_dupes = False
    for field in ['subjects', 'disciplines', 'themes', 'thinkers', 'modalities']:
        values = list(field_values[field].keys())
        for i, v1 in enumerate(values):
            for v2 in values[i+1:]:
                if v1.lower() == v2.lower() and v1 != v2:
                    print(f"  {field}: '{v1}' vs '{v2}'")
                    found_dupes = True
    if not found_dupes:
        print("  None found")


def main():
    parser = argparse.ArgumentParser(description='Deep enrichment of backfill articles via Claude API')
    parser.add_argument('files', nargs='*', help='toc.json files to enrich, or enrichment.json files for --report')
    parser.add_argument('--dry-run', action='store_true',
                        help='Estimate cost without calling API')
    parser.add_argument('--force', action='store_true',
                        help='Re-enrich articles that already have enrichment data')
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help=f'Claude model to use (default: {DEFAULT_MODEL})')
    parser.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY,
                        help=f'Number of parallel API calls (default: {DEFAULT_CONCURRENCY})')
    parser.add_argument('--report', action='store_true',
                        help='Generate vocabulary report from enrichment.json files')
    args = parser.parse_args()

    if args.report:
        if not args.files:
            print("Usage: python3 backfill/enrich.py --report backfill/output/*/enrichment.json",
                  file=sys.stderr)
            sys.exit(1)
        do_report(args.files)
        return

    if not args.files:
        print("Usage: python3 backfill/enrich.py backfill/output/*/toc.json [--dry-run] [--force]",
              file=sys.stderr)
        sys.exit(1)

    enriched, skipped, errors = run_enrichment(
        args.files, args.model, dry_run=args.dry_run, force=args.force,
        concurrency=args.concurrency)

    if errors:
        sys.exit(1)


if __name__ == '__main__':
    main()
