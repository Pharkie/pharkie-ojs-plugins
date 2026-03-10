#!/usr/bin/env python3
"""
Step 2a: Parse TOC from an issue PDF.

Extracts article titles, page numbers, and authors from the CONTENTS page,
then enriches with metadata from each article's first page (abstract, keywords).

Usage:
    python backfill/parse_toc.py <issue.pdf> [--output toc.json]

Outputs structured JSON with all articles and their metadata.
"""

import sys
import os
import re
import json
import argparse
import fitz  # PyMuPDF


# OJS section mapping
SECTION_EDITORIAL = 'Editorial'
SECTION_ARTICLES = 'Articles'
SECTION_BOOK_REVIEW_EDITORIAL = 'Book Review Editorial'
SECTION_BOOK_REVIEWS = 'Book Reviews'


def find_toc_page(doc):
    """Find the 0-based page index containing 'CONTENTS'."""
    for i in range(min(10, len(doc))):
        text = doc[i].get_text()
        if re.search(r'^CONTENTS\s*$', text, re.MULTILINE):
            return i
    return None


def find_page_offset(doc, toc_page_idx):
    """Determine offset: pdf_index = journal_page + offset.

    Looks for the EDITORIAL page (journal page 3) and computes
    the offset from there. Also checks for printed page numbers
    in headers/footers as a cross-check.
    """
    # Strategy 1: find a page with "EDITORIAL" near the top
    for test_idx in range(toc_page_idx, min(toc_page_idx + 6, len(doc))):
        text = doc[test_idx].get_text()
        # Check if EDITORIAL appears in first ~500 chars
        if re.search(r'^EDITORIAL\b', text[:500], re.MULTILINE):
            return test_idx - 3

    # Strategy 2: look for printed page numbers in headers
    # Pages often start with "journal_page\t" or "Existential Analysis...\t\npage_num"
    for test_idx in range(toc_page_idx + 1, min(toc_page_idx + 10, len(doc))):
        text = doc[test_idx].get_text()
        m = re.match(r'^(\d{1,3})\t', text)
        if m:
            printed_page = int(m.group(1))
            return test_idx - printed_page

    return toc_page_idx - 1


def journal_page_to_pdf_index(journal_page, offset):
    return journal_page + offset


def parse_toc_text(toc_text):
    """Parse the CONTENTS section into entries.

    PyMuPDF extracts the EA TOC with this pattern:
        CONTENTS
        Editorial\t          <- title (has tab)
        3                    <- page number
        Title line\t         <- title (has tab)
        7                    <- page number
        Author Name          <- author (no tab, no page num)
        continuation\t       <- continuation can have tab too, just means more title
        26                   <- page number
        title overflow       <- title continuation AFTER page num (no tab)
        Author Name          <- author

    Strategy: scan line by line, building up entries.
    - Tab line = title (or title part)
    - Bare number after title = page number → emit entry
    - Non-tab, non-number line after page number: could be title continuation or author
      We distinguish by checking if the NEXT meaningful line is a tab-title (= this is author)
      or another non-tab line followed by tab-title (= this is title overflow)
    """
    entries = []
    lines = toc_text.split('\n')

    start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == 'CONTENTS':
            start_idx = i + 1
            break
    if start_idx is None:
        return entries

    # First pass: classify each line
    classified = []  # (type, content) where type is 'title', 'page', 'text'
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            continue
        if '\t' in line:
            classified.append(('title', stripped.rstrip('\t').strip()))
        elif re.match(r'^\d{1,3}$', stripped):
            classified.append(('page', int(stripped)))
        else:
            classified.append(('text', stripped))

    # Second pass: group into entries
    # Pattern: title+ page text* (where text = title_overflow* then author?)
    i = 0
    while i < len(classified):
        kind, val = classified[i]

        if kind == 'title':
            title_parts = [val]
            i += 1

            # Collect more title parts
            while i < len(classified) and classified[i][0] == 'title':
                title_parts.append(classified[i][1])
                i += 1

            # Expect page number
            if i < len(classified) and classified[i][0] == 'page':
                page = classified[i][1]
                i += 1

                # Now collect trailing text lines until next title or page
                trailing_texts = []
                while i < len(classified) and classified[i][0] == 'text':
                    trailing_texts.append(classified[i][1])
                    i += 1

                # Determine which trailing texts are title overflow vs author
                # Heuristic: the LAST trailing text is the author (if it looks like a name)
                # Everything before it is title overflow
                author = None
                title_overflow = []

                if trailing_texts:
                    # Check if last text looks like an author name
                    last = trailing_texts[-1]
                    is_name = (
                        len(last) < 80 and
                        re.match(r'^[A-Z]', last) and
                        not last.startswith('Existential') and
                        not last.startswith('Journal') and
                        # Names have 2-5 words, possibly with & , .
                        len(last.split()) <= 8
                    )
                    if is_name and len(trailing_texts) >= 1:
                        author = last
                        title_overflow = trailing_texts[:-1]
                    else:
                        # All are title overflow, no author
                        title_overflow = trailing_texts

                full_title = ' '.join(title_parts + title_overflow)
                entries.append({
                    'title': full_title,
                    'author': author,
                    'page': page,
                })
            else:
                # No page found — skip
                pass
        else:
            i += 1  # skip orphan text/page lines

    return entries


