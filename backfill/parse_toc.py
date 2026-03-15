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

# Section headers in TOC — Format A uses these to skip section dividers
# in dot-leader TOCs (e.g. "Articles", "Conference Papers" headings)
_FORMAT_A_SKIP_HEADERS = frozenset({
    'conference papers', 'articles', 'reports', 'poem', 'poems',
})

# Title conjunctions/prepositions — shared across Format A and book review
# parsing to detect title continuations split at line breaks
_TITLE_CONJUNCTIONS = frozenset({
    'and', 'of', 'the', 'in', 'for', 'on', 'to',
    'a', 'an', 'as', 'at', 'or', 'with', 'by',
})

# Known academic publisher cities and publishers — used to strip
# publisher info from book_author fields and build pub_split regex
_KNOWN_CITIES = frozenset({
    'Princeton', 'Oxford', 'Cambridge', 'London', 'New York',
    'Edinburgh', 'Bristol', 'Chichester', 'Hove', 'Basingstoke',
    'Buckingham', 'Maidenhead', 'Milton Keynes', 'Philadelphia',
    'Boston', 'Chicago', 'San Francisco', 'Los Angeles', 'Berkeley',
    'Durham', 'Harlow', 'Harmondsworth', 'Thousand Oaks', 'Palo Alto',
    'Lanham',
})

_KNOWN_PUBLISHERS = frozenset({
    'Sage', 'Routledge', 'Penguin', 'Vintage', 'Karnac', 'PCCS', 'Erlbaum',
})

# Backmatter markers — stop parsing book reviews at these boundaries
# Heuristic thresholds for name/title detection
_MAX_NAME_LENGTH = 80
_MAX_NAME_WORDS = 8
_BODY_TEXT_MIN_LENGTH = 50

_BACKMATTER_RE = re.compile(
    r'(Advertising|Subscription)\s+Rates|Information for Contributors'
    r'|Membership of the|Publications and films received'
)

# Reviewer name pattern — standalone name at end of review
_REVIEWER_NAME_RE = re.compile(
    r'^[A-Z][a-z]+(?:\s+(?:[A-Z]\.?\s*)?[A-Z]?[a-z\'\-]*){1,4}$'
)

# Publisher/city split — strips publisher info from book_author field
_PUB_SPLIT_RE = re.compile(
    r'^(.+?)\.\s+((?:'
    + '|'.join(re.escape(c) for c in sorted(_KNOWN_CITIES, key=len, reverse=True))
    + '|'
    + '|'.join(re.escape(p) for p in sorted(_KNOWN_PUBLISHERS))
    + r'|[A-Z][a-z]+\s+University\s+Press|University\s+of).*)$'
)

# Book review publication line patterns — compiled once, used by
# _parse_book_reviews_by_regex() and _match_pub_line()
# Standard: Author. (2024). City: Publisher.
_PUB_STANDARD_RE = re.compile(
    r'^(.+?(?:\(eds?\))?)[.,]\s*\(?(\d{4})\)?\.\s*(.+?):\s*(.+?)\.?\s*$'
)
# Partial: publisher on next line
_PUB_PARTIAL_RE = re.compile(
    r'^(.+?(?:\(eds?\))?)[.,]\s*\(?(\d{4})\)?\.\s*(.+?):\s*$'
)
# Year-first: "2024. Author. City: Publisher."
_PUB_YEAR_FIRST_RE = re.compile(
    r'^\(?(\d{4})\)?\.\s*(.+?)\.\s*(.+?):\s*(.+?)\.?\s*$'
)
_PUB_YEAR_FIRST_PARTIAL_RE = re.compile(
    r'^\(?(\d{4})\)?\.\s*(.+?)\.\s*(.+?):\s*$'
)
# Relaxed: "Author (Year). City: Publisher"
_PUB_RELAXED_RE = re.compile(
    r'^(.+?)\s+\((\d{4})\)\.?\s+(.+?):\s*(.+?)\.?\s*$'
)
_PUB_RELAXED_PARTIAL_RE = re.compile(
    r'^(.+?)\s+\((\d{4})\)\.?\s+(.+?):\s*$'
)
# No city: "Author (Year) Publisher"
_PUB_NOCITY_RE = re.compile(
    r'^(.+?)\s+\((\d{4})\)\.?\s+([A-Z][A-Za-z &]+?)(?:\s+[\d£€$].+)?\.?\s*$'
)
# 1990s inverted: "Title by Author, Publisher (Year)."
# After year+paren, allow optional page-count/price info (e.g. "599 pp, £20.00")
_PUB_INVERTED_A_RE = re.compile(
    r'^\*?\s*(.+?)[\s.]+[Bb]y\s+(.+?),\s*(.+?)\s*[\(,]\s*(\d{4})\)?'
    r'(?:\s+\d+\s*pp[^.]*)?[.,]'
)
# Inverted B: "Title by Author. Year. Publisher."
_PUB_INVERTED_B_RE = re.compile(
    r'^\*?\s*(.+?)[\s.]+[Bb]y\s+(.+?)\.\s*(\d{4})[.,]\s*(.+?)\.?'
)
# Inverted C: "Title by Author. Publisher, City, Year."
_PUB_INVERTED_C_RE = re.compile(
    r'^\*?\s*(.+?)[\s.]+[Bb]y\s+(.+?)\.\s*(.+?),\s*(\d{4})[.,]'
)

# Star review patterns
_STAR_LINE_RE = re.compile(r'^\s*★\s*(.+)$')
_YEAR_SPLIT_RE = re.compile(r'[.,]\s*(\d{4})[¹²³⁴⁵⁶⁷⁸⁹⁰]*[.,\s]\s*(.+)$')

_KNOWN_AUTHORS = None
_KNOWN_AUTHORS_NORMALIZED = None


def _get_known_authors():
    """Lazy-load the set of all known author names from authors.json."""
    global _KNOWN_AUTHORS
    if _KNOWN_AUTHORS is None:
        _KNOWN_AUTHORS = set()
        authors_path = os.path.join(os.path.dirname(__file__), 'authors.json')
        try:
            with open(authors_path) as f:
                registry = json.load(f)
            for name, info in registry.items():
                _KNOWN_AUTHORS.add(name)
                for variant in info.get('variants', []):
                    _KNOWN_AUTHORS.add(variant)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return _KNOWN_AUTHORS


def _get_known_authors_normalized():
    """Lazy-load a dict mapping normalized (stripped, title-cased) names to originals."""
    global _KNOWN_AUTHORS_NORMALIZED
    if _KNOWN_AUTHORS_NORMALIZED is None:
        _KNOWN_AUTHORS_NORMALIZED = {}
        for name in _get_known_authors():
            key = name.strip().title()
            _KNOWN_AUTHORS_NORMALIZED[key] = name
    return _KNOWN_AUTHORS_NORMALIZED


def _match_known_author(line):
    """Check if line matches a known author (exact or normalized).

    Returns the canonical name if matched, else None.
    """
    stripped = line.strip()
    if stripped in _get_known_authors():
        return stripped
    normalized = _get_known_authors_normalized()
    key = stripped.title()
    if key in normalized:
        return normalized[key]
    return None


def find_toc_page(doc):
    """Find the 0-based page index containing 'CONTENTS'."""
    for i in range(min(10, len(doc))):
        text = doc[i].get_text()
        if CONTENTS_RE.search(text):
            return i
    return None


