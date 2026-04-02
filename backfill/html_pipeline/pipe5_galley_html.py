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
import os
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, NavigableString

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.citations import local_name, extract_text_from_element

_INLINE_TAG_MAP = {
    'italic': 'em',
    'bold': 'strong',
    'sup': 'sup',
    'sub': 'sub',
}


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

    soup = BeautifulSoup('', 'html.parser')

    # Convert body children
    for child in body:
        _convert_element(child, soup, soup)

    # Reviewed-work metadata (book reviews only — prepended before body)
    article_meta = root.find('.//{*}article-meta')
    if article_meta is not None:
        _render_product(article_meta, soup)

    # Render back matter (except references)
    back = root.find('.//{*}back')
    if back is not None:
        _render_back_matter(back, soup)

    # decode_contents() not prettify() — prettify() inserts newlines inside
    # inline tags (<em>, <strong>) which browsers collapse to visible spaces.
    return soup.decode_contents()


def _convert_element(et_el, parent, soup, sec_depth=0):
    """Recursively convert an ElementTree element to BS4 tags."""
    tag_name = local_name(et_el.tag)

    if tag_name == 'sec':
        title_el = et_el.find('{*}title')
        if title_el is None:
            title_el = et_el.find('title')
        if title_el is not None:
            title_text = extract_text_from_element(title_el)
            if title_text:
                heading_tag = 'h3' if sec_depth > 0 else 'h2'
                h = soup.new_tag(heading_tag)
                h.string = title_text
                parent.append(h)
        for child in et_el:
            if local_name(child.tag) != 'title':
                _convert_element(child, parent, soup, sec_depth + 1)

    elif tag_name == 'p':
        p = soup.new_tag('p')
        _add_inline_content(et_el, p, soup)
        parent.append(p)

    elif tag_name == 'disp-quote':
        bq = soup.new_tag('blockquote')
        _add_inline_content(et_el, bq, soup)
        parent.append(bq)

    elif tag_name == 'list':
        list_type = et_el.get('list-type', 'bullet')
        html_tag = 'ol' if list_type == 'order' else 'ul'
        lst = soup.new_tag(html_tag)
        for item in et_el:
            if local_name(item.tag) == 'list-item':
                li = soup.new_tag('li')
                children = list(item)
                # Single-paragraph list items: unwrap the <p>
                if len(children) == 1 and local_name(children[0].tag) == 'p':
                    _add_inline_content(children[0], li, soup)
                else:
                    for child in item:
                        _convert_element(child, li, soup)
                lst.append(li)
        parent.append(lst)

    elif tag_name in _INLINE_TAG_MAP:
        el = soup.new_tag(_INLINE_TAG_MAP[tag_name])
        _add_inline_content(et_el, el, soup)
        parent.append(el)

    elif tag_name == 'ext-link':
        href = et_el.get('{http://www.w3.org/1999/xlink}href', '')
        a = soup.new_tag('a', href=href)
        _add_inline_content(et_el, a, soup)
        parent.append(a)

    elif tag_name == 'break':
        parent.append(soup.new_tag('br'))

    else:
        # Unknown element — render inline content directly into parent
        _add_inline_content(et_el, parent, soup)


def _add_inline_content(et_el, parent, soup):
    """Add mixed content (text + children) from ET element to BS4 parent.

    BS4 auto-escapes text nodes on serialisation, so &, <, > in text
    are handled correctly without manual html.escape() calls.
    """
    if et_el.text:
        parent.append(NavigableString(et_el.text))
    for child in et_el:
        _convert_element(child, parent, soup)
        if child.tail:
            parent.append(NavigableString(child.tail))


