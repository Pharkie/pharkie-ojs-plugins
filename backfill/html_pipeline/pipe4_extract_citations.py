#!/usr/bin/env python3
"""Extract citations from JATS article body into JATS back matter.

Reads JATS <body>, finds reference sections (References, Bibliography,
Notes, etc.), extracts items, classifies them, and writes to JATS <back>:
  - references → <ref-list><ref><mixed-citation>
  - notes → <fn-group><fn>
  - author bios → <bio>
  - provenance → <notes notes-type="provenance">

Also removes the reference sections from <body> so they don't appear
in HTML galleys (generated from JATS body by jats_to_html.py).

Usage:
    python3 backfill/extract_citations.py --extract --dry-run     # preview
    python3 backfill/extract_citations.py --extract               # write to JATS
    python3 backfill/extract_citations.py --extract --volume 37.1 # single volume
    python3 backfill/extract_citations.py --sheet                 # JATS → Google Sheet
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.citations import (
    find_jats_reference_sections, is_non_reference, is_citation_like, is_author_bio,
    is_author_contact, is_provenance, is_reference, is_note, classify, extract_text_from_element,
    sort_notes_by_number, strip_note_number,
    NOTES_HEADING_RE, PURE_REFERENCE_HEADING_RE,
)

OUTPUT_DIR = Path(__file__).parent.parent / "private" / "output"


def _vol_sort_key(path):
    """Sort key for toc.json paths by volume.issue number."""
    name = path.parent.name
    parts = name.split('.')
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 1)
    except ValueError:
        return (999, 999)


# ---------------------------------------------------------------
# JATS extraction
# ---------------------------------------------------------------

def extract_from_jats(jats_path: Path) -> dict:
    """Extract citations from a JATS file's <body> reference sections.

    Returns dict with 'citations' (list of text strings), 'bios', 'provenance',
    'notes' (filtered non-citation items), 'headings', and 'sections_to_remove'
    (list of Element references to remove from body).
    """
    tree = ET.parse(jats_path)
    root = tree.getroot()
    body = root.find('.//{*}body')
    if body is None:
        return {'citations': [], 'bios': [], 'provenance': [], 'notes': [],
                'headings': [], 'sections_to_remove': [], 'tree': tree}

    # Extract author names from <front> for bio section detection
    author_names = []
    for contrib in root.findall('.//{*}contrib'):
        if contrib.get('contrib-type') != 'author':
            continue
        given = contrib.find('{*}name/{*}given-names')
        family = contrib.find('{*}name/{*}surname')
        if given is not None and family is not None and given.text and family.text:
            author_names.append(f'{given.text} {family.text}')
        elif family is not None and family.text:
            author_names.append(family.text)

    # Scan leading <p> elements (before first <sec>) for provenance notes.
    # Conference/presentation notes appear at the top of the article body,
    # not in reference sections at the tail.
    leading_provenance = []
    leading_provenance_elements = []
    for child in body:
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if tag == 'sec':
            break  # Reached first section — stop scanning
        if tag == 'p':
            text = extract_text_from_element(child).strip()
            if text and is_provenance(text):
                leading_provenance.append(text)
                leading_provenance_elements.append(child)

    sections = find_jats_reference_sections(body, tail_only=True,
                                            author_names=author_names)

    citations = []
    bios = []
    provenance_items = list(leading_provenance)
    note_items = []
    headings = []

    # Build normalised author name variants for matching
    author_surnames = [n.split()[-1].lower() for n in author_names if n]
    author_full = [n.lower() for n in author_names if n]

    # When both a Notes section and a separate References section exist,
    # trust the author's separation: ALL items under Notes stay as notes.
    # The is_citation_like filter only applies when there is no separate
    # References section (notes and references may be mixed under one heading).
    has_separate_references = any(
        PURE_REFERENCE_HEADING_RE.match(sec['heading'])
        for sec in sections
    )

    for sec in sections:
        heading = sec['heading']
        headings.append(heading)
        is_notes_section = bool(NOTES_HEADING_RE.match(heading))

        for item in sec['items']:
            if is_non_reference(item):
                # Classify filtered items — provenance, note, or drop.
                # Bio detection is handled by the trailing scan (with
                # author matching) so we don't check is_author_bio here.
                if is_provenance(item):
                    provenance_items.append(item)
                elif is_note(item) == 'name-only' and item.strip().rstrip('.').lower() in author_full:
                    pass  # Author sign-off — drop, not a note
                else:
                    note_items.append(item)
                continue

            if is_notes_section:
                if has_separate_references or not is_citation_like(item):
                    note_items.append(item)
                    continue

            citations.append(item)

    # Scan for inline notes: bare <p> elements with "Notes:" label (in <bold>
    # or plain text) followed by numbered items like (1), (2), etc.
    # These appear when the author didn't use a <sec><title>Notes</title> heading.
    inline_notes_elements = []
    _INLINE_NOTES_RE = re.compile(r'^Notes?\s*:', re.IGNORECASE)
    _NUMBERED_ITEM_RE = re.compile(r'^\(\d+\)')
    for child in list(body):
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if tag != 'p':
            continue
        text = extract_text_from_element(child).strip()
        if not text:
            continue
        if _INLINE_NOTES_RE.match(text):
            # First inline note — strip "Notes:" prefix and extract note text
            note_text = _INLINE_NOTES_RE.sub('', text).strip()
            # The text after "Notes:" may start with (1) — strip the number
            if note_text:
                note_items.append(note_text)
                inline_notes_elements.append(child)
        elif _NUMBERED_ITEM_RE.match(text) and inline_notes_elements:
            # Continuation numbered item after the Notes: paragraph
            note_items.append(text)
            inline_notes_elements.append(child)

    # Scan trailing <p> elements for bios and contacts that aren't inside
    # a headed bio section. These are bare paragraphs like
    # "Del Loewenthal is Emeritus Professor..." at the end of the body,
    # either as direct children of <body> or inside the last <sec>.
    trailing_bio_elements = []
    trailing_bio_parts = []

    # Scan all <p> elements for bios about the article's OWN authors.
    # Only accept a bio if it starts with one of the author names from
    # JATS front matter. This eliminates false positives from body text
    # discussing other people (e.g. "Emmy van Deurzen is a philosopher").
    # Skip paragraphs already classified as citations by the section scan.
    # Don't skip junk items — they may be author bios that the trailing
    # scan (with author matching) should pick up.
    already_extracted = set(citations)

    def _starts_with_author(text):
        """Check if text starts with one of the article's own authors."""
        text_lower = text.lower()
        # Match "Dr Martin Milton is..." or "Martin Milton is..."
        for name in author_full:
            # Try with optional title prefix (Dr, Prof, Professor)
            if text_lower.startswith(name):
                return True
            # Try with Dr/Prof prefix
            for prefix in ('dr ', 'prof ', 'professor '):
                if text_lower.startswith(prefix + name):
                    return True
        # Match by surname for ALL CAPS bios ("CHARLES SCOTT is...")
        for surname in author_surnames:
            words = text.split()
            if len(words) >= 2 and words[-1].lower() == surname:
                return False  # Would need more context
            # Check if surname appears in first few words
            first_words = ' '.join(words[:5]).lower()
            if surname in first_words and len(surname) > 2:
                return True
        return False

    all_ps = list(body.iter())
    all_ps = [el for el in all_ps
              if (el.tag.split('}')[-1] if '}' in el.tag else el.tag) == 'p']
    # Group: when we find a bio <p>, consume following contact <p> elements
    # as part of the same bio. A standalone contact without a preceding bio
    # is still collected (it belongs to a bio extracted by the section scan).
    current_bio_parts = []
    current_bio_elements = []
    in_bio = False
    for p_el in all_ps:
        text = extract_text_from_element(p_el).strip()
        if not text:
            continue
        if text in already_extracted:
            continue
        # Reject reference-format text: "Surname, I. (Year)..." or "Surname, I. Title..."
        _looks_like_ref = bool(re.match(
            r'^[A-Z][a-z]+,\s+[A-Z]\.?\s', text
        ))
        # Require a bio verb — "Name is/was/has [role]". Without it,
        # "Dr. Name, Department, University..." is an address, not a bio.
        _has_bio_verb = bool(re.search(r'\b(is|was|has)\s', text[:200]))
        is_bio_text = (is_author_bio(text) and not is_author_contact(text)
                       and _starts_with_author(text)
                       and not _looks_like_ref
                       and _has_bio_verb)
        is_contact_text = is_author_contact(text)
        # Author statements (funding, COI, ethics) → notes
        _note_reason = is_note(text)
        if _note_reason == 'author-statement':
            if current_bio_parts:
                bios.append(' '.join(current_bio_parts))
                trailing_bio_elements.extend(current_bio_elements)
                current_bio_parts = []
                current_bio_elements = []
            in_bio = False
            note_items.append(text)
            trailing_bio_elements.append(p_el)  # remove from body
            continue
        if is_bio_text:
            # Only accept one bio per author — find which author this
            # bio belongs to, skip if we already have one for them
            bio_author = None
            for name in author_full:
                if text.lower().startswith(name) or any(
                    text.lower().startswith(p + name) for p in ('dr ', 'prof ', 'professor ')
                ):
                    bio_author = name
                    break
            if not bio_author:
                for surname in author_surnames:
                    if surname in ' '.join(text.split()[:5]).lower():
                        bio_author = surname
                        break
            if bio_author:
                # If we already have a bio for this author, REPLACE it
                # with the new one (bios at the end are more likely to be
                # the real bio than body text earlier that mentions the author).
                # Match by first name + surname, ignoring middle names/initials
                # ("Ian R. Owen" vs "Ian Owen").
                author_parts = bio_author.split()
                match_first = author_parts[0] if author_parts else ''
                match_last = author_parts[-1] if len(author_parts) > 1 else ''
                existing_idx = None
                for bi, b in enumerate(bios):
                    b_lower = b.lower()
                    if match_first and match_last and match_first in b_lower and match_last in b_lower:
                        existing_idx = bi
                        break
                if existing_idx is not None:
                    bios.pop(existing_idx)
            # Start a new bio group (flush previous if any)
            if current_bio_parts:
                bios.append(' '.join(current_bio_parts))
                trailing_bio_elements.extend(current_bio_elements)
            current_bio_parts = [text]
            current_bio_elements = [p_el]
            in_bio = True
        elif is_contact_text and in_bio:
            # Append contact to current bio
            current_bio_parts.append(text)
            current_bio_elements.append(p_el)
        elif is_contact_text and not in_bio:
            # Contact without a preceding bio in this scan — append to
            # the last bio from any source (section scan or earlier).
            # Stay in "appending to last bio" mode for consecutive contacts.
            if bios:
                bios[-1] = bios[-1] + ' ' + text
                trailing_bio_elements.append(p_el)
                in_bio = False  # don't start a new group, just keep appending
            else:
                current_bio_parts = [text]
                current_bio_elements = [p_el]
                in_bio = True
        else:
            # Non-bio content — flush and stop grouping
            if current_bio_parts:
                bios.append(' '.join(current_bio_parts))
                trailing_bio_elements.extend(current_bio_elements)
                current_bio_parts = []
                current_bio_elements = []
            in_bio = False
    # Flush final group
    if current_bio_parts:
        bios.append(' '.join(current_bio_parts))
        trailing_bio_elements.extend(current_bio_elements)

    # Deduplicate: remove any note_items that were also identified as bios
    # by the trailing scan (prevents the same text appearing as both bio
    # and note in JATS).
    if bios:
        bio_texts = set(bios)
        note_items = [n for n in note_items if n not in bio_texts]

    return {
        'citations': citations,
        'bios': bios,
        'provenance': provenance_items,
        'notes': note_items,
        'headings': headings,
        'sections_to_remove': [sec['element'] for sec in sections],
        'trailing_bio_elements': trailing_bio_elements,
        'leading_provenance_elements': leading_provenance_elements,
        'inline_notes_elements': inline_notes_elements,
        'tree': tree,
    }


