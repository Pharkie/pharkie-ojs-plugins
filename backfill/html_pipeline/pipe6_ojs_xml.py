#!/usr/bin/env python3
"""
Step 5: Generate OJS Native XML for import.

Takes the TOC JSON (with split PDF paths) and generates an OJS-compatible
Native XML file that can be imported via:
    php tools/importExport.php NativeImportExportPlugin import file.xml journal admin

Usage:
    python backfill/html_pipeline/pipe6_ojs_xml.py <toc.json> [--output import.xml]

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
        'access_status': '1',  # open (free) — book reviews are not paywalled
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

    prepared_pdf = find_issue_pdf(toc_json_path, vol, iss)
    if not prepared_pdf:
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


def find_issue_pdf(toc_json_path, vol, iss):
    """Find the source whole-issue PDF in backfill/private/input/.

    Returns the path or None if not found.
    """
    if not toc_json_path:
        return None
    issue_dir = os.path.dirname(os.path.abspath(toc_json_path))
    project_root = os.path.dirname(os.path.dirname(issue_dir))
    vol_iss = f'{vol}.{iss}' if iss not in (0, '0') else str(vol)
    prepared_pdf = os.path.join(project_root, 'input', f'{vol_iss}.pdf')
    if not os.path.exists(prepared_pdf):
        prepared_pdf = os.path.join(project_root, 'input', f'{vol}.pdf')
    if not os.path.exists(prepared_pdf):
        return None
    return prepared_pdf


def generate_issue_galley(toc_json_path, vol, iss, date_published):
    """Generate XML lines for an issue galley (whole-issue PDF).

    Uses pre-saved cleaned PDFs from backfill/private/output/issue-galleys/ if available,
    otherwise falls back to re-saving through PyMuPDF.
    Returns list of XML lines or empty list.
    """
    if not toc_json_path:
        return []

    issue_dir = os.path.dirname(os.path.abspath(toc_json_path))
    project_root = os.path.dirname(os.path.dirname(issue_dir))
    vol_iss = f'{vol}.{iss}' if iss not in (0, '0') else str(vol)

    # Try pre-saved cleaned PDF first (in the issue's own output folder)
    presaved_pdf = os.path.join(issue_dir, 'issue-galley.pdf')
    if os.path.exists(presaved_pdf):
        with open(presaved_pdf, 'rb') as f:
            clean_pdf = f.read()
        clean_size = len(clean_pdf)
        # Get page count
        if fitz:
            doc = fitz.open(presaved_pdf)
            page_count = len(doc)
            doc.close()
        else:
            page_count = 0
        print(f"Issue galley: {page_count} pages, {clean_size:,} bytes (pre-saved)", file=sys.stderr)
    else:
        # Fall back to re-saving from source
        if fitz is None:
            print("WARNING: PyMuPDF not available, skipping issue galley", file=sys.stderr)
            return []

        source_pdf = find_issue_pdf(toc_json_path, vol, iss)
        if not source_pdf:
            print(f"WARNING: No source PDF found for issue galley (vol {vol} iss {iss})", file=sys.stderr)
            return []

        try:
            doc = fitz.open(source_pdf)
            page_count = len(doc)
            clean_pdf = doc.tobytes(garbage=3, deflate=True)
            doc.close()
        except Exception as e:
            print(f"WARNING: Failed to process issue PDF {source_pdf}: {e}", file=sys.stderr)
            return []

        original_size = os.path.getsize(source_pdf)
        clean_size = len(clean_pdf)
        print(f"Issue galley: {page_count} pages, {original_size:,} → {clean_size:,} bytes", file=sys.stderr)
    vol_iss = f'{vol}.{iss}' if iss not in (0, '0') else str(vol)
    filename = f'vol-{vol}-iss-{iss}.pdf'
    original_filename = f'{vol_iss}.pdf'

    b64_data = base64.b64encode(clean_pdf).decode('ascii')

    lines = []
    lines.append('    <issue_galleys>')
    lines.append('      <issue_galley locale="en">')
    lines.append('        <label>PDF</label>')
    lines.append('        <issue_file>')
    lines.append(f'          <file_name>{filename}</file_name>')
    lines.append('          <file_type>application/pdf</file_type>')
    lines.append(f'          <file_size>{clean_size}</file_size>')
    lines.append('          <content_type>1</content_type>')
    lines.append(f'          <original_file_name>{original_filename}</original_file_name>')
    lines.append(f'          <date_uploaded>{date_published}</date_uploaded>')
    lines.append(f'          <date_modified>{date_published}</date_modified>')
    lines.append(f'          <embed encoding="base64">{b64_data}</embed>')
    lines.append('        </issue_file>')
    lines.append('      </issue_galley>')
    lines.append('    </issue_galleys>')
    return lines


def _load_jats_tree(pdf_path):
    """Load and parse the JATS XML file for an article.

    Looks for {split_pdf_stem}.jats.xml next to the split PDF.
    Returns an ElementTree or None if no JATS file exists.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return None

    jats_path = os.path.splitext(pdf_path)[0] + '.jats.xml'
    if not os.path.exists(jats_path):
        return None

    try:
        return ET.parse(jats_path)
    except ET.ParseError:
        return None


