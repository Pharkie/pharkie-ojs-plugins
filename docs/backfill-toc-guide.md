# Backfill TOC Guide

How to create `toc.json` files for the backfill pipeline. Each issue PDF needs a `toc.json` in `backfill/output/<vol>.<iss>/` before `split-issue.sh` can process it.

## Quick version

Ask Claude: "Read `backfill/input/<vol>.pdf` and create `backfill/output/<vol>/toc.json` following the schema in `docs/backfill-toc-guide.md`."

## toc.json schema

```json
{
  "source_pdf": "/absolute/path/to/issue.pdf",
  "volume": 23,
  "issue": 1,
  "date": "January 2012",
  "total_pdf_pages": 193,
  "articles": [ ... ]
}
```

### Top-level fields

| Field | Type | Description |
|---|---|---|
| `source_pdf` | string | Absolute path to the source PDF. Updated automatically by `split-issue.sh`. |
| `volume` | int | Volume number (1-37). |
| `issue` | int | Issue number (1 for single-issue volumes 1-5, 1 or 2 for the rest). |
| `date` | string | Publication date, e.g. "January 2012", "July 2015". |
| `total_pdf_pages` | int | Total pages in the PDF. |

### Article entry fields

Every item in the `articles` array has these fields:

| Field | Required | Description |
|---|---|---|
| `title` | yes | Article title. Book reviews: `"Book Review: <book title>"`. |
| `authors` | yes | Author name(s) as a string. For book reviews, this is the **reviewer**. Null for unsigned editorials. |
| `section` | yes | One of: `"Editorial"`, `"Articles"`, `"Book Review Editorial"`, `"Book Reviews"`. |
| `pdf_page_start` | yes | 0-based PDF page index. Page 0 is the first page of the PDF. |
| `pdf_page_end` | yes | 0-based PDF page index of the last page (inclusive). |

Book reviews have additional fields:

| Field | Required | Description |
|---|---|---|
| `book_title` | yes | Title of the book being reviewed (without "Book Review:" prefix). |
| `book_author` | yes | Author of the book. |
| `book_year` | yes | Publication year (int). |
| `publisher` | no | Publisher, e.g. "London: Routledge". |
| `reviewer` | yes | Name of the person who wrote the review. Same as `authors`. |

### Sections

- **Editorial** — usually the first entry. Authors should be the editor(s) of the issue — check the masthead/cover page.
- **Articles** — everything else: research papers, essays, poems, conference papers, clinical reports, correspondence, letters, obituaries. Default for anything not a book review or editorial.
- **Book Review Editorial** — the introductory page(s) of the book reviews section, before individual reviews start. Usually by the Book Review Editor.
- **Book Reviews** — individual book reviews. Each is a separate entry with book metadata + reviewer name.

These are the ONLY four valid sections. Do not invent others (e.g. "Poem", "Obituary", "Essay Review", "Thinking Aloud", "Conference Papers" — all go under "Articles").

### Page numbering

All page numbers are **0-based PDF page indices**. Page 0 is the first page of the PDF file. Ignore printed page numbers on the pages themselves — just count from 0 in the PDF.

Use PyMuPDF to check: `doc[N].get_text()` reads 0-based page N.

## How to create toc.json from a PDF

### Step 1: Read the CONTENTS page

Open the PDF with PyMuPDF and find the CONTENTS/TOC page (usually within the first 5 pages). This gives you all article titles, authors, and printed page numbers.

```python
import fitz
doc = fitz.open('backfill/input/23.1.pdf')
for i in range(min(10, len(doc))):
    text = doc[i].get_text()
    if 'CONTENTS' in text.upper():
        print(f'TOC on PDF page {i}')
        print(text)
        break
```

### Step 2: Map printed page numbers to PDF pages

The CONTENTS page lists printed page numbers (e.g. "Editorial ... 1"). You need to figure out which 0-based PDF page that corresponds to. Check a known page — e.g. read the first article's PDF page and verify the content matches.

### Step 3: Build the articles array

For each TOC entry:
1. Use the title and author from the CONTENTS page.
2. Set `pdf_page_start` to the 0-based PDF page where the article begins.
3. Set `pdf_page_end` to the last page of the article (typically the page before the next article starts).
4. Assign the correct section.

### Step 4: Process book reviews

Book reviews require extra work because the CONTENTS page usually just says "Book Reviews" with a single page number. You need to read the actual review pages to find:

