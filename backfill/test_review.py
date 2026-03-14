#!/usr/bin/env python3
"""
Automated tests for the export/import review pipeline.

Covers all validation, sanitization, and round-trip scenarios.
No pytest needed — uses assert + temp dirs. Run with:

    python3 backfill/test_review.py
"""

import sys
import os
import csv
import json
import shutil
import subprocess
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_SCRIPT = os.path.join(SCRIPT_DIR, 'export_review.py')
IMPORT_SCRIPT = os.path.join(SCRIPT_DIR, 'import_review.py')


def make_toc(tmpdir, vol=37, iss=1, articles=None):
    """Create a toc.json in an appropriately-named subdirectory."""
    issue_dir = os.path.join(tmpdir, f'{vol}.{iss}')
    os.makedirs(issue_dir, exist_ok=True)
    toc_path = os.path.join(issue_dir, 'toc.json')

    if articles is None:
        articles = [
            {
                'title': 'First Article',
                'authors': 'Smith, John',
                'section': 'Articles',
                'abstract': 'An abstract about things.',
                'keywords': ['phenomenology', 'therapy'],
                'journal_page_start': 1,
                'journal_page_end': 10,
            },
            {
                'title': 'Second Article',
                'authors': 'Doe, Jane',
                'section': 'Book Reviews',
                'abstract': 'A review of a book.',
                'keywords': ['existentialism'],
                'journal_page_start': 11,
                'journal_page_end': 20,
            },
            {
                'title': 'Third Article',
                'authors': 'Brown, Alice',
                'section': 'Editorial',
                'abstract': 'Editorial note.',
                'keywords': [],
                'journal_page_start': 21,
                'journal_page_end': 25,
            },
        ]

    toc = {'volume': vol, 'issue': iss, 'articles': articles}
    with open(toc_path, 'w') as f:
        json.dump(toc, f, indent=2)
    return toc_path


def run_export(toc_paths, output_csv, expect_fail=False):
    """Run export_review.py, return (returncode, stderr)."""
    cmd = [sys.executable, EXPORT_SCRIPT] + toc_paths + ['-o', output_csv]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if not expect_fail and result.returncode != 0:
        print(f"EXPORT FAILED: {result.stderr}", file=sys.stderr)
    return result.returncode, result.stderr


def run_import(csv_path, dry_run=False, restore=False, expect_fail=False):
    """Run import_review.py, return (returncode, stderr)."""
    cmd = [sys.executable, IMPORT_SCRIPT, csv_path]
    if dry_run:
        cmd.append('--dry-run')
    if restore:
        cmd.append('--restore')
    result = subprocess.run(cmd, capture_output=True, text=True)
    if not expect_fail and result.returncode != 0:
        print(f"IMPORT FAILED: {result.stderr}", file=sys.stderr)
    return result.returncode, result.stderr


def read_toc(toc_path):
    with open(toc_path) as f:
        return json.load(f)


def read_csv_rows(csv_path):
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def modify_csv(csv_path, modifier_fn):
    """Read CSV, apply modifier_fn to list of row dicts, write back."""
    rows = read_csv_rows(csv_path)
    rows = modifier_fn(rows)
    if rows:
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_happy_path_round_trip():
    """1. Export → modify CSV → import → assert edits applied, backup exists, IDs preserved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        # Export
        rc, _ = run_export([toc_path], csv_path)
        assert rc == 0, "export failed"

        # Verify IDs assigned in toc.json
        toc = read_toc(toc_path)
        assert toc['articles'][0]['_review_id'] == 'v37i1a0'
        assert toc['articles'][1]['_review_id'] == 'v37i1a1'

        # Modify CSV
        def edit(rows):
            rows[0]['title'] = 'Updated First Article'
            rows[1]['authors'] = 'Doe, Jane; Smith, Bob'
            rows[2]['keywords'] = 'new keyword'
            return rows
        modify_csv(csv_path, edit)

        # Import
        rc, stderr = run_import(csv_path)
        assert rc == 0, f"import failed: {stderr}"

        # Verify edits
        toc = read_toc(toc_path)
        assert toc['articles'][0]['title'] == 'Updated First Article'
        assert toc['articles'][1]['authors'] == 'Doe, Jane; Smith, Bob'
        assert toc['articles'][2]['keywords'] == ['new keyword']

        # Verify backup exists
        assert os.path.exists(toc_path + '.pre-review')

        # Verify IDs still present
        assert toc['articles'][0]['_review_id'] == 'v37i1a0'

    print("  PASS: test_happy_path_round_trip")


def test_dry_run():
    """2. Export → modify → --dry-run → assert toc.json unchanged."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)
        original_toc = read_toc(toc_path)

        def edit(rows):
            rows[0]['title'] = 'Changed Title'
            return rows
        modify_csv(csv_path, edit)

        rc, stderr = run_import(csv_path, dry_run=True)
        assert rc == 0, f"dry-run failed: {stderr}"
        assert 'DRY RUN' in stderr
        assert 'Changed Title' in stderr

        # toc.json unchanged
        toc = read_toc(toc_path)
        assert toc['articles'][0]['title'] == 'First Article'
        assert not os.path.exists(toc_path + '.pre-review')

    print("  PASS: test_dry_run")