def classify_entry(title):
    title_lower = title.lower().strip()
    if title_lower == 'editorial':
        return SECTION_EDITORIAL
    if title_lower == 'book reviews':
        return SECTION_BOOK_REVIEW_EDITORIAL
    return SECTION_ARTICLES


def extract_article_metadata(doc, pdf_start_idx, pdf_end_idx):
    """Extract abstract and keywords from article's first page(s)."""
    if pdf_start_idx >= len(doc):
        return {}

    text = ''
    for i in range(pdf_start_idx, min(pdf_start_idx + 2, pdf_end_idx, len(doc))):
        text += doc[i].get_text() + '\n'

    metadata = {}

    abstract_match = re.search(
        r'Abstract\s*\n(.*?)(?=\nKey\s*Words|\nIntroduction|\n[A-Z][a-z]+\s*\n)',
        text, re.DOTALL
    )
    if abstract_match:
        abstract = abstract_match.group(1).strip()
        abstract = re.sub(r'\s*\n\s*', ' ', abstract)
        abstract = re.sub(r'\s+', ' ', abstract)
        metadata['abstract'] = abstract

    # Extract keywords — collect lines after "Key Words" that contain commas
    # (keyword lists are comma-separated). Stop at first non-keyword line.
    kw_start = re.search(r'Key\s*Words?\s*\n', text)
    if kw_start:
        remaining = text[kw_start.end():]
        kw_lines = []
        for line in remaining.split('\n'):
            line = line.strip()
            if not line:
                break
            # Keyword lines contain commas; continuation lines start lowercase
            if ',' in line or (kw_lines and line[0].islower()):
                kw_lines.append(line)
            else:
                break
        if kw_lines:
            keywords = ' '.join(kw_lines)
            kw_list = [k.strip() for k in keywords.split(',') if k.strip()]
            metadata['keywords'] = kw_list

    return metadata