1. **Individual review boundaries** — look for publication lines: `Author. (Year). City: Publisher.` or `*Title by Author, Publisher (Year).`
2. **Reviewer names** — usually a standalone name line at the end of each review, often in **bold**. Check the last few lines of each review, and the first few lines of the next page.
3. **Book metadata** — title, author, year, publisher from the publication line.

### Step 5: Verify

After writing toc.json, spot-check a few entries by reading the actual PDF pages:
- Does the title on the PDF page match the toc.json title?
- Are the page boundaries correct (each article starts and ends where expected)?
- Do book reviews have the correct reviewer names?

## Gotchas

### Page boundaries on book reviews
Reviews often share pages — one review ends and the next begins on the same page. Both reviews reference that shared page. The reviewer name often appears on the first page of the NEXT review, between the previous review's References section and the next review's title.

### Reviewer names
- Often in **bold** while body text is not — use `page.get_text('dict')` to check font flags.
- Can appear inline at the end of a paragraph: `"...do so. Emmy van Deurzen-Smith"`.
- Some reviews are combined (2-3 books reviewed together by one reviewer).
- Very early volumes (1-5) sometimes have no reviewer attribution.

### Title/author from TOC vs PDF
The CONTENTS page is the primary source for titles and authors. However:
- Multi-line titles may have line breaks in odd places.
- Some authors are listed on the article's first page but not the CONTENTS page.
- Book review sections on the CONTENTS page don't list individual reviews.

### Output directory naming
- Volumes 1-5 (single-issue): `backfill/output/<vol>/` (e.g. `output/3/`)
- Volumes 6+: `backfill/output/<vol>.<iss>/` (e.g. `output/23.1/`)

## Example entries

### Article
```json
{
  "title": "Towards the Cybernetic Mind",
  "authors": "Niklas Serning",
  "section": "Articles",
  "pdf_page_start": 10,
  "pdf_page_end": 17
}
```

### Book review
```json
{
  "title": "Book Review: Heidegger's Contribution to the Understanding of Work-Based Studies",
  "authors": "Mo Mandic",
  "section": "Book Reviews",
  "pdf_page_start": 165,
  "pdf_page_end": 166,
  "book_title": "Heidegger's Contribution to the Understanding of Work-Based Studies",
  "book_author": "Paul Gibbs",
  "book_year": 2011,
  "publisher": "London: Springer",
  "reviewer": "Mo Mandic"
}
```

### Book review pitfalls

When setting page boundaries for book reviews, watch for:

1. **Dual reviews of the same book** — some issues have two reviewers for one book (the Book Review Editorial intro says so). Each needs its own toc.json entry with separate page ranges.
2. **Reviewer byline is at the END** — not the start. Read the last page of each review to find the actual reviewer name.
3. **Trailing boilerplate** — "Publications received for review" and "The Society for Existential Analysis" pages are NOT article content. Don't include them in the last article's `pdf_page_end`.
4. **Reviews share pages** — one review ends and the next begins mid-page. Both the ending review AND the starting review should include the shared page in their range.
5. **Errors cascade** — when one boundary is wrong, check ALL neighbouring articles. A wrong `pdf_page_end` on one review means the next review's `pdf_page_start` may also be wrong.
6. **HTML bleed on shared pages** — when two articles share a page, the HTML galley for each will include the tail/head of the adjacent article. The `fix_html_bleed.py` tool trims book review bleed automatically. For non-book-review articles, `htmlgen.py` uses a shared-page prompt addendum to tell Haiku to extract only the right article's content.
7. **Same reviewer for consecutive reviews** — when the same person reviews two adjacent books, Haiku can't distinguish which review to extract from shared pages without the book title. `htmlgen.py` always passes the `book_title` to the prompt for book reviews.
8. **Reviewer name spelling** — always verify the reviewer name against the actual PDF byline, not the CONTENTS page. Common discrepancies: trailing periods ("Zinovieff."), spelling variants (Ticktin/Tickin, Sorensen/Sorenson).

## Lessons learned from auditing all 68 issues

These patterns were discovered by verifying every volume's book reviews against source PDFs. Read this before creating or auditing any toc.json.

### Systematic errors found across all early volumes (1-12)

1. **`pdf_page_end` consistently too short** — the single most common error. The end page stops 1-2 pages before the reviewer's actual byline. Every early volume had this. Always read the PDF to find the reviewer byline — don't guess.

2. **Missing reviews** — every early volume had 1-5 book reviews completely absent from toc.json. They exist in the PDF between listed reviews but were never catalogued. Gaps in page ranges (where one review ends at page X and the next starts at page X+3) almost always mean there's a missing review in between.

