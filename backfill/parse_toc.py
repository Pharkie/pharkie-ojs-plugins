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
import tempfile
import fitz  # PyMuPDF


# OJS section mapping
SECTION_EDITORIAL = 'Editorial'
SECTION_ARTICLES = 'Articles'
SECTION_BOOK_REVIEW_EDITORIAL = 'Book Review Editorial'
SECTION_BOOK_REVIEWS = 'Book Reviews'

# Case-insensitive CONTENTS heading — matches "CONTENTS", "Contents",
# "--- Contents ---" with dashes/spaces
CONTENTS_RE = re.compile(r'^[-\s]*Contents[-\s]*$', re.IGNORECASE | re.MULTILINE)

# Section headers in TOC that should not be treated as article titles
SECTION_HEADERS = {
    'conference papers', 'articles', 'book reviews', 'letters',
    'responses', 'obituary', 'obituaries', 'reports', 'poem', 'poems',
}


def find_toc_page(doc):
    """Find the 0-based page index containing 'CONTENTS'."""
    for i in range(min(10, len(doc))):
        text = doc[i].get_text()
        if CONTENTS_RE.search(text):
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

    # Strategy 3: look for printed page numbers in footers
    # Many issues have a bare number as the last line of each page
    for test_idx in range(toc_page_idx + 1, min(toc_page_idx + 10, len(doc))):
        text = doc[test_idx].get_text().strip()
        lines = text.split('\n')
        if lines:
            last_line = lines[-1].strip()
            m = re.match(r'^(\d{1,3})$', last_line)
            if m:
                printed_page = int(m.group(1))
                candidate = test_idx - printed_page
                if candidate >= 0:
                    return candidate

    return None


def journal_page_to_pdf_index(journal_page, offset):
    return journal_page + offset


def detect_toc_format(toc_text):
    """Detect which TOC format is used in the text after CONTENTS heading.

    Returns one of: 'dot-leader', 'stacked', 'spaced', 'tabbed'
    """
    m = CONTENTS_RE.search(toc_text)
    if not m:
        return 'tabbed'  # fallback to existing parser
    after = toc_text[m.end():]

    # Has tab characters → tabbed format (existing parser)
    if '\t' in after:
        return 'tabbed'

    # Has clean dot-leaders (5+ dots followed by a digit)
    if re.search(r'\.{5,}\s*\d', after):
        return 'dot-leader'

    # Has inline page numbers with 3+ spaces on the same line → spaced
    # Use [ ] not \s to avoid matching across newlines
    # Must check spaced BEFORE OCR'd dot-leaders (both have long lines with pages)
    spaced_lines = re.findall(r'\S[ ]{3,}\d{1,3}\s*$', after, re.MULTILINE)
    if len(spaced_lines) >= 3:
        return 'spaced'

    # OCR'd dot-leaders: long lines (>40 chars) ending with a page number
    # In stacked format, page numbers are on their own short lines
    # Use [^\n] and [ ] to prevent matching across line breaks
    long_with_page = re.findall(r'^[^\n]{40,}[ ]+\d{1,3}[ ]*$', after, re.MULTILINE)
    if len(long_with_page) >= 3:
        return 'dot-leader'

    # Otherwise → stacked (title, author, page on separate lines)
    return 'stacked'


def _find_contents_start(lines):
    """Find the line index after the CONTENTS heading."""
    for i, line in enumerate(lines):
        if CONTENTS_RE.match(line.strip()):
            return i + 1
    return None


