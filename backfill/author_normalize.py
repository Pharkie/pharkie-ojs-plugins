#!/usr/bin/env python3
"""
Step 3: Author name normalization.

Builds and maintains an author registry (authors.json) that maps every
extracted name variant to a canonical form. Ensures consistent author
names across 30 years of journal issues before OJS import.

Usage:
    # Process one or more TOC JSON files, update registry
    python backfill/author_normalize.py toc1.json toc2.json ...

    # Process all TOC JSONs in the output directory
    python backfill/author_normalize.py backfill/output/*/toc.json

    # Just show the current registry stats
    python backfill/author_normalize.py --stats

Registry file: backfill/authors.json
- Checked into git, grows as you process issues
- Each entry maps a canonical name to known variants
- Ambiguous matches are flagged for human review in authors-review.json
"""

import sys
import os
import re
import json
import argparse
import unicodedata
from difflib import SequenceMatcher


REGISTRY_PATH = os.path.join(os.path.dirname(__file__), 'authors.json')
REVIEW_PATH = os.path.join(os.path.dirname(__file__), 'authors-review.json')


def normalize_key(name):
    """Create a normalized lookup key from a name.

    Strips accents, lowercases, collapses whitespace.
    'Emmy van Deurzen' -> 'emmy van deurzen'
    'Aleksandar Dimitrijević' -> 'aleksandar dimitrijevic'
    """
    # Strip accents
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_name = ''.join(c for c in nfkd if not unicodedata.combining(c))
    # Lowercase, collapse whitespace
    return ' '.join(ascii_name.lower().split())


def extract_surname(name):
    """Extract likely surname from a full name.

    Handles particles: van, de, du, von, di, etc.
    'Emmy van Deurzen' -> 'van deurzen'
    'Kim Loliya' -> 'loliya'
    'Michael R. Montgomery' -> 'montgomery'
    """
    words = name.split()
    if len(words) <= 1:
        return name.lower()

    particles = {'van', 'de', 'du', 'von', 'di', 'la', 'le', 'el', 'dos', 'das'}
    for i in range(1, len(words)):
        if words[i].lower() in particles:
            return ' '.join(words[i:]).lower()
    return words[-1].lower()


def extract_first_initial(name):
    """Get first initial from a name. 'Emmy van Deurzen' -> 'e'."""
    words = name.split()
    if words:
        return words[0][0].lower()
    return ''