def parse_book_reviews(doc, br_start_pdf, br_end_pdf):
    """Parse individual book reviews from the Book Reviews section.

    Each review starts near the top of a page with:
        Book Title (possibly multi-line)
        Author. (Year). City: Publisher.

    We only match publication lines that appear in the first ~8 lines of a page
    to avoid matching bibliography/reference entries within review text.
    For older issues where reviews don't start on new pages, this catches most
    and it's OK to include a bit of extra text.
    """
    reviews = []
    # Match publication lines in multiple formats:
    # 1. Author. (2024). City: Publisher.     (parenthesised year, one line)
    # 2. Author. 2024. City: Publisher.       (bare year, one line)
    # 3. Author. 2024. City:                  (city at end, publisher on next line)
    # Also handles (ed). or (eds). before the year
    pub_pattern = re.compile(
        r'^(.+?(?:\(eds?\))?)\.\s*\(?(\d{4})\)?\.\s*(.+?):\s*(.+?)\.?\s*$'
    )
    # Fallback: publication line where publisher is on the next line
    pub_pattern_partial = re.compile(
        r'^(.+?(?:\(eds?\))?)\.\s*\(?(\d{4})\)?\.\s*(.+?):\s*$'
    )

    # Track which pages we've already identified as mid-review
    # to avoid matching bibliography entries at page tops
    skip_until = br_start_pdf  # don't skip anything initially

    for page_idx in range(br_start_pdf, min(br_end_pdf + 1, len(doc))):
        text = doc[page_idx].get_text()

        # Stop at backmatter / publications received / ads
        if re.search(
            r'(Advertising|Subscription)\s+Rates|Information for Contributors'
            r'|Membership of the|Publications and films received',
            text
        ):
            if reviews:
                reviews[-1]['pdf_page_end'] = page_idx - 1
            break

        lines = text.split('\n')

        # Only check first ~8 non-empty content lines for book review start.
        # This avoids matching bibliography entries deeper in review text.
        non_empty_count = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Skip page headers — only the exact journal header line
            if stripped.startswith('Existential Analysis: Journal of The Society'):
                continue
            if stripped == 'Book Reviews':
                continue
            if re.match(r'^\d+$', stripped):
                continue
            non_empty_count += 1
            if non_empty_count > 8:
                break

            m = pub_pattern.match(stripped)
            mp = pub_pattern_partial.match(stripped) if not m else None
            if m or mp:
                match = m or mp
                title_lines = []
                for j in range(i - 1, max(i - 5, -1), -1):
                    prev = lines[j].strip()
                    if not prev:
                        break
                    if prev.startswith('Existential Analysis: Journal of The Society'):
                        break
                    if prev == 'Book Reviews':
                        break
                    if re.match(r'^\d+$', prev):
                        break
                    title_lines.insert(0, prev)

                # Validate: title lines should look like a book title heading,
                # not a bibliography reference entry. Key differences:
                # - Book titles in headings don't end with periods
                # - Bibliography entries are often followed by page ranges
                # - Real titles are short lines, not paragraph text
                last_title = title_lines[-1] if title_lines else ''
                title_looks_valid = (
                    title_lines and
                    all(len(t) < 70 for t in title_lines) and
                    not any(t.endswith(',') for t in title_lines) and
                    not last_title.endswith('.')  # bibliography titles end with period
                )

                if title_looks_valid:
                    book_title = ' '.join(title_lines)
                    if reviews:
                        reviews[-1]['pdf_page_end'] = page_idx - 1

                    publisher_city = match.group(3)
                    if m:
                        publisher = f"{publisher_city}: {m.group(4)}"
                    else:
                        # Publisher is on the next line
                        next_pub = ''
                        for k in range(i + 1, min(i + 3, len(lines))):
                            nl = lines[k].strip()
                            if nl:
                                next_pub = nl.rstrip('.')
                                break
                        publisher = f"{publisher_city}: {next_pub}"

                    reviews.append({
                        'title': f"Book Review: {book_title}",
                        'book_title': book_title,
                        'book_author': match.group(1),
                        'book_year': int(match.group(2)),
                        'publisher': publisher,
                        'pdf_page_start': page_idx,
                        'section': SECTION_BOOK_REVIEWS,
                    })

    # Close final review
    if reviews and 'pdf_page_end' not in reviews[-1]:
        for page_idx in range(reviews[-1]['pdf_page_start'], min(br_end_pdf + 1, len(doc))):
            text = doc[page_idx].get_text()
            if re.search(r'(Advertising|Subscription)\s+Rates|Information for Contributors|^Membership of the',
                          text, re.MULTILINE):
                reviews[-1]['pdf_page_end'] = page_idx - 1
                break
        else:
            reviews[-1]['pdf_page_end'] = min(br_end_pdf, len(doc) - 1)

    # Extract reviewer names
    for review in reviews:
        reviewer = extract_reviewer_name(doc, review['pdf_page_start'], review['pdf_page_end'])
        if reviewer:
            review['reviewer'] = reviewer
            review['authors'] = reviewer

    return reviews


def extract_reviewer_name(doc, start_pdf, end_pdf):
    """Extract reviewer name from end of a book review."""
    if end_pdf >= len(doc):
        return None
    # Scan backwards from end, checking up to 4 pages (long reviews may have
    # multi-page reference sections between the reviewer name and the end)
    for page_idx in range(min(end_pdf, len(doc) - 1), max(start_pdf - 1, end_pdf - 4), -1):
        text = doc[page_idx].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        # Scan backwards through all lines looking for a standalone name
        for line in reversed(lines):
            if line.startswith('Existential Analysis: Journal of The Society'):
                continue
            if line == 'Book Reviews':
                continue
            if re.match(r'^\d+$', line):
                continue
            # Skip reference entries (Author. (Year). Title... or similar)
            if re.match(r'^References$', line):
                continue
            if re.search(r'\(\d{4}\)', line):  # contains (year) — reference
                continue
            if re.search(r'Vol\.\s*\d+', line):  # journal ref
                continue
            # Check for standalone name
            if re.match(r'^[A-Z][a-z]+(?:\s+(?:[A-Z]\.?\s*)?[A-Z]?[a-z\'\-]*){1,4}$', line):
                return line
            # If we hit regular body text, stop searching this page
            if len(line) > 50:
                break
    return None


