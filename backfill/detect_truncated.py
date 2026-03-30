#!/usr/bin/env python3
"""Detect raw HTML files that were truncated by Haiku's output token limit.

When Haiku hits max_tokens, the HTML stops mid-output. This script detects
truncation by checking for unclosed tags, mid-sentence endings, and
incomplete HTML entities at the end of *.raw.html files.

Articles with _continuations set in toc.json are excluded (already re-extracted).
"""

import argparse
import glob
import json
import os
import re
import sys
from html.parser import HTMLParser


class TagTracker(HTMLParser):
    """Track unclosed tags at end of HTML."""

    BLOCK_TAGS = {'p', 'blockquote', 'table', 'tr', 'td', 'th', 'thead',
                  'tbody', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'div',
                  'section', 'article'}
    VOID_TAGS = {'br', 'hr', 'img', 'meta', 'link', 'input', 'area',
                 'base', 'col', 'embed', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.last_text = ''

    def handle_starttag(self, tag, attrs):
        if tag.lower() not in self.VOID_TAGS:
            self.stack.append(tag.lower())

    def handle_endtag(self, tag):
        tag = tag.lower()
        # Pop matching tag (tolerant of mismatches)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i] == tag:
                self.stack.pop(i)
                break

    def handle_data(self, data):
        stripped = data.strip()
        if stripped:
            self.last_text = stripped


def detect_truncation(html_content):
    """Return list of truncation signals found, or empty list if clean."""
    signals = []

    # Skip AUTO-EXTRACTED (pymupdf fallback) — different problem
    if '<!-- AUTO-EXTRACTED' in html_content[:200]:
        return []

    # Check for unclosed tags
    tracker = TagTracker()
    try:
        tracker.feed(html_content)
    except Exception:
        signals.append('parse-error')
        return signals

    unclosed_block = [t for t in tracker.stack if t in TagTracker.BLOCK_TAGS]
    if unclosed_block:
        signals.append(f'unclosed-tags: {", ".join(unclosed_block)}')

    # Check for unclosed inline tags (em, strong, etc.)
    unclosed_inline = [t for t in tracker.stack
                       if t in ('em', 'strong', 'a', 'span', 'sup', 'sub')]
    if unclosed_inline:
        signals.append(f'unclosed-inline: {", ".join(unclosed_inline)}')

    # Check last text for mid-sentence ending
    last_text = tracker.last_text
    if last_text:
        # Strip trailing whitespace and common artifacts
        clean = last_text.rstrip()
        if clean and not re.search(r'[.?!"\'\)\]>]$', clean):
            # Could be mid-sentence — but only flag if we also have unclosed tags
            # (many articles legitimately end without punctuation, e.g. a poem)
            if unclosed_block or unclosed_inline:
                signals.append(f'mid-sentence: ...{clean[-40:]}')

    # Check for incomplete HTML entity at end
    tail = html_content.rstrip()[-20:]
    if re.search(r'&[a-zA-Z]+$', tail) or re.search(r'&#\d+$', tail):
        signals.append('incomplete-entity')

    return signals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    args = parser.parse_args()

    output_dir = os.path.join(os.path.dirname(__file__), 'private', 'output')

    # Load toc.json data to check _continuations and get titles
    toc_articles = {}  # (vol, seq) -> article dict
    for toc_path in sorted(glob.glob(os.path.join(output_dir, '*/toc.json'))):
        vol = os.path.basename(os.path.dirname(toc_path))
        with open(toc_path) as f:
            toc = json.load(f)
        for i, a in enumerate(toc['articles']):
            toc_articles[(vol, i + 1)] = a

    results = []

    for raw_path in sorted(glob.glob(os.path.join(output_dir, '*/*.raw.html'))):
        vol_dir = os.path.dirname(raw_path)
        vol = os.path.basename(vol_dir)
        basename = os.path.basename(raw_path)
        # Extract sequence number from filename (e.g. "11-the-madhouse-of-being.raw.html" -> 11)
        seq_match = re.match(r'^(\d+)-', basename)
        if not seq_match:
            continue
        seq = int(seq_match.group(1))

        # Skip articles already re-extracted with continuations
        article = toc_articles.get((vol, seq), {})
        if article.get('_continuations', 0) > 0:
            continue

        with open(raw_path, encoding='utf-8') as f:
            content = f.read()

        signals = detect_truncation(content)
        if signals:
            size = len(content)
            title = article.get('title', '(unknown)')
            ps = article.get('pdf_page_start', 0)
            pe = article.get('pdf_page_end', 0)
            pages = pe - ps + 1 if ps and pe else 0
            results.append({
                'vol': vol,
                'seq': seq,
                'title': title,
                'pages': pages,
                'size': size,
                'signals': signals,
                'file': basename,
            })

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        print()
    else:
        if not results:
            print('No truncated articles detected.')
            return

        print(f'Detected {len(results)} potentially truncated article(s):\n')
        print(f'{"Vol":>6} {"#":>3} {"Pages":>5} {"Size":>7}  {"Signals":<40} Title')
        print('-' * 100)
        for r in results:
            sigs = '; '.join(r['signals'])[:40]
            print(f'{r["vol"]:>6} {r["seq"]:>3} {r["pages"]:>5} {r["size"]:>7}  {sigs:<40} {r["title"][:45]}')


if __name__ == '__main__':
    main()
