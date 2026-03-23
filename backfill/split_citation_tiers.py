#!/usr/bin/env python3
"""Split JATS ref-list items into references and notes.

Reads all <mixed-citation> from JATS <ref-list>, classifies each as
reference or note, then rewrites the JATS file: references stay in
<ref-list>, notes move to <fn-group>.

Usage:
    python3 backfill/split_citation_tiers.py --dry-run   # preview
    python3 backfill/split_citation_tiers.py              # write to JATS
"""

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
from lib.citations import is_note, is_reference, classify

OUTPUT_DIR = Path(__file__).parent / "output"


def split_jats_citations(jats_path: Path, dry_run: bool = False,
                          verbose: bool = False) -> dict:
    """Split ref-list items in a JATS file into references and notes.

    Returns dict with counts for stats aggregation.
    """
    tree = ET.parse(jats_path)
    root = tree.getroot()
    back = root.find('.//{*}back')
    if back is None:
        return {}

    ref_list = back.find('{*}ref-list') if '{' in back.tag else back.find('ref-list')
    if ref_list is None:
        # Try without namespace
        for child in back:
            if child.tag.endswith('ref-list') or child.tag == 'ref-list':
                ref_list = child
                break
    if ref_list is None:
        return {}

    # Collect all mixed-citation texts
    refs_to_keep = []
    notes_to_move = []
    note_reasons = Counter()

    for ref_el in list(ref_list):
        tag = ref_el.tag.split('}')[-1] if '}' in ref_el.tag else ref_el.tag
        if tag != 'ref':
            continue
        mc = None
        for child in ref_el:
            ctag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if ctag == 'mixed-citation':
                mc = child
                break
        if mc is None or not mc.text:
            continue

        text = mc.text.strip()
        note_reason = is_note(text)
        if note_reason:
            notes_to_move.append(text)
            note_reasons[note_reason] += 1
        elif is_reference(text):
            refs_to_keep.append(text)
        else:
            notes_to_move.append(text)
            note_reasons['default'] += 1

    if not notes_to_move and not refs_to_keep:
        return {}

    result = {
        'references': len(refs_to_keep),
        'notes_by_rule': sum(v for k, v in note_reasons.items() if k != 'default'),
        'notes_default': note_reasons.get('default', 0),
        'note_reasons': dict(note_reasons),
    }

    if dry_run:
        return result

    # Rewrite the JATS file
    # Clear existing ref-list
    for child in list(ref_list):
        ref_list.remove(child)

    # Write references back to ref-list
    for i, text in enumerate(refs_to_keep, 1):
        ref = ET.SubElement(ref_list, 'ref', id=f'ref{i}')
        mc = ET.SubElement(ref, 'mixed-citation')
        mc.text = text

    # Remove empty ref-list
    if len(ref_list) == 0:
        back.remove(ref_list)

    # Find or create fn-group for notes
    fn_group = None
    for child in back:
        ctag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if ctag == 'fn-group':
            fn_group = child
            break

    if notes_to_move:
        # Get existing notes from fn-group
        existing_notes = []
        if fn_group is not None:
            for fn in list(fn_group):
                p = None
                for child in fn:
                    ctag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                    if ctag == 'p':
                        p = child
                        break
                if p is not None and p.text:
                    existing_notes.append(p.text.strip())
            back.remove(fn_group)

        # Merge and sort: existing notes + newly moved notes
        all_notes = existing_notes + notes_to_move

        # Sort by leading number
        def sort_key(note):
            m = re.match(r'^(\d+)[\.\)\s]', note)
            return (0, int(m.group(1))) if m else (1, 0)
        all_notes = sorted(all_notes, key=sort_key)

        # Insert fn-group before bio/notes elements
        insert_idx = 0
        for i, child in enumerate(back):
            ctag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if ctag in ('bio', 'notes'):
                insert_idx = i
                break
            insert_idx = i + 1

        fn_group = ET.Element('fn-group')
        back.insert(insert_idx, fn_group)

        for i, text in enumerate(all_notes, 1):
            fn = ET.SubElement(fn_group, 'fn', id=f'fn{i}')
            p = ET.SubElement(fn, 'p')
            p.text = text

    ET.indent(tree, space='')
    tree.write(jats_path, encoding='unicode', xml_declaration=True)
    with open(jats_path, 'a') as f:
        f.write('\n')

    return result


def main():
    dry_run = '--dry-run' in sys.argv
    verbose = '--verbose' in sys.argv

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
    all_note_reasons = Counter()
    borderline = []

    for toc_path in toc_files:
        vol_dir = toc_path.parent

        with open(toc_path) as f:
            toc = json.load(f)

        vol = toc.get('volume', 0)
        iss = toc.get('issue', 0)

        for art in toc.get('articles', []):
            split_pdf = art.get('split_pdf', '')
            slug = Path(split_pdf).stem if split_pdf else ''
            if not slug:
                continue

            jats_path = vol_dir / f'{slug}.jats.xml'
            if not jats_path.exists():
                continue

            try:
                result = split_jats_citations(jats_path, dry_run=dry_run, verbose=verbose)
            except ET.ParseError:
                stats['parse_error'] += 1
                continue

            if not result:
                continue

            stats['references'] += result['references']
            stats['notes_by_rule'] += result['notes_by_rule']
            stats['notes_default'] += result['notes_default']
            for reason, count in result.get('note_reasons', {}).items():
                all_note_reasons[reason] += count

            if verbose:
                title = art.get('title', '')[:40]
                print(f"  Vol {vol}.{iss} | {title} | "
                      f"{result['references']} refs, "
                      f"{result['notes_by_rule'] + result['notes_default']} notes")

    total = stats['references'] + stats['notes_by_rule'] + stats['notes_default']
    print("=" * 60)
    print("CITATION TIER SPLIT" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)
    print(f"Total items:          {total}")
    print(f"→ References:         {stats['references']}")
    print(f"→ Notes (by rule):    {stats['notes_by_rule']}")
    print(f"→ Notes (by default): {stats['notes_default']}")
    print()
    if all_note_reasons:
        print("Note reasons:")
        for reason, count in all_note_reasons.most_common():
            print(f"  {reason:25s} {count:5d}")

    if dry_run:
        print(f"\nDRY RUN — no files modified.")
    else:
        print(f"\nDone — split refs/notes in JATS files.")


if __name__ == '__main__':
    main()