def test_row_reorder_survives():
    """3. Swap CSV row order → import → edits applied to correct articles."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)

        def reorder_and_edit(rows):
            # Reverse order and edit each
            rows = list(reversed(rows))
            for r in rows:
                if r['id'] == 'v37i1a0':
                    r['title'] = 'First Edited'
                elif r['id'] == 'v37i1a2':
                    r['title'] = 'Third Edited'
            return rows
        modify_csv(csv_path, reorder_and_edit)

        rc, _ = run_import(csv_path)
        assert rc == 0

        toc = read_toc(toc_path)
        assert toc['articles'][0]['title'] == 'First Edited'
        assert toc['articles'][1]['title'] == 'Second Article'  # unchanged
        assert toc['articles'][2]['title'] == 'Third Edited'

    print("  PASS: test_row_reorder_survives")


def test_row_deletion_caught():
    """4. Delete a CSV row → import → hard failure, toc.json unchanged."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)
        original_toc = read_toc(toc_path)

        def delete_row(rows):
            return [rows[0], rows[2]]  # skip row 1
        modify_csv(csv_path, delete_row)

        rc, stderr = run_import(csv_path, expect_fail=True)
        assert rc == 1, "should have failed"
        assert 'no corresponding CSV row' in stderr

        # toc.json unchanged
        toc = read_toc(toc_path)
        assert toc['articles'] == original_toc['articles']

    print("  PASS: test_row_deletion_caught")


def test_duplicate_id_caught():
    """5. Duplicate a CSV row → import → hard failure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)

        def dup_row(rows):
            rows.append(dict(rows[0]))  # duplicate first row
            return rows
        modify_csv(csv_path, dup_row)

        rc, stderr = run_import(csv_path, expect_fail=True)
        assert rc == 1, "should have failed"
        assert 'duplicate ID' in stderr

    print("  PASS: test_duplicate_id_caught")


def test_unknown_id_caught():
    """6. Change an ID value → import → hard failure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)

        def bad_id(rows):
            rows[0]['id'] = 'v99i99a99'
            return rows
        modify_csv(csv_path, bad_id)

        rc, stderr = run_import(csv_path, expect_fail=True)
        assert rc == 1, "should have failed"
        assert 'unknown ID' in stderr

    print("  PASS: test_unknown_id_caught")


def test_empty_id_caught():
    """7. Blank out an ID cell → import → hard failure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)

        def blank_id(rows):
            rows[1]['id'] = ''
            return rows
        modify_csv(csv_path, blank_id)

        rc, stderr = run_import(csv_path, expect_fail=True)
        assert rc == 1, "should have failed"
        assert 'empty' in stderr.lower()

    print("  PASS: test_empty_id_caught")


def test_missing_toc_path_caught():
    """8. Change file path to nonexistent → import → hard failure before any files touched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)

        def bad_path(rows):
            for r in rows:
                r['file'] = '/nonexistent/toc.json'
            return rows
        modify_csv(csv_path, bad_path)

        rc, stderr = run_import(csv_path, expect_fail=True)
        assert rc == 1, "should have failed"
        assert 'not found' in stderr

    print("  PASS: test_missing_toc_path_caught")