def main():
    parser = argparse.ArgumentParser(description='Parse TOC from journal issue PDF')
    parser.add_argument('pdf', help='Path to issue PDF')
    parser.add_argument('--output', '-o', help='Output JSON file (default: stdout)')
    parser.add_argument('--no-metadata', action='store_true',
                        help='Skip per-article metadata extraction (faster)')
    args = parser.parse_args()

    doc = fitz.open(args.pdf)

    toc_page_idx = find_toc_page(doc)
    if toc_page_idx is None:
        print("ERROR: No CONTENTS page found", file=sys.stderr)
        sys.exit(1)
    print(f"TOC found on PDF page {toc_page_idx + 1}", file=sys.stderr)

    offset = find_page_offset(doc, toc_page_idx)
    print(f"Page offset: journal_page + {offset} = pdf_index", file=sys.stderr)

    toc_text = doc[toc_page_idx].get_text()

    raw_entries = parse_toc_text(toc_text)
    if not raw_entries:
        print("ERROR: No TOC entries found", file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(raw_entries)} TOC entries", file=sys.stderr)

    # Volume/issue/date from cover
    vol, iss, date = None, None, None
    for i in range(min(3, len(doc))):
        text = doc[i].get_text()
        if vol is None:
            m = re.search(r'(\d{1,2})\.(\d{1,2})', text)
            if m:
                v, s = int(m.group(1)), int(m.group(2))
                if 1 <= v <= 50 and 1 <= s <= 4:
                    vol, iss = v, s
        if date is None:
            months = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
            m = re.search(rf'({months})\s+(\d{{4}})', text)
            if m:
                date = f"{m.group(1)} {m.group(2)}"

    # Build article list
    articles = []
    for idx, entry in enumerate(raw_entries):
        if idx + 1 < len(raw_entries):
            end_journal = raw_entries[idx + 1]['page'] - 1
        else:
            end_journal = None

        pdf_start = journal_page_to_pdf_index(entry['page'], offset)
        pdf_end = journal_page_to_pdf_index(end_journal, offset) if end_journal else len(doc) - 1

        section = classify_entry(entry['title'])

        article = {
            'title': entry['title'],
            'authors': entry.get('author'),
            'section': section,
            'journal_page_start': entry['page'],
            'journal_page_end': end_journal,
            'pdf_page_start': pdf_start,
            'pdf_page_end': pdf_end,
        }

        if not args.no_metadata and section in (SECTION_ARTICLES, SECTION_EDITORIAL):
            meta = extract_article_metadata(doc, pdf_start, pdf_end + 1)
            article.update(meta)

        articles.append(article)

    # Split Book Reviews into editorial + individual reviews
    final_articles = []
    for article in articles:
        if article['section'] == SECTION_BOOK_REVIEW_EDITORIAL:
            br_start = article['pdf_page_start']
            br_end = article['pdf_page_end']

            individual_reviews = parse_book_reviews(doc, br_start, br_end)
            if individual_reviews:
                article['pdf_page_end'] = individual_reviews[0]['pdf_page_start'] - 1
                article['journal_page_end'] = article['pdf_page_end'] - offset

            # Extract book review editorial author — a standalone name line
            # Scan forward, find last name-like line before ERRATUM/end
            if not article.get('authors'):
                name_pattern = re.compile(
                    r'^[A-Z][a-z]+(?:\s+(?:[A-Z]\.?\s*)?[A-Z]?[a-z\'\-]*){1,4}$'
                )
                for pi in range(article['pdf_page_start'],
                                min(article['pdf_page_end'] + 1, len(doc))):
                    page_text = doc[pi].get_text()
                    page_lines = [l.strip() for l in page_text.split('\n')
                                  if l.strip()]
                    for line in page_lines:
                        if line == 'ERRATUM':
                            break  # stop before erratum
                        if name_pattern.match(line):
                            article['authors'] = line

            final_articles.append(article)
            for review in individual_reviews:
                final_articles.append(review)
        else:
            final_articles.append(article)

    output = {
        'source_pdf': os.path.abspath(args.pdf),
        'volume': vol,
        'issue': iss,
        'date': date,
        'page_offset': offset,
        'total_pdf_pages': len(doc),
        'articles': final_articles,
    }

    by_section = {}
    for a in final_articles:
        s = a['section']
        by_section[s] = by_section.get(s, 0) + 1
    print(f"\nParsed {len(final_articles)} items:", file=sys.stderr)
    for s, c in by_section.items():
        print(f"  {s}: {c}", file=sys.stderr)

    doc.close()

    result = json.dumps(output, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(result)
        print(f"\nWritten to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == '__main__':
    main()
