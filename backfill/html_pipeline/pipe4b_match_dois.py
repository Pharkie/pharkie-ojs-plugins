#!/usr/bin/env python3
"""
Match extracted references against Crossref DOIs.

Reads references from JATS XML <ref-list> elements, queries the Crossref
bibliographic search API, writes results to doi_matches.json, and adds
matched DOIs as <pub-id> elements in JATS.

Usage:
    # Single article
    python3 backfill/html_pipeline/pipe4b_match_dois.py \\
        --volume 35.1 --article 02-who-do-we-think-we-are \\
        --verbose --email user@example.com

    # Full issue
    python3 backfill/html_pipeline/pipe4b_match_dois.py \\
        --volume 35.1 --verbose --email user@example.com

    # Dry run (query only, don't write)
    python3 backfill/html_pipeline/pipe4b_match_dois.py \\
        --volume 35.1 --dry-run --verbose --email user@example.com
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

# Allow imports from backfill root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.crossref import (
    DEFAULT_DELAY,
    OWN_DOI_PREFIX,
    TIER_MATCHED,
    TIER_NO_MATCH,
    has_existing_doi,
    query_crossref,
    score_match,
    strip_doi_from_text,
)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'private' / 'output'
MATCHES_FILENAME = 'doi_matches.json'


def load_refs_from_jats(jats_path):
    """Load references from a JATS XML file.

    Returns list of dicts with 'ref_id' and 'text' keys.
    """
    tree = ET.parse(jats_path)
    refs = []
    for ref_el in tree.findall('.//ref-list/ref'):
        ref_id = ref_el.get('id', '')
        mc = ref_el.find('mixed-citation')
        if mc is not None and mc.text and mc.text.strip():
            # Skip if already has a <pub-id> DOI sibling
            pub_id = ref_el.find("pub-id[@pub-id-type='doi']")
            refs.append({
                'ref_id': ref_id,
                'text': mc.text.strip(),
                'has_pub_id': pub_id is not None,
                'existing_doi': pub_id.text if pub_id is not None else None,
            })
    return refs


def load_existing_matches(vol_dir):
    """Load existing doi_matches.json if present, for caching."""
    matches_path = vol_dir / MATCHES_FILENAME
    if matches_path.exists():
        with open(matches_path) as f:
            return json.load(f)
    return None


def _get_cached_ref(existing, article_slug, ref_text):
    """Look up a previously matched ref in cached results by text.

    Matches on reference TEXT, not ref_id. ref_id is a positional index
    that changes when pipe3+pipe4 re-extract citations (reordering,
    adding/removing refs). Text matching is resilient to this.
    """
    if existing is None:
        return None
    articles = existing.get('articles', {})
    article_data = articles.get(article_slug)
    if article_data is None:
        return None
    for ref in article_data.get('refs', []):
        if ref.get('text') == ref_text:
            return ref
    return None


def process_article(jats_path, email, article_slug, limit=None,
                    verbose=False, delay=DEFAULT_DELAY, existing_matches=None,
                    revalidate=False):
    """Process all references in one article's JATS file.

    Returns list of result dicts for each reference.
    """
    refs = load_refs_from_jats(jats_path)
    if not refs:
        if verbose:
            print(f"  No references found in {jats_path.name}")
        return []

    if limit:
        refs = refs[:limit]

    results = []
    for i, ref in enumerate(refs):
        ref_id = ref['ref_id']
        text = ref['text']

        # Skip if already has <pub-id> DOI in JATS (unless --revalidate).
        # Validate: the DOI must appear in the ref text or be an own-prefix
        # DOI. A <pub-id> with a DOI not found in the text is a leftover
        # from a previous buggy run — don't trust it.
        if ref['has_pub_id'] and not revalidate:
            existing_doi = ref['existing_doi']
            doi_in_text = existing_doi and (
                existing_doi in text
                or f'doi.org/{existing_doi}' in text
                or text.startswith(OWN_DOI_PREFIX if hasattr(existing_doi, 'startswith') else '')
            )
            if doi_in_text:
                results.append({
                    'ref_id': ref_id,
                    'text': text,
                    'tier': 'already_has_doi',
                    'matched_doi': existing_doi,
                    'written_to_jats': True,
                })
                continue
            # DOI in JATS doesn't match ref text — ignore it, re-match below

        # DOI already in reference text — extract it and write to JATS
        # as structured data (pub-id), skip Crossref query
        existing_doi = has_existing_doi(text)
        if existing_doi:
            # When revalidating, re-query external DOIs (may be wrong from
            # OCR or previous runs). Own-prefix DOIs are authoritative.
            if revalidate and not existing_doi.startswith(OWN_DOI_PREFIX):
                if verbose:
                    print(f"  [{ref_id}] REVALIDATING text DOI: "
                          f"{existing_doi}")
            else:
                if verbose:
                    print(f"  [{ref_id}] EXTRACTED from text: {existing_doi}")
                results.append({
                    'ref_id': ref_id,
                    'text': text,
                    'tier': 'already_has_doi',
                    'matched_doi': existing_doi,
                    'written_to_jats': False,
                })
                continue

        # Check cache (bypass when revalidating — need fresh Crossref queries).
        # All tiers are cached including no_match — only --revalidate re-queries.
        cached = _get_cached_ref(existing_matches, article_slug, text)
        if cached and not revalidate:
            if verbose:
                print(f"  [{ref_id}] CACHED ({cached['tier']}: "
                      f"{cached.get('matched_doi', 'N/A')})")
            results.append(cached)
            continue

        # Query Crossref
        if i > 0:
            time.sleep(delay)

        if verbose:
            print(f"  [{ref_id}] Querying: {text[:80]}...")

        cr_results = query_crossref(text, email)

        if not cr_results:
            if verbose:
                print(f"  [{ref_id}] NO RESULTS")
            results.append({
                'ref_id': ref_id,
                'text': text,
                'tier': TIER_NO_MATCH,
                'matched_doi': None,
                'written_to_jats': False,
            })
            continue

        # Score all candidates, pick best match.
        # Ranking: matched tier first, then self-citation preference,
        # then by similarity, then Crossref score.
        _TIER_RANK = {TIER_MATCHED: 1, TIER_NO_MATCH: 0}
        best_rank = (-1, -1, -1, -1)
        best_tier = TIER_NO_MATCH
        best_sim = 0
        best_details = {}
        for candidate in cr_results:
            t, sim, det = score_match(candidate, text)
            cr_score = det.get('crossref_score', 0)
            self_cite = 1 if det.get('self_citation_boost') else 0
            rank = (_TIER_RANK.get(t, 0), self_cite, sim, cr_score)
            if rank > best_rank:
                best_rank = rank
                best_tier = t
                best_sim = sim
                best_details = det

        tier = best_tier
        similarity = best_sim
        details = best_details

        result = {
            'ref_id': ref_id,
            'text': text,
            'tier': tier,
            'written_to_jats': False,
            **details,
        }
        results.append(result)

        if verbose:
            doi = details.get('matched_doi', 'N/A')
            cr_title = details.get('crossref_title', 'N/A')
            cr_score = details.get('crossref_score', 0)
            print(f"  [{ref_id}] {tier.upper()} "
                  f"(score={cr_score:.1f}, sim={similarity:.2f})")
            print(f"           DOI: {doi}")
            print(f"           Crossref title: {cr_title}")
            print()

    return results


def write_matches_json(vol_dir, all_results, email):
    """Write doi_matches.json to the volume directory."""
    # Compute stats
    all_refs = [r for refs in all_results.values() for r in refs]
    stats = {
        'total': len(all_refs),
        'matched': sum(1 for r in all_refs if r['tier'] == TIER_MATCHED),
        'no_match': sum(1 for r in all_refs if r['tier'] == TIER_NO_MATCH),
        'already_has_doi': sum(
            1 for r in all_refs if r['tier'] == 'already_has_doi'),
    }

    output = {
        'matched_at': datetime.now(timezone.utc).isoformat(),
        'email': email,
        'stats': stats,
        'articles': {
            slug: {'refs': refs}
            for slug, refs in all_results.items()
        },
    }

    matches_path = vol_dir / MATCHES_FILENAME
    with open(matches_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {matches_path}")
    print(f"  Total: {stats['total']}, Matched: {stats['matched']}, "
          f"No match: {stats['no_match']}, "
          f"Already has DOI: {stats['already_has_doi']}")


def write_dois_to_jats(jats_path, refs):
    """Write matched DOIs to JATS XML as <pub-id> siblings of <mixed-citation>.

    Also removes stale <pub-id> elements for refs that are now no_match
    (from --revalidate runs).
    """
    tree = ET.parse(jats_path)
    root = tree.getroot()
    written = 0
    removed = 0

    # Build set of ref_ids that should have DOIs
    matched_refs = {}
    no_match_refs = set()
    for ref_data in refs:
        if ref_data['tier'] in (TIER_MATCHED, 'already_has_doi'):
            matched_refs[ref_data['ref_id']] = ref_data.get('matched_doi')
        elif ref_data['tier'] == TIER_NO_MATCH:
            no_match_refs.add(ref_data['ref_id'])

    for ref_el in root.findall('.//ref-list/ref'):
        ref_id = ref_el.get('id', '')
        existing = ref_el.find("pub-id[@pub-id-type='doi']")

        # Remove stale pub-id for refs that are now no_match
        if ref_id in no_match_refs and existing is not None:
            ref_el.remove(existing)
            removed += 1
            continue

        # Add pub-id for matched refs that don't have one yet
        doi = matched_refs.get(ref_id)
        if doi and existing is None:
            pub_id = ET.SubElement(ref_el, 'pub-id')
            pub_id.set('pub-id-type', 'doi')
            pub_id.text = doi
            written += 1
            # Strip DOI from citation text to avoid double display
            mc = ref_el.find('mixed-citation')
            if mc is not None and mc.text and doi in mc.text:
                mc.text = strip_doi_from_text(mc.text, doi)

    if written > 0 or removed > 0:
        tree.write(jats_path, encoding='unicode', xml_declaration=True)
        parts = []
        if written:
            parts.append(f'{written} written')
        if removed:
            parts.append(f'{removed} removed')
        print(f"  {jats_path.name}: {', '.join(parts)}")

    return written


def main():
    parser = argparse.ArgumentParser(
        description='Match references against Crossref DOIs')
    parser.add_argument('--volume', required=True,
                        help='Volume/issue directory (e.g., 35.1)')
    parser.add_argument('--email', default=os.environ.get('CROSSREF_EMAIL'),
                        help='Email for Crossref polite pool '
                             '(or set CROSSREF_EMAIL env var)')
    parser.add_argument('--article', help='Process only one article slug')
    parser.add_argument('--limit', type=int,
                        help='Limit refs per article (for testing)')
    parser.add_argument('--delay', type=float, default=DEFAULT_DELAY,
                        help=f'Delay between requests (default: {DEFAULT_DELAY}s)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print detailed output for each reference')
    parser.add_argument('--dry-run', action='store_true',
                        help='Query Crossref but don\'t write doi_matches.json or JATS')
    parser.add_argument('--revalidate', action='store_true',
                        help='Re-check existing <pub-id> DOIs against current scoring. '
                             'Removes stale false positives from earlier runs.')

    args = parser.parse_args()

    if not args.email:
        print("ERROR: --email required (or set CROSSREF_EMAIL env var)")
        sys.exit(1)

    vol_dir = OUTPUT_DIR / args.volume
    if not vol_dir.exists():
        print(f"ERROR: Volume directory not found: {vol_dir}")
        sys.exit(1)

    # Find JATS files
    jats_files = sorted(vol_dir.glob('*.jats.xml'))
    if args.article:
        jats_files = [f for f in jats_files if f.stem.startswith(args.article)]
        if not jats_files:
            print(f"ERROR: No JATS file matching '{args.article}' in {vol_dir}")
            sys.exit(1)

    print(f"Processing {len(jats_files)} article(s) in {args.volume}")

    # Load existing matches for caching
    existing_matches = load_existing_matches(vol_dir)

    all_results = {}
    for jats_path in jats_files:
        slug = jats_path.stem.replace('.jats', '')
        print(f"\n--- {slug} ---")

        refs = process_article(
            jats_path, args.email, slug,
            limit=args.limit,
            verbose=args.verbose,
            delay=args.delay,
            existing_matches=existing_matches,
            revalidate=args.revalidate,
        )

        if refs:
            all_results[slug] = refs

    if not all_results:
        print("\nNo references to process.")
        return

    # Write results
    if not args.dry_run:
        write_matches_json(vol_dir, all_results, args.email)

    # Write matched DOIs to JATS
    if not args.dry_run:
        print("\nWriting DOIs to JATS files...")
        total_written = 0
        for jats_path in jats_files:
            slug = jats_path.stem.replace('.jats', '')
            if slug in all_results:
                total_written += write_dois_to_jats(
                    jats_path, all_results[slug],
                )
        print(f"Total DOIs written to JATS: {total_written}")


if __name__ == '__main__':
    main()
