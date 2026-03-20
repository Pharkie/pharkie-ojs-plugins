#!/usr/bin/env python3
"""
Phase 2: Strip leading Abstract (and optional Keywords) sections from HTML galley files.

After Phase 1 has updated toc.json with the cleaner HTML abstracts, this script
removes the duplicate abstract from the HTML files themselves. OJS displays the
metadata abstract separately, so having it in the HTML body causes duplication.

Usage:
    python3 backfill/strip_abstract.py --dry-run    # Report what would change
    python3 backfill/strip_abstract.py --apply       # Modify HTML files
"""

import sys
import os
import re
import glob
import argparse


def strip_leading_abstract(content):
    """Strip leading Abstract and optional Keywords sections from HTML content.

    Returns (modified_content, was_modified) or (None, False) if should be skipped.
    """
    stripped = content.strip()
    if not stripped.startswith('<h2>Abstract</h2>'):
        return content, False

    # Split at <h2> boundaries, keeping delimiters
    # We'll rebuild from the parts we want to keep
    parts = re.split(r'(<h2>.*?</h2>)', stripped, flags=re.DOTALL)
    # parts = [before_first_h2, <h2>Abstract</h2>, content_after, <h2>Next</h2>, content_after, ...]

    # Find sections to skip (Abstract + optional Keywords)
    sections = []  # list of (heading_text, start_index_in_parts)
    for i, part in enumerate(parts):
        m = re.match(r'<h2>(.*?)</h2>', part, re.DOTALL)
        if m:
            sections.append((m.group(1).strip(), i))

    if not sections:
        return content, False

    # Determine how many leading sections to strip
    skip_until = 1  # Always skip Abstract (first section)
    if len(sections) > 1:
        next_heading = sections[1][0]
        if re.match(r'Key\s*[Ww]ords?$', next_heading):
            skip_until = 2  # Also skip Keywords section

    # Safety: check remaining content has at least one <h2>
    remaining_sections = sections[skip_until:]
    if not remaining_sections:
        return None, False  # Skip - no content headings remain

    # Rebuild from the first kept section
    first_kept_idx = sections[skip_until][1]
    result = ''.join(parts[first_kept_idx:]).strip()

    return result, True


def main():
    parser = argparse.ArgumentParser(description='Strip leading abstracts from HTML galley files')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true', help='Report what would change')
    group.add_argument('--apply', action='store_true', help='Modify HTML files')
    args = parser.parse_args()

    html_files = sorted(glob.glob('backfill/output/*/*.html'))
    if not html_files:
        print("No HTML files found in backfill/output/*/", file=sys.stderr)
        sys.exit(1)

    stats = {
        'total_html': len(html_files),
        'has_abstract': 0,
        'stripped': 0,
        'skipped_no_remaining': 0,
    }

    for html_path in html_files:
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if not content.strip().startswith('<h2>Abstract</h2>'):
            continue

        stats['has_abstract'] += 1
        issue_name = os.path.basename(os.path.dirname(html_path))
        html_basename = os.path.basename(html_path)

        result, was_modified = strip_leading_abstract(content)

        if result is None:
            stats['skipped_no_remaining'] += 1
            print(f"  SKIP {issue_name}/{html_basename}: no content headings after abstract")
            continue

        if was_modified:
            stats['stripped'] += 1
            if args.dry_run:
                # Show first line of remaining content
                first_line = result.split('\n')[0][:100]
                print(f"  STRIP {issue_name}/{html_basename} -> starts with: {first_line}")
            else:
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(result + '\n')

    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN' if args.dry_run else 'APPLIED'} Summary:")
    print(f"  Total HTML files:              {stats['total_html']}")
    print(f"  HTML files with abstract:      {stats['has_abstract']}")
    print(f"  Abstracts stripped:            {stats['stripped']}")
    print(f"  Skipped (no remaining content):{stats['skipped_no_remaining']}")


if __name__ == '__main__':
    main()
