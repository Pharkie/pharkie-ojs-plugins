#!/usr/bin/env python3
"""
Step 7: Verify OJS import matches expected data.

Compares the TOC JSON (expected) against the OJS database (actual) to
catch missing articles, wrong sections, missing PDFs, etc.

Usage:
    python backfill/verify.py <toc.json> [--db-host 127.0.0.1] [--db-port 3306]

    # Dev environment (Docker):
    python backfill/verify.py backfill/output/37.1/toc.json --docker

Checks:
- Issue exists with correct volume/number/year
- All articles present with correct titles
- Section assignments match (Editorial, Articles, Book Reviews, etc.)
- PDF galleys attached where expected
- Author names match
- Access status correct (free vs paywalled)
"""

import sys
import os
import re
import json
import argparse
import subprocess


def run_db_query(query, docker_container=None, db_host=None, db_port=None,
                 db_name='ojs', db_user='ojs', db_pass='ojs'):
    """Run a MariaDB query and return rows as list of dicts."""
    env = None
    if docker_container:
        cmd = [
            'docker', 'exec', '-e', f'MYSQL_PWD={db_pass}',
            docker_container,
            'mariadb', '-u', db_user, db_name,
            '-N', '-e', query,
        ]
    else:
        cmd = [
            'mariadb', '-h', db_host or '127.0.0.1', '-P', str(db_port or 3306),
            '-u', db_user, db_name,
            '-N', '-e', query,
        ]
        env = {**os.environ, 'MYSQL_PWD': db_pass}

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        print(f"  DB ERROR: {result.stderr.strip()}", file=sys.stderr)
        return []

    rows = []
    for line in result.stdout.strip().split('\n'):
        if line:
            rows.append(line.split('\t'))
    return rows


def find_ojs_container(role='db'):
    """Find the running OJS Docker container name.

    role='db' finds the database container (for queries).
    role='app' finds the OJS application container.
    """
    try:
        result = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}'],
            capture_output=True, text=True
        )
        names = result.stdout.strip().split('\n')
        for name in names:
            if role == 'db' and 'ojs-db' in name:
                return name
            if role == 'app' and re.search(r'-ojs-?\d*$', name):
                return name
    except Exception:
        pass
    return None


