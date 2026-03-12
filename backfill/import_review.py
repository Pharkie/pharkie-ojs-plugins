#!/usr/bin/env python3
"""
Import reviewed CSV back into toc.json files.

Reads a CSV (edited in Google Sheets or similar) and patches each toc.json
in place with the reviewed metadata. Creates a .pre-review backup before
modifying.

All validation runs before any files are touched. Hard-fails on:
- Missing/unknown/duplicate/empty IDs
- Missing toc.json paths
- Invalid section values

Flags:
    --dry-run   Preview changes without writing files
    --restore   Restore toc.json files from .pre-review backups

Usage:
    python backfill/import_review.py review.csv --dry-run
    python backfill/import_review.py review.csv
    python backfill/import_review.py review.csv --restore
"""

import sys
import os
import csv
import json
import re
import argparse
import tempfile


EDITABLE_FIELDS = ['title', 'authors', 'section', 'abstract', 'keywords']
VALID_SECTIONS = {'Editorial', 'Articles', 'Book Review Editorial', 'Book Reviews'}

# XML-invalid control chars (U+0000-U+001F except tab, newline, CR)
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def sanitize_text(value):
    """Remove XML-invalid control characters and normalize newlines to spaces.

    Returns (sanitized_value, list_of_transformations).
    """
    transformations = []

    # Strip control chars
    cleaned = _CONTROL_CHAR_RE.sub('', value)
    if cleaned != value:
        n_removed = len(value) - len(cleaned)
        transformations.append(f"{n_removed} control character(s) removed")
        value = cleaned

    # Replace newlines with spaces
    newline_count = value.count('\r\n') + value.count('\n') - value.count('\r\n')  # avoid double-counting
    if '\r\n' in value or '\n' in value:
        value = value.replace('\r\n', ' ').replace('\n', ' ')
        # Collapse multiple spaces from newline replacement
        while '  ' in value:
            value = value.replace('  ', ' ')
        transformations.append(f"{newline_count} newline(s) replaced with spaces")

    return value, transformations


def load_csv(csv_path):
    """Load CSV rows grouped by toc.json file path, keyed by review ID.

    Returns: {file_path: {review_id: row_dict, ...}, ...}
    Exits with error if duplicate IDs or missing id column detected.
    """
    grouped = {}
    # Track all IDs per file to detect duplicates before dict dedup
    id_counts = {}  # {file_path: {review_id: count}}

    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)

        if 'id' not in (reader.fieldnames or []):
            print("ERROR: CSV has no 'id' column. Re-export with current export_review.py.",
                  file=sys.stderr)
            sys.exit(1)

        for row in reader:
            file_path = row['file']
            review_id = row.get('id', '').strip()
            if file_path not in grouped:
                grouped[file_path] = {}
                id_counts[file_path] = {}
            id_counts[file_path][review_id] = id_counts[file_path].get(review_id, 0) + 1
            grouped[file_path][review_id] = row

    # Check for duplicate IDs early (before validation phase)
    dup_errors = []
    for file_path, counts in id_counts.items():
        for rid, count in counts.items():
            if count > 1:
                dup_errors.append(
                    f"{file_path}: duplicate ID '{rid}' appears {count} times in CSV")
    if dup_errors:
        print("ERROR: ID validation failed:", file=sys.stderr)
        for err in dup_errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    return grouped


def validate_ids(grouped, all_tocs):
    """Validate ID integrity across CSV and toc.json files.

    all_tocs: {file_path: toc_data}
    Returns list of error strings. Empty = valid.
    """
    errors = []

    for file_path, csv_rows in grouped.items():
        if file_path not in all_tocs:
            continue  # handled by path validation

        toc = all_tocs[file_path]
        articles = toc.get('articles', [])

        # Check for empty IDs in CSV
        for review_id in csv_rows:
            if not review_id:
                errors.append(f"{file_path}: CSV row has empty 'id' field")

        # Check for duplicate IDs in CSV
        id_counts = {}
        for review_id in csv_rows:
            id_counts[review_id] = id_counts.get(review_id, 0) + 1
        for review_id, count in id_counts.items():
            if count > 1:
                errors.append(f"{file_path}: duplicate ID '{review_id}' appears {count} times in CSV")

        # Check for duplicate IDs in toc.json
        toc_ids = {}
        for article in articles:
            rid = article.get('_review_id', '')
            if rid:
                toc_ids[rid] = toc_ids.get(rid, 0) + 1
        for rid, count in toc_ids.items():
            if count > 1:
                errors.append(f"{file_path}: duplicate _review_id '{rid}' in toc.json")

        # Build toc ID set
        toc_id_set = {a.get('_review_id', '') for a in articles if a.get('_review_id')}
        csv_id_set = {rid for rid in csv_rows if rid}

        # Unknown IDs in CSV (not in toc.json)
        for rid in sorted(csv_id_set - toc_id_set):
            errors.append(f"{file_path}: CSV has unknown ID '{rid}' not found in toc.json")

        # Missing articles (in toc.json but not in CSV)
        for rid in sorted(toc_id_set - csv_id_set):
            errors.append(f"{file_path}: toc.json article '{rid}' has no corresponding CSV row (deleted?)")

    return errors