def write_back_matter_to_jats(jats_path: Path, extracted: dict,
                               dry_run: bool = False) -> None:
    """Write extracted citations to JATS <back> and remove ref sections from <body>."""
    if dry_run:
        return

    tree = extracted['tree']
    root = tree.getroot()
    body = root.find('.//{*}body')

    # Remove reference sections from body
    for sec_el in extracted['sections_to_remove']:
        if body is not None:
            try:
                body.remove(sec_el)
            except ValueError:
                pass  # already removed

    # Remove leading provenance elements from body
    for el in extracted.get('leading_provenance_elements', []):
        if body is not None:
            try:
                body.remove(el)
            except ValueError:
                pass

    # Remove inline notes elements from body
    for el in extracted.get('inline_notes_elements', []):
        if body is not None:
            try:
                body.remove(el)
            except ValueError:
                pass

    # Remove trailing bio elements (may be in body or inside a <sec>)
    for el in extracted.get('trailing_bio_elements', []):
        # Try removing from body first, then search sections
        removed = False
        if body is not None:
            try:
                body.remove(el)
                removed = True
            except ValueError:
                pass
        if not removed and body is not None:
            for sec in body:
                try:
                    sec.remove(el)
                    break
                except (ValueError, TypeError):
                    pass

    # Find or create <back>
    back = root.find('.//{*}back')
    if back is None:
        back = ET.SubElement(root, 'back')

    # Preserve existing back-matter items not found in this extraction pass
    # (e.g. refs already extracted in a prior run, now body has them removed)
    existing_refs = []
    existing_notes = []
    existing_bios = []
    existing_prov = []
    for child in list(back):
        ltag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if ltag == 'ref-list':
            for ref in child:
                mc = ref.find('{*}mixed-citation') if '}' in ref.tag else ref.find('mixed-citation')
                if mc is not None and mc.text:
                    existing_refs.append(mc.text.strip())
        elif ltag == 'fn-group':
            for fn in child:
                p = fn.find('{*}p') if '}' in fn.tag else fn.find('p')
                if p is not None and p.text:
                    existing_notes.append(p.text.strip())
        elif ltag == 'bio':
            p = child.find('{*}p') if '}' in child.tag else child.find('p')
            if p is not None and p.text:
                existing_bios.append(p.text.strip())
        elif ltag == 'notes' and child.get('notes-type') == 'provenance':
            p = child.find('{*}p') if '}' in child.tag else child.find('p')
            if p is not None and p.text:
                existing_prov.append(p.text.strip())

    # Clear existing back matter (we're rewriting it with merged content)
    for child in list(back):
        back.remove(child)

    # Merge: new extractions take priority, fall back to existing
    citations = extracted['citations'] if extracted['citations'] else existing_refs
    if citations:
        ref_list = ET.SubElement(back, 'ref-list')
        for i, ref_text in enumerate(citations, 1):
            ref = ET.SubElement(ref_list, 'ref', id=f'ref{i}')
            mc = ET.SubElement(ref, 'mixed-citation')
            mc.text = ref_text

    # Write notes (sorted by leading number)
    notes = extracted['notes'] if extracted['notes'] else existing_notes
    if notes:
        notes = sort_notes_by_number(notes)
        fn_group = ET.SubElement(back, 'fn-group')
        for i, note_text in enumerate(notes, 1):
            fn = ET.SubElement(fn_group, 'fn', id=f'fn{i}')
            p = ET.SubElement(fn, 'p')
            p.text = strip_note_number(note_text)

    # Write author bios
    bios = extracted['bios'] if extracted['bios'] else existing_bios
    for bio_text in bios:
        bio = ET.SubElement(back, 'bio')
        p = ET.SubElement(bio, 'p')
        p.text = bio_text

    # Write provenance
    prov = extracted['provenance'] if extracted['provenance'] else existing_prov
    if prov:
        prov_text = prov[0]
        if len(prov) > 1:
            prov_text = ' '.join(prov)
        notes_el = ET.SubElement(back, 'notes')
        notes_el.set('notes-type', 'provenance')
        p = ET.SubElement(notes_el, 'p')
        p.text = prov_text

    # Remove empty <back>
    if len(back) == 0:
        root.remove(back)

    # Write with proper formatting
    ET.indent(tree, space='')
    tree.write(jats_path, encoding='unicode', xml_declaration=True)
    # Add newline at end
    with open(jats_path, 'a') as f:
        f.write('\n')