def _render_product(article_meta, soup):
    """Render JATS <product> as a reviewed-work div, prepended before body."""
    product = article_meta.find('{*}product')
    if product is None:
        product = article_meta.find('product')
    if product is None:
        return

    # Book title from <source>
    source_el = product.find('{*}source')
    if source_el is None:
        source_el = product.find('source')
    title_text = extract_text_from_element(source_el).strip() if source_el is not None else ''
    if not title_text:
        return

    # Authors from <person-group>
    authors = []
    for pg in product:
        if local_name(pg.tag) == 'person-group':
            for name_el in pg:
                if local_name(name_el.tag) == 'name':
                    surname_el = name_el.find('{*}surname')
                    if surname_el is None:
                        surname_el = name_el.find('surname')
                    given_el = name_el.find('{*}given-names')
                    if given_el is None:
                        given_el = name_el.find('given-names')
                    parts = []
                    if given_el is not None and given_el.text:
                        parts.append(given_el.text.strip())
                    if surname_el is not None and surname_el.text:
                        parts.append(surname_el.text.strip())
                    if parts:
                        authors.append(' '.join(parts))

    year_el = product.find('{*}year')
    if year_el is None:
        year_el = product.find('year')
    year_text = year_el.text.strip() if year_el is not None and year_el.text else ''

    pub_name_el = product.find('{*}publisher-name')
    if pub_name_el is None:
        pub_name_el = product.find('publisher-name')
    pub_loc_el = product.find('{*}publisher-loc')
    if pub_loc_el is None:
        pub_loc_el = product.find('publisher-loc')
    pub_parts = []
    if pub_loc_el is not None and pub_loc_el.text:
        pub_parts.append(pub_loc_el.text.strip())
    if pub_name_el is not None and pub_name_el.text:
        pub_parts.append(pub_name_el.text.strip())
    publisher_text = ': '.join(pub_parts)

    # Build single citation line: Author (Year). Title. Publisher
    citation_parts = []
    if authors:
        citation_parts.append(', '.join(authors))
    if year_text:
        citation_parts.append(f'({year_text})')
    prefix = ' '.join(citation_parts)

    div = soup.new_tag('div', attrs={'class': 'jats-reviewed-work'})
    p = soup.new_tag('p')
    if prefix:
        p.append(NavigableString(prefix + '. '))
    em = soup.new_tag('em')
    em.string = title_text if title_text.endswith(('?', '!', '.')) else title_text + '.'
    p.append(em)
    if publisher_text:
        p.append(NavigableString(' ' + publisher_text))
    div.append(p)

    # Prepend before existing content
    if soup.contents:
        soup.contents[0].insert_before(div)
    else:
        soup.append(div)


def _render_back_matter(back, soup):
    """Render JATS <back> elements as BS4 tags (except ref-list).

    Order matches typical journal PDF layout:
      body → provenance → bios → notes → references (refs excluded)
    """
    # Provenance notes (conference/presentation context — before bios)
    prov_items = []
    for child in back:
        if local_name(child.tag) == 'notes':
            if child.get('notes-type', '') == 'provenance':
                p = _find_p(child)
                if p is not None:
                    text = extract_text_from_element(p).strip()
                    if text:
                        prov_items.append(text)
    if prov_items:
        div = soup.new_tag('div', attrs={'class': 'jats-provenance'})
        for prov in prov_items:
            p = soup.new_tag('p')
            em = soup.new_tag('em')
            em.string = prov
            p.append(em)
            div.append(p)
        soup.append(div)

    # Author bios
    for child in back:
        if local_name(child.tag) == 'bio':
            p_el = _find_p(child)
            if p_el is not None:
                text = extract_text_from_element(p_el).strip()
                if text:
                    div = soup.new_tag('div', attrs={'class': 'jats-bios'})
                    p = soup.new_tag('p')
                    p.string = text
                    div.append(p)
                    soup.append(div)

    # Notes/endnotes → <h2>Notes</h2> + <ol>
    fn_group = None
    for child in back:
        if local_name(child.tag) == 'fn-group':
            fn_group = child
            break

    if fn_group is not None:
        notes = []
        for fn in fn_group:
            if local_name(fn.tag) == 'fn':
                p_el = _find_p(fn)
                if p_el is not None:
                    text = extract_text_from_element(p_el).strip()
                    if text:
                        notes.append(text)
        if notes:
            div = soup.new_tag('div', attrs={'class': 'jats-notes'})
            h2 = soup.new_tag('h2')
            h2.string = 'Notes'
            div.append(h2)
            ol = soup.new_tag('ol')
            for note in notes:
                li = soup.new_tag('li')
                li.string = note
                ol.append(li)
            div.append(ol)
            soup.append(div)


def _find_p(et_el):
    """Find first <p> child, handling namespace prefixes."""
    p = et_el.find('{*}p') if '}' in et_el.tag else et_el.find('p')
    if p is None:
        for c in et_el:
            if local_name(c.tag) == 'p':
                return c
    return p


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
        html_path = vol_dir / f'{slug}.galley.html'

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
            print(f'  {vol_dir.name}/{slug}.html')

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
