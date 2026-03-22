#!/usr/bin/env python3
"""Strip end-of-article reference/notes sections from HTML galley files.

These sections have been extracted into toc.json `references` and `notes` arrays.
OJS will render References from its citations table; Notes will be re-embedded
by generate_xml.py. Stripping avoids duplication.

Targets the same headings as extract_citations.py:
  References, Reference, Notes, Note, Endnotes, Endnote, Footnotes, Footnote,
  Bibliography, Further Reading, Works Cited, Notes and References,
  References and Notes, Selected Bibliography, References:

Usage:
    python3 backfill/strip_references.py --dry-run    # Report what would change
    python3 backfill/strip_references.py --apply       # Modify HTML files
"""

import sys
import os
import re
import json
import glob
import argparse

# Same heading pattern as extract_citations.py REFERENCE_HEADINGS
REFERENCE_HEADINGS_RE = re.compile(
    r'^('
    r'References?'
    r'|Notes?'
    r'|Endnotes?'
    r'|Footnotes?'
    r'|Bibliography'
    r'|Further Reading'
    r'|Works Cited'
    r'|Notes and References'
    r'|References and Notes'
    r'|Selected Bibliography'
    r'|References:'
    r')$',
    re.IGNORECASE
)


def strip_reference_sections(content):
    """Strip all end-of-article reference/notes sections from HTML content.

    Removes matching <h2> headings and everything after them through to the
    next non-matching <h2> or end of file. If multiple reference sections
    appear consecutively at the end, all are removed.

    Returns (modified_content, removed_headings) where removed_headings is
    a list of heading texts that were stripped.
    """
    stripped = content.strip()

    # Split at <h2> boundaries, keeping delimiters
    parts = re.split(r'(<h2[^>]*>.*?</h2>)', stripped, flags=re.DOTALL)

    # Find all <h2> sections with their indices in parts
    sections = []
    for i, part in enumerate(parts):
        m = re.match(r'<h2[^>]*>(.*?)</h2>', part, re.DOTALL)
        if m:
            heading_text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            sections.append((heading_text, i))

    if not sections:
        return content, []

    # Find which sections are reference/notes headings
    # We only strip sections that are at the END of the file — i.e., no
    # non-reference <h2> appears after the first reference heading we find.
    # This prevents stripping a "Notes" heading that appears mid-article.

    # Walk backwards from the end to find the contiguous block of
    # reference headings at the tail
    tail_ref_start = None
    for si in range(len(sections) - 1, -1, -1):
        heading = sections[si][0]
        if REFERENCE_HEADINGS_RE.match(heading):
            tail_ref_start = si
        else:
            break

    if tail_ref_start is None:
        return content, []

    # The reference sections to remove: from tail_ref_start to end
    removed_headings = [sections[si][0] for si in range(tail_ref_start, len(sections))]
    cut_at = sections[tail_ref_start][1]  # index in parts

    # Rebuild content from parts before the cut point
    result = ''.join(parts[:cut_at]).strip()

    return result, removed_headings


def main():
    parser = argparse.ArgumentParser(
        description='Strip end-of-article reference sections from HTML galley files')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true', help='Report what would change')
    group.add_argument('--apply', action='store_true', help='Modify HTML files')
    parser.add_argument('issues', nargs='*',
                        help='Specific issue dirs (e.g. backfill/output/10.2). Default: all.')
    args = parser.parse_args()

    if args.issues:
        html_files = []
        for issue_dir in args.issues:
            html_files.extend(sorted(glob.glob(os.path.join(issue_dir, '*.html'))))
    else:
        html_files = sorted(glob.glob('backfill/output/*/*.html'))

    if not html_files:
        print("No HTML files found.", file=sys.stderr)
        sys.exit(1)

    # Load toc.json data to cross-reference which articles have citations
    toc_cache = {}
    for toc_path in glob.glob('backfill/output/*/toc.json'):
        issue_dir = os.path.dirname(toc_path)
        with open(toc_path) as f:
            toc_cache[issue_dir] = json.load(f)

    stats = {
        'total_html': len(html_files),
        'has_ref_sections': 0,
        'stripped': 0,
        'skipped_no_toc_data': 0,
        'headings_removed': {},
    }
    details = []

    for html_path in html_files:
        issue_dir = os.path.dirname(html_path)
        html_basename = os.path.basename(html_path)
        issue_name = os.path.basename(issue_dir)

        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()

        result, removed_headings = strip_reference_sections(content)

        if not removed_headings:
            continue

        stats['has_ref_sections'] += 1

        # Cross-check: does toc.json have citations for this article?
        toc = toc_cache.get(issue_dir)
        has_toc_citations = False
        if toc:
            for art in toc.get('articles', []):
                # Match by HTML filename pattern: seq-slug.html or slug.html
                art_slug = art.get('slug', '')
                art_split = art.get('split_pdf', '')
                # The HTML file stem should match the split PDF stem
                html_stem = os.path.splitext(html_basename)[0]
                split_stem = os.path.splitext(os.path.basename(art_split))[0] if art_split else ''
                if html_stem == split_stem or (art_slug and art_slug in html_basename):
                    refs = art.get('references', [])
                    notes = art.get('notes', [])
                    citations = art.get('citations', [])
                    if refs or notes or citations:
                        has_toc_citations = True
                    break

        if not has_toc_citations:
            stats['skipped_no_toc_data'] += 1
            if args.dry_run:
                print(f"  WARN {issue_name}/{html_basename}: has sections {removed_headings} "
                      f"but no citations in toc.json — skipping")
            continue

        stats['stripped'] += 1
        for h in removed_headings:
            stats['headings_removed'][h] = stats['headings_removed'].get(h, 0) + 1

        if args.dry_run:
            details.append(f"  STRIP {issue_name}/{html_basename}: {removed_headings}")
        else:
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(result + '\n')

    # Summary
    print(f"\n{'=' * 60}")
    print(f"STRIP REFERENCE SECTIONS {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'=' * 60}")
    print(f"  Total HTML files:          {stats['total_html']}")
    print(f"  With reference sections:   {stats['has_ref_sections']}")
    print(f"  Stripped:                  {stats['stripped']}")
    print(f"  Skipped (no toc.json data):{stats['skipped_no_toc_data']}")
    print()
    if stats['headings_removed']:
        print("Headings removed:")
        for h, count in sorted(stats['headings_removed'].items(), key=lambda x: -x[1]):
            print(f"  {h:30s} {count:5d}")
    print()

    if args.dry_run and details:
        print(f"Files to strip ({len(details)}):")
        for d in details[:50]:
            print(d)
        if len(details) > 50:
            print(f"  ... and {len(details) - 50} more")
        print(f"\nDRY RUN — no files modified.")
    elif not args.dry_run:
        print(f"Done — {stats['stripped']} files modified.")


if __name__ == '__main__':
    main()
