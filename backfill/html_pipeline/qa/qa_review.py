#!/usr/bin/env python3
"""
Archive Checker CLI — approve, reject, recheck, or check article review status.

CLI equivalent of the Archive Checker web interface. Manages review records
in the OJS archive_checker_reviews table.

Articles can be specified by:
  - Backfill path:   29.2/03-on-the-phenomenon
  - Submission ID:   9494
  - Title search:    "embracing vulnerability" (must match exactly 1)

Usage:
    # Approve an article:
    python backfill/qa_review.py approve 29.2/03-on-the-phenomenon

    # Reject with reason:
    python backfill/qa_review.py reject 9494 "references mixed with notes"

    # Mark as recheck (fixed, needs re-review):
    python backfill/qa_review.py recheck 9494 "Fixed: body was being truncated"

    # Check status of one article:
    python backfill/qa_review.py status 29.2/03-on-the-phenomenon

    # List flagged articles (default: needs_fix + recheck):
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

    Note: publication_id in archive_checker_reviews is audit-only — it goes stale
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
        INSERT INTO archive_checker_reviews
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
        INSERT INTO archive_checker_reviews
            (submission_id, publication_id, user_id, username, decision, comment, created_at)
        VALUES
            ({pub_id}, {publication_id}, 1, 'claude', 'needs_fix',
             '{safe_comment}', NOW());
    """)

    print(f'Rejected: {title}')
    print(f'  submission_id={pub_id}, target={target}')
    print(f'  comment: {comment}')


def cmd_recheck(target: str, article_ref: str, comment: str) -> None:
    """Mark an article as recheck (fixed, needs re-review)."""
    pub_id, title = resolve_article(target, article_ref)
    publication_id = get_publication_id(target, pub_id)

    safe_comment = comment.replace("'", "''")

    run_sql(target, f"""
        INSERT INTO archive_checker_reviews
            (submission_id, publication_id, user_id, username, decision, comment, created_at)
        VALUES
            ({pub_id}, {publication_id}, 1, 'claude', 'recheck',
             '{safe_comment}', NOW());
    """)

    print(f'Recheck: {title}')
    print(f'  submission_id={pub_id}, target={target}')
    print(f'  comment: {comment}')


def cmd_defer(target: str, article_ref: str, comment: str) -> None:
    """Mark an article as deferred (needs separate project to fix)."""
    pub_id, title = resolve_article(target, article_ref)
    publication_id = get_publication_id(target, pub_id)

    safe_comment = comment.replace("'", "''")

    run_sql(target, f"""
        INSERT INTO archive_checker_reviews
            (submission_id, publication_id, user_id, username, decision, comment, created_at)
        VALUES
            ({pub_id}, {publication_id}, 1, 'claude', 'deferred',
             '{safe_comment}', NOW());
    """)

    print(f'Deferred: {title}')
    print(f'  submission_id={pub_id}, target={target}')
    print(f'  comment: {comment}')


def cmd_status(target: str, article_ref: str) -> None:
    """Show review history for an article."""
    pub_id, title = resolve_article(target, article_ref)

    out = run_sql(target, f"""
        SELECT r.decision, r.username, r.comment, r.created_at
        FROM archive_checker_reviews r
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
    """List reviews. Default: needs_fix + recheck. --all: everything."""
    where = '' if show_all else "WHERE r.decision IN ('needs_fix', 'recheck')"
    out = run_sql(target, f"""
        SELECT r.submission_id, r.decision, r.username, r.comment,
               r.created_at, ps.setting_value AS title
        FROM archive_checker_reviews r
        LEFT JOIN submissions s ON s.submission_id = r.submission_id
        LEFT JOIN publication_settings ps ON ps.publication_id = s.current_publication_id
            AND ps.setting_name = 'title' AND ps.locale = 'en'
        {where}
          AND r.review_id = (
              SELECT MAX(r2.review_id) FROM archive_checker_reviews r2
              WHERE r2.submission_id = r.submission_id
          )
        ORDER BY r.created_at DESC;
    """)

    if not out.strip():
        label = 'reviews' if show_all else 'flagged articles'
        print(f'No {label}.')
        return

    header = 'All reviews' if show_all else 'Flagged articles (needs_fix + recheck)'
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
        DELETE FROM archive_checker_reviews WHERE submission_id = {pub_id};
    """)

    print(f'Cleared all reviews for: {title} (submission_id={pub_id})')


def _fetch_all_reviews(target: str) -> list[dict]:
    """Fetch all review rows from a target."""
    out = run_sql(target, """
        SELECT submission_id, decision, username, comment,
               content_hash, created_at
        FROM archive_checker_reviews
        ORDER BY created_at;
    """)
    rows = []
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 6:
            rows.append({
                'submission_id': int(parts[0]),
                'decision': parts[1],
                'username': parts[2],
                'comment': parts[3] if parts[3] != 'NULL' else '',
                'content_hash': parts[4] if parts[4] != 'NULL' else None,
                'created_at': parts[5],
            })
    return rows