def _extract_printed_page(text):
    """Extract printed page number from a page's text.

    Looks for standalone numbers in headers (first line) or footers
    (last line), or tab-separated numbers.  Returns int or None.
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return None
    # Header: bare number as first line, or "number\t"
    m = re.match(r'^(\d+)\s*$', lines[0])
    if m:
        return int(m.group(1))
    m = re.match(r'^(\d+)\t', lines[0])
    if m:
        return int(m.group(1))
    # Footer: bare number as last line
    m = re.match(r'^(\d+)$', lines[-1])
    if m:
        return int(m.group(1))
    # After journal header: "Existential Analysis...\n215\n"
    for line in lines[:3]:
        if re.match(r'^\d+$', line):
            return int(line)
    return None


def find_page_offset(doc, toc_page_idx):
    """Determine offset: pdf_index = journal_page + offset.

    Finds the printed page number on a known page and computes
    offset = pdf_index - printed_page.  Works for both .1 issues
    (printed pages starting at ~3) and .2 issues (pages continuing
    from previous issue, e.g. starting at 215).
    """
    # Strategy 1: find a page with "EDITORIAL" near the top,
    # then read its printed page number
    for test_idx in range(toc_page_idx, min(toc_page_idx + 6, len(doc))):
        text = doc[test_idx].get_text()
        if re.search(r'^EDITORIAL\b', text[:500], re.MULTILINE):
            printed = _extract_printed_page(text)
            if printed is not None:
                return test_idx - printed
            # Fallback: assume editorial is on printed page 3
            return test_idx - 3

    # Strategy 2: look for printed page numbers on pages near TOC
    for test_idx in range(toc_page_idx + 1, min(toc_page_idx + 10, len(doc))):
        printed = _extract_printed_page(doc[test_idx].get_text())
        if printed is not None:
            return test_idx - printed

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
    if len(text) > _MAX_NAME_LENGTH:
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
    if len(words) > _MAX_NAME_WORDS or len(words) < 1:
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
    # Reject if it looks like a publication/journal name or section header
    if text.startswith('Existential') or text.startswith('Journal'):
        return False
    if text.lower() in _FORMAT_B_SECTION_HEADERS | {'references', 'bibliography'}:
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


def _is_reviewer_name_like(line):
    """More permissive name check for reviewer bylines.

    Strips title prefixes (Dr, Prof, Professor), allows single-word names,
    tolerates up to 10 words (handles "Dr John Smith MA PhD"), but still
    rejects lines with sentence punctuation or title-word starters.
    """
    text = line.strip()
    if not text:
        return False
    if len(text) > _MAX_NAME_LENGTH:
        return False
    # Reject sentence punctuation
    if any(c in text for c in ':?!'):
        return False
    # Strip title prefixes
    for prefix in ('Dr ', 'Prof ', 'Professor '):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if not text or not text[0].isupper():
        return False
    words = text.split()
    if len(words) < 1 or len(words) > 10:
        return False
    # Reject title-word starters (same as _is_name_like)
    first_lower = text.lower()
    title_starters = (
        'a ', 'an ', 'the ', 'on ', 'some ', 'towards', 'toward',
        'is ', 'what ', 'why ', 'how ', 'from ', 'between ', 'beyond ',
        'being ', 'not ', 'can ', 'could ', 'in ', 'of ', 'for ',
    )
    if any(first_lower.startswith(s) for s in title_starters):
        return False
    # Reject journal/section names
    if text.startswith('Existential') or text.startswith('Journal'):
        return False
    if text.lower() in _FORMAT_B_SECTION_HEADERS | {'references', 'bibliography'}:
        return False
    # Allow single-word names but reject if all lowercase
    if len(words) == 1:
        return words[0][0].isupper()
    # Reject too many lowercase content words (body text)
    lowercase_words = [w for w in words if w[0].islower() and len(w) > 3]
    if len(lowercase_words) > 2:
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
        # Clean dot-leader line: text....page (dots or Unicode ellipsis …)
        m = re.match(r'^(.+?)[.\u2026]{3,}\s*(\d{1,3})\s*$', stripped)
        if m:
            text = m.group(1).strip()
            page = int(m.group(2))
            classified.append(('dot', text, page))
            continue
        # OCR'd dot-leader: text..noise..page (dots mixed with OCR garbage)
        m = re.match(r'^(.+?)(?:\s*[.\u2026]{2,}).+?(\d{1,3})\s*$', stripped)
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
            if text.lower().strip() in _FORMAT_A_SKIP_HEADERS:
                continue
            # Skip journal header lines
            if text.lower().startswith('journal of the society'):
                continue
            # If no pending texts and previous entry has no author,
            # this text may be the author of the previous entry
            if (not pending_texts and entries and
                    entries[-1].get('author') is None and
                    _is_name_like(text)):
                entries[-1]['author'] = text
            else:
                pending_texts.append(text)
        elif kind == 'dot':
            if pending_texts:
                # Preceding text lines = title, dot-line text = author
                title = ' '.join(pending_texts)
                # If the pending title ends with a conjunction/preposition,
                # the dot-leader text is a title continuation, not an author
                last_word = title.rsplit(None, 1)[-1].lower() if title else ''
                if last_word in _TITLE_CONJUNCTIONS:
                    author = None
                else:
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
        elif stripped.lower().startswith('journal of the society'):
            continue
        else:
            # Detect inline page numbers with large whitespace gap
            # e.g. "All the lonely people...                    168"
            m_inline = re.match(r'^(.+?)\s{3,}(\d{1,3})\s*$', stripped)
            if m_inline:
                title_text = m_inline.group(1).strip()
                if title_text:
                    classified.append(('text', title_text))
                classified.append(('page', int(m_inline.group(2))))
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


def _is_strong_name(text):
    """Stricter name check for ordering detection — must look like a person name.

    Unlike _is_name_like (which permits ambiguous cases), this checks for
    structures that are clearly person names: first+last pattern, initials,
    hyphenated surnames, etc.  Used for ordering detection and author assignment
    where false positives cause cascading errors.
    """
    if not _is_name_like(text):
        return False
    text = text.strip()
    words = text.split()
    if len(words) < 2 or len(words) > 5:
        return False
    if len(text) > 40:
        return False
    # Must not contain punctuation typical of titles
    if any(c in text for c in ':?!;"\u2013\u2014'):
        return False
    # Reject if any word is a common English noun/adjective (title words)
    _title_words = {
        'analysis', 'approach', 'attachment', 'being', 'clinical', 'comments',
        'consciousness', 'crisis', 'death', 'dialogue', 'dream', 'essay',
        'essentials', 'ethics', 'evil', 'existential', 'experience', 'heresy',
        'history', 'ideas', 'identity', 'imaginative', 'intersubjectivity',
        'introduction', 'it', 'meaning', 'meeting', 'migration', 'model',
        'new', 'other', 'overview', 'perspectives', 'phenomenological',
        'philosophy', 'practice', 'psychotherapy', 'radical', 'relationship',
        'review', 'sexuality', 'society', 'some', 'therapeutic', 'therapy',
        'three', 'time', 'towards', 'transference', 'unconscious',
        'variations', 'world',
    }
    for w in words:
        if w.lower() in _title_words:
            return False
    # At least one word should look like a first name or initial pattern
    # (single letter + period, or not in common words)
    has_name_pattern = any(
        re.match(r'^[A-Z]\.$', w) or  # Initial: "R.", "J."
        re.match(r'^[A-Z][a-z]{2,}$', w)  # Capitalized word ≥3 chars
        for w in words
    )
    return has_name_pattern


def _detect_b_newline_ordering(classified):
    """Detect whether B-newline uses title→author→page or title→page→author.

    Strategy: count how often strong name-like text appears BEFORE vs AFTER
    page numbers. Uses stricter name detection to avoid false positives from
    short title strings that pass the regular _is_name_like check.
    """
    non_blank = [c for c in classified if c[0] != 'blank']

    # Count name-before-page vs name-after-page across all entries
    name_before = 0
    name_after = 0
    for j, (kind, val) in enumerate(non_blank):
        if kind != 'page':
            continue
        # Check if text immediately before page is name-like
        if j > 0 and non_blank[j - 1][0] == 'text' and _is_strong_name(non_blank[j - 1][1]):
            name_before += 1
        # Check if text immediately after page is name-like
        if j + 1 < len(non_blank) and non_blank[j + 1][0] == 'text' and _is_strong_name(non_blank[j + 1][1]):
            name_after += 1

    if name_after > name_before:
        return 'title-page-author'
    return 'title-author-page'


_FORMAT_B_SECTION_HEADERS = {
    'book reviews', 'book review', 'editorial', 'obituary', 'obituaries',
    'correspondence', 'letters', 'contributors', 'notes on contributors',
}


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

            # Check if any pending text is a section header (e.g. "Book Reviews").
            # If so, split: text before it becomes author of previous entry,
            # the header becomes its own entry with this page number.
            split_idx = None
            for si, pt in enumerate(pending_texts):
                if pt.lower().strip() in _FORMAT_B_SECTION_HEADERS:
                    split_idx = si
                    break

            if split_idx is not None and split_idx > 0:
                # Text before the section header = author of previous entry
                pre_header = pending_texts[:split_idx]
                if entries and entries[-1].get('author') is None and len(pre_header) == 1 and _is_name_like(pre_header[0]):
                    entries[-1]['author'] = pre_header[0]
                else:
                    # Multiple pre-header lines or no previous entry — treat as separate entry
                    title = ' '.join(pre_header)
                    entries.append({'title': title, 'author': None, 'page': val})
                # Section header becomes its own entry
                header_title = pending_texts[split_idx]
                # Any text after the header in pending_texts becomes part of header entry
                remaining = pending_texts[split_idx + 1:]
                author = None
                if remaining and _is_name_like(remaining[-1]):
                    author = remaining[-1]
                entries.append({'title': header_title, 'author': author, 'page': val})
            else:
                # Normal case: no section header in pending texts
                if split_idx == 0:
                    # Section header is the first/only text — just use it as title
                    title = pending_texts[0]
                    author = None
                    if len(pending_texts) >= 2 and _is_name_like(pending_texts[-1]):
                        author = pending_texts[-1]
                    entries.append({'title': title, 'author': author, 'page': val})
                else:
                    # Find the author among pending texts.
                    # Prefer the last strong-name; fall back to last name-like.
                    # When multiple name-like texts exist, use the last one that
                    # passes the stricter _is_strong_name check (2-4 word name
                    # with all-caps initials) to avoid misidentifying short titles.
                    author = None
                    author_idx = None
                    if len(pending_texts) >= 2:
                        # Try strong name first (last match wins)
                        for si in range(len(pending_texts) - 1, 0, -1):
                            if _is_strong_name(pending_texts[si]):
                                author = pending_texts[si]
                                author_idx = si
                                break
                        # Fall back to regular name-like (last text only)
                        if author is None and _is_name_like(pending_texts[-1]):
                            author = pending_texts[-1]
                            author_idx = len(pending_texts) - 1
                    title_parts = pending_texts[:author_idx] if author_idx is not None else pending_texts[:]
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
            # If this text is a section header and we already have title parts,
            # the preceding text may be the author of the previous entry
            if items[i][1].lower().strip() in _FORMAT_B_SECTION_HEADERS and title_parts:
                # Assign preceding name-like text as author of previous entry
                if (entries and entries[-1].get('author') is None
                        and len(title_parts) == 1 and _is_name_like(title_parts[0])):
                    entries[-1]['author'] = title_parts[0]
                elif title_parts:
                    # Not a single name — keep as carry for a title-only entry
                    # (will be flushed as a separate entry)
                    pass
                title_parts = []  # reset — section header starts fresh
            title_parts.append(items[i][1])
            i += 1

        # Expect page number
        if i >= len(items) or items[i][0] != 'page':
            break
        page = items[i][1]
        i += 1

        # Collect subtitle continuations and author after the page.
        # Use _is_strong_name for the first candidate after the page to
        # avoid misidentifying short titles as authors.
        subtitle_parts = []
        author = None
        while i < len(items) and items[i][0] == 'text':
            # First text after page: use stricter check to avoid false positives.
            # Subsequent texts: use regular _is_name_like (less ambiguous by position).
            name_check = _is_strong_name if not subtitle_parts else _is_name_like
            if name_check(items[i][1]) and author is None:
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

    Two sub-formats based on PDF source:
    - Original PDFs: blank lines separate entries → _parse_spaced_groups()
    - Re-OCR'd PDFs: no blank lines, entries run together → _parse_spaced_no_blanks()

    Re-OCR'd sub-format further splits into:
    - Format A (14.1, 14.2): page numbers on title lines, authors on separate lines
    - Format B (13.1): page numbers on author lines, titles on separate lines
    """
    entries = []
    lines = toc_text.split('\n')
    start_idx = _find_contents_start(lines)
    if start_idx is None:
        return entries

    # Collect lines into groups separated by blank lines
    current_group = []
    groups = []
    has_blank_separators = False
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            if current_group:
                groups.append(current_group)
                current_group = []
                has_blank_separators = True
        else:
            if stripped.lower().startswith('journal of the society'):
                continue
            if stripped.startswith('Existential Analysis'):
                continue
            current_group.append(stripped)
    if current_group:
        groups.append(current_group)

    # If blank-line grouping produced multiple groups, use group-based parser
    if has_blank_separators and len(groups) >= 3:
        return _parse_spaced_groups(groups)

    # No blank-line separators (re-OCR'd text) — parse by structure
    all_lines = []
    for group in groups:
        all_lines.extend(group)
    return _parse_spaced_no_blanks(all_lines)