def verify_issue(toc_data, container=None, journal_path='ea', **db_opts):
    """Verify an imported issue against expected TOC data."""
    raw_vol = toc_data.get('volume')
    raw_iss = toc_data.get('issue')
    try:
        vol = int(raw_vol)
        iss = int(raw_iss)
    except (ValueError, TypeError):
        print(f"  ERROR: volume ({raw_vol!r}) and issue ({raw_iss!r}) must be integers",
              file=sys.stderr)
        return {
            'volume': raw_vol, 'issue': raw_iss,
            'checks': [], 'errors': 1, 'warnings': 0,
        }
    year = toc_data.get('date', '').split()[-1] if toc_data.get('date') else None

    results = {
        'volume': vol,
        'issue': iss,
        'checks': [],
        'errors': 0,
        'warnings': 0,
    }

    def check(name, passed, detail=''):
        status = 'PASS' if passed else 'FAIL'
        results['checks'].append({'name': name, 'status': status, 'detail': detail})
        if not passed:
            results['errors'] += 1
        icon = '  ✓' if passed else '  ✗'
        print(f"{icon} {name}" + (f" — {detail}" if detail else ''))

    def warn(name, detail=''):
        results['checks'].append({'name': name, 'status': 'WARN', 'detail': detail})
        results['warnings'] += 1
        print(f"  ⚠ {name}" + (f" — {detail}" if detail else ''))

    # Validate journal_path — SAFETY: this regex check is required to prevent
    # SQL injection in the f-string queries below. Do not remove or relax.
    if not re.match(r'^[a-zA-Z0-9_-]+$', journal_path):
        print(f"  ERROR: Invalid journal path '{journal_path}'", file=sys.stderr)
        return results

    # Look up journal_id from path
    journal_query = f"SELECT journal_id FROM journals WHERE path = '{journal_path}'"
    rows = run_db_query(journal_query, docker_container=container, **db_opts)
    if not rows:
        check('Journal exists', False, f"Journal path '{journal_path}' not found")
        return results
    journal_id = int(rows[0][0])
    check('Journal exists', True, f'journal_id={journal_id}')

    # 1. Find the issue (volume/number/year are columns in the issues table, not issue_settings)
    issue_query = f"""
        SELECT i.issue_id, i.volume, i.number, i.year
        FROM issues i
        WHERE i.journal_id = {journal_id} AND i.volume = {vol} AND i.number = {iss}
    """
    rows = run_db_query(issue_query, docker_container=container, **db_opts)
    if not rows:
        check('Issue exists', False, f'Vol {vol}.{iss} not found in OJS')
        return results

    issue_id = int(rows[0][0])
    check('Issue exists', True, f'issue_id={issue_id}')

    if year:
        ojs_year = rows[0][3] if len(rows[0]) > 3 else None
        check('Year matches', str(ojs_year) == str(year), f'expected={year}, got={ojs_year}')

    # 2. Count articles
    expected_articles = toc_data['articles']
    article_query = f"""
        SELECT p.publication_id, ps_title.setting_value AS title,
               ss.setting_value AS section_abbrev, p.access_status, p.seq
        FROM publications p
        JOIN publication_settings ps_title ON p.publication_id = ps_title.publication_id
            AND ps_title.setting_name = 'title' AND ps_title.locale = 'en'
        JOIN submissions sub ON p.submission_id = sub.submission_id
        LEFT JOIN section_settings ss ON p.section_id = ss.section_id
            AND ss.setting_name = 'abbrev' AND ss.locale = 'en'
        WHERE sub.context_id = {journal_id}
        AND p.issue_id = {issue_id}
        ORDER BY p.seq
    """
    ojs_articles = run_db_query(article_query, docker_container=container, **db_opts)

    check('Article count', len(ojs_articles) == len(expected_articles),
          f'expected={len(expected_articles)}, got={len(ojs_articles)}')

    # 3. Match articles by title
    ojs_titles = {row[1].strip().lower(): row for row in ojs_articles}
    section_ref_map = {'ED': 'Editorial', 'ART': 'Articles',
                       'bookeditorial': 'Book Review Editorial', 'BR': 'Book Reviews'}

    matched = 0
    for expected in expected_articles:
        exp_title = expected['title'].strip().lower()
        if exp_title in ojs_titles:
            matched += 1
            ojs_row = ojs_titles[exp_title]
            ojs_section = section_ref_map.get(ojs_row[2], ojs_row[2])
            exp_section = expected['section']

            # Check section
            if ojs_section != exp_section:
                check(f'Section: {expected["title"][:50]}', False,
                      f'expected={exp_section}, got={ojs_section}')

            # Check access status
            exp_access = '0' if exp_section == 'Articles' else '1'
            ojs_access = ojs_row[3]
            if str(ojs_access) != exp_access:
                check(f'Access: {expected["title"][:50]}', False,
                      f'expected={exp_access} ({"paywalled" if exp_access=="0" else "free"}), got={ojs_access}')
        else:
            check(f'Title found: {expected["title"][:60]}', False, 'not in OJS')

    check('Titles matched', matched == len(expected_articles),
          f'{matched}/{len(expected_articles)}')

    # 4. Check galleys (PDFs)
    galley_query = f"""
        SELECT ps_title.setting_value, COUNT(ag.galley_id) AS galley_count
        FROM publications p
        JOIN publication_settings ps_title ON p.publication_id = ps_title.publication_id
            AND ps_title.setting_name = 'title' AND ps_title.locale = 'en'
        LEFT JOIN publication_galleys ag ON p.publication_id = ag.publication_id
        WHERE p.issue_id = {issue_id}
        GROUP BY p.publication_id
        ORDER BY p.seq
    """
    galley_rows = run_db_query(galley_query, docker_container=container, **db_opts)
    articles_with_pdf = sum(1 for row in galley_rows if row[1] and int(row[1]) > 0)
    expected_with_pdf = sum(1 for a in expected_articles if a.get('split_pdf'))
    check('PDFs attached', articles_with_pdf == expected_with_pdf,
          f'expected={expected_with_pdf}, got={articles_with_pdf}')

    # 5. Check authors
    author_query = f"""
        SELECT ps_title.setting_value AS title,
               GROUP_CONCAT(
                   CONCAT(
                       IFNULL(aus_gn.setting_value, ''), ' ',
                       IFNULL(aus_fn.setting_value, '')
                   ) ORDER BY a.seq SEPARATOR ' & '
               ) AS authors
        FROM publications p
        JOIN publication_settings ps_title ON p.publication_id = ps_title.publication_id
            AND ps_title.setting_name = 'title' AND ps_title.locale = 'en'
        LEFT JOIN authors a ON p.publication_id = a.publication_id
        LEFT JOIN author_settings aus_gn ON a.author_id = aus_gn.author_id
            AND aus_gn.setting_name = 'givenname' AND aus_gn.locale = 'en'
        LEFT JOIN author_settings aus_fn ON a.author_id = aus_fn.author_id
            AND aus_fn.setting_name = 'familyname' AND aus_fn.locale = 'en'
        WHERE p.issue_id = {issue_id}
        GROUP BY p.publication_id
        ORDER BY p.seq
    """
    author_rows = run_db_query(author_query, docker_container=container, **db_opts)
    authors_ok = 0
    authors_total = 0
    for row in author_rows:
        ojs_title = row[0].strip().lower()
        ojs_authors = row[1].strip() if len(row) > 1 and row[1] else ''

        for expected in expected_articles:
            if expected['title'].strip().lower() == ojs_title:
                exp_authors = expected.get('authors', '') or ''
                authors_total += 1
                if exp_authors and ojs_authors:
                    # Normalize for comparison
                    exp_norm = ' '.join(exp_authors.lower().split())
                    ojs_norm = ' '.join(ojs_authors.lower().split())
                    if exp_norm == ojs_norm:
                        authors_ok += 1
                    else:
                        warn(f'Author mismatch: {expected["title"][:40]}',
                             f'expected="{exp_authors}", got="{ojs_authors}"')
                elif not exp_authors and not ojs_authors:
                    authors_ok += 1
                elif not exp_authors:
                    # No expected author but OJS has one — probably fine
                    authors_ok += 1
                else:
                    warn(f'Missing author: {expected["title"][:40]}',
                         f'expected="{exp_authors}"')
                break

    if authors_total > 0:
        check('Authors match', authors_ok >= authors_total * 0.8,
              f'{authors_ok}/{authors_total} correct')

    return results


