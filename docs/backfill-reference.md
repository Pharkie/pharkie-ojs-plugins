# Backfill Reference

Technical reference for the backfill pipeline. For the process overview and operator guide, see [Backfill Pipeline](backfill-pipeline.md).

---

## Output structure

After `split-issue.sh` completes, each issue gets a directory under `backfill/output/`:

```
backfill/output/EA-vol37-iss1/
    toc.json                          # Structured TOC with all metadata
    import.xml                        # OJS Native XML (large, base64 PDFs)
    01-editorial.pdf                  # Per-article PDFs
    02-therapy-for-the-revolution.pdf
    03-all-those-useless-passions.pdf
    ...
    15-book-review-editorial.pdf
    16-book-review-why-in-the-world-not.pdf
```

### toc.json schema

Top-level fields:

| Field | Description |
|---|---|
| `source_pdf` | Absolute path to the original issue PDF |
| `volume`, `issue` | Extracted from cover page |
| `date` | Publication date (e.g. "January 2026") |
| `page_offset` | Mapping: `pdf_index = journal_page + offset` |
| `total_pdf_pages` | Page count of source PDF |
| `articles[]` | Array of article objects (see below) |

Each article object:

| Field | Description |
|---|---|
| `title` | Article title |
| `authors` | Normalized author string (ampersand-separated) |
| `authors_original` | Pre-normalization name (if changed) |
| `section` | One of the four OJS sections |
| `journal_page_start/end` | Printed page numbers |
| `pdf_page_start/end` | 0-based PDF page indices |
| `abstract` | Extracted abstract text (articles only) |
| `keywords` | List of extracted keywords (articles only) |
| `split_pdf` | Path to the individual article PDF |
| `split_pages` | Page count of the split PDF |
| `_review_id` | Stable ID for human review matching (e.g. `v37i1a0`) |

Book review articles also have `book_title`, `book_author`, `book_year`, `publisher`, and `reviewer`.

---

## Author registry

`backfill/authors.json` is a persistent registry that maps canonical author names to known variants. It is checked into git and grows as you process issues.

Structure:

```json
{
  "Emmy van Deurzen": {
    "variants": ["Emmy Van Deurzen", "E. van Deurzen"],
    "articles": 12
  }
}
```

### Matching strategy

1. **Exact match** -- after normalizing (strip accents, lowercase, collapse whitespace)
2. **Surname + first initial** -- matches "E. van Deurzen" to "Emmy van Deurzen" if surname and first initial agree and there is exactly one candidate
3. **Fuzzy match** -- SequenceMatcher similarity >= 0.85 (catches typos)
4. **Ambiguous** -- multiple candidates with same surname + initial. Written to `backfill/authors-review.json` for human review.
5. **New** -- no match found. Added to registry as a new canonical entry.

### Review file

When the normalizer encounters ambiguous matches, it writes them to `backfill/authors-review.json` with the raw name, candidate matches, article title, and source issue. Resolve these manually, then re-run normalization.

### Registry commands

```bash
# Show stats
python3 backfill/author_normalize.py --stats

# List all authors
python3 backfill/author_normalize.py --list

# Process all issues at once
python3 backfill/author_normalize.py backfill/output/*/toc.json
```

---

## OJS sections

The pipeline maps articles to four OJS sections with specific paywall settings:

| Section | Ref | Paywalled | Peer reviewed | Abstracts required |
|---|---|---|---|---|
| Editorial | `ED` | No (open) | No | No |
| Articles | `ART` | Yes | Yes | Yes |
| Book Review Editorial | `bookeditorial` | No (open) | No | No |
| Book Reviews | `BR` | Yes | No | No |

Classification rules:

| TOC title | Section |
|---|---|
| `Editorial` | Editorial |
| `Obituary` / `Obituary: ...` | Editorial |
| `Erratum` / `Errata` | Editorial |
| `Contributors` / `Notes on Contributors` | Editorial |
| `Correspondence` / `Letters` | Articles |
| `Book Reviews` | Book Review Editorial |
| Individual book reviews (detected by publication lines) | Book Reviews |
| Everything else | Articles |

Unknown short titles (≤2 words) are classified as Articles with a warning.

---

## DOI preservation

Articles that already have DOIs registered at Crossref (prefix `10.65828`) need to keep those DOIs when reimported into a fresh OJS instance. The pipeline handles this automatically.

### How it works

1. **Registry file** (`backfill/doi-registry.json`): Contains all DOIs fetched from the Crossref API, with title, volume, issue, and author metadata. Also includes manual `aliases` for articles where the TOC title differs significantly from the Crossref title.

2. **Lookup** (`generate_xml.py`): For each article, the generator tries to match against the registry using a fuzzy matching chain:
   - Manual aliases (TOC title → Crossref title)
   - Exact normalized title match
   - Strip "Book Review:" or "Obituary:" prefix and retry
   - Prefix match (TOC title includes subtitle that Crossref omits)
   - Editorial naming variants ("Editorial" vs "37.1 editorial")
   - "Book Reviews" → "book reviews editorial"

3. **XML output**: Matched DOIs are emitted as `<id type="doi" advice="update">` inside the `<publication>` element, which tells OJS to store (not ignore) the DOI.