def test_newlines_transformed():
    """9. Inject newlines → dry-run reports transformation; real import strips them."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)

        def add_newlines(rows):
            rows[0]['abstract'] = 'Line one.\nLine two.\r\nLine three.'
            return rows
        modify_csv(csv_path, add_newlines)

        # Dry run — check transformation reported
        rc, stderr = run_import(csv_path, dry_run=True)
        assert rc == 0
        assert 'TRANSFORMED' in stderr
        assert 'newline' in stderr.lower()

        # Real import
        rc, _ = run_import(csv_path)
        assert rc == 0

        toc = read_toc(toc_path)
        abstract = toc['articles'][0]['abstract']
        assert '\n' not in abstract
        assert '\r' not in abstract
        assert 'Line one. Line two. Line three.' == abstract

    print("  PASS: test_newlines_transformed")


def test_control_chars_stripped():
    """10. Inject control char → import → char removed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)

        def add_control(rows):
            rows[0]['title'] = 'Title\x00With\x01Control'
            return rows
        modify_csv(csv_path, add_control)

        rc, _ = run_import(csv_path)
        assert rc == 0

        toc = read_toc(toc_path)
        assert toc['articles'][0]['title'] == 'TitleWithControl'

    print("  PASS: test_control_chars_stripped")


def test_invalid_section_rejected():
    """11. Set invalid section → import → hard failure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)
        original_toc = read_toc(toc_path)

        def bad_section(rows):
            rows[0]['section'] = 'Artcles'  # typo
            return rows
        modify_csv(csv_path, bad_section)

        rc, stderr = run_import(csv_path, expect_fail=True)
        assert rc == 1, "should have failed"
        assert 'invalid section' in stderr.lower() or 'Invalid section' in stderr

        # toc.json unchanged
        toc = read_toc(toc_path)
        assert toc['articles'] == original_toc['articles']

    print("  PASS: test_invalid_section_rejected")


def test_restore_works():
    """12. Export → import edits → --restore → toc.json matches original."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)
        original_toc = read_toc(toc_path)

        def edit(rows):
            rows[0]['title'] = 'Modified Title'
            return rows
        modify_csv(csv_path, edit)

        run_import(csv_path)
        toc = read_toc(toc_path)
        assert toc['articles'][0]['title'] == 'Modified Title'

        # Restore
        rc, _ = run_import(csv_path, restore=True)
        assert rc == 0

        toc = read_toc(toc_path)
        assert toc['articles'][0]['title'] == original_toc['articles'][0]['title']

    print("  PASS: test_restore_works")


def test_idempotent_reimport():
    """13. Import same CSV twice → second run reports no changes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')

        run_export([toc_path], csv_path)

        def edit(rows):
            rows[0]['title'] = 'Edited Title'
            return rows
        modify_csv(csv_path, edit)

        # First import
        rc, _ = run_import(csv_path)
        assert rc == 0
        toc_after_first = read_toc(toc_path)

        # Second import — should report 0 updated
        rc, stderr = run_import(csv_path)
        assert rc == 0
        assert '0 articles' in stderr or 'no changes' in stderr.lower()

        # toc.json identical
        toc_after_second = read_toc(toc_path)
        assert toc_after_first == toc_after_second

    print("  PASS: test_idempotent_reimport")


def test_ids_stable_across_reexport():
    """14. Export → import edits → re-export → IDs unchanged, edited values in new CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)
        csv_path = os.path.join(tmpdir, 'review.csv')
        csv_path2 = os.path.join(tmpdir, 'review2.csv')

        run_export([toc_path], csv_path)

        def edit(rows):
            rows[0]['title'] = 'Stable ID Title'
            return rows
        modify_csv(csv_path, edit)

        run_import(csv_path)

        # Re-export
        run_export([toc_path], csv_path2)

        rows = read_csv_rows(csv_path2)
        assert rows[0]['id'] == 'v37i1a0'
        assert rows[0]['title'] == 'Stable ID Title'
        assert rows[1]['id'] == 'v37i1a1'
        assert rows[2]['id'] == 'v37i1a2'

    print("  PASS: test_ids_stable_across_reexport")


def test_pdf_file_column_in_csv():
    """15. Export includes pdf_file column with basename of split PDF."""
    with tempfile.TemporaryDirectory() as tmpdir:
        articles = [
            {
                'title': 'Test Article',
                'authors': 'Smith, John',
                'section': 'Articles',
                'abstract': 'Test.',
                'keywords': [],
                'journal_page_start': 1,
                'journal_page_end': 10,
                'split_pdf': '/some/path/01-test-article.pdf',
            },
        ]
        toc_path = make_toc(tmpdir, articles=articles)
        csv_path = os.path.join(tmpdir, 'review.csv')

        rc, _ = run_export([toc_path], csv_path)
        assert rc == 0

        rows = read_csv_rows(csv_path)
        assert rows[0]['pdf_file'] == '01-test-article.pdf'

    print("  PASS: test_pdf_file_column_in_csv")


