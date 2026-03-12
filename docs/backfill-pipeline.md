# Backfill Pipeline

Converts whole-issue PDFs of Existential Analysis into per-article PDFs and OJS Native XML for import. Designed for backfilling the journal archive -- 30+ years of issues that predate the OJS installation.

Three-step workflow:

1. **Split** -- validate the PDF, parse the table of contents, split into individual article PDFs, verify splits match TOC titles, and normalize author names.
2. **Human review** -- export metadata to a spreadsheet, correct titles/authors/abstracts/sections/keywords in Google Sheets, import corrections back.
3. **Import** -- generate OJS Native XML and load it into OJS. Checks for existing issues to prevent duplicates.

---

## Quick start

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

---

## Pipeline steps

```
 Issue PDF
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  split-issue.sh                                      │
│                                                      │
│  1. Preflight ──── validate PDF, detect vol/issue    │
│       │                                              │
│  2. Parse TOC ──── extract titles, authors, pages    │
│       │                                              │
│  3. Split PDF ──── one PDF per article               │
│       │                                              │
│  3b. Verify ────── check split PDFs match titles     │
│       │                                              │
│  4. Normalize ──── resolve author name variants      │
│       │                                              │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  toc.json + per-article PDFs
       │
┌──────┴───────────────────────────────────────────────┐
│  4b. Human review                                    │
│                                                      │
│  export_review.py ──► Google Sheets ──► import_review│
│                                                      │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  corrected toc.json
       │
  5. Generate XML ── OJS Native XML with embedded PDFs
       │
       ▼
  import.sh ──────── load into OJS
       │
       ▼
  verify.py ──────── check articles exist in OJS DB
```

Each step is a standalone Python script. `split-issue.sh` runs steps 1--4 in sequence.

### 1. Preflight

Validates the source PDF before processing. Checks that the PDF is readable, has extractable text (not scanned images), has a plausible page count, contains a detectable `CONTENTS` page, and that volume/issue numbers can be extracted from the cover. Fails the issue if the PDF is unreadable or has too little text.

### 2. Parse TOC

Extracts the table of contents from the `CONTENTS` page and builds per-article metadata: titles, authors, page ranges, sections, abstracts, and keywords. Automatically detects the page offset between printed page numbers and PDF page indices. Recognizes section types (Editorial, Book Reviews, Obituary, Erratum, Correspondence, etc.) and classifies articles accordingly. Book reviews are detected individually by scanning for publication-line patterns within the Book Reviews section.

If page offset auto-detection fails (e.g. no EDITORIAL heading), pass `--page-offset=N` manually.

### 3. Split PDF + verify

Splits the whole-issue PDF into one PDF per article, named sequentially (`01-editorial.pdf`, `02-title-slug.pdf`, etc.). Then verifies that each split PDF's content matches its TOC title by checking for keyword overlap in the first two pages. This catches page-offset errors where the wrong article text ends up under a title.

### 4. Normalize authors

Resolves author name variants to canonical forms using a persistent registry (`backfill/authors.json`). Matches by exact name, surname + first initial, and fuzzy similarity. Ambiguous matches are written to a review file for human resolution.

---

## Human review

A spreadsheet review step between splitting and XML generation. The automated TOC parser does a good job, but over 30 years of issues you'll find titles with OCR artefacts, author names split incorrectly, abstracts that captured too much or too little, and articles in the wrong section. This step lets you scan everything in a spreadsheet and fix problems before they're baked into OJS.

This runs once across the entire archive. There's no second pass.

Each article gets a stable ID (like `v37i1a0` -- volume 37, issue 1, article 0). The ID is stored in the toc.json and included in the CSV. Matching on import is by ID, not row position, so you can sort, filter, and rearrange the spreadsheet without breaking anything.

### Export

```bash
# Single issue
python3 backfill/export_review.py backfill/output/EA-vol37-iss1/toc.json -o review.csv

# All issues at once
python3 backfill/export_review.py backfill/output/*/toc.json -o review.csv
```

### Edit in Google Sheets

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

### Preview changes (always do this first)

```bash
python3 backfill/import_review.py review.csv --dry-run
```

Shows every change field by field without writing anything. Review the output before applying.

### Import corrections

```bash
python3 backfill/import_review.py review.csv
```

Updates each toc.json in place. A backup is saved as `toc.json.pre-review` before changes are written.

If you changed author names, re-run normalization afterwards:

```bash
python3 backfill/author_normalize.py backfill/output/*/toc.json
```

### Undo

```bash
python3 backfill/import_review.py review.csv --restore
```

Restores every toc.json from its `.pre-review` backup.

---

## Generate XML and import

**Generate XML** creates an OJS Native XML file with base64-embedded PDFs from the corrected toc.json. Each article PDF is encoded inline, producing a 20--50 MB XML file per issue. Articles with existing DOIs registered at Crossref are automatically matched and preserved in the XML.

**Import** loads the XML into OJS via the Native Import/Export CLI. Before importing, it checks whether the issue already exists in the OJS database (by volume + number) and skips unless `--force` is passed.

```bash
# Generate XML (re-run after human review)
backfill/split-issue.sh path/to/EA-vol37-iss1.pdf --only=generate_xml

# Import into OJS
backfill/import.sh backfill/output/EA-vol37-iss1

# Verify the import
backfill/verify.py backfill/output/EA-vol37-iss1/toc.json --docker
```

---

## Known limitations

- **TOC parser tuned for recent issues.** The `CONTENTS` page format, text extraction patterns, and page offset detection are calibrated against issues from the last ~10 years. Older issues with different layouts may need the `--page-offset=N` flag or manual `toc.json` adjustment.
- **Book review detection is heuristic.** Individual reviews are identified by publication-line patterns near the top of pages. Reviews that don't start on a new page, or that use unusual citation formats, may be missed or mis-split.
- **Keyword extraction edge cases.** Keywords are extracted by finding "Key Words", "Keywords", or "Key Word" headings (with or without colons), supporting both comma and semicolon separators. Articles that use other keyword formats (e.g. inline keywords with no heading) may not be captured.
- **Abstract extraction relies on section headers.** Abstracts are captured between "Abstract" (or "Abstract:") and the next section heading ("Key Words", "Keywords", "Introduction", or a capitalised heading). Unusual article structures may yield incomplete or missing abstracts.
- **Reviewer name extraction is best-effort.** The pipeline scans backwards from the end of each book review looking for a standalone name line. Long reference sections can cause missed extractions.
- **Author email placeholders.** OJS requires an email for every author. The XML uses `firstname.lastname@placeholder.invalid` since historical articles don't have author emails.
- **DOI matching is fuzzy.** The registry lookup handles common differences but unusual title variations may need manual aliases in `doi-registry.json`.

---

For data formats, CLI flags, and script reference, see [Backfill Reference](backfill-reference.md).