3. **Combined multi-book reviews** — when one reviewer reviews 2-4 books together in a single continuous essay, keep all book entries but give them **identical page ranges**. They share the same split PDF and HTML. Common reviewers for combined reviews: Ernesto Spinelli, Simon du Plock, Hans W. Cohn, Diana Pringle, Martin Adams.

   **Critical:** Both entries MUST have the same `pdf_page_start` and `pdf_page_end`. A common error is giving the first book a single-page range (just the shared start page) while the second book gets the full range — this produces a truncated 1-page split PDF for the first book. If you see a single-page book review followed by a multi-page review with the same reviewer and same start page, they are a combined review and both need the full range.

4. **Wrong reviewer attribution** — a systematic error where article authors from the same issue are assigned as book reviewers. The last few book reviews in an issue are most vulnerable. Always verify the reviewer byline in the actual PDF pages, not the CONTENTS page. If the listed reviewer's name doesn't appear anywhere in the review's PDF pages, it's almost certainly wrong.

### Red flags that indicate problems

- **Page gaps of 2+** between consecutive reviews — almost always means a missing review
- **Single-page reviews** (`split_pages: 1`) — suspicious unless very short. Usually means `pdf_page_end` is wrong. If the next review has the same reviewer and same start page, it's a combined review and both need the full range.
- **Identical page ranges** for two entries — correct if combined review (same reviewer). Error if different reviewers.
- **Single-page review followed by multi-page review, same reviewer, same start page** — combined review where the first entry's range was not extended. Give both entries identical ranges.
- **Reviews > 10 pages** — suspicious for a book review. Check if there are multiple reviews inside that range.
- **Consistent 2-page gaps** — systematic error where every review's end page is too short by the same amount
- **Last article ending at `total_pdf_pages - 1`** — check that the last page isn't an ISSN back cover or "Publications received" page

### Back matter that must NOT be included

The last article in an issue must NOT include these pages:
- ISSN back cover (often just "ISSN 0958-0476" or "ISSN 1752-5616")
- "The Society for Existential Analysis" blurb page
- "Publications and films received for review" listings
- Advertising rates / membership forms / subscription info
- Table of contents page at the back

If the last PDF page contains only ISSN or a Society blurb, reduce `pdf_page_end` by 1.

### Reviewer byline + references

A book review's content includes:
1. Book metadata header (title, author, publisher, year)
2. Review body text
3. Reviewer byline (standalone bold name)
4. References section (if present — comes AFTER the byline)

The `pdf_page_end` must include the References page. The HTML trim tool (`fix_html_bleed.py`) preserves references after the byline when trimming end-bleed.

### Articles sharing pages (continuous layout)

In many issues (especially early volumes), articles run continuously without page breaks. When article A ends mid-page and article B starts on the same page:
- Both A and B should include that shared page in their range
- The split PDFs will each contain the shared page (with content from both articles)
- `htmlgen.py` detects shared pages and adds a prompt addendum telling Haiku to extract only the correct article's content
- `fix_html_bleed.py` trims residual bleed from book review HTML as a safety net
- For non-book-review articles, shared-page bleed is handled by the prompt, not post-processing

### Offset calculation

toc.json uses 0-based PDF page indices. Printed page numbers in the PDF differ by a fixed offset (varies per issue, typically +1 to +5):
- To find the offset: compare a known toc.json entry's `pdf_page_start` with the printed page number visible on that PDF page
- Formula: `0_based_index = printed_page + offset`
- The offset can vary if the issue has unnumbered front matter pages

### Verification checklist

After creating or modifying a toc.json:

1. **Split PDFs exist** — run `split-issue.sh` and check all PDFs were created
2. **Title on first page** — each split PDF's first page should contain the article's title (or book title for reviews)
3. **No back matter** — last article's split PDF should not end with ISSN/Society blurb pages
4. **No gaps** — consecutive articles' page ranges should be contiguous or share a page (gaps = missing content)
5. **Reviewer bylines** — each book review's last page should contain the reviewer's name
6. **References included** — if a review has references after the byline, those pages are included in the range

### Tools

- **`backfill/fix_html_bleed.py --report`** — audit all book review HTML for bleed issues
- **`backfill/fix_html_bleed.py --trim`** — automatically trim detected bleed from book review HTML
- **`backfill/output/split-verification.json`** — tracks which volumes have been human-verified
- **`toc-confirmed.json`** — locked copy of toc.json after human approval (do not modify without asking)

### Editorial
```json
{
  "title": "Editorial",
  "authors": "Simon du Plock",
  "section": "Editorial",
  "pdf_page_start": 4,
  "pdf_page_end": 5
}
```
