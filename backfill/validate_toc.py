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
import sys
from pathlib import Path

REQUIRED_ISSUE_FIELDS = ['volume', 'date_published', 'articles']
REQUIRED_ARTICLE_FIELDS = ['title', 'authors', 'section', 'pdf_page_start', 'pdf_page_end']


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