def _is_name_like(text):
    """Heuristic: does text look like an author name?"""
    text = text.strip()
    if not text:
        return False
    # Quick rejections
    if len(text) > 80:
        return False
    if not re.match(r'^[A-Z]', text):
        return False
    # Names don't contain em-dashes, colons, question marks, or exclamation marks
    if any(c in text for c in '\u2013\u2014:?!'):
        return False
    # Names don't end with punctuation (except period for initials)
    if text[-1] in '?:!;':
        return False
    # Names are short (2-8 words)
    words = text.split()
    if len(words) > 8 or len(words) < 1:
        return False
    # Names don't start with common title words
    first_lower = text.lower()
    title_starters = (
        'a ', 'an ', 'the ', 'on ', 'some ', 'towards', 'toward',
        'is ', 'what ', 'why ', 'how ', 'from ', 'between ', 'beyond ',
        'being ', 'not ', 'can ', 'could ', 'in ', 'of ', 'for ',
    )
    if any(first_lower.startswith(s) for s in title_starters):
        return False
    # Reject if it looks like a publication/journal name
    if text.startswith('Existential') or text.startswith('Journal'):
        return False
    # Reject if there are too many lowercase words (titles have articles/prepositions)
    lowercase_words = [w for w in words if w[0].islower() and len(w) > 3]
    if len(lowercase_words) > 2:
        return False
    # Capitalized function words in interior positions indicate a title, not a name
    # (names use lowercase: "van", "de", "du"; titles capitalize: "Of", "The", "And")
    cap_function = {'The', 'And', 'Or', 'In', 'Of', 'For', 'To', 'With', 'On', 'At', 'By', 'As'}
    if any(w in cap_function for w in words[1:]):
        return False
    return True


def _parse_toc_format_a(toc_text):
    """Parse Format A: dot-leader TOCs (Vol 1, 3–11.1).

    Pattern: author/title lines with dot-leaders to page numbers.
    Title on preceding line(s), author on the dot-leader line.
    One-liner entries like "Editorial.....1" have title=dot-line text, no author.
    """
    entries = []
    lines = toc_text.split('\n')
    start_idx = _find_contents_start(lines)
    if start_idx is None:
        return entries

    # Classify lines
    classified = []  # (type, content, page)
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            continue
        # Clean dot-leader line: text....page
        m = re.match(r'^(.+?)\.{3,}\s*(\d{1,3})\s*$', stripped)
        if m:
            text = m.group(1).strip()
            page = int(m.group(2))
            classified.append(('dot', text, page))
            continue
        # OCR'd dot-leader: text..noise..page (dots mixed with OCR garbage)
        m = re.match(r'^(.+?)(?:\s*\.{2,}).+?(\d{1,3})\s*$', stripped)
        if m and len(stripped) > 40:
            text = m.group(1).strip()
            page = int(m.group(2))
            classified.append(('dot', text, page))
            continue
        classified.append(('text', stripped, None))

    # Group into entries
    pending_texts = []
    for kind, text, page in classified:
        if kind == 'text':
            # Skip known section headers
            if text.lower().strip() in SECTION_HEADERS:
                continue
            # Skip journal header lines
            if text.startswith('Journal of the Society'):
                continue
            pending_texts.append(text)
        elif kind == 'dot':
            if pending_texts:
                # Preceding text lines = title, dot-line text = author
                title = ' '.join(pending_texts)
                author = text if _is_name_like(text) else None
                if not author:
                    # Dot-line text is part of title, not author
                    title = title + ' ' + text if text else title
                entries.append({'title': title.strip(), 'author': author, 'page': page})
            else:
                # No preceding text — dot-line text is the title (e.g. "Editorial.....1")
                entries.append({'title': text, 'author': None, 'page': page})
            pending_texts = []

    return entries


def _parse_toc_format_b_newline(toc_text):
    """Parse Format B-newline: title, author, page on separate lines.

    Two sub-orderings:
    1. title→author→page (Vol 11.2–14.1, 15.2, 17.1, 17.2):
        Title Line
        Author Name
        42

    2. title→page→author (Vol 27.2–28.2):
        Title Line
        244
        Author Name

    Also handles separated format (e.g. 14.2) where all page numbers
    are grouped at the end.
    """
    entries = []
    lines = toc_text.split('\n')
    start_idx = _find_contents_start(lines)
    if start_idx is None:
        return entries

    # Classify lines
    classified = []  # (type, content)
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            classified.append(('blank', ''))
            continue
        if re.match(r'^\d{1,3}$', stripped):
            classified.append(('page', int(stripped)))
        elif stripped.startswith('Journal of the Society'):
            continue
        else:
            classified.append(('text', stripped))

    # Check if page numbers are all grouped at the end (separated/columnar style)
    last_non_blank = [c for c in classified if c[0] != 'blank']
    page_count = 0
    for item in reversed(last_non_blank):
        if item[0] == 'page':
            page_count += 1
        else:
            break
    interleaved_pages = sum(1 for c in classified if c[0] == 'page') - page_count
    if page_count >= 5 and interleaved_pages == 0:
        return _parse_toc_format_b_separated(classified, page_count)

    # Detect ordering: look at first few non-blank items after any initial
    # text+page (Editorial/page pattern). If text→page→text-name, it's
    # title→page→author. If text→text-name→page, it's title→author→page.
    ordering = _detect_b_newline_ordering(classified)

    if ordering == 'title-page-author':
        return _parse_b_newline_tpa(classified)
    else:
        return _parse_b_newline_tap(classified)