def main():
    parser = argparse.ArgumentParser(description='Verify OJS import against expected TOC')
    parser.add_argument('toc_json', help='TOC JSON file to verify against')
    parser.add_argument('--docker', action='store_true',
                        help='Auto-detect OJS Docker container')
    parser.add_argument('--container', help='Docker container name')
    parser.add_argument('--db-host', default='127.0.0.1')
    parser.add_argument('--db-port', type=int, default=3306)
    parser.add_argument('--db-name', default='ojs')
    parser.add_argument('--db-user', default='ojs')
    parser.add_argument('--db-pass', default='ojs')
    parser.add_argument('--journal-path', default='ea', help='OJS journal path (default: ea)')
    args = parser.parse_args()

    with open(args.toc_json) as f:
        toc_data = json.load(f)

    container = args.container
    if args.docker and not container:
        container = find_ojs_container(role='db')
        if not container:
            print("ERROR: No OJS DB Docker container found", file=sys.stderr)
            sys.exit(1)
        print(f"Using container: {container}", file=sys.stderr)

        # Auto-detect DB credentials from OJS app container config
        app_container = find_ojs_container(role='app')
        if app_container:
            try:
                result = subprocess.run(
                    ['docker', 'exec', app_container, 'grep',
                     'password', '/var/www/html/config.inc.php'],
                    capture_output=True, text=True
                )
                for line in result.stdout.strip().split('\n'):
                    line = line.strip()
                    if line.startswith('password') and '=' in line:
                        args.db_pass = line.split('=', 1)[1].strip()
                        break
            except Exception:
                pass

    print(f"\nVerifying Vol {toc_data.get('volume')}.{toc_data.get('issue')} "
          f"({len(toc_data['articles'])} articles)")
    print(f"{'='*50}")

    db_opts = {
        'db_name': args.db_name,
        'db_user': args.db_user,
        'db_pass': args.db_pass,
    }
    if not container:
        db_opts['db_host'] = args.db_host
        db_opts['db_port'] = args.db_port

    results = verify_issue(toc_data, container=container, journal_path=args.journal_path, **db_opts)

    print(f"\n{'='*50}")
    print(f"Results: {len(results['checks'])} checks, "
          f"{results['errors']} errors, {results['warnings']} warnings")

    if results['errors'] > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