def test_enrichment_columns_in_csv():
    """16. Export includes enrichment columns when enrichment.json exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)

        # Create enrichment.json
        issue_dir = os.path.dirname(toc_path)
        enrichment = {
            '_generated': '2026-01-01T00:00:00Z',
            '_model': 'test',
            '_version': 1,
            'articles': {
                'v37i1a0': {
                    'subjects': ['Existential Therapy'],
                    'disciplines': ['Psychotherapy'],
                    'keywords_enriched': ['phenomenology', 'therapy', 'existentialism'],
                }
            }
        }
        with open(os.path.join(issue_dir, 'enrichment.json'), 'w') as f:
            json.dump(enrichment, f)

        csv_path = os.path.join(tmpdir, 'review.csv')

        # Need to export first to assign review IDs
        rc, _ = run_export([toc_path], csv_path)
        assert rc == 0

        rows = read_csv_rows(csv_path)
        assert 'subjects' in rows[0]
        assert 'disciplines' in rows[0]
        assert 'keywords_enriched' in rows[0]
        assert rows[0]['subjects'] == 'Existential Therapy'
        assert rows[0]['disciplines'] == 'Psychotherapy'

    print("  PASS: test_enrichment_columns_in_csv")


def test_subjects_disciplines_round_trip():
    """17. Export enrichment → edit subjects/disciplines → import → verify in toc.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        toc_path = make_toc(tmpdir)

        # Create enrichment.json
        issue_dir = os.path.dirname(toc_path)
        enrichment = {
            '_generated': '2026-01-01T00:00:00Z',
            '_model': 'test',
            '_version': 1,
            'articles': {
                'v37i1a0': {
                    'subjects': ['Existential Therapy'],
                    'disciplines': ['Psychotherapy'],
                    'keywords_enriched': ['phenomenology', 'therapy'],
                }
            }
        }
        with open(os.path.join(issue_dir, 'enrichment.json'), 'w') as f:
            json.dump(enrichment, f)

        csv_path = os.path.join(tmpdir, 'review.csv')
        rc, _ = run_export([toc_path], csv_path)
        assert rc == 0

        # Edit subjects in CSV
        def edit(rows):
            rows[0]['subjects'] = 'Existential Therapy; Phenomenology'
            rows[0]['disciplines'] = 'Psychotherapy; Philosophy'
            rows[0]['keywords_enriched'] = 'phenomenology; therapy; dasein'
            return rows
        modify_csv(csv_path, edit)

        # Import
        rc, stderr = run_import(csv_path)
        assert rc == 0, f"import failed: {stderr}"

        # Verify
        toc = read_toc(toc_path)
        assert toc['articles'][0]['subjects'] == ['Existential Therapy', 'Phenomenology']
        assert toc['articles'][0]['disciplines'] == ['Psychotherapy', 'Philosophy']
        # keywords_enriched replaces keywords
        assert toc['articles'][0]['keywords'] == ['phenomenology', 'therapy', 'dasein']

    print("  PASS: test_subjects_disciplines_round_trip")


# ─── Runner ──────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_happy_path_round_trip,
        test_dry_run,
        test_row_reorder_survives,
        test_row_deletion_caught,
        test_duplicate_id_caught,
        test_unknown_id_caught,
        test_empty_id_caught,
        test_missing_toc_path_caught,
        test_newlines_transformed,
        test_control_chars_stripped,
        test_invalid_section_rejected,
        test_restore_works,
        test_idempotent_reimport,
        test_ids_stable_across_reexport,
        test_pdf_file_column_in_csv,
        test_enrichment_columns_in_csv,
        test_subjects_disciplines_round_trip,
    ]

    print(f"Running {len(tests)} tests...\n")
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test_fn.__name__}: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"{passed} passed, {failed} failed out of {len(tests)} tests")
    if failed:
        sys.exit(1)
    print("All tests passed!")


if __name__ == '__main__':
    main()