def _review_fingerprint(r: dict) -> str:
    """Unique fingerprint for deduplication."""
    return f"{r['submission_id']}|{r['username']}|{r['created_at']}|{r['decision']}"


def _get_publication_ids(target: str) -> dict[int, int]:
    """Get submission_id → current_publication_id map."""
    out = run_sql(target, """
        SELECT submission_id, current_publication_id FROM submissions;
    """)
    result = {}
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 2:
            result[int(parts[0])] = int(parts[1])
    return result


def _sync_direction(source_name: str, dest_name: str,
                    source_rows: list[dict], dest_fingerprints: set[str],
                    dest_pub_ids: dict[int, int]) -> tuple[list[dict], int, int]:
    """Find rows to insert from source into dest. Returns (to_insert, skipped, not_found)."""
    to_insert = []
    skipped = 0
    not_found = 0

    for r in source_rows:
        fp = _review_fingerprint(r)
        if fp in dest_fingerprints:
            skipped += 1
            continue

        if r['submission_id'] not in dest_pub_ids:
            not_found += 1
            continue

        to_insert.append(r)

    return to_insert, skipped, not_found


def _insert_reviews(target: str, rows: list[dict], pub_ids: dict[int, int]) -> int:
    """Batch-insert review rows into target. Returns count inserted."""
    if not rows:
        return 0

    values = []
    for r in rows:
        pub_id = pub_ids[r['submission_id']]
        safe_comment = (r['comment'] or '').replace("'", "''")
        safe_username = (r['username'] or 'unknown').replace("'", "''")
        content_hash_sql = f"'{r['content_hash']}'" if r['content_hash'] else 'NULL'
        values.append(
            f"({r['submission_id']}, {pub_id}, 1, '{safe_username}', "
            f"'{r['decision']}', '{safe_comment}', {content_hash_sql}, "
            f"'{r['created_at']}')"
        )

    # Insert in batches of 100 to avoid overly long SQL
    BATCH = 100
    for i in range(0, len(values), BATCH):
        batch = values[i:i + BATCH]
        run_sql(target, (
            "INSERT INTO archive_checker_reviews "
            "(submission_id, publication_id, user_id, username, decision, "
            "comment, content_hash, created_at) VALUES\n"
            + ",\n".join(batch) + ";"
        ))

    return len(values)


def cmd_sync() -> None:
    """Bidirectional sync of all review history between dev and live.

    Matches by submission_id (stable across environments after pipe8).
    Merges all history rows, deduplicating by submission_id + username +
    created_at + decision. Effective status (newest wins) is automatic
    via MAX(review_id).
    """
    print('Bidirectional sync: dev ↔ live')
    print('Fetching reviews...')

    dev_rows = _fetch_all_reviews('dev')
    live_rows = _fetch_all_reviews('live')

    print(f'  dev:  {len(dev_rows)} review rows')
    print(f'  live: {len(live_rows)} review rows')

    dev_fps = {_review_fingerprint(r) for r in dev_rows}
    live_fps = {_review_fingerprint(r) for r in live_rows}

    dev_pub_ids = _get_publication_ids('dev')
    live_pub_ids = _get_publication_ids('live')

    # dev → live
    to_live, skip_live, nf_live = _sync_direction(
        'dev', 'live', dev_rows, live_fps, live_pub_ids)
    inserted_live = _insert_reviews('live', to_live, live_pub_ids)

    # live → dev
    to_dev, skip_dev, nf_dev = _sync_direction(
        'live', 'dev', live_rows, dev_fps, dev_pub_ids)
    inserted_dev = _insert_reviews('dev', to_dev, dev_pub_ids)

    print(f'\ndev → live: {inserted_live} synced, {skip_live} already present, {nf_live} not found on live')
    print(f'live → dev: {inserted_dev} synced, {skip_dev} already present, {nf_dev} not found on dev')


def main():
    parser = argparse.ArgumentParser(
        description='Archive Checker CLI — approve, reject, or check article review status.',
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

    # recheck
    p_recheck = sub.add_parser('recheck', help='Mark as recheck (fixed, needs re-review)')
    p_recheck.add_argument('article', help='Article: path, submission_id, or title search')
    p_recheck.add_argument('comment', help='Description of the fix')

    # defer
    p_defer = sub.add_parser('defer', help='Mark as deferred (needs separate project)')
    p_defer.add_argument('article', help='Article: path, submission_id, or title search')
    p_defer.add_argument('comment', help='Reason for deferral')

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
    sub.add_parser('sync', help='Bidirectional sync of review history between dev and live')

    args = parser.parse_args()

    if args.command == 'approve':
        cmd_approve(args.target, args.article)
    elif args.command == 'reject':
        cmd_reject(args.target, args.article, args.comment)
    elif args.command == 'recheck':
        cmd_recheck(args.target, args.article, args.comment)
    elif args.command == 'defer':
        cmd_defer(args.target, args.article, args.comment)
    elif args.command == 'status':
        cmd_status(args.target, args.article)
    elif args.command == 'list':
        cmd_list(args.target, args.all)
    elif args.command == 'clear':
        cmd_clear(args.target, args.article)
    elif args.command == 'sync':
        cmd_sync()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
