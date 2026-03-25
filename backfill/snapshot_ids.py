#!/usr/bin/env python3
"""
Snapshot OJS submission/issue IDs for idempotent rebuild.

Queries the OJS database and saves the mapping of
{title, volume, issue} -> {submission_id, publication_id, issue_id}
to id-registry.json. After a teardown/rebuild, restore_ids.py uses
this to remap auto-assigned IDs back to the originals, preserving
URLs, DOIs, and payment records.

Run daily as part of backups. Store timestamped copies alongside DB backups.

Usage:
    # Snapshot dev:
    python backfill/snapshot_ids.py --target dev

    # Snapshot live:
    python backfill/snapshot_ids.py --target live

    # Single issue:
    python backfill/snapshot_ids.py --target dev --issue 35.2
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

BACKFILL_DIR = os.path.dirname(__file__)
REGISTRY_PATH = os.path.join(BACKFILL_DIR, 'private', 'id-registry.json')

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
    """Execute SQL against the target and return output."""
    cfg = TARGETS[target]
    proc = subprocess.run(
        cfg['cmd'],
        input=sql,
        capture_output=True,
        text=True,
    )
    stderr = proc.stderr.strip()
    stderr_lines = [l for l in stderr.splitlines()
                    if 'password on the command line' not in l]
    stderr_clean = '\n'.join(stderr_lines).strip()

    if proc.returncode != 0:
        raise SqlError(f'SQL failed (exit {proc.returncode}): {stderr_clean}')
    if stderr_clean:
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


def snapshot_articles(target: str, issue_filter: str | None = None) -> list[dict]:
    """Query all published articles with their IDs."""
    where_clause = ""
    if issue_filter:
        vol, num = issue_filter.split('.')
        where_clause = f"AND i.volume = '{vol}' AND i.number = '{num}'"

    sql = f"""
        SELECT sub.submission_id, p.publication_id,
               ps_title.setting_value AS title,
               i.volume, i.number
        FROM publications p
        JOIN submissions sub ON p.submission_id = sub.submission_id
        JOIN issues i ON p.issue_id = i.issue_id
        JOIN publication_settings ps_title ON p.publication_id = ps_title.publication_id
            AND ps_title.setting_name = 'title' AND ps_title.locale = 'en'
        WHERE sub.context_id = (SELECT journal_id FROM journals WHERE path = 'ea' LIMIT 1)
            AND p.status = 3
            {where_clause}
        ORDER BY i.volume+0, i.number+0, p.seq;
    """
    out = run_sql(target, sql)
    articles = []
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 5:
            articles.append({
                'title': parts[2].strip(),
                'volume': parts[3].strip(),
                'issue': parts[4].strip(),
                'submission_id': int(parts[0]),
                'publication_id': int(parts[1]),
            })
    return articles


def snapshot_issues(target: str, issue_filter: str | None = None) -> list[dict]:
    """Query all issues with their IDs."""
    where_clause = ""
    if issue_filter:
        vol, num = issue_filter.split('.')
        where_clause = f"AND i.volume = '{vol}' AND i.number = '{num}'"

    sql = f"""
        SELECT i.issue_id, i.volume, i.number
        FROM issues i
        WHERE i.journal_id = (SELECT journal_id FROM journals WHERE path = 'ea' LIMIT 1)
            {where_clause}
        ORDER BY i.volume+0, i.number+0;
    """
    out = run_sql(target, sql)
    issues = []
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 3:
            issues.append({
                'volume': parts[1].strip(),
                'issue': parts[2].strip(),
                'issue_id': int(parts[0]),
            })
    return issues


def main():
    parser = argparse.ArgumentParser(
        description='Snapshot OJS submission/issue IDs for idempotent rebuild')
    parser.add_argument('--target', choices=['dev', 'live'], required=True,
                        help='Target environment')
    parser.add_argument('--issue', help='Snapshot only this issue (e.g. 35.2)')
    parser.add_argument('-o', '--output', default=REGISTRY_PATH,
                        help=f'Output path (default: {REGISTRY_PATH})')
    args = parser.parse_args()

    print(f'Connecting to {args.target}...')
    if not check_connectivity(args.target):
        sys.exit(1)
    print('Connected.\n')

    articles = snapshot_articles(args.target, args.issue)
    issues = snapshot_issues(args.target, args.issue)

    if not articles and not issues:
        print('WARNING: No articles or issues found. Is the database empty?')
        sys.exit(1)

    registry = {
        '_comment': 'ID registry for idempotent rebuild. Generated by snapshot_ids.py.',
        '_source': args.target,
        '_generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'articles': articles,
        'issues': issues,
    }

    # If filtering a single issue and registry already exists, merge
    if args.issue and os.path.exists(args.output):
        with open(args.output) as f:
            existing = json.load(f)

        vol, num = args.issue.split('.')
        # Remove old entries for this issue, keep the rest
        existing['articles'] = [
            a for a in existing.get('articles', [])
            if not (a['volume'] == vol and a['issue'] == num)
        ]
        existing['issues'] = [
            i for i in existing.get('issues', [])
            if not (i['volume'] == vol and i['issue'] == num)
        ]
        # Add new entries
        existing['articles'].extend(articles)
        existing['issues'].extend(issues)
        existing['_generated'] = registry['_generated']
        existing['_source'] = registry['_source']
        registry = existing

    # Write atomically
    tmp_path = args.output + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.rename(tmp_path, args.output)

    print(f'Snapshot: {len(articles)} articles, {len(issues)} issues')
    print(f'Written to: {args.output}')


if __name__ == '__main__':
    main()
