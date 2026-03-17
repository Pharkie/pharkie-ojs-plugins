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
import unicodedata
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from datetime import datetime

try:
    import fitz  # PyMuPDF — optional, needed for cover image generation
except ImportError:
    fitz = None


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
    """Convert 'January 2026' to '2026-01-01', or '1991' to '1991-07-01'."""
    if not date_str:
        return datetime.now().strftime('%Y-%m-%d')
    parts = date_str.split()
    if len(parts) == 2:
        month = MONTH_MAP.get(parts[0], '01')
        year = parts[1]
        return f'{year}-{month}-01'
    if len(parts) == 1 and parts[0].isdigit():
        # Year-only date — use mid-year as default
        return f'{parts[0]}-07-01'
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

    # Normalize separators: commas, " and ", " & " all become "&"
    # Handle trailing period (e.g., "Alice Smith and Bob Jones.")
    normalized = full_name.rstrip('. ')
    # Replace ", and " / " and " with "&" (order matters: longest first)
    normalized = re.sub(r',\s+and\s+', ' & ', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s+and\s+', ' & ', normalized, flags=re.IGNORECASE)
    # Replace remaining commas (between authors) with "&"
    normalized = re.sub(r',\s+', ' & ', normalized)

    # Split on & for multiple authors
    parts = [a.strip() for a in normalized.split('&')]
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


def load_doi_registry(script_dir=None):
    """Load the DOI registry (existing Crossref DOIs to preserve).

    Returns a dict keyed by (normalized_title, volume, issue) -> DOI string.
    """
    if script_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    registry_path = os.path.join(script_dir, 'doi-registry.json')
    if not os.path.exists(registry_path):
        return {}
    with open(registry_path) as f:
        data = json.load(f)
    lookup = {}
    for entry in data.get('dois', []):
        key = (_normalize_title(entry['title']), entry['volume'], entry['issue'])
        lookup[key] = entry['doi']
    # Load aliases (TOC title -> Crossref title) for manual overrides
    lookup['_aliases'] = {}
    for toc_title, crossref_title in data.get('aliases', {}).items():
        if toc_title.startswith('_'):
            continue
        lookup['_aliases'][_normalize_title(toc_title)] = _normalize_title(crossref_title)
    return lookup


def _normalize_title(title):
    """Normalize a title for DOI matching (lowercase, collapse whitespace, strip punctuation)."""
    title = title.strip().lower()
    # Collapse whitespace
    title = re.sub(r'\s+', ' ', title)
    # Strip leading/trailing punctuation
    title = title.strip(' .,;:')
    return title


def lookup_doi(doi_registry, title, volume, issue):
    """Look up an existing DOI for an article by title + volume + issue.

    Tries exact match first, then falls back to prefix matching (TOC titles
    often include subtitles that Crossref entries omit). Also handles
    'Book Review: ...' prefixes and editorial naming differences.
    """
    vol = str(volume)
    iss = str(issue)
    norm = _normalize_title(title)

    # Check aliases (manual title overrides)
    aliases = doi_registry.get('_aliases', {})
    if norm in aliases:
        aliased = aliases[norm]
        key = (aliased, vol, iss)
        if key in doi_registry:
            return doi_registry[key]

    # Exact match
    key = (norm, vol, iss)
    if key in doi_registry:
        return doi_registry[key]

    # Strip 'book review: ' prefix and try again
    stripped = re.sub(r'^book reviews?\s*:\s*', '', norm)
    if stripped != norm:
        key = (stripped, vol, iss)
        if key in doi_registry:
            return doi_registry[key]

    # Strip 'obituary: ' prefix — Crossref may use the person's name as title
    stripped = re.sub(r'^obituary\s*:\s*', '', norm)
    if stripped != norm:
        key = (stripped, vol, iss)
        if key in doi_registry:
            return doi_registry[key]

    # Prefix match: TOC title starts with registry title (subtitle after colon)
    for key_entry, doi in doi_registry.items():
        if not isinstance(key_entry, tuple):
            continue
        reg_title, reg_vol, reg_iss = key_entry
        if reg_vol != vol or reg_iss != iss:
            continue
        # Registry title is a prefix of the TOC title (min 20 chars to avoid false positives)
        if len(reg_title) >= 20 and norm.startswith(reg_title):
            return doi
        # TOC title (after stripping prefixes) starts with registry title
        if len(reg_title) >= 20 and stripped != norm and stripped.startswith(reg_title):
            return doi

    # Editorial naming: 'editorial' in TOC vs '{vol}.{iss} editorial' in Crossref
    if norm == 'editorial':
        key = (f'{vol}.{iss} editorial', vol, iss)
        if key in doi_registry:
            return doi_registry[key]

    # 'Book Reviews' (section header) vs 'book reviews editorial' in Crossref
    if norm in ('book reviews', 'book review'):
        key = ('book reviews editorial', vol, iss)
        if key in doi_registry:
            return doi_registry[key]

    return None


def encode_pdf(pdf_path):
    """Read a PDF file and return base64-encoded content."""
    with open(pdf_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


def generate_cover_image(toc_json_path, vol, iss):
    """Extract first page of the issue PDF as a JPEG for the cover image.

    Returns (filename, base64_data) or (None, None) if not available.
    """
    if fitz is None or not toc_json_path:
        return None, None

    # Look for the source issue PDF in backfill/prepared/
    issue_dir = os.path.dirname(os.path.abspath(toc_json_path))
    project_root = os.path.dirname(os.path.dirname(issue_dir))
    vol_iss = f'{vol}.{iss}' if iss not in (0, '0') else str(vol)
    prepared_pdf = os.path.join(project_root, 'prepared', f'{vol_iss}.pdf')

    if not os.path.exists(prepared_pdf):
        # Try integer-only name for single-issue volumes
        prepared_pdf = os.path.join(project_root, 'prepared', f'{vol}.pdf')
    if not os.path.exists(prepared_pdf):
        return None, None

    doc = fitz.open(prepared_pdf)
    page = doc[0]
    # Render at 2x resolution for good quality thumbnails
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img_bytes = pix.tobytes('jpeg', jpg_quality=85)
    doc.close()

    filename = f'cover-{vol}-{iss}.jpg'
    b64_data = base64.b64encode(img_bytes).decode('ascii')
    return filename, b64_data


def load_html_galley(pdf_path):
    """Load pre-generated HTML galley for an article.

    Looks for {split_pdf_stem}.html next to the split PDF (generated by htmlgen.py).
    Wraps the body content in a full HTML document for OJS import.
    Returns HTML string or None if no pre-generated file exists.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return None

    html_path = os.path.splitext(pdf_path)[0] + '.html'
    if not os.path.exists(html_path):
        return None

    with open(html_path, 'r', encoding='utf-8') as f:
        body_content = f.read().strip()

    if not body_content:
        return None

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Full Text</title></head>
<body>
{body_content}
</body>
</html>"""


def generate_article_xml(article, article_idx, date_published, indent='      ', doi=None, enrichment=None):
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
    html_file_id = 6000 + article_idx
    html_submission_file_id = 7000 + article_idx

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

    # HTML galley (from pre-generated .html file, any section)
    html_content = None
    if has_pdf:
        html_content = load_html_galley(pdf_path)
    if html_content:
        html_bytes = html_content.encode('utf-8')
        html_b64 = base64.b64encode(html_bytes).decode('ascii')
        html_filename = os.path.splitext(os.path.basename(pdf_path))[0] + '.html'
        lines.append(f'{i2}<submission_file xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                     f' id="{html_submission_file_id}" created_at="{date_published}"'
                     f' file_id="{html_file_id}" stage="proof"'
                     f' updated_at="{date_published}" viewable="false"'
                     f' genre="Article Text" uploader="admin"'
                     f' xsi:schemaLocation="http://pkp.sfu.ca native.xsd">')
        lines.append(f'{i3}<name locale="en">{escape(html_filename)}</name>')
        lines.append(f'{i3}<file id="{html_file_id}" filesize="{len(html_bytes)}" extension="html">')
        lines.append(f'{i4}<embed encoding="base64">{html_b64}</embed>')
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
    if doi:
        lines.append(f'{i3}<id type="doi" advice="update">{escape(doi)}</id>')
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

    # Subjects (from toc.json, populated by enrichment review)
    subjects = article.get('subjects', [])
    if subjects:
        lines.append(f'{i3}<subjects locale="en">')
        for subj in subjects:
            lines.append(f'{i4}<subject>{escape(subj)}</subject>')
        lines.append(f'{i3}</subjects>')

    # Disciplines (from toc.json, populated by enrichment review)
    disciplines = article.get('disciplines', [])
    if disciplines:
        lines.append(f'{i3}<disciplines locale="en">')
        for disc in disciplines:
            lines.append(f'{i4}<discipline>{escape(disc)}</discipline>')
        lines.append(f'{i3}</disciplines>')

    # Coverage (from enrichment sidecar)
    if enrichment:
        coverage_parts = []
        geo = enrichment.get('geographical_context')
        if geo:
            coverage_parts.append(geo)
        era = enrichment.get('era_focus')
        if era:
            coverage_parts.append(era)
        if coverage_parts:
            lines.append(f'{i3}<coverage locale="en">{escape("; ".join(coverage_parts))}</coverage>')

    # Pages
    page_start = article.get('journal_page_start')
    page_end = article.get('journal_page_end')
    if page_start and page_end:
        lines.append(f'{i3}<pages>{page_start}-{page_end}</pages>')
    elif page_start:
        lines.append(f'{i3}<pages>{page_start}</pages>')

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
            # Email is required by OJS — use a placeholder.
            # Transliterate accented chars (é→e, ü→u) instead of stripping.
            def _ascii(s):
                nfkd = unicodedata.normalize('NFKD', s)
                return ''.join(c for c in nfkd if not unicodedata.combining(c)).lower().replace(' ', '')
            if given:
                email = f'{_ascii(given)}.{_ascii(family)}@placeholder.invalid'
            else:
                email = f'{_ascii(family)}@placeholder.invalid'
            email = re.sub(r'[^a-z0-9.@_-]', '', email)
            # OJS authors.email is varchar(90) — truncate local part if needed
            if len(email) > 90:
                local, domain = email.rsplit('@', 1)
                max_local = 90 - len(domain) - 1  # -1 for @
                email = f'{local[:max_local]}@{domain}'
            lines.append(f'{i4}  <email>{email}</email>')
            lines.append(f'{i4}</author>')
        lines.append(f'{i3}</authors>')

    # Citations (from enrichment sidecar)
    if enrichment and enrichment.get('references'):
        lines.append(f'{i3}<citations>')
        for ref in enrichment['references']:
            author = ref.get('author', '')
            year = ref.get('year', '')
            title = ref.get('title', '')
            citation_str = f'{author} ({year}). {title}.' if year else f'{author}. {title}.'
            lines.append(f'{i4}<citation>{escape(citation_str)}</citation>')
        lines.append(f'{i3}</citations>')

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

    # HTML galley (for inline rendering of open-access articles)
    if html_content:
        lines.append(f'{i3}<article_galley xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                     f' locale="en" approved="false"'
                     f' xsi:schemaLocation="http://pkp.sfu.ca native.xsd">')
        lines.append(f'{i4}<id type="internal" advice="ignore">{pub_id + 9000}</id>')
        lines.append(f'{i4}<name locale="en">Full Text</name>')
        lines.append(f'{i4}<seq>1</seq>')
        lines.append(f'{i4}<submission_file_ref id="{html_submission_file_id}"/>')
        lines.append(f'{i3}</article_galley>')

    lines.append(f'{i2}</publication>')
    lines.append(f'{i}</article>')

    return '\n'.join(lines)


def load_enrichment(toc_json_path):
    """Load enrichment.json from the same directory as toc.json, if it exists.

    Returns: {review_id: article_enrichment_dict} or empty dict.
    """
    if not toc_json_path:
        return {}
    enrichment_path = os.path.join(os.path.dirname(os.path.abspath(toc_json_path)), 'enrichment.json')
    if not os.path.exists(enrichment_path):
        return {}
    with open(enrichment_path) as f:
        data = json.load(f)
    return data.get('articles', {})


def generate_xml(toc_data, doi_registry=None, toc_json_path=None):
    """Generate complete OJS Native XML for an issue."""
    vol = toc_data.get('volume', 1)
    iss = toc_data.get('issue', 1)
    date_str = toc_data.get('date')
    date_published = parse_date(date_str)
    year = date_published[:4]

    if doi_registry is None:
        doi_registry = load_doi_registry()

    enrichment_data = load_enrichment(toc_json_path)

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

    # Cover image (first page of issue PDF)
    cover_filename, cover_b64 = generate_cover_image(toc_json_path, vol, iss)
    if cover_filename and cover_b64:
        lines.append(f'    <covers>')
        lines.append(f'      <cover locale="en">')
        lines.append(f'        <cover_image>{cover_filename}</cover_image>')
        lines.append(f'        <cover_image_alt_text>Vol. {vol} No. {iss} ({year})</cover_image_alt_text>')
        lines.append(f'        <embed encoding="base64">{cover_b64}</embed>')
        lines.append(f'      </cover>')
        lines.append(f'    </covers>')

    # Articles
    lines.append(f'    <articles>')
    doi_count = 0
    for idx, article in enumerate(toc_data['articles']):
        doi = lookup_doi(doi_registry, article['title'], vol, iss)
        if doi:
            doi_count += 1
        review_id = article.get('_review_id', '')
        article_enrichment = enrichment_data.get(review_id, {}) if enrichment_data else None
        lines.append(generate_article_xml(article, idx, date_published, indent='      ',
                                          doi=doi, enrichment=article_enrichment))
    lines.append(f'    </articles>')
    if doi_count > 0:
        print(f"DOIs preserved: {doi_count}", file=sys.stderr)

    # Close
    lines.append(f'  </issue>')
    lines.append(f'</issues>')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Generate OJS Native XML from TOC JSON')
    parser.add_argument('toc_json', help='TOC JSON file (see docs/backfill-toc-guide.md)')
    parser.add_argument('--output', '-o', help='Output XML file (default: stdout)')
    parser.add_argument('--no-pdfs', action='store_true',
                        help='Skip PDF embedding (much faster, for testing XML structure)')
    args = parser.parse_args()

    with open(args.toc_json) as f:
        toc_data = json.load(f)

    if args.no_pdfs:
        # Remove PDF paths so they won't be embedded
        for article in toc_data['articles']:
            article.pop('split_pdf', None)

    print(f"Generating XML for Vol {toc_data.get('volume')}.{toc_data.get('issue')}", file=sys.stderr)
    print(f"Articles: {len(toc_data['articles'])}", file=sys.stderr)
    if not args.no_pdfs:
        pdfs = sum(1 for a in toc_data['articles'] if a.get('split_pdf'))
        print(f"PDFs to embed: {pdfs}", file=sys.stderr)

    xml = generate_xml(toc_data, toc_json_path=args.toc_json)

    # Validate generated XML is well-formed
    try:
        ET.fromstring(xml)
    except ET.ParseError as e:
        print(f"ERROR: Generated XML is malformed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(xml)
        size_mb = os.path.getsize(args.output) / (1024 * 1024)
        print(f"Written to {args.output} ({size_mb:.1f}MB)", file=sys.stderr)
    else:
        print(xml)


if __name__ == '__main__':
    main()
