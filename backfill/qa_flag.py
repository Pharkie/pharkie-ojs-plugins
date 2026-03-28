#!/usr/bin/env python3
"""
Flag an article as a QA problem case.

Inserts a 'rejected' review into the OJS qa_split_reviews table so the
article appears in the QA Splits plugin's "Next Problem" navigation.

Looks up the submission_id from the JATS publisher-id, so you only need
the backfill path (vol.iss/seq-slug).

Usage:
    # Flag an article on dev:
    python backfill/qa_flag.py 29.2/03-on-the-phenomenon "references mixed with notes"

    # Flag on live:
    python backfill/qa_flag.py --target live 29.2/03-on-the-phenomenon "bad split page 5"

    # List current flags:
    python backfill/qa_flag.py --list

    # List flags on live:
    python backfill/qa_flag.py --list --target live

    # Clear a flag (remove all rejected reviews for an article):
    python backfill/qa_flag.py --clear 29.2/03-on-the-phenomenon
"""

import argparse
import glob
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
            "bash -c 'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N'",
        ],
    },
}


def run_sql(target: str, sql: str) -> str:
    """Execute SQL against the target and return output."""
    proc = subprocess.run(
        TARGETS[target]['cmd'],
        input=sql,
        capture_output=True,
        text=True,
    )
    stderr = proc.stderr.strip()
    stderr_lines = [l for l in stderr.splitlines()
                    if 'password on the command line' not in l]
    stderr_clean = '\n'.join(stderr_lines).strip()

    if proc.returncode != 0:
        print(f'ERROR: SQL failed (exit {proc.returncode}): {stderr_clean}',
              file=sys.stderr)
        sys.exit(1)
    return proc.stdout


def find_jats(article_path: str) -> str | None:
    """Find the JATS file matching a vol.iss/seq-slug prefix."""
    # Try exact match first
    exact = os.path.join(OUTPUT_DIR, article_path + '.jats.xml')
    if os.path.exists(exact):
        return exact

    # Try prefix match (user may omit the full slug)
    vol_iss = os.path.dirname(article_path)
    prefix = os.path.basename(article_path)
    pattern = os.path.join(OUTPUT_DIR, vol_iss, prefix + '*.jats.xml')
    matches = sorted(glob.glob(pattern))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f'Ambiguous match for "{article_path}":', file=sys.stderr)
        for m in matches:
            print(f'  {os.path.relpath(m, OUTPUT_DIR)}', file=sys.stderr)
        sys.exit(1)

    print(f'No JATS file found for "{article_path}"', file=sys.stderr)
    print(f'  Looked in: {os.path.join(OUTPUT_DIR, vol_iss)}/', file=sys.stderr)
    sys.exit(1)


def read_publisher_id(jats_path: str) -> int | None:
    """Read publisher-id from a JATS file."""
    try:
        tree = ET.parse(jats_path)
        for el in tree.iter():
            if el.tag.endswith('article-id') and el.get('pub-id-type') == 'publisher-id':
                return int(el.text.strip())
    except Exception:
        pass
    return None


def read_title(jats_path: str) -> str:
    """Read article title from JATS."""
    try:
        tree = ET.parse(jats_path)
        for el in tree.iter():
            if el.tag.endswith('article-title'):
                return ''.join(el.itertext()).strip()
    except Exception:
        pass
    return '(unknown title)'


def resolve_article(target: str, article_ref: str) -> tuple[int, str]:
    """Resolve an article reference to (submission_id, title).

    Accepts:
      - Numeric submission_id: "9494"
      - Backfill path: "29.2/03-on-the-phenomenon"
      - Title search: "embracing vulnerability" (searched in OJS DB)
    """
    # Numeric submission_id
    if article_ref.isdigit():
        pub_id = int(article_ref)
        out = run_sql(target, f"""
            SELECT ps.setting_value FROM publication_settings ps
            JOIN submissions s ON s.current_publication_id = ps.publication_id
            WHERE s.submission_id = {pub_id}
              AND ps.setting_name = 'title' AND ps.locale = 'en'
            LIMIT 1;
        """)
        title = out.strip() or '(unknown)'
        if not out.strip():
            print(f'Warning: submission_id {pub_id} not found in OJS ({target}).',
                  file=sys.stderr)
        return pub_id, title

    # Backfill path (contains / or matches vol.iss pattern)
    if '/' in article_ref:
        jats_path = find_jats(article_ref)
        pub_id = read_publisher_id(jats_path)
        title = read_title(jats_path)
        if pub_id is None:
            print(f'No publisher-id in {os.path.relpath(jats_path, OUTPUT_DIR)}',
                  file=sys.stderr)
            print('Article may not be imported into OJS yet.', file=sys.stderr)
            sys.exit(1)
        return pub_id, title

    # Title search in OJS DB
    safe_search = article_ref.replace("'", "''")
    out = run_sql(target, f"""
        SELECT s.submission_id, ps.setting_value
        FROM publication_settings ps
        JOIN submissions s ON s.current_publication_id = ps.publication_id
        WHERE ps.setting_name = 'title' AND ps.locale = 'en'
          AND ps.setting_value LIKE '%{safe_search}%'
        ORDER BY s.submission_id
        LIMIT 5;
    """)
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if not lines:
        print(f'No articles matching "{article_ref}" in OJS ({target}).', file=sys.stderr)
        sys.exit(1)
    if len(lines) > 1:
        print(f'Multiple matches for "{article_ref}":', file=sys.stderr)
        for line in lines:
            parts = line.split('\t', 1)
            print(f'  {parts[0]}: {parts[1] if len(parts) > 1 else "?"}', file=sys.stderr)
        print(f'Use a more specific search or pass the submission_id directly.',
              file=sys.stderr)
        sys.exit(1)
    parts = lines[0].split('\t', 1)
    return int(parts[0]), parts[1] if len(parts) > 1 else '(unknown)'


