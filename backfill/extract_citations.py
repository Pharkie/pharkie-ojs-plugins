#!/usr/bin/env python3
"""Extract end-reference citations from HTML galleys into toc.json.

Reads HTML galleys, finds reference sections (References, Bibliography, Notes
with citation-like items), extracts individual citation strings, and writes
them to each article's toc.json entry as "citations": [...].

Decisions (from audit review):
- Extract from References/Bibliography/Works Cited headings
- Also extract citation-like items from Notes/Endnotes/Footnotes sections
  (items with year + author pattern), skip pure commentary notes
- Filter junk: drop items < 15 chars or missing year/author patterns
- Skip inline/in-text citations — end references only

Usage:
    python3 backfill/extract_citations.py --dry-run     # preview, no writes
    python3 backfill/extract_citations.py                # write to toc.json
    python3 backfill/extract_citations.py --volume 37.1  # single volume
    python3 backfill/extract_citations.py --sheet        # also publish to Google Sheet

Output:
    Updates toc.json files with "citations" field per article
    Optional: Google Sheet with one row per citation
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"

# ---------------------------------------------------------------
# HTML parsing (shared with audit_citations.py)
# ---------------------------------------------------------------

class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags, return plain text."""
    def __init__(self):
        super().__init__()
        self._text = []

    def handle_data(self, data):
        self._text.append(data)

    def get_text(self):
        return "".join(self._text).strip()


def strip_html(html_str: str) -> str:
    extractor = HTMLTextExtractor()
    extractor.feed(html_str)
    return extractor.get_text()


# Headings that indicate a reference/citation section
# Allow trailing punctuation (.,:) and optional extras like "& Filmography"
REFERENCE_HEADINGS = re.compile(
    r'<h2>\s*('
    r'References?'
    r'|Notes?'
    r'|Endnotes?'
    r'|Footnotes?:?'
    r'|Bibliography'
    r'|Further Reading'
    r'|Works Cited'
    r'|Notes and References'
    r'|References and Notes'
    r'|Bibliography and References'
    r'|Further References'
    r'|References and Bibliography'
    r'|References and further reading'
    r'|Selected Bibliography'
    r'|References:'
    r')(?:\s*[&;]\s*\w+)*'  # optional "& Filmography", "&amp; Filmography", etc.
    r'(?:\s*\([^)]*\))?'  # optional parenthetical like "(including some key...)"
    r'[.:]?\s*</h2>',
    re.IGNORECASE
)

# Headings that are "pure reference" sections (always extract all items)
PURE_REFERENCE_HEADINGS = re.compile(
    r'^(References?|Bibliography|Works Cited|Further Reading'
    r'|Further References'
    r'|References and Bibliography|References and further reading'
    r'|Selected Bibliography|References:|References and Notes'
    r'|Bibliography and References'
    r'|Notes and References)(?:\s*[&;]\s*\w+)*(?:\s*\([^)]*\))?[.:]?$',
    re.IGNORECASE
)

# Headings that are "notes" sections (only extract citation-like items)
NOTES_HEADINGS = re.compile(
    r'^(Notes?|Endnotes?|Footnotes?:?)[.:]?$',
    re.IGNORECASE
)


