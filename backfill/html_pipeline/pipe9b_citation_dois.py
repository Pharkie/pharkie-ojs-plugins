#!/usr/bin/env python3
"""
Write matched citation DOIs from JATS to OJS citation_settings table.

Reads <pub-id pub-id-type="doi"> elements from JATS XML (written by
pipe4b_match_dois.py) and writes them to OJS's citation_settings table
as 'crossref::doi' entries. This matches the format used by the
pkp/crossrefReferenceLinking plugin, which renders DOI links on article pages.

JATS is the single source of truth — this script reads from JATS, not
from doi_matches.json.

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
import os
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
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise SqlError('SQL timed out after 60 seconds')
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


def load_jats_ref_dois(jats_path):
    """Load refs with DOIs from a JATS file.

    Returns list of dicts: {seq (1-indexed), doi, text (first 30 chars)}.
    """
    tree = ET.parse(jats_path)
    results = []
    for i, ref_el in enumerate(tree.findall('.//ref-list/ref'), start=1):
        pub_id = ref_el.find("pub-id[@pub-id-type='doi']")
        if pub_id is None or not pub_id.text:
            continue
        mc = ref_el.find('mixed-citation')
        text = mc.text.strip() if mc is not None and mc.text else ''
        results.append({
            'seq': i,
            'doi': pub_id.text.strip(),
            'text': text,
        })
    return results


def get_publication_id(target, title, volume, issue):
    """Find OJS publication_id for an article by title + volume + issue."""
    title_escaped = _escape_sql(title)
    sql = f"""
        SELECT p.publication_id
        FROM publications p
        JOIN issues i ON p.issue_id = i.issue_id
        JOIN publication_settings ps ON ps.publication_id = p.publication_id
            AND ps.setting_name = 'title'
        WHERE ps.setting_value = '{title_escaped}'
            AND i.volume = '{volume}'
            AND i.number = '{issue}'
            AND p.status = 3
        LIMIT 1;
    """
    out = run_sql(target, sql).strip()
    return int(out) if out else None


def get_citations(target, publication_id):
    """Get citations for a publication, ordered by seq.

    Returns list of dicts: {citation_id, seq, text (first 30 chars)}.
    """
    sql = f"""
        SELECT c.citation_id, c.seq, LEFT(c.raw_citation, 30)
        FROM citations c
        WHERE c.publication_id = {publication_id}
        ORDER BY c.seq;
    """
    out = run_sql(target, sql).strip()
    if not out:
        return []
    results = []
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) >= 3:
            results.append({
                'citation_id': int(parts[0]),
                'seq': int(parts[1]),
                'text': parts[2],
            })
    return results


def build_insert_sql(citation_id, doi):
    """Build SQL to write a citation DOI."""
    doi_escaped = _escape_sql(doi)
    return (
        f"INSERT INTO citation_settings "
        f"(citation_id, locale, setting_name, setting_value, setting_type) "
        f"VALUES ({citation_id}, '', '{SETTING_NAME}', '{doi_escaped}', 'string') "
        f"ON DUPLICATE KEY UPDATE setting_value = '{doi_escaped}';"
    )


def process_volume(target, vol_dir, dry_run=False, verbose=False):
    """Process all articles in a volume directory.

    Returns (written, skipped, errors) counts.
    """
    toc_path = vol_dir / 'toc.json'
    if not toc_path.exists():
        return 0, 0, 0

    with open(toc_path) as f:
        toc = json.load(f)

    volume = str(toc.get('volume', ''))
    issue = str(toc.get('issue', ''))
    vol_label = f'{volume}.{issue}' if issue else volume

    written = 0
    skipped = 0
    errors = 0

    for article in toc['articles']:
        pdf = article.get('split_pdf', '')
        slug = Path(pdf).stem if pdf else ''
        if not slug:
            continue

        jats_path = vol_dir / f'{slug}.jats.xml'
        if not jats_path.exists():
            continue

        # Load refs with DOIs from JATS
        ref_dois = load_jats_ref_dois(jats_path)
        if not ref_dois:
            continue

        title = article.get('title', '')
        if not title:
            continue

        # Find publication in OJS
        pub_id = get_publication_id(target, title, volume, issue)
        if pub_id is None:
            if verbose:
                print(f'  WARNING: article not found in OJS: '
                      f'{vol_label}/{slug} "{title[:40]}"')
            errors += len(ref_dois)
            continue

        # Get OJS citations for this publication
        ojs_citations = get_citations(target, pub_id)
        if not ojs_citations:
            if verbose:
                print(f'  WARNING: no citations in OJS for '
                      f'{vol_label}/{slug} (pub_id={pub_id})')
            errors += len(ref_dois)
            continue

        # Build a map: seq → citation_id + text
        cit_by_seq = {c['seq']: c for c in ojs_citations}

        for ref in ref_dois:
            seq = ref['seq']
            doi = ref['doi']
            jats_text = _normalize(ref['text'])[:30]

            cit = cit_by_seq.get(seq)
            if cit is None:
                if verbose:
                    print(f'  WARNING: no citation at seq={seq} for '
                          f'{vol_label}/{slug}')
                errors += 1
                continue

            # Sanity check: do the texts roughly match?
            ojs_text = _normalize(cit['text'])
            if jats_text and ojs_text and jats_text[:20] != ojs_text[:20]:
                if verbose:
                    print(f'  WARNING: text mismatch at seq={seq} for '
                          f'{vol_label}/{slug}')
                    print(f'    JATS: {jats_text[:30]}')
                    print(f'    OJS:  {ojs_text[:30]}')
                errors += 1
                continue

            sql = build_insert_sql(cit['citation_id'], doi)

            if dry_run:
                if verbose:
                    print(f'  DRY-RUN: {vol_label}/{slug} ref{seq} → {doi}')
                written += 1
            else:
                try:
                    run_sql(target, sql)
                    written += 1
                    if verbose:
                        print(f'  OK: {vol_label}/{slug} ref{seq} → {doi}')
                except SqlError as e:
                    print(f'  ERROR writing {vol_label}/{slug} ref{seq}: {e}',
                          file=sys.stderr)
                    errors += 1

    return written, skipped, errors


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
    if not args.dry_run:
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

    total_written = 0
    total_errors = 0

    for vol_dir in vol_dirs:
        vol = vol_dir.name
        w, s, e = process_volume(
            args.target, vol_dir,
            dry_run=args.dry_run, verbose=args.verbose,
        )
        if w > 0 or e > 0:
            prefix = 'DRY-RUN: ' if args.dry_run else ''
            print(f'{prefix}{vol}: {w} DOIs written, {e} errors')
        total_written += w
        total_errors += e

    prefix = 'DRY-RUN: ' if args.dry_run else ''
    print(f'\n{prefix}Total: {total_written} DOIs written, '
          f'{total_errors} errors')


if __name__ == '__main__':
    main()
