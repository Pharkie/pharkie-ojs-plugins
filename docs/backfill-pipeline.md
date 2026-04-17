# Backfill Pipeline

A process guide for reviewing and importing journal back-issues into OJS. This guide is for anyone reviewing the backfill output -- you don't need to run commands or use the terminal. If you're the person running the scripts, see [Backfill Reference](backfill-reference.md) for all commands and technical details.

## What you'll need

- Access to the output directory (`backfill/private/output/`) -- each issue gets its own folder with split PDFs and metadata
- A PDF viewer to check the split articles
- A browser + login to the [Archive Checker plugin](archive-checker-plugin.md) on the OJS instance — this is the visual review interface once articles are imported
- A copy of each issue's CONTENTS page for cross-referencing

## Overview

Four-step workflow:

1. **Split** -- the pipeline validates the PDF, parses the table of contents, splits it into individual article PDFs, and normalizes author names. Fully automated.
2. **Check the split PDFs** -- you spot-check that the pipeline split the issue correctly (pages line up, book reviews separated, nothing missing). Flag issues for the pipeline operator to fix in `toc.json`.
3. **Enrich** -- the pipeline uses AI to extract deeper metadata (subjects, disciplines, themes, references). Optional but recommended.
4. **Import + review** -- the pipeline generates OJS XML, loads it into OJS, and you review each article in Archive Checker (approve / report problem). Fixes trigger a per-issue reimport (~8 sec) and a re-review. This is where most of your time goes.

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
│  Step 2: Check the split PDFs                        │
│                                                      │
│  Spot-check ── page alignment, boundaries,           │
│       │        book reviews, missing articles         │
│       │        Flag problems → fix in toc.json        │
│                                                      │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  corrected toc.json
       │
┌──────┴───────────────────────────────────────────────┐
│  Step 3: Enrich (AI-powered, optional)               │
│                                                      │
│  Extract subjects, disciplines, themes, references   │
│                                                      │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  enriched metadata
       │
  Step 4: Generate XML, import into OJS,
          review each article in Archive Checker
       │
       ▼  Archive Checker: approve / report problem
       │
  Iterate: pipeline fix → per-issue reimport (~8 sec) →
           Archive Checker re-review until all approved