# ---------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------

def process_all(volume_filter=None, dry_run=False, verbose=False):
    """Extract citations from JATS body sections into JATS back matter."""
    if volume_filter:
        toc_files = [OUTPUT_DIR / volume_filter / 'toc.json']
        if not toc_files[0].exists():
            print(f"ERROR: {toc_files[0]} not found")
            sys.exit(1)
    else:
        toc_files = sorted(OUTPUT_DIR.glob('*/toc.json'))

    toc_files = sorted(toc_files, key=_vol_sort_key)
    stats = Counter()

    for toc_path in toc_files:
        vol_dir = toc_path.parent
        vol_name = vol_dir.name

        with open(toc_path) as f:
            toc = json.load(f)

        for article in toc.get('articles', []):
            split_pdf = article.get('split_pdf', '')
            slug = Path(split_pdf).stem if split_pdf else ''
            if not slug:
                continue

            jats_path = vol_dir / f"{slug}.jats.xml"
            if not jats_path.exists():
                stats['no_jats'] += 1
                continue

            stats['total'] += 1

            try:
                extracted = extract_from_jats(jats_path)
            except ET.ParseError as e:
                stats['parse_error'] += 1
                if verbose:
                    print(f"  ERROR {vol_name}/{slug}: {e}")
                continue

            total_items = (len(extracted['citations']) + len(extracted['notes']) +
                          len(extracted['bios']) + len(extracted['provenance']))

            if total_items > 0:
                stats['with_citations'] += 1
                stats['total_citations'] += len(extracted['citations'])
                stats['total_notes'] += len(extracted['notes'])
                stats['total_bios'] += len(extracted['bios'])
                stats['total_provenance'] += len(extracted['provenance'])

                write_back_matter_to_jats(jats_path, extracted, dry_run=dry_run)

                if verbose:
                    parts = [f"{len(extracted['citations'])} citations",
                             f"{len(extracted['notes'])} notes",
                             f"{len(extracted['bios'])} bios",
                             f"{len(extracted['provenance'])} provenance"]
                    print(f"  ✓ {vol_name}/{slug}: {', '.join(parts)}")
            else:
                stats['without_citations'] += 1
                if verbose:
                    print(f"  ✗ {vol_name}/{slug}: no citations in body")

    return stats


