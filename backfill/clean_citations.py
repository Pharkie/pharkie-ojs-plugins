#!/usr/bin/env python3
"""Clean citations in toc.json by removing non-citation items.

Operates directly on toc.json (source of truth). Run --dry-run first to preview.

Rules applied:
1. Remove "See X" / "cf." cross-references
2. Remove Ibid / Ibidem / Op cit shorthand
3. Remove numbered commentary endnotes (no citation markers)
4. Remove "Yours sincerely" and similar

Usage:
    python3 backfill/clean_citations.py --dry-run
    python3 backfill/clean_citations.py
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "private" / "output"


def should_remove(text: str) -> str | None:
    """Return a reason string if this citation should be removed, else None."""

    # Rule 1: "See X" / "cf." cross-references
    if re.match(r'^(See |see |cf\.\s|Cf\.\s)', text):
        return 'see-crossref'

    # Rule 2: Ibid / Ibidem / Op cit shorthand
    # Only remove if the ENTIRE item is essentially just a back-reference
    stripped = re.sub(r'^\d+[\.\)]\s*', '', text).strip()  # strip leading number
    if re.match(r'^(Ibid\.?|Ibidem|Op\.?\s*cit)', stripped, re.IGNORECASE):
        return 'ibid'
    # Also catch items where ibid/op cit is the main content (short items)
    if len(text) < 80 and re.search(r'\b(Ibid\.?|Ibidem|Op\.?\s*cit)', text, re.IGNORECASE):
        return 'ibid'

    # Rule 3: Numbered commentary endnotes without citation markers
    if re.match(r'^\d+[\.\)]\s', text):
        after_num = re.sub(r'^\d+[\.\)]\s*', '', text).strip()

        # Check for ANY citation signal (broad — we'd rather keep than wrongly remove)
        has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', after_num))
        has_publisher = bool(re.search(
            r'(Press|Publisher|Books|University|Routledge|Sage|Springer|Wiley|Oxford|Cambridge)',
            after_num, re.IGNORECASE))
        has_journal = bool(re.search(
            r'(Journal|Review|Quarterly|Bulletin|Annals|Archives)\s+of\b',
            after_num, re.IGNORECASE))
        has_pages = bool(re.search(r'\bpp?\.?\s*\d+[-–]\d+\b', after_num))
        has_doi = bool(re.search(r'doi[:\s]|10\.\d{4,}/', after_num, re.IGNORECASE))
        has_place = bool(re.search(
            r'(London|New York|Cambridge|Oxford|Paris|Berlin|Chicago|Boston)\s*:', after_num))
        has_author_year = bool(re.search(r'[A-Z][a-z]+.*\d{4}', after_num[:80]))

        citation_signals = sum([has_year, has_publisher, has_journal,
                                has_pages, has_doi, has_place, has_author_year])

        # Only remove if zero citation signals AND clearly prose
        if citation_signals == 0:
            sentence_count = len(re.findall(r'[.!?]\s+[A-Z]', text))
            if sentence_count >= 2 or len(text) > 200:
                return 'numbered-commentary'

    # Rule 4: Short surname+year-only endnote refs (useless for DOI matching)
    # ONLY matches the pattern: [number] Surname year[, year...] [: page].
    # e.g. "Szasz 1988.", "6 Stadlen 2012.", "Condrau 1999: 45-49.", "30 Szasz 2003, 2007, 2010."
    # Must NOT have anything that looks like a title, publisher, or other content.
    stripped_num = re.sub(r'^\d+[\.\)\s]+', '', text).strip()
    # Match: Surname [optional comma/space] year [optional extra years, page refs, brackets]
    # The entire content after the surname must be years/pages/punctuation only
    if re.match(
        r'^[A-Z][a-zà-ü]+(?:\s+and\s+[A-Z][a-zà-ü]+)?'  # Surname [and Surname]
        r'(?:[,\s]+(?:[A-Z][a-zà-ü]+\s*[&]\s*[A-Z][a-zà-ü]+\s+)?'  # optional "Surname & Surname"
        r'\d{4}[a-d]?(?:\s*\[?\d{4}[a-d]?\]?)?'  # year [year]
        r'(?:[,;\s]+(?:or\s+)?\d{4}[a-d]?(?:\s*\[?\d{4}[a-d]?\]?)?)*'  # more years (with optional "or")
        r')+'
        r'(?:[:\s]+(?:pp?\.?\s*)?\d+[-–]?\d*(?:[-–]\d+)?(?:\s*,\s*\d+[-–]?\d*)*)?'  # optional pages
        r'(?:\s*;\s*my translation)?'  # optional note
        r'[.\s]*$',  # trailing punctuation
        stripped_num
    ):
        return 'short-endnote-ref'

    # Rule 5: "Yours sincerely" etc.
    lower = text.strip().rstrip('.').lower()
    if lower in ('yours sincerely', 'yours faithfully', 'kind regards', 'best wishes'):
        return 'greeting'

    return None


def main():
    dry_run = '--dry-run' in sys.argv

    toc_files = sorted(OUTPUT_DIR.glob('*/toc.json'))
    removed_counts = Counter()
    removed_examples = []
    total_before = 0
    total_after = 0

    for toc_path in toc_files:
        with open(toc_path) as f:
            toc = json.load(f)

        vol = toc.get('volume', 0)
        iss = toc.get('issue', 0)
        modified = False

        for art in toc.get('articles', []):
            citations = art.get('citations', [])
            if not citations:
                continue

            total_before += len(citations)
            kept = []

            for c in citations:
                reason = should_remove(c)
                if reason:
                    removed_counts[reason] += 1
                    if len(removed_examples) < 50:
                        removed_examples.append({
                            'vol': f'{vol}.{iss}',
                            'reason': reason,
                            'text': c[:120],
                        })
                else:
                    kept.append(c)

            total_after += len(kept)

            if len(kept) != len(citations):
                modified = True
                if not dry_run:
                    art['citations'] = kept

        if modified and not dry_run:
            with open(toc_path, 'w') as f:
                json.dump(toc, f, indent=2, ensure_ascii=False)
                f.write('\n')

    # Summary
    total_removed = total_before - total_after
    print("=" * 60)
    print("CITATION CLEANUP" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)
    print(f"Before:  {total_before}")
    print(f"Removed: {total_removed}")
    print(f"After:   {total_after}")
    print()
    print("By rule:")
    for reason, count in removed_counts.most_common():
        print(f"  {reason:25s} {count:5d}")
    print()
    print("Sample removals:")
    for ex in removed_examples[:20]:
        print(f"  [{ex['reason']:20s}] Vol {ex['vol']} | {ex['text']}")

    if dry_run:
        print(f"\nDRY RUN — no files modified.")
    else:
        print(f"\nDone — {total_removed} items removed from toc.json.")


if __name__ == '__main__':
    main()