### Updating the registry

To refresh the registry from Crossref:

```bash
curl -s "https://api.crossref.org/prefixes/10.65828/works?rows=100" | \
  python3 -c "import sys, json; ..." > backfill/doi-registry.json
```

The `aliases` section is preserved manually for titles that can't be matched automatically.

---

## Review pipeline internals

### Validation checks

The review import checks all of these **before writing anything**. If any check fails, no files are touched.

| Check | What it catches |
|---|---|
| Missing `id` column | CSV was exported before review IDs existed -- re-export |
| Empty ID | An `id` cell was accidentally cleared |
| Unknown ID | ID in CSV doesn't exist in toc.json (typo or wrong file) |
| Missing article | A toc.json article has no matching CSV row (row deleted) |
| Duplicate ID | Two CSV rows have the same ID (row duplicated) |
| Missing toc.json | A file path in the CSV doesn't exist on disk |
| Invalid section | Section value isn't one of the four valid options |

### Automatic transformations

Applied silently, reported in `--dry-run`:

| Transformation | Why |
|---|---|
| Newlines replaced with spaces | Google Sheets allows Ctrl+Enter; newlines break XML generation |
| Control characters removed | Copy-paste from PDFs can introduce invisible chars that produce invalid XML |

### Testing

```bash
python3 backfill/test_review.py
```

14 automated tests covering validation, round-trips, sanitization, dry-run, restore, and idempotency.

---

## Flags reference

### split-issue.sh

| Flag | Description |
|---|---|
| `--no-pdfs` | Generate XML without base64-embedded PDFs. Fast, useful for testing XML structure. |
| `--only=<step>` | Run a single step only. Valid steps: `preflight`, `parse_toc`, `split`, `verify_split`, `normalize`, `generate_xml`. |
| `--page-offset=N` | Manual page offset for TOC parsing (`pdf_index = journal_page + N`). Use when auto-detection fails. |

### import.sh

| Flag | Default | Description |
|---|---|---|
| `--container=<name>` | Auto-detected | Docker container name for OJS. |
| `--journal=<path>` | `ea` | OJS journal path (URL path component). |
| `--admin=<user>` | `admin` | OJS admin username for the import. |
| `--force` | Off | Reimport issues that already exist in OJS. |

### export_review.py

| Argument | Default | Description |
|---|---|---|
| `toc.json files` | (required) | One or more toc.json files to export |
| `-o`, `--output` | `review.csv` | Output CSV path |

### import_review.py

| Flag | Description |
|---|---|
| `csv_file` | (required) The reviewed CSV file |
| `--dry-run` | Run all validation and show what would change, without writing files |
| `--restore` | Restore toc.json files from `.pre-review` backups |

### verify.py

| Flag | Description |
|---|---|
| `--docker` | Auto-detect OJS Docker container for DB queries. |
| `--container=<name>` | Specify Docker container name explicitly. |
| `--db-host`, `--db-port`, `--db-name`, `--db-user`, `--db-pass` | Direct DB connection (non-Docker). Defaults: `127.0.0.1`, `3306`, `ojs`, `ojs`, `ojs`. |

---

## Running individual steps

Each Python script can be run standalone. This is useful for debugging a specific step or reprocessing after manual `toc.json` edits.

```bash
# Preflight only
python3 backfill/preflight.py path/to/issue.pdf

# Parse TOC, write to specific file
python3 backfill/parse_toc.py path/to/issue.pdf -o backfill/output/EA-vol37-iss1/toc.json

# Parse TOC with manual page offset
python3 backfill/parse_toc.py path/to/issue.pdf -o toc.json --page-offset=2

# Parse TOC without per-article metadata extraction (faster)
python3 backfill/parse_toc.py path/to/issue.pdf --no-metadata -o toc.json

# Split PDF using existing toc.json
python3 backfill/split.py backfill/output/EA-vol37-iss1/toc.json -o backfill/output

# Verify split PDFs match their TOC titles
python3 backfill/verify_split.py backfill/output/EA-vol37-iss1/toc.json

# Normalize authors across all processed issues
python3 backfill/author_normalize.py backfill/output/*/toc.json

# Export metadata for review
python3 backfill/export_review.py backfill/output/*/toc.json -o review.csv

# Preview review corrections (dry run)
python3 backfill/import_review.py review.csv --dry-run

# Apply review corrections
python3 backfill/import_review.py review.csv

# Undo review corrections
python3 backfill/import_review.py review.csv --restore

# Generate XML without PDFs (fast, for testing)
python3 backfill/generate_xml.py backfill/output/EA-vol37-iss1/toc.json -o import.xml --no-pdfs

# Generate XML with embedded PDFs
python3 backfill/generate_xml.py backfill/output/EA-vol37-iss1/toc.json -o import.xml

# Verify import against OJS database
python3 backfill/verify.py backfill/output/EA-vol37-iss1/toc.json --docker
```

To re-run a single step via `split-issue.sh` (uses the same orchestration logic but skips other steps):

```bash
backfill/split-issue.sh path/to/issue.pdf --only=normalize
backfill/split-issue.sh path/to/issue.pdf --only=generate_xml
```