def find_reference_sections(html_content: str, tail_only: bool = False) -> list[dict]:
    """Find reference-like sections in an HTML galley.

    If tail_only=True, only return sections that form a contiguous block at
    the end of the document (matching strip_references.py behaviour). This
    prevents extracting mid-article Notes/References that shouldn't be stripped.
    """
    h2_pattern = re.compile(r'<h2[^>]*>(.*?)</h2>', re.IGNORECASE | re.DOTALL)
    headings = list(h2_pattern.finditer(html_content))

    if not headings:
        return []

    # Build list of (heading_text, is_reference, heading_index, match)
    heading_info = []
    for i, match in enumerate(headings):
        heading_text = strip_html(match.group(1)).strip()
        is_ref = bool(REFERENCE_HEADINGS.match(f'<h2>{heading_text}</h2>'))
        heading_info.append((heading_text, is_ref, i, match))

    if tail_only:
        # Walk backwards to find contiguous reference headings at the tail
        tail_start = None
        for si in range(len(heading_info) - 1, -1, -1):
            if heading_info[si][1]:  # is_reference
                tail_start = si
            else:
                break
        if tail_start is None:
            return []
        # Only process headings from tail_start onwards
        heading_info = heading_info[tail_start:]

    sections = []
    for heading_text, is_ref, i, match in heading_info:
        if not is_ref:
            continue

        start = match.end()
        if i + 1 < len(headings):
            end = headings[i + 1].start()
        else:
            end = len(html_content)

        content_html = html_content[start:end].strip()
        items, structure = extract_citation_items(content_html)

        sections.append({
            'heading': heading_text,
            'items': items,
            'structure': structure,
        })

    return sections


def _count_embedded_citations(text: str) -> int:
    """Estimate how many citations are concatenated in a block of text."""
    years = re.findall(r'\b(?:1[89]\d{2}|20[0-2]\d)\b', text)
    return len(years)


def citation_confidence(text: str, heading: str) -> int:
    """Score 0-100 how confident we are this is a single, clean citation.

    High confidence (80-100): looks like a standard bibliographic reference.
    Medium (50-79): probably a citation but something's off (long, from Notes, etc.).
    Low (0-49): likely commentary, bio, concatenated block, or junk.
    """
    score = 50  # start neutral

    length = len(text)
    is_notes = bool(NOTES_HEADINGS.match(heading))
    is_refs = bool(PURE_REFERENCE_HEADINGS.match(heading))

    # --- Positive signals ---

    # Year in parentheses: strong citation signal
    if re.search(r'\(\d{4}\)', text):
        score += 12
    # Year without parens: weaker signal
    elif re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', text):
        score += 6

    # Author pattern at start: "Lastname, I." or "Lastname I."
    if re.match(r'^[A-Z][a-zà-ü]+,?\s+[A-Z]\.', text):
        score += 12
    # Numbered note starting with citation: "1. Lastname..."
    elif re.match(r'^\d+[\.\)]\s+[A-Z][a-zà-ü]+', text):
        score += 6

    # Publisher/press name
    if re.search(r'(Press|Publisher|Books|Routledge|Sage|Springer|Wiley|Penguin|'
                 r'Palgrave|Harper|Random House|Vintage)', text, re.IGNORECASE):
        score += 8

    # Journal name pattern
    if re.search(r'(Journal|Review|Quarterly|Bulletin|Annals|Archives)\s+of\b', text, re.IGNORECASE):
        score += 8

    # Page range (pp. 123-456 or 123–456)
    if re.search(r'(pp?\.?\s*\d+[-–]\d+|\b\d+[-–]\d+\b)', text):
        score += 6

    # DOI
    if re.search(r'doi[:\s]|10\.\d{4,}/', text, re.IGNORECASE):
        score += 10

    # URL (weaker — could be anything)
    if re.search(r'https?://', text):
        score += 4

    # Italic/em markers in source suggest a title
    # (already stripped, but "Trans." or "ed." patterns remain)
    if re.search(r'\b(Trans\.|trans\.|Transl\.|ed\.|eds\.|Ed\.|Vol\.)', text):
        score += 4

    # Place of publication
    if re.search(r'(London|New York|Cambridge|Oxford|Paris|Berlin|Chicago|Boston)\s*:', text):
        score += 6

    # From a References/Bibliography heading: boost
    if is_refs:
        score += 8

    # --- Negative signals ---

    # From a Notes heading: penalty
    if is_notes:
        score -= 10

    # Length penalties
    if length < 30:
        score -= 15  # very short, likely fragment
    elif length > 500:
        score -= 20  # very long, likely commentary or concat
    elif length > 300:
        score -= 8

    # Multiple years = likely concatenated block (but only penalise if long)
    year_count = len(re.findall(r'\b(?:1[89]\d{2}|20[0-2]\d)\b', text))
    if year_count > 3 and length > 200:
        score -= 25  # almost certainly concatenated
    elif year_count > 2 and length > 150:
        score -= 12

    # Prose indicators (sentences, not a citation)
    sentence_count = len(re.findall(r'[.!?]\s+[A-Z]', text))
    if sentence_count > 3:
        score -= 15  # multiple sentences = commentary
    elif sentence_count > 1 and length > 200:
        score -= 8

    # Author bio patterns
    if _is_author_bio(text):
        score -= 40

    # Starts with "The", "This", "It", "In" — prose, not a citation
    if re.match(r'^(The|This|It|In|As|For|We|He|She|A)\s', text) and not re.match(r'^The\s', text[:20]):
        score -= 10

    # "Ibidem" / "Ibid" / "Op cit" — footnote shorthand, not a full citation
    if re.search(r'\b(Ibid\.?|Ibidem|Op\.?\s*cit)', text, re.IGNORECASE):
        score -= 15

    return max(0, min(100, score))


