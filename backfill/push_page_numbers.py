#!/usr/bin/env python3
"""
Push journal page numbers from toc.json to a live (or dev) OJS database.

Page numbers are stored in publication_settings (setting_name='pages').
Articles are matched by title within the correct issue (volume + number).

Usage:
    # Preview SQL without executing:
    python backfill/push_page_numbers.py --dry-run

    # Execute against dev:
    python backfill/push_page_numbers.py --target dev

    # Execute against live:
    python backfill/push_page_numbers.py --target live

    # Single issue:
    python backfill/push_page_numbers.py --target dev --issue 35.2
"""

import argparse
import glob
import json
import os
import subprocess
import sys

BACKFILL_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(BACKFILL_DIR, 'output')

# How to run mysql for each target
TARGETS = {
    'dev': {
        'cmd': [
            'docker', 'compose', 'exec', '-T', 'ojs-db',
            'bash', '-c',
            'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N',
        ],
        'cwd': None,  # use current dir
    },
    'live': {
        'cmd': [
            'ssh', 'sea-live',
            'cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs-db '
            'bash -c \'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N\'',
        ],
        'cwd': None,
    },
}


def run_sql(target: str, sql: str) -> str:
    """Execute SQL against the target and return output."""
    cfg = TARGETS[target]
    proc = subprocess.run(
        cfg['cmd'],
        input=sql,
        capture_output=True,
        text=True,
        cwd=cfg.get('cwd'),
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        # Filter out mysql password warnings
        stderr = '\n'.join(l for l in stderr.splitlines()
                          if 'password on the command line' not in l)
        if stderr:
            print(f'  SQL warning: {stderr}', file=sys.stderr)
    return proc.stdout


def get_issue_publications(target: str, volume: str, number: str) -> dict[str, dict]:
    """
    Get all publications for an issue, keyed by normalised title.
    Returns {title: {publication_id, current_pages}}.
    """
    sql = f"""
        SELECT p.publication_id,
               ps_title.setting_value AS title,
               ps_pages.setting_value AS pages
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
            # Normalise: lowercase, strip whitespace and common punctuation diffs
            norm = title.lower().strip()
            result[norm] = {'publication_id': pub_id, 'title': title, 'current_pages': pages}
    return result


def process_issue(toc_path: str, target: str | None, dry_run: bool) -> dict:
    """Process a single issue. Returns summary."""
    vol_iss = os.path.basename(os.path.dirname(toc_path))

    with open(toc_path) as f:
        data = json.load(f)

    volume = str(data.get('volume', ''))
    number = str(data.get('issue', ''))
    articles = data.get('articles', [])

    if not articles:
        return {'issue': vol_iss, 'status': 'skip', 'reason': 'no articles'}

    # Check toc.json has page numbers
    if not any('journal_page_start' in a for a in articles):
        return {'issue': vol_iss, 'status': 'skip', 'reason': 'no journal_page_start in toc.json'}

    # Build desired pages from toc.json
    desired = {}
    for art in articles:
        jp_start = art.get('journal_page_start')
        jp_end = art.get('journal_page_end')
        if jp_start is None or jp_end is None:
            continue
        title_norm = art.get('title', '').lower().strip()
        desired[title_norm] = f'{jp_start}-{jp_end}'

    if not desired:
        return {'issue': vol_iss, 'status': 'skip', 'reason': 'no page data'}

    if dry_run and not target:
        # Pure dry-run: just show what we'd set
        return {
            'issue': vol_iss,
            'status': 'preview',
            'articles': len(desired),
            'sample': list(desired.items())[:3],
        }

    # Get current state from DB
    pubs = get_issue_publications(target, volume, number)

    if not pubs:
        return {'issue': vol_iss, 'status': 'skip', 'reason': f'no publications found in DB for vol {volume} no {number}'}

    # Match and build SQL
    matched = 0
    unmatched = []
    already_set = 0
    sql_statements = []

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
                    f"INSERT INTO publication_settings (publication_id, locale, setting_name, setting_value) "
                    f"VALUES ({pub_id}, '', 'pages', '{escaped_pages}');"
                )
            else:
                sql_statements.append(
                    f"UPDATE publication_settings SET setting_value = '{escaped_pages}' "
                    f"WHERE publication_id = {pub_id} AND setting_name = 'pages';"
                )
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

    # Execute
    if sql_statements:
        full_sql = 'START TRANSACTION;\n' + '\n'.join(sql_statements) + '\nCOMMIT;\n'
        run_sql(target, full_sql)

    return {
        'issue': vol_iss,
        'status': 'updated',
        'matched': matched,
        'already_set': already_set,
        'unmatched': len(unmatched),
    }


def main():
    parser = argparse.ArgumentParser(description='Push page numbers to OJS database')
    parser.add_argument('--target', choices=['dev', 'live'], help='Target environment')
    parser.add_argument('--dry-run', action='store_true', help='Show SQL without executing')
    parser.add_argument('--issue', help='Process only this issue (e.g. 35.2)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    if not args.dry_run and not args.target:
        print('ERROR: specify --target (dev or live) or use --dry-run', file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print('=== DRY RUN ===\n')

    # Find toc.json files
    if args.issue:
        toc_files = [os.path.join(OUTPUT_DIR, args.issue, 'toc.json')]
    else:
        toc_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, '*/toc.json')))

    stats = {'updated': 0, 'would_update': 0, 'skip': 0, 'already': 0, 'unmatched_total': 0}

    for toc_path in toc_files:
        if not os.path.exists(toc_path):
            print(f'  ERROR: {toc_path} not found')
            continue

        result = process_issue(toc_path, args.target, args.dry_run)
        status = result['status']

        if status == 'skip':
            if args.verbose:
                print(f"  skip  {result['issue']}: {result['reason']}")
        elif status == 'preview':
            print(f"  {result['issue']}: {result['articles']} articles with page data")
            if args.verbose:
                for t, p in result['sample']:
                    print(f"    {p}  {t[:60]}")
        elif status in ('would_update', 'updated'):
            action = 'would update' if args.dry_run else 'updated'
            m = result['matched']
            a = result['already_set']
            u = result['unmatched']
            print(f"  {action} {result['issue']}: {m} to set, {a} already correct, {u} unmatched")
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

    print(f"\nDone. {stats.get('unmatched_total', 0)} total unmatched articles.")


if __name__ == '__main__':
    main()