def validate_sections(grouped):
    """Check all section values are valid. Returns list of error strings."""
    errors = []
    for file_path, csv_rows in grouped.items():
        for review_id, row in csv_rows.items():
            section = row.get('section', '').strip()
            if section and section not in VALID_SECTIONS:
                errors.append(
                    f"{file_path}: article '{review_id}' has invalid section '{section}' "
                    f"(valid: {', '.join(sorted(VALID_SECTIONS))})")
    return errors


def write_json_atomic(path, data):
    """Write JSON atomically via temp file + os.replace()."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)), suffix='.json.tmp')
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def apply_edits(toc_path, toc, edits, dry_run=False):
    """Apply CSV edits to a toc dict. Returns (change_counts, diff_lines).

    edits: {review_id: row_dict}
    If dry_run, toc is not modified (but diffs are still computed).
    """
    articles = toc.get('articles', [])

    # Build lookup: _review_id -> article index
    id_to_idx = {}
    for i, article in enumerate(articles):
        rid = article.get('_review_id')
        if rid:
            id_to_idx[rid] = i

    title_changes = 0
    author_changes = 0
    other_changes = 0
    updated = 0
    diff_lines = []

    for review_id, row in edits.items():
        if not review_id or review_id not in id_to_idx:
            continue

        idx = id_to_idx[review_id]
        article = articles[idx]
        changed = False
        article_diffs = []

        # Title
        new_title = row.get('title', '').strip()
        new_title, title_transforms = sanitize_text(new_title)
        old_title = article.get('title', '')
        if new_title and new_title != old_title:
            article_diffs.append(f"    title: {old_title!r} → {new_title!r}")
            for t in title_transforms:
                article_diffs.append(f"      TRANSFORMED: title had {t}")
            if not dry_run:
                article['title'] = new_title
            title_changes += 1
            changed = True
        elif title_transforms and new_title:
            # Transforms applied but result matches — still report in dry-run
            for t in title_transforms:
                article_diffs.append(f"      TRANSFORMED: title had {t} (no net change)")

        # Authors
        new_authors = row.get('authors', '').strip()
        new_authors, author_transforms = sanitize_text(new_authors)
        old_authors = article.get('authors', '')
        if new_authors and new_authors != old_authors:
            article_diffs.append(f"    authors: {old_authors!r} → {new_authors!r}")
            for t in author_transforms:
                article_diffs.append(f"      TRANSFORMED: authors had {t}")
            if not dry_run:
                if 'authors_original' not in article:
                    article['authors_original'] = old_authors
                article['authors'] = new_authors
                article['_authors_counted'] = False
            author_changes += 1
            changed = True

        # Section
        new_section = row.get('section', '').strip()
        old_section = article.get('section', '')
        if new_section and new_section != old_section:
            article_diffs.append(f"    section: {old_section!r} → {new_section!r}")
            if not dry_run:
                article['section'] = new_section
            other_changes += 1
            changed = True

        # Abstract
        new_abstract = row.get('abstract', '').strip()
        new_abstract, abstract_transforms = sanitize_text(new_abstract)
        old_abstract = article.get('abstract', '')
        if new_abstract != old_abstract:
            # Truncate long values for display
            old_display = (old_abstract[:60] + '...') if len(old_abstract) > 63 else old_abstract
            new_display = (new_abstract[:60] + '...') if len(new_abstract) > 63 else new_abstract
            article_diffs.append(f"    abstract: {old_display!r} → {new_display!r}")
            for t in abstract_transforms:
                article_diffs.append(f"      TRANSFORMED: abstract had {t}")
            if not dry_run:
                article['abstract'] = new_abstract
            other_changes += 1
            changed = True

        # Keywords
        new_keywords_str = row.get('keywords', '').strip()
        new_keywords_str, kw_transforms = sanitize_text(new_keywords_str)
        new_keywords = [k.strip() for k in new_keywords_str.split(';') if k.strip()] if new_keywords_str else []
        old_keywords = article.get('keywords', [])
        if isinstance(old_keywords, str):
            old_keywords = [k.strip() for k in old_keywords.split(';') if k.strip()]
        if new_keywords != old_keywords:
            article_diffs.append(f"    keywords: {old_keywords!r} → {new_keywords!r}")
            for t in kw_transforms:
                article_diffs.append(f"      TRANSFORMED: keywords had {t}")
            if not dry_run:
                article['keywords'] = new_keywords
            other_changes += 1
            changed = True

        if changed:
            updated += 1
            diff_lines.append(f"  [{review_id}] {article.get('title', '')[:50]}")
            diff_lines.extend(article_diffs)

    return {
        'updated': updated,
        'title_changes': title_changes,
        'author_changes': author_changes,
        'other_changes': other_changes,
    }, diff_lines


def do_restore(csv_path):
    """Restore toc.json files from .pre-review backups."""
    grouped = load_csv(csv_path)
    restored = 0
    for toc_path in sorted(grouped.keys()):
        backup_path = toc_path + '.pre-review'
        if not os.path.exists(backup_path):
            print(f"  {toc_path}: no .pre-review backup found, skipping", file=sys.stderr)
            continue
        with open(backup_path) as f:
            original = json.load(f)
        write_json_atomic(toc_path, original)
        print(f"  {toc_path}: restored from {backup_path}", file=sys.stderr)
        restored += 1

    if restored:
        print(f"\nRestored {restored} file(s).", file=sys.stderr)
    else:
        print("\nNo backups found to restore.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='Import reviewed CSV back into toc.json files')
    parser.add_argument('csv_file', help='Reviewed CSV file')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without writing files')
    parser.add_argument('--restore', action='store_true',
                        help='Restore toc.json files from .pre-review backups')
    args = parser.parse_args()

    if not os.path.exists(args.csv_file):
        print(f"ERROR: {args.csv_file} not found", file=sys.stderr)
        sys.exit(1)

    if args.restore:
        do_restore(args.csv_file)
        return

    grouped = load_csv(args.csv_file)
    if not grouped:
        print("No rows found in CSV.", file=sys.stderr)
        sys.exit(1)

    # --- Phase 1: Validate ALL paths exist before touching anything ---
    missing_paths = []
    for toc_path in sorted(grouped.keys()):
        if not os.path.exists(toc_path):
            missing_paths.append(toc_path)
    if missing_paths:
        print("ERROR: toc.json file(s) not found:", file=sys.stderr)
        for p in missing_paths:
            print(f"  {p}", file=sys.stderr)
        sys.exit(1)

    # --- Phase 2: Load all toc.json files ---
    all_tocs = {}
    for toc_path in grouped:
        with open(toc_path) as f:
            all_tocs[toc_path] = json.load(f)

    # --- Phase 3: Validate IDs ---
    id_errors = validate_ids(grouped, all_tocs)
    if id_errors:
        print("ERROR: ID validation failed:", file=sys.stderr)
        for err in id_errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    # --- Phase 4: Validate sections ---
    section_errors = validate_sections(grouped)
    if section_errors:
        print("ERROR: Invalid section values:", file=sys.stderr)
        for err in section_errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    # --- Phase 5: Print summary and apply edits ---
    total_updated = 0
    total_title = 0
    total_author = 0
    total_other = 0

    for toc_path in sorted(grouped.keys()):
        toc = all_tocs[toc_path]
        edits = grouped[toc_path]
        n_articles = len(toc.get('articles', []))
        n_rows = len(edits)
        print(f"  {toc_path}: {n_articles} articles in toc.json, {n_rows} rows in CSV",
              file=sys.stderr)

        counts, diff_lines = apply_edits(toc_path, toc, edits, dry_run=args.dry_run)
        total_updated += counts['updated']
        total_title += counts['title_changes']
        total_author += counts['author_changes']
        total_other += counts['other_changes']

        if diff_lines:
            for line in diff_lines:
                print(line, file=sys.stderr)
        elif not args.dry_run:
            print(f"    no changes", file=sys.stderr)

        if not args.dry_run and counts['updated']:
            # Save backup before first write
            backup_path = toc_path + '.pre-review'
            if not os.path.exists(backup_path):
                with open(toc_path) as f:
                    original = json.load(f)
                write_json_atomic(backup_path, original)
            write_json_atomic(toc_path, toc)

    mode = "DRY RUN — " if args.dry_run else ""
    print(f"\n{mode}Total: {total_updated} articles {'would be ' if args.dry_run else ''}updated "
          f"across {len(grouped)} issue(s) "
          f"({total_title} title, {total_author} author, {total_other} other changes)",
          file=sys.stderr)

    if args.dry_run and total_updated:
        print("\nNo files written. Run without --dry-run to apply.", file=sys.stderr)


if __name__ == '__main__':
    main()
