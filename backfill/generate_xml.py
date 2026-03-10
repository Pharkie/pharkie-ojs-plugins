#!/usr/bin/env python3
"""
Step 5: Generate OJS Native XML for import.

Takes the TOC JSON (with split PDF paths) and generates an OJS-compatible
Native XML file that can be imported via:
    php tools/importExport.php NativeImportExportPlugin import file.xml journal admin

Usage:
    python backfill/generate_xml.py <toc.json> [--output import.xml]

The XML includes:
- Issue metadata (volume, number, year, date)
- Sections (Editorial, Articles, Book Review Editorial, Book Reviews)
- Articles with title, authors, abstract, keywords
- PDF galleys embedded as base64
"""

import sys
import os
import re
import json
import base64
import argparse
from xml.sax.saxutils import escape
from datetime import datetime


# Section config: ref, title, abbreviation, access_status, seq
SECTIONS = {
    'Editorial': {
        'ref': 'ED', 'title': 'Editorial', 'abbrev': 'ED',
        'access_status': '1',  # open (free)
        'seq': 0,
        'abstracts_not_required': '1',
        'meta_reviewed': '0',
    },
    'Articles': {
        'ref': 'ART', 'title': 'Articles', 'abbrev': 'ART',
        'access_status': '0',  # subscription (paywalled)
        'seq': 1,
        'abstracts_not_required': '0',
        'meta_reviewed': '1',
    },
    'Book Review Editorial': {
        'ref': 'bookeditorial', 'title': 'Book Review Editorial', 'abbrev': 'bookeditorial',
        'access_status': '1',  # open (free)
        'seq': 2,
        'abstracts_not_required': '1',
        'meta_reviewed': '0',
    },
    'Book Reviews': {
        'ref': 'BR', 'title': 'Book Reviews', 'abbrev': 'BR',
        'access_status': '0',  # subscription (paywalled)
        'seq': 3,
        'abstracts_not_required': '1',
        'meta_reviewed': '0',
    },
}

# Month name to number
MONTH_MAP = {
    'January': '01', 'February': '02', 'March': '03', 'April': '04',
    'May': '05', 'June': '06', 'July': '07', 'August': '08',
    'September': '09', 'October': '10', 'November': '11', 'December': '12',
}


def parse_date(date_str):
    """Convert 'January 2026' to '2026-01-01'."""
    if not date_str:
        return datetime.now().strftime('%Y-%m-%d')
    parts = date_str.split()
    if len(parts) == 2:
        month = MONTH_MAP.get(parts[0], '01')
        year = parts[1]
        return f'{year}-{month}-01'
    return datetime.now().strftime('%Y-%m-%d')


def split_author_name(full_name):
    """Split 'Kim Loliya' into ('Kim', 'Loliya').

    Handles:
    - 'Emmy van Deurzen' -> ('Emmy', 'van Deurzen')
    - 'Sheba Boakye-Duah & Neresia Osbourne' -> [('Sheba', 'Boakye-Duah'), ('Neresia', 'Osbourne')]
    - 'Michael R. Montgomery & Noah Cebuliak' -> [('Michael R.', 'Montgomery'), ('Noah', 'Cebuliak')]
    """
    if not full_name:
        return [('', '')]

    # Split on & for multiple authors
    parts = [a.strip() for a in full_name.split('&')]
    authors = []

    for name in parts:
        if not name:
            continue
        words = name.split()
        if len(words) == 1:
            authors.append(('', words[0]))
        elif len(words) == 2:
            authors.append((words[0], words[1]))
        else:
            # Check for particles: van, de, du, von, etc.
            particles = {'van', 'de', 'du', 'von', 'di', 'la', 'le', 'el'}
            # Find where family name starts
            family_start = len(words) - 1
            for i in range(1, len(words)):
                if words[i].lower() in particles:
                    family_start = i
                    break
                # If it's the last word and previous weren't particles
                if i == len(words) - 1:
                    family_start = i
            given = ' '.join(words[:family_start])
            family = ' '.join(words[family_start:])
            authors.append((given, family))

    return authors if authors else [('', '')]


