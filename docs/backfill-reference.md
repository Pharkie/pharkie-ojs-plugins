# Backfill Reference

Technical reference for the backfill pipeline. For the process overview and reviewer guide, see [Backfill Pipeline](backfill-pipeline.md).

---

## Common workflows

### Full pipeline (single issue)

```bash
# Step 1: Split the issue PDF into articles
backfill/split_pipeline/split_issue.sh path/to/37.1.pdf

# Step 2: Human review (see Backfill Pipeline for what to check)
# 2a: Check split PDFs -- open backfill/private/output/37.1/*.pdf
#     Verify page alignment, article boundaries, book review splits
# 2b: Review metadata in toc.json directly or via QA Splits

# Step 3: Generate XML and import into OJS
python3 backfill/html_pipeline/pipe6_ojs_xml.py backfill/private/output/37.1/toc.json -o backfill/private/output/37.1/import.xml
sudo bash backfill/html_pipeline/pipe7_import.sh backfill/private/output/37.1

# Step 4: Verify the import
python3 backfill/html_pipeline/pipe10_verify.py backfill/private/output/37.1/toc.json --docker
```

### Batch workflows

```bash
# Normalize authors across all issues
python3 backfill/split_pipeline/split4_normalize_authors.py backfill/private/output/*/toc.json
```

### Re-running after corrections

```bash
# Re-run a single step (e.g. after editing toc.json)
backfill/split_pipeline/split_issue.sh path/to/issue.pdf --only=split
backfill/split_pipeline/split_issue.sh path/to/issue.pdf --only=normalize

# Re-run normalization after changing author names
python3 backfill/split_pipeline/split4_normalize_authors.py backfill/private/output/*/toc.json
```

---

## Output structure

After `split_issue.sh` completes, each issue gets a directory under `backfill/private/output/`:

```
backfill/private/output/37.1/
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

After enrichment review, articles may also have `subjects` (list) and `disciplines` (list) stored directly in toc.json.

---

## Author registry

`backfill/private/authors.json` is a persistent registry that maps canonical author names to known variants. It is checked into git and grows as you process issues.

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
4. **Ambiguous** -- multiple candidates with same surname + initial. Written to `backfill/private/authors-review.json` for human review.
5. **New** -- no match found. Added to registry as a new canonical entry.

### Review file

When the normalizer encounters ambiguous matches, it writes them to `backfill/private/authors-review.json` with the raw name, candidate matches, article title, and source issue. Resolve these manually, then re-run normalization.

### Registry commands

```bash
# Show stats
python3 backfill/split_pipeline/split4_normalize_authors.py --stats

# List all authors
python3 backfill/split_pipeline/split4_normalize_authors.py --list

# Process all issues at once
python3 backfill/split_pipeline/split4_normalize_authors.py backfill/private/output/*/toc.json
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

1. **Registry file** (`backfill/private/doi-registry.json`): Contains all DOIs fetched from the Crossref API, with title, volume, issue, and author metadata. Also includes manual `aliases` for articles where the TOC title differs significantly from the Crossref title.

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
  python3 -c "import sys, json; ..." > backfill/private/doi-registry.json
```

The `aliases` section is preserved manually for titles that can't be matched automatically.

---

## Known limitations

- **Book review detection is heuristic.** Individual reviews are identified by publication-line patterns near the top of pages. Reviews that don't start on a new page, or that use unusual citation formats, may be missed or mis-split.
- **Keyword extraction edge cases.** Keywords are extracted by finding "Key Words", "Keywords", or "Key Word" headings (with or without colons), supporting both comma and semicolon separators. Articles that use other keyword formats (e.g. inline keywords with no heading) may not be captured.
- **Abstract extraction relies on section headers.** Abstracts are captured between "Abstract" (or "Abstract:") and the next section heading ("Key Words", "Keywords", "Introduction", or a capitalised heading). Unusual article structures may yield incomplete or missing abstracts.
- **Reviewer name extraction is best-effort.** The pipeline scans backwards from the end of each book review looking for a standalone name line. Long reference sections can cause missed extractions.
- **Author email placeholders.** OJS requires an email for every author. The XML uses `firstname.lastname@placeholder.invalid` since historical articles don't have author emails.
- **DOI matching is fuzzy.** Unusual title variations may need manual correction in JATS.

---

## Flags reference

### split_issue.sh

| Flag | Description |
|---|---|
| `--only=<step>` | Run a single step only. Valid steps: `preflight`, `split`, `verify_split`, `normalize`. |
| `--stop-after=<step>` | Run through a step then stop. |

### pipe7_import.sh

| Flag | Default | Description |
|---|---|---|
| `--container=<name>` | Auto-detected | Docker container name for OJS. |
| `--journal=<path>` | `ea` | OJS journal path (URL path component). |
| `--admin=<user>` | `admin` | OJS admin username for the import. |
| `--force` | Off | Reimport issues that already exist in OJS. |
| `--wipe-articles` | Off | Wipe ALL existing issues/articles before importing. |

### pipe10_verify.py

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
python3 backfill/split_pipeline/split1_preflight.py path/to/issue.pdf

# Split PDF using existing toc.json
python3 backfill/split_pipeline/split2_split_pdf.py backfill/private/output/37.1/toc.json -o backfill/private/output

# Verify split PDFs match their TOC titles
python3 backfill/split_pipeline/split3_verify.py backfill/private/output/37.1/toc.json

# Normalize authors across all processed issues
python3 backfill/split_pipeline/split4_normalize_authors.py backfill/private/output/*/toc.json

