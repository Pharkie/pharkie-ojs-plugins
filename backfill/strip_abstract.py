#!/usr/bin/env python3
"""
Phase 2: Strip Abstract (and optional Keywords) sections from HTML galley files.

After Phase 1 has updated toc.json with the cleaner HTML abstracts, this script
removes the duplicate abstract from the HTML files themselves. OJS displays the
metadata abstract separately, so having it in the HTML body causes duplication.

Handles both leading abstracts (file starts with <h2>Abstract</h2>) and
non-leading abstracts (preceded by epigraphs, boilerplate, etc.).

Usage:
    python3 backfill/strip_abstract.py --dry-run    # Report what would change
    python3 backfill/strip_abstract.py --apply       # Modify HTML files
"""

import sys
import os
import re
import glob
import argparse


def strip_abstract_section(content):
    """Strip Abstract and optional Keywords sections from HTML content.

    Handles both leading and non-leading Abstract sections.
    Returns (modified_content, was_modified) or (None, False) if should be skipped.
    """
    stripped = content.strip()
    if '<h2>Abstract</h2>' not in stripped:
        return content, False

    # Split at <h2> boundaries, keeping delimiters
    parts = re.split(r'(<h2>.*?</h2>)', stripped, flags=re.DOTALL)

    # Find all sections: (heading_text, index_in_parts)
    sections = []
    for i, part in enumerate(parts):
        m = re.match(r'<h2>(.*?)</h2>', part, re.DOTALL)
        if m:
            sections.append((m.group(1).strip(), i))

    if not sections:
        return content, False

    # Find the Abstract section
    abstract_idx = None
    for si, (heading, _) in enumerate(sections):
        if heading == 'Abstract':
            abstract_idx = si
            break

    if abstract_idx is None:
        return content, False

    # Determine how many sections to strip (Abstract + optional Keywords)
    skip_start = abstract_idx
    skip_end = abstract_idx + 1
    if skip_end < len(sections):
        next_heading = sections[skip_end][0]
        if re.match(r'Key\s*[Ww]ords?$', next_heading):
            skip_end += 1

    # Safety: must have at least one <h2> section remaining after stripping
    remaining_sections = sections[:skip_start] + sections[skip_end:]
    if not remaining_sections:
        return None, False

    # Rebuild: keep everything before the Abstract section, skip Abstract
    # (+optional Keywords), keep everything after
    abstract_parts_start = sections[skip_start][1]
    if skip_end < len(sections):
        abstract_parts_end = sections[skip_end][1]
    else:
        abstract_parts_end = len(parts)

    before = ''.join(parts[:abstract_parts_start])
    after = ''.join(parts[abstract_parts_end:])
    result = (before + after).strip()

    return result, True


def main():
    parser = argparse.ArgumentParser(description='Strip abstracts from HTML galley files')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true', help='Report what would change')
    group.add_argument('--apply', action='store_true', help='Modify HTML files')
    args = parser.parse_args()

    html_files = sorted(glob.glob('backfill/private/output/*/*.html'))
    if not html_files:
        print("No HTML files found in backfill/private/output/*/", file=sys.stderr)
        sys.exit(1)

    stats = {
        'total_html': len(html_files),
        'has_abstract': 0,
        'stripped_leading': 0,
        'stripped_nonleading': 0,
        'skipped_no_remaining': 0,
    }

    for html_path in html_files:
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if '<h2>Abstract</h2>' not in content:
            continue

        stats['has_abstract'] += 1
        issue_name = os.path.basename(os.path.dirname(html_path))
        html_basename = os.path.basename(html_path)
        is_leading = content.strip().startswith('<h2>Abstract</h2>')

        result, was_modified = strip_abstract_section(content)

        if result is None:
            stats['skipped_no_remaining'] += 1
            print(f"  SKIP {issue_name}/{html_basename}: no content headings after abstract")
            continue

        if was_modified:
            if is_leading:
                stats['stripped_leading'] += 1
            else:
                stats['stripped_nonleading'] += 1
            if args.dry_run:
                first_line = result.split('\n')[0][:100]
                label = 'STRIP' if is_leading else 'STRIP (non-leading)'
                print(f"  {label} {issue_name}/{html_basename} -> starts with: {first_line}")
            else:
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(result + '\n')

    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN' if args.dry_run else 'APPLIED'} Summary:")
    print(f"  Total HTML files:              {stats['total_html']}")
    print(f"  HTML files with abstract:      {stats['has_abstract']}")
    print(f"  Abstracts stripped (leading):  {stats['stripped_leading']}")
    print(f"  Abstracts stripped (non-lead): {stats['stripped_nonleading']}")
    print(f"  Skipped (no remaining content):{stats['skipped_no_remaining']}")


if __name__ == '__main__':
    main()
