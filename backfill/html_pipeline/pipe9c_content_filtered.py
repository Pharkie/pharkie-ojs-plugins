#!/usr/bin/env python3
"""
Write content-filtered flags from JATS to OJS publication_settings table.

Reads <custom-meta><meta-name>content-filtered</meta-name> elements from
JATS XML (written by pipe3_generate_jats.py) and writes them to OJS's
publication_settings table as 'contentFiltered' entries.

JATS is the single source of truth.

Architecture: two SQL calls total.
  1. One bulk SELECT to fetch all publications with their article titles
  2. One bulk INSERT with all content-filtered flags

Usage:
    python3 backfill/html_pipeline/pipe9c_content_filtered.py --target dev --dry-run
    python3 backfill/html_pipeline/pipe9c_content_filtered.py --target dev
    python3 backfill/html_pipeline/pipe9c_content_filtered.py --target live --confirm
"""

import argparse
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

BACKFILL_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BACKFILL_DIR / 'private' / 'output'

# Reuse pipe9b's SQL infrastructure
sys.path.insert(0, str(BACKFILL_DIR))
from html_pipeline.pipe9b_citation_dois import (
    TARGETS, run_sql, check_connectivity, SqlError, _normalize, _escape_sql,
)

SETTING_NAME = 'contentFiltered'


def load_content_filtered_from_jats(vol_dirs):
    """Load all content-filtered articles from JATS across all volumes.

    Returns list of dicts: {vol, slug, title, volume, issue}.
    """
    all_articles = []
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

            # Check for content-filtered custom-meta
            tree = ET.parse(jats_path)
            is_filtered = False
            for cm in list(tree.findall('.//{*}custom-meta')) + list(tree.findall('.//custom-meta')):
                mn = cm.find('{*}meta-name') or cm.find('meta-name')
                if mn is not None and mn.text == 'content-filtered':
                    is_filtered = True
                    break

            if is_filtered:
                all_articles.append({
                    'vol': vol_dir.name,
                    'slug': slug,
                    'title': title,
                    'volume': volume,
                    'issue': issue,
                })

    return all_articles


def fetch_all_publications(target):
    """Fetch all publications from OJS.

    Returns dict: (normalised_title, volume, issue) → publication_id.
    """
    sql = """
        SELECT p.publication_id,
               ps.setting_value AS title,
               i.volume, i.number
        FROM publications p
        JOIN publication_settings ps ON ps.publication_id = p.publication_id
            AND ps.setting_name = 'title'
        JOIN issues i ON p.issue_id = i.issue_id
        WHERE p.status = 3
        ORDER BY p.publication_id;
    """
    out = run_sql(target, sql).strip()
    if not out:
        return {}

    pub_lookup = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) < 4:
            continue
        publication_id = int(parts[0])
        title = parts[1]
        volume = parts[2]
        issue_num = parts[3]
        norm_title = _normalize(title)
        pub_lookup[(norm_title, volume, issue_num)] = publication_id

    return pub_lookup


def main():
    parser = argparse.ArgumentParser(
        description='Write content-filtered flags from JATS to OJS database')
    parser.add_argument('--target', required=True, choices=['dev', 'live'])
    parser.add_argument('--issue', help='Process only one issue (e.g., 12.1)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--confirm', action='store_true')
    parser.add_argument('--verbose', '-v', action='store_true')

    args = parser.parse_args()

    if args.target == 'live' and not args.dry_run and not args.confirm:
        print('ERROR: --confirm required for live execution', file=sys.stderr)
        sys.exit(1)

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

    # Step 1: Load content-filtered articles from JATS
    print('Loading content-filtered articles from JATS...')
    filtered_articles = load_content_filtered_from_jats(vol_dirs)
    print(f'  {len(filtered_articles)} content-filtered articles found')

    if not filtered_articles:
        print('Nothing to write.')
        return

    # Step 2: Fetch all OJS publications
    print('Fetching OJS publications...')
    pub_lookup = fetch_all_publications(args.target)
    print(f'  {len(pub_lookup)} publications')

    # Step 3: Match and build INSERT statements
    inserts = []
    errors = 0

    for art in filtered_articles:
        norm_title = _normalize(art['title'])
        pub_id = pub_lookup.get((norm_title, art['volume'], art['issue']))

        if pub_id is None:
            if args.verbose:
                print(f'  WARNING: article not found: '
                      f'{art["vol"]}/{art["slug"]} "{art["title"][:40]}"')
            errors += 1
            continue

        inserts.append(
            f"({pub_id}, '', '{SETTING_NAME}', '1')"
        )

    print(f'\n{len(inserts)} content-filtered flags to write, {errors} errors')

    if not inserts:
        print('Nothing to write.')
        return

    # Step 4: Execute one bulk INSERT
    bulk_sql = (
        "INSERT INTO publication_settings "
        "(publication_id, locale, setting_name, setting_value) "
        "VALUES\n"
        + ",\n".join(inserts)
        + "\nON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value);"
    )

    if args.dry_run:
        print(f'\nDRY-RUN: would execute {len(inserts)} inserts')
        if args.verbose:
            print(bulk_sql[:500] + '...')
    else:
        print(f'\nExecuting bulk insert ({len(inserts)} rows)...')
        try:
            run_sql(args.target, bulk_sql)
            print(f'Done. {len(inserts)} content-filtered flags written.')
        except SqlError as e:
            print(f'ERROR: {e}', file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
