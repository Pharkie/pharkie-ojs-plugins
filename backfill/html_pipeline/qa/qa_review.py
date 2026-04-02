#!/usr/bin/env python3
"""
QA Splits CLI — approve, reject, or check article review status.

CLI equivalent of the QA Splits web interface. Manages review records
in the OJS qa_split_reviews table.

Articles can be specified by:
  - Backfill path:   29.2/03-on-the-phenomenon
  - Submission ID:   9494
  - Title search:    "embracing vulnerability" (must match exactly 1)

Usage:
    # Approve an article:
    python backfill/qa_review.py approve 29.2/03-on-the-phenomenon

    # Reject with reason:
    python backfill/qa_review.py reject 9494 "references mixed with notes"

    # Check status of one article:
    python backfill/qa_review.py status 29.2/03-on-the-phenomenon

    # List all reviews (default: problems only):
    python backfill/qa_review.py list
    python backfill/qa_review.py list --all

    # Clear all reviews for an article:
    python backfill/qa_review.py clear 9494

    # Target live instead of dev:
    python backfill/qa_review.py --target live list
"""

import argparse
import glob
import os
import subprocess
import sys
from xml.etree import ElementTree as ET

BACKFILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    try:
        proc = subprocess.run(
            TARGETS[target]['cmd'],
            input=sql,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print('ERROR: SQL timed out after 60 seconds', file=sys.stderr)
        sys.exit(1)
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
    exact = os.path.join(OUTPUT_DIR, article_path + '.jats.xml')
    if os.path.exists(exact):
        return exact

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

    Accepts numeric ID, backfill path (vol.iss/seq-slug), or title search.
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

    # Backfill path (contains /)
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
    safe_search = article_ref.replace("'", "''").replace('%', '\\%').replace('_', '\\_')
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
        print('Use a more specific search or pass the submission_id directly.',
              file=sys.stderr)
        sys.exit(1)
    parts = lines[0].split('\t', 1)
    return int(parts[0]), parts[1] if len(parts) > 1 else '(unknown)'


def get_publication_id(target: str, submission_id: int) -> int:
    """Get current_publication_id for a submission.

    Note: publication_id in qa_split_reviews is audit-only — it goes stale
    after reimport. NEVER join on r.publication_id for lookups; always go
    through submissions.current_publication_id.
    """
    out = run_sql(target, f"""
        SELECT current_publication_id FROM submissions
        WHERE submission_id = {submission_id};
    """)
    pub_row = out.strip()
    if not pub_row:
        print(f'Submission {submission_id} not found in OJS ({target}).', file=sys.stderr)
        sys.exit(1)
    return int(pub_row)


# ── Commands ──


def cmd_approve(target: str, article_ref: str) -> None:
    """Record an approval for the article."""
    pub_id, title = resolve_article(target, article_ref)
    publication_id = get_publication_id(target, pub_id)

    run_sql(target, f"""
        INSERT INTO qa_split_reviews
            (submission_id, publication_id, user_id, username, decision, comment, created_at)
        VALUES
            ({pub_id}, {publication_id}, 1, 'claude', 'approved', NULL, NOW());
    """)

    print(f'Approved: {title}')
    print(f'  submission_id={pub_id}, target={target}')


def cmd_reject(target: str, article_ref: str, comment: str) -> None:
    """Record a rejection with comment."""
    pub_id, title = resolve_article(target, article_ref)
    publication_id = get_publication_id(target, pub_id)

    safe_comment = comment.replace("'", "''")

    run_sql(target, f"""
        INSERT INTO qa_split_reviews
            (submission_id, publication_id, user_id, username, decision, comment, created_at)
        VALUES
            ({pub_id}, {publication_id}, 1, 'claude', 'needs_fix',
             '{safe_comment}', NOW());
    """)

    print(f'Rejected: {title}')
    print(f'  submission_id={pub_id}, target={target}')
    print(f'  comment: {comment}')


def cmd_status(target: str, article_ref: str) -> None:
    """Show review history for an article."""
    pub_id, title = resolve_article(target, article_ref)

    out = run_sql(target, f"""
        SELECT r.decision, r.username, r.comment, r.created_at
        FROM qa_split_reviews r
        WHERE r.submission_id = {pub_id}
        ORDER BY r.created_at DESC;
    """)

    print(f'{title} (#{pub_id})')
    if not out.strip():
        print('  Status: not reviewed')
        return

    lines = out.strip().splitlines()
    first = lines[0].split('\t')
    status = first[0].upper()
    print(f'  Status: {status} (by {first[1]}, {first[3]})')
    if first[2] and first[2] != 'NULL':
        print(f'  Comment: {first[2]}')

    if len(lines) > 1:
        print(f'\n  Review history ({len(lines)} reviews):')
        for line in lines:
            parts = line.split('\t')
            decision = parts[0]
            user = parts[1] if len(parts) > 1 else '?'
            comment = parts[2] if len(parts) > 2 and parts[2] != 'NULL' else ''
            date = parts[3] if len(parts) > 3 else '?'
            suffix = f' — {comment}' if comment else ''
            print(f'    {date}  {decision:<10} by {user}{suffix}')


def cmd_list(target: str, show_all: bool) -> None:
    """List reviews. Default: rejected/problem cases only. --all: everything."""
    where = '' if show_all else "WHERE r.decision = 'needs_fix'"
    out = run_sql(target, f"""
        SELECT r.submission_id, r.decision, r.username, r.comment,
               r.created_at, ps.setting_value AS title
        FROM qa_split_reviews r
        LEFT JOIN submissions s ON s.submission_id = r.submission_id
        LEFT JOIN publication_settings ps ON ps.publication_id = s.current_publication_id
            AND ps.setting_name = 'title' AND ps.locale = 'en'
        {where}
          AND r.review_id = (
              SELECT MAX(r2.review_id) FROM qa_split_reviews r2
              WHERE r2.submission_id = r.submission_id
          )
        ORDER BY r.created_at DESC;
    """)

    if not out.strip():
        label = 'reviews' if show_all else 'flagged articles'
        print(f'No {label}.')
        return

    header = 'All reviews' if show_all else 'Flagged articles (rejected)'
    print(f'{header}:\n')
    print(f'{"ID":<6} {"Status":<10} {"By":<10} {"Date":<20} {"Title":<35} Comment')
    print('-' * 105)
    for line in out.strip().splitlines():
        parts = line.split('\t')
        if len(parts) >= 6:
            sid, decision, user, comment, date, title = parts[:6]
            comment = '' if comment == 'NULL' else comment
            print(f'{sid:<6} {decision:<10} {user:<10} {date:<20} {title[:33]:<35} {comment}')


def cmd_clear(target: str, article_ref: str) -> None:
    """Remove all reviews for an article."""
    pub_id, title = resolve_article(target, article_ref)

    run_sql(target, f"""
        DELETE FROM qa_split_reviews WHERE submission_id = {pub_id};
    """)

    print(f'Cleared all reviews for: {title} (submission_id={pub_id})')


def cmd_sync(source: str, dest: str) -> None:
    """Sync reviews from one environment to another.

    Matches articles by title (not submission_id, which may differ).
    Only syncs the latest review per article. Skips articles that
    already have a newer review on the destination.
    """
    # Get all latest reviews from source with titles
    out = run_sql(source, """
        SELECT r.submission_id, r.decision, r.username, r.comment,
               r.created_at, ps.setting_value AS title
        FROM qa_split_reviews r
        LEFT JOIN submissions s ON s.submission_id = r.submission_id
        LEFT JOIN publication_settings ps ON ps.publication_id = s.current_publication_id
            AND ps.setting_name = 'title' AND ps.locale = 'en'
        WHERE r.review_id = (
            SELECT MAX(r2.review_id) FROM qa_split_reviews r2
            WHERE r2.submission_id = r.submission_id
        )
        ORDER BY r.created_at DESC;
    """)

    if not out.strip():
        print(f'No reviews on {source} to sync.')
        return

    synced = 0
    skipped = 0
    not_found = 0

    for line in out.strip().splitlines():
        parts = line.split('\t')
        if len(parts) < 6:
            continue
        src_id, decision, username, comment, created_at, title = parts[:6]
        if not title or title == 'NULL':
            skipped += 1
            continue

        # Find article on destination by title
        safe_title = title.replace("'", "''").replace('%', '\\%').replace('_', '\\_')
        dest_out = run_sql(dest, f"""
            SELECT s.submission_id, s.current_publication_id
            FROM submissions s
            JOIN publication_settings ps ON ps.publication_id = s.current_publication_id
            WHERE ps.setting_name = 'title' AND ps.locale = 'en'
              AND ps.setting_value = '{safe_title}'
            LIMIT 1;
        """)

        dest_row = dest_out.strip()
        if not dest_row:
            not_found += 1
            continue

        dest_parts = dest_row.split('\t')
        dest_sub_id = int(dest_parts[0])
        dest_pub_id = int(dest_parts[1])

        # Check if destination already has a newer review
        dest_latest = run_sql(dest, f"""
            SELECT created_at FROM qa_split_reviews
            WHERE submission_id = {dest_sub_id}
            ORDER BY review_id DESC LIMIT 1;
        """).strip()

        if dest_latest and dest_latest >= created_at:
            skipped += 1
            continue

        safe_comment = (comment or '').replace("'", "''")
        if comment == 'NULL':
            safe_comment = ''
        safe_username = (username or 'unknown').replace("'", "''")

        run_sql(dest, f"""
            INSERT INTO qa_split_reviews
                (submission_id, publication_id, user_id, username, decision, comment, created_at)
            VALUES
                ({dest_sub_id}, {dest_pub_id}, 1, '{safe_username}',
                 '{decision}', '{safe_comment}', '{created_at}');
        """)
        synced += 1
        print(f'  {decision}: {title[:50]}')

    print(f'\nSync {source} → {dest}: {synced} synced, {skipped} skipped, {not_found} not found on {dest}')


def main():
    parser = argparse.ArgumentParser(
        description='QA Splits CLI — approve, reject, or check article review status.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--target', choices=['dev', 'live'], default='dev',
                        help='Target environment (default: dev)')

    sub = parser.add_subparsers(dest='command', help='Command')

    # approve
    p_approve = sub.add_parser('approve', help='Approve an article')
    p_approve.add_argument('article', help='Article: path, submission_id, or title search')

    # reject
    p_reject = sub.add_parser('reject', help='Reject an article with comment')
    p_reject.add_argument('article', help='Article: path, submission_id, or title search')
    p_reject.add_argument('comment', help='Rejection reason')

    # status
    p_status = sub.add_parser('status', help='Show review status and history')
    p_status.add_argument('article', help='Article: path, submission_id, or title search')

    # list
    p_list = sub.add_parser('list', help='List reviews (default: problems only)')
    p_list.add_argument('--all', action='store_true', help='Show all reviews, not just rejections')

    # clear
    p_clear = sub.add_parser('clear', help='Remove all reviews for an article')
    p_clear.add_argument('article', help='Article: path, submission_id, or title search')

    # sync
    p_sync = sub.add_parser('sync', help='Sync reviews between environments')
    p_sync.add_argument('--from', dest='sync_from', choices=['dev', 'live'], required=True,
                        help='Source environment')
    p_sync.add_argument('--to', dest='sync_to', choices=['dev', 'live'], required=True,
                        help='Destination environment')

    args = parser.parse_args()

    if args.command == 'approve':
        cmd_approve(args.target, args.article)
    elif args.command == 'reject':
        cmd_reject(args.target, args.article, args.comment)
    elif args.command == 'status':
        cmd_status(args.target, args.article)
    elif args.command == 'list':
        cmd_list(args.target, args.all)
    elif args.command == 'clear':
        cmd_clear(args.target, args.article)
    elif args.command == 'sync':
        if args.sync_from == args.sync_to:
            print('Source and destination must be different.', file=sys.stderr)
            sys.exit(1)
        cmd_sync(args.sync_from, args.sync_to)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