def similarity(a, b):
    """String similarity ratio (0-1)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class AuthorRegistry:
    """Maintains canonical author names and their variants."""

    def __init__(self, path=REGISTRY_PATH):
        self.path = path
        self.entries = {}  # canonical_name -> {variants: [], articles: int}
        self._key_index = {}  # normalized_key -> canonical_name
        self._surname_index = {}  # surname -> [canonical_names]
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.entries = json.load(f)
            self._rebuild_index()

    def save(self):
        with open(self.path, 'w') as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False, sort_keys=True)

    def _rebuild_index(self):
        self._key_index = {}
        self._surname_index = {}
        for canonical, data in self.entries.items():
            key = normalize_key(canonical)
            self._key_index[key] = canonical
            for variant in data.get('variants', []):
                self._key_index[normalize_key(variant)] = canonical

            surname = extract_surname(canonical)
            if surname not in self._surname_index:
                self._surname_index[surname] = []
            self._surname_index[surname].append(canonical)

    def lookup(self, raw_name):
        """Look up a name in the registry.

        Returns: (canonical_name, match_type) where match_type is:
            'exact' - exact match (after normalization)
            'fuzzy' - high-confidence fuzzy match
            'ambiguous' - multiple possible matches, needs human review
            'new' - not in registry
        """
        if not raw_name or not raw_name.strip():
            return None, 'empty'

        raw_name = raw_name.strip()
        key = normalize_key(raw_name)

        # Exact match (normalized)
        if key in self._key_index:
            return self._key_index[key], 'exact'

        # Surname + first initial match
        surname = extract_surname(raw_name)
        initial = extract_first_initial(raw_name)
        candidates = []

        if surname in self._surname_index:
            for canonical in self._surname_index[surname]:
                canon_initial = extract_first_initial(canonical)
                if initial == canon_initial:
                    candidates.append(canonical)

        if len(candidates) == 1:
            return candidates[0], 'fuzzy'
        if len(candidates) > 1:
            return candidates, 'ambiguous'

        # Fuzzy match across all names (for typos)
        best_match = None
        best_score = 0
        for canonical in self.entries:
            score = similarity(key, normalize_key(canonical))
            if score > best_score:
                best_score = score
                best_match = canonical

        if best_score >= 0.85:
            return best_match, 'fuzzy'

        return raw_name, 'new'

    def add(self, canonical_name, variant=None):
        """Add a new canonical name or a variant of an existing one."""
        if canonical_name not in self.entries:
            self.entries[canonical_name] = {
                'variants': [],
                'articles': 0,
            }

        if variant and variant != canonical_name:
            key = normalize_key(variant)
            canon_key = normalize_key(canonical_name)
            if key != canon_key and variant not in self.entries[canonical_name]['variants']:
                self.entries[canonical_name]['variants'].append(variant)

        self._rebuild_index()

    def increment(self, canonical_name):
        """Increment article count for an author."""
        if canonical_name in self.entries:
            self.entries[canonical_name]['articles'] = \
                self.entries[canonical_name].get('articles', 0) + 1

    def stats(self):
        total = len(self.entries)
        with_variants = sum(1 for e in self.entries.values() if e.get('variants'))
        total_variants = sum(len(e.get('variants', [])) for e in self.entries.values())
        total_articles = sum(e.get('articles', 0) for e in self.entries.values())
        return {
            'authors': total,
            'with_variants': with_variants,
            'total_variants': total_variants,
            'total_articles': total_articles,
        }


def split_multiple_authors(author_string):
    """Split 'Sheba Boakye-Duah & Neresia Osbourne' into individual names."""
    if not author_string:
        return []
    parts = [a.strip() for a in author_string.split('&')]
    return [p for p in parts if p]


def process_toc(toc_path, registry):
    """Process a TOC JSON file, normalizing all author names.

    Returns list of issues needing human review.
    """
    with open(toc_path) as f:
        toc = json.load(f)

    review_items = []
    changes = 0

    vol = toc.get('volume', '?')
    iss = toc.get('issue', '?')
    print(f"\nProcessing Vol {vol}.{iss} ({len(toc['articles'])} articles)", file=sys.stderr)

    for article in toc['articles']:
        raw_authors = article.get('authors', '')
        if not raw_authors:
            continue

        names = split_multiple_authors(raw_authors)
        normalized_names = []

        for name in names:
            canonical, match_type = registry.lookup(name)

            if match_type == 'exact':
                normalized_names.append(canonical)
                registry.increment(canonical)

            elif match_type == 'fuzzy':
                print(f"  FUZZY: '{name}' -> '{canonical}'", file=sys.stderr)
                registry.add(canonical, variant=name)
                normalized_names.append(canonical)
                registry.increment(canonical)
                changes += 1

            elif match_type == 'ambiguous':
                print(f"  AMBIGUOUS: '{name}' matches {canonical}", file=sys.stderr)
                review_items.append({
                    'raw_name': name,
                    'candidates': canonical,
                    'article': article['title'],
                    'source': f"Vol {vol}.{iss}",
                })
                # Use raw name for now
                normalized_names.append(name)

            elif match_type == 'new':
                registry.add(name)
                normalized_names.append(name)
                registry.increment(name)
                changes += 1

        # Update the article with normalized names
        new_authors = ' & '.join(normalized_names)
        if new_authors != raw_authors:
            article['authors_original'] = raw_authors
            article['authors'] = new_authors

    # Save updated TOC
    with open(toc_path, 'w') as f:
        json.dump(toc, f, indent=2, ensure_ascii=False)

    print(f"  {changes} new/updated entries, {len(review_items)} need review", file=sys.stderr)
    return review_items


def main():
    parser = argparse.ArgumentParser(description='Normalize author names across issues')
    parser.add_argument('toc_files', nargs='*', help='TOC JSON files to process')
    parser.add_argument('--stats', action='store_true', help='Show registry statistics')
    parser.add_argument('--list', action='store_true', help='List all authors in registry')
    parser.add_argument('--registry', default=REGISTRY_PATH,
                        help=f'Path to registry file (default: {REGISTRY_PATH})')
    args = parser.parse_args()

    registry = AuthorRegistry(args.registry)

    if args.stats:
        s = registry.stats()
        print(f"Authors: {s['authors']}")
        print(f"With variants: {s['with_variants']}")
        print(f"Total variants: {s['total_variants']}")
        print(f"Total article appearances: {s['total_articles']}")
        return

    if args.list:
        for name, data in sorted(registry.entries.items()):
            variants = data.get('variants', [])
            count = data.get('articles', 0)
            v_str = f" (also: {', '.join(variants)})" if variants else ''
            print(f"  {name} [{count} articles]{v_str}")
        return

    if not args.toc_files:
        print("No TOC files specified. Use --stats to see registry, or provide TOC JSON files.",
              file=sys.stderr)
        sys.exit(1)

    all_review = []
    for toc_path in args.toc_files:
        if not os.path.exists(toc_path):
            print(f"WARNING: {toc_path} not found, skipping", file=sys.stderr)
            continue
        review = process_toc(toc_path, registry)
        all_review.extend(review)

    registry.save()
    print(f"\nRegistry saved to {args.registry}", file=sys.stderr)

    s = registry.stats()
    print(f"Total: {s['authors']} authors, {s['total_variants']} variants,"
          f" {s['total_articles']} article appearances", file=sys.stderr)

    if all_review:
        with open(REVIEW_PATH, 'w') as f:
            json.dump(all_review, f, indent=2, ensure_ascii=False)
        print(f"\n{len(all_review)} items need review -> {REVIEW_PATH}", file=sys.stderr)
        print("Edit that file to resolve ambiguous matches, then re-run.", file=sys.stderr)


if __name__ == '__main__':
    main()