def _detect_b_newline_ordering(classified):
    """Detect whether B-newline uses title→author→page or title→page→author."""
    non_blank = [c for c in classified if c[0] != 'blank']
    # Skip initial Editorial+page pair
    i = 0
    while i < len(non_blank) and non_blank[i][0] == 'text':
        i += 1
    if i < len(non_blank) and non_blank[i][0] == 'page':
        i += 1  # skip past first page

    # Now look at the next sequence: text(s), then page or name
    texts_seen = 0
    for j in range(i, min(i + 6, len(non_blank))):
        kind, val = non_blank[j]
        if kind == 'text':
            texts_seen += 1
        elif kind == 'page':
            # If we saw text then hit page quickly (1-2 texts), and the next
            # item is a name, it's title→page→author
            if texts_seen <= 2 and j + 1 < len(non_blank):
                next_kind, next_val = non_blank[j + 1]
                if next_kind == 'text' and _is_name_like(next_val):
                    return 'title-page-author'
            # Otherwise title→author→page (accumulated more text before page)
            return 'title-author-page'

    return 'title-author-page'  # default


def _parse_b_newline_tap(classified):
    """Parse B-newline: title→author→page ordering."""
    entries = []
    pending_texts = []
    for kind, val in classified:
        if kind == 'blank':
            continue
        if kind == 'text':
            pending_texts.append(val)
        elif kind == 'page':
            if not pending_texts:
                continue
            # Last text line before page = author (if it looks like a name)
            author = None
            title_parts = pending_texts[:]
            if len(pending_texts) >= 2 and _is_name_like(pending_texts[-1]):
                author = pending_texts[-1]
                title_parts = pending_texts[:-1]

            title = ' '.join(title_parts)
            entries.append({'title': title, 'author': author, 'page': val})
            pending_texts = []

    return entries


def _parse_b_newline_tpa(classified):
    """Parse B-newline: title→page→author ordering.

    Forward scan: collect title lines → page number → subtitle lines → author.
    If no author is found before the next page number, any text collected after
    the page was actually the next entry's title, not subtitles.
    """
    entries = []
    items = [(k, v) for k, v in classified if k != 'blank']
    i = 0
    # Seed: collect initial title
    carry_title = []

    while i < len(items):
        # Collect title lines (or use carried-over title from previous iteration)
        title_parts = carry_title[:]
        carry_title = []
        while i < len(items) and items[i][0] == 'text':
            title_parts.append(items[i][1])
            i += 1

        # Expect page number
        if i >= len(items) or items[i][0] != 'page':
            break
        page = items[i][1]
        i += 1

        # Collect subtitle continuations and author after the page
        subtitle_parts = []
        author = None
        while i < len(items) and items[i][0] == 'text':
            if _is_name_like(items[i][1]) and author is None:
                author = items[i][1]
                i += 1
                break  # author found — next text is next entry's title
            subtitle_parts.append(items[i][1])
            i += 1

        if author is not None:
            # Found author: subtitles are part of this entry's title
            full_title = ' '.join(title_parts + subtitle_parts)
            entries.append({'title': full_title, 'author': author, 'page': page})
        else:
            # No author found: subtitle_parts are actually the next entry's title
            entries.append({'title': ' '.join(title_parts), 'author': None, 'page': page})
            carry_title = subtitle_parts

    # Flush any remaining carry_title as a title-only entry (shouldn't happen normally)
    return entries


