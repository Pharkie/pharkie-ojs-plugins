#!/usr/bin/env python3
"""
Snapshot OJS submission IDs and DOIs into JATS and toc.json.

Queries the OJS database and writes:
- submission_id → JATS <article-id pub-id-type="publisher-id">
- DOI → JATS <article-id pub-id-type="doi">
- issue_id → toc.json issue_id
- issue DOI → toc.json issue_doi

JATS is the single source of truth. After a teardown/rebuild,
generate_xml.py reads these IDs from JATS and includes them in
import XML with advice="update", preserving URLs and DOIs.

Usage:
    # Snapshot from dev:
    python backfill/snapshot_ids.py --target dev

    # Snapshot from live:
    python backfill/snapshot_ids.py --target live

    # Single issue:
    python backfill/snapshot_ids.py --target dev --issue 35.2

    # Preview without writing:
    python backfill/snapshot_ids.py --target dev --dry-run
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
    """Query all published articles with their IDs and DOIs."""
    where_clause = ""
    if issue_filter:
        vol, num = issue_filter.split('.')
        where_clause = f"AND i.volume = '{vol}' AND i.number = '{num}'"

    sql = f"""
        SELECT sub.submission_id,
               ps_title.setting_value AS title,
               i.volume, i.number,
               IFNULL(d.doi, '') AS doi
        FROM publications p
        JOIN submissions sub ON p.submission_id = sub.submission_id
        JOIN issues i ON p.issue_id = i.issue_id
        JOIN publication_settings ps_title ON p.publication_id = ps_title.publication_id
            AND ps_title.setting_name = 'title' AND ps_title.locale = 'en'
        LEFT JOIN dois d ON p.doi_id = d.doi_id
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
        if len(parts) >= 4:
            articles.append({
                'title': parts[1].strip(),
                'volume': parts[2].strip(),
                'issue': parts[3].strip(),
                'submission_id': int(parts[0]),
                'doi': parts[4].strip() if len(parts) > 4 else '',
            })
    return articles


