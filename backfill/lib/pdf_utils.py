"""PDF back-matter extraction using PyMuPDF.

Extracts notes, references, and other back-matter sections from the
last half of an article PDF. Used by both the Haiku extraction prompt
(pipe1) and the postprocessing step (pipe2).
"""

import re

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

BACK_MATTER_HEADINGS = re.compile(
    r'^(Notes?|Endnotes?|Footnotes?|References?|Bibliography|Works Cited'
    r'|Further Reading|Selected Bibliography|Notes and References'
    r'|References and Notes)\s*$',
    re.IGNORECASE,
)

NOTES_HEADINGS = re.compile(
    r'^(Notes?|Endnotes?|Footnotes?)\s*$', re.IGNORECASE,
)

# Running headers/footers to strip from PyMuPDF text.
RUNNING_TEXT_RE = re.compile(
    r'^(Existential Analysis.*|Journal of the Society.*|\d{1,3})$',
    re.IGNORECASE,
)


def _clean_pymupdf_html(html):
    """Clean PyMuPDF HTML output for content extraction.

    Strips inline styles, converts <i> to <em>, removes <span> wrappers,
    strips <sup> tags (note numbers handled separately), and normalises
    whitespace.
    """
    # Remove style attributes
    html = re.sub(r'\s+style="[^"]*"', '', html)
    # Convert <i>...</i> to <em>...</em>
    html = html.replace('<i>', '<em>').replace('</i>', '</em>')
    # Remove <b>...</b> (running headers are often bold)
    html = re.sub(r'</?b>', '', html)
    # Remove <span> wrappers (keep content)
    html = re.sub(r'</?span[^>]*>', '', html)
    # Remove <sup> tags (note numbers) — keep content for matching
    html = re.sub(r'</?sup>', '', html)
    # Remove <img> tags
    html = re.sub(r'<img[^>]*>', '', html)
    return html


def _split_html_to_lines(html):
    """Split cleaned PyMuPDF HTML into content lines.

    Extracts text content from <p> elements, preserving <em> tags.
    """
    lines = []
    for m in re.finditer(r'<p[^>]*>(.*?)</p>', html, re.DOTALL):
        content = m.group(1).strip()
        if not content:
            continue
        # Collapse internal whitespace but preserve <em> tags
        content = re.sub(r'\s+', ' ', content)
        # Decode HTML entities
        content = content.replace('&#x2013;', '\u2013').replace('&#x2014;', '\u2014')
        content = content.replace('&#x2018;', '\u2018').replace('&#x2019;', '\u2019')
        content = content.replace('&#x201c;', '\u201c').replace('&#x201d;', '\u201d')
        content = content.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        lines.append(content)
    return lines


def extract_pdf_back_matter(pdf_path, title=None, authors=None):
    """Extract back-matter sections (notes, references) from PDF text.

    Uses PyMuPDF text extraction to find sections by heading. Returns a
    list of dicts: [{'heading': str, 'items': [str], 'is_numbered': bool}].
    Only searches the last 50% of pages (back matter is at the end).

    title/authors: article metadata used to filter running headers.
    """
    if fitz is None:
        raise ImportError("PyMuPDF (fitz) required. Install with: pip install PyMuPDF")

    doc = fitz.open(pdf_path)
    num_pages = len(doc)
    start_page = max(0, int(num_pages * 0.5))

    # Build running-text filter including article-specific headers
    skip_patterns = [RUNNING_TEXT_RE]
    if title:
        skip_patterns.append(re.compile(re.escape(title.strip()), re.IGNORECASE))
    if authors:
        # Author name as it appears in running headers (e.g. "Anthony Stadlen")
        author_str = authors if isinstance(authors, str) else ' and '.join(authors)
        for name in re.split(r'\s*[&,;]\s*|\s+and\s+', author_str):
            name = name.strip()
            if name and len(name) > 3:
                skip_patterns.append(
                    re.compile(r'^' + re.escape(name) + r'$', re.IGNORECASE))

    def is_running_text(line):
        return any(p.match(line) for p in skip_patterns)

    # Collect text lines from the back half.
    # Also collect HTML for formatting preservation (italics via <em>).
    all_lines = []       # plain text — for heading detection and numbering
    all_lines_html = []  # cleaned HTML — for content with <em> tags
    for pg in range(start_page, num_pages):
        text = doc[pg].get_text()
        html = doc[pg].get_text('html')
        # Clean PyMuPDF HTML: strip styles, convert <i> → <em>, remove <span>
        html = _clean_pymupdf_html(html)
        html_lines = _split_html_to_lines(html)
        html_idx = 0

        for line in text.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            # Skip running headers/footers and standalone page numbers
            if is_running_text(stripped):
                html_idx += 1  # advance HTML index too
                continue
            # Find matching HTML line (best-effort: advance to next HTML line)
            html_line = stripped  # fallback to plain text
            if html_idx < len(html_lines):
                html_line = html_lines[html_idx]
                html_idx += 1
            all_lines.append(stripped)
            all_lines_html.append(html_line)
    doc.close()

    # Find heading positions
    heading_positions = []
    for i, line in enumerate(all_lines):
        if BACK_MATTER_HEADINGS.match(line):
            heading_positions.append((i, line))

    if not heading_positions:
        return []

    # Extract items between headings
    sections = []
    for idx, (pos, heading) in enumerate(heading_positions):
        # Items run from after heading to next heading (or end)
        if idx + 1 < len(heading_positions):
            end = heading_positions[idx + 1][0]
        else:
            end = len(all_lines)

        items_lines = all_lines[pos + 1:end]
        items_html = all_lines_html[pos + 1:end]

        # For notes sections, parse numbered items (rejoin continuations)
        is_notes = bool(NOTES_HEADINGS.match(heading))
        if is_notes:
            items, is_numbered = _parse_numbered_items(items_lines, items_html)
        else:
            items = _parse_paragraph_items(items_lines)
            is_numbered = False

        if items:
            sections.append({
                'heading': heading,
                'items': items,
                'is_numbered': is_numbered,
            })

    return sections