def _load_jats_references(pdf_path):
    """Load references from the article's JATS XML file.

    Returns a list of raw citation strings, or empty list if no JATS file.
    """
    tree = _load_jats_tree(pdf_path)
    if tree is None:
        return []

    refs = []
    for ref in tree.findall('.//{*}mixed-citation'):
        text = ref.text
        if text and text.strip():
            refs.append(text.strip())
    return refs


def _load_jats_subtitle(pdf_path):
    """Load subtitle from the article's JATS XML file.

    JATS is the single source of truth for article content.
    Returns subtitle string or None if not found.
    """
    tree = _load_jats_tree(pdf_path)
    if tree is None:
        return None
    sub_el = tree.find('.//{*}subtitle')
    return sub_el.text.strip() if sub_el is not None and sub_el.text else None


def _load_jats_doi(pdf_path):
    """Load DOI from the article's JATS XML file.

    JATS is the single source of truth for article content.
    Returns DOI string or None if not found.
    """
    tree = _load_jats_tree(pdf_path)
    if tree is None:
        return None
    doi_el = tree.find('.//{*}article-id[@pub-id-type="doi"]')
    return doi_el.text.strip() if doi_el is not None and doi_el.text else None



def _load_jats_source(pdf_path):
    """Load book review source citation from the article's JATS <product>.

    JATS is the single source of truth for article content.
    Returns author-first citation string, or None if no <product> found.
    """
    tree = _load_jats_tree(pdf_path)
    if tree is None:
        return None

    product = tree.find('.//{*}product')
    if product is None:
        return None

    # Authors from person-group
    authors = []
    for pg in product:
        tag = pg.tag.split('}')[-1] if '}' in pg.tag else pg.tag
        if tag == 'person-group':
            for name_el in pg:
                ntag = name_el.tag.split('}')[-1] if '}' in name_el.tag else name_el.tag
                if ntag == 'name':
                    given_el = name_el.find('{*}given-names')
                    if given_el is None:
                        given_el = name_el.find('given-names')
                    surname_el = name_el.find('{*}surname')
                    if surname_el is None:
                        surname_el = name_el.find('surname')
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

    source_el = product.find('{*}source')
    if source_el is None:
        source_el = product.find('source')
    title_text = source_el.text.strip() if source_el is not None and source_el.text else ''

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

    # Author-first citation: Author (Year). Title. Publisher
    citation_parts = []
    if authors:
        citation_parts.append(', '.join(authors))
    if year_text:
        citation_parts.append(f'({year_text})')
    prefix = ' '.join(citation_parts)

    result_parts = []
    if prefix:
        result_parts.append(prefix + '.')
    if title_text:
        title_end = title_text if title_text.endswith(('?', '!', '.')) else title_text + '.'
        result_parts.append(title_end)
    if publisher_text:
        result_parts.append(publisher_text)
    return ' '.join(result_parts) if result_parts else None


