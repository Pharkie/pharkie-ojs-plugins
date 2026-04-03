# Archive Checker Plugin

Visual review tool for checking archive journal articles inside OJS. Three-pane interface: sidebar (left), original PDF (centre), HTML version + end-matter (right).

## Purpose

After the backfill pipeline converts PDF articles to HTML and imports them into OJS, reviewers compare the originals against the HTML versions. This plugin provides a rapid review workflow:

1. Compare original PDF against HTML version side-by-side
2. Verify metadata (title, authors, pages, keywords, abstract)
3. Check end-matter classification (references, notes, bios, provenance)
4. Approve articles or report problems with comments
5. Track review progress across all articles
6. Detect when content changes invalidate prior approvals

## Requirements

- OJS 3.5+
- Articles must be imported into OJS (needs submission_id, HTML/PDF galleys, citations)
- Any authenticated OJS user can access and review

## Installation

### Docker (dev)

Already configured in `docker-compose.yml`:

```yaml
- ./plugins/archive-checker:/var/www/html/plugins/generic/archiveChecker
- ./plugins/archive-checker/api/v1/archive-checker:/var/www/html/api/v1/archive-checker:ro
```

The plugin is auto-enabled by `scripts/ojs/setup-ojs.sh` when `ARCHIVE_CHECKER_ENABLED=1` (default).

### Manual (non-Docker / live)

1. Copy `plugins/archive-checker/` to `plugins/generic/archiveChecker/` in your OJS installation
2. Copy `plugins/archive-checker/api/v1/archive-checker/` to `api/v1/archive-checker/` in your OJS installation
3. Enable in OJS admin: Website > Plugins > Generic > Archive Checker

No configuration needed — the plugin reads all data from the OJS database and file storage.

## Data Sources

All data comes from OJS — no filesystem access to backfill output is required:

| Data | OJS Source |
|------|-----------|
| Article metadata | `publications`, `submissions`, `issues` tables |
| PDF galley | OJS file storage (`publication_galleys` → `files`) |
| HTML galley | OJS file storage (includes `jats-*` wrapper divs) |
| References | `citations` table (structured citations from import) |
| Citation DOIs | `citation_settings` table (`crossref::doi`, written by pipe9b) |
| Content-filtered flag | `publication_settings` table (`contentFiltered`, written by pipe9c) |
| Notes/Bios/Provenance counts | Counted from `jats-notes`/`jats-bios`/`jats-provenance` divs in HTML galley |
| Review history | `archive_checker_reviews` table (plugin's own table) |

## Usage

### Accessing the QA interface

Navigate to `/<journal-path>/archive-checker` (e.g., `/index.php/ea/archive-checker`). Requires any authenticated OJS login. There's also a floating "Archive Checker" button on the OJS dashboard.

Deep links: append `?id=<submission_id>` to link directly to an article, e.g. `/archive-checker?id=9207`. The URL always reflects all active filters, so you can copy and share it to show someone exactly what you're looking at.

### Interface layout

```
+------------+---------------------------+---------------------------+
| SIDEBAR    | PDF Viewer                | Article Metadata          |
| Search     | Page 3 of 12              | Issue #ID / Title / Sub   |
| Filters    | [scrollable PDF pages]    | DOI / Keywords / Abstract |
| Article    |                           |─────────────────────────  |
| list       |                           | HTML Galley               |
|            |                           | (body text)               |
| 4/12       |                           |                           |
| ‹ Prev     |                           | [Author bio]              |
| Next ›     |                           |───────────────────────────|
|            |                           | End-Matter Classification |
|            |                           | Author Bios (1)           |
|            |                           | References (10)           |
+------------+---------------------------+---------------------------+
```

**Sidebar**: Search, "Surprise me" random button, issue/status/section filter pills, scrollable article list with status icons (✓ approved, ⚠ reported, · unchecked), position counter, keyboard shortcuts link.

**Top bar**: Article title with close button (×), status badge with progress thermometer (green/amber/grey), Report Problem / Approve buttons.

**Left pane**: Original PDF with "ORIGINAL PDF" label, page counter, search, and dark mode colour inversion hint.

**Right pane**: "HTML VERSION" label, article metadata header (issue + article ID, title, subtitle, authors, DOI, pages, keywords, abstract), then HTML galley body (with content-filtered warning if applicable), then end-matter classification panel with DOI links.

### End-matter classification

The classification panel shows what the pipeline extracted:

- **Author Bios** — count only (content visible in the HTML above, labelled with "Author bio" CSS marker)
- **References** — full list from OJS citations table (not in HTML galley — OJS renders these separately)
- **Notes** — count only (visible in HTML above)
- **Provenance** — count only (visible in HTML above)

Pipeline-extracted content in the HTML galley is marked with a left border and label via CSS (`jats-notes`, `jats-bios`, `jats-provenance` classes). If content appears without this marker, it wasn't extracted by the pipeline.

### Sidebar filters

- **Search**: filters by title, author, or keyword (client-side, instant)
- **Issue dropdown**: filter to a single issue
- **Status pills**: Approved / Reported / Unchecked (counts reflect current filter context)
- **Section pills**: Articles / Editorials / Book Reviews etc.
- **Reviewer pills**: By me / By others — filter by who reviewed

All pill counts are contextual — they show how many articles match if you click that pill, given the other active filters.

### Shareable URLs

All filters are serialised into URL params: `issue`, `status`, `section`, `reviewer`, `q` (search), and `id` (current article). Copy the URL to share your exact view — the recipient gets the same filters and article position.

### Features

- **Dark/light mode**: Follows OS preference (`prefers-color-scheme`). Dark mode inverts PDF page colours via pdf.js `pageColors` for comfortable reading. A "Colours inverted for dark mode" hint shows in the PDF toolbar.
- **First-visit guide**: Auto-shows a "Help check the archive" overlay on first visit explaining the interface, what to check, and known limitations. Dismissed with any key, re-openable via "What to check?" in the sidebar.
- **"Surprise me"**: Loads a random batch of unchecked articles. Dice emoji shakes on click. Also triggered by `?mode=random` URL param (used by the article page CTA).
- **Progress thermometer**: Green (approved) / amber (reported) / grey (remaining) bar under the progress stats.
- **Button confirmation**: "Approved ✓" / "Saved ✓" flash for 2 seconds after submission. Approve auto-advances after 600ms; Report Problem stays on the article for further edits.
- **Content-filtered filter**: Articles that couldn't be fully extracted are flagged in JATS (`<custom-meta name="content-filtered">`), stored in OJS `publication_settings`, and excluded from the sidebar by default. Toggle the "Content filtered" pill to show them. Flagged articles show an amber warning banner.
- **Pane labels**: "ORIGINAL PDF" and "HTML VERSION" labels help orient first-time users.
- **Citation DOIs**: Reference list shows matched DOIs as clickable links, with DOI count in the pill label.
- **Article page CTA**: Logged-in users see a "Help Check the Archive" box on article pages with progress stats and a link to Archive Checker in random mode.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Previous / Next article |
| `A` | Approve (auto-advances) |
| `R` | Report Problem (opens form) |
| `Ctrl+Enter` | Submit report |
| `Ctrl+F` / `Cmd+F` | Search within PDF |
| `Enter` / `Shift+Enter` | Next / previous search match |
| `Escape` | Cancel / close |
| `?` | Show keyboard shortcuts |

### PDF viewer

- **Text selection**: Select and copy text directly from the PDF
- **Search**: Click the magnifying glass icon or press `Ctrl+F` to search within the current PDF. Matches are highlighted with next/previous navigation.
- **Dark mode**: PDF pages render with dark background and light text via pdf.js `pageColors` — not a CSS filter, so text remains crisp
- Uses the official pdf.js v5 `PDFViewer` with `PDFFindController` — full-featured rendering, text layer, and search

### Review workflow

1. **Compare**: Original PDF (left) vs HTML version (right) side-by-side
2. **Check metadata**: Title, authors, page numbers, keywords, abstract correct?
3. **Check content**: Article text matches PDF? Nothing missing, garbled, or out of order?
4. **Check boundaries**: No content from neighbouring articles mixed in?
5. **Check end-matter**: References complete? DOI links correct? Notes and bios in the right place?
6. **Decide**: Approve if everything looks correct, or Report Problem with description

### Content invalidation

When a review is submitted, a SHA256 hash of the HTML galley is stored. If the galley is reimported with different content, the hash changes and the review is marked **INVALIDATED**.

## API Endpoints

All endpoints require authenticated session. Base: `/api/v1/archive-checker/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/articles` | List all articles with metadata, review status |
| `GET` | `/articles/{id}` | Single article + full review history |
| `GET` | `/articles/{id}/pdf` | Stream PDF galley from OJS storage |
| `GET` | `/articles/{id}/html` | Return HTML galley body (sandboxed) |
| `GET` | `/articles/{id}/classification` | References (from citations table) + notes/bios/provenance counts |
| `POST` | `/reviews` | Submit review: `{submissionId, decision, comment}` |
| `GET` | `/nav/random-unreviewed` | Batch of random unreviewed submission_ids |
| `GET` | `/nav/problem-case` | Next problem case with reason |
| `GET` | `/stats` | QA progress: overall counts, section breakdown |

### CSRF protection

POST requires `X-Csrf-Token` header matching the OJS session token.

## Database

The plugin creates a `archive_checker_reviews` table:

| Column | Type | Description |
|--------|------|-------------|
| `review_id` | BIGINT PK | Auto-increment |
| `submission_id` | BIGINT | OJS submission_id |
| `publication_id` | BIGINT | OJS publication_id at review time |
| `user_id` | BIGINT | Reviewer's OJS user_id |
| `username` | VARCHAR(255) | Snapshot of username |
| `decision` | ENUM | `'approved'` or `'needs_fix'` |
| `comment` | TEXT | Fix reason (null for approvals) |
| `content_hash` | VARCHAR(64) | SHA256 of HTML galley at review time |
| `created_at` | DATETIME | Timestamp |

Multiple reviews per article stored (audit trail). Most recent = current status.

## Security

- All endpoints require authenticated OJS session
- Per-article endpoints validate `context_id` (IDOR protection)
- HTML galley script tags stripped before rendering
- HTML endpoint returns `Content-Security-Policy: script-src 'none'`
- CSRF token validated on review submission
- Fix comments limited to 5000 characters

## CLI Tool

`backfill/html_pipeline/qa/qa_review.py` is the command-line equivalent:

```bash
python3 backfill/html_pipeline/qa/qa_review.py approve 9494
python3 backfill/html_pipeline/qa/qa_review.py reject 9494 "references mixed with notes"
python3 backfill/html_pipeline/qa/qa_review.py status 9494
python3 backfill/html_pipeline/qa/qa_review.py list
python3 backfill/html_pipeline/qa/qa_review.py --target live list
```

## QA iteration workflow

Archive Checker reads from OJS — pipeline changes require reimport to be visible. Always use per-issue reimport when possible.

**Per-issue iteration** (~8 sec, preferred):
```bash
# 1. Fix pipeline code
# 2. Reprocess from raw (~11 sec for all, or target one issue)
python3 backfill/html_pipeline/pipe2_postprocess.py backfill/private/output/23.1/toc.json
# 3. JATS pipeline
python3 backfill/html_pipeline/pipe3_generate_jats.py backfill/private/output/23.1/toc.json
python3 backfill/html_pipeline/pipe4_extract_citations.py --extract --volume 23.1
python3 backfill/html_pipeline/pipe5_galley_html.py backfill/private/output/23.1/toc.json
# 4. Regenerate import XML
python3 backfill/html_pipeline/pipe6_ojs_xml.py backfill/private/output/23.1/toc.json
# 5. Reimport just that issue (~7 sec)
sudo bash backfill/html_pipeline/pipe7_import.sh backfill/private/output/23.1 --force
# 6. Restore IDs for that issue only (~0.6 sec)
sudo python3 backfill/html_pipeline/pipe8_restore_ids.py --target dev --issue 23.1
# 7. Check in Archive Checker
```

**Full reimport** (~20 min, only for systemic changes affecting all issues):
```bash
# Each step writes to its own file — no ordering collisions:
# .raw.html → .post.html → .jats.xml → .galley.html

# 1. Reprocess all from raw (.raw.html → .post.html)
python3 backfill/html_pipeline/pipe2_postprocess.py backfill/private/output/*/toc.json
# 2. Generate JATS (reads .post.html)
python3 backfill/html_pipeline/pipe3_generate_jats.py backfill/private/output/*/toc.json
# 3. Extract citations (body → back matter)
python3 backfill/html_pipeline/pipe4_extract_citations.py --extract
# 4. JATS → HTML galley (.jats.xml → .galley.html)
python3 backfill/html_pipeline/pipe5_galley_html.py backfill/private/output/*/toc.json
# 6. Generate import XML
for t in backfill/private/output/*/toc.json; do
  python3 backfill/html_pipeline/pipe6_ojs_xml.py "$t"
done
# 7. Reimport all (--wipe-articles wipes first; --force reimports existing without wiping)
sudo bash backfill/html_pipeline/pipe7_import.sh backfill/private/output/* --wipe-articles
# 8. Restore IDs
sudo python3 backfill/html_pipeline/pipe8_restore_ids.py --target dev
```

## Reporting content issues

The Inline HTML Galley plugin shows a "request a fix" link on article pages. Logged-in users can expand a form and submit — this writes to the same `archive_checker_reviews` table, visible in Archive Checker.
