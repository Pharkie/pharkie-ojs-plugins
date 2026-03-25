#!/usr/bin/env python3
"""
Push journal page numbers from JATS to a live (or dev) OJS database.

Page numbers are read from JATS <fpage>/<lpage> (single source of truth)
and written to publication_settings (setting_name='pages').
Articles are matched by title within the correct issue (volume + number).

Usage:
    # Preview SQL without executing:
    python backfill/push_page_numbers.py --dry-run --target dev

    # Execute against dev:
    python backfill/push_page_numbers.py --target dev

    # Execute against live (requires --confirm):
    python backfill/push_page_numbers.py --target live --confirm

    # Single issue:
    python backfill/push_page_numbers.py --target dev --issue 35.2
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from xml.etree import ElementTree as ET

BACKFILL_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(BACKFILL_DIR, 'private', 'output')

# How to run mysql for each target
TARGETS = {
    'dev': {
        'cmd': [
            'docker', 'compose', 'exec', '-T', 'ojs-db',
            'bash', '-c',
            'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N',
        ],
    },
    'live': {
        'cmd': [
            'ssh', 'sea-live',
            'cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs-db '
            'bash -c \'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N\'',
        ],
    },
}


class SqlError(Exception):
    pass


def run_sql(target: str, sql: str) -> str:
    """Execute SQL against the target and return output. Raises SqlError on failure."""
    cfg = TARGETS[target]
    proc = subprocess.run(
        cfg['cmd'],
        input=sql,
        capture_output=True,
        text=True,
    )
    stderr = proc.stderr.strip()
    # Filter out mysql password warnings (not real errors)
    stderr_lines = [l for l in stderr.splitlines()
                    if 'password on the command line' not in l]
    stderr_clean = '\n'.join(stderr_lines).strip()

    if proc.returncode != 0:
        raise SqlError(f'SQL failed (exit {proc.returncode}): {stderr_clean}')
    if stderr_clean:
        # Warnings that aren't fatal — log but continue
        print(f'  SQL warning: {stderr_clean}', file=sys.stderr)
    return proc.stdout


def check_connectivity(target: str) -> bool:
    """Verify we can reach the database."""
    try:
        out = run_sql(target, 'SELECT 1;')
        return '1' in out
    except (SqlError, Exception) as e:
        print(f'ERROR: Cannot connect to {target} database: {e}', file=sys.stderr)
        return False


def get_issue_publications(target: str, volume: str, number: str) -> dict[str, dict]:
    """
    Get all publications for an issue, keyed by normalised title.
    Returns {title: {publication_id, current_pages}}.
    """
    sql = f"""
        SELECT p.publication_id,
               ps_title.setting_value AS title,
               IFNULL(ps_pages.setting_value, 'NULL') AS pages
        FROM publications p
        JOIN issues i ON p.issue_id = i.issue_id
            AND i.volume = '{volume}' AND i.number = '{number}'
        JOIN publication_settings ps_title ON p.publication_id = ps_title.publication_id
            AND ps_title.setting_name = 'title' AND ps_title.locale = 'en'
        LEFT JOIN publication_settings ps_pages ON p.publication_id = ps_pages.publication_id
            AND ps_pages.setting_name = 'pages'
        WHERE p.status = 3;
    """
    out = run_sql(target, sql)
    result = {}
    for line in out.strip().splitlines():
        parts = line.split('\t')
        if len(parts) >= 2:
            pub_id = int(parts[0])
            title = parts[1].strip()
            pages = parts[2] if len(parts) > 2 and parts[2] != 'NULL' else None
            norm = title.lower().strip()
            result[norm] = {'publication_id': pub_id, 'title': title, 'current_pages': pages}
    return result


def verify_pages(target: str, pub_id: int, expected: str) -> bool:
    """Verify a single publication's pages value after write."""
    sql = (
        f"SELECT setting_value FROM publication_settings "
        f"WHERE publication_id = {pub_id} AND setting_name = 'pages';"
    )
    out = run_sql(target, sql).strip()
    return out == expected