def _parse_numbered_items(lines, html_lines=None):
    """Parse sequentially numbered items from lines.

    Returns (items_list, is_numbered). If items aren't numbered,
    falls back to paragraph parsing.

    lines: plain text lines (for structure detection / numbering).
    html_lines: matching HTML lines with <em> tags (for content).
    """
    if html_lines is None:
        html_lines = lines  # fallback: use plain text

    items = {}
    current_num = None
    current_text = ''

    for i, line in enumerate(lines):
        html_line = html_lines[i] if i < len(html_lines) else line
        m = re.match(r'^(\d+)\s*(.+)', line)
        if m and int(m.group(1)) == (current_num or 0) + 1:
            if current_num is not None:
                items[current_num] = current_text.strip()
            current_num = int(m.group(1))
            # Use HTML version for content (strip leading number + any tags/spaces)
            html_content = re.sub(r'^\d+(?:<[^>]*>|\s)*', '', html_line)
            current_text = html_content
        elif m and current_num is None and int(m.group(1)) == 1:
            current_num = 1
            html_content = re.sub(r'^\d+\s+', '', html_line)
            current_text = html_content
        else:
            if current_num is not None:
                current_text += ' ' + html_line

    if current_num is not None:
        items[current_num] = current_text.strip()

    if len(items) >= 3:
        # Clean up whitespace (preserve <em> tags)
        sorted_items = [re.sub(r'\s+', ' ', items[k]).strip()
                        for k in sorted(items.keys())]
        return sorted_items, True

    # Not numbered — fall back to paragraph parsing
    return _parse_paragraph_items(lines), False


def _parse_paragraph_items(lines):
    """Parse paragraph-separated items from lines.

    Joins continuation lines (lines not starting with a capital letter
    after a name pattern) into the previous item.
    """
    items = []
    current = ''

    for line in lines:
        # New item: starts with author-like pattern (Surname, Initial)
        # or is the first line
        if not current or re.match(r'^[A-ZÀ-Ž]', line):
            if current:
                items.append(re.sub(r'\s+', ' ', current).strip())
            current = line
        else:
            current += ' ' + line

    if current:
        items.append(re.sub(r'\s+', ' ', current).strip())

    return items


def build_back_matter_prompt(sections):
    """Build the dynamic prompt appendix from pre-extracted back matter."""
    if not sections:
        return ''

    parts = [
        '\n\nThe following back-matter sections were extracted from the PDF '
        'as plain text. Include all of this content in your HTML with proper '
        'formatting (<em> for italics, etc.). Use the original heading '
        '(e.g. <h2>Notes</h2>).'
    ]

    for sec in sections:
        heading = sec['heading']
        items = sec['items']
        is_numbered = sec['is_numbered']

        if is_numbered:
            parts.append(f'\n=== {heading} ({len(items)} numbered items) ===')
            parts.append(f'Your notes <ol> must contain exactly {len(items)} '
                         f'<li> items, matching the {len(items)} notes below.')
            for i, item in enumerate(items, 1):
                parts.append(f'{i}. {item}')
        else:
            parts.append(f'\n=== {heading} ===')
            for item in items:
                parts.append(item)

    return '\n'.join(parts)