def _parse_spaced_groups(groups):
    """Parse spaced TOC entries from blank-line-separated groups.

    Each group is a list of stripped lines forming one TOC entry.
    Uses \\s{3,} for inline page number detection (original PDF spacing).
    """
    entries = []
    for group in groups:
        if not group:
            continue

        page = None
        title_parts = []
        author = None

        # Check first line for inline page: "Title text       42"
        m = re.match(r'^(.+?)\s{3,}(\d{1,3})\s*$', group[0])
        if m:
            title_parts.append(m.group(1).strip())
            page = int(m.group(2))
        else:
            title_parts.append(group[0])

        # Process remaining lines
        for line in group[1:]:
            bare_page = re.match(r'^(\d{1,3})$', line)
            if bare_page and page is None:
                page = int(bare_page.group(1))
            elif _is_name_like(line):
                author = line
            else:
                m2 = re.match(r'^(.+?)\s{3,}(\d{1,3})\s*$', line)
                if m2 and page is None:
                    title_parts.append(m2.group(1).strip())
                    page = int(m2.group(2))
                else:
                    title_parts.append(line)

        if page is None:
            continue

        title = ' '.join(title_parts)
        entries.append({'title': title, 'author': author, 'page': page})

    return entries


def _parse_spaced_no_blanks(all_lines):
    """Parse spaced TOC with no blank-line separators (re-OCR'd text).

    Detects format by checking where page numbers appear:
    - Format A: page numbers on title lines (14.1, 14.2 style)
    - Format B: page numbers on author/name lines (13.1 style)

    Uses \\s{2,} for inline page detection (re-OCR'd spacing is tighter).
    """
    # Classify each line: has inline page number or not
    page_lines = []  # indices of lines with inline page numbers
    non_page_lines = []  # indices of lines without
    for i, line in enumerate(all_lines):
        if re.match(r'^(.+?)\s{2,}(\d{1,3})\s*$', line):
            page_lines.append(i)
        else:
            non_page_lines.append(i)

    if not page_lines:
        return []

    # Detect format: check if non-page lines are mostly names (format A)
    # or mostly titles (format B)
    name_count = sum(1 for i in non_page_lines if _is_name_like(all_lines[i]))
    if non_page_lines:
        name_ratio = name_count / len(non_page_lines)
    else:
        name_ratio = 0

    # Format A: non-page lines are mostly names → pages are on title lines
    # Format B: non-page lines are mostly titles → pages are on author lines
    if name_ratio >= 0.5:
        return _parse_spaced_format_a(all_lines, page_lines)
    else:
        return _parse_spaced_pages_on_authors(all_lines, page_lines)


def _parse_spaced_format_a(all_lines, page_line_indices):
    """Format A: page numbers on title lines, authors on separate lines.

    E.g. 14.1, 14.2:
        Scheler, Nietzsche & Social Psychology                2
        Daniel Burston
        The Internalisation of Nietzsche's Master...         14
        Steve Kirby

    Lines between consecutive page-bearing lines belong to the preceding entry.
    The last name-like line in each partition is the author; everything else
    is title overflow. Multi-author lines are joined when the preceding line
    ends with a comma.
    """
    entries = []

    for idx, i in enumerate(page_line_indices):
        line = all_lines[i]
        m = re.match(r'^(.+?)\s{2,}(\d{1,3})\s*$', line)
        if not m:
            continue

        title_parts = [m.group(1).strip()]
        page = int(m.group(2))

        # Lines belonging to this entry: from after this page-line
        # to before the next page-line
        next_page = page_line_indices[idx + 1] if idx + 1 < len(page_line_indices) else len(all_lines)
        trailing = all_lines[i + 1 : next_page]

        author = None
        if trailing:
            # Find last name-like line — that's the author
            last_name_idx = None
            for j in range(len(trailing) - 1, -1, -1):
                if _is_name_like(trailing[j]):
                    last_name_idx = j
                    break

            if last_name_idx is not None:
                # Include preceding name-like lines that end with comma
                # (multi-author continuation)
                author_start = last_name_idx
                while author_start > 0:
                    prev = trailing[author_start - 1]
                    if _is_name_like(prev) and prev.rstrip().endswith(','):
                        author_start -= 1
                    else:
                        break

                author = ' '.join(trailing[author_start:last_name_idx + 1])
                title_parts.extend(trailing[:author_start])
            else:
                title_parts.extend(trailing)

        title = ' '.join(title_parts)
        entries.append({'title': title, 'author': author, 'page': page})

    return entries


def _try_split_trailing_author(text):
    """Try to split a trailing author name from the end of a title string.

    Returns (title, author) if a split is found, or (text, None) if not.
    """
    # Try known authors first (longest match)
    known = _get_known_authors()
    for name in sorted(known, key=len, reverse=True):
        if text.endswith(name) and len(text) > len(name) + 20:
            title_part = text[:-len(name)].rstrip(' .')
            if title_part:
                return (title_part, name)

    # Fallback: try _is_name_like on successively shorter suffixes
    words = text.split()
    for n in range(2, min(6, len(words))):
        candidate = ' '.join(words[-n:])
        remaining = ' '.join(words[:-n])
        if _is_name_like(candidate) and len(remaining) > 20:
            return (remaining.rstrip(' .'), candidate)

    return (text, None)


def _parse_spaced_pages_on_authors(all_lines, page_line_indices):
    """Format B: page numbers on author lines, titles on separate lines.

    E.g. 13.1:
        Death - a Philosophical Perspective
        Alfons Grieder                                        2
        'Illness' ... and Its Human Values
        Greg Madison                                         10
    """
    page_set = set(page_line_indices)
    entries = []

    for i in page_line_indices:
        line = all_lines[i]
        m = re.match(r'^(.+?)\s{2,}(\d{1,3})\s*$', line)
        if not m:
            continue

        text_part = m.group(1).strip()
        page = int(m.group(2))

        if _is_name_like(text_part):
            author = text_part
            # Collect title lines going backwards until we hit another page line
            title_parts = []
            for j in range(i - 1, -1, -1):
                if j in page_set:
                    break
                title_parts.insert(0, all_lines[j])
            if title_parts:
                title = ' '.join(title_parts)
            else:
                # No title lines before this — it's a section header, not an author
                title = text_part
                author = None
        else:
            # This page-bearing line might be "Title Author" concatenated
            # Strip trailing dot-leaders before trying to split
            clean_text = re.sub(r'\s*\.{3,}[\s.]*$', '', text_part).strip()
            title, author = _try_split_trailing_author(clean_text)
            if author is None:
                title = text_part

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
    content_lines = []  # (raw_line, stripped) — non-blank lines after CONTENTS
    for line in lines[start_idx:]:
        stripped = line.strip()
        if stripped:
            content_lines.append((line, stripped))

    classified = []  # (type, content) where type is 'title', 'page', 'text'
    for li, (line, stripped) in enumerate(content_lines):
        if '\t' in line:
            clean = stripped.rstrip('\t').strip()
            # A tab-line immediately after a page number might be a title
            # continuation or author name (trailing tab is a PyMuPDF
            # extraction artifact), not a new entry.  Only reclassify
            # if the NEXT line is not a page number (real new entries
            # have tab-title followed by page number).
            if classified and classified[-1][0] == 'page':
                next_stripped = content_lines[li + 1][1] if li + 1 < len(content_lines) else ''
                next_is_page = bool(re.match(r'^\d{1,3}$', next_stripped))
                if not next_is_page:
                    # Next line isn't a page number, so this tab-line is
                    # probably not a new entry.  Treat as text if it's
                    # name-like or short (title continuation).
                    if _is_name_like(clean) or len(clean) < 60:
                        classified.append(('text', clean))
                        continue
            classified.append(('title', clean))
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


