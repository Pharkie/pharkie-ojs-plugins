#!/usr/bin/env python3
"""
Extract subtitles from raw HTML and split title/subtitle in toc.json.

The raw HTML from htmlgen has titles and subtitles as separate elements:
  <h1>Being With Cyril</h1>
  <p>Can Heideggerian language do justice to...</p>
  <h2>Titos Florides</h2>

But toc.json has them concatenated into one title string. This script
detects the split point using the raw HTML structure and known author
names, then writes a `subtitle` field to toc.json.

Usage:
    python3 backfill/extract_subtitles.py --report backfill/private/output/*/toc.json
    python3 backfill/extract_subtitles.py --report --report-file private/reports/subtitle-review.md backfill/private/output/*/toc.json
    python3 backfill/extract_subtitles.py --apply backfill/private/output/23.1/toc.json
    python3 backfill/extract_subtitles.py --apply --dry-run backfill/private/output/*/toc.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib.citations import (
    looks_like_person_name, normalise_allcaps, normalise_for_match as _normalise_for_match,
    is_provenance, strip_html,
)


# ---------------------------------------------------------------
# Constants
# ---------------------------------------------------------------

# Max length for a subtitle candidate (longer = body text, not subtitle)
SUBTITLE_MAX_LENGTH = 200

# Min length for an author name segment to be used for matching
AUTHOR_NAME_MIN_MATCH_LENGTH = 4

# Min length for a text element to be considered (skip empty/trivial elements)
MIN_ELEMENT_TEXT_LENGTH = 5

# How far into the raw HTML to search for subtitle elements after the title
SUBTITLE_SEARCH_WINDOW = 1000

# Min word length for single-word subtitle match in split_title
DISTINCTIVE_WORD_MIN_LENGTH = 6

# Subtitle must be found in the latter portion of the toc title (fraction)
SPLIT_POSITION_MIN_FRACTION = 0.3

# Section headings that indicate end of front matter (not subtitles)
SECTION_HEADINGS = frozenset({
    'abstract', 'introduction', 'keywords', 'summary', 'preamble',
    'background', 'method', 'methods', 'methodology', 'discussion',
    'results', 'conclusion', 'conclusions', 'acknowledgements',
    'references', 'bibliography', 'notes', 'endnotes',
    'contents', 'book reviews', 'letters', 'editorial',
    'letter to the editors', 'letters to the editors',
    'obituary', 'obituaries', 'essay review',
})

# Patterns that indicate book review metadata (not subtitles)
BOOK_REVIEW_PATTERNS = [
    re.compile(r'^\*?\s*\w.+(?:Press|Publishing|Books|Publications|Routledge|Sage|Springer)', re.I),
    re.compile(r'\b\d{4}\b.*\b(?:pp|pages)\b', re.I),
    re.compile(r'\bISBN\b', re.I),
]

# Pattern for issue headers misdetected as titles
ISSUE_HEADER_RE = re.compile(
    r'^Existential\s+Analysis\s+\d+\.\d+', re.I
)

# Pattern for bibliographic citation lines (not subtitles)
BIBLIOGRAPHIC_RE = re.compile(
    r'^\w+.*?\(\d{4}(?:\s*\[\d{4}\])?\).*?(?:Trans\.|Ed\.|pp\.?|Hardback|Paperback)', re.I
)


# ---------------------------------------------------------------
# Author name parsing
# ---------------------------------------------------------------

def parse_author_names(authors_raw) -> list[str]:
    """Parse authors field (string or list) into a list of individual names."""
    if isinstance(authors_raw, list):
        return [a.strip() for a in authors_raw if a and a.strip()]
    if not authors_raw or not isinstance(authors_raw, str):
        return []
    # Split on common separators: ", ", " and ", " & ", "; "
    # But be careful: "du Plock" has a comma-less compound name
    parts = re.split(r'\s*(?:,\s*|\s+and\s+|\s*&\s*|\s*;\s*)', authors_raw)
    # Recombine parts that are just first/last fragments
    # e.g. "Andrea F. Ycaza, Scott M. Hyman" splits correctly
    # but "Smith, John" would need recombining — unlikely in this dataset
    return [p.strip() for p in parts if p and p.strip()]



def text_matches_author(text: str, author_names: list[str]) -> bool:
    """Check if text is (or contains) a known author name."""
    text_norm = _normalise_for_match(text)
    for name in author_names:
        name_norm = _normalise_for_match(name)
        if len(name_norm) < AUTHOR_NAME_MIN_MATCH_LENGTH:
            continue
        # Full name match
        if text_norm == name_norm:
            return True
        # Family name in text (catches middle-initial variants like "Evgenia T. Georganda")
        parts = name_norm.split()
        family = parts[-1] if parts else ''
        if family and len(family) >= AUTHOR_NAME_MIN_MATCH_LENGTH and family in text_norm:
            # Extra check: text should be short (name-length) to avoid matching
            # "Georganda" inside a long subtitle about Georganda's work
            if len(text_norm.split()) <= 5:
                return True
        # Text contains full name
        if name_norm in text_norm:
            return True
    return False


# ---------------------------------------------------------------
# Subtitle detection
# ---------------------------------------------------------------

def is_section_heading(text: str) -> bool:
    """Check if text is a known section heading."""
    return text.strip().lower().rstrip(':') in SECTION_HEADINGS


def is_book_review_metadata(text: str) -> bool:
    """Check if text looks like book review bibliographic details."""
    return any(p.search(text) for p in BOOK_REVIEW_PATTERNS)


def is_provenance_note(text: str) -> bool:
    """Check if text is a conference/presentation provenance note.

    Delegates to the shared is_provenance() in lib/citations.py.
    """
    return is_provenance(text)




def strip_html_tags(html: str) -> str:
    """Remove HTML tags, decode entities.

    Uses the HTMLParser-based strip_html() from lib/citations.py.
    """
    return strip_html(html)


def detect_subtitle(raw_html: str, author_names: list[str],
                     section: str = '', toc_title: str = '') -> str | None:
    """Detect a subtitle in the raw HTML after the title heading.

    Returns (html_title, subtitle) tuple, or None if no subtitle detected.
    html_title is the text from the <h1>/<h2> heading (source of truth for title).
    Skips book reviews (title is "Book Review: [Book Title]").
    """
    # Skip sections where titles shouldn't be split
    section_lower = section.lower()
    if 'book review' in section_lower:
        return None  # Reviews keep full title ("Book Review: [Book Title]")
    # Strip leading issue header ("Existential Analysis X.Y: Month Year")
    html = re.sub(
        r'^\s*(?:<div[^>]*>\s*)?<p[^>]*>\s*(?:<strong[^>]*>\s*)?Existential\s+Analysis\s+\d+\.\d+.*?</p>\s*(?:</div>\s*)?',
        '', raw_html, flags=re.IGNORECASE
    )
    # Strip leading div wrappers
    html = re.sub(r'^\s*<div[^>]*>\s*', '', html)

    # Find title heading (h1 or h2)
    title_match = re.match(r'\s*<h[12][^>]*>(.*?)</h[12]>\s*', html, re.DOTALL)
    if not title_match:
        return None

    raw_title = strip_html_tags(title_match.group(1)).strip()
    # If HTML heading is ALL CAPS but toc.json has proper case, use toc casing
    alpha = [c for c in raw_title if c.isalpha()]
    if alpha and all(c.isupper() for c in alpha) and toc_title:
        # toc_title may include the subtitle concatenated — extract just the title part
        # by matching the first N words case-insensitively
        toc_words = toc_title.split()
        raw_words = raw_title.split()
        if len(toc_words) >= len(raw_words):
            # Check that the toc title starts with the same words (case-folded)
            match = all(t.lower() == r.lower()
                        for t, r in zip(toc_words[:len(raw_words)], raw_words))
            if match:
                raw_title = ' '.join(toc_words[:len(raw_words)])
    html_title = normalise_allcaps(raw_title)

    # Skip titles that are structural headings (not article titles)
    title_lower = html_title.lower().strip()
    if title_lower in ('contents', 'letters to the editors', 'letters to the editor',
                        'letter to the editors', 'letter to the editor',
                        'obituary', 'obituaries', 'editorial'):
        return None

    # Skip issue headers misdetected as titles (e.g. "Existential Analysis 13.1: January 2002")
    if ISSUE_HEADER_RE.match(html_title):
        return None

    rest = html[title_match.end():]

    # Look at the next block-level element(s)
    elements = re.finditer(
        r'<(h[1-6]|p)[^>]*>(.*?)</\1>',
        rest[:SUBTITLE_SEARCH_WINDOW], re.DOTALL
    )

    for m in elements:
        tag = m.group(1)
        content_html = m.group(2)
        text = strip_html_tags(content_html).strip()

        if not text or len(text) < MIN_ELEMENT_TEXT_LENGTH:
            continue

        # Skip author names (known from toc.json)
        if text_matches_author(text, author_names):
            return None  # Author comes right after title = no subtitle

        # Skip text that looks like a person name — even if not in the
        # known author list (handles middle initials, accents, variants).
        # Pattern: optional title + given name(s) + optional middle initial(s) + family name
        # with optional nobiliary particles (van, de, du, von, etc.)
        if looks_like_person_name(text):
            return None

        # Skip section headings
        if is_section_heading(text):
            return None

        # Skip book review metadata — check each line because <br> tags
        # create multi-line content (e.g. "Book Title\nAuthor (2004). Publisher. 376pp.")
        text_lines = text.split('\n')
        if any(is_book_review_metadata(line.strip()) for line in text_lines):
            return None

        # Skip provenance notes
        if is_provenance_note(text):
            return None

        # Skip bibliographic citation lines (also per-line for <br> content)
        if any(BIBLIOGRAPHIC_RE.match(line.strip()) for line in text_lines):
            return None

        # Skip if too long (body text, not subtitle)
        if len(text) > SUBTITLE_MAX_LENGTH:
            return None

        # Skip if starts with typical body text openers
        if re.match(r'^(This|The|In|As|It|We|A|An|I|My|Our|One|For|From|After|During|When|There)\s+\w+\s+\w+', text):
            # But allow questions as subtitles
            if not text.endswith('?'):
                return None

        # Skip citation references (contain "Existential Analysis" journal name + volume)
        if re.search(r'Existential Analysis\s+\d+\.\d+', text):
            return None

        # Strip title repetition from the start of the subtitle.
        # Some PDFs repeat the title in ALL CAPS before the actual subtitle:
        # <h1>Title</h1><p>TITLE: actual subtitle</p>
        title_norm = _normalise_for_match(html_title)
        text_norm = _normalise_for_match(text)
        if title_norm and text_norm.startswith(title_norm):
            # Find the position in the original text after the title words
            title_words = title_norm.split()
            text_words_lower = [w.lower() for w in re.findall(r'[a-zA-Z]+', text)]
            if text_words_lower[:len(title_words)] == title_words:
                # Count characters consumed by the title words in original text
                consumed = 0
                words_matched = 0
                for m in re.finditer(r'[a-zA-Z]+', text):
                    if words_matched >= len(title_words):
                        break
                    words_matched += 1
                    consumed = m.end()
                remainder = text[consumed:].lstrip()
                remainder = re.sub(r'^[\s:,\-–—]+', '', remainder).strip()
                if remainder:
                    text = remainder

        # Clean up the subtitle
        subtitle = normalise_allcaps(text)
        # Collapse line breaks to spaces
        subtitle = re.sub(r'\s*\n\s*', ' ', subtitle)
        # Strip stray footnote markers (single digit immediately after a letter/punctuation,
        # not preceded by a space — "Job!1" → "Job!", but "May 2015" stays)
        subtitle = re.sub(r'([a-zA-Z!?."\'])\d$', r'\1', subtitle)

        # Skip if subtitle is just the title repeated (sometimes in ALL CAPS)
        if _normalise_for_match(subtitle) == _normalise_for_match(html_title):
            return None

        # This looks like a subtitle — use html_title as the authoritative title
        return (html_title, subtitle)

    return None


def _clean_split_title(title: str) -> str:
    """Clean up a title after splitting off the subtitle.

    Removes trailing punctuation that was a separator between title and subtitle
    (colons, dashes, opening parens/quotes) but preserves meaningful trailing
    punctuation (question marks, exclamation marks).
    """
    # Iteratively strip trailing separator punctuation + whitespace
    prev = None
    while title != prev:
        prev = title
        title = title.rstrip()
        title = re.sub(r'[\s:,\-–—(]+$', '', title)
        # Strip trailing orphaned quotes (opening quote without matching close)
        # But keep closing quotes that match an opening quote
        if title.endswith("'") and title.count("'") % 2 == 1:
            title = title[:-1]
        if title.endswith('"') and title.count('"') % 2 == 1:
            title = title[:-1]
    return title.rstrip()


def split_title(toc_title: str, subtitle: str) -> str | None:
    """Try to find where the subtitle starts in the concatenated toc title.

    Returns the title-only portion (cleaned), or None if the subtitle isn't found.
    Uses progressive word matching from the subtitle to find the split point.
    """
    subtitle_words = subtitle.split()
    if not subtitle_words:
        return None

    # Try matching progressively shorter prefixes of the subtitle
    # against the end of the toc title (case-insensitive)
    toc_lower = toc_title.lower()
    for n in range(len(subtitle_words), 1, -1):
        prefix = ' '.join(subtitle_words[:n]).lower()
        idx = toc_lower.rfind(prefix)
        if idx > 0:
            return _clean_split_title(toc_title[:idx])

    # Try first word only if it's distinctive enough
    first_word = subtitle_words[0].lower()
    if len(first_word) >= DISTINCTIVE_WORD_MIN_LENGTH:
        idx = toc_lower.rfind(first_word)
        if idx > len(toc_title) * SPLIT_POSITION_MIN_FRACTION:
            return _clean_split_title(toc_title[:idx])

    return None


# ---------------------------------------------------------------
# toc.json processing
# ---------------------------------------------------------------

def process_toc(toc_path: str, apply: bool = False, dry_run: bool = False) -> dict:
    """Process one toc.json file. Returns stats."""
    with open(toc_path) as f:
        toc = json.load(f)

    vol = toc.get('volume', '?')
    iss = toc.get('issue', '?')
    vol_dir = os.path.dirname(toc_path)

    stats = {'total': 0, 'subtitles_found': 0, 'details': []}
    modified = False

    for art in toc.get('articles', []):
        sp = art.get('split_pdf', '')
        if not sp:
            continue
        stats['total'] += 1

        # split_pdf may be a full relative path or bare filename — join with
        # vol_dir using just the stem to be safe regardless of working directory.
        stem = os.path.splitext(os.path.basename(sp))[0]
        raw_path = os.path.join(vol_dir, stem + '.raw.html')
        if not os.path.exists(raw_path):
            continue

        # Parse author names for subtitle detection (toc.json stays as string)
        auth_raw = art.get('authors', '')
        if isinstance(auth_raw, list):
            author_list = auth_raw
        elif auth_raw:
            author_list = parse_author_names(auth_raw)
        else:
            author_list = []

        # Skip if already has subtitle
        if art.get('subtitle'):
            continue

        # Detect subtitle
        with open(raw_path) as f:
            raw_html = f.read()

        section = art.get('section', '')
        toc_title = art.get('title', '')
        result = detect_subtitle(raw_html, author_list, section=section,
                                  toc_title=toc_title)
        if not result:
            continue

        # detect_subtitle returns (html_title, subtitle) — use the HTML
        # heading as the authoritative title (not the concatenated toc title)
        new_title, subtitle = result
        toc_title = art.get('title', '')

        if new_title and subtitle:
            # Review titles (Essay Review, Film Review) — append subtitle to title
            # instead of creating a separate subtitle field
            title_lower = new_title.lower().strip()
            is_review_title = title_lower in ('essay review', 'film review',
                                               'review', 'book reviews')
            if is_review_title:
                combined = new_title.rstrip(':') + ': ' + subtitle
                stats['subtitles_found'] += 1
                stats['details'].append({
                    'vol': f'{vol}.{iss}',
                    'old_title': toc_title,
                    'new_title': combined,
                    'subtitle': '(appended to title)',
                })
                if apply:
                    art['title'] = combined
                    modified = True
            else:
                stats['subtitles_found'] += 1
                stats['details'].append({
                    'vol': f'{vol}.{iss}',
                    'old_title': toc_title,
                    'new_title': new_title,
                    'subtitle': subtitle,
                })
                if apply:
                    art['title'] = new_title
                    art['subtitle'] = subtitle
                modified = True

    if apply and modified and not dry_run:
        with open(toc_path, 'w') as f:
            json.dump(toc, f, indent=2, ensure_ascii=False)
            f.write('\n')

    return stats


# ---------------------------------------------------------------
# CLI
# ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Extract subtitles from raw HTML into toc.json')
    parser.add_argument('toc_json', nargs='+', help='toc.json file(s)')

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--report', action='store_true',
                      help='Show detected subtitles without modifying files')
    mode.add_argument('--apply', action='store_true',
                      help='Write subtitle field to toc.json')

    parser.add_argument('--dry-run', action='store_true',
                        help='With --apply: show changes without writing')
    parser.add_argument('--report-file', metavar='PATH',
                        help='Write markdown review table to file (with --report)')

    args = parser.parse_args()

    total_articles = 0
    total_subtitles = 0
    all_details = []

    for toc_path in args.toc_json:
        if not os.path.exists(toc_path):
            print(f'WARNING: {toc_path} not found, skipping', file=sys.stderr)
            continue

        stats = process_toc(toc_path, apply=args.apply, dry_run=args.dry_run)
        total_articles += stats['total']
        total_subtitles += stats['subtitles_found']
        all_details.extend(stats['details'])

    prefix = '[DRY RUN] ' if args.dry_run else ''
    print(f'\n{"=" * 60}')
    print(f'{prefix}SUBTITLE EXTRACTION')
    print(f'{"=" * 60}')
    print(f'  Articles scanned:     {total_articles}')
    print(f'  Subtitles found:      {total_subtitles}')
    print(f'{"=" * 60}\n')

    if all_details:
        for d in all_details:
            print(f'  {d["vol"]}: "{d["new_title"]}"')
            print(f'       sub: "{d["subtitle"]}"')
            print()

    if args.report_file and all_details:
        lines = ['# Subtitle Review', '',
                 '| # | Issue | Proposed Title | Subtitle |',
                 '|---|-------|---------------|----------|']
        for i, d in enumerate(all_details, 1):
            title = d['new_title'].replace('|', '\\|')
            sub = d['subtitle'].replace('|', '\\|')
            lines.append(f'| {i} | {d["vol"]} | {title} | {sub} |')
        with open(args.report_file, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        print(f'Report written to {args.report_file}')


if __name__ == '__main__':
    main()
