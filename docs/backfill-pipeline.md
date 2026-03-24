# Backfill Pipeline

A process guide for reviewing and importing journal back-issues into OJS. This guide is for anyone reviewing the backfill output -- you don't need to run commands or use the terminal. If you're the person running the scripts, see [Backfill Reference](backfill-reference.md) for all commands and technical details.

## What you'll need

- Access to the output directory (`backfill/output/`) -- each issue gets its own folder with split PDFs and metadata
- A PDF viewer to check the split articles
- Google Sheets (or any spreadsheet editor) for reviewing metadata
- A copy of each issue's CONTENTS page for cross-referencing

## Overview

Four-step workflow:

1. **Split** -- the pipeline validates the PDF, parses the table of contents, splits it into individual article PDFs, and normalizes author names. Fully automated.
2. **Human review** -- you check the split PDFs are correct and review metadata in a spreadsheet. This is where most of your time goes.
3. **Enrich** -- the pipeline uses AI to extract deeper metadata (subjects, disciplines, themes, references). Optional but recommended. You can review the results in a second spreadsheet pass.
4. **Import** -- the pipeline generates OJS XML and loads it into OJS. It checks for duplicates automatically.

---

## How the pipeline works

```
 Issue PDF
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  Step 1: Automated split                             │
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
│  Step 2: Human review                                │
│                                                      │
│  Check split PDFs ── page alignment, boundaries,     │
│       │              book reviews, missing articles   │
│       │                                              │
│  Review spreadsheet ── titles, authors, sections,    │
│       │                 abstracts, keywords           │
│                                                      │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  corrected metadata
       │
┌──────┴───────────────────────────────────────────────┐
│  Step 3: Enrich (AI-powered, optional)               │
│                                                      │
│  Extract subjects, disciplines, themes, references   │
│  Optional: re-export spreadsheet to spot-check       │
│                                                      │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  enriched metadata
       │
  Step 4: Generate XML and import into OJS
       │
       ▼
  Verify the import
```

### What happens in each automated step

**Preflight** checks that the PDF is readable, has extractable text (not scanned images), has a plausible page count, contains a detectable CONTENTS page, and that volume/issue numbers can be found on the cover.

**Parse TOC** reads the CONTENTS page and extracts per-article metadata: titles, authors, page ranges, sections, abstracts, and keywords. It automatically detects the offset between printed page numbers and PDF page indices. It recognizes section types (Editorial, Book Reviews, Obituary, Erratum, Correspondence, etc.) and classifies articles accordingly. Individual book reviews are detected by scanning for publication-line patterns.

**Split PDF + verify** creates one PDF per article, named sequentially (`01-editorial.pdf`, `02-title-slug.pdf`, etc.). It then verifies each split PDF's content matches its TOC title, catching page-offset errors where the wrong text ends up under a title.

**Normalize authors** resolves name variants (e.g. "E. van Deurzen" → "Emmy van Deurzen") using a persistent registry of known authors built up across all processed issues.

---

## Human review

Two parts: check the split PDFs visually, then review metadata in a spreadsheet. The automated pipeline does a good job, but over 30 years of issues you'll find page offsets that are wrong, book reviews that weren't separated properly, titles with OCR artefacts, and abstracts that captured too much or too little. This step catches those problems before they're baked into OJS.

### Part 1: Check the split PDFs

Open the issue output directory and spot-check the split PDFs. The `pdf_file` column in the review spreadsheet (see Part 2) maps each row to its PDF filename.

**What to check:**

- **Page alignment.** Open a few PDFs and confirm the content matches the title. If article 3's PDF contains article 4's text, the page offset is wrong -- flag it for re-processing.
- **Article boundaries.** Each PDF should start at the beginning of its article and end before the next one. Look for articles that are cut short or include pages from the next article.
- **Book reviews.** The trickiest part. The pipeline detects individual book reviews by scanning for publication-line patterns, but reviews that don't start on a new page or use unusual citation formats may be missed. Check that:
  - Each book review is its own PDF (not merged with the next review)
  - The Book Review Editorial (the introductory overview) is separate from the individual reviews
  - No reviews are missing entirely
- **Page order.** Scan through the numbered PDF filenames (`01-editorial.pdf`, `02-...`, etc.) and confirm they follow the issue's actual article order.
- **Missing articles.** Compare the PDF count against the CONTENTS page. If articles are missing, flag them for investigation.

If you find problems with page alignment or missing articles, let the person running the pipeline know. See [Backfill Reference](backfill-reference.md) for how to re-run with corrections.

### Part 2: Review metadata in a spreadsheet