def load_jats_pages(toc_path: str) -> dict[str, str]:
    """
    Load page numbers from JATS files for all articles in an issue.
    Returns {normalised_title: 'fpage-lpage'}.
    """
    with open(toc_path) as f:
        data = json.load(f)

    articles = data.get('articles', [])
    desired = {}

    for art in articles:
        split_pdf = art.get('split_pdf')
        if not split_pdf:
            continue
        # split_pdf paths are relative to project root
        if not os.path.isabs(split_pdf):
            project_root = os.path.join(os.path.dirname(__file__), '..')
            split_pdf = os.path.normpath(os.path.join(project_root, split_pdf))
        jats_path = os.path.splitext(split_pdf)[0] + '.jats.xml'
        if not os.path.exists(jats_path):
            continue
        try:
            tree = ET.parse(jats_path)
            fpage_el = tree.find('.//{*}fpage')
            lpage_el = tree.find('.//{*}lpage')
            fpage = fpage_el.text.strip() if fpage_el is not None and fpage_el.text else None
            lpage = lpage_el.text.strip() if lpage_el is not None and lpage_el.text else None
        except ET.ParseError:
            continue
        if not fpage or not lpage:
            continue
        title_norm = art.get('title', '').lower().strip()
        desired[title_norm] = f'{fpage}-{lpage}'

    return desired


