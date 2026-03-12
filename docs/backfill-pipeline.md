# Backfill Pipeline

Converts whole-issue PDFs of Existential Analysis into per-article PDFs and OJS Native XML for import. Designed for backfilling the journal archive -- 30+ years of issues that predate the OJS installation.

Three-step workflow:

1. **`split-issue.sh`** -- validates the PDF, parses the table of contents, splits into individual article PDFs, verifies splits match TOC titles, and normalizes author names. Does not touch OJS.
2. **Human review** -- export metadata to CSV, correct titles/authors/abstracts/sections/keywords in Google Sheets, import corrections back. See [Human review](#4b-human-review-export_reviewpy--import_reviewpy) below.
3. **`import.sh`** -- generates OJS Native XML and loads it into OJS via the Native Import/Export CLI. Checks for existing issues to prevent duplicates.

---

## Quick start

Process a single issue:

```bash
# Step 1: Split the issue PDF into articles
backfill/split-issue.sh path/to/EA-vol37-iss1.pdf

# Step 2: Review and correct metadata (see Human review section)
python3 backfill/export_review.py backfill/output/EA-vol37-iss1/toc.json -o review.csv
# ... edit review.csv in Google Sheets ...
python3 backfill/import_review.py review.csv --dry-run
python3 backfill/import_review.py review.csv

# Step 3: Generate XML and import into OJS
backfill/split-issue.sh path/to/EA-vol37-iss1.pdf --only=generate_xml
backfill/import.sh backfill/output/EA-vol37-iss1

# Step 4: Verify the import
backfill/verify.py backfill/output/EA-vol37-iss1/toc.json --docker
```

Process all PDFs in a folder:

```bash
backfill/split-issue.sh /path/to/pdf-folder
python3 backfill/export_review.py backfill/output/*/toc.json -o review.csv
# ... review in Sheets, import corrections ...
backfill/split-issue.sh /path/to/pdf-folder --only=generate_xml
backfill/import.sh backfill/output/EA-vol*
```

---

## Pipeline steps

`split-issue.sh` runs six steps in sequence. Each step is a standalone Python script.

### 1. Preflight (`preflight.py`)

Validates the source PDF before processing.

- **Input:** Issue PDF
- **Output:** JSON report to stdout (errors/warnings)
- **Checks:** PDF readable and not corrupted, text extractable (not scanned images), page count plausible (20--400), TOC page (`CONTENTS` heading) detectable, volume/issue number extractable from cover
- **Fails the issue if:** PDF is unreadable or has too little extractable text

```bash
python3 backfill/preflight.py path/to/issue.pdf
```

### 2. Parse TOC (`parse_toc.py`)

Extracts the table of contents and per-article metadata.

- **Input:** Issue PDF
- **Output:** `toc.json` with article titles, authors, page ranges, sections, abstracts, and keywords
- **How it works:** Finds the `CONTENTS` page, parses the PyMuPDF text layout (tab-delimited titles, bare page numbers, trailing author names), computes the PDF page offset (journal page numbers vs. PDF page indices), then visits each article's first pages to extract abstracts and keywords
- **Page offset:** Auto-detected by finding the EDITORIAL heading (journal page 3) or printed page numbers in headers. If both strategies fail, the script errors out with instructions to supply `--page-offset=N` manually.
- **Section classification:** Recognizes `Editorial`, `Book Reviews`, `Obituary`/`Obituary: ...`, `Erratum`/`Errata`, `Correspondence`/`Letters`, `Contributors`/`Notes on Contributors`. Warns on unknown short titles (≤2 words) that might be unrecognized section types.
- **Page range validation:** Overlapping page ranges are auto-corrected (previous article's end is adjusted). Backwards page ranges (end < start) are skipped with a warning.
- **Book reviews:** Detects the "Book Reviews" TOC entry, then scans individual review boundaries within that section by matching publication-line patterns (`Author. (Year). City: Publisher.`) near the top of each page. Extracts reviewer names from the end of each review.
- **Sections assigned:** `Editorial`, `Articles`, `Book Review Editorial`, `Book Reviews` (see OJS sections below)

```bash
python3 backfill/parse_toc.py path/to/issue.pdf -o toc.json

# When auto-detection fails (e.g. no EDITORIAL heading):
python3 backfill/parse_toc.py path/to/issue.pdf -o toc.json --page-offset=2
```

### 3. Split PDF (`split.py`)

Splits the whole-issue PDF into one PDF per article.

- **Input:** `toc.json` (must contain `source_pdf` path and page ranges)
- **Output:** Individual PDFs in the issue output directory, named `01-editorial.pdf`, `02-title-slug.pdf`, etc. Updates `toc.json` with `split_pdf` paths.
- **PDF format:** Output is always clean PDF 1.7 regardless of source PDF version. Uses `garbage=3, deflate=1, clean=1` for optimal output.
- **Skip warnings:** Reports how many articles were skipped due to bad page ranges (start beyond document length, end before start).

```bash
python3 backfill/split.py backfill/output/EA-vol37-iss1/toc.json
```

### 3b. Verify split (`verify_split.py`)

Checks that each split PDF's content matches its TOC title.

- **Input:** `toc.json` (after split.py has added `split_pdf` paths)
- **Output:** Per-article results to stderr: OK (≥60% word match), WARN (30--60%), FAIL (<30%), SKIP (no matchable words, e.g. "Editorial")
- **How it works:** Extracts significant words from each title (stripping prefixes like "Book Review:", filtering stop words), then checks if those words appear in the first 2 pages of the split PDF. Catches page-offset errors where the wrong article is assigned to a title.
- **Exit code:** 1 if any article fails verification.

```bash
python3 backfill/verify_split.py backfill/output/EA-vol37-iss1/toc.json
```

### 4. Normalize authors (`author_normalize.py`)

Resolves author name variants to canonical forms using a persistent registry.

- **Input:** One or more `toc.json` files
- **Output:** Updates `toc.json` in place (adds `authors_original` if changed). Updates `backfill/authors.json` registry. Writes `backfill/authors-review.json` if ambiguous matches need human review.
- **See:** Author registry section below

```bash
python3 backfill/author_normalize.py backfill/output/EA-vol37-iss1/toc.json
```

### 4b. Human review (`export_review.py` + `import_review.py`)

A human review step between splitting and XML generation. The automated TOC parser does a good job, but over 30 years of issues you'll find titles with OCR artefacts, author names split incorrectly, abstracts that captured too much or too little, and articles in the wrong section. This step lets you scan everything in a spreadsheet and fix problems before they're baked into OJS.

This runs once across the entire archive. There's no second pass.

#### How it works

Each article gets a stable ID (like `v37i1a0` -- volume 37, issue 1, article 0). The ID is stored in the toc.json and included in the CSV. Matching on import is by ID, not row position, so you can sort, filter, and rearrange the spreadsheet without breaking anything.

#### Export

```bash
# Single issue
python3 backfill/export_review.py backfill/output/EA-vol37-iss1/toc.json -o review.csv

# All issues at once
python3 backfill/export_review.py backfill/output/*/toc.json -o review.csv
```

Creates a CSV with one row per article. Also assigns a `_review_id` to each article in the toc.json if not already present.

#### Edit in Google Sheets

1. Upload `review.csv` to Google Sheets (File > Import > Upload)
2. Edit the columns you need to correct:
   - **title** -- fix typos, missing words, OCR errors
   - **authors** -- correct names, fix splitting (use `&` between multiple authors)
   - **section** -- must be exactly one of: `Editorial`, `Articles`, `Book Review Editorial`, `Book Reviews`
   - **abstract** -- fix or add abstract text
   - **keywords** -- semicolon-separated (e.g. `phenomenology; therapy; Heidegger`)
3. Don't edit these columns (used for matching):
   - **id** -- the stable article ID
   - **file** -- path to the toc.json
   - **index** -- original position in toc.json
   - **pages** -- page range (informational)
4. Download as CSV (File > Download > Comma-separated values)

**Don't** delete rows (every article must have a row), edit the `id` column, or add rows. The import rejects all of these with a clear error.

#### Preview changes (always do this first)

```bash
python3 backfill/import_review.py review.csv --dry-run
```

Shows every change field by field without writing anything. Also flags automatic transformations (newline removal, control character stripping). Review the output before applying.

#### Import corrections

```bash
python3 backfill/import_review.py review.csv
```

Updates each toc.json in place. A backup is saved as `toc.json.pre-review` before changes are written.

If you changed author names, re-run normalization afterwards:

```bash
python3 backfill/author_normalize.py backfill/output/*/toc.json
```

#### Undo

```bash
python3 backfill/import_review.py review.csv --restore
```

Restores every toc.json from its `.pre-review` backup.

#### What gets validated

The import checks all of these **before writing anything**. If any check fails, no files are touched.

| Check | What it catches |
|---|---|
| Missing `id` column | CSV was exported before review IDs existed -- re-export |
| Empty ID | An `id` cell was accidentally cleared |
| Unknown ID | ID in CSV doesn't exist in toc.json (typo or wrong file) |
| Missing article | A toc.json article has no matching CSV row (row deleted) |
| Duplicate ID | Two CSV rows have the same ID (row duplicated) |
| Missing toc.json | A file path in the CSV doesn't exist on disk |
| Invalid section | Section value isn't one of the four valid options |

Automatic transformations (applied silently, reported in `--dry-run`):

| Transformation | Why |
|---|---|
| Newlines replaced with spaces | Google Sheets allows Ctrl+Enter; newlines break XML generation |
| Control characters removed | Copy-paste from PDFs can introduce invisible chars that produce invalid XML |

#### Testing

```bash
python3 backfill/test_review.py
```

14 automated tests covering validation, round-trips, sanitization, dry-run, restore, and idempotency.

### 5. Generate XML (`generate_xml.py`)

Generates OJS Native XML with base64-embedded PDFs.

- **Input:** `toc.json` (with `split_pdf` paths from step 3)
- **Output:** `import.xml` -- a complete OJS Native XML file containing issue metadata, sections, articles (with authors, abstracts, keywords, copyright), and PDF galleys
- **PDF embedding:** Each article PDF is base64-encoded and embedded inline. A typical issue produces a 20--50 MB XML file. Use `--no-pdfs` to skip embedding for testing XML structure.
- **DOI preservation:** Automatically looks up existing DOIs from `backfill/doi-registry.json` (see DOI preservation section below). Matched DOIs are emitted as `<id type="doi" advice="update">` in the XML, telling OJS to store the DOI on import.

```bash
python3 backfill/generate_xml.py backfill/output/EA-vol37-iss1/toc.json -o import.xml
```

---

## Import

`import.sh` loads generated XML into OJS.

- **Idempotency:** Before importing, queries the OJS database to check if the issue (volume + number) already exists. Skips with a message unless `--force` is passed.
- **Input validation:** Volume and issue number extracted from the XML are validated as numeric before use in DB queries.
- **Auto-detection:** Finds the OJS Docker container automatically (looks for container names matching `*-ojs*`).

```bash
# Import one issue
backfill/import.sh backfill/output/EA-vol37-iss1

# Import all prepared issues
backfill/import.sh backfill/output/EA-vol*

# Re-import an issue that already exists
backfill/import.sh backfill/output/EA-vol37-iss1 --force
```

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

The `toc.json` contains:

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

## Known limitations

- **TOC parser tuned for recent issues.** The `CONTENTS` page format, text extraction patterns, and page offset detection are calibrated against issues from the last ~10 years. Older issues with different layouts may need the `--page-offset=N` flag or manual `toc.json` adjustment.
- **Book review detection is heuristic.** Individual reviews are identified by publication-line patterns near the top of pages (`Author. (Year). City: Publisher.`). Reviews that don't start on a new page, or that use unusual citation formats, may be missed or mis-split. The pipeline validates that detected titles look like headings (short lines, no trailing period) to avoid matching bibliography entries.
- **Keyword extraction edge cases.** Keywords are extracted by finding "Key Words" followed by comma-separated lines. Articles that use different keyword formatting (e.g. semicolons, inline keywords, "Keywords" without space) may not be captured.
- **Abstract extraction relies on section headers.** Abstracts are captured between "Abstract" and the next section heading ("Key Words", "Introduction", or a capitalized heading). Unusual article structures may yield incomplete or missing abstracts.
- **Reviewer name extraction is best-effort.** The pipeline scans backwards from the end of each book review looking for a standalone name line. Long reference sections between the reviewer's name and the page boundary can cause missed extractions.
- **Author email placeholders.** OJS requires an email for every author. The XML uses `firstname.lastname@placeholder.invalid` since historical articles don't have author emails.
- **DOI matching is fuzzy.** The registry lookup handles common differences (subtitles, prefixes, editorial naming) but unusual title variations may need manual aliases in `doi-registry.json`.

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