def _load_jats_pages(pdf_path):
    """Load page numbers from the article's JATS XML file.

    JATS is the single source of truth for article content.
    Returns (fpage, lpage) as strings, or (None, None) if not found.
    """
    tree = _load_jats_tree(pdf_path)
    if tree is None:
        return None, None

    fpage_el = tree.find('.//{*}fpage')
    lpage_el = tree.find('.//{*}lpage')
    fpage = fpage_el.text.strip() if fpage_el is not None and fpage_el.text else None
    lpage = lpage_el.text.strip() if lpage_el is not None and lpage_el.text else None
    return fpage, lpage


def load_html_galley(pdf_path):
    """Load pre-generated HTML galley for an article.

    Looks for {split_pdf_stem}.html next to the split PDF (generated by htmlgen.py).
    Wraps the body content in a full HTML document for OJS import.
    Returns HTML string or None if no pre-generated file exists.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return None

    html_path = os.path.splitext(pdf_path)[0] + '.galley.html'
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


def generate_article_xml(article, article_idx, date_published, indent='      ', doi=None, subtitle=None, enrichment=None):
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

    # Sequential IDs for file references within the XML (placeholders, OJS assigns its own)
    # Publisher-id (OJS submission_id) lives in JATS only — restore_ids.py applies it post-import
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
    if subtitle:
        lines.append(f'{i3}<subtitle locale="en">{escape(subtitle)}</subtitle>')

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

    # Source — book review citation from JATS <product> (single source of truth)
    source_citation = _load_jats_source(pdf_path)
    if source_citation:
        lines.append(f'{i3}<source locale="en">{escape(source_citation)}</source>')

    # Pages — from JATS (single source of truth for article content)
    page_start, page_end = _load_jats_pages(pdf_path)
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

    # Citations (from JATS XML — the single source of truth for article content)
    jats_refs = _load_jats_references(pdf_path)
    if jats_refs:
        lines.append(f'{i3}<citations>')
        for ref in jats_refs:
            lines.append(f'{i4}<citation>{escape(ref)}</citation>')
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


def generate_xml(toc_data, toc_json_path=None, skip_issue_galley=False, **_kwargs):
    """Generate complete OJS Native XML for an issue.

    DOIs and publisher-IDs are read from JATS (single source of truth).
    """
    vol = toc_data.get('volume', 1)
    iss = toc_data.get('issue', 1)
    date_str = toc_data.get('date')
    date_published = parse_date(date_str)
    year = date_published[:4]

    # Vol 1 canonical date is 1990 (founding), not 1994 (reprint). Guard against regression.
    if vol == 1 and iss == 1 and year != '1990':
        raise ValueError(
            f"Vol 1 Issue 1 date must be 1990 (founding year), not {year}. "
            f"The PDF says 1994 but that's the reprint date. Fix toc.json."
        )

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
    # Issue-level IDs (from toc.json)
    issue_id = toc_data.get('issue_id')
    if issue_id:
        lines.append(f'    <id type="internal" advice="ignore">{issue_id}</id>')
    issue_doi = toc_data.get('issue_doi')
    if issue_doi:
        lines.append(f'    <id type="doi" advice="update">{escape(issue_doi)}</id>')
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

    # Issue galley (whole-issue PDF)
    if not skip_issue_galley:
        galley_lines = generate_issue_galley(toc_json_path, vol, iss, date_published)
        lines.extend(galley_lines)

    # Articles
    lines.append(f'    <articles>')
    doi_count = 0
    for idx, article in enumerate(toc_data['articles']):
        doi = _load_jats_doi(article.get('split_pdf'))
        if doi:
            doi_count += 1
        subtitle = _load_jats_subtitle(article.get('split_pdf'))
        review_id = article.get('_review_id', '')
        article_enrichment = enrichment_data.get(review_id, {}) if enrichment_data else None
        lines.append(generate_article_xml(article, idx, date_published, indent='      ',
                                          doi=doi, subtitle=subtitle,
                                          enrichment=article_enrichment))
    lines.append(f'    </articles>')
    if doi_count > 0 or issue_doi:
        parts = []
        if doi_count > 0:
            parts.append(f"{doi_count} articles")
        if issue_doi:
            parts.append("1 issue")
        print(f"DOIs preserved: {', '.join(parts)}", file=sys.stderr)

    # Close
    lines.append(f'  </issue>')
    lines.append(f'</issues>')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Generate OJS Native XML from TOC JSON')
    parser.add_argument('toc_json', help='TOC JSON file (see docs/backfill-toc-guide.md)')
    parser.add_argument('--output', '-o',
                        help='Output XML file (default: import.xml next to toc.json)')
    parser.add_argument('--no-pdfs', action='store_true',
                        help='Skip PDF embedding (much faster, for testing XML structure)')
    parser.add_argument('--no-issue-galley', action='store_true',
                        help='Skip whole-issue PDF galley (article galleys still included)')
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

    xml = generate_xml(toc_data, toc_json_path=args.toc_json,
                       skip_issue_galley=args.no_issue_galley)

    # Validate generated XML
    try:
        root = ET.fromstring(xml)
        ns = 'http://pkp.sfu.ca'

        def find(parent, path):
            """Find element with or without namespace."""
            result = parent.find(path.replace('/', f'/{{{ns}}}').replace(f'/{{{ns}}}/', f'/{{{ns}}}'), )
            if result is None:
                # Try with full namespace prefix on each tag
                parts = path.split('/')
                prefixed = '/'.join(f'{{{ns}}}{p}' if p and not p.startswith('.') else p for p in parts)
                result = parent.find(prefixed)
            if result is None:
                result = parent.find(path)  # try without namespace
            return result

        def findall(parent, path):
            parts = path.split('/')
            prefixed = '/'.join(f'{{{ns}}}{p}' if p and not p.startswith('.') else p for p in parts)
            result = parent.findall(prefixed)
            if not result:
                result = parent.findall(path)
            return result

        # Root is <issues> (possibly namespaced), children are <issue>
        issue = find(root, 'issue')
        if root.tag.endswith('}issue') or root.tag == 'issue':
            issue = root
        if issue is None:
            raise ValueError("No <issue> element found")
        articles = findall(issue, './/article')
        if not articles:
            raise ValueError("No <article> elements found")
        # Check issue galley if expected
        if not args.no_issue_galley:
            galley = find(issue, './/issue_galleys/issue_galley')
            if galley is not None:
                embed = find(galley, './/issue_file/embed')
                if embed is None or not embed.text or len(embed.text) < 100:
                    raise ValueError("Issue galley embed is empty or truncated")
        # Check article galleys have content
        if not args.no_pdfs:
            for art in articles:
                art_galleys = findall(art, './/article_galley')
                if not art_galleys:
                    title_el = find(art, './/title')
                    title = title_el.text if title_el is not None else '?'
                    print(f"WARNING: Article '{title}' has no galleys", file=sys.stderr)
        print(f"XML valid: {len(articles)} articles", file=sys.stderr)
    except (ET.ParseError, ValueError) as e:
        print(f"ERROR: XML validation failed: {e}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or os.path.join(os.path.dirname(args.toc_json), 'import.xml')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(xml)
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"Written to {output_path} ({size_mb:.1f}MB)", file=sys.stderr)


if __name__ == '__main__':
    main()
