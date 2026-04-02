#!/usr/bin/env python3
"""
Write matched citation DOIs from JATS to OJS citation_settings table.

Reads <pub-id pub-id-type="doi"> elements from JATS XML (written by
pipe4b_match_dois.py) and writes them to OJS's citation_settings table
as 'crossref::doi' entries. This matches the format used by the
pkp/crossrefReferenceLinking plugin, which renders DOI links on article pages.

JATS is the single source of truth — this script reads from JATS, not
from doi_matches.json.

Architecture: two SQL calls total.
  1. One bulk SELECT to fetch all citations with their article titles
  2. One bulk INSERT with all matched DOIs

Usage:
    # Preview SQL (dev):
    python3 backfill/html_pipeline/pipe9b_citation_dois.py --target dev --dry-run

    # Single issue:
    python3 backfill/html_pipeline/pipe9b_citation_dois.py --target dev --issue 12.1

    # Execute on dev:
    python3 backfill/html_pipeline/pipe9b_citation_dois.py --target dev

    # Execute on live (requires --confirm):
    python3 backfill/html_pipeline/pipe9b_citation_dois.py --target live --confirm
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

BACKFILL_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BACKFILL_DIR / 'private' / 'output'

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

SETTING_NAME = 'crossref::doi'


class SqlError(Exception):
    pass


def run_sql(target, sql):
    """Execute SQL against the target and return output."""
    cfg = TARGETS[target]
    try:
        proc = subprocess.run(
            cfg['cmd'],
            input=sql,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise SqlError('SQL timed out after 120 seconds')
    stderr = proc.stderr.strip()
    stderr_lines = [l for l in stderr.splitlines()
                    if 'password on the command line' not in l]
    stderr_clean = '\n'.join(stderr_lines).strip()

    if proc.returncode != 0:
        raise SqlError(f'SQL failed (exit {proc.returncode}): {stderr_clean}')
    if stderr_clean:
        print(f'  SQL warning: {stderr_clean}', file=sys.stderr)
    return proc.stdout


def check_connectivity(target):
    """Verify we can reach the database."""
    try:
        out = run_sql(target, 'SELECT 1;')
        return '1' in out
    except (SqlError, Exception) as e:
        print(f'ERROR: Cannot connect to {target} database: {e}',
              file=sys.stderr)
        return False


def _normalize(text):
    """Normalize text for comparison (lowercase, collapse whitespace)."""
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def _escape_sql(s):
    """Escape a string for SQL."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def load_all_jats_ref_dois(vol_dirs):
    """Load all refs with DOIs from JATS across all volumes.

    Returns list of dicts: {vol, slug, title, volume, issue, seq, doi, text}.
    """
    all_refs = []
    for vol_dir in vol_dirs:
        toc_path = vol_dir / 'toc.json'
        if not toc_path.exists():
            continue
        with open(toc_path) as f:
            toc = json.load(f)

        volume = str(toc.get('volume', ''))
        issue = str(toc.get('issue', ''))

        for article in toc['articles']:
            pdf = article.get('split_pdf', '')
            slug = Path(pdf).stem if pdf else ''
            if not slug:
                continue

            jats_path = vol_dir / f'{slug}.jats.xml'
            if not jats_path.exists():
                continue

            title = article.get('title', '')
            if not title:
                continue

            tree = ET.parse(jats_path)
            for i, ref_el in enumerate(tree.findall('.//ref-list/ref'),
                                       start=1):
                pub_id = ref_el.find("pub-id[@pub-id-type='doi']")
                if pub_id is None or not pub_id.text:
                    continue
                mc = ref_el.find('mixed-citation')
                text = mc.text.strip() if mc is not None and mc.text else ''
                all_refs.append({
                    'vol': vol_dir.name,
                    'slug': slug,
                    'title': title,
                    'volume': volume,
                    'issue': issue,
                    'seq': i,
                    'doi': pub_id.text.strip(),
                    'text': text,
                })

    return all_refs


def fetch_all_citations(target):
    """Fetch all citations from OJS in one query.

    Returns dict: (publication_id, seq) → {citation_id, text}.
    Also returns dict: (normalised_title, volume, issue) → publication_id.
    """
    sql = """
        SELECT c.citation_id, c.publication_id, c.seq,
               LEFT(c.raw_citation, 30) AS citation_text,
               ps.setting_value AS title,
               i.volume, i.number
        FROM citations c
        JOIN publications p ON c.publication_id = p.publication_id
        JOIN publication_settings ps ON ps.publication_id = p.publication_id
            AND ps.setting_name = 'title'
        JOIN issues i ON p.issue_id = i.issue_id
        WHERE p.status = 3
        ORDER BY c.publication_id, c.seq;
    """
    out = run_sql(target, sql).strip()
    if not out:
        return {}, {}

    citations = {}  # (publication_id, seq) → {citation_id, text}
    pub_lookup = {}  # (norm_title, volume, issue) → publication_id

    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) < 7:
            continue
        citation_id = int(parts[0])
        publication_id = int(parts[1])
        seq = int(parts[2])
        cit_text = parts[3]
        title = parts[4]
        volume = parts[5]
        issue_num = parts[6]

        citations[(publication_id, seq)] = {
            'citation_id': citation_id,
            'text': cit_text,
        }
        norm_title = _normalize(title)
        pub_lookup[(norm_title, volume, issue_num)] = publication_id

    return citations, pub_lookup


