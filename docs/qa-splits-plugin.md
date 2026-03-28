# QA Splits Plugin

Visual QA tool for reviewing backfill article splits inside OJS. Three-pane interface: PDF (left), HTML galley + end-matter classification (right), metadata + review controls (top).

## Purpose

After the backfill pipeline splits issue PDFs into per-article PDFs, generates JATS XML, classifies end-matter, and produces HTML galleys, a human reviewer needs to verify the results visually. This plugin provides a rapid review workflow:

1. Compare PDF source against HTML output side-by-side
2. Verify end-matter classification (references, notes, bios, provenance) is correct
3. Record approve/reject decisions with comments
4. Track review progress across all articles
5. Detect when content changes invalidate prior approvals

## Requirements

- OJS 3.5+
- Articles must be imported into OJS before QA (needs OJS submission_id)
- Backfill output directory mounted in the OJS container (for JATS/PDF/HTML files)
- Journal Manager or Site Admin role

## Installation

### Docker (dev)

Already configured in `docker-compose.yml`:

```yaml
- ./plugins/qa-splits:/var/www/html/plugins/generic/qaSplits
- ./plugins/qa-splits/api/v1/qa-splits:/var/www/html/api/v1/qa-splits:ro
```

The plugin is auto-enabled by `scripts/setup-ojs.sh` when `QA_SPLITS_ENABLED=1` (default). Set `QA_SPLITS_ENABLED=0` in `.env` to disable (recommended for live).

### Manual (non-Docker)

1. Copy `plugins/qa-splits/` to `plugins/generic/qaSplits/` in your OJS installation
2. Copy `plugins/qa-splits/api/v1/qa-splits/` to `api/v1/qa-splits/` in your OJS installation
3. Add to `config.inc.php`:
   ```ini
   [qa-splits]
   backfill_output_dir = "/path/to/backfill/output"
   ```
4. Enable in OJS admin: Website > Plugins > Generic > QA Splits

## Configuration

In `config.inc.php`:

```ini
[qa-splits]
backfill_output_dir = "/data/sample-issues"
```

This should point to the directory containing issue subdirectories with JATS, PDF, and HTML files. In Docker dev, this is mounted from `private/backfill/output/`.

## Usage

### Accessing the QA interface

Navigate to `/<journal-path>/qa-splits` (e.g., `/index.php/ea/qa-splits`). Requires Journal Manager or Site Admin login.

### Interface layout

