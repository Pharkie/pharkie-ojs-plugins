#!/usr/bin/env python3
"""Validate toc.json schema and auto-fill missing page numbers.

toc.json is authored manually (Claude reads the issue PDF). Both the
split pipeline and html pipeline consume it. Run this before either.

Usage:
    python3 backfill/validate_toc.py backfill/private/output/10.1/toc.json
    python3 backfill/validate_toc.py backfill/private/output/*/toc.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_ISSUE_FIELDS = ['volume', 'date', 'articles']
REQUIRED_ARTICLE_FIELDS = ['title', 'authors', 'section', 'pdf_page_start', 'pdf_page_end']

# Optional per-article access override. Access normally follows the section,
# but an individual article can be opened or paywalled against that default --
# an obituary under Articles that the editors want everyone to read, say.
VALID_ACCESS = {'open', 'subscription', 'paywalled'}


def validate_toc(toc_path: Path) -> list[str]:
    """Validate a toc.json file. Returns list of error strings (empty = valid)."""
    errors = []

    try:
        with open(toc_path) as f:
            toc = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return [f'{toc_path}: {e}']

    for field in REQUIRED_ISSUE_FIELDS:
        if field not in toc:
            errors.append(f'{toc_path}: missing required field "{field}"')

    articles = toc.get('articles', [])
    if not articles:
        errors.append(f'{toc_path}: no articles')

    for i, article in enumerate(articles):
        prefix = f'{toc_path}: article[{i}]'
        for field in REQUIRED_ARTICLE_FIELDS:
            if field not in article:
                errors.append(f'{prefix}: missing "{field}"')

        # authors must be a string
        if 'authors' in article and not isinstance(article['authors'], str):
            errors.append(f'{prefix}: "authors" must be a string, got {type(article["authors"]).__name__}')

        # Per-article access override. Reject anything unrecognised rather
        # than falling back to the section default: the failure mode of a
        # typo here is an article the editors meant to open staying behind
        # the paywall, which nothing downstream would flag.
        if 'access' in article:
            access = article['access']
            if not isinstance(access, str) or access.strip().lower() not in VALID_ACCESS:
                errors.append(
                    f'{prefix}: "access" must be one of '
                    f'{", ".join(sorted(VALID_ACCESS))} (got {access!r}); '
                    f'omit it to follow the section default'
                )

        # Per-article ORCID map: full author name (as written in "authors")
        # -> canonical ORCID URL. Keys are checked against the authors string
        # so a misspelt name fails here rather than silently emitting nothing.
        if 'orcids' in article:
            orcids = article['orcids']
            if not isinstance(orcids, dict):
                errors.append(f'{prefix}: "orcids" must be an object of '
                              f'{{author name: ORCID URL}}, got {type(orcids).__name__}')
            else:
                for name, url in orcids.items():
                    if name not in article.get('authors', ''):
                        errors.append(f'{prefix}: orcids key "{name}" not found in '
                                      f'authors "{article.get("authors", "")}"')
                    if not isinstance(url, str) or not re.fullmatch(
                            r'https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]', url):
                        errors.append(f'{prefix}: orcid for "{name}" must be the full '
                                      f'canonical URL https://orcid.org/XXXX-XXXX-XXXX-XXXX (got {url!r})')

        # Page range sanity
        start = article.get('pdf_page_start')
        end = article.get('pdf_page_end')
        if isinstance(start, int) and isinstance(end, int) and start > end:
            errors.append(f'{prefix}: pdf_page_start ({start}) > pdf_page_end ({end})')

        # Book review metadata
        section = article.get('section', '')
        if section in ('Book Reviews', 'Book Review'):
            # "/" in title = multi-book review, no individual book metadata expected
            is_multi = '/' in article.get('title', '')
            if not is_multi:
                for field in ('book_title', 'book_author', 'book_year'):
                    if not article.get(field):
                        errors.append(f'{prefix}: book review missing "{field}"')
                pub = article.get('publisher', '')
                if pub and pub.rstrip().endswith(':'):
                    errors.append(f'{prefix}: publisher "{pub}" looks truncated (missing name after city)')

    return errors


def main():
    parser = argparse.ArgumentParser(description='Validate toc.json files')
    parser.add_argument('toc_files', nargs='+', help='toc.json file(s)')
    args = parser.parse_args()

    total_errors = 0
    for toc_file in sorted(args.toc_files):
        errors = validate_toc(Path(toc_file))
        for err in errors:
            print(f'ERROR: {err}', file=sys.stderr)
        total_errors += len(errors)

    if total_errors:
        print(f'\n{total_errors} error(s) found', file=sys.stderr)
        sys.exit(1)
    else:
        print(f'All {len(args.toc_files)} toc.json file(s) valid')


if __name__ == '__main__':
    main()
