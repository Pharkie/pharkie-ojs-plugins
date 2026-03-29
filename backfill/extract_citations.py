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

sys.path.insert(0, os.path.dirname(__file__))
from lib.citations import (
    find_jats_reference_sections, is_junk, is_citation_like, is_author_bio,
    is_provenance, classify, citation_confidence, note_confidence,
    bio_confidence, provenance_confidence, extract_text_from_element,
    NOTES_HEADING_RE, PURE_REFERENCE_HEADING_RE,
)

OUTPUT_DIR = Path(__file__).parent / "private" / "output"


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
        if given is not None and family is not None:
            author_names.append(f'{given.text} {family.text}')
        elif family is not None:
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

    for sec in sections:
        heading = sec['heading']
        headings.append(heading)
        is_notes_section = bool(NOTES_HEADING_RE.match(heading))

        # Collect bio items from this section to merge into one per author
        sec_bio_parts = []

        for item in sec['items']:
            if is_junk(item):
                # Classify filtered items (provenance before bio — more specific)
                if is_provenance(item):
                    provenance_items.append(item)
                elif is_author_bio(item):
                    sec_bio_parts.append(item)
                else:
                    note_items.append(item)
                continue

            if is_notes_section and not is_citation_like(item):
                note_items.append(item)
                continue

            citations.append(item)

        # Merge consecutive bio items from the same section into one bio
        if sec_bio_parts:
            bios.append(' '.join(sec_bio_parts))

    return {
        'citations': citations,
        'bios': bios,
        'provenance': provenance_items,
        'notes': note_items,
        'headings': headings,
        'sections_to_remove': [sec['element'] for sec in sections],
        'leading_provenance_elements': leading_provenance_elements,
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
        notes = _sort_notes_by_number(notes)
        fn_group = ET.SubElement(back, 'fn-group')
        for i, note_text in enumerate(notes, 1):
            fn = ET.SubElement(fn_group, 'fn', id=f'fn{i}')
            p = ET.SubElement(fn, 'p')
            p.text = note_text

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


def _sort_notes_by_number(notes: list[str]) -> list[str]:
    """Sort notes by their leading number."""
    def sort_key(note):
        m = re.match(r'^(\d+)[\.\)\s]', note)
        return (0, int(m.group(1))) if m else (1, 0)
    return sorted(notes, key=sort_key)


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

    def vol_sort_key(path):
        name = path.parent.name
        parts = name.split('.')
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 1)
        except ValueError:
            return (999, 999)

    toc_files = sorted(toc_files, key=vol_sort_key)
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
    'Seq', 'Heading', 'Class', 'Confidence', 'Text', 'Matched DOI', 'Crossref Citation',
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
                    confidence = note_confidence(text)
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, 'Notes', 'note', confidence,
                        text, '', '',
                    ])

            for bio in tree.findall('.//{*}bio'):
                p = bio.find('{*}p')
                if p is not None and p.text and p.text.strip():
                    seq += 1
                    text = p.text.strip()
                    confidence = bio_confidence(text)
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, '', 'author_bio', confidence,
                        text, '', '',
                    ])

            prov = tree.find('.//{*}notes[@notes-type="provenance"]')
            if prov is not None:
                p = prov.find('{*}p')
                if p is not None and p.text and p.text.strip():
                    seq += 1
                    text = p.text.strip()
                    confidence = provenance_confidence(text)
                    rows.append([
                        volume, issue, date, section, title, authors,
                        seq, '', 'provenance', confidence,
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

    confidence_col_index = SHEET_HEADERS.index('Confidence')
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
