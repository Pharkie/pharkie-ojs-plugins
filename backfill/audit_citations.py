#!/usr/bin/env python3
"""
Audit HTML galleys for citation/reference sections.

Non-destructive: reads HTML files only, produces a report.
Does NOT modify toc.json, the database, or any files.

Usage:
    python3 backfill/audit_citations.py
    python3 backfill/audit_citations.py --volume 37.1
    python3 backfill/audit_citations.py --verbose

Output:
    backfill/private/output/citations-audit-report.json
    stdout: human-readable summary
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from lib.citations import strip_html

OUTPUT_DIR = Path(__file__).parent / "private" / "output"
REPORT_PATH = OUTPUT_DIR / "citations-audit-report.json"


# Headings that indicate a reference/citation section
REFERENCE_HEADINGS = re.compile(
    r'<h2>\s*('
    r'References?'
    r'|Notes?'
    r'|Endnotes?'
    r'|Footnotes?'
    r'|Bibliography'
    r'|Further Reading'
    r'|Works Cited'
    r'|Notes and References'
    r'|References and Notes'
    r'|Selected Bibliography'
    r'|References:'
    r')\s*</h2>',
    re.IGNORECASE
)

# Headings that are NOT reference sections (to detect end of references)
NON_REFERENCE_HEADINGS = re.compile(
    r'<h2>\s*('
    r'Acknowledg[e]?ments?'
    r'|About the Authors?'
    r'|Biographical? Notes?'
    r'|Contributors?'
    r'|Author Bio'
    r'|Appendix'
    r'|BOOK REVIEWS'
    r')\s*</h2>',
    re.IGNORECASE
)


def find_reference_sections(html_content: str) -> list[dict]:
    """Find all reference-like sections in an HTML galley.

    Returns list of dicts with:
      heading: the matched heading text
      content_html: raw HTML between this heading and the next h2 or end
      items: list of extracted citation strings
      structure: 'p_tags' | 'ol_li' | 'numbered_p' | 'mixed'
    """
    sections = []

    # Find all h2 headings and their positions
    h2_pattern = re.compile(r'<h2[^>]*>(.*?)</h2>', re.IGNORECASE | re.DOTALL)
    headings = list(h2_pattern.finditer(html_content))

    for i, match in enumerate(headings):
        heading_text = strip_html(match.group(1)).strip()

        # Check if this is a reference heading
        if not REFERENCE_HEADINGS.match(f'<h2>{heading_text}</h2>'):
            continue

        # Extract content between this heading and the next h2 (or end of file)
        start = match.end()
        if i + 1 < len(headings):
            end = headings[i + 1].start()
        else:
            end = len(html_content)

        content_html = html_content[start:end].strip()

        # Detect structure and extract items
        items, structure = extract_citation_items(content_html)

        sections.append({
            'heading': heading_text,
            'content_html_length': len(content_html),
            'items': items,
            'item_count': len(items),
            'structure': structure,
        })

    return sections


def extract_citation_items(content_html: str) -> tuple[list[str], str]:
    """Extract individual citation strings from a reference section's HTML.

    Returns (items, structure_type).
    """
    items = []

    # Check for <ol>/<ul> with <li> items
    li_pattern = re.compile(r'<li[^>]*>(.*?)</li>', re.IGNORECASE | re.DOTALL)
    li_matches = li_pattern.findall(content_html)

    # Check for <p> tags
    p_pattern = re.compile(r'<p[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)
    p_matches = p_pattern.findall(content_html)

    if li_matches and len(li_matches) > len(p_matches):
        # List structure
        items = [strip_html(li).strip() for li in li_matches if strip_html(li).strip()]
        structure = 'ol_li'
    elif p_matches:
        items = [strip_html(p).strip() for p in p_matches if strip_html(p).strip()]
        # Check if items are numbered
        numbered = sum(1 for item in items if re.match(r'^\d+[\.\)]\s', item))
        if numbered > len(items) * 0.5:
            structure = 'numbered_p'
        else:
            structure = 'p_tags'
    else:
        # Fallback: split on newlines
        lines = [strip_html(line).strip() for line in content_html.split('\n') if strip_html(line).strip()]
        items = [l for l in lines if len(l) > 10]  # skip very short fragments
        structure = 'raw_lines'

    return items, structure


def classify_item(text: str) -> str:
    """Classify a citation item as 'citation', 'note_with_citation', 'note_only', or 'non_citation'."""
    # Very short = probably not a citation
    if len(text) < 15:
        return 'non_citation'

    # Has typical citation markers
    has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', text))
    has_author_pattern = bool(re.search(r'^[A-Z][a-zà-ü]+,?\s', text))
    has_title_marker = bool(re.search(r'["\']|<em>|<i>', text)) or bool(re.search(r'\.\s+[A-Z]', text))
    has_publisher = bool(re.search(r'(Press|Publisher|Books|University|Routledge|Sage|Springer|Wiley|Oxford|Cambridge|London|New York)', text, re.IGNORECASE))
    has_journal = bool(re.search(r'(Journal|Review|Quarterly|Analysis|Psycholog|Psychother|Existential)', text, re.IGNORECASE))
    has_pages = bool(re.search(r'\b\d+[-–]\d+\b', text))
    has_doi = bool(re.search(r'doi[:\s]|10\.\d{4,}/', text, re.IGNORECASE))
    has_url = bool(re.search(r'https?://', text))

    citation_score = sum([has_year, has_author_pattern, has_title_marker, has_publisher, has_journal, has_pages, has_doi, has_url])

    # Starts with a number = likely a note/endnote
    starts_with_number = bool(re.match(r'^\d+[\.\)]\s', text))

    if starts_with_number:
        if citation_score >= 2:
            return 'note_with_citation'
        else:
            return 'note_only'
    elif citation_score >= 2:
        return 'citation'
    elif citation_score == 1 and has_year:
        return 'citation'  # Year alone is a strong signal for references
    else:
        return 'non_citation'


def audit_html_file(html_path: Path, section_name: str) -> dict:
    """Audit a single HTML galley file."""
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    is_auto_extracted = '<!-- AUTO-EXTRACTED -->' in content

    sections = find_reference_sections(content)

    # Classify items
    all_items = []
    item_classifications = Counter()

    for sec in sections:
        for item_text in sec['items']:
            classification = classify_item(item_text)
            item_classifications[classification] += 1
            all_items.append({
                'text': item_text[:200],  # truncate for report
                'full_length': len(item_text),
                'classification': classification,
                'heading': sec['heading'],
            })

    return {
        'file': str(html_path.relative_to(OUTPUT_DIR.parent)),
        'section': section_name,
        'is_auto_extracted': is_auto_extracted,
        'reference_sections': [{
            'heading': s['heading'],
            'item_count': s['item_count'],
            'structure': s['structure'],
            'content_length': s['content_html_length'],
        } for s in sections],
        'total_items': len(all_items),
        'classifications': dict(item_classifications),
        'has_references': len(sections) > 0,
        'headings_found': [s['heading'] for s in sections],
        'sample_items': all_items[:5],  # first 5 for spot-checking
    }


def main():
    parser = argparse.ArgumentParser(description='Audit HTML galleys for citations')
    parser.add_argument('--volume', help='Audit a single volume (e.g. 37.1)')
    parser.add_argument('--verbose', action='store_true', help='Print details for each file')
    args = parser.parse_args()

    # Find all toc.json files
    if args.volume:
        toc_files = [OUTPUT_DIR / args.volume / 'toc.json']
        if not toc_files[0].exists():
            print(f"ERROR: {toc_files[0]} not found")
            sys.exit(1)
    else:
        toc_files = sorted(OUTPUT_DIR.glob('*/toc.json'))

    print(f"Auditing {len(toc_files)} issue(s)...\n")

    # Aggregate stats
    total_html = 0
    total_with_refs = 0
    total_without_refs = 0
    total_citations = 0
    total_notes = 0
    heading_counts = Counter()
    structure_counts = Counter()
    classification_counts = Counter()
    by_section = defaultdict(lambda: {'total': 0, 'with_refs': 0, 'without_refs': 0, 'citations': 0})
    edge_cases = []
    all_results = []
    auto_extracted_files = []
    multi_section_files = []

    for toc_path in toc_files:
        vol_dir = toc_path.parent
        vol_name = vol_dir.name

        with open(toc_path) as f:
            toc = json.load(f)

        articles = toc.get('articles', [])

        for article in articles:
            section = article.get('section', 'Unknown')
            slug = article.get('split_pdf', '').replace('.pdf', '')
            if not slug:
                # Try to construct from sequence
                continue

            html_path = vol_dir / f"{slug}.html"
            if not html_path.exists():
                continue

            total_html += 1
            result = audit_html_file(html_path, section)
            all_results.append(result)

            sec_stats = by_section[section]
            sec_stats['total'] += 1

            if result['has_references']:
                total_with_refs += 1
                sec_stats['with_refs'] += 1

                for sec in result['reference_sections']:
                    heading_counts[sec['heading']] += 1
                    structure_counts[sec['structure']] += 1

                citation_count = result['classifications'].get('citation', 0) + result['classifications'].get('note_with_citation', 0)
                note_count = result['classifications'].get('note_only', 0)
                total_citations += citation_count
                total_notes += note_count
                sec_stats['citations'] += citation_count

                for cls, count in result['classifications'].items():
                    classification_counts[cls] += count
            else:
                total_without_refs += 1
                sec_stats['without_refs'] += 1

            # Flag edge cases
            if result['is_auto_extracted']:
                auto_extracted_files.append(result['file'])

            if len(result['reference_sections']) > 1:
                multi_section_files.append({
                    'file': result['file'],
                    'headings': result['headings_found'],
                })

            # Flag mixed notes/citations
            if result['classifications'].get('note_only', 0) > 0 and result['classifications'].get('citation', 0) > 0:
                edge_cases.append({
                    'file': result['file'],
                    'issue': 'mixed_notes_and_citations',
                    'notes': result['classifications'].get('note_only', 0),
                    'citations': result['classifications'].get('citation', 0),
                })

            # Flag non-citation content in reference sections
            non_cite = result['classifications'].get('non_citation', 0)
            if non_cite > 0:
                edge_cases.append({
                    'file': result['file'],
                    'issue': 'non_citation_in_refs',
                    'count': non_cite,
                })

            if args.verbose:
                status = "✓" if result['has_references'] else "✗"
                items = result['total_items']
                headings = ', '.join(result['headings_found']) or 'none'
                print(f"  {status} {result['file']}: {items} items [{headings}]")

    # Build report
    report = {
        'total_html_files': total_html,
        'with_reference_section': total_with_refs,
        'without_reference_section': total_without_refs,
        'total_citation_items': total_citations,
        'total_note_items': total_notes,
        'heading_variants': dict(heading_counts.most_common()),
        'structure_variants': dict(structure_counts.most_common()),
        'item_classifications': dict(classification_counts.most_common()),
        'by_section': {k: dict(v) for k, v in sorted(by_section.items())},
        'auto_extracted_files': auto_extracted_files,
        'multi_section_files': multi_section_files,
        'edge_cases_count': len(edge_cases),
        'edge_cases': edge_cases[:50],  # cap at 50 to keep report readable
        'sample_articles_with_refs': [
            r for r in all_results if r['has_references']
        ][:10],
        'sample_articles_without_refs': [
            r for r in all_results if not r['has_references']
        ][:10],
    }

    # Write report
    with open(REPORT_PATH, 'w') as f:
        json.dump(report, f, indent=2)

    # Print summary
    print("=" * 60)
    print("CITATION AUDIT REPORT")
    print("=" * 60)
    print(f"\nTotal HTML files scanned:    {total_html}")
    print(f"With reference section:      {total_with_refs} ({total_with_refs*100//max(total_html,1)}%)")
    print(f"Without reference section:   {total_without_refs} ({total_without_refs*100//max(total_html,1)}%)")
    print(f"\nTotal citation items:        {total_citations}")
    print(f"Total note-only items:       {total_notes}")

    print(f"\n--- Heading variants ---")
    for heading, count in heading_counts.most_common():
        print(f"  {heading:30s} {count:5d}")

    print(f"\n--- Structure variants ---")
    for structure, count in structure_counts.most_common():
        print(f"  {structure:30s} {count:5d}")

    print(f"\n--- Item classifications ---")
    for cls, count in classification_counts.most_common():
        print(f"  {cls:30s} {count:5d}")

    print(f"\n--- By section ---")
    for section, stats in sorted(by_section.items()):
        total = stats['total']
        with_refs = stats['with_refs']
        cites = stats['citations']
        print(f"  {section:30s} {total:4d} articles, {with_refs:4d} with refs ({cites} citations)")

    print(f"\n--- Edge cases ---")
    print(f"  Auto-extracted (PyMuPDF):   {len(auto_extracted_files)}")
    print(f"  Multiple ref sections:      {len(multi_section_files)}")
    print(f"  Mixed notes+citations:      {len([e for e in edge_cases if e['issue'] == 'mixed_notes_and_citations'])}")
    print(f"  Non-citation in refs:       {len([e for e in edge_cases if e['issue'] == 'non_citation_in_refs'])}")

    print(f"\nFull report: {REPORT_PATH}")
    print("=" * 60)


if __name__ == '__main__':
    main()