```
+------------------------------------------------------------------+
| TOP: Title | Authors | Section | Vol/Issue | #ID | [Status]      |
| [Approved] [Reject: ___________]                                  |
| [Last Seen] [< Prev] [Next >] [Random] [Problem Case]           |
| Progress: 150 total | 89 approved | 3 rejected | 58 unreviewed   |
+-------------------------------+----------------------------------+
| LEFT: PDF Viewer              | RIGHT: HTML Galley (iframe)      |
| Page 3 of 12                  |                                  |
| [scrollable PDF pages]        | [article body content]           |
|                               |                                  |
|                               | ─── End-Matter Classification ─ |
|                               | [Reference] Smith, J. (2020)...  |
|                               | [Note] See also Winnicott...     |
|                               | [Bio] John Smith is a pract...   |
|                               | [Provenance] This article was... |
+-------------------------------+----------------------------------+
```

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Left arrow` | Previous article (sequential) |
| `Right arrow` | Next article (sequential) |
| `A` | Approve current article |
| `R` | Reject (opens comment box) |
| `Enter` | Submit rejection (when comment box is focused) |
| `Escape` | Close comment box / help overlay |
| `?` | Show keyboard shortcuts help |

### Review workflow

1. **Compare**: Look at PDF (left) and HTML (right) side by side. Check that text extraction is correct, formatting is preserved, and no content is missing.

2. **Check end-matter**: Scroll down in the right pane to see classified items with category pills:
   - **Reference** (blue) — bibliographic citations
   - **Note** (amber) — editorial notes, cross-references
   - **Bio** (green) — author biographical information
   - **Provenance** (gray) — publication history ("This article was originally...")

3. **Decide**: Press `A` to approve or `R` to reject with a comment explaining the issue.

4. **Navigate**: Use arrow keys for sequential review, "Random" for unreviewed articles, or "Problem Case" to find rejected/invalidated articles.

### Navigation modes

- **Sequential** (Prev/Next): Moves through articles ordered by issue (volume desc, number desc) then by sequence within issue. Crosses issue boundaries automatically.
- **Last Seen**: Returns to the last article you viewed (persisted in browser localStorage).
- **Random**: Jumps to a random unreviewed article.
- **Problem Case**: Priority order: rejected articles first, then hash-invalidated (content changed after approval), then unreviewed.

### Content invalidation

When a review is submitted, the plugin computes a SHA256 hash of the HTML galley + JATS file content. If either file is regenerated (e.g., re-running `jats_to_html.py` or `extract_citations.py`), the hash changes and the review is marked as **INVALIDATED**. This means the approved content no longer matches what was reviewed.

Invalidated articles appear with an amber "Invalidated" badge and are surfaced by the "Problem Case" button.

## API Endpoints

All endpoints require authenticated Manager/Admin session. Base: `/api/v1/qa-splits/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/articles` | List all articles with issue info, review status, hash validity |
| `GET` | `/articles/{id}` | Single article metadata + full review history |
| `GET` | `/articles/{id}/pdf` | Stream PDF galley (backfill files, fallback to OJS storage) |
| `GET` | `/articles/{id}/html` | Return HTML galley body (sandboxed) |
| `GET` | `/articles/{id}/classification` | JATS back-matter parsed into categorized items |
| `POST` | `/reviews` | Submit review: `{submissionId, decision, comment}` |
| `GET` | `/nav/random-unreviewed` | Random unreviewed submission_id |
| `GET` | `/nav/problem-case` | Next problem case with reason |

### CSRF protection

The POST endpoint requires an `X-Csrf-Token` header matching the OJS session token. The QA page embeds this token automatically.

## Database

The plugin creates a `qa_split_reviews` table via migration:

| Column | Type | Description |
|--------|------|-------------|
| `review_id` | BIGINT PK | Auto-increment |
| `submission_id` | BIGINT | OJS submission_id |
| `publication_id` | BIGINT | OJS publication_id at review time |
| `user_id` | BIGINT | OJS user_id of reviewer |
| `username` | VARCHAR(255) | Snapshot of username at review time |
| `decision` | ENUM | `'approved'` or `'rejected'` |
| `comment` | TEXT | Rejection reason (null for approvals) |
| `content_hash` | VARCHAR(64) | SHA256 of HTML+JATS at review time |
| `created_at` | DATETIME | Timestamp |

Multiple reviews per article are stored (audit trail). The most recent review is the current status.

## File mapping

The plugin maps OJS `submission_id` to backfill files by scanning JATS XML files in the configured output directory and reading `<article-id pub-id-type="publisher-id">`. This publisher-id equals the OJS submission_id (set by `restore_ids.py` after import).

Articles without a publisher-id in their JATS file are flagged as warnings in the articles list response (meaning they haven't been imported into OJS yet).

## Security

- All endpoints verify Manager or Site Admin role via OJS session auth
- Per-article endpoints validate `context_id` (IDOR protection)
- HTML galley rendered in a sandboxed `<iframe sandbox="allow-same-origin">` (XSS protection)
- HTML endpoint returns `Content-Security-Policy: script-src 'none'`
- CSRF token validated on review submission
- File paths validated with `realpath()` to prevent traversal
- Rejection comments limited to 5000 characters

## E2E Tests

Tests are in `e2e/tests/qa-splits/qa-splits.spec.ts`. Run with:

```bash
npx playwright test qa-splits
```

Tests cover: access control, page rendering, PDF/HTML display, keyboard navigation, review submission, progress tracking, and API authentication.
