#!/usr/bin/env python3
"""Split toc.json endmatter[] into author_bios[], provenance, and notes[].

The endmatter field is a catch-all for non-citation items extracted from
reference sections: author bios, provenance notes, prose endnotes, ibid-style
refs, short fragments. This script classifies each item and moves it to the
proper field:

  - author_bios[]: biographical notes about article authors
  - provenance: "This paper was first delivered at..." (string, not array)
  - notes[]: everything else merges back into the existing notes array

Usage:
    python3 backfill/split_endmatter.py --dry-run   # preview
    python3 backfill/split_endmatter.py              # write to toc.json
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"


def _is_author_bio(text: str) -> bool:
    """Detect author biographical notes. Reused from split_citation_tiers.py."""
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
        r'^[A-Z][A-Z\s\.\-]+\b(is|was|has)\s',
        r'^[A-Z]+\s+(?:van|de|von)\s+[A-Z\-]+\s+(is|was|has)\s',
        r'^[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]+(\s+[A-Z][a-zà-ü]+)?\s+(is|was|has)\s',
        r'^All (three|four|five|six) authors',
        r'^(Dr\.?|Professor)\s+[A-Z][a-z]',
        r'^[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]+\s+(PhD|MA|MSc|UKCP|BPS)',
    ]

    if any(re.match(p, text) for p in bio_patterns):
        return True

    if has_bio_phrase and re.match(r'^[A-Z][a-zà-ü]+\s', text) and len(text) > 50:
        if not re.search(r'\(\d{4}\)', text[:80]):
            return True

    return False


def _is_provenance(text: str) -> bool:
    """Detect article provenance notes."""
    return bool(re.match(
        r'^This (article|paper|chapter|essay|lecture|talk)\s+(is|was)\s', text
    ))


def classify_endmatter_item(text: str) -> str:
    """Classify an endmatter item. Returns 'bio', 'provenance', or 'note'."""
    if _is_author_bio(text):
        return 'bio'
    if _is_provenance(text):
        return 'provenance'
    return 'note'


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Split endmatter[] into author_bios[], provenance, and notes[]')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without writing')
    args = parser.parse_args()

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
        with open(toc_path) as f:
            toc = json.load(f)

        modified = False

        for article in toc.get('articles', []):
            endmatter = article.get('endmatter', [])
            if not endmatter:
                continue

            bios = []
            provenance_items = []
            note_items = []

            for item in endmatter:
                cat = classify_endmatter_item(item)
                if cat == 'bio':
                    bios.append(item)
                    stats['bio'] += 1
                elif cat == 'provenance':
                    provenance_items.append(item)
                    stats['provenance'] += 1
                else:
                    note_items.append(item)
                    stats['note'] += 1

            if not args.dry_run:
                # Author bios
                if bios:
                    article['author_bios'] = bios
                # Provenance (string — usually 0-1 per article)
                if provenance_items:
                    article['provenance'] = provenance_items[0]
                    if len(provenance_items) > 1:
                        # Rare: multiple provenance notes, join them
                        article['provenance'] = ' '.join(provenance_items)
                # Merge remaining items into notes
                existing_notes = article.get('notes', [])
                if note_items:
                    article['notes'] = existing_notes + note_items
                # Remove endmatter
                del article['endmatter']
                modified = True

        if modified and not args.dry_run:
            with open(toc_path, 'w') as f:
                json.dump(toc, f, indent=2, ensure_ascii=False)
                f.write('\n')

    print(f"{'=' * 50}")
    print(f"SPLIT ENDMATTER {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'=' * 50}")
    print(f"  Author bios:    {stats['bio']}")
    print(f"  Provenance:     {stats['provenance']}")
    print(f"  → notes:        {stats['note']}")
    print(f"  Total:          {sum(stats.values())}")
    if args.dry_run:
        print(f"\nDRY RUN — no files modified.")
    else:
        print(f"\nDone — wrote author_bios[], provenance, merged rest into notes[].")


if __name__ == '__main__':
    main()
