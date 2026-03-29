#!/usr/bin/env python3
"""
Phase 1: Update toc.json metadata abstracts and keywords from HTML galleys.

HTML galley abstracts are cleaner than metadata abstracts (no PDF artefacts like
broken hyphens). This script extracts abstract text and keywords from HTML files
that start with <h2>Abstract</h2> and updates the corresponding toc.json entries.

Usage:
    python3 backfill/update_abstracts.py --dry-run    # Report differences
    python3 backfill/update_abstracts.py --apply       # Update toc.json files
"""

import sys
import os
import re
import json
import glob
import argparse

sys.path.insert(0, os.path.dirname(__file__))
from lib.citations import strip_html


MAX_ABSTRACT_LENGTH = 2000
MAX_ABSTRACT_PARAGRAPHS = 2


def strip_html_tags(html):
    """Remove HTML tags and normalise whitespace.

    Delegates to shared strip_html() then collapses whitespace.
    """
    text = strip_html(html)
    return re.sub(r'\s+', ' ', text).strip()


def split_at_h2(html):
    """Split HTML content at <h2> boundaries. Returns list of (heading_text, content) tuples."""
    # Split on <h2> tags, keeping the heading text
    parts = re.split(r'<h2>(.*?)</h2>', html, flags=re.DOTALL)
    # parts[0] is before first h2, then alternating heading/content
    sections = []
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        content = parts[i + 1] if i + 1 < len(parts) else ''
        sections.append((heading, content))
    return sections


def extract_abstract_and_keywords(html_path):
    """Extract abstract text and keywords from an HTML galley file.

    Returns (abstract_text, keywords_list) or (None, None) if no abstract found.
    """
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Must start with <h2>Abstract</h2>
    if not content.strip().startswith('<h2>Abstract</h2>'):
        return None, None

    sections = split_at_h2(content)
    if not sections or sections[0][0] != 'Abstract':
        return None, None

    abstract_html = sections[0][1]

    # Extract paragraphs from the abstract section
    paragraphs = re.findall(r'<p>(.*?)</p>', abstract_html, re.DOTALL)

    # Separate abstract paragraphs from inline keywords
    abstract_paras = []
    keywords = None

    for p in paragraphs:
        # Check if this paragraph is a keywords label (standalone)
        p_stripped = strip_html_tags(p).strip()
        if re.match(r'^Key\s*[Ww]ords:?\s*$', p_stripped):
            # Next paragraph should contain the actual keywords
            continue
        # Check if this paragraph IS the keywords (after a Key Words label, or inline)
        if keywords is None and abstract_paras:
            # Check if previous para was a keywords label
            prev_stripped = strip_html_tags(paragraphs[paragraphs.index(p) - 1]) if paragraphs.index(p) > 0 else ''
            if re.match(r'^Key\s*[Ww]ords:?\s*$', prev_stripped.strip()):
                keywords = parse_keywords_text(p_stripped)
                continue
        # Check for inline keywords: <strong>Keywords:</strong> ... or <strong>Key Words:</strong> ...
        kw_match = re.match(r'<strong>Key\s*[Ww]ords:?\s*</strong>:?\s*(.*)', p, re.DOTALL)
        if kw_match:
            kw_text = strip_html_tags(kw_match.group(1)).strip()
            if kw_text:
                keywords = parse_keywords_text(kw_text)
            continue
        # Regular abstract paragraph
        abstract_paras.append(p)

    # Also check for <h2>Key Words</h2> or <h2>Keywords</h2> section
    if keywords is None:
        for heading, sect_content in sections[1:]:
            if re.match(r'Key\s*[Ww]ords?', heading):
                # Extract keywords from this section
                kw_paragraphs = re.findall(r'<p>(.*?)</p>', sect_content, re.DOTALL)
                for kw_p in kw_paragraphs:
                    # Skip the label paragraph if present
                    kw_text = strip_html_tags(kw_p).strip()
                    # Remove leading "Keywords:" prefix if present
                    kw_text = re.sub(r'^Key\s*[Ww]ords:?\s*', '', kw_text).strip()
                    if kw_text:
                        keywords = parse_keywords_text(kw_text)
                        break
                break

    if not abstract_paras:
        return None, keywords

    # Build plain text abstract
    abstract_text = '\n\n'.join(strip_html_tags(p) for p in abstract_paras)

    return abstract_text, keywords


def parse_keywords_text(text):
    """Parse a keywords string into a list. Handles comma, semicolon, and mixed separators."""
    # Remove trailing period
    text = text.rstrip('.')
    # Split on semicolons first (if present), otherwise commas
    if ';' in text:
        parts = [p.strip() for p in text.split(';')]
    else:
        parts = [p.strip() for p in text.split(',')]
    # Filter empty strings
    return [p for p in parts if p]


def normalize_for_comparison(text):
    """Normalize text for comparison: collapse whitespace, strip, lowercase."""
    if not text:
        return ''
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text


def find_html_for_article(article, issue_dir):
    """Find the HTML galley file for a toc.json article entry."""
    split_pdf = article.get('split_pdf', '')
    if not split_pdf:
        return None
    # HTML file has same stem as split PDF but .html extension
    pdf_basename = os.path.basename(split_pdf)
    html_basename = os.path.splitext(pdf_basename)[0] + '.html'
    html_path = os.path.join(issue_dir, html_basename)
    if os.path.exists(html_path):
        return html_path
    return None