def normalize_title_case(title):
    """Convert ALL CAPS titles to title case.

    Only triggers when the title has a high proportion of ALL CAPS words.
    Each word is individually title-cased; small words (a, the, of, etc.)
    are lowercased unless they start the title or follow a colon.
    Non-ALL-CAPS words are left untouched (preserves mixed-case subtitles).
    Hyphenated words like SELF-ANALYSIS are handled part-by-part.
    """
    if not title:
        return title

    words = title.split()

    # Count ALL CAPS words (3+ alpha chars, fully uppercase) to decide
    # if this is an ALL CAPS title
    def _is_caps_word(w):
        alpha = re.sub(r'[^A-Za-z]', '', w)
        return len(alpha) >= 3 and alpha == alpha.upper() and alpha.isalpha()

    caps_count = sum(1 for w in words if _is_caps_word(w))
    if caps_count < 2:
        return title  # Not an ALL CAPS title

    SMALL_WORDS = {'a', 'an', 'the', 'and', 'but', 'or', 'nor', 'for',
                   'in', 'on', 'at', 'to', 'of', 'by', 'is', 'as', 'not',
                   'we', 'do', 'it'}

    result = []
    capitalize_next = True  # First word or after colon

    for word in words:
        # Check if this word is ALL CAPS (any length)
        alpha_only = re.sub(r'[^A-Za-z]', '', word)
        is_upper = (alpha_only and alpha_only == alpha_only.upper()
                    and alpha_only.isalpha())

        if not is_upper:
            # Not ALL CAPS — leave as-is (mixed-case subtitle, abbreviation)
            result.append(word)
            capitalize_next = word.endswith(':')
            continue

        # Abbreviation-like words with dots (T.S.SZASZ, U.S.A.) — leave as-is
        if '.' in word and re.match(r'^([A-Z]\.)+', word):
            result.append(word)
            capitalize_next = word.endswith(':')
            continue

        # Handle hyphenated words (SELF-ANALYSIS → Self-Analysis)
        if '-' in word:
            parts = word.split('-')
            converted = '-'.join(p[0].upper() + p[1:].lower() if p else p
                                for p in parts)
            result.append(converted)
            capitalize_next = word.endswith(':')
            continue

        # Strip leading/trailing punctuation for case logic
        prefix_punc = ''
        core = word
        while core and not core[0].isalpha():
            prefix_punc += core[0]
            core = core[1:]
        suffix_punc = ''
        while core and not core[-1].isalpha():
            suffix_punc = core[-1] + suffix_punc
            core = core[:-1]

        if not core:
            result.append(word)
        elif capitalize_next or core.lower() not in SMALL_WORDS:
            result.append(prefix_punc + core[0].upper() + core[1:].lower() + suffix_punc)
        else:
            result.append(prefix_punc + core.lower() + suffix_punc)

        capitalize_next = word.endswith(':')

    return ' '.join(result)


def collapse_letter_spacing(title):
    """Collapse letter-spaced OCR artifacts like 'S e x u a l' → 'Sexual'."""
    if not title:
        return title
    # Detect pattern: 5+ single chars separated by spaces
    if not re.search(r'(\w ){5,}', title):
        return title
    # Collapse single-char space sequences (char + space before another char)
    result = re.sub(r'(\w) (?=\w(?:\s|$))', r'\1', title)
    # Collapse remaining multiple spaces to single
    result = re.sub(r'  +', ' ', result)
    # Fix space before punctuation introduced by collapsing
    result = re.sub(r' ([:.;,!?])', r'\1', result)
    return result