# Generate XML without PDFs (fast, for testing)
python3 backfill/html_pipeline/pipe6_ojs_xml.py backfill/private/output/37.1/toc.json -o import.xml --no-pdfs

# Generate XML with embedded PDFs
python3 backfill/html_pipeline/pipe6_ojs_xml.py backfill/private/output/37.1/toc.json -o import.xml

# Verify import against OJS database
python3 backfill/html_pipeline/pipe10_verify.py backfill/private/output/37.1/toc.json --docker
```

To re-run a single step via `split_issue.sh` (uses the same orchestration logic but skips other steps):

```bash
backfill/split_pipeline/split_issue.sh path/to/issue.pdf --only=normalize
```

---

## Fixing a bad split or HTML galley

1. Fix `pdf_page_start`/`pdf_page_end` in `backfill/private/output/<vol>.<iss>/toc.json`
2. Re-split: `backfill/split_pipeline/split_issue.sh backfill/private/input/<vol>.<iss>.pdf`
3. Delete the affected galley file(s): `rm backfill/private/output/<vol>.<iss>/<seq>-<slug>.galley.html`
4. Re-generate HTML: `python3 backfill/html_pipeline/pipe1_haiku_html.py backfill/private/output/<vol>.<iss>/toc.json --yes`
5. Re-generate XML: `python3 backfill/html_pipeline/pipe6_ojs_xml.py backfill/private/output/<vol>.<iss>/toc.json -o backfill/private/output/<vol>.<iss>/import.xml`
6. Re-import: `sudo bash backfill/html_pipeline/pipe7_import.sh backfill/private/output/<vol>.<iss> --force`

## Fixing an HTML galley on live

**Never re-import on live** — it risks duplicates and ID changes. Instead, update the galley file in place:

1. Edit the `.galley.html` file in `backfill/private/output/<vol>.<iss>/` and commit
2. Find the galley file path on live:
   ```
   docker compose exec -T ojs-db bash -c 'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -e "
     SELECT f.path FROM files f
     JOIN submission_files sf ON f.file_id = sf.file_id
     JOIN publication_galleys g ON g.submission_file_id = sf.submission_file_id
     JOIN publications p ON g.publication_id = p.publication_id
     JOIN publication_settings ps ON p.publication_id = ps.publication_id AND ps.setting_name = \"title\"
     WHERE ps.setting_value LIKE \"%Article Title%\" AND f.mimetype = \"text/html\";
   "'
   ```
3. Build the full HTML file (repo files are body-only, live files need the DOCTYPE wrapper):
   ```
   { echo '<!DOCTYPE html>'; echo '<html lang="en">'; echo '<head><meta charset="utf-8"><title>Full Text</title></head>'; echo '<body>'; cat backfill/private/output/<vol>.<iss>/<seq>-<slug>.galley.html; echo '</body>'; echo '</html>'; } > /tmp/galley-update.html
   ```
4. Copy into the live OJS container:
   ```
   scp /tmp/galley-update.html root@$SERVER_IP:/tmp/
   ssh root@$SERVER_IP "cd /opt/pharkie-ojs-plugins && docker compose cp /tmp/galley-update.html ojs:/var/www/files/<path-from-step-2>"
   ```