def main():
    parser = argparse.ArgumentParser(
        description='Write matched citation DOIs from JATS to OJS database')
    parser.add_argument('--target', required=True, choices=['dev', 'live'],
                        help='Target database (dev or live)')
    parser.add_argument('--issue', help='Process only one issue (e.g., 12.1)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be written without executing')
    parser.add_argument('--confirm', action='store_true',
                        help='Required for live execution (safety gate)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print detailed output')

    args = parser.parse_args()

    if args.target == 'live' and not args.dry_run and not args.confirm:
        print('ERROR: --confirm required for live execution '
              '(use --dry-run to preview)', file=sys.stderr)
        sys.exit(1)

    # Check connectivity
    if not check_connectivity(args.target):
        sys.exit(1)
    print(f'Connected to {args.target} database')

    # Find volume directories
    if args.issue:
        vol_dirs = [OUTPUT_DIR / args.issue]
        if not vol_dirs[0].exists():
            print(f'ERROR: Volume directory not found: {vol_dirs[0]}',
                  file=sys.stderr)
            sys.exit(1)
    else:
        vol_dirs = sorted(
            [d for d in OUTPUT_DIR.iterdir()
             if d.is_dir() and (d / 'toc.json').exists()],
            key=lambda p: p.name,
        )

    # Step 1: Load all JATS ref DOIs (local, fast)
    print('Loading JATS ref DOIs...')
    jats_refs = load_all_jats_ref_dois(vol_dirs)
    print(f'  {len(jats_refs)} refs with DOIs in JATS')

    if not jats_refs:
        print('Nothing to do.')
        return

    # Step 2: Fetch all OJS citations in one query
    print('Fetching OJS citations...')
    citations, pub_lookup = fetch_all_citations(args.target)
    print(f'  {len(citations)} citations, {len(pub_lookup)} articles')

    # Step 3: Match JATS refs to OJS citations and build INSERT statements
    inserts = []
    errors = 0
    matched_vols = {}

    for ref in jats_refs:
        norm_title = _normalize(ref['title'])
        pub_id = pub_lookup.get((norm_title, ref['volume'], ref['issue']))

        if pub_id is None:
            if args.verbose:
                print(f'  WARNING: article not found: '
                      f'{ref["vol"]}/{ref["slug"]} "{ref["title"][:40]}"')
            errors += 1
            continue

        cit = citations.get((pub_id, ref['seq']))
        if cit is None:
            if args.verbose:
                print(f'  WARNING: no citation at seq={ref["seq"]} '
                      f'for {ref["vol"]}/{ref["slug"]}')
            errors += 1
            continue

        # Sanity check
        jats_text = _normalize(ref['text'])[:20]
        ojs_text = _normalize(cit['text'])[:20]
        if jats_text and ojs_text and jats_text != ojs_text:
            if args.verbose:
                print(f'  WARNING: text mismatch at seq={ref["seq"]} '
                      f'for {ref["vol"]}/{ref["slug"]}')
                print(f'    JATS: {jats_text}')
                print(f'    OJS:  {ojs_text}')
            errors += 1
            continue

        doi_escaped = _escape_sql(ref['doi'])
        inserts.append(
            f"({cit['citation_id']}, '', '{SETTING_NAME}', "
            f"'{doi_escaped}', 'string')"
        )

        vol = ref['vol']
        matched_vols[vol] = matched_vols.get(vol, 0) + 1

    print(f'\n{len(inserts)} DOIs to write, {errors} errors')

    if not inserts:
        print('Nothing to write.')
        return

    # Show per-volume breakdown
    for vol in sorted(matched_vols, key=lambda v: v):
        print(f'  {vol}: {matched_vols[vol]}')

    # Step 4: Execute one bulk INSERT
    bulk_sql = (
        "INSERT INTO citation_settings "
        "(citation_id, locale, setting_name, setting_value, setting_type) "
        "VALUES\n"
        + ",\n".join(inserts)
        + "\nON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value);"
    )

    if args.dry_run:
        print(f'\nDRY-RUN: would execute {len(inserts)} inserts '
              f'({len(bulk_sql)} chars SQL)')
        if args.verbose:
            print(bulk_sql[:500] + '...')
    else:
        print(f'\nExecuting bulk insert ({len(inserts)} rows)...')
        try:
            run_sql(args.target, bulk_sql)
            print(f'Done. {len(inserts)} DOIs written to citation_settings.')
        except SqlError as e:
            print(f'ERROR: {e}', file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