def extract_citation_items(content_html: str) -> tuple[list[str], str]:
    """Extract individual citation strings from a reference section's HTML."""
    li_pattern = re.compile(r'<li[^>]*>(.*?)</li>', re.IGNORECASE | re.DOTALL)
    li_matches = li_pattern.findall(content_html)

    p_pattern = re.compile(r'<p[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)
    p_matches = p_pattern.findall(content_html)

    if li_matches and len(li_matches) > len(p_matches):
        items = [strip_html(li).strip() for li in li_matches if strip_html(li).strip()]
        structure = 'ol_li'
    elif p_matches:
        raw_items = [strip_html(p).strip() for p in p_matches if strip_html(p).strip()]
        items = raw_items
        numbered = sum(1 for item in items if re.match(r'^\d+[\.\)]\s', item))
        structure = 'numbered_p' if numbered > len(items) * 0.5 else 'p_tags'
    else:
        lines = [strip_html(line).strip() for line in content_html.split('\n')
                 if strip_html(line).strip()]
        items = [l for l in lines if len(l) > 10]
        structure = 'raw_lines'

    return items, structure


def is_citation_like(text: str) -> bool:
    """Check if a text item looks like it contains a citation.

    Used to filter Notes/Endnotes — keep items with year + author pattern,
    skip pure commentary.
    """
    if len(text) < 15:
        return False

    has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', text))
    has_author_pattern = bool(re.search(r'[A-Z][a-zà-ü]+,?\s', text))
    has_publisher = bool(re.search(
        r'(Press|Publisher|Books|University|Routledge|Sage|Springer|Wiley|Oxford|Cambridge)',
        text, re.IGNORECASE))
    has_journal = bool(re.search(
        r'(Journal|Review|Quarterly|Analysis|Psycholog|Psychother|Existential)',
        text, re.IGNORECASE))
    has_pages = bool(re.search(r'\b\d+[-–]\d+\b', text))
    has_doi = bool(re.search(r'doi[:\s]|10\.\d{4,}/', text, re.IGNORECASE))
    has_url = bool(re.search(r'https?://', text))

    score = sum([has_year, has_author_pattern, has_publisher, has_journal,
                 has_pages, has_doi, has_url])

    # Extended commentary detection
    sentence_count = len(re.findall(r'[.!?]\s+[A-Z]', text))

    # Multiple sentences + long = commentary, not a citation
    if sentence_count >= 3 and len(text) > 200:
        return False

    # Even 2 sentences if very long = commentary
    if sentence_count >= 2 and len(text) > 350:
        return False

    # Very long single block with no clear citation structure = commentary
    if len(text) > 400 and score < 3:
        return False

    return score >= 2 or (score >= 1 and has_year)


def is_junk(text: str) -> bool:
    """Filter out non-citation junk from reference sections."""
    if len(text) < 15:
        return True

    has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', text))
    has_author_pattern = bool(re.search(r'[A-Z][a-zà-ü]+,?\s', text))

    # No year AND no author pattern = probably junk
    if not has_year and not has_author_pattern:
        return True

    # Obviously not a citation
    if text.strip().rstrip('.').lower() in ('yours sincerely', 'yours faithfully',
                                             'kind regards', 'best wishes'):
        return True

    # Looks like just a person's name (reviewer byline that slipped into refs)
    stripped = text.strip().rstrip('.')
    if (not has_year
            and re.match(r'^(Dr\.?\s+)?[A-Z][a-zà-ü]+(\s+[A-Z]\.?)*(\s+[A-Z][a-zà-ü-]+){0,3}$', stripped)
            and len(stripped.split()) <= 5):
        return True

    # Author bios and article provenance notes
    if _is_author_bio(text):
        return True

    # "This article/paper is a revised version of..."
    if re.match(r'^This (article|paper|chapter|essay|lecture|talk)\s+(is|was)\s', text):
        return True

    return False


def extract_article_citations(html_path: Path) -> tuple[list[dict], list[str]]:
    """Extract citation strings from an HTML galley file.

    Returns (citations, endmatter):
    - citations: list of dicts with 'text', 'heading', 'confidence' keys.
      These are items that look like bibliographic references.
    - endmatter: list of plain text strings for items that don't look
      like citations (bios, prose endnotes, provenance notes, ibid-style
      refs, short fragments). Captured to prevent content loss when HTML
      reference sections are stripped.
    """
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    sections = find_reference_sections(content, tail_only=True)
    citations = []
    endmatter = []

    for sec in sections:
        heading = sec['heading']
        is_notes = bool(NOTES_HEADINGS.match(heading))

        for item in sec['items']:
            if is_junk(item):
                endmatter.append(item)
                continue

            if is_notes and not is_citation_like(item):
                endmatter.append(item)
                continue

            confidence = citation_confidence(item, heading)

            citations.append({
                'text': item,
                'heading': heading,
                'confidence': confidence,
            })

    return citations, endmatter


def _is_author_bio(text: str) -> bool:
    """Detect author biographical notes mistakenly in reference sections."""
    # Quick check: does the text contain bio-indicator phrases?
    bio_phrases = [
        'is a ', 'is an ', 'was a ', 'was an ',
        'private practice', 'in practice', 'practitioner',
        'working in', 'works with', 'works as',
        'academic interests', 'has been a ',
        'Research Fellow', 'has a particular interest',
        'currently in private', 'currently a ',
    ]
    has_bio_phrase = any(phrase in text for phrase in bio_phrases)

    bio_patterns = [
        # ALL CAPS name followed by "is/was/has..."
        r'^[A-Z][A-Z\s\.]+\b(is|was|has)\s',
        # Mixed case name followed by "is/was/has..."
        r'^[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]+(\s+[A-Z][a-zà-ü]+)?\s+(is|was|has)\s',
        # "All three/four authors..."
        r'^All (three|four|five|six) authors',
        # "Dr/Professor Name..."
        r'^(Dr\.?|Professor)\s+[A-Z][a-z]',
        # Name + "PhD" or "MA" or credentials
        r'^[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]+\s+(PhD|MA|MSc|UKCP|BPS)',
    ]

    if any(re.match(p, text) for p in bio_patterns):
        return True

    # Name-like start + bio phrase in first 200 chars
    if has_bio_phrase and re.match(r'^[A-Z][a-zà-ü]+\s', text) and len(text) > 50:
        # Check it's not a citation that happens to contain "is a"
        # Bios don't have years in parentheses near the start
        if not re.search(r'\(\d{4}\)', text[:80]):
            return True

    return False


def process_all(volume_filter=None, dry_run=False, verbose=False):
    """Extract citations from all HTML galleys and update toc.json files.

    Returns list of (volume, issue, article_title, citations) for sheet export.
    """
    if volume_filter:
        toc_files = [OUTPUT_DIR / volume_filter / 'toc.json']
        if not toc_files[0].exists():
            print(f"ERROR: {toc_files[0]} not found")
            sys.exit(1)
    else:
        toc_files = sorted(OUTPUT_DIR.glob('*/toc.json'))

    stats = Counter()
    all_rows = []  # For sheet export

    # Sort toc files numerically by volume.issue
    def vol_sort_key(path):
        name = path.parent.name
        parts = name.split('.')
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 1)
        except ValueError:
            return (999, 999)

    toc_files = sorted(toc_files, key=vol_sort_key)

    for toc_path in toc_files:
        vol_dir = toc_path.parent
        vol_name = vol_dir.name

        with open(toc_path) as f:
            toc = json.load(f)

        volume = toc.get('volume', 0)
        issue = toc.get('issue', 0)
        date = toc.get('date', '')
        modified = False

        for article in toc.get('articles', []):
            section = article.get('section', 'Unknown')
            split_pdf = article.get('split_pdf', '')
            slug = Path(split_pdf).stem if split_pdf else ''
            if not slug:
                continue

            html_path = vol_dir / f"{slug}.html"
            if not html_path.exists():
                stats['no_html'] += 1
                continue

            stats['total'] += 1
            citation_items, filtered = extract_article_citations(html_path)

            if citation_items or filtered:
                stats['with_citations'] += 1
                stats['total_citations'] += len(citation_items)
                stats['total_filtered'] += len(filtered)
                low_conf = sum(1 for c in citation_items if c['confidence'] < 50)
                stats['low_confidence'] += low_conf

                # toc.json gets all extracted items — confidence is for review only
                if not dry_run:
                    article['citations'] = [c['text'] for c in citation_items]
                    # Store which heading the citations came from
                    headings_found = list(dict.fromkeys(
                        c['heading'] for c in citation_items))
                    article['citation_headings'] = headings_found
                    # Classify non-citation items into proper fields
                    bios = []
                    prov = []
                    extra_notes = []
                    for item in filtered:
                        if _is_author_bio(item):
                            bios.append(item)
                        elif re.match(r'^This (article|paper|chapter|essay|lecture|talk)\s+(is|was)\s', item):
                            prov.append(item)
                        else:
                            extra_notes.append(item)
                    if bios:
                        article['author_bios'] = bios
                    elif 'author_bios' in article:
                        del article['author_bios']
                    if prov:
                        article['provenance'] = prov[0] if len(prov) == 1 else ' '.join(prov)
                    elif 'provenance' in article:
                        del article['provenance']
                    # extra_notes merge into notes[] after split_citation_tiers runs
                    modified = True

                if verbose:
                    filt_note = f", {len(filtered)} filtered" if filtered else ""
                    note = f" ({low_conf} low confidence)" if low_conf else ""
                    print(f"  ✓ {vol_name}/{slug}: {len(citation_items)} citations{note}{filt_note}")

                # Collect rows for sheet export (ALL items, reviewable)
                title = article.get('title', '')
                authors = article.get('authors', '')
                for seq, cit in enumerate(citation_items, 1):
                    all_rows.append([
                        volume, issue, date, section, title, authors,
                        seq, cit['heading'], cit['confidence'],
                        cit['text'],
                        '',  # Matched DOI — populated in Phase 4
                        '',  # Crossref Citation — populated in Phase 4
                    ])
            else:
                stats['without_citations'] += 1
                if verbose:
                    print(f"  ✗ {vol_name}/{slug}: no citations")

        if modified and not dry_run:
            with open(toc_path, 'w') as f:
                json.dump(toc, f, indent=2, ensure_ascii=False)
                f.write('\n')

    return stats, all_rows