def _parse_toc_format_b_separated(classified, page_count):
    """Parse B-newline variant where all page numbers are at the end.

    Strategy: collect text entries (separated by author-name heuristic),
    then pair with page numbers in order.
    """
    # Extract page numbers from end
    pages = []
    for item in reversed([c for c in classified if c[0] != 'blank']):
        if item[0] == 'page':
            pages.insert(0, item[1])
        else:
            break

    # Extract text entries — group into (title, author) pairs
    # An entry ends when we see a name-like line followed by another
    # non-name line or a blank gap
    text_lines = []
    for kind, val in classified:
        if kind == 'text':
            text_lines.append(val)
        elif kind == 'page':
            break  # stop at first page number

    # Group text lines into entries using author-name heuristic
    raw_entries = []
    current = []
    for line in text_lines:
        current.append(line)
        # If this line looks like an author name, close the entry
        if _is_name_like(line) and len(current) >= 2:
            raw_entries.append(current[:])
            current = []

    # Remaining lines (like "Book Reviews", "Letters to the Editors") are
    # single-line entries without authors
    if current:
        for line in current:
            raw_entries.append([line])

    # Pair with page numbers
    entries = []
    for i, group in enumerate(raw_entries):
        if i >= len(pages):
            break
        if len(group) >= 2 and _is_name_like(group[-1]):
            author = group[-1]
            title = ' '.join(group[:-1])
        else:
            author = None
            title = ' '.join(group)
        entries.append({'title': title, 'author': author, 'page': pages[i]})

    return entries


def _parse_toc_format_b_spaced(toc_text):
    """Parse Format B-spaced: inline page numbers with spaces (Vol 15.1–23.1, 27.2–28.2).

    Pattern:
        Title Text                                                    42
        Subtitle if any
        Author Name

    Blank lines separate entries.
    """
    entries = []
    lines = toc_text.split('\n')
    start_idx = _find_contents_start(lines)
    if start_idx is None:
        return entries

    # Parse entries separated by blank lines
    current_group = []
    groups = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            if current_group:
                groups.append(current_group)
                current_group = []
        else:
            if stripped.startswith('Journal of the Society'):
                continue
            current_group.append(stripped)
    if current_group:
        groups.append(current_group)

    for group in groups:
        if not group:
            continue

        # Try to extract inline page number from first line
        page = None
        title_parts = []
        author = None

        # Check first line for inline page: "Title text       42"
        m = re.match(r'^(.+?)\s{3,}(\d{1,3})\s*$', group[0])
        if m:
            title_parts.append(m.group(1).strip())
            page = int(m.group(2))
        else:
            # No inline page — might be on a subsequent line as bare number
            title_parts.append(group[0])

        # Process remaining lines: subtitles, author, or bare page number
        for line in group[1:]:
            bare_page = re.match(r'^(\d{1,3})$', line)
            if bare_page and page is None:
                page = int(bare_page.group(1))
            elif _is_name_like(line):
                author = line
            else:
                # Subtitle or title continuation
                # Check if it has an inline page
                m2 = re.match(r'^(.+?)\s{3,}(\d{1,3})\s*$', line)
                if m2 and page is None:
                    title_parts.append(m2.group(1).strip())
                    page = int(m2.group(2))
                else:
                    title_parts.append(line)

        if page is None:
            continue  # skip entries without page numbers

        title = ' '.join(title_parts)
        entries.append({'title': title, 'author': author, 'page': page})

    return entries