def process_issue(toc_path: str, target: str, dry_run: bool) -> dict:
    """Process a single issue. Returns summary dict."""
    vol_iss = os.path.basename(os.path.dirname(toc_path))

    with open(toc_path) as f:
        data = json.load(f)

    volume = str(data.get('volume', ''))
    number = str(data.get('issue', ''))
    articles = data.get('articles', [])

    if not articles:
        return {'issue': vol_iss, 'status': 'skip', 'reason': 'no articles'}

    # Build desired pages from JATS (single source of truth)
    desired = load_jats_pages(toc_path)

    if not desired:
        return {'issue': vol_iss, 'status': 'skip', 'reason': 'no page data in JATS files'}

    # Get current state from DB
    pubs = get_issue_publications(target, volume, number)

    if not pubs:
        return {'issue': vol_iss, 'status': 'skip',
                'reason': f'no publications found in DB for vol {volume} no {number}'}

    # Match and build SQL
    matched = 0
    unmatched = []
    already_set = 0
    sql_statements = []
    verify_list = []  # (pub_id, expected_pages) for post-write verification

    for title_norm, pages in desired.items():
        if title_norm in pubs:
            pub = pubs[title_norm]
            if pub['current_pages'] == pages:
                already_set += 1
                continue
            pub_id = pub['publication_id']
            escaped_pages = pages.replace("'", "\\'")
            if pub['current_pages'] is None:
                sql_statements.append(
                    f"INSERT INTO publication_settings "
                    f"(publication_id, locale, setting_name, setting_value) "
                    f"VALUES ({pub_id}, '', 'pages', '{escaped_pages}');"
                )
            else:
                sql_statements.append(
                    f"UPDATE publication_settings SET setting_value = '{escaped_pages}' "
                    f"WHERE publication_id = {pub_id} AND setting_name = 'pages';"
                )
            verify_list.append((pub_id, pages))
            matched += 1
        else:
            unmatched.append(title_norm[:60])

    if dry_run:
        return {
            'issue': vol_iss,
            'status': 'would_update',
            'matched': matched,
            'already_set': already_set,
            'unmatched': len(unmatched),
            'unmatched_titles': unmatched[:3] if unmatched else None,
            'sql': sql_statements,
        }

    # Execute with transaction
    if sql_statements:
        full_sql = 'START TRANSACTION;\n' + '\n'.join(sql_statements) + '\nCOMMIT;\n'
        try:
            run_sql(target, full_sql)
        except SqlError as e:
            return {'issue': vol_iss, 'status': 'error', 'reason': str(e)}

        # Verify a sample of writes (first, last, and middle)
        if verify_list:
            sample_indices = {0, len(verify_list) - 1}
            if len(verify_list) > 2:
                sample_indices.add(len(verify_list) // 2)
            verify_failures = 0
            for idx in sample_indices:
                pub_id, expected = verify_list[idx]
                try:
                    if not verify_pages(target, pub_id, expected):
                        verify_failures += 1
                        print(f'  VERIFY FAIL: pub_id={pub_id} expected="{expected}"',
                              file=sys.stderr)
                except SqlError:
                    verify_failures += 1
            if verify_failures > 0:
                return {'issue': vol_iss, 'status': 'error',
                        'reason': f'{verify_failures} verification(s) failed after write'}

    return {
        'issue': vol_iss,
        'status': 'updated',
        'matched': matched,
        'already_set': already_set,
        'unmatched': len(unmatched),
    }


def main():
    parser = argparse.ArgumentParser(description='Push page numbers from JATS to OJS database')
    parser.add_argument('--target', choices=['dev', 'live'], required=True,
                        help='Target environment')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show SQL without executing')
    parser.add_argument('--confirm', action='store_true',
                        help='Required for live execution (safety gate)')
    parser.add_argument('--issue', help='Process only this issue (e.g. 35.2)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    # Safety gate for live
    if args.target == 'live' and not args.dry_run and not args.confirm:
        print('ERROR: running against live requires --confirm flag.', file=sys.stderr)
        print('  Run with --dry-run first to preview changes.', file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print('=== DRY RUN (no changes will be made) ===\n')

    # Pre-flight: verify DB connectivity
    print(f'Connecting to {args.target}...')
    if not check_connectivity(args.target):
        sys.exit(1)
    print(f'Connected.\n')

    # Find toc.json files
    if args.issue:
        toc_files = [os.path.join(OUTPUT_DIR, args.issue, 'toc.json')]
    else:
        toc_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, '*/toc.json')))

    stats = {'updated': 0, 'would_update': 0, 'skip': 0, 'error': 0,
             'already': 0, 'unmatched_total': 0, 'matched_total': 0}

    for toc_path in toc_files:
        if not os.path.exists(toc_path):
            print(f'  ERROR: {toc_path} not found')
            stats['error'] += 1
            continue

        result = process_issue(toc_path, args.target, args.dry_run)
        status = result['status']

        if status == 'error':
            print(f"  ERROR {result['issue']}: {result['reason']}")
            stats['error'] += 1
        elif status == 'skip':
            if args.verbose:
                print(f"  skip  {result['issue']}: {result['reason']}")
            stats['skip'] += 1
        elif status in ('would_update', 'updated'):
            action = 'would update' if args.dry_run else 'updated'
            m = result['matched']
            a = result['already_set']
            u = result['unmatched']
            print(f"  {action} {result['issue']}: {m} to set, {a} already correct, {u} unmatched")
            stats['matched_total'] += m
            stats['already'] += a
            stats['unmatched_total'] += u
            if args.verbose and result.get('sql'):
                for s in result['sql'][:5]:
                    print(f"    {s}")
                if len(result.get('sql', [])) > 5:
                    print(f"    ... and {len(result['sql']) - 5} more")
            if result.get('unmatched_titles'):
                for t in result['unmatched_titles']:
                    print(f"    UNMATCHED: {t}")

            if status == 'updated':
                stats['updated'] += 1
            else:
                stats['would_update'] += 1

    # Summary
    print(f"\n{'=== DRY RUN SUMMARY ===' if args.dry_run else '=== SUMMARY ==='}")
    print(f"  Issues processed: {stats['updated'] + stats['would_update'] + stats['skip'] + stats['error']}")
    print(f"  {'Would update' if args.dry_run else 'Updated'}: {stats['updated'] or stats['would_update']} issues, "
          f"{stats['matched_total']} articles")
    print(f"  Already correct: {stats['already']} articles")
    print(f"  Unmatched: {stats['unmatched_total']} articles")
    if stats['error']:
        print(f"  ERRORS: {stats['error']}")
        sys.exit(1)


if __name__ == '__main__':
    main()