SHEET_HEADERS = [
    'Volume', 'Issue', 'Date', 'Section', 'Article Title', 'Authors',
    'Seq', 'Heading', 'Class', 'Confidence', 'Citation', 'Matched DOI', 'Crossref Citation',
]


def load_citations_for_sheet(volume_filter=None):
    """Load citations from JATS XML files (source of truth) for sheet export.

    Reads references, notes, author bios, and provenance from .jats.xml files,
    with article metadata from toc.json.

    Returns rows suitable for sheet export.
    """
    from xml.etree import ElementTree as ET

    if volume_filter:
        toc_files = [OUTPUT_DIR / volume_filter / 'toc.json']
        if not toc_files[0].exists():
            print(f"ERROR: {toc_files[0]} not found")
            sys.exit(1)
    else:
        toc_files = sorted(OUTPUT_DIR.glob('*/toc.json'))

    def vol_sort_key(path):
        name = path.parent.name
        parts = name.split('.')
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 1)
        except ValueError:
            return (999, 999)

    toc_files = sorted(toc_files, key=vol_sort_key)
    rows = []

    for toc_path in toc_files:
        with open(toc_path) as f:
            toc = json.load(f)

        vol_dir = toc_path.parent
        volume = toc.get('volume', 0)
        issue = toc.get('issue', 0)
        date = toc.get('date', '')

        for article in toc.get('articles', []):
            split_pdf = article.get('split_pdf', '')
            slug = Path(split_pdf).stem if split_pdf else ''
            if not slug:
                continue

            jats_path = vol_dir / f'{slug}.jats.xml'
            if not jats_path.exists():
                continue

            try:
                tree = ET.parse(jats_path)
            except ET.ParseError:
                continue

            section = article.get('section', 'Unknown')
            title = article.get('title', '')
            authors = article.get('authors', '')

            seq = 0
            for ref in tree.findall('.//{*}mixed-citation'):
                if ref.text and ref.text.strip():
                    seq += 1
                    text = ref.text.strip()
                    confidence = citation_confidence(text, 'References')
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, 'References', 'reference', confidence,
                        text, '', '',
                    ])

            for fn in tree.findall('.//{*}fn'):
                p = fn.find('{*}p')
                if p is not None and p.text and p.text.strip():
                    seq += 1
                    text = p.text.strip()
                    confidence = citation_confidence(text, 'Notes')
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, 'Notes', 'note', confidence,
                        text, '', '',
                    ])

            for bio in tree.findall('.//{*}bio'):
                p = bio.find('{*}p')
                if p is not None and p.text and p.text.strip():
                    seq += 1
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, '', 'author_bio', 0,
                        p.text.strip(), '', '',
                    ])

            prov = tree.find('.//{*}notes[@notes-type="provenance"]')
            if prov is not None:
                p = prov.find('{*}p')
                if p is not None and p.text and p.text.strip():
                    seq += 1
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, '', 'provenance', 0,
                        p.text.strip(), '', '',
                    ])

    return rows


