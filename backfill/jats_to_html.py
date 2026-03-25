#!/usr/bin/env python3
"""Generate HTML galley files from JATS XML (source of truth).

JATS → HTML is the correct direction. HTML galleys are a derived format
for OJS display, generated from the canonical JATS representation.

Usage:
    python3 backfill/jats_to_html.py backfill/private/output/10.1/toc.json          # one issue
    python3 backfill/jats_to_html.py backfill/private/output/*/toc.json             # all issues
    python3 backfill/jats_to_html.py backfill/private/output/*/toc.json --dry-run   # count only
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


def jats_to_html(jats_path: Path) -> str | None:
    """Convert JATS article to HTML galley content.

    Renders <body> + selected <back> matter:
    - <fn-group> → "Notes" section (readers need these inline)
    - <bio> → author biographical note
    - <notes type="provenance"> → provenance note
    - <ref-list> → EXCLUDED (OJS renders references from citations table)

    Returns HTML body content (no DOCTYPE/html/head wrapper — OJS adds those).
    Returns None if no body found.
    """
    try:
        tree = ET.parse(jats_path)
    except ET.ParseError:
        return None

    root = tree.getroot()
    body = root.find('.//{*}body')
    if body is None:
        return None

    parts = [_element_to_html(body)]

    # Render back matter (except references)
    back = root.find('.//{*}back')
    if back is not None:
        back_html = _render_back_matter(back)
        if back_html:
            parts.append(back_html)

    return '\n'.join(parts)


def _render_back_matter(back) -> str:
    """Render JATS <back> elements as HTML (except ref-list)."""
    parts = []

    # Notes/endnotes → <h2>Notes</h2> + <ol>
    fn_group = None
    for child in back:
        if _local_name(child.tag) == 'fn-group':
            fn_group = child
            break

    if fn_group is not None:
        notes = []
        for fn in fn_group:
            if _local_name(fn.tag) == 'fn':
                p = fn.find('{*}p') if '}' in fn.tag else fn.find('p')
                if p is None:
                    # Try without namespace
                    for c in fn:
                        if _local_name(c.tag) == 'p':
                            p = c
                            break
                if p is not None:
                    text = _text_content(p)
                    if text.strip():
                        notes.append(text.strip())
        if notes:
            parts.append('\n<h2>Notes</h2>')
            parts.append('<ol>')
            for note in notes:
                parts.append(f'<li>{note}</li>')
            parts.append('</ol>')

    # Author bios
    for child in back:
        if _local_name(child.tag) == 'bio':
            p = child.find('{*}p') if '}' in child.tag else child.find('p')
            if p is None:
                for c in child:
                    if _local_name(c.tag) == 'p':
                        p = c
                        break
            if p is not None:
                text = _text_content(p)
                if text.strip():
                    parts.append(f'\n<p>{text.strip()}</p>')

    # Provenance notes
    for child in back:
        if _local_name(child.tag) == 'notes':
            notes_type = child.get('notes-type', '')
            if notes_type == 'provenance':
                p = child.find('{*}p') if '}' in child.tag else child.find('p')
                if p is None:
                    for c in child:
                        if _local_name(c.tag) == 'p':
                            p = c
                            break
                if p is not None:
                    text = _text_content(p)
                    if text.strip():
                        parts.append(f'\n<p><em>{text.strip()}</em></p>')

    return '\n'.join(parts)


def _element_to_html(element) -> str:
    """Recursively convert a JATS element tree to HTML."""
    tag = _local_name(element.tag)
    parts = []

    if tag == 'body':
        for child in element:
            parts.append(_element_to_html(child))
        return '\n'.join(parts)

    elif tag == 'sec':
        title_el = element.find('{*}title') if '}' in (element.tag or '') else element.find('title')
        if title_el is None:
            title_el = element.find('{*}title')
        title_text = _text_content(title_el) if title_el is not None else ''
        if title_text:
            parts.append(f'<h2>{title_text}</h2>')
        for child in element:
            if _local_name(child.tag) != 'title':
                parts.append(_element_to_html(child))
        return '\n\n'.join(parts)

    elif tag == 'p':
        return f'<p>{_inline_content(element)}</p>'

    elif tag == 'disp-quote':
        inner = []
        for child in element:
            inner.append(_element_to_html(child))
        return f'<blockquote>{"".join(inner)}</blockquote>'

    elif tag == 'list':
        list_type = element.get('list-type', 'bullet')
        html_tag = 'ol' if list_type == 'order' else 'ul'
        items = []
        for item in element:
            if _local_name(item.tag) == 'list-item':
                item_parts = []
                for child in item:
                    item_parts.append(_element_to_html(child))
                # Strip <p> wrapper from single-paragraph list items
                content = ''.join(item_parts)
                content = re.sub(r'^<p>(.*)</p>$', r'\1', content, flags=re.DOTALL)
                items.append(f'<li>{content}</li>')
        return f'<{html_tag}>{"".join(items)}</{html_tag}>'

    elif tag in ('italic', 'bold', 'sup', 'sub'):
        html_map = {'italic': 'em', 'bold': 'strong', 'sup': 'sup', 'sub': 'sub'}
        html_tag = html_map[tag]
        return f'<{html_tag}>{_inline_content(element)}</{html_tag}>'

    elif tag == 'ext-link':
        href = element.get('{http://www.w3.org/1999/xlink}href', '')
        return f'<a href="{href}">{_inline_content(element)}</a>'

    elif tag == 'break':
        return '<br>'

    else:
        # Unknown element — render content only
        return _inline_content(element)


def _inline_content(element) -> str:
    """Get mixed content (text + child elements) as HTML."""
    parts = []
    if element.text:
        parts.append(element.text)
    for child in element:
        parts.append(_element_to_html(child))
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts)


def _text_content(element) -> str:
    """Get plain text content of an element (stripping all tags)."""
    parts = []
    if element.text:
        parts.append(element.text)
    for child in element:
        parts.append(_text_content(child))
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts)


def _local_name(tag: str) -> str:
    """Strip namespace from an element tag."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def process_toc(toc_path: Path, dry_run: bool, verbose: bool) -> Counter:
    """Generate HTML files from JATS for all articles in an issue."""
    import json

    stats = Counter()

    with open(toc_path) as f:
        toc = json.load(f)

    vol_dir = toc_path.parent

    for article in toc.get('articles', []):
        split_pdf = article.get('split_pdf', '')
        slug = Path(split_pdf).stem if split_pdf else ''
        if not slug:
            continue

        jats_path = vol_dir / f'{slug}.jats.xml'
        html_path = vol_dir / f'{slug}.html'

        stats['total'] += 1

        if not jats_path.exists():
            stats['no_jats'] += 1
            if verbose:
                print(f'  SKIP {vol_dir.name}/{slug}: no JATS file')
            continue

        html_content = jats_to_html(jats_path)
        if html_content is None:
            stats['no_body'] += 1
            if verbose:
                print(f'  SKIP {vol_dir.name}/{slug}: no body in JATS')
            continue

        stats['generated'] += 1

        if not dry_run:
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
                f.write('\n')

        if verbose:
            print(f'  ✓ {vol_dir.name}/{slug}.html')

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Generate HTML galley files from JATS XML')
    parser.add_argument('toc_files', nargs='+', help='toc.json file(s)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Count only, do not write files')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print details per article')
    args = parser.parse_args()

    total_stats = Counter()

    for toc_file in sorted(args.toc_files):
        toc_path = Path(toc_file)
        if not toc_path.exists():
            print(f'WARN: {toc_path} not found, skipping', file=sys.stderr)
            continue

        stats = process_toc(toc_path, args.dry_run, args.verbose)
        total_stats += stats

    print(f"\n{'=' * 50}")
    print(f"JATS → HTML {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'=' * 50}")
    print(f"  Articles:     {total_stats['total']}")
    print(f"  Generated:    {total_stats['generated']}")
    print(f"  No JATS file: {total_stats['no_jats']}")
    print(f"  No body:      {total_stats['no_body']}")
    print(f"{'=' * 50}")


if __name__ == '__main__':
    main()
