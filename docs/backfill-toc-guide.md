# Backfill TOC Guide

How to create `toc.json` files for the backfill pipeline. Each issue PDF needs a `toc.json` in `backfill/output/<vol>.<iss>/` before `split-issue.sh` can process it.

## Quick version

Ask Claude: "Read `backfill/prepared/<vol>.pdf` and create `backfill/output/<vol>/toc.json` following the schema in `docs/backfill-toc-guide.md`."

## toc.json schema

```json
{
  "source_pdf": "/absolute/path/to/issue.pdf",
  "volume": 23,
  "issue": 1,
  "date": "January 2012",
  "page_offset": 3,
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
| `page_offset` | int | `pdf_page_index = journal_page_number + offset`. E.g. if journal page 1 is PDF page 4, offset = 3. |
| `total_pdf_pages` | int | Total pages in the PDF. |

### Article entry fields

Every item in the `articles` array has these fields:

| Field | Required | Description |
|---|---|---|
| `title` | yes | Article title. Book reviews: `"Book Review: <book title>"`. |
| `authors` | yes | Author name(s) as a string. For book reviews, this is the **reviewer**. Null for unsigned editorials. |
| `section` | yes | One of: `"Editorial"`, `"Articles"`, `"Book Review Editorial"`, `"Book Reviews"`, `"Obituary"`, `"Conference Papers"`, `"Poem"`. |
| `journal_page_start` | yes | First printed page number. |
| `journal_page_end` | yes | Last printed page number. |
| `pdf_page_start` | yes | 0-based PDF page index (= `journal_page_start + page_offset`). |
| `pdf_page_end` | yes | 0-based PDF page index (= `journal_page_end + page_offset`). |

Book reviews have additional fields:

| Field | Required | Description |
|---|---|---|
| `book_title` | yes | Title of the book being reviewed (without "Book Review:" prefix). |
| `book_author` | yes | Author of the book. |
| `book_year` | yes | Publication year (int). |
| `publisher` | no | Publisher, e.g. "London: Routledge". |
| `reviewer` | yes | Name of the person who wrote the review. Same as `authors`. |

### Sections

- **Editorial** — usually the first entry, often unsigned (authors: null).
- **Articles** — research papers, essays, clinical reports. Default for anything not otherwise classified.
- **Conference Papers** — papers from SEA conferences (vol 1 only).
- **Book Review Editorial** — the introductory page(s) of the book reviews section, before individual reviews start. Usually by the Book Review Editor.
- **Book Reviews** — individual book reviews. Each is a separate entry with book metadata + reviewer name.
- **Obituary** — memorial pieces.
- **Poem** — occasional poetry entries.

## How to create toc.json from a PDF

### Step 1: Read the CONTENTS page

Open the PDF with PyMuPDF and find the CONTENTS/TOC page (usually within the first 5 pages). This gives you all article titles, authors, and journal page numbers.

```python
import fitz
doc = fitz.open('backfill/prepared/23.1.pdf')
for i in range(min(10, len(doc))):
    text = doc[i].get_text()
    if 'CONTENTS' in text.upper():
        print(f'TOC on PDF page {i}')
        print(text)
        break
```

### Step 2: Determine the page offset

Find a page where you know the printed page number (e.g. the Editorial is usually journal page 1). The offset is `pdf_page_index - journal_page_number`.

Example: if the Editorial is on PDF page 4 and its printed page number is 1, then `offset = 4 - 1 = 3`.

### Step 3: Build the articles array

For each TOC entry:
1. Use the title and author from the CONTENTS page.
2. Calculate `pdf_page_start = journal_page + offset`.
3. Calculate `pdf_page_end` from the next entry's start page - 1.
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

### Fields the splitter needs
`split.py` only needs: `source_pdf`, `articles[].title`, `articles[].pdf_page_start`, `articles[].pdf_page_end`. Everything else is metadata for XML generation and the Google Sheet.

## Example entries

### Article
```json
{
  "title": "Towards the Cybernetic Mind",
  "authors": "Niklas Serning",
  "section": "Articles",
  "journal_page_start": 7,
  "journal_page_end": 14,
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
  "journal_page_start": 162,
  "journal_page_end": 164,
  "pdf_page_start": 165,
  "pdf_page_end": 166,
  "book_title": "Heidegger's Contribution to the Understanding of Work-Based Studies",
  "book_author": "Paul Gibbs",
  "book_year": 2011,
  "publisher": "London: Springer",
  "reviewer": "Mo Mandic"
}
```

### Editorial
```json
{
  "title": "Editorial",
  "authors": null,
  "section": "Editorial",
  "journal_page_start": 1,
  "journal_page_end": 2,
  "pdf_page_start": 4,
  "pdf_page_end": 5
}
```
