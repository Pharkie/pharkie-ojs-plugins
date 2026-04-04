#!/usr/bin/env python3
"""
Poll Crossref SBMV (Search-Based Matching with Validation) for resolved refs.

For each deposited article, calls getResolvedRefs to check if Crossref's
independent matching found DOIs we missed. Compares against doi_matches.json
and reports new matches.

Requires Crossref member credentials (username/password).

Usage:
    # Single volume
    python3 backfill/html_pipeline/pipe4c_poll_sbmv.py \
        --volume 20.1 --username USER --password PASS

    # All volumes
    python3 backfill/html_pipeline/pipe4c_poll_sbmv.py \
        --username USER --password PASS

    # Dry run (just show what would be polled)
    python3 backfill/html_pipeline/pipe4c_poll_sbmv.py \
        --volume 20.1 --dry-run

    # Accept SBMV matches into doi_matches.json + JATS
    python3 backfill/html_pipeline/pipe4c_poll_sbmv.py \
        --username USER --password PASS --accept
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'private' / 'output'
MATCHES_FILENAME = 'doi_matches.json'

RESOLVED_REFS_URL = 'https://doi.crossref.org/getResolvedRefs'
# Polite delay between API calls (seconds)
POLL_DELAY = 0.5


def load_article_dois(vol_dir):
    """Load article DOIs from JATS files in a volume directory.

    Returns list of dicts: {slug, doi, jats_path, ref_count}.
    """
    articles = []
    for jats_path in sorted(vol_dir.glob('*.jats.xml')):
        tree = ET.parse(jats_path)
        doi_el = tree.find('.//article-id[@pub-id-type="doi"]')
        if doi_el is None or not doi_el.text:
            continue

        slug = jats_path.stem.replace('.jats', '')
        ref_count = len(tree.findall('.//ref-list/ref'))
        articles.append({
            'slug': slug,
            'doi': doi_el.text.strip(),
            'jats_path': jats_path,
            'ref_count': ref_count,
        })
    return articles


def load_jats_ref_keys(jats_path):
    """Load ref keys and their citation text from JATS.

    The deposit XML uses ref id attributes (ref1, ref2...) as citation keys.
    Returns dict: ref_id (str) -> {ref_id, text, has_doi, existing_doi}.
    """
    tree = ET.parse(jats_path)
    refs = {}
    for i, ref_el in enumerate(tree.findall('.//ref-list/ref'), start=1):
        ref_id = ref_el.get('id', f'ref{i}')
        mc = ref_el.find('mixed-citation')
        text = mc.text.strip() if mc is not None and mc.text else ''
        pub_id = ref_el.find("pub-id[@pub-id-type='doi']")
        refs[ref_id] = {
            'ref_id': ref_id,
            'text': text,
            'has_doi': pub_id is not None,
            'existing_doi': pub_id.text.strip() if pub_id is not None else None,
        }
    return refs


def poll_resolved_refs(article_doi, username, password):
    """Poll Crossref getResolvedRefs for one article.

    Returns list of {key, doi, type} dicts, or None on error.
    """
    params = {
        'doi': article_doi,
        'usr': username,
        'pwd': password,
    }
    try:
        resp = requests.post(
            RESOLVED_REFS_URL,
            params=params,
            timeout=30,
        )
        if resp.status_code == 404:
            # Article not deposited or no refs
            return []
        resp.raise_for_status()
        data = resp.json()
        return data.get('matched-references', [])
    except requests.RequestException as e:
        print(f'  WARNING: getResolvedRefs failed for {article_doi}: {e}',
              file=sys.stderr)
        return None
    except (ValueError, KeyError) as e:
        print(f'  WARNING: bad response for {article_doi}: {e}',
              file=sys.stderr)
        return None


def load_our_matches(vol_dir, slug):
    """Load our doi_matches.json results for one article.

    Returns dict: ref_id -> {tier, matched_doi}.
    """
    matches_path = vol_dir / MATCHES_FILENAME
    if not matches_path.exists():
        return {}
    with open(matches_path) as f:
        data = json.load(f)

    article_data = data.get('articles', {}).get(slug, {})
    result = {}
    for ref in article_data.get('refs', []):
        ref_id = ref.get('ref_id', '')
        result[ref_id] = {
            'tier': ref.get('tier', ''),
            'matched_doi': ref.get('matched_doi'),
        }
    return result


def compare_results(sbmv_refs, jats_refs, our_matches):
    """Compare SBMV results against our matches.

    Returns dict with categorised results:
    - agree: both found same DOI
    - sbmv_extra: SBMV found DOI we didn't
    - ours_extra: we found DOI, SBMV didn't
    - neither: neither found a DOI
    - sbmv_different: both found DOI but different ones
    """
    # Build SBMV lookup: key -> doi
    sbmv_by_key = {r['key']: r.get('doi', '') for r in sbmv_refs}

    results = {
        'agree': [],
        'sbmv_extra': [],
        'ours_extra': [],
        'neither': [],
        'sbmv_different': [],
    }

    for key, jats_ref in jats_refs.items():
        ref_id = jats_ref['ref_id']
        our = our_matches.get(ref_id, {})
        our_tier = our.get('tier', '')
        # Only count DOIs we actually accepted (matched/already_has_doi),
        # not rejected candidates stored in no_match entries
        if our_tier in ('matched', 'already_has_doi', 'sbmv_matched'):
            our_doi = our.get('matched_doi') or jats_ref.get('existing_doi')
        else:
            our_doi = jats_ref.get('existing_doi')
        sbmv_doi = sbmv_by_key.get(key, '')

        entry = {
            'key': key,
            'ref_id': ref_id,
            'text': jats_ref['text'][:100],
            'our_doi': our_doi,
            'our_tier': our_tier,
            'sbmv_doi': sbmv_doi,
        }

        has_ours = bool(our_doi)
        has_sbmv = bool(sbmv_doi)

        if has_ours and has_sbmv:
            if our_doi.lower() == sbmv_doi.lower():
                results['agree'].append(entry)
            else:
                results['sbmv_different'].append(entry)
        elif has_sbmv and not has_ours:
            results['sbmv_extra'].append(entry)
        elif has_ours and not has_sbmv:
            results['ours_extra'].append(entry)
        else:
            results['neither'].append(entry)

    return results


def write_sbmv_to_jats(jats_path, sbmv_extras):
    """Write SBMV-discovered DOIs to JATS as <pub-id> elements.

    Only writes for refs that don't already have a <pub-id>.
    Returns count of DOIs written.
    """
    tree = ET.parse(jats_path)
    root = tree.getroot()
    written = 0

    # Build lookup: ref_id -> doi
    extra_by_ref_id = {e['ref_id']: e['sbmv_doi'] for e in sbmv_extras}

    for ref_el in root.findall('.//ref-list/ref'):
        ref_id = ref_el.get('id', '')
        doi = extra_by_ref_id.get(ref_id)
        if not doi:
            continue
        existing = ref_el.find("pub-id[@pub-id-type='doi']")
        if existing is not None:
            continue
        pub_id = ET.SubElement(ref_el, 'pub-id')
        pub_id.set('pub-id-type', 'doi')
        pub_id.text = doi
        written += 1

    if written > 0:
        tree.write(jats_path, encoding='unicode', xml_declaration=True)

    return written


def update_doi_matches(vol_dir, slug, sbmv_extras):
    """Update doi_matches.json with SBMV-discovered DOIs."""
    matches_path = vol_dir / MATCHES_FILENAME
    if not matches_path.exists():
        return

    with open(matches_path) as f:
        data = json.load(f)

    article_data = data.get('articles', {}).get(slug)
    if not article_data:
        return

    extra_by_ref_id = {e['ref_id']: e['sbmv_doi'] for e in sbmv_extras}

    for ref in article_data.get('refs', []):
        ref_id = ref.get('ref_id', '')
        doi = extra_by_ref_id.get(ref_id)
        if doi and ref.get('tier') == 'no_match':
            ref['tier'] = 'sbmv_matched'
            ref['matched_doi'] = doi
            ref['sbmv_matched_at'] = datetime.now(timezone.utc).isoformat()
            ref['written_to_jats'] = False

    # Update stats
    all_refs = [r for art in data.get('articles', {}).values()
                for r in art.get('refs', [])]
    data['stats'] = {
        'total': len(all_refs),
        'matched': sum(1 for r in all_refs if r.get('tier') == 'matched'),
        'sbmv_matched': sum(1 for r in all_refs
                            if r.get('tier') == 'sbmv_matched'),
        'no_match': sum(1 for r in all_refs if r.get('tier') == 'no_match'),
        'already_has_doi': sum(1 for r in all_refs
                               if r.get('tier') == 'already_has_doi'),
    }

    with open(matches_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description='Poll Crossref SBMV for resolved references')
    parser.add_argument('--volume',
                        help='Volume/issue directory (e.g., 20.1). '
                             'Omit for all volumes.')
    parser.add_argument('--username',
                        default=os.environ.get('OJS_CROSSREF_USERNAME'),
                        help='Crossref member username '
                             '(or set OJS_CROSSREF_USERNAME)')
    parser.add_argument('--password',
                        default=os.environ.get('OJS_CROSSREF_PASSWORD'),
                        help='Crossref member password '
                             '(or set OJS_CROSSREF_PASSWORD)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be polled without calling API')
    parser.add_argument('--accept', action='store_true',
                        help='Write SBMV-discovered DOIs to JATS and '
                             'doi_matches.json')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print detailed output')

    args = parser.parse_args()

    if not args.dry_run and (not args.username or not args.password):
        print('ERROR: --username and --password required '
              '(or set OJS_CROSSREF_USERNAME / OJS_CROSSREF_PASSWORD)',
              file=sys.stderr)
        sys.exit(1)

    # Find volume directories
    if args.volume:
        vol_dirs = [OUTPUT_DIR / args.volume]
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

    # Totals
    total_articles = 0
    total_refs = 0
    total_agree = 0
    total_sbmv_extra = 0
    total_ours_extra = 0
    total_neither = 0
    total_different = 0
    total_errors = 0
    all_sbmv_extras = []  # for summary

    for vol_dir in vol_dirs:
        articles = load_article_dois(vol_dir)
        if not articles:
            continue

        vol_name = vol_dir.name
        print(f'\n=== {vol_name} ({len(articles)} articles) ===')

        for article in articles:
            slug = article['slug']
            doi = article['doi']
            total_articles += 1

            if args.dry_run:
                print(f'  {slug}: {doi} ({article["ref_count"]} refs)')
                total_refs += article['ref_count']
                continue

            # Load JATS refs and our matches
            jats_refs = load_jats_ref_keys(article['jats_path'])
            our_matches = load_our_matches(vol_dir, slug)
            total_refs += len(jats_refs)

            # Poll Crossref
            time.sleep(POLL_DELAY)
            sbmv_refs = poll_resolved_refs(doi, args.username, args.password)

            if sbmv_refs is None:
                total_errors += 1
                continue

            # Compare
            comparison = compare_results(sbmv_refs, jats_refs, our_matches)

            n_agree = len(comparison['agree'])
            n_sbmv_extra = len(comparison['sbmv_extra'])
            n_ours_extra = len(comparison['ours_extra'])
            n_neither = len(comparison['neither'])
            n_different = len(comparison['sbmv_different'])

            total_agree += n_agree
            total_sbmv_extra += n_sbmv_extra
            total_ours_extra += n_ours_extra
            total_neither += n_neither
            total_different += n_different

            # Only print articles with interesting results
            if n_sbmv_extra > 0 or n_different > 0 or args.verbose:
                status_parts = []
                if n_agree:
                    status_parts.append(f'agree={n_agree}')
                if n_sbmv_extra:
                    status_parts.append(f'SBMV_EXTRA={n_sbmv_extra}')
                if n_ours_extra:
                    status_parts.append(f'ours={n_ours_extra}')
                if n_neither:
                    status_parts.append(f'neither={n_neither}')
                if n_different:
                    status_parts.append(f'DIFFERENT={n_different}')
                print(f'  {slug}: {", ".join(status_parts)}')

            # Detail SBMV extras
            for extra in comparison['sbmv_extra']:
                extra['vol'] = vol_name
                extra['slug'] = slug
                all_sbmv_extras.append(extra)
                print(f'    NEW: [{extra["ref_id"]}] {extra["sbmv_doi"]}')
                print(f'         {extra["text"]}')

            for diff in comparison['sbmv_different']:
                print(f'    DIFF: [{diff["ref_id"]}] '
                      f'ours={diff["our_doi"]} vs sbmv={diff["sbmv_doi"]}')
                print(f'          {diff["text"]}')

            # Accept SBMV extras
            if args.accept and comparison['sbmv_extra']:
                written = write_sbmv_to_jats(
                    article['jats_path'], comparison['sbmv_extra'])
                update_doi_matches(vol_dir, slug, comparison['sbmv_extra'])
                if written:
                    print(f'    WROTE {written} DOIs to JATS + doi_matches')

    # Summary
    print(f'\n{"=" * 60}')
    print(f'SUMMARY')
    print(f'{"=" * 60}')
    print(f'Articles polled:    {total_articles}')
    print(f'Total references:   {total_refs}')
    if not args.dry_run:
        print(f'Both agree:         {total_agree}')
        print(f'SBMV found extra:   {total_sbmv_extra}')
        print(f'We found extra:     {total_ours_extra}')
        print(f'Neither matched:    {total_neither}')
        if total_different:
            print(f'Different DOI:      {total_different}')
        if total_errors:
            print(f'Errors:             {total_errors}')

    if all_sbmv_extras:
        print(f'\n--- New SBMV matches ({len(all_sbmv_extras)}) ---')
        for extra in all_sbmv_extras:
            print(f'  {extra["vol"]}/{extra["slug"]} [{extra["ref_id"]}]')
            print(f'    DOI: {extra["sbmv_doi"]}')
            print(f'    Ref: {extra["text"]}')
            print()

        if not args.accept:
            print('Run with --accept to write these DOIs to JATS + '
                  'doi_matches.json')


if __name__ == '__main__':
    main()