def count_paragraphs(text):
    """Count paragraphs in abstract text (split by double newline)."""
    if not text:
        return 0
    return len([p for p in text.split('\n\n') if p.strip()])


def main():
    parser = argparse.ArgumentParser(description='Update toc.json abstracts and keywords from HTML galleys')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true', help='Report differences without changing files')
    group.add_argument('--apply', action='store_true', help='Update toc.json files')
    args = parser.parse_args()

    toc_files = sorted(glob.glob('backfill/private/output/*/toc.json'))
    if not toc_files:
        print("No toc.json files found in backfill/private/output/*/", file=sys.stderr)
        sys.exit(1)

    stats = {
        'html_with_abstract': 0,
        'abstracts_updated': 0,
        'abstracts_skipped_too_long': 0,
        'abstracts_skipped_too_many_paras': 0,
        'keywords_added': 0,
        'keywords_fixed': 0,
        'keywords_updated': 0,
        'toc_files_modified': set(),
    }

    for toc_path in toc_files:
        issue_dir = os.path.dirname(toc_path)
        issue_name = os.path.basename(issue_dir)

        with open(toc_path, 'r', encoding='utf-8') as f:
            toc = json.load(f)

        modified = False

        for i, article in enumerate(toc['articles']):
            html_path = find_html_for_article(article, issue_dir)
            if not html_path:
                continue

            abstract_text, keywords = extract_abstract_and_keywords(html_path)

            if abstract_text is None and keywords is None:
                continue

            stats['html_with_abstract'] += 1
            html_basename = os.path.basename(html_path)

            # --- Abstract update ---
            if abstract_text:
                para_count = count_paragraphs(abstract_text)
                skip_abstract = False
                if len(abstract_text) > MAX_ABSTRACT_LENGTH:
                    stats['abstracts_skipped_too_long'] += 1
                    if args.dry_run:
                        print(f"  SKIP abstract {issue_name}/{html_basename}: too long ({len(abstract_text)} chars)")
                    skip_abstract = True
                elif para_count > MAX_ABSTRACT_PARAGRAPHS:
                    stats['abstracts_skipped_too_many_paras'] += 1
                    if args.dry_run:
                        print(f"  SKIP abstract {issue_name}/{html_basename}: too many paragraphs ({para_count})")
                    skip_abstract = True

                if not skip_abstract:
                    existing = article.get('abstract', '')
                    if normalize_for_comparison(abstract_text) != normalize_for_comparison(existing):
                        stats['abstracts_updated'] += 1
                        if args.dry_run:
                            print(f"\n  UPDATE abstract: {issue_name}/{html_basename}")
                            if existing:
                                print(f"    OLD: {existing[:120]}...")
                            else:
                                print(f"    OLD: (none)")
                            print(f"    NEW: {abstract_text[:120]}...")
                        else:
                            article['abstract'] = abstract_text
                            modified = True

            # --- Keywords update ---
            if keywords:
                # Dedupe keywords (preserve order)
                seen = set()
                deduped = []
                for k in keywords:
                    if k not in seen:
                        seen.add(k)
                        deduped.append(k)
                keywords = deduped

                existing_kw = article.get('keywords', [])
                if not existing_kw:
                    stats['keywords_added'] += 1
                    if args.dry_run:
                        print(f"  ADD keywords: {issue_name}/{html_basename}: {keywords}")
                    else:
                        article['keywords'] = keywords
                        modified = True
                elif existing_kw != keywords:
                    # Fix bad keywords: overwrite if existing has entries > 80 chars
                    # (body text leaked into keywords) or has duplicates
                    has_bad = any(len(k) > 80 for k in existing_kw)
                    has_dupes = len(existing_kw) != len(set(existing_kw))
                    if has_bad or has_dupes:
                        stats['keywords_fixed'] += 1
                        if args.dry_run:
                            print(f"  FIX keywords: {issue_name}/{html_basename}")
                            print(f"    OLD: {[k[:60]+'...' if len(k)>60 else k for k in existing_kw]}")
                            print(f"    NEW: {keywords}")
                        else:
                            article['keywords'] = keywords
                            modified = True
                    elif set(normalize_for_comparison(k) for k in existing_kw) != set(normalize_for_comparison(k) for k in keywords):
                        stats['keywords_updated'] += 1
                        if args.dry_run:
                            print(f"  UPDATE keywords: {issue_name}/{html_basename}")
                            print(f"    OLD: {existing_kw}")
                            print(f"    NEW: {keywords}")

        if modified:
            stats['toc_files_modified'].add(toc_path)
            with open(toc_path, 'w', encoding='utf-8') as f:
                json.dump(toc, f, indent=2, ensure_ascii=False)
                f.write('\n')

    # Summary
    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN' if args.dry_run else 'APPLIED'} Summary:")
    print(f"  HTML files with abstract: {stats['html_with_abstract']}")
    print(f"  Abstracts updated:        {stats['abstracts_updated']}")
    print(f"  Abstracts skipped (long): {stats['abstracts_skipped_too_long']}")
    print(f"  Abstracts skipped (paras):{stats['abstracts_skipped_too_many_paras']}")
    print(f"  Keywords added:           {stats['keywords_added']}")
    print(f"  Keywords fixed (bad):     {stats['keywords_fixed']}")
    print(f"  Keywords updated:         {stats['keywords_updated']}")
    print(f"  toc.json files modified:  {len(stats['toc_files_modified'])}")


if __name__ == '__main__':
    main()
