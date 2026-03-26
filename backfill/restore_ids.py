#!/usr/bin/env python3
"""
Restore OJS submission/issue IDs after a rebuild.

After a fresh import (which assigns new auto-increment IDs), this script
remaps submission_id and issue_id back to the original values stored in
JATS (publisher-id) and toc.json (issue_id). This preserves URLs, DOIs,
and payment records.

Normally generate_xml.py includes IDs with advice="update" so OJS
preserves them on import. This script is a safety net for when that
doesn't work or when IDs need correcting after the fact.

Uses a two-pass remap to avoid primary key collisions:
  Pass 1: remap all IDs to temporary high values (original + offset)
  Pass 2: remap from temporary to final original values

Usage:
    # Preview SQL without executing:
    python backfill/restore_ids.py --dry-run --target dev

    # Execute against dev:
    python backfill/restore_ids.py --target dev

    # Execute against live (requires --confirm):
    python backfill/restore_ids.py --target live --confirm

    # Single issue:
    python backfill/restore_ids.py --target dev --issue 35.2
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

# Temporary ID offset for two-pass remap (avoids PK collisions)
TEMP_OFFSET = 10_000_000

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


def get_current_articles(target: str, volume: str, number: str) -> dict[str, list[dict]]:
    """Get current articles for an issue, keyed by normalised title.

    Returns {title: [list of articles]} to handle duplicate titles within
    an issue. Each article includes first_author for disambiguation.
    """
    sql = f"""
        SELECT sub.submission_id, p.publication_id,
               ps_title.setting_value AS title,
               IFNULL(
                   (SELECT CONCAT(
                       IFNULL(aset_g.setting_value, ''), ' ',
                       IFNULL(aset_f.setting_value, ''))
                    FROM authors a
                    LEFT JOIN author_settings aset_g ON a.author_id = aset_g.author_id
                        AND aset_g.setting_name = 'givenname' AND aset_g.locale = 'en'
                    LEFT JOIN author_settings aset_f ON a.author_id = aset_f.author_id
                        AND aset_f.setting_name = 'familyname' AND aset_f.locale = 'en'
                    WHERE a.publication_id = p.publication_id
                    ORDER BY a.seq LIMIT 1),
                   '') AS first_author
        FROM publications p
        JOIN submissions sub ON p.submission_id = sub.submission_id
        JOIN issues i ON p.issue_id = i.issue_id
            AND i.volume = '{volume}' AND i.number = '{number}'
        JOIN publication_settings ps_title ON p.publication_id = ps_title.publication_id
            AND ps_title.setting_name = 'title' AND ps_title.locale = 'en'
        WHERE p.status = 3
        ORDER BY p.seq;
    """
    out = run_sql(target, sql)
    result: dict[str, list[dict]] = {}
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 3:
            norm = parts[2].strip().lower().strip()
            first_author = parts[3].strip() if len(parts) > 3 else ''
            entry = {
                'submission_id': int(parts[0]),
                'publication_id': int(parts[1]),
                'title': parts[2].strip(),
                'first_author': first_author,
            }
            result.setdefault(norm, []).append(entry)
    return result


def get_current_issue(target: str, volume: str, number: str) -> int | None:
    """Get current issue_id for a volume.number."""
    sql = f"""
        SELECT issue_id FROM issues
        WHERE journal_id = (SELECT journal_id FROM journals WHERE path = 'ea' LIMIT 1)
            AND volume = '{volume}' AND number = '{number}'
        LIMIT 1;
    """
    out = run_sql(target, sql).strip()
    if out:
        return int(out.split('\t')[0])
    return None


def build_submission_remap_sql(old_id: int, new_id: int) -> list[str]:
    """Build SQL to remap a submission_id from new_id to old_id."""
    if old_id == new_id:
        return []
    return [
        f"UPDATE submissions SET submission_id = {old_id} WHERE submission_id = {new_id};",
        f"UPDATE publications SET submission_id = {old_id} WHERE submission_id = {new_id};",
        f"UPDATE submission_files SET submission_id = {old_id} WHERE submission_id = {new_id};",
        f"UPDATE submission_settings SET submission_id = {old_id} WHERE submission_id = {new_id};",
        f"UPDATE submission_search_objects SET submission_id = {old_id} WHERE submission_id = {new_id};",
    ]


def build_issue_remap_sql(old_id: int, new_id: int) -> list[str]:
    """Build SQL to remap an issue_id from new_id to old_id."""
    if old_id == new_id:
        return []
    return [
        f"UPDATE issues SET issue_id = {old_id} WHERE issue_id = {new_id};",
        f"UPDATE issue_settings SET issue_id = {old_id} WHERE issue_id = {new_id};",
        f"UPDATE issue_galleys SET issue_id = {old_id} WHERE issue_id = {new_id};",
        f"UPDATE publications SET issue_id = {old_id} WHERE issue_id = {new_id};",
        f"UPDATE custom_issue_orders SET issue_id = {old_id} WHERE issue_id = {new_id};",
        f"UPDATE journals SET current_issue_id = {old_id} WHERE current_issue_id = {new_id};",
    ]


def main():
    parser = argparse.ArgumentParser(
        description='Restore OJS submission/issue IDs after rebuild')
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

    print(f'Connecting to {args.target}...')
    if not check_connectivity(args.target):
        sys.exit(1)
    print('Connected.\n')

    # Load IDs from JATS and toc.json (single source of truth)
    reg_articles = []
    reg_issues = []
    for toc_path in sorted(glob.glob(os.path.join(OUTPUT_DIR, '*/toc.json'))):
        with open(toc_path) as f:
            toc = json.load(f)
        vol = str(toc['volume'])
        iss = str(toc['issue'])

        if args.issue:
            fv, fn = args.issue.split('.')
            if vol != fv or iss != fn:
                continue

        # Issue ID from toc.json
        if toc.get('issue_id'):
            reg_issues.append({
                'volume': vol, 'issue': iss,
                'issue_id': toc['issue_id'],
            })

        # Article IDs from JATS publisher-id
        for art in toc.get('articles', []):
            sp = art.get('split_pdf', '')
            if not sp:
                continue
            jats_name = os.path.splitext(os.path.basename(sp))[0] + '.jats.xml'
            jats_path = os.path.join(os.path.dirname(toc_path), jats_name)
            if not os.path.exists(jats_path):
                continue
            try:
                tree = ET.parse(jats_path)
                pid_el = tree.find('.//{*}article-id[@pub-id-type="publisher-id"]')
                if pid_el is not None and pid_el.text:
                    reg_articles.append({
                        'title': art.get('title', ''),
                        'first_author': art.get('authors', '').split('&')[0].split(',')[0].strip() if art.get('authors') else '',
                        'volume': vol, 'issue': iss,
                        'submission_id': int(pid_el.text.strip()),
                    })
            except (ET.ParseError, ValueError):
                continue

    print(f'Loaded from JATS/toc.json: {len(reg_articles)} articles, {len(reg_issues)} issues')

    # Group articles by issue
    issues_seen = {}
    for art in reg_articles:
        key = (art['volume'], art['issue'])
        issues_seen.setdefault(key, []).append(art)

    # Build remap plan
    submission_remaps = []  # (old_id, new_id)
    issue_remaps = []  # (old_id, new_id)
    matched = 0
    already_correct = 0
    unmatched = []

    # Process issues
    for reg_iss in reg_issues:
        vol, num = reg_iss['volume'], reg_iss['issue']
        old_issue_id = reg_iss['issue_id']
        new_issue_id = get_current_issue(args.target, vol, num)
        if new_issue_id is None:
            unmatched.append(f'issue {vol}.{num}')
            continue
        if new_issue_id != old_issue_id:
            issue_remaps.append((old_issue_id, new_issue_id))
        else:
            already_correct += 1

    # Process articles
    for (vol, num), arts in issues_seen.items():
        current = get_current_articles(args.target, vol, num)
        if not current:
            for art in arts:
                unmatched.append(f'{vol}.{num}: {art["title"][:50]}')
            continue

        for art in arts:
            title_norm = art['title'].lower().strip()
            old_sub_id = art['submission_id']
            reg_author = art.get('first_author', '').lower().strip()

            if title_norm not in current:
                unmatched.append(f'{vol}.{num}: {art["title"][:50]}')
                continue

            cur_group = current[title_norm]
            if len(cur_group) == 1:
                # Unique title — straightforward match
                match = cur_group[0]
            else:
                # Duplicate title — disambiguate by first author
                match = None
                for candidate in cur_group:
                    if candidate.get('_matched'):
                        continue
                    cur_author = candidate.get('first_author', '').lower().strip()
                    if cur_author == reg_author:
                        match = candidate
                        break
                if match is None:
                    # Author didn't match; fall back to first unmatched
                    for candidate in cur_group:
                        if not candidate.get('_matched'):
                            match = candidate
                            break

            if match is None:
                unmatched.append(f'{vol}.{num}: {art["title"][:50]} (all dups consumed)')
                continue

            match['_matched'] = True
            new_sub_id = match['submission_id']
            if new_sub_id != old_sub_id:
                submission_remaps.append((old_sub_id, new_sub_id))
                matched += 1
            else:
                already_correct += 1
                matched += 1

    # Check for collisions: would an old_id clash with another entry's new_id?
    old_sub_ids = {old for old, _ in submission_remaps}
    new_sub_ids = {new for _, new in submission_remaps}
    old_iss_ids = {old for old, _ in issue_remaps}
    new_iss_ids = {new for _, new in issue_remaps}

    sub_collisions = old_sub_ids & new_sub_ids
    iss_collisions = old_iss_ids & new_iss_ids
    needs_two_pass = bool(sub_collisions or iss_collisions)

    # Summary
    total_remaps = len(submission_remaps) + len(issue_remaps)
    print(f'Registry: {len(reg_articles)} articles, {len(reg_issues)} issues')
    print(f'Matched: {matched} articles, {len(issue_remaps) + already_correct} issues')
    print(f'Need remapping: {len(submission_remaps)} submissions, {len(issue_remaps)} issues')
    print(f'Already correct: {already_correct}')
    if unmatched:
        print(f'UNMATCHED: {len(unmatched)}')
        for u in unmatched[:5]:
            print(f'  - {u}')
        if len(unmatched) > 5:
            print(f'  ... and {len(unmatched) - 5} more')
    if needs_two_pass:
        print(f'Two-pass remap needed (collision avoidance): '
              f'{len(sub_collisions)} submission, {len(iss_collisions)} issue collisions')

    if total_remaps == 0:
        print('\nNothing to remap. All IDs already match.')
        return

    # Abort if too many unmatched
    total_expected = len(reg_articles)
    if total_expected > 0 and len(unmatched) / total_expected > 0.2:
        print(f'\nERROR: {len(unmatched)}/{total_expected} articles unmatched (>20%). '
              f'Import may be incomplete. Aborting.', file=sys.stderr)
        sys.exit(1)

    # Build SQL
    sql_parts = ['SET FOREIGN_KEY_CHECKS=0;']

    if needs_two_pass:
        # Pass 1: remap to temporary IDs
        for old_id, new_id in submission_remaps:
            temp_id = new_id + TEMP_OFFSET
            sql_parts.extend(build_submission_remap_sql(temp_id, new_id))
        for old_id, new_id in issue_remaps:
            temp_id = new_id + TEMP_OFFSET
            sql_parts.extend(build_issue_remap_sql(temp_id, new_id))
        # Pass 2: remap from temporary to final
        for old_id, new_id in submission_remaps:
            temp_id = new_id + TEMP_OFFSET
            sql_parts.extend(build_submission_remap_sql(old_id, temp_id))
        for old_id, new_id in issue_remaps:
            temp_id = new_id + TEMP_OFFSET
            sql_parts.extend(build_issue_remap_sql(old_id, temp_id))
    else:
        for old_id, new_id in submission_remaps:
            sql_parts.extend(build_submission_remap_sql(old_id, new_id))
        for old_id, new_id in issue_remaps:
            sql_parts.extend(build_issue_remap_sql(old_id, new_id))

    # Reset auto-increment
    if submission_remaps:
        max_sub = max(old for old, _ in submission_remaps)
        sql_parts.append(f'ALTER TABLE submissions AUTO_INCREMENT = {max_sub + 1};')
    if issue_remaps:
        max_iss = max(old for old, _ in issue_remaps)
        sql_parts.append(f'ALTER TABLE issues AUTO_INCREMENT = {max_iss + 1};')

    sql_parts.append('SET FOREIGN_KEY_CHECKS=1;')

    full_sql = '\n'.join(sql_parts)

    if args.dry_run:
        if args.verbose:
            print(f'\n--- SQL ({len(sql_parts)} statements) ---')
            print(full_sql)
        else:
            print(f'\n{len(sql_parts)} SQL statements would be executed.')
            print('Use --verbose to see full SQL.')
        print('\n=== DRY RUN COMPLETE ===')
        return

    # Execute
    print(f'\nExecuting {len(sql_parts)} SQL statements...')
    try:
        run_sql(args.target, full_sql)
    except SqlError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)

    # Verify: spot-check a few remapped submissions
    verify_ok = 0
    verify_fail = 0
    sample = submission_remaps[:3] + submission_remaps[-1:]
    for old_id, _ in sample:
        try:
            out = run_sql(args.target,
                          f'SELECT submission_id FROM submissions '
                          f'WHERE submission_id = {old_id};')
            if str(old_id) in out:
                verify_ok += 1
            else:
                verify_fail += 1
                print(f'  VERIFY FAIL: submission_id {old_id} not found after remap',
                      file=sys.stderr)
        except SqlError:
            verify_fail += 1

    print(f'\nDone. Remapped {len(submission_remaps)} submissions, {len(issue_remaps)} issues.')
    if verify_fail:
        print(f'WARNING: {verify_fail} verification(s) failed!', file=sys.stderr)
        sys.exit(1)
    else:
        print(f'Verified {verify_ok} remapped submissions.')


if __name__ == '__main__':
    main()