```

### What happens in each automated step

**Preflight** checks that the PDF is readable, has extractable text (not scanned images), has a plausible page count, contains a detectable CONTENTS page, and that volume/issue numbers can be found on the cover.

**Parse TOC** reads the CONTENTS page and extracts per-article metadata: titles, authors, page ranges, sections, abstracts, and keywords. It automatically detects the offset between printed page numbers and PDF page indices. It recognizes section types (Editorial, Book Reviews, Obituary, Erratum, Correspondence, etc.) and classifies articles accordingly. Individual book reviews are detected by scanning for publication-line patterns.

**Split PDF + verify** creates one PDF per article, named sequentially (`01-editorial.pdf`, `02-title-slug.pdf`, etc.). It then verifies each split PDF's content matches its TOC title, catching page-offset errors where the wrong text ends up under a title.

**Normalize authors** resolves name variants (e.g. "E. van Deurzen" → "Emmy van Deurzen") using a persistent registry of known authors built up across all processed issues.

---

## Human review

Two phases: a quick pre-import check that the PDFs were split correctly, then the main post-import review in Archive Checker where you verify each article looks right in OJS and approve it (or flag it for a fix).

The automated pipeline does a good job, but over 30 years of issues you'll find page offsets that are wrong, book reviews that weren't separated properly, titles with OCR artefacts, and abstracts that captured too much or too little. Catching those problems is what this step is for.

### Phase 1: Check the split PDFs (pre-import)

Open the issue output directory and spot-check the split PDFs. Each issue's `toc.json` lists every article with its `pdf_file` field, so you can map each entry to its PDF.

**What to check:**

- **Page alignment.** Open a few PDFs and confirm the content matches the title. If article 3's PDF contains article 4's text, the page offset is wrong -- flag it for re-processing.
- **Article boundaries.** Each PDF should start at the beginning of its article and end before the next one. Look for articles that are cut short or include pages from the next article.
- **Book reviews.** The trickiest part. The pipeline detects individual book reviews by scanning for publication-line patterns, but reviews that don't start on a new page or use unusual citation formats may be missed. Check that:
  - Each book review is its own PDF (not merged with the next review)
  - The Book Review Editorial (the introductory overview) is separate from the individual reviews
  - No reviews are missing entirely
- **Page order.** Scan through the numbered PDF filenames (`01-editorial.pdf`, `02-...`, etc.) and confirm they follow the issue's actual article order.
- **Missing articles.** Compare the PDF count against the CONTENTS page. If articles are missing, flag them for investigation.

If you find problems, let the person running the pipeline know — they'll correct `toc.json` and re-run the split. See [Backfill Reference](backfill-reference.md) for how to re-run with corrections.

### Phase 2: Review each article in Archive Checker (post-import)

Once the pipeline has imported an issue into OJS, every article shows up in the [Archive Checker](archive-checker-plugin.md) plugin — a three-pane interface showing the article sidebar (left), the original PDF (centre), and the HTML "Full Text" version alongside end-matter (right). Walk through each article, compare the two, and click **Approve** or **Report Problem**.

**What to check:**

- **title** -- no OCR artefacts (broken characters, missing spaces, truncation). Compare against the PDF. Book review titles should read `Book Review: <book title>`.
- **authors** -- names complete and correctly split. Multiple authors joined with `&` (e.g. `John Smith & Jane Doe`). Watch for first/last swaps, missing initials, mangled accented characters (`van Deurzen`, not `van Deu rzen`).
- **section** -- one of `Editorial`, `Articles`, `Book Review Editorial`, `Book Reviews`. Editorials should not be under Articles; the Book Review Editorial (introductory overview) should not be mixed in with individual Book Reviews; obituaries and errata go under Editorial.
- **abstract** -- matches the article's actual abstract, not the keywords line or the introduction, not truncated mid-sentence, not missing where one exists. Editorials and book reviews typically don't have an abstract.
- **keywords** -- correctly extracted, not merged into a single blob, not prefixed with heading text ("Key Words:"), not missing.
- **HTML "Full Text" body** (right pane) -- renders the article's full prose without gaps, misordered paragraphs, or missing diagrams/tables. This is the AI-generated galley; most issues are post-processing fixes that need a pipeline rerun.
- **Citations and end-matter** (right pane, below body) -- references vs. notes correctly classified, author bios present and complete.
- **PDF vs HTML consistency** (centre vs right) -- nothing material is missing from the HTML that's in the PDF, and vice versa.

Click **Approve** when everything looks right. Click **Report Problem** with a short note when it doesn't — the report goes into the review log and the pipeline operator picks it up.

**Iteration loop.** When a problem report needs a pipeline fix, the operator fixes the code, re-runs the relevant pipeline steps (~seconds per issue), reimports the affected issue into dev (~8 sec), and marks the article as `recheck`. You come back to it, re-review, and approve if fixed. Rinse and repeat until everything is green.

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

Enriched fields (`subjects`, `disciplines`, enriched `keywords`, `coverage`, references) show up on the imported article pages in OJS, so they're visible in Archive Checker alongside everything else — spot-check them as part of Phase 2.

Enrichment is optional -- the import works without it. It's resumable, so if interrupted it picks up where it left off.

See [Backfill Reference](backfill-reference.md) for enrichment commands, cost estimates, and concurrency options.

---

## Generate XML and import

After review (and optional enrichment), the pipeline generates OJS Native XML with the corrected metadata and embedded article PDFs, then loads it into OJS. Before importing, it checks whether the issue already exists (by volume and number) to prevent duplicates. Articles with existing DOIs registered at Crossref are automatically matched and preserved.

After import, a verification step checks that all articles exist in the OJS database.

See [Backfill Reference](backfill-reference.md) for the generate/import/verify commands.

---

For all commands, flags, data formats, and troubleshooting, see [Backfill Reference](backfill-reference.md).
