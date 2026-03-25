#!/usr/bin/env python3
"""
Generate an archive-wide summary of all processed issues.

Reads toc.json and enrichment.json files and outputs a MANIFEST.md with
issue-level stats, coverage percentages, and gap analysis.

Usage:
    python3 backfill/manifest.py backfill/private/output/*/toc.json
"""

import sys
import os
import json
import argparse


def load_issue(toc_path):
    """Load toc.json and optional enrichment.json, return summary stats."""
    with open(toc_path) as f:
        toc = json.load(f)

    issue_dir = os.path.dirname(os.path.abspath(toc_path))
    enrichment_path = os.path.join(issue_dir, 'enrichment.json')
    enrichment = {}
    if os.path.exists(enrichment_path):
        with open(enrichment_path) as f:
            enrichment = json.load(f).get('articles', {})

    articles = toc.get('articles', [])
    n_articles = len(articles)

    # Keyword coverage: articles with at least one keyword
    n_with_keywords = sum(1 for a in articles if a.get('keywords'))

    # Enrichment coverage
    n_with_subjects = 0
    n_enriched = 0
    for a in articles:
        rid = a.get('_review_id', '')
        if rid in enrichment:
            n_enriched += 1
            if enrichment[rid].get('subjects'):
                n_with_subjects += 1
        elif a.get('subjects'):
            n_with_subjects += 1

    return {
        'vol': toc.get('volume', 0),
        'iss': toc.get('issue', 0),
        'date': toc.get('date', ''),
        'n_articles': n_articles,
        'n_with_keywords': n_with_keywords,
        'n_with_subjects': n_with_subjects,
        'n_enriched': n_enriched,
        'dir': os.path.basename(issue_dir),
    }


def pct(n, total):
    if total == 0:
        return '-'
    return f'{100 * n // total}%'


def main():
    parser = argparse.ArgumentParser(description='Generate archive manifest')
    parser.add_argument('toc_files', nargs='+', help='toc.json files')
    parser.add_argument('-o', '--output', default='backfill/private/output/MANIFEST.md',
                        help='Output path (default: backfill/private/output/MANIFEST.md)')
    args = parser.parse_args()

    issues = []
    for toc_path in args.toc_files:
        if not os.path.exists(toc_path):
            print(f"WARNING: {toc_path} not found, skipping", file=sys.stderr)
            continue
        issues.append(load_issue(toc_path))

    if not issues:
        print("No issues found.", file=sys.stderr)
        sys.exit(1)

    # Sort by volume then issue
    issues.sort(key=lambda x: (x['vol'], x['iss']))

    total_articles = sum(i['n_articles'] for i in issues)
    total_enriched = sum(i['n_enriched'] for i in issues)

    lines = []
    lines.append('# Archive Manifest')
    lines.append('')
    lines.append(f'**Issues:** {len(issues)}  ')
    lines.append(f'**Articles:** {total_articles}  ')
    lines.append(f'**Enriched:** {total_enriched} ({pct(total_enriched, total_articles)})')
    lines.append('')
    lines.append('| Vol | Iss | Date | Articles | Keywords | Subjects | Enriched |')
    lines.append('|-----|-----|------|----------|----------|----------|----------|')

    gaps = []
    for i in issues:
        kw_pct = pct(i['n_with_keywords'], i['n_articles'])
        subj_pct = pct(i['n_with_subjects'], i['n_articles'])
        enr_pct = pct(i['n_enriched'], i['n_articles'])
        lines.append(f"| {i['vol']} | {i['iss']} | {i['date']} | {i['n_articles']} "
                     f"| {kw_pct} | {subj_pct} | {enr_pct} |")
        # Flag low coverage
        if i['n_articles'] > 0:
            kw_ratio = i['n_with_keywords'] / i['n_articles']
            if kw_ratio < 0.5:
                gaps.append(f"- Vol {i['vol']}.{i['iss']}: only {kw_pct} keyword coverage")

    if gaps:
        lines.append('')
        lines.append('## Gaps')
        lines.append('')
        lines.extend(gaps)

    lines.append('')

    output = '\n'.join(lines)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w') as f:
        f.write(output)

    print(f"Manifest: {len(issues)} issues, {total_articles} articles -> {args.output}", file=sys.stderr)


if __name__ == '__main__':
    main()