def _parse_toc_format_c(toc_text):
    """Parse Format C: tab-based TOCs (Vol 23.2–36.1).

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

    start_idx = _find_contents_start(lines)
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


def parse_toc_text(toc_text):
    """Parse the CONTENTS section into entries.

    Detects the TOC format and dispatches to the appropriate parser.
    All parsers return [{title, author, page}, ...].
    """
    fmt = detect_toc_format(toc_text)
    print(f"TOC format: {fmt}", file=sys.stderr)

    if fmt == 'dot-leader':
        return _parse_toc_format_a(toc_text)
    elif fmt == 'stacked':
        return _parse_toc_format_b_newline(toc_text)
    elif fmt == 'spaced':
        return _parse_toc_format_b_spaced(toc_text)
    else:
        return _parse_toc_format_c(toc_text)


def classify_entry(title):
    """Classify a TOC entry into an OJS section.

    Returns a (section, skip) tuple where skip=True means the entry
    should be omitted from output (e.g. 'Notes on Contributors').
    """
    title_lower = title.lower().strip()
    if title_lower == 'editorial':
        return (SECTION_EDITORIAL, False)
    if title_lower == 'book reviews':
        return (SECTION_BOOK_REVIEW_EDITORIAL, False)
    # Obituaries and errata are free content, filed under Editorial
    if title_lower in ('obituary', 'erratum', 'errata') or title_lower.startswith('obituary:'):
        return (SECTION_EDITORIAL, False)
    # Correspondence / letters are articles
    if title_lower in ('correspondence', 'letters'):
        return (SECTION_ARTICLES, False)
    # Contributors lists — skip these from output
    if title_lower in ('contributors', 'notes on contributors'):
        return (SECTION_EDITORIAL, True)
    # Known article-like patterns: anything with substantial text
    # Warn on short/unusual titles that might be new section types
    known_section_words = {
        'editorial', 'book reviews', 'obituary', 'erratum', 'errata',
        'correspondence', 'letters', 'contributors', 'notes on contributors',
    }
    if title_lower not in known_section_words and len(title_lower.split()) <= 2:
        print(f"WARNING: Unknown short TOC entry '{title}' — classifying as Articles. "
              f"May be a new section type.", file=sys.stderr)
    return (SECTION_ARTICLES, False)


def extract_article_metadata(doc, pdf_start_idx, pdf_end_idx):
    """Extract abstract and keywords from article's first page(s)."""
    if pdf_start_idx >= len(doc):
        return {}

    text = ''
    for i in range(pdf_start_idx, min(pdf_start_idx + 2, pdf_end_idx, len(doc))):
        text += doc[i].get_text() + '\n'

    metadata = {}

    # Abstract: match "Abstract" heading, capture until next section heading.
    # Handles both "Abstract\n text..." and "Abstract: text..." formats.
    # Terminators: "Key Words", "Keywords", "Introduction", or any capitalised heading.
    abstract_match = re.search(
        r'Abstract[:\s]*\n(.*?)(?=\nKey\s*Words?|\nKeywords?|\nIntroduction|\n[A-Z][a-z]+\s*\n)',
        text, re.DOTALL
    )
    if not abstract_match:
        # Try inline form: "Abstract: text..." or "Abstract text..." on same line
        abstract_match = re.search(
            r'Abstract[:\s]+(.*?)(?=\nKey\s*Words?|\nKeywords?|\nIntroduction|\n[A-Z][a-z]+\s*\n)',
            text, re.DOTALL
        )
    if abstract_match:
        abstract = abstract_match.group(1).strip()
        abstract = re.sub(r'\s*\n\s*', ' ', abstract)
        abstract = re.sub(r'\s+', ' ', abstract)
        if abstract:
            metadata['abstract'] = abstract

    # Extract keywords — collect lines after keyword heading.
    # Handles "Key Words", "Keywords", "Key Word" with optional colon/newline.
    # Supports both comma-separated and semicolon-separated keywords.
    kw_start = re.search(r'Key\s*Words?[:\s]*\n|Keywords?[:\s]*\n', text)
    if kw_start:
        remaining = text[kw_start.end():]
        kw_lines = []
        for line in remaining.split('\n'):
            line = line.strip()
            if not line:
                break
            # First line after heading is always a keyword line.
            # Subsequent lines need delimiters or lowercase start (continuation).
            if not kw_lines or ',' in line or ';' in line or line[0].islower():
                kw_lines.append(line)
            else:
                break
        if kw_lines:
            keywords = ' '.join(kw_lines)
            # Split on whichever delimiter is present (prefer semicolons if both)
            if ';' in keywords:
                kw_list = [k.strip() for k in keywords.split(';') if k.strip()]
            else:
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
    parser.add_argument('--page-offset', type=int, default=None,
                        help='Manual page offset (pdf_index = journal_page + offset). '
                             'Use when auto-detection fails.')
    args = parser.parse_args()

    doc = fitz.open(args.pdf)

    toc_page_idx = find_toc_page(doc)
    if toc_page_idx is None:
        print("ERROR: No CONTENTS page found", file=sys.stderr)
        sys.exit(1)
    print(f"TOC found on PDF page {toc_page_idx + 1}", file=sys.stderr)

    if args.page_offset is not None:
        offset = args.page_offset
        print(f"Page offset (manual): journal_page + {offset} = pdf_index", file=sys.stderr)
    else:
        offset = find_page_offset(doc, toc_page_idx)
        if offset is None:
            print(
                "ERROR: Could not determine page offset automatically.\n"
                "  Strategy 1 (find EDITORIAL heading on journal page 3): no match.\n"
                "  Strategy 2 (find printed page numbers in headers): no match.\n"
                "\n"
                "Please supply the offset manually with --page-offset=N\n"
                "  where pdf_index = journal_page + N.\n"
                "  (e.g. if journal page 3 is on PDF page 5, then N = 2)",
                file=sys.stderr
            )
            sys.exit(1)
        print(f"Page offset: journal_page + {offset} = pdf_index", file=sys.stderr)

    toc_text = doc[toc_page_idx].get_text()

    raw_entries = parse_toc_text(toc_text)
    if not raw_entries:
        print("ERROR: No TOC entries found", file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(raw_entries)} TOC entries", file=sys.stderr)

    # Volume/issue/date from cover
    # Try two-issue format on all pages first, then single-issue fallback.
    # This avoids "ANALYSIS 1 0 .2" (garbled OCR) matching single-issue "1".
    vol, iss, date = None, None, None
    for i in range(min(3, len(doc))):
        text = doc[i].get_text()
        if vol is None:
            m = re.search(r'(\d{1,2})\.(\d{1,2})', text)
            if m:
                v, s = int(m.group(1)), int(m.group(2))
                if 1 <= v <= 50 and 1 <= s <= 4:
                    vol, iss = v, s
    if vol is None:
        for i in range(min(3, len(doc))):
            text = doc[i].get_text()
            m = re.search(r'Analysis\s+(\d{1,2})\s', text, re.IGNORECASE)
            if m:
                v = int(m.group(1))
                if 1 <= v <= 50:
                    vol, iss = v, 1
                    break
    for i in range(min(3, len(doc))):
        text = doc[i].get_text()
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

        section, skip = classify_entry(entry['title'])
        if skip:
            continue

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

    # Validate page ranges
    validated_articles = []
    for idx, article in enumerate(articles):
        start = article['pdf_page_start']
        end = article['pdf_page_end']

        # Check for backwards ranges (end < start)
        if end < start:
            print(f"WARNING: Skipping '{article['title']}' — backwards page range "
                  f"(pdf pages {start}–{end})", file=sys.stderr)
            continue

        # Check for overlapping ranges with previous article
        if validated_articles:
            prev = validated_articles[-1]
            if start <= prev['pdf_page_end']:
                old_end = prev['pdf_page_end']
                prev['pdf_page_end'] = start - 1
                prev['journal_page_end'] = prev['pdf_page_end'] - offset
                print(f"WARNING: Overlapping page ranges — '{prev['title']}' end adjusted "
                      f"from pdf page {old_end} to {prev['pdf_page_end']}", file=sys.stderr)

        validated_articles.append(article)
    articles = validated_articles

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
        out_dir = os.path.dirname(os.path.abspath(args.output))
        tmp_fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix='.json.tmp')
        try:
            with os.fdopen(tmp_fd, 'w') as f:
                f.write(result)
            os.replace(tmp_path, args.output)
        except BaseException:
            os.unlink(tmp_path)
            raise
        print(f"\nWritten to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == '__main__':
    main()