def classify_entry(title):
    """Classify a TOC entry into an OJS section.

    Returns a (section, skip) tuple where skip=True means the entry
    should be omitted from output (e.g. 'Notes on Contributors').

    Note: this uses hardcoded section names, not _FORMAT_A_SKIP_HEADERS
    or _FORMAT_B_SECTION_HEADERS — those constants control TOC parsing
    boundaries, not OJS section assignment.
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
    # Index entries — skip from output
    if re.match(r'(?i)(journal\s+)?index\b', title):
        return (SECTION_ARTICLES, True)  # skip=True
    return (SECTION_ARTICLES, False)


def extract_article_metadata(doc, pdf_start_idx, pdf_end_idx):
    """Extract abstract and keywords from article's first page(s).

    Abstract formats: "Abstract\\n text..." or "Abstract: text..." (inline).
    Terminates at Key Words/Keywords/Introduction or any capitalized heading.

    Keyword delimiters: semicolons preferred when present, else commas.
    Handles "Key Words", "Keywords", "Key Word" headings with optional colon.
    """
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


def _parse_book_reviews_by_font(doc, br_start_pdf, br_end_pdf):
    """Parse book reviews using font formatting (Arial Bold ~12pt = title).

    Returns list of reviews, or None if font detection isn't viable
    (e.g. scanned PDFs or pre-2003 issues with different formatting).

    Viability checks: requires both title-font (Arial Bold) and pub-font
    (Arial non-bold) lines present. Pub lines must parse into author/year/publisher
    with author having at least 2 words (filters out inverted-format issues
    where city/publisher fragments appear in pub-font).
    """
    reviews = []
    # Collect lines with font classification across the BR pages.
    # Each entry: (page_idx, full_text, line_type, size)
    # line_type: 'title' (all Arial Bold), 'pub' (all Arial non-bold),
    #            'body' (Times/other), 'mixed'
    classified_lines = []

    for page_idx in range(br_start_pdf, min(br_end_pdf + 1, len(doc))):
        page = doc[page_idx]

        # get_text("dict") returns structured font info; if the doc doesn't
        # support it (e.g. mock objects in tests), fall back to regex
        try:
            text_dict = page.get_text("dict")
            blocks = text_dict["blocks"]
        except (TypeError, KeyError, AttributeError):
            return None

        # Stop at backmatter
        page_text = page.get_text()
        if _BACKMATTER_RE.search(page_text):
            if reviews:
                reviews[-1]['pdf_page_end'] = page_idx - 1
            break

        for block in blocks:
            if "lines" not in block:
                continue
            for line_obj in block["lines"]:
                spans = line_obj["spans"]
                # Join all span text for this line
                parts = []
                has_title_font = False
                has_pub_font = False
                has_body_font = False
                max_size = 0

                for span in spans:
                    text = span["text"]
                    if not text.strip():
                        continue
                    parts.append(text)
                    font = span["font"]
                    size = span["size"]
                    flags = span["flags"]
                    bold = bool(flags & (1 << 4))
                    max_size = max(max_size, size)

                    is_arial = 'Arial' in font or 'Helvetica' in font
                    if is_arial and bold and size >= 9.0:
                        has_title_font = True
                    elif is_arial and not bold and size >= 9.0:
                        has_pub_font = True
                    else:
                        has_body_font = True

                full_text = ''.join(parts).strip()
                if not full_text:
                    continue

                # Classify line by dominant font
                if has_title_font and not has_pub_font and not has_body_font:
                    line_type = 'title'
                elif has_pub_font and not has_title_font:
                    line_type = 'pub'
                elif has_title_font and has_pub_font:
                    line_type = 'mixed'
                else:
                    line_type = 'body'

                classified_lines.append((page_idx, full_text, line_type, max_size))

    if not classified_lines:
        return None

    # Check if we found both title-font AND pub-font lines — if either is
    # missing, font detection isn't viable (scanned PDF, inverted format, etc.)
    title_line_count = sum(1 for _, _, lt, _ in classified_lines if lt == 'title')
    pub_line_count = sum(1 for _, _, lt, _ in classified_lines if lt == 'pub')
    if title_line_count == 0 or pub_line_count == 0:
        return None

    # Check that pub lines actually parse into author/year/publisher AND that
    # the author field looks like a real person name (not a city/publisher
    # fragment from inverted-format issues). Require at least 2 words in author.
    parseable_pub_count = 0
    for _, text, lt, _ in classified_lines:
        if lt == 'pub':
            author, year, _ = _parse_pub_line(text)
            if year and author and len(author.split()) >= 2:
                parseable_pub_count += 1
    if parseable_pub_count == 0:
        return None

    # State machine: find title lines (Arial Bold) followed by pub lines
    # (Arial non-bold that parse into author/year/publisher)
    current_title_parts = []
    current_title_page = None
    state = 'seeking_title'

    for page_idx, text, line_type, size in classified_lines:
        # Skip headers, page numbers, section headings
        if text.lower() in ('book reviews', 'book review'):
            continue
        if text.lower().startswith('existential analysis'):
            continue
        if re.match(r'^\d+$', text):
            continue
        if size >= 20.0 and line_type == 'title':
            continue
        if text.upper() in ('ERRATUM', 'ERRATA', 'REFERENCES', 'BIBLIOGRAPHY',
                            'WORKS CITED'):
            continue

        if state == 'seeking_title':
            if line_type == 'title':
                current_title_parts = [text]
                current_title_page = page_idx
                state = 'in_title'
        elif state == 'in_title':
            if line_type == 'title':
                current_title_parts.append(text)
            elif line_type in ('pub', 'mixed'):
                # Found Arial non-bold line after title — parse as pub line
                book_author, book_year, publisher = _parse_pub_line(text)
                if book_year:
                    book_title = ' '.join(current_title_parts)
                    if reviews:
                        reviews[-1]['pdf_page_end'] = current_title_page - 1
                    reviews.append({
                        'title': f"Book Review: {book_title}",
                        'book_title': book_title,
                        'book_author': book_author,
                        'book_year': book_year,
                        'publisher': publisher,
                        'pdf_page_start': current_title_page,
                        'section': SECTION_BOOK_REVIEWS,
                    })
                    state = 'seeking_title'
                else:
                    # Pub line didn't parse (inverted format, etc.) — reset
                    state = 'seeking_title'
            else:
                # Body text after title — try parsing as pub line anyway
                # (some issues use body font for pub lines)
                book_author, book_year, publisher = _parse_pub_line(text)
                if book_year:
                    book_title = ' '.join(current_title_parts)
                    if reviews:
                        reviews[-1]['pdf_page_end'] = current_title_page - 1
                    reviews.append({
                        'title': f"Book Review: {book_title}",
                        'book_title': book_title,
                        'book_author': book_author,
                        'book_year': book_year,
                        'publisher': publisher,
                        'pdf_page_start': current_title_page,
                        'section': SECTION_BOOK_REVIEWS,
                    })
                state = 'seeking_title'

    # If font detection found very few reviews relative to title lines,
    # it's probably not working well for this issue. Fall back to None so
    # the caller uses regex instead.
    if len(reviews) < 2 and title_line_count > 4:
        return None

    # Close final review
    if reviews and 'pdf_page_end' not in reviews[-1]:
        reviews[-1]['pdf_page_end'] = min(br_end_pdf, len(doc) - 1)

    # Deduplicate reviews with the same book title
    reviews = _dedup_reviews(reviews)

    # Extract reviewer names
    for review in reviews:
        reviewer = extract_reviewer_name(doc, review['pdf_page_start'], review['pdf_page_end'])
        if reviewer:
            review['reviewer'] = reviewer
            review['authors'] = reviewer

    return reviews


def _dedup_reviews(reviews):
    """Remove duplicate reviews by normalized title.

    Catches exact duplicates and also cases where one title is a prefix
    of another (e.g. truncated vs full title).
    """
    seen_keys = []
    result = []
    for review in reviews:
        raw = review.get('book_title', '')
        key = re.sub(r'\W+', '', raw.lower())
        # Check if this key matches or is a prefix/suffix of any seen key
        is_dup = False
        for sk in seen_keys:
            if key == sk or key.startswith(sk) or sk.startswith(key):
                is_dup = True
                break
        if is_dup:
            continue
        seen_keys.append(key)
        result.append(review)
    return result


def _parse_pub_line(text):
    """Parse a publication line into (author, year, publisher).

    Handles: "Author. (Year). City: Publisher." and common variants.
    Returns (author, year, publisher) or (None, 0, '') if no match.
    """
    # Standard: Author. (Year). City: Publisher.
    m = re.match(r'^(.+?(?:\(eds?\))?)[.,]\s*\(?(\d{4})\)?\.\s*(.+)', text)
    if m:
        return m.group(1).strip(), int(m.group(2)), m.group(3).strip().rstrip('.')

    # Relaxed: Author (Year). City: Publisher
    m = re.match(r'^(.+?)\s+\((\d{4})\)\.?\s+(.+)', text)
    if m:
        return m.group(1).strip(), int(m.group(2)), m.group(3).strip().rstrip('.')

    # Relaxed no-parens: Author, Year. City: Publisher
    m = re.match(r'^(.+?),\s*(\d{4})\.\s*(.+)', text)
    if m:
        return m.group(1).strip(), int(m.group(2)), m.group(3).strip().rstrip('.')

    # Year-first: (Year). Author. City: Publisher  or  Year. Author. City: Publisher
    m = re.match(r'^\(?(\d{4})\)?\.\s*(.+?)\.\s*(.+)', text)
    if m:
        return m.group(2).strip(), int(m.group(1)), m.group(3).strip().rstrip('.')

    return None, 0, ''


def _clamp_review_page_ranges(reviews):
    """Clamp backwards page ranges on book reviews (end < start → end = start)."""
    for review in reviews:
        s = review.get('pdf_page_start', 0)
        e = review.get('pdf_page_end', 0)
        if e < s:
            review['pdf_page_end'] = s
    return reviews


def parse_book_reviews(doc, br_start_pdf, br_end_pdf):
    """Parse individual book reviews from the Book Reviews section.

    Tries font-based detection first (reliable for standard-format issues,
    vol 14+/2003+). Falls back to regex-based detection for scanned/older PDFs.
    """
    # Try font-based detection first (standard format, vol 14+)
    font_reviews = _parse_book_reviews_by_font(doc, br_start_pdf, br_end_pdf)
    if font_reviews is not None and len(font_reviews) > 0:
        reviews = _clamp_review_page_ranges(font_reviews)
    else:
        # Try ★-prefixed format (vol 13.1)
        star_reviews = _parse_star_reviews(doc, br_start_pdf, br_end_pdf)
        if star_reviews is not None and len(star_reviews) > 0:
            reviews = _clamp_review_page_ranges(star_reviews)
        else:
            # Fall back to regex-based detection for older/scanned issues
            reviews = _clamp_review_page_ranges(
                _parse_book_reviews_by_regex(doc, br_start_pdf, br_end_pdf))

    # Post-process book_author for ALL review sources
    for review in reviews:
        ba = review.get('book_author', '')
        if not ba:
            continue
        # Strip "Edited by", "Directed by", "Adapted by", "Translated by"
        prefix_match = re.match(
            r'^(?:Edited|Directed|Adapted|Translated)\s+by\s+(.+)$',
            ba, re.IGNORECASE)
        if prefix_match:
            review['book_author'] = prefix_match.group(1).strip()
            continue
        # Strip "with a foreword by ..." suffix
        foreword_match = re.match(
            r'^(.+?),?\s+with\s+a\s+(?:foreword|preface|introduction)\s+by\s+.+$',
            ba, re.IGNORECASE)
        if foreword_match:
            review['book_author'] = foreword_match.group(1).strip()

    return reviews


def _parse_star_reviews(doc, br_start_pdf, br_end_pdf):
    """Parse ★-prefixed book reviews (vol 13.1 "star" format).

    Format: ★ *Title*, by Author. Year. City: Publisher.
    or:     ★ Title, Author (Eds.). Year. City: Publisher.

    Returns list of reviews if ★ lines are found, else None.
    """
    reviews = []
    for page_idx in range(br_start_pdf, min(br_end_pdf + 1, len(doc))):
        text = doc[page_idx].get_text()
        lines = text.split('\n')
        for li, line in enumerate(lines):
            stripped = line.strip()
            sl = _STAR_LINE_RE.match(stripped)
            if not sl:
                continue
            citation = sl.group(1).strip()

            # If no year on this line, join with next 1-2 lines
            ym = _YEAR_SPLIT_RE.search(citation)
            if not ym:
                for k in range(li + 1, min(li + 3, len(lines))):
                    nxt = lines[k].strip()
                    if not nxt:
                        break
                    citation += ' ' + nxt
                    ym = _YEAR_SPLIT_RE.search(citation)
                    if ym:
                        break
            if not ym:
                continue
            book_year = int(ym.group(1))
            publisher = ym.group(2).strip().rstrip('.')
            pre_year = citation[:ym.start()].strip()

            # Parse pre-year part into title and author
            # Format A: *Title*, by Author  (has "by" keyword)
            by_match = re.match(r'^\*?(.+?)\*?,?\s+(?<!edited )(?<!translated )by\s+(.+)$', pre_year)
            if by_match:
                book_title = by_match.group(1).strip().strip('*').strip()
                book_author = by_match.group(2).strip().rstrip('.')
                # Strip "edited by..." and "translated by..." from author
                book_author = re.sub(
                    r',?\s*(?:edited|translated)\s+by\s+.+$', '',
                    book_author)
            else:
                # Format B: Title, Author (Eds.)  or  Author, Title. Subtitle
                # Use comma as separator; if second part has (Eds/Ed), it's author
                parts = pre_year.split(',', 1)
                if len(parts) == 2:
                    p1 = parts[0].strip().strip('*').strip()
                    p2 = parts[1].strip().rstrip('.')
                    # If p2 contains (Ed) or (Eds), it's the author
                    if re.search(r'\(Eds?\.\)', p2):
                        book_title = p1
                        book_author = p2
                    else:
                        # Assume: first part is title, second is author+rest
                        # But check if first part looks like a person name
                        # (2-3 capitalized words, no colon)
                        words = p1.split()
                        if (len(words) <= 3 and ':' not in p1
                                and all(w[0].isupper() for w in words)):
                            # Author first: Author, Title. Subtitle
                            book_author = p1
                            book_title = p2.strip()
                            # Clean up title: remove trailing editor info
                            book_title = re.sub(
                                r',?\s*(?:edited|translated)\s+by\s+.+$',
                                '', book_title)
                        else:
                            book_title = p1
                            book_author = p2
                else:
                    # No comma — whole thing is the title
                    book_title = pre_year.strip('*').strip()
                    book_author = ''

            if reviews:
                reviews[-1]['pdf_page_end'] = page_idx - 1

            reviews.append({
                'title': f"Book Review: {book_title}",
                'book_title': book_title,
                'book_author': book_author,
                'book_year': book_year,
                'publisher': publisher,
                'pdf_page_start': page_idx,
                'section': SECTION_BOOK_REVIEWS,
            })

    if not reviews:
        return None

    # Close final review
    if 'pdf_page_end' not in reviews[-1]:
        reviews[-1]['pdf_page_end'] = min(br_end_pdf, len(doc) - 1)

    # Extract reviewer names
    for review in reviews:
        reviewer = extract_reviewer_name(doc, review['pdf_page_start'],
                                         review['pdf_page_end'])
        if reviewer:
            review['reviewer'] = reviewer
            review['authors'] = reviewer

    return reviews


def _match_pub_line(stripped, *, try_inverted=False, lines=None, line_idx=0):
    """Try all publication-line patterns against a stripped line.

    Returns (match, match_type) where match_type is one of:
    'standard', 'partial', 'year_first', 'year_first_partial',
    'relaxed', 'relaxed_partial', 'nocity', 'inverted', or None.
    """
    m = _PUB_STANDARD_RE.match(stripped)
    if m:
        return m, 'standard'
    mp = _PUB_PARTIAL_RE.match(stripped)
    if mp:
        return mp, 'partial'
    myf = _PUB_YEAR_FIRST_RE.match(stripped)
    if myf:
        return myf, 'year_first'
    myfp = _PUB_YEAR_FIRST_PARTIAL_RE.match(stripped)
    if myfp:
        return myfp, 'year_first_partial'
    mrl = _PUB_RELAXED_RE.match(stripped)
    if mrl:
        return mrl, 'relaxed'
    mrlp = _PUB_RELAXED_PARTIAL_RE.match(stripped)
    if mrlp:
        return mrlp, 'relaxed_partial'
    mnc = _PUB_NOCITY_RE.match(stripped)
    if mnc:
        return mnc, 'nocity'

    if try_inverted:
        minv = (_PUB_INVERTED_A_RE.match(stripped)
                or _PUB_INVERTED_B_RE.match(stripped)
                or _PUB_INVERTED_C_RE.match(stripped))
        if minv:
            return minv, 'inverted'

        # Multi-line inverted: join 2-3 consecutive non-blank lines
        # Only for lines starting with * (1990s review marker)
        if stripped.startswith('*') and lines is not None:
            joined_lines = [stripped]
            for k in range(line_idx + 1, min(line_idx + 3, len(lines))):
                nxt = lines[k].strip()
                if not nxt:
                    break
                joined_lines.append(nxt)
            if len(joined_lines) > 1:
                joined = ' '.join(joined_lines)
                minv = (_PUB_INVERTED_A_RE.match(joined)
                        or _PUB_INVERTED_B_RE.match(joined)
                        or _PUB_INVERTED_C_RE.match(joined))
                if minv:
                    return minv, 'inverted'

    return None, None


def _extract_backward_title(lines, pub_line_idx):
    """Scan backward from a pub line to extract title lines above it.

    Returns list of title lines (in forward order), or empty list if
    no valid title found. Stops at blank lines (after finding text),
    page headers, page numbers, section headings, bibliography entries,
    and lines ending with periods.
    """
    title_lines = []
    found_text = False
    for j in range(pub_line_idx - 1, max(pub_line_idx - 8, -1), -1):
        prev = lines[j].strip()
        if not prev:
            if found_text:
                break
            continue
        if prev.lower().startswith('existential analysis'):
            break
        if prev.lower() in ('book reviews', 'book review',
                            'references', 'bibliography'):
            break
        if re.match(r'^\d+$', prev):
            break
        # Stop at lines that look like bibliography/reference entries
        if (_PUB_STANDARD_RE.match(prev) or _PUB_PARTIAL_RE.match(prev)
                or _PUB_YEAR_FIRST_RE.match(prev)
                or _PUB_RELAXED_RE.match(prev)
                or _PUB_INVERTED_A_RE.match(prev)
                or _PUB_INVERTED_B_RE.match(prev)):
            break
        # Stop at lines ending with period — end of paragraph
        if prev.endswith('.'):
            break
        title_lines.insert(0, prev)
        found_text = True
    return title_lines


_MAX_BOOK_TITLE_LENGTH = 120


def _validate_review_titles(reviews):
    """Filter out reviews where the title looks like body text rather than
    a book title heading (too long, lowercase start, sentence patterns,
    publisher/city strings, bibliography-style author format).
    """
    validated = []
    for review in reviews:
        bt = review.get('book_title', '')
        if (bt and bt[0].islower()
                or len(bt) > _MAX_BOOK_TITLE_LENGTH
                or re.match(r'^(Finally|However|Indeed|Moreover|Also|And|But|The author)', bt)
                or 'Journal of the Society' in bt
                or re.search(r'https?://', bt)
                or re.search(r'www\.', bt)
                or re.search(r'University\s+(of\s+\w+\s+)?Press|: University|Publishers?\b', bt)):
            continue
        # Author in "Last, First" format is bibliography-style, not a review
        ba = review.get('book_author', '')
        if ba and re.match(r'^[A-Z][a-z]+,\s+[A-Z][\.\-]', ba):
            continue
        # Reject BRE garbage: citation fragments, publisher remnants
        if ba and (re.search(r'(?i)\bHardback\b', ba)
                   or re.search(r'\(\d{4}\)\.', ba)):
            continue
        # Reject if the ENTIRE book_author is just "(Editors)" or "edited by X"
        if ba and re.match(r'^\((?:Editors?|eds?\.?)\)\s*$', ba, re.IGNORECASE):
            continue
        if ba and re.match(r'^edited\s+by\s+', ba, re.IGNORECASE):
            continue
        # "Film" as book_author — film reviews, not book reviews
        if ba and ba.strip() == 'Film':
            continue
        validated.append(review)
    return validated


def _strip_markers(text):
    """Strip leading/trailing *, star (★), ** markers from text."""
    if not text:
        return text
    return re.sub(r'^[\s*\u2605]+|[\s*\u2605]+$', '', text).strip()


def _strip_publisher_from_author(reviews):
    """Remove city/publisher info from book_author fields.

    Handles "Author. City: Publisher" and "Author City" patterns.
    """
    for review in reviews:
        ba = review.get('book_author', '')
        if not ba:
            continue
        # Split at ". City" or ". Publisher" patterns
        pub_split = _PUB_SPLIT_RE.match(ba)
        if pub_split:
            review['book_author'] = pub_split.group(1).strip()
            if not review.get('publisher'):
                review['publisher'] = pub_split.group(2).strip().rstrip('.')
            continue
        # Fallback: "Name City" without period separator
        words = ba.split()
        if len(words) >= 3 and words[-1] in _KNOWN_CITIES:
            candidate_name = ' '.join(words[:-1])
            if _is_name_like(candidate_name):
                review['book_author'] = candidate_name
                continue

        # Strip known standalone publisher names appended to author
        for pub in ('Erlbaum Associates', 'Lawrence Erlbaum',
                    'Jessica Kingsley', 'Free Association Books'):
            if ba.endswith(pub):
                candidate = ba[:-len(pub)].rstrip(' ,.')
                if candidate and _is_name_like(candidate):
                    review['book_author'] = candidate
                    break

        # Strip series names ("Michael Inwood Past Masters")
        for series in ('Past Masters', 'Key Figures'):
            if series in ba:
                candidate = ba[:ba.index(series)].rstrip(' ,.')
                if candidate and _is_name_like(candidate):
                    review['book_author'] = candidate
                    break

        # Strip trailing state abbreviations ("Press, Boulder, CO.")
        stripped = re.sub(r',\s*[A-Z]{2}\.?\s*$', '', ba)
        if stripped != ba:
            review['book_author'] = stripped.rstrip(' ,.')


def _fix_truncated_titles(reviews):
    """Fix titles split at conjunctions where book_author contains " by ".

    E.g. book_title="Title of", book_author="Psychoanalysis by Author"
    → book_title="Title of Psychoanalysis", book_author="Author"
    """
    for review in reviews:
        ba = review.get('book_author', '')
        by_match = re.match(r'^(.+?)\s+by\s+(.+)$', ba, re.IGNORECASE)
        if by_match:
            title_tail = by_match.group(1)
            real_author = by_match.group(2)
            bt = review.get('book_title', '')
            last_word = bt.rsplit(None, 1)[-1].lower() if bt else ''
            if last_word in _TITLE_CONJUNCTIONS:
                review['book_title'] = f"{bt} {title_tail}"
                review['title'] = f"Book Review: {review['book_title']}"
                review['book_author'] = real_author


def _parse_book_reviews_by_regex(doc, br_start_pdf, br_end_pdf):
    """Parse book reviews using text pattern matching (regex fallback).

    Used for pre-2003 issues with inverted citation format, scanned PDFs,
    and any issues where font detection isn't viable.
    """
    reviews = []

    for page_idx in range(br_start_pdf, min(br_end_pdf + 1, len(doc))):
        text = doc[page_idx].get_text()

        # Stop at backmatter / publications received / ads
        if _BACKMATTER_RE.search(text):
            if reviews:
                reviews[-1]['pdf_page_end'] = page_idx - 1
            break

        lines = text.split('\n')

        # Scan all lines for publication-line patterns that signal a new
        # book review.  To distinguish real review starts from bibliography
        # entries deeper in review text, we require the title line(s) above
        # the pub line to look like a heading: preceded by a blank line (or
        # page header / "Book Reviews" / page number / reviewer name).
        # Track content lines to limit inverted pattern to near-start
        content_line_count = 0
        prev_was_blank = True  # treat start of page as after blank

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                prev_was_blank = True
                content_line_count = 0
                continue
            # Skip page headers (don't count as content)
            if stripped.lower().startswith('existential analysis'):
                continue
            if stripped.lower() in ('book reviews', 'book review'):
                prev_was_blank = True
                content_line_count = 0
                continue
            if re.match(r'^\d+$', stripped):
                continue

            content_line_count += 1

            try_inverted = (content_line_count <= 3 or stripped.startswith('*'))
            match, match_type = _match_pub_line(
                stripped, try_inverted=try_inverted,
                lines=lines, line_idx=i)

            prev_was_blank = False

            if match:
                is_inverted = (match_type == 'inverted')
                year_first = match_type in ('year_first', 'year_first_partial')
                is_nocity = (match_type == 'nocity')
                has_full_pub = match_type in ('standard', 'year_first', 'relaxed')

                # Inverted format: title+author+publisher all on pub line
                if is_inverted:
                    book_title = match.group(1).lstrip('* ').strip()
                    book_author = match.group(2).strip()
                    # Pattern B has (title, author, year, publisher)
                    # Patterns A and C have (title, author, publisher, year)
                    g3 = match.group(3)
                    g4 = match.group(4) or ''
                    if g3 and re.match(r'^\d{4}$', g3):
                        book_year = int(g3)
                        publisher = g4.strip()
                    else:
                        book_year = int(g4) if re.match(r'^\d{4}$', g4) else 0
                        publisher = g3.strip() if g3 else ''
                    if reviews:
                        reviews[-1]['pdf_page_end'] = page_idx - 1
                    reviews.append({
                        'title': f"Book Review: {book_title}",
                        'book_title': book_title,
                        'book_author': book_author,
                        'book_year': book_year,
                        'publisher': publisher,
                        'pdf_page_start': page_idx,
                        'section': SECTION_BOOK_REVIEWS,
                    })
                    continue

                title_lines = _extract_backward_title(lines, i)
                last_title = title_lines[-1] if title_lines else ''

                title_looks_valid = (
                    title_lines and
                    all(len(t) < 70 for t in title_lines) and
                    not any(t.endswith(',') for t in title_lines) and
                    not last_title.endswith('.')
                )

                if title_looks_valid:
                    # Strip leading reviewer name
                    if len(title_lines) > 1:
                        first = title_lines[0]
                        fw = first.split()
                        if (2 <= len(fw) <= 4 and ':' not in first
                                and fw[0].lower() not in ('the', 'a', 'an')
                                and all(w[0].isupper() or w in ('de', 'van', 'von', 'du', 'di', 'and', '&')
                                        for w in fw)):
                            title_lines = title_lines[1:]

                    book_title = ' '.join(title_lines)
                    if reviews:
                        reviews[-1]['pdf_page_end'] = page_idx - 1

                    if year_first:
                        book_author = match.group(2)
                        book_year = int(match.group(1))
                    elif is_nocity:
                        book_author = match.group(1)
                        book_year = int(match.group(2))
                        publisher = match.group(3)
                    else:
                        book_author = match.group(1)
                        book_year = int(match.group(2))

                    if not is_nocity:
                        publisher_city = match.group(3)
                        if has_full_pub:
                            publisher = f"{publisher_city}: {match.group(4)}"
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
                        'book_author': book_author,
                        'book_year': book_year,
                        'publisher': publisher,
                        'pdf_page_start': page_idx,
                        'section': SECTION_BOOK_REVIEWS,
                    })

    # Close final review
    if reviews and 'pdf_page_end' not in reviews[-1]:
        for page_idx in range(reviews[-1]['pdf_page_start'], min(br_end_pdf + 1, len(doc))):
            text = doc[page_idx].get_text()
            if _BACKMATTER_RE.search(text):
                reviews[-1]['pdf_page_end'] = page_idx - 1
                break
        else:
            reviews[-1]['pdf_page_end'] = min(br_end_pdf, len(doc) - 1)

    # Deduplicate reviews with the same book title
    reviews = _dedup_reviews(reviews)
    reviews = _validate_review_titles(reviews)
    _strip_publisher_from_author(reviews)
    _fix_truncated_titles(reviews)

    # Extract reviewer names
    for review in reviews:
        reviewer = extract_reviewer_name(doc, review['pdf_page_start'], review['pdf_page_end'])
        if reviewer:
            review['reviewer'] = reviewer
            review['authors'] = reviewer

    return reviews


def _is_reviewer_skip_line(line):
    """Return True if this line should be skipped during reviewer scanning."""
    if line.startswith('Existential Analysis'):
        return True
    if 'Society for Existential Analysis' in line:
        return True
    if line.startswith('Journal of The Society') or line.startswith('Journal of the Society'):
        return True
    if line == 'Book Reviews' or line == 'Book Review':
        return True
    if re.match(r'^\d+$', line):
        return True
    if re.match(r'^References$', line):
        return True
    if re.search(r'\(\d{4}\)', line):  # contains (year) — reference
        return True
    if re.search(r'Vol\.\s*\d+', line):  # journal ref
        return True
    if re.search(r'(?i)intentionally\s+left\s+blank', line):
        return True
    if re.search(r'ISSN\s+\d', line):
        return True
    if re.search(r'(?i)^chapter\s+\d', line):
        return True
    # Journal/publication metadata lines
    if re.search(r'(?i)\bNo\.\s*\d+\s+pp\b', line):
        return True
    # Publisher/press fragments
    if re.search(r'(?i)\bPress\.?\s*$', line):
        return True
    if re.search(r'(?i)\bPublishers?\.?\s*$', line):
        return True
    if re.search(r'(?i)\bUniversity\b', line):
        return True
    return False


def _is_reviewer_candidate(line):
    """Check if line could be a reviewer name (permissive check)."""
    # Quick rejects for common false positives
    if _is_reviewer_skip_line(line):
        return None
    if ';' in line:  # semicolons indicate lists/references, not names
        return None
    if ',' in line and len(line.split(',')) > 2:  # multiple commas = list, not name
        return None
    if '\u2013' in line or '\u2014' in line:  # em/en dashes = title, not name
        return None
    # Reject lines starting with "and" — continuation of a list
    if re.match(r'^(?:and|&)\s', line, re.IGNORECASE):
        return None
    # Reject known publisher names (with optional trailing period)
    if line.rstrip('.') in _KNOWN_PUBLISHERS:
        return None
    # Reject long lines (body text) — reviewer names are short
    if len(line) > _BODY_TEXT_MIN_LENGTH:
        return None
    # Reject lines ending with "." unless it's an initial (e.g. "J." or "PhD.")
    stripped = line.rstrip()
    if stripped.endswith('.') and not re.search(r'\b[A-Z]\.$', stripped):
        # Allow credentials like "PhD." "MA." etc.
        if not re.search(r'(?:PhD|MA|MSc|BA|BSc|MD|UKCP)\.$', stripped):
            return None
    known = _match_known_author(line)
    if known:
        return known
    if _is_reviewer_name_like(line) and len(line) <= _MAX_NAME_LENGTH:
        return line
    if _is_name_like(line) and len(line) <= _MAX_NAME_LENGTH:
        return line
    return None


def extract_reviewer_name(doc, start_pdf, end_pdf):
    """Extract reviewer name from end of a book review.

    Multi-pass strategy:
    0. Spillover scan — check start of next page for name between References and next title.
    1. Last page, bottom-third short-line scan — find name-like lines near bottom.
    2. All pages forward scan — look for name-like lines after blank-line boundaries.
    3. Backward scan fallback — original approach with 3-consecutive-long-lines cutoff.
    4. Inline name — known author embedded at end of body text line.
    """
    if end_pdf >= len(doc):
        return None

    last_page = min(end_pdf, len(doc) - 1)

    # --- Pass 0: Spillover — reviewer name on next page before next review ---
    # Reviews often share pages. The current review's text may continue onto
    # the next page: body text → [References →] reviewer name → next review.
    # Scan backward from the next review's pub line looking for a name.
    next_page = last_page + 1
    if next_page < len(doc):
        text = doc[next_page].get_text()
        lines = [l.strip() for l in text.split('\n')]
        # Two sub-strategies for finding the reviewer on the next page:
        #
        # (a) Forward scan: after a References section, the first short
        #     name-like line is the reviewer.
        # (b) Backward scan: find where the next review starts (pub line
        #     with valid title above it), scan backward from there —
        #     the last short name-like line is the reviewer.
        #
        # Try (a) first, fall back to (b).

        # --- (a) Forward: first name after References ---
        saw_refs = False
        for line in lines:
            if not line:
                continue
            if line in ('References', 'Bibliography'):
                saw_refs = True
                continue
            if _is_reviewer_skip_line(line) or re.match(r'^\d+$', line):
                continue
            if re.search(r'\(\d{4}\)', line):
                continue  # reference/citation entry
            if saw_refs and len(line) < _BODY_TEXT_MIN_LENGTH:
                cand = _is_reviewer_candidate(line)
                if cand:
                    return cand

        # --- (b) Backward: scan back from next review's pub line ---
        # Find a pub line with valid title lines above it (= real review start)
        pub_idx = None
        for i, line in enumerate(lines):
            if not line:
                continue
            match, _ = _match_pub_line(line, try_inverted=True,
                                       lines=lines, line_idx=i)
            if match:
                title_lines = _extract_backward_title(lines, i)
                if title_lines:
                    pub_idx = i
                    break
        if pub_idx is not None:
            # Scan backward from just before the pub line
            for j in range(pub_idx - 1, -1, -1):
                line = lines[j]
                if not line:
                    continue
                if _is_reviewer_skip_line(line) or re.match(r'^\d+$', line):
                    continue
                if re.search(r'\(\d{4}\)', line):
                    continue
                if len(line) < _BODY_TEXT_MIN_LENGTH:
                    cand = _is_reviewer_candidate(line)
                    if cand:
                        return cand
                if len(line) >= _BODY_TEXT_MIN_LENGTH:
                    break

    # --- Pass 1: Last page, bottom-third short-line scan ---
    text = doc[last_page].get_text()
    all_lines = [l.strip() for l in text.split('\n')]
    non_empty = [(i, l) for i, l in enumerate(all_lines) if l.strip()]
    if non_empty:
        # Bottom third of non-empty lines
        bottom_start = len(non_empty) * 2 // 3
        best = None
        for _, line in non_empty[bottom_start:]:
            if _is_reviewer_skip_line(line):
                continue
            if len(line) >= _BODY_TEXT_MIN_LENGTH:
                continue
            candidate = _is_reviewer_candidate(line)
            if candidate:
                best = candidate
        if best:
            return best

    # --- Pass 2: All review pages, forward scan for name after blank line ---
    best = None
    for page_idx in range(start_pdf, last_page + 1):
        text = doc[page_idx].get_text()
        lines_raw = text.split('\n')
        lines = [l.strip() for l in lines_raw]
        after_blank = False
        in_references = False
        for i, line in enumerate(lines):
            if not line:
                after_blank = True
                continue
            # Stop scanning forward if we hit References
            if line == 'References':
                in_references = True
                after_blank = False
                continue
            if in_references:
                after_blank = False
                continue
            if _is_reviewer_skip_line(line):
                after_blank = False
                continue
            if after_blank and len(line) < _BODY_TEXT_MIN_LENGTH:
                candidate = _is_reviewer_candidate(line)
                if candidate:
                    # Check next non-blank line — if it's also short or blank/end, good
                    next_is_ok = True
                    for j in range(i + 1, len(lines)):
                        next_line = lines[j]
                        if not next_line:
                            break  # blank after name — good
                        if _is_reviewer_skip_line(next_line):
                            break
                        if len(next_line) >= _BODY_TEXT_MIN_LENGTH:
                            next_is_ok = False  # body text follows — probably not a name
                        break
                    if next_is_ok:
                        best = candidate
            after_blank = False
        # Don't return early — keep scanning to find the *last* such candidate

    if best:
        return best

    # --- Pass 3: Backward scan fallback (original logic) ---
    for page_idx in range(last_page, max(start_pdf - 1, last_page - 4), -1):
        text = doc[page_idx].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        consecutive_long = 0
        for line in reversed(lines):
            if _is_reviewer_skip_line(line):
                continue
            candidate = _is_reviewer_candidate(line)
            if candidate:
                return candidate
            if len(line) > _BODY_TEXT_MIN_LENGTH:
                consecutive_long += 1
                if consecutive_long >= 3:
                    break
            else:
                consecutive_long = 0

    # --- Pass 4: Inline name at end of body text (e.g. "...do so. Emmy van Deurzen-Smith") ---
    # Scan ALL review pages forward, keeping the last match (closest to end of review).
    known = _get_known_authors()
    last_inline_match = None
    for page_idx in range(start_pdf, last_page + 1):
        text = doc[page_idx].get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for line in lines:
            if _is_reviewer_skip_line(line):
                continue
            if len(line) < 20:
                continue
            # Check if a known author name appears at the end of this line
            for name in sorted(known, key=len, reverse=True):
                if line.endswith(name) and len(line) > len(name) + 5:
                    # Verify the part before the name ends with sentence punctuation
                    prefix = line[:-len(name)].rstrip()
                    if prefix and prefix[-1] in '.!':
                        last_inline_match = name
                        break  # longest match wins for this line
    if last_inline_match:
        return last_inline_match

    # --- Pass 5: Bold font detection ---
    # Reviewer names are typically set in bold while body text is not.
    # Scan review pages + spillover page for bold short name-like spans.
    _bold_skip = {'Book Reviews', 'Book Review', 'References', 'Bibliography',
                  'Endnotes', 'Notes', 'Acknowledgements', 'Abstract'}
    best_bold = None
    for page_idx in range(start_pdf, min(last_page + 2, len(doc))):
        try:
            blocks = doc[page_idx].get_text('dict')['blocks']
        except Exception:
            continue
        for block in blocks:
            if 'lines' not in block:
                continue
            for line_obj in block['lines']:
                for span in line_obj['spans']:
                    text = span['text'].strip()
                    is_bold = bool(span['flags'] & (1 << 4))
                    if not is_bold or not text:
                        continue
                    if len(text) <= 3 or len(text) > _MAX_NAME_LENGTH:
                        continue
                    if text in _bold_skip or text.isdigit():
                        continue
                    if _is_reviewer_skip_line(text):
                        continue
                    cand = _is_reviewer_candidate(text)
                    if cand:
                        best_bold = cand
    return best_bold


_TOC_OVERRIDES = None


def _get_toc_overrides():
    """Lazy-load TOC overrides from toc_overrides.json."""
    global _TOC_OVERRIDES
    if _TOC_OVERRIDES is None:
        _TOC_OVERRIDES = []
        path = os.path.join(os.path.dirname(__file__), 'toc_overrides.json')
        try:
            with open(path) as f:
                data = json.load(f)
            _TOC_OVERRIDES = data.get('overrides', [])
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return _TOC_OVERRIDES


def apply_toc_overrides(articles, vol, iss):
    """Apply manual overrides to articles list. Each override patches
    fields on matched articles. Match key: 'vol.issue:title_prefix'."""
    overrides = _get_toc_overrides()
    if not overrides:
        return
    prefix = f"{vol}.{iss}:"
    for override in overrides:
        match_key = override.get('match', '')
        if not match_key.startswith(prefix):
            continue
        title_prefix = match_key[len(prefix):]
        fields = override.get('set', {})
        for art in articles:
            title = art.get('title', '')
            if title.startswith(title_prefix):
                art.update(fields)
                break


def _detect_vol_issue_date(doc):
    """Detect volume, issue, and date from the first few pages of a PDF.

    Tries two-issue format (e.g. "10.2") first on all pages, then falls
    back to single-issue (e.g. "Analysis 10"). Returns (vol, iss, date)
    where any value may be None.
    """
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
    return vol, iss, date


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

    vol, iss, date = _detect_vol_issue_date(doc)

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
            'title': collapse_letter_spacing(normalize_title_case(entry['title'])),
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

    # Post-process: try to split trailing author from title for authorless articles
    for article in articles:
        if not article.get('authors') and article['section'] == SECTION_ARTICLES:
            title, author = _try_split_trailing_author(article['title'])
            if author:
                article['title'] = title
                article['authors'] = author

    # Split Book Reviews into editorial + individual reviews
    final_articles = []
    for article in articles:
        if article['section'] == SECTION_BOOK_REVIEW_EDITORIAL:
            br_start = article['pdf_page_start']
            br_end = article['pdf_page_end']

            individual_reviews = parse_book_reviews(doc, br_start, br_end)
            if individual_reviews:
                new_end = individual_reviews[0]['pdf_page_start'] - 1
                # Clamp to at least the start page (avoid backwards ranges)
                article['pdf_page_end'] = max(new_end, article['pdf_page_start'])
                article['journal_page_end'] = article['pdf_page_end'] - offset

            # Extract book review editorial author — a standalone name line
            # Only scan BRE intro pages (up to 2 pages), not into individual reviews
            if not article.get('authors'):
                _bre_reject = {'book reviews', 'the society for existential analysis',
                               'book review'}
                # Limit to first 2 pages of BRE section
                bre_scan_end = min(article['pdf_page_start'] + 2,
                                   article['pdf_page_end'] + 1, len(doc))
                for pi in range(article['pdf_page_start'], bre_scan_end):
                    page_text = doc[pi].get_text()
                    page_lines = [l.strip() for l in page_text.split('\n')
                                  if l.strip()]
                    for line in page_lines:
                        if line == 'ERRATUM':
                            break  # stop before erratum
                        if (line.lower() not in _bre_reject
                                and _is_name_like(line)
                                and len(line) <= _MAX_NAME_LENGTH):
                            article['authors'] = line

            final_articles.append(article)
            for review in individual_reviews:
                final_articles.append(review)
        else:
            final_articles.append(article)

    # Apply manual reviewer overrides
    apply_toc_overrides(final_articles, vol, iss)

    # Post-process: collapse letter-spaced OCR artifacts in all titles
    for article in final_articles:
        for key in ('title', 'book_title'):
            if key in article and article[key]:
                article[key] = collapse_letter_spacing(article[key])
        if article.get('book_title'):
            cleaned = _strip_markers(article['book_title'])
            if cleaned != article['book_title']:
                article['book_title'] = cleaned
                article['title'] = f"Book Review: {cleaned}"
        # Strip trailing dot-leader artifacts from OCR
        if article.get('title'):
            article['title'] = re.sub(r'\s*\.{3,}[\s.]*$', '', article['title']).strip()
            # If stripped title is just "Editorial", ensure correct section
            if article['title'].lower() == 'editorial' and article['section'] != SECTION_EDITORIAL:
                article['section'] = SECTION_EDITORIAL
        # Fix hyphenation artifacts from line breaks
        for key in ('title', 'book_title'):
            if key in article and article[key]:
                article[key] = re.sub(r'(\w)- (\w)', r'\1\2', article[key])

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