def snapshot_issues(target: str, issue_filter: str | None = None) -> list[dict]:
    """Query all issues with their IDs and DOIs."""
    where_clause = ""
    if issue_filter:
        vol, num = issue_filter.split('.')
        where_clause = f"AND i.volume = '{vol}' AND i.number = '{num}'"

    sql = f"""
        SELECT i.issue_id, i.volume, i.number,
               IFNULL(d.doi, '') AS doi
        FROM issues i
        LEFT JOIN dois d ON i.doi_id = d.doi_id
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
                'doi': parts[3].strip() if len(parts) > 3 else '',
            })
    return issues


def issue_dir_name(vol, iss):
    """Match the output directory naming convention."""
    v, i = int(vol), int(iss)
    if v <= 5 and i == 1:
        return str(v)
    return f'{v}.{i}'


def update_jats(jats_path, submission_id, doi, dry_run=False):
    """Update publisher-id and DOI in a JATS XML file."""
    try:
        tree = ET.parse(jats_path)
    except ET.ParseError:
        return False

    root = tree.getroot()
    # Find or create article-meta
    meta = root.find('.//{*}article-meta')
    if meta is None:
        return False

    changed = False

    # Update publisher-id
    pid_el = meta.find('{*}article-id[@pub-id-type="publisher-id"]')
    if pid_el is None:
        pid_el = meta.find('article-id[@pub-id-type="publisher-id"]')
    if pid_el is not None:
        if pid_el.text != str(submission_id):
            if not dry_run:
                pid_el.text = str(submission_id)
            changed = True
    # If no publisher-id element exists, we'd need to insert one — but
    # generate_jats.py should have created it. Skip if missing.

    # Update DOI
    if doi:
        doi_el = meta.find('{*}article-id[@pub-id-type="doi"]')
        if doi_el is None:
            doi_el = meta.find('article-id[@pub-id-type="doi"]')
        if doi_el is not None:
            if doi_el.text != doi:
                if not dry_run:
                    doi_el.text = doi
                changed = True

    if changed and not dry_run:
        tree.write(jats_path, xml_declaration=True, encoding='unicode')

    return changed


def normalize_title(title):
    """Simple title normalization for matching."""
    import re
    title = title.strip().lower()
    title = re.sub(r'[\u201c\u201d\u2018\u2019"\']', '', title)
    title = re.sub(r'\s+', ' ', title)
    return title.strip(' .,;:')


def main():
    parser = argparse.ArgumentParser(
        description='Snapshot OJS IDs and DOIs into JATS and toc.json')
    parser.add_argument('--target', choices=['dev', 'live'], required=True,
                        help='Target environment to snapshot from')
    parser.add_argument('--issue', help='Snapshot only this issue (e.g. 35.2)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without writing')
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

    print(f'Found {len(articles)} articles, {len(issues)} issues\n')

    # Group articles by (volume, issue)
    by_issue = {}
    for art in articles:
        key = (art['volume'], art['issue'])
        by_issue.setdefault(key, []).append(art)

    # Update JATS files
    jats_updated = 0
    jats_not_found = 0
    for (vol, iss), issue_articles in sorted(by_issue.items()):
        dirname = issue_dir_name(vol, iss)
        issue_dir = os.path.join(OUTPUT_DIR, dirname)
        if not os.path.isdir(issue_dir):
            print(f'  SKIP Vol {vol}.{iss}: no output dir')
            continue

        # Load toc.json to get split_pdf → JATS path mapping
        toc_path = os.path.join(issue_dir, 'toc.json')
        if not os.path.exists(toc_path):
            print(f'  SKIP Vol {vol}.{iss}: no toc.json')
            continue
        with open(toc_path) as f:
            toc = json.load(f)

        # Build title → JATS path lookup from toc.json
        title_to_jats = {}
        for toc_art in toc.get('articles', []):
            sp = toc_art.get('split_pdf', '')
            if sp:
                stem = os.path.splitext(os.path.basename(sp))[0]
                jats_path = os.path.join(issue_dir, f'{stem}.jats.xml')
                norm = normalize_title(toc_art.get('title', ''))
                title_to_jats[norm] = jats_path

        for art in issue_articles:
            norm = normalize_title(art['title'])
            jats_path = title_to_jats.get(norm)
            if not jats_path or not os.path.exists(jats_path):
                jats_not_found += 1
                if jats_not_found <= 5:
                    print(f'  MISS: {art["title"][:50]} (Vol {vol}.{iss})')
                continue

            changed = update_jats(jats_path, art['submission_id'], art['doi'],
                                  dry_run=args.dry_run)
            if changed:
                jats_updated += 1
                action = 'WOULD UPDATE' if args.dry_run else 'UPDATED'
                print(f'  {action}: {os.path.basename(jats_path)} '
                      f'(id={art["submission_id"]}, doi={art["doi"] or "none"})')

    # Update toc.json with issue IDs and DOIs
    toc_updated = 0
    for issue_data in issues:
        dirname = issue_dir_name(issue_data['volume'], issue_data['issue'])
        toc_path = os.path.join(OUTPUT_DIR, dirname, 'toc.json')
        if not os.path.exists(toc_path):
            continue
        with open(toc_path) as f:
            toc = json.load(f)

        changed = False
        if toc.get('issue_id') != issue_data['issue_id']:
            toc['issue_id'] = issue_data['issue_id']
            changed = True
        if issue_data['doi'] and toc.get('issue_doi') != issue_data['doi']:
            toc['issue_doi'] = issue_data['doi']
            changed = True

        if changed:
            toc_updated += 1
            if not args.dry_run:
                with open(toc_path, 'w') as f:
                    json.dump(toc, f, indent=2, ensure_ascii=False)
                    f.write('\n')
            action = 'WOULD UPDATE' if args.dry_run else 'UPDATED'
            print(f'  {action}: {dirname}/toc.json '
                  f'(issue_id={issue_data["issue_id"]}, doi={issue_data["doi"] or "none"})')

    print(f'\nJATS updated: {jats_updated}, not found: {jats_not_found}')
    print(f'toc.json updated: {toc_updated}')
    if args.dry_run:
        print('(dry run — no files changed)')


if __name__ == '__main__':
    main()
