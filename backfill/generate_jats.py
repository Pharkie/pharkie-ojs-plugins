#!/usr/bin/env python3
"""Generate JATS XML files from toc.json + HTML galleys.

Produces one JATS 1.3 (Archiving and Interchange) XML file per article.
JATS becomes the single source of truth for article content — metadata,
body text, references, notes, author bios, and provenance in one file.

Usage:
    python3 backfill/generate_jats.py backfill/output/25.1/toc.json         # one issue
    python3 backfill/generate_jats.py backfill/output/*/toc.json            # all issues
    python3 backfill/generate_jats.py backfill/output/*/toc.json --dry-run  # count only
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape

# Import shared utilities from generate_xml.py
sys.path.insert(0, os.path.dirname(__file__))
from generate_xml import (
    parse_date, split_author_name, load_doi_registry, lookup_doi, SECTIONS,
)

JOURNAL_TITLE = 'Existential Analysis'
ISSN = '1752-5616'
PUBLISHER = 'Society for Existential Analysis'

# JATS article-type mapping from toc.json sections
ARTICLE_TYPES = {
    'Editorial': 'editorial',
    'Articles': 'research-article',
    'Book Review Editorial': 'editorial',
    'Book Reviews': 'book-review',
}


# ---------------------------------------------------------------
# HTML → JATS body conversion
# ---------------------------------------------------------------

class HTMLToJATSConverter(HTMLParser):
    """Convert simple HTML galley content to JATS body XML.

    Handles: h2, p, ol, ul, li, blockquote, em, strong, a, sup, sub, br, span.
    Produces: sec/title, p, list/list-item, disp-quote, italic, bold, ext-link, sup, sub.
    """

    def __init__(self):
        super().__init__()
        self._output = []
        self._in_sec = False
        self._tag_stack = []
        self._list_stack = []  # track ol/ul nesting
        self._skip_content = False
        self._in_blockquote = False  # suppress inner <p> wrapping
        self._in_li = False  # suppress inner <p> wrapping
        self._blockquote_has_p = False  # whether blockquote had inner <p>

    def _emit(self, text):
        if not self._skip_content:
            self._output.append(text)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)
        self._tag_stack.append(tag)

        if tag == 'h2':
            if self._in_sec:
                self._emit('</sec>\n')
            self._in_sec = True
            self._emit('<sec><title>')
        elif tag == 'p':
            if self._in_blockquote:
                # Blockquote already opened disp-quote — use inner <p> directly
                self._blockquote_has_p = True
                self._emit('<p>')
            elif self._in_li:
                # li already has <p> wrapper — skip inner <p>
                pass
            else:
                self._emit('<p>')
        elif tag == 'em' or tag == 'i':
            self._emit('<italic>')
        elif tag == 'strong' or tag == 'b':
            self._emit('<bold>')
        elif tag == 'sup':
            self._emit('<sup>')
        elif tag == 'sub':
            self._emit('<sub>')
        elif tag == 'ol':
            self._list_stack.append('order')
            self._emit('<list list-type="order">')
        elif tag == 'ul':
            self._list_stack.append('bullet')
            self._emit('<list list-type="bullet">')
        elif tag == 'li':
            self._in_li = True
            self._emit('<list-item><p>')
        elif tag == 'blockquote':
            self._in_blockquote = True
            self._blockquote_has_p = False
            self._emit('<disp-quote>')
        elif tag == 'a':
            href = attrs_dict.get('href', '')
            if href:
                self._emit(f'<ext-link xlink:href="{escape(href)}">')
            else:
                self._emit('')
        elif tag == 'br':
            self._emit('<break/>')
        elif tag in ('span', 'div', 'font', 'center'):
            pass  # skip wrapper tags, keep content
        elif tag in ('table', 'thead', 'tbody', 'tr', 'td', 'th',
                     'img', 'figure', 'figcaption', 'hr'):
            # Can't meaningfully convert these — skip tag but keep text
            pass
        elif tag in ('html', 'head', 'body', 'meta', 'title', 'style', 'script'):
            if tag in ('style', 'script'):
                self._skip_content = True

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == 'h2':
            self._emit('</title>\n')
        elif tag == 'p':
            if self._in_blockquote:
                self._emit('</p>\n')
            elif self._in_li:
                pass  # skip — li handles closing
            else:
                self._emit('</p>\n')
        elif tag == 'em' or tag == 'i':
            self._emit('</italic>')
        elif tag == 'strong' or tag == 'b':
            self._emit('</bold>')
        elif tag == 'sup':
            self._emit('</sup>')
        elif tag == 'sub':
            self._emit('</sub>')
        elif tag == 'ol':
            if self._list_stack:
                self._list_stack.pop()
            self._emit('</list>\n')
        elif tag == 'ul':
            if self._list_stack:
                self._list_stack.pop()
            self._emit('</list>\n')
        elif tag == 'li':
            self._in_li = False
            self._emit('</p></list-item>\n')
        elif tag == 'blockquote':
            if not self._blockquote_has_p:
                # No inner <p> tags — wrap bare text content
                # Retroactively wrap: find the <disp-quote> we emitted and add <p>
                self._emit('</disp-quote>\n')
                # Need to wrap content in <p> — do it in post-processing
            else:
                self._emit('</disp-quote>\n')
            self._in_blockquote = False
            self._blockquote_has_p = False
        elif tag == 'a':
            self._emit('</ext-link>')
        elif tag in ('style', 'script'):
            self._skip_content = False

        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

    def handle_data(self, data):
        self._emit(escape(data))

    def handle_entityref(self, name):
        self._emit(f'&{name};')

    def handle_charref(self, name):
        self._emit(f'&#{name};')

    def get_jats(self):
        result = ''.join(self._output)
        if self._in_sec:
            result += '</sec>\n'
        return result


def _postprocess_jats_body(jats: str) -> str:
    """Fix structural issues in converted JATS body XML."""
    # Wrap bare disp-quote content (no inner <p>) in <p> tags
    # Only wrap if there's no <p> tag inside at all
    def _wrap_bare_dispquote(m):
        inner = m.group(1).strip()
        if '<p>' not in inner:
            return f'<disp-quote><p>{inner}</p></disp-quote>'
        return m.group(0)
    jats = re.sub(
        r'<disp-quote>(.*?)</disp-quote>',
        _wrap_bare_dispquote,
        jats, flags=re.DOTALL
    )

    # Remove empty <p></p> pairs (from suppressed inner tags)
    jats = re.sub(r'<p>\s*</p>\s*', '', jats)

    # Remove empty sections
    jats = re.sub(r'<sec><title>\s*</title>\s*</sec>\s*', '', jats)

    # Fix inline tags (italic, bold, sup, sub) that cross block boundaries.
    # Source HTML often has <strong> spanning across <p> tags — invalid but common.
    # Close any open inline tag before </p> and reopen after <p>.
    inline_tags = {'italic', 'bold', 'sup', 'sub'}
    open_inlines = []
    fixed_parts = []
    # Split on block-level boundaries while preserving them
    tokens = re.split(r'(</?(?:p|disp-quote|list-item|sec|title)(?:\s[^>]*)?>)', jats)
    for token in tokens:
        close_match = re.match(r'^</(p|disp-quote|list-item|sec|title)>', token)
        open_match = re.match(r'^<(p|disp-quote|list-item|sec|title)', token)

        if close_match:
            # Close any open inline tags before the block close
            for tag in reversed(open_inlines):
                fixed_parts.append(f'</{tag}>')
            fixed_parts.append(token)
            open_inlines.clear()
        elif open_match:
            fixed_parts.append(token)
            # Don't reopen — inline state resets at block boundaries
            open_inlines.clear()
        else:
            # Track which inline tags are opened/closed in this text chunk
            for m in re.finditer(r'<(/?)(\w+)(?:\s[^>]*)?>', token):
                is_close = m.group(1) == '/'
                tag_name = m.group(2)
                if tag_name in inline_tags:
                    if is_close and tag_name in open_inlines:
                        open_inlines.remove(tag_name)
                    elif not is_close:
                        open_inlines.append(tag_name)
            fixed_parts.append(token)
    jats = ''.join(fixed_parts)

    # Clean up excessive whitespace between tags
    jats = re.sub(r'\n{3,}', '\n\n', jats)

    return jats


def _repair_xml(jats: str) -> str:
    """Ensure JATS body fragment is well-formed XML.

    Fixes unclosed tags, bare ampersands, and other common issues from
    malformed source HTML. Uses a stack-based approach: parse all tags,
    close any that are left open at the end.
    """
    from xml.etree import ElementTree as ET

    # Escape bare ampersands (& not followed by amp;/lt;/gt;/quot;/apos;/#)
    jats = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', jats)

    # Try parsing — if it works, we're done
    try:
        ET.fromstring(f'<root>{jats}</root>')
        return jats
    except ET.ParseError:
        pass

    # Stack-based repair: track open tags and close unclosed ones
    tag_pattern = re.compile(r'<(/?)(\w[\w-]*)(?:\s[^>]*)?\s*(/?)>')
    void_tags = {'break', 'ext-link'}  # self-closing in our output
    stack = []
    result_parts = []
    last_end = 0

    for m in tag_pattern.finditer(jats):
        result_parts.append(jats[last_end:m.start()])
        tag_text = m.group(0)
        is_close = m.group(1) == '/'
        tag_name = m.group(2)
        is_self_close = m.group(3) == '/'

        if is_self_close or tag_name in void_tags:
            result_parts.append(tag_text)
        elif is_close:
            # Close tag — pop matching open tag, or skip if not found
            if tag_name in stack:
                # Close any intervening unclosed tags first
                while stack and stack[-1] != tag_name:
                    orphan = stack.pop()
                    result_parts.append(f'</{orphan}>')
                if stack:
                    stack.pop()
                result_parts.append(tag_text)
            # else: close tag with no matching open — skip it
        else:
            stack.append(tag_name)
            result_parts.append(tag_text)

        last_end = m.end()

    result_parts.append(jats[last_end:])

    # Close any remaining open tags
    for tag in reversed(stack):
        result_parts.append(f'</{tag}>')

    repaired = ''.join(result_parts)

    # Verify repair worked
    try:
        ET.fromstring(f'<root>{repaired}</root>')
    except ET.ParseError:
        # Last resort: return original (will produce invalid JATS but not crash)
        return jats

    return repaired


def html_to_jats_body(html_content: str) -> str:
    """Convert HTML galley body content to JATS <body> XML."""
    # Strip DOCTYPE/html/head/body wrappers if present
    body_match = re.search(r'<body[^>]*>(.*)</body>', html_content,
                           re.DOTALL | re.IGNORECASE)
    if body_match:
        html_content = body_match.group(1)

    converter = HTMLToJATSConverter()
    converter.feed(html_content)
    raw = converter.get_jats().strip()
    cleaned = _postprocess_jats_body(raw)
    return _repair_xml(cleaned)


# ---------------------------------------------------------------
# JATS XML generation
# ---------------------------------------------------------------

def _load_back_matter_from_jats(html_path: Path | None) -> dict | None:
    """Load back matter from an existing JATS file (if references etc. removed from toc.json)."""
    if not html_path:
        return None
    jats_path = html_path.with_suffix('.jats.xml')
    if not jats_path.exists():
        return None
    try:
        from xml.etree import ElementTree as ET
        tree = ET.parse(jats_path)
        result = {}
        refs = [r.text.strip() for r in tree.findall('.//{*}mixed-citation') if r.text]
        if refs:
            result['references'] = refs
        notes = [fn.find('{*}p').text.strip() for fn in tree.findall('.//{*}fn')
                 if fn.find('{*}p') is not None and fn.find('{*}p').text]
        if notes:
            result['notes'] = notes
        bios = [b.find('{*}p').text.strip() for b in tree.findall('.//{*}bio')
                if b.find('{*}p') is not None and b.find('{*}p').text]
        if bios:
            result['author_bios'] = bios
        prov = tree.find('.//{*}notes[@notes-type="provenance"]')
        if prov is not None:
            p = prov.find('{*}p')
            if p is not None and p.text:
                result['provenance'] = p.text.strip()
        return result if result else None
    except Exception:
        return None


def _sort_notes_by_number(notes: list[str]) -> list[str]:
    """Sort notes by their leading number (e.g. '3 Text...' before '4 Text...').

    Notes without a leading number are placed at the end in original order.
    """
    def sort_key(note):
        m = re.match(r'^(\d+)[\.\)\s]', note)
        if m:
            return (0, int(m.group(1)))
        return (1, 0)  # unnumbered notes go last

    return sorted(notes, key=sort_key)


def generate_article_jats(article: dict, volume: int, issue: int,
                          date_published: str, html_path: Path | None,
                          doi: str | None) -> str:
    """Generate complete JATS XML for a single article."""
    section = article.get('section', 'Articles')
    article_type = ARTICLE_TYPES.get(section, 'research-article')
    year = date_published[:4]
    month = date_published[5:7]

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Archiving '
                 'and Interchange DTD v1.3 20210610//EN"')
    lines.append('  "JATS-archivearticle1-3.dtd">')
    lines.append(f'<article article-type="{article_type}" xml:lang="en" '
                 f'dtd-version="1.3" xmlns:xlink="http://www.w3.org/1999/xlink">')

    # --- <front> ---
    lines.append('<front>')

    # Journal metadata
    lines.append('<journal-meta>')
    lines.append(f'<journal-id journal-id-type="issn">{ISSN}</journal-id>')
    lines.append(f'<journal-title-group><journal-title>{JOURNAL_TITLE}</journal-title></journal-title-group>')
    lines.append(f'<issn pub-type="ppub">{ISSN}</issn>')
    lines.append(f'<publisher><publisher-name>{PUBLISHER}</publisher-name></publisher>')
    lines.append('</journal-meta>')

    # Article metadata
    lines.append('<article-meta>')

    # DOI
    if doi:
        lines.append(f'<article-id pub-id-type="doi">{escape(doi)}</article-id>')

    # Title
    title = article.get('title', '')
    lines.append(f'<title-group><article-title>{escape(title)}</article-title></title-group>')

    # Authors
    authors_raw = article.get('authors', '')
    if authors_raw:
        author_pairs = split_author_name(authors_raw)
        lines.append('<contrib-group>')
        for given, family in author_pairs:
            lines.append('<contrib contrib-type="author">')
            lines.append(f'<name><surname>{escape(family)}</surname>'
                         f'<given-names>{escape(given)}</given-names></name>')
            lines.append('</contrib>')
        lines.append('</contrib-group>')

    # Publication date
    lines.append(f'<pub-date date-type="pub" publication-format="print" '
                 f'iso-8601-date="{date_published}">')
    lines.append(f'<month>{month}</month><year>{year}</year>')
    lines.append('</pub-date>')

    # Volume / issue
    lines.append(f'<volume>{volume}</volume>')
    lines.append(f'<issue>{issue}</issue>')

    # Pages
    page_start = article.get('journal_page_start')
    page_end = article.get('journal_page_end')
    if page_start is not None:
        lines.append(f'<fpage>{page_start}</fpage>')
    if page_end is not None:
        lines.append(f'<lpage>{page_end}</lpage>')

    # Abstract
    abstract = article.get('abstract', '')
    if abstract:
        lines.append(f'<abstract><p>{escape(abstract)}</p></abstract>')

    # Keywords
    keywords = article.get('keywords', [])
    if keywords:
        lines.append('<kwd-group kwd-group-type="author">')
        for kw in keywords:
            lines.append(f'<kwd>{escape(kw)}</kwd>')
        lines.append('</kwd-group>')

    # Subjects
    subjects = article.get('subjects', [])
    if subjects:
        lines.append('<subj-group subj-group-type="subject">')
        for subj in subjects:
            lines.append(f'<subject>{escape(subj)}</subject>')
        lines.append('</subj-group>')

    lines.append('</article-meta>')
    lines.append('</front>')

    # --- <body> ---
    if html_path and html_path.exists():
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        jats_body = html_to_jats_body(html_content)
        if jats_body:
            lines.append('<body>')
            lines.append(jats_body)
            lines.append('</body>')

    # --- <back> ---
    # Read from toc.json first; fall back to existing JATS file if fields
    # have been removed from toc.json (JATS is the source of truth)
    references = article.get('references', [])
    notes = article.get('notes', [])
    author_bios = article.get('author_bios', [])
    provenance = article.get('provenance', '')

    if not references and not notes and not author_bios and not provenance:
        existing = _load_back_matter_from_jats(html_path)
        if existing:
            references = existing.get('references', [])
            notes = existing.get('notes', [])
            author_bios = existing.get('author_bios', [])
            provenance = existing.get('provenance', '')

    if references or notes or author_bios or provenance:
        lines.append('<back>')

        # References
        if references:
            lines.append('<ref-list>')
            for i, ref in enumerate(references, 1):
                lines.append(f'<ref id="ref{i}"><mixed-citation>{escape(ref)}</mixed-citation></ref>')
            lines.append('</ref-list>')

        # Notes/endnotes (sorted by leading number if present)
        if notes:
            sorted_notes = _sort_notes_by_number(notes)
            lines.append('<fn-group>')
            for i, note in enumerate(sorted_notes, 1):
                lines.append(f'<fn id="fn{i}"><p>{escape(note)}</p></fn>')
            lines.append('</fn-group>')

        # Author bios
        if author_bios:
            for bio in author_bios:
                lines.append(f'<bio><p>{escape(bio)}</p></bio>')

        # Provenance
        if provenance:
            lines.append(f'<notes notes-type="provenance"><p>{escape(provenance)}</p></notes>')

        lines.append('</back>')

    lines.append('</article>')
    return '\n'.join(lines)


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def process_toc(toc_path: Path, doi_registry: dict, dry_run: bool,
                verbose: bool) -> Counter:
    """Generate JATS files for all articles in a toc.json."""
    stats = Counter()

    with open(toc_path) as f:
        toc = json.load(f)

    vol_dir = toc_path.parent
    volume = toc.get('volume', 0)
    issue = toc.get('issue', 0)
    date_str = toc.get('date', '')
    date_published = parse_date(date_str)

    for article in toc.get('articles', []):
        split_pdf = article.get('split_pdf', '')
        slug = Path(split_pdf).stem if split_pdf else ''
        if not slug:
            stats['no_slug'] += 1
            continue

        # HTML galley path
        html_path = vol_dir / f'{slug}.html'

        # DOI lookup
        doi = lookup_doi(doi_registry, article.get('title', ''),
                         str(volume), str(issue),
                         authors=article.get('authors', ''))

        # Output path
        jats_path = vol_dir / f'{slug}.jats.xml'

        stats['total'] += 1
        if html_path.exists():
            stats['with_html'] += 1

        if doi:
            stats['with_doi'] += 1

        has_refs = bool(article.get('references'))
        has_notes = bool(article.get('notes'))
        has_bios = bool(article.get('author_bios'))
        # Also check existing JATS file (back matter may have been removed from toc.json)
        if not has_refs and not has_notes and not has_bios:
            existing = _load_back_matter_from_jats(html_path)
            if existing:
                has_refs = bool(existing.get('references'))
                has_notes = bool(existing.get('notes'))
                has_bios = bool(existing.get('author_bios'))
        if has_refs:
            stats['with_refs'] += 1
        if has_notes:
            stats['with_notes'] += 1
        if has_bios:
            stats['with_bios'] += 1

        if dry_run:
            if verbose:
                print(f'  {vol_dir.name}/{slug}: doi={bool(doi)} html={html_path.exists()} '
                      f'refs={has_refs} notes={has_notes} bios={has_bios}')
            continue

        # Generate JATS
        jats_xml = generate_article_jats(
            article, volume, issue, date_published, html_path, doi)

        with open(jats_path, 'w', encoding='utf-8') as f:
            f.write(jats_xml)
            f.write('\n')

        stats['written'] += 1
        if verbose:
            print(f'  ✓ {vol_dir.name}/{slug}.jats.xml')

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Generate JATS XML files from toc.json + HTML galleys')
    parser.add_argument('toc_files', nargs='+', help='toc.json file(s)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Count only, do not write files')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print details per article')
    args = parser.parse_args()

    doi_registry = load_doi_registry()
    total_stats = Counter()

    for toc_file in sorted(args.toc_files):
        toc_path = Path(toc_file)
        if not toc_path.exists():
            print(f'WARN: {toc_path} not found, skipping', file=sys.stderr)
            continue

        stats = process_toc(toc_path, doi_registry, args.dry_run, args.verbose)
        total_stats += stats

    print(f"\n{'=' * 50}")
    print(f"JATS GENERATION {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'=' * 50}")
    print(f"  Articles:       {total_stats['total']}")
    print(f"  With HTML body: {total_stats['with_html']}")
    print(f"  With DOI:       {total_stats['with_doi']}")
    print(f"  With refs:      {total_stats['with_refs']}")
    print(f"  With notes:     {total_stats['with_notes']}")
    print(f"  With bios:      {total_stats['with_bios']}")
    if not args.dry_run:
        print(f"  Written:        {total_stats['written']}")
    print(f"{'=' * 50}")


if __name__ == '__main__':
    main()
