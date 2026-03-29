#!/usr/bin/env python3
"""
Extract subtitles from raw HTML and split title/subtitle in toc.json.

Also normalises the `authors` field from string to list.

The raw HTML from htmlgen has titles and subtitles as separate elements:
  <h1>Being With Cyril</h1>
  <p>Can Heideggerian language do justice to...</p>
  <h2>Titos Florides</h2>

But toc.json has them concatenated into one title string. This script
detects the split point using the raw HTML structure and known author
names, then writes a `subtitle` field to toc.json.

Usage:
    python3 backfill/extract_subtitles.py --report backfill/private/output/*/toc.json
    python3 backfill/extract_subtitles.py --apply backfill/private/output/23.1/toc.json
    python3 backfill/extract_subtitles.py --apply --dry-run backfill/private/output/*/toc.json
"""

import argparse
import json
import os
import re
import sys


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

# Patterns for conference/presentation notes (not subtitles, but provenance)
PROVENANCE_PATTERNS = [
    re.compile(r'^(?:This\s+)?(?:paper|article|talk|presentation)\s+(?:was\s+)?(?:given|presented|delivered)', re.I),
    re.compile(r'^Presentation\s+given\s+at', re.I),
]


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


def normalise_for_match(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = re.sub(r'[^a-zA-Z\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()


# Max words in a text element to be considered a potential name
NAME_MAX_WORDS = 6

# Words that appear in subtitles/headings but not in names — if ANY of these
# appear, the text is not a name. Covers common English prepositions, articles,
# conjunctions that wouldn't appear in a person's name.
_NOT_NAME_WORDS = frozenset({
    'a', 'an', 'the',                         # articles
    'in', 'on', 'at', 'to', 'by', 'of',      # prepositions
    'for', 'from', 'with', 'about', 'into',
    'as', 'or', 'but', 'nor', 'yet', 'so',   # conjunctions
    'is', 'was', 'are', 'were', 'be',         # verbs
    'some', 'towards', 'between', 'toward',
    'why', 'how', 'what', 'when', 'where',
    'not', 'its', 'it', 'this', 'that',
})

# Words allowed in names (nobiliary particles)
_NAME_PARTICLES = frozenset({
    'van', 'de', 'du', 'von', 'le', 'la', 'di', 'el', 'al',
    'bin', 'del', 'der', 'dos', 'den', 'das', 'ibn',
})


def _looks_like_person_name(text: str) -> bool:
    """Check if text looks like a person name (not a subtitle or heading).

    Works for Western and non-Western names by checking structure:
    - Short (within NAME_MAX_WORDS)
    - No common English words that wouldn't appear in names
    - Each word is either capitalised, an initial (A.), or a name particle
    - At least 2 capitalised words (to avoid "A Rejoinder" matching)

    Handles: "Titos Florides", "Emmy van Deurzen", "R.D. Laing",
    "Ann-Helen Siirala", "Jiří Různička", "Rimantas Koėiūnas",
    "Evgenia T. Georganda", "Hans W. Cohn"
    """
    clean = text.rstrip('.').strip()
    if not clean or len(clean) > 80:
        return False
    if clean[-1] in '?!':
        return False

    words = clean.split()
    if len(words) < 2 or len(words) > NAME_MAX_WORDS:
        return False

    # If ANY lowercase word is a common non-name word, it's not a name.
    # Only check words that are actually lowercase (not capitalised names
    # that happen to match — "Del" shouldn't match "del")
    for w in words:
        w_clean = w.rstrip('.,;:')
        if w_clean and w_clean[0].islower() and w_clean.lower() in _NOT_NAME_WORDS:
            return False

    # "A [Word]" is an English article + noun, not initial + surname
    if words[0] == 'A' and len(words) == 2:
        return False

    # For "Name and Name" / "Name & Name", split and check each half
    if ' and ' in clean or ' & ' in clean:
        parts = re.split(r'\s+(?:and|&)\s+', clean)
        if len(parts) == 2 and all(_is_single_name(p.strip()) for p in parts):
            return True

    return _is_single_name(clean)


def _is_single_name(text: str) -> bool:
    """Check if text is a single person name (no 'and'/'&' connectors)."""
    words = text.split()
    if len(words) < 2 or len(words) > NAME_MAX_WORDS:
        return False

    cap_count = 0
    for w in words:
        w_clean = w.rstrip('.,;:')
        # Nobiliary particles are lowercase by convention (van, de, du).
        # Only skip if actually lowercase in the text — "Del" as a given
        # name should not be treated as the particle "del".
        if w_clean[0].islower() and w_clean.lower() in _NAME_PARTICLES:
            continue
        # Initials: "W.", "R.D.", "F." — must have a dot to distinguish
        # from articles ("A Rejoinder" — "A" without dot is not an initial)
        if re.match(r'^[A-ZÀ-Ž]\.$', w_clean) or re.match(r'^(?:[A-ZÀ-Ž]\.)+$', w_clean):
            cap_count += 1
            continue
        # Single capital letter without dot — only count if followed by another name
        # (e.g. "Kirk J Schneider" — "J" is an initial)
        if re.match(r'^[A-ZÀ-Ž]$', w_clean):
            cap_count += 1
            continue
        # Capitalised word (including hyphenated: Ann-Helen, Merleau-Ponty)
        if re.match(r'^[A-ZÀ-Ž]', w_clean):
            cap_count += 1
            continue
        # Hyphenated with particle prefix: al-Rashid, el-Sayed
        if '-' in w_clean:
            prefix = w_clean.split('-')[0].lower()
            if prefix in _NAME_PARTICLES:
                cap_count += 1
                continue
        # Lowercase word that isn't a particle = not a name
        return False

    return cap_count >= 2


def text_matches_author(text: str, author_names: list[str]) -> bool:
    """Check if text is (or contains) a known author name."""
    text_norm = normalise_for_match(text)
    for name in author_names:
        name_norm = normalise_for_match(name)
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
    """Check if text is a conference/presentation provenance note."""
    return any(p.search(text) for p in PROVENANCE_PATTERNS)


# Words that stay lowercase in title case (unless first word)
TITLE_CASE_LOWERCASE = frozenset({
    'a', 'an', 'the', 'and', 'but', 'or', 'nor', 'for', 'yet', 'so',
    'in', 'on', 'at', 'to', 'by', 'of', 'as', 'is', 'it',
})


def normalise_caps(text: str) -> str:
    """Convert ALL CAPS text to title case, preserving quoted phrases.

    Only applies if the entire text (ignoring punctuation) is uppercase.
    """
    # Check if text is all caps (ignoring punctuation and whitespace)
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars or not all(c.isupper() for c in alpha_chars):
        return text

    # Title-case each word, keeping small words lowercase (except first)
    words = text.split()
    result = []
    for i, word in enumerate(words):
        # Preserve punctuation wrapping: strip, case, re-add
        prefix = ''
        suffix = ''
        core = word
        while core and not core[0].isalpha():
            prefix += core[0]
            core = core[1:]
        while core and not core[-1].isalpha():
            suffix = core[-1] + suffix
            core = core[:-1]

        if not core:
            result.append(word)
            continue

        lower = core.lower()
        if i > 0 and lower in TITLE_CASE_LOWERCASE:
            result.append(prefix + lower + suffix)
        else:
            result.append(prefix + core.capitalize() + suffix)

    return ' '.join(result)


def strip_html_tags(html: str) -> str:
    """Remove HTML tags, decode entities."""
    text = re.sub(r'<[^>]+>', '', html)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    return text.strip()


def detect_subtitle(raw_html: str, author_names: list[str],
                     section: str = '') -> str | None:
    """Detect a subtitle in the raw HTML after the title heading.

    Returns (html_title, subtitle) tuple, or None if no subtitle detected.
    html_title is the text from the <h1>/<h2> heading (source of truth for title).
    Skips book reviews (title is "Book Review: [Book Title]").
    """
    # Skip sections where titles shouldn't be split
    section_lower = section.lower()
    if 'book review' in section_lower:
        return None  # Book reviews keep full title ("Book Review: [Book Title]")
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

    html_title = normalise_caps(strip_html_tags(title_match.group(1)).strip())

    # Skip titles that are structural headings (not article titles)
    title_lower = html_title.lower().strip()
    if title_lower in ('contents', 'letters to the editors', 'letters to the editor',
                        'letter to the editors', 'letter to the editor',
                        'obituary', 'obituaries', 'editorial'):
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
        if _looks_like_person_name(text):
            return None

        # Skip section headings
        if is_section_heading(text):
            return None

        # Skip book review metadata
        if is_book_review_metadata(text):
            return None

        # Skip provenance notes
        if is_provenance_note(text):
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

        # This looks like a subtitle — use html_title as the authoritative title
        return (html_title, normalise_caps(text))

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

    stats = {'total': 0, 'subtitles_found': 0, 'authors_normalised': 0, 'details': []}
    modified = False

    for art in toc.get('articles', []):
        sp = art.get('split_pdf', '')
        if not sp:
            continue
        stats['total'] += 1

        raw_path = os.path.splitext(sp)[0] + '.raw.html'
        if not os.path.exists(raw_path):
            continue

        # Normalise authors from string to list
        auth_raw = art.get('authors', '')
        if isinstance(auth_raw, str) and auth_raw:
            author_list = parse_author_names(auth_raw)
            if apply:
                art['authors'] = author_list
                modified = True
            stats['authors_normalised'] += 1
        else:
            author_list = auth_raw if isinstance(auth_raw, list) else []

        # Skip if already has subtitle
        if art.get('subtitle'):
            continue

        # Detect subtitle
        with open(raw_path) as f:
            raw_html = f.read()

        section = art.get('section', '')
        result = detect_subtitle(raw_html, author_list, section=section)
        if not result:
            continue

        # detect_subtitle returns (html_title, subtitle) — use the HTML
        # heading as the authoritative title (not the concatenated toc title)
        new_title, subtitle = result
        toc_title = art.get('title', '')

        if new_title and subtitle:
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
                      help='Write subtitle field to toc.json and normalise authors')

    parser.add_argument('--dry-run', action='store_true',
                        help='With --apply: show changes without writing')

    args = parser.parse_args()

    total_articles = 0
    total_subtitles = 0
    total_authors = 0
    all_details = []

    for toc_path in args.toc_json:
        if not os.path.exists(toc_path):
            print(f'WARNING: {toc_path} not found, skipping', file=sys.stderr)
            continue

        stats = process_toc(toc_path, apply=args.apply, dry_run=args.dry_run)
        total_articles += stats['total']
        total_subtitles += stats['subtitles_found']
        total_authors += stats['authors_normalised']
        all_details.extend(stats['details'])

    prefix = '[DRY RUN] ' if args.dry_run else ''
    print(f'\n{"=" * 60}')
    print(f'{prefix}SUBTITLE EXTRACTION')
    print(f'{"=" * 60}')
    print(f'  Articles scanned:     {total_articles}')
    print(f'  Subtitles found:      {total_subtitles}')
    print(f'  Authors normalised:   {total_authors}')
    print(f'{"=" * 60}\n')

    if all_details:
        for d in all_details:
            print(f'  {d["vol"]}: "{d["new_title"]}"')
            print(f'       sub: "{d["subtitle"]}"')
            print()


if __name__ == '__main__':
    main()