def flag_article(target: str, article_ref: str, comment: str) -> None:
    """Insert a rejected review for the article."""
    pub_id, title = resolve_article(target, article_ref)

    # Get current_publication_id from OJS
    out = run_sql(target, f"""
        SELECT current_publication_id FROM submissions
        WHERE submission_id = {pub_id};
    """)
    pub_row = out.strip()
    if not pub_row:
        print(f'Submission {pub_id} not found in OJS ({target}).', file=sys.stderr)
        sys.exit(1)
    publication_id = int(pub_row)

    # Escape single quotes in comment
    safe_comment = comment.replace("'", "''")

    run_sql(target, f"""
        INSERT INTO qa_split_reviews
            (submission_id, publication_id, user_id, username, decision, comment, created_at)
        VALUES
            ({pub_id}, {publication_id}, 1, 'claude', 'rejected',
             '{safe_comment}', NOW());
    """)

    print(f'Flagged: {title}')
    print(f'  submission_id={pub_id}, target={target}')
    print(f'  comment: {comment}')


def list_flags(target: str) -> None:
    """List all rejected reviews (current flags)."""
    out = run_sql(target, """
        SELECT r.submission_id, r.username, r.comment,
               r.created_at, ps.setting_value AS title
        FROM qa_split_reviews r
        LEFT JOIN publications p ON p.publication_id = r.publication_id
        LEFT JOIN publication_settings ps ON ps.publication_id = p.publication_id
            AND ps.setting_name = 'title' AND ps.locale = 'en'
        WHERE r.decision = 'rejected'
          AND r.review_id = (
              SELECT MAX(r2.review_id) FROM qa_split_reviews r2
              WHERE r2.submission_id = r.submission_id
          )
        ORDER BY r.created_at DESC;
    """)

    if not out.strip():
        print('No flagged articles.')
        return

    print(f'{"ID":<6} {"By":<10} {"Date":<20} {"Title":<40} Comment')
    print('-' * 100)
    for line in out.strip().splitlines():
        parts = line.split('\t')
        if len(parts) >= 5:
            sid, user, comment, date, title = parts[0], parts[1], parts[2], parts[3], parts[4]
            print(f'{sid:<6} {user:<10} {date:<20} {title[:38]:<40} {comment}')
        elif len(parts) >= 4:
            sid, user, comment, date = parts[0], parts[1], parts[2], parts[3]
            print(f'{sid:<6} {user:<10} {date:<20} {"?":<40} {comment}')


def clear_flag(target: str, article_ref: str) -> None:
    """Remove all reviews for an article (clear flag)."""
    pub_id, title = resolve_article(target, article_ref)

    run_sql(target, f"""
        DELETE FROM qa_split_reviews WHERE submission_id = {pub_id};
    """)

    print(f'Cleared all reviews for: {title} (submission_id={pub_id})')


def main():
    parser = argparse.ArgumentParser(
        description='Flag articles as QA problem cases in OJS.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--target', choices=['dev', 'live'], default='dev',
                        help='Target environment (default: dev)')
    parser.add_argument('--list', action='store_true',
                        help='List current flags')
    parser.add_argument('--clear', metavar='ARTICLE',
                        help='Clear flag for article (vol.iss/seq-slug)')
    parser.add_argument('article', nargs='?',
                        help='Article path: vol.iss/seq-slug (e.g. 29.2/03-on-the-phenomenon)')
    parser.add_argument('comment', nargs='?',
                        help='Rejection comment describing the problem')

    args = parser.parse_args()

    if args.list:
        list_flags(args.target)
    elif args.clear:
        clear_flag(args.target, args.clear)
    elif args.article and args.comment:
        flag_article(args.target, args.article, args.comment)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
