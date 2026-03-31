# QA Splits Plugin

Visual QA tool for reviewing backfill article splits inside OJS. Three-pane interface: sidebar (left), PDF (centre), HTML galley + end-matter classification (right).

## Purpose

After the backfill pipeline produces HTML galleys and imports them into OJS, a human reviewer needs to verify the results visually. This plugin provides a rapid review workflow:

1. Compare PDF source against HTML output side-by-side
2. Verify end-matter classification (references, notes, bios, provenance) is correct
3. Record approve/reject decisions with comments
4. Track review progress across all articles
5. Detect when content changes invalidate prior approvals

## Requirements

- OJS 3.5+
- Articles must be imported into OJS (needs submission_id, HTML/PDF galleys, citations)
- Any authenticated OJS user can access and review

## Installation

### Docker (dev)

Already configured in `docker-compose.yml`:

```yaml
- ./plugins/qa-splits:/var/www/html/plugins/generic/qaSplits
- ./plugins/qa-splits/api/v1/qa-splits:/var/www/html/api/v1/qa-splits:ro
```

The plugin is auto-enabled by `scripts/setup-ojs.sh` when `QA_SPLITS_ENABLED=1` (default).

### Manual (non-Docker / live)

1. Copy `plugins/qa-splits/` to `plugins/generic/qaSplits/` in your OJS installation
2. Copy `plugins/qa-splits/api/v1/qa-splits/` to `api/v1/qa-splits/` in your OJS installation
3. Enable in OJS admin: Website > Plugins > Generic > QA Splits

No configuration needed — the plugin reads all data from the OJS database and file storage.

## Data Sources

All data comes from OJS — no filesystem access to backfill output is required:

| Data | OJS Source |
|------|-----------|
| Article metadata | `publications`, `submissions`, `issues` tables |
| PDF galley | OJS file storage (`publication_galleys` → `files`) |
| HTML galley | OJS file storage (includes `jats-*` wrapper divs) |
| References | `citations` table (structured citations from import) |
| Notes/Bios/Provenance counts | Counted from `jats-notes`/`jats-bios`/`jats-provenance` divs in HTML galley |
| Review history | `qa_split_reviews` table (plugin's own table) |

## Usage

### Accessing the QA interface

Navigate to `/<journal-path>/qa-splits` (e.g., `/index.php/ea/qa-splits`). Requires any authenticated OJS login. There's also a floating "QA Splits" button on the OJS dashboard.

Deep links: append `?id=<submission_id>` to link directly to an article, e.g. `/qa-splits?id=9207`.

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

**Sidebar**: Search, issue/status/section filter pills, scrollable article list with status icons (✓ approved, ⚠ needs fix, · unreviewed), position counter, navigation.

**Top bar**: Article title/authors (wrapping), status badge, progress counter, Request Fix / Approve buttons.

**Right pane**: Article metadata header (issue + article ID, title, subtitle, authors, DOI, pages, keywords, abstract), then HTML galley body, then end-matter classification panel.

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
- **Status pills**: Approved / Needs Fix / Unreviewed (counts reflect current filter context)
- **Section pills**: Articles / Editorials / Book Reviews etc.

All pill counts are contextual — they show how many articles match if you click that pill, given the other active filters.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Previous / Next article |
| `A` | Approve and advance |
| `R` | Open Request Fix form |
| `Ctrl+Enter` | Submit fix request |
| `Escape` | Cancel / close |
| `?` | Toggle keyboard shortcuts help |

### Review workflow

1. **Compare**: PDF (left) vs HTML (right) side-by-side
2. **Check metadata**: Title, authors, DOI, abstract correct?
3. **Check end-matter**: References count reasonable? Bios extracted? Notes present?
4. **Check HTML markers**: Pipeline-extracted content has border labels. Unmarked content = not extracted.
5. **Decide**: `A` to approve, `R` to request fix with comment

### Content invalidation

When a review is submitted, a SHA256 hash of the HTML galley is stored. If the galley is reimported with different content, the hash changes and the review is marked **INVALIDATED**.

## API Endpoints

All endpoints require authenticated session. Base: `/api/v1/qa-splits/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/articles` | List all articles with metadata, review status |
| `GET` | `/articles/{id}` | Single article + full review history |
| `GET` | `/articles/{id}/pdf` | Stream PDF galley from OJS storage |
| `GET` | `/articles/{id}/html` | Return HTML galley body (sandboxed) |
| `GET` | `/articles/{id}/classification` | References (from citations table) + notes/bios/provenance counts |
| `POST` | `/reviews` | Submit review: `{submissionId, decision, comment}` |
| `GET` | `/nav/random-unreviewed` | Random unreviewed submission_id |
| `GET` | `/nav/problem-case` | Next problem case with reason |
| `GET` | `/stats` | QA progress: overall counts, section breakdown |

### CSRF protection

POST requires `X-Csrf-Token` header matching the OJS session token.

## Database

The plugin creates a `qa_split_reviews` table:

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

QA Splits reads from OJS — pipeline changes require reimport to be visible. Always use per-issue reimport when possible.

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
python3 backfill/html_pipeline/pipe6_ojs_xml.py backfill/private/output/23.1/toc.json -o backfill/private/output/23.1/import.xml
# 5. Reimport just that issue (~7 sec)
backfill/html_pipeline/pipe7_import.sh backfill/private/output/23.1 --force
# 6. Restore IDs for that issue only (~0.6 sec)
python3 backfill/html_pipeline/pipe8_restore_ids.py --target dev --issue 23.1
# 7. Check in QA Splits
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
  python3 backfill/html_pipeline/pipe6_ojs_xml.py "$t" -o "$(dirname "$t")/import.xml"
done
# 7. Reimport all (--wipe-articles wipes first; --force reimports existing without wiping)
backfill/html_pipeline/pipe7_import.sh backfill/private/output/* --wipe-articles
# 8. Restore IDs
python3 backfill/html_pipeline/pipe8_restore_ids.py --target dev
```

## Reporting content issues

The Inline HTML Galley plugin shows a "request a fix" link on article pages. Logged-in users can expand a form and submit — this writes to the same `qa_split_reviews` table, visible in QA Splits.