def export_to_sheet(rows, dry_run=False):
    """Publish citation rows to a 'Citations' tab in the existing backfill spreadsheet."""
    import gspread

    SHEET_KEY = '189pMpS12ZuxYtMS2iLYiHKhwp6N972nZSTuNrea8Mos'
    CREDS_FILE = os.path.join(os.path.dirname(__file__), '..', 'data-export',
                              'sea-journal-87a19feadadd.json')
    gc = gspread.service_account(filename=CREDS_FILE)
    sh = gc.open_by_key(SHEET_KEY)

    # Create or reuse 'Citations' tab
    TAB_NAME = 'Citations'
    try:
        ws = sh.worksheet(TAB_NAME)
        ws.clear()
        # Ensure sheet has enough rows for all data
        needed_rows = len(rows) + 1  # +1 for header
        if ws.row_count < needed_rows:
            ws.resize(rows=needed_rows)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB_NAME, rows=len(rows) + 1,
                              cols=len(SHEET_HEADERS))

    all_data = [SHEET_HEADERS] + rows
    print(f"Publishing {len(rows)} citation rows to '{TAB_NAME}' tab...")

    # Write in batches (gspread has a limit per request)
    BATCH_SIZE = 5000
    for i in range(0, len(all_data), BATCH_SIZE):
        batch = all_data[i:i + BATCH_SIZE]
        cell = f'A{i + 1}'
        ws.update(range_name=cell, values=batch)
        print(f"  Wrote rows {i + 1}–{i + len(batch)}")

    # Format header
    col_letter = chr(ord('A') + len(SHEET_HEADERS) - 1)
    ws.format(f'A1:{col_letter}1', {'textFormat': {'bold': True}})
    ws.freeze(rows=1)

    # Set basic filter on all columns (enables dropdown arrows in header)
    num_rows = len(rows) + 1  # +1 for header
    num_cols = len(SHEET_HEADERS)
    ws.set_basic_filter(f'A1:{col_letter}{num_rows}')

    # Sort by Confidence ascending (column I = index 8) so low-confidence
    # items are at the top for review. User can re-sort in the UI.
    confidence_col_index = SHEET_HEADERS.index('Confidence')
    body = {
        'requests': [{
            'sortRange': {
                'range': {
                    'sheetId': ws.id,
                    'startRowIndex': 1,  # skip header
                    'endRowIndex': num_rows,
                    'startColumnIndex': 0,
                    'endColumnIndex': num_cols,
                },
                'sortSpecs': [{
                    'dimensionIndex': confidence_col_index,
                    'sortOrder': 'ASCENDING',
                }],
            }
        }]
    }
    sh.batch_update(body)

    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid={ws.id}"
    print(f"\nPublished to: {sheet_url}")
    return sheet_url