def encode_pdf(pdf_path):
    """Read a PDF file and return base64-encoded content."""
    with open(pdf_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


def generate_article_xml(article, article_idx, date_published, indent='      '):
    """Generate XML for a single article."""
    i = indent
    i2 = indent + '  '
    i3 = indent + '    '
    i4 = indent + '      '

    section_config = SECTIONS.get(article['section'], SECTIONS['Articles'])
    section_ref = section_config['ref']
    access_status = section_config['access_status']

    title = escape(article['title'])
    abstract = article.get('abstract', '')
    keywords = article.get('keywords', [])
    authors_raw = article.get('authors', '')

    # Parse authors
    if authors_raw:
        author_pairs = split_author_name(authors_raw)
    else:
        author_pairs = []

    # PDF file
    pdf_path = article.get('split_pdf')
    has_pdf = pdf_path and os.path.exists(pdf_path)

    # IDs (sequential, will be ignored on import per advice="ignore")
    file_id = 1000 + article_idx
    submission_file_id = 2000 + article_idx
    pub_id = 3000 + article_idx

    lines = []

    # Article open
    lines.append(f'{i}<article xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                 f' locale="en" date_submitted="{date_published}" status="3"'
                 f' submission_progress="" current_publication_id="{pub_id}"'
                 f' stage="production">')
    lines.append(f'{i2}<id type="internal" advice="ignore">{pub_id}</id>')

    # Submission file (PDF)
    if has_pdf:
        pdf_name = escape(os.path.basename(pdf_path))
        pdf_size = os.path.getsize(pdf_path)
        pdf_b64 = encode_pdf(pdf_path)

        lines.append(f'{i2}<submission_file xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                     f' id="{submission_file_id}" created_at="{date_published}"'
                     f' file_id="{file_id}" stage="proof"'
                     f' updated_at="{date_published}" viewable="false"'
                     f' genre="Article Text" uploader="admin"'
                     f' xsi:schemaLocation="http://pkp.sfu.ca native.xsd">')
        lines.append(f'{i3}<name locale="en">{pdf_name}</name>')
        lines.append(f'{i3}<file id="{file_id}" filesize="{pdf_size}" extension="pdf">')
        lines.append(f'{i4}<embed encoding="base64">{pdf_b64}</embed>')
        lines.append(f'{i3}</file>')
        lines.append(f'{i2}</submission_file>')

    # Publication
    lines.append(f'{i2}<publication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                 f' version="1" status="3" url_path="" seq="{article_idx}"'
                 f' access_status="{access_status}"'
                 f' date_published="{date_published}"'
                 f' section_ref="{section_ref}"'
                 f' xsi:schemaLocation="http://pkp.sfu.ca native.xsd">')
    lines.append(f'{i3}<id type="internal" advice="ignore">{pub_id}</id>')
    lines.append(f'{i3}<title locale="en">{title}</title>')

    # Abstract
    if abstract:
        lines.append(f'{i3}<abstract locale="en">&lt;p&gt;{escape(abstract)}&lt;/p&gt;</abstract>')

    # Copyright
    if author_pairs:
        first_author = f'{author_pairs[0][0]} {author_pairs[0][1]}'.strip()
        lines.append(f'{i3}<copyrightHolder locale="en">{escape(first_author)} (Author)</copyrightHolder>')
    year = date_published[:4]
    lines.append(f'{i3}<copyrightYear>{year}</copyrightYear>')

    # Keywords
    if keywords:
        lines.append(f'{i3}<keywords locale="en">')
        for kw in keywords:
            lines.append(f'{i4}<keyword>{escape(kw)}</keyword>')
        lines.append(f'{i3}</keywords>')

    # Authors
    if author_pairs:
        lines.append(f'{i3}<authors xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                     f' xsi:schemaLocation="http://pkp.sfu.ca native.xsd">')
        for a_idx, (given, family) in enumerate(author_pairs):
            a_id = pub_id * 10 + a_idx
            lines.append(f'{i4}<author include_in_browse="true" user_group_ref="Author"'
                         f' seq="{a_idx}" id="{a_id}">')
            lines.append(f'{i4}  <givenname locale="en">{escape(given)}</givenname>')
            lines.append(f'{i4}  <familyname locale="en">{escape(family)}</familyname>')
            lines.append(f'{i4}  <country>GB</country>')
            # Email is required by OJS — use a placeholder
            email = f'{given.lower().replace(" ", "")}.{family.lower().replace(" ", "")}@placeholder.invalid'
            email = re.sub(r'[^a-z0-9.@_-]', '', email)
            lines.append(f'{i4}  <email>{email}</email>')
            lines.append(f'{i4}</author>')
        lines.append(f'{i3}</authors>')

    # Galley (PDF link)
    if has_pdf:
        lines.append(f'{i3}<article_galley xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                     f' locale="en" approved="false"'
                     f' xsi:schemaLocation="http://pkp.sfu.ca native.xsd">')
        lines.append(f'{i4}<id type="internal" advice="ignore">{pub_id + 5000}</id>')
        lines.append(f'{i4}<name locale="en">PDF</name>')
        lines.append(f'{i4}<seq>0</seq>')
        lines.append(f'{i4}<submission_file_ref id="{submission_file_id}"/>')
        lines.append(f'{i3}</article_galley>')

    lines.append(f'{i2}</publication>')
    lines.append(f'{i}</article>')

    return '\n'.join(lines)


def generate_xml(toc_data):
    """Generate complete OJS Native XML for an issue."""
    vol = toc_data.get('volume', 1)
    iss = toc_data.get('issue', 1)
    date_str = toc_data.get('date')
    date_published = parse_date(date_str)
    year = date_published[:4]

    # Determine which sections are actually used
    used_sections = set()
    for article in toc_data['articles']:
        used_sections.add(article['section'])

    lines = []

    # XML header
    lines.append('<?xml version="1.0" encoding="utf-8"?>')
    lines.append('<issues xmlns="http://pkp.sfu.ca"'
                 ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                 ' xsi:schemaLocation="http://pkp.sfu.ca native.xsd">')

    # Issue
    lines.append(f'  <issue xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                 f' published="1" current="0" access_status="2" url_path="">')
    lines.append(f'    <issue_identification>')
    lines.append(f'      <volume>{vol}</volume>')
    lines.append(f'      <number>{iss}</number>')
    lines.append(f'      <year>{year}</year>')
    lines.append(f'      <title locale="en">Existential Analysis</title>')
    lines.append(f'    </issue_identification>')
    lines.append(f'    <date_published>{date_published}</date_published>')
    lines.append(f'    <last_modified>{date_published}</last_modified>')

    # Sections
    lines.append(f'    <sections>')
    for section_name, config in SECTIONS.items():
        if section_name in used_sections:
            lines.append(f'      <section ref="{config["ref"]}" seq="{config["seq"]}"'
                         f' editor_restricted="0" meta_indexed="1"'
                         f' meta_reviewed="{config["meta_reviewed"]}"'
                         f' abstracts_not_required="{config["abstracts_not_required"]}"'
                         f' hide_title="0" hide_author="0" abstract_word_count="0">')
            lines.append(f'        <abbrev locale="en">{config["abbrev"]}</abbrev>')
            lines.append(f'        <title locale="en">{config["title"]}</title>')
            lines.append(f'      </section>')
    lines.append(f'    </sections>')

    # Articles
    lines.append(f'    <articles>')
    for idx, article in enumerate(toc_data['articles']):
        lines.append(generate_article_xml(article, idx, date_published, indent='      '))
    lines.append(f'    </articles>')

    # Close
    lines.append(f'  </issue>')
    lines.append(f'</issues>')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Generate OJS Native XML from TOC JSON')
    parser.add_argument('toc_json', help='TOC JSON file (from parse_toc.py + split.py)')
    parser.add_argument('--output', '-o', help='Output XML file (default: stdout)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Skip PDF embedding (much faster, for testing XML structure)')
    args = parser.parse_args()

    with open(args.toc_json) as f:
        toc_data = json.load(f)

    if args.dry_run:
        # Remove PDF paths so they won't be embedded
        for article in toc_data['articles']:
            article.pop('split_pdf', None)

    print(f"Generating XML for Vol {toc_data.get('volume')}.{toc_data.get('issue')}", file=sys.stderr)
    print(f"Articles: {len(toc_data['articles'])}", file=sys.stderr)
    if not args.dry_run:
        pdfs = sum(1 for a in toc_data['articles'] if a.get('split_pdf'))
        print(f"PDFs to embed: {pdfs}", file=sys.stderr)

    xml = generate_xml(toc_data)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(xml)
        size_mb = os.path.getsize(args.output) / (1024 * 1024)
        print(f"Written to {args.output} ({size_mb:.1f}MB)", file=sys.stderr)
    else:
        print(xml)


if __name__ == '__main__':
    main()