# ---------------------------------------------------------------
# Sheet export (reads from JATS files)
# ---------------------------------------------------------------

SHEET_HEADERS = [
    'Volume', 'Issue', 'Date', 'Section', 'Article Title', 'Authors',
    'Seq', 'Heading', 'Class', 'Text', 'Matched DOI', 'Crossref Citation',
]


def load_citations_for_sheet(volume_filter=None):
    """Load citations from JATS XML files (source of truth) for sheet export."""
    if volume_filter:
        toc_files = [OUTPUT_DIR / volume_filter / 'toc.json']
        if not toc_files[0].exists():
            print(f"ERROR: {toc_files[0]} not found")
            sys.exit(1)
    else:
        toc_files = sorted(OUTPUT_DIR.glob('*/toc.json'))

    toc_files = sorted(toc_files, key=_vol_sort_key)
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
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, 'References', 'reference',
                        text, '', '',
                    ])

            for fn in tree.findall('.//{*}fn'):
                p = fn.find('{*}p')
                if p is not None and p.text and p.text.strip():
                    seq += 1
                    text = p.text.strip()
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, 'Notes', 'note',
                        text, '', '',
                    ])

            for bio in tree.findall('.//{*}bio'):
                p = bio.find('{*}p')
                if p is not None and p.text and p.text.strip():
                    seq += 1
                    text = p.text.strip()
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, '', 'author_bio',
                        text, '', '',
                    ])

            prov = tree.find('.//{*}notes[@notes-type="provenance"]')
            if prov is not None:
                p = prov.find('{*}p')
                if p is not None and p.text and p.text.strip():
                    seq += 1
                    text = p.text.strip()
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, '', 'provenance',
                        text, '', '',
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

    TAB_NAME = 'Citations'
    try:
        ws = sh.worksheet(TAB_NAME)
        ws.clear()
        needed_rows = len(rows) + 1
        ws.resize(rows=needed_rows, cols=len(SHEET_HEADERS))
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB_NAME, rows=len(rows) + 1,
                              cols=len(SHEET_HEADERS))

    all_data = [SHEET_HEADERS] + rows
    print(f"Publishing {len(rows)} citation rows to '{TAB_NAME}' tab...")

    BATCH_SIZE = 5000
    for i in range(0, len(all_data), BATCH_SIZE):
        batch = all_data[i:i + BATCH_SIZE]
        cell = f'A{i + 1}'
        ws.update(range_name=cell, values=batch)
        print(f"  Wrote rows {i + 1}–{i + len(batch)}")

    col_letter = chr(ord('A') + len(SHEET_HEADERS) - 1)
    ws.format(f'A1:{col_letter}1', {'textFormat': {'bold': True}})
    ws.freeze(rows=1)

    num_rows = len(rows) + 1
    num_cols = len(SHEET_HEADERS)
    ws.set_basic_filter(f'A1:{col_letter}{num_rows}')

    class_col_index = SHEET_HEADERS.index('Class')
    body = {
        'requests': [{
            'sortRange': {
                'range': {
                    'sheetId': ws.id,
                    'startRowIndex': 1,
                    'endRowIndex': num_rows,
                    'startColumnIndex': 0,
                    'endColumnIndex': num_cols,
                },
                'sortSpecs': [{
                    'dimensionIndex': class_col_index,
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
        description='Extract citations from JATS body and/or publish to Google Sheet',
        epilog='Examples:\n'
               '  python3 backfill/extract_citations.py --extract          # JATS body → JATS back\n'
               '  python3 backfill/extract_citations.py --extract --dry-run  # preview only\n'
               '  python3 backfill/extract_citations.py --sheet            # JATS → Google Sheet\n'
               '  python3 backfill/extract_citations.py --extract --sheet  # both\n',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--extract', action='store_true',
                        help='Extract citations from JATS body into JATS back matter')
    parser.add_argument('--sheet', action='store_true',
                        help='Publish citations from JATS to Google Sheet')
    parser.add_argument('--volume', help='Process a single volume (e.g. 37.1)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview extraction without writing')
    parser.add_argument('--verbose', action='store_true',
                        help='Print details for each file')
    args = parser.parse_args()

    if not args.extract and not args.sheet:
        parser.error('Specify --extract, --sheet, or both')

    if args.extract:
        print("Extracting citations from JATS body → JATS back matter...")
        if args.dry_run:
            print("(DRY RUN — no files will be modified)\n")

        stats = process_all(
            volume_filter=args.volume,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

        print("\n" + "=" * 60)
        print("CITATION EXTRACTION SUMMARY")
        print("=" * 60)
        print(f"JATS files scanned:       {stats['total']}")
        print(f"With citations extracted:  {stats['with_citations']}")
        print(f"Without citations:         {stats['without_citations']}")
        print(f"No JATS file:              {stats['no_jats']}")
        print(f"Parse errors:              {stats['parse_error']}")
        print(f"Total citations:           {stats['total_citations']}")
        print(f"Total notes:               {stats['total_notes']}")
        print(f"Total bios:                {stats['total_bios']}")
        print(f"Total provenance:          {stats['total_provenance']}")
        if args.dry_run:
            print("\nDRY RUN — no JATS files were modified.")
        else:
            print("\nUpdated JATS files: refs → <ref-list>, notes → <fn-group>.")
        print("=" * 60)

    if args.sheet:
        if args.dry_run:
            print("\nSkipping sheet export in dry-run mode.")
        else:
            print("\nLoading citations from JATS → Google Sheet...")
            rows = load_citations_for_sheet(volume_filter=args.volume)
            print(f"{len(rows)} citation rows loaded from JATS")
            sheet_url = export_to_sheet(rows)
            print(f"\nSheet: {sheet_url}")


if __name__ == '__main__':
    main()