The pipeline exports metadata to a CSV file. Upload it to Google Sheets, review and correct it, then download and hand it back for import. Each article has a stable ID (like `v37i1a0` -- volume 37, issue 1, article 0), so you can sort, filter, and rearrange the spreadsheet without breaking anything.

**What to check per column:**

- **title** -- Fix OCR artefacts (broken characters, missing spaces), missing words, or truncated titles. Compare against the PDF if unsure. Book review titles should be `Book Review: <book title>`.
- **authors** -- Check names are complete and correctly split. Multiple authors should be separated by `&` (e.g. `John Smith & Jane Doe`). Watch for: first/last name swaps, missing initials, OCR mangling of accented characters (van Deurzen, not van Deu rzen).
- **section** -- Must be exactly one of: `Editorial`, `Articles`, `Book Review Editorial`, `Book Reviews`. Check that: editorials aren't classified as Articles, the Book Review Editorial (introductory overview) isn't mixed in with individual Book Reviews, obituaries and errata are under Editorial.
- **abstract** -- Check it captured the right text. Common problems: abstract includes the keywords line, abstract is truncated mid-sentence, abstract captured the introduction instead, or abstract is entirely missing (add it manually from the PDF). Not all articles have abstracts -- editorials and book reviews typically don't.
- **keywords** -- Semicolon-separated (e.g. `phenomenology; therapy; Heidegger`). Check they were extracted correctly. Common problems: keywords merged into one blob, keywords include the heading text ("Key Words:"), or keywords are missing entirely.
- **pages** -- Page range (read-only). Useful for cross-referencing against the PDF.
- **pdf_file** -- Filename of the split PDF (read-only). Open this file to check the content matches the row.

**Don't edit these columns** (used for matching): `id`, `file`, `index`.

**Don't** delete rows (every article must have a row), edit the `id` column, or add rows. The import rejects all of these with a clear error.

After reviewing, download as CSV (File > Download > Comma-separated values) and hand it back for import. A dry-run preview will show every change before anything is written. See [Backfill Reference](backfill-reference.md) for the export/import commands.

---

## Enrichment

After human review, the pipeline can optionally extract deeper metadata from each article using AI. This reads the full text of every split PDF and extracts structured information to make the archive more discoverable.

### What it extracts and why

The enrichment extracts two kinds of metadata:

**Fields that go into OJS** (searchable and visible on article pages):

- **Subjects** -- broad topic areas (e.g. "clinical practice", "philosophy of mind"). These appear on article pages and can be searched/browsed.
- **Disciplines** -- academic fields (e.g. "existential psychotherapy", "phenomenological psychology"). Helps readers and search engines understand what area an article belongs to.
- **Keywords** -- enriched versions of the original keywords, filling gaps where keywords were missing or incomplete. The most visible metadata -- shown on every article page and used in search.
- **References** -- key works cited by the article, shown on a "References" tab.
- **Coverage** -- geographical context and historical period, where relevant.

**Fields stored for future use** (not in OJS yet, but preserved):

- **Themes** -- existential concepts (authenticity, dasein, being-toward-death)
- **Thinkers** -- philosophers and theorists the article substantially engages with
- **Modalities** -- therapeutic approaches discussed
- **Methodology** -- research method (phenomenological study, case study, etc.)
- **Summary** -- a 2-3 sentence synopsis
- **Clinical population** -- specific groups discussed (adolescents, veterans, etc.)

This sidecar data is a structured index of the entire 30-year archive. It could power features like a browse-by-theme page, a thinker index ("all articles engaging with Heidegger"), related article recommendations, or a modality navigator for practitioners looking for clinical literature.

### Reviewing enrichment

After enrichment, you can do a second spreadsheet review. The exported CSV will now include `subjects`, `disciplines`, and `keywords_enriched` columns for spot-checking. The same export/review/import process applies.

Enrichment is optional -- the import works without it. It's resumable, so if interrupted it picks up where it left off.

See [Backfill Reference](backfill-reference.md) for enrichment commands, cost estimates, and concurrency options.

---

## Generate XML and import

After review (and optional enrichment), the pipeline generates OJS Native XML with the corrected metadata and embedded article PDFs, then loads it into OJS. Before importing, it checks whether the issue already exists (by volume and number) to prevent duplicates. Articles with existing DOIs registered at Crossref are automatically matched and preserved.

After import, a verification step checks that all articles exist in the OJS database.

See [Backfill Reference](backfill-reference.md) for the generate/import/verify commands.

---

For all commands, flags, data formats, and troubleshooting, see [Backfill Reference](backfill-reference.md).