def main():
    parser = argparse.ArgumentParser(
        description='Extract citations from HTML galleys and/or publish to Google Sheet',
        epilog='Examples:\n'
               '  python3 backfill/extract_citations.py --extract          # HTML → toc.json (run once)\n'
               '  python3 backfill/extract_citations.py --extract --dry-run  # preview only\n'
               '  python3 backfill/extract_citations.py --sheet            # toc.json → Google Sheet\n'
               '  python3 backfill/extract_citations.py --extract --sheet  # both\n',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--extract', action='store_true',
                        help='Extract citations from HTML galleys into toc.json')
    parser.add_argument('--sheet', action='store_true',
                        help='Publish citations from toc.json to Google Sheet')
    parser.add_argument('--volume', help='Process a single volume (e.g. 37.1)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview extraction without writing to toc.json')
    parser.add_argument('--verbose', action='store_true',
                        help='Print details for each file')
    args = parser.parse_args()

    if not args.extract and not args.sheet:
        parser.error('Specify --extract, --sheet, or both')

    if args.extract:
        print("Extracting citations from HTML galleys → toc.json...")
        if args.dry_run:
            print("(DRY RUN — no files will be modified)\n")

        stats, _ = process_all(
            volume_filter=args.volume,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

        print("\n" + "=" * 60)
        print("CITATION EXTRACTION SUMMARY")
        print("=" * 60)
        print(f"HTML files scanned:       {stats['total']}")
        print(f"With citations extracted:  {stats['with_citations']}")
        print(f"Without citations:         {stats['without_citations']}")
        print(f"No HTML file:              {stats['no_html']}")
        print(f"Total citations:           {stats['total_citations']}")
        print(f"Total filtered items:      {stats['total_filtered']}")
        if args.dry_run:
            print("\nDRY RUN — no toc.json files were modified.")
        else:
            print("\nUpdated toc.json files with 'citations' and 'endmatter' fields.")
        print("=" * 60)

    if args.sheet:
        if args.dry_run:
            print("\nSkipping sheet export in dry-run mode.")
        else:
            print("\nLoading citations from toc.json → Google Sheet...")
            rows = load_citations_for_sheet(volume_filter=args.volume)
            print(f"{len(rows)} citation rows loaded from toc.json")
            sheet_url = export_to_sheet(rows)
            print(f"\nSheet: {sheet_url}")


if __name__ == '__main__':
    main()
