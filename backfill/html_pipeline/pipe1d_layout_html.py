#!/usr/bin/env python3
"""
Generate HTML galleys from born-digital split PDFs using layout analysis.

Drop-in alternative to pipe1_haiku_html.py for issues typeset in InDesign
(vol 36 onwards). Instead of rendering pages as images and asking a model to
transcribe them, this reads the PDF's own text layer and infers structure from
font, size and geometry. No API calls, no cost, and byte-identical output on
every re-run.

Writes .raw.html next to each split PDF, exactly like pipe1, so the rest of
the pipeline (pipe2 -> pipe6) is unchanged.

Only use this on issues with a real text layer and a consistent template --
run --audit first, which reports whether the layout model fits. Scanned
back-issues still need pipe1.

Usage:
    python3 backfill/html_pipeline/pipe1d_layout_html.py backfill/private/output/37.2/toc.json --audit
    python3 backfill/html_pipeline/pipe1d_layout_html.py backfill/private/output/37.2/toc.json
    python3 backfill/html_pipeline/pipe1d_layout_html.py backfill/private/output/37.2/toc.json --article=3
    python3 backfill/html_pipeline/pipe1d_layout_html.py backfill/private/output/37.2/toc.json --overwrite
"""

import argparse
import html
import json
import os
import re
import sys
import unicodedata

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. pip install pymupdf", file=sys.stderr)
    sys.exit(1)

EXTRACTOR_VERSION = 1  # Bump when the layout model changes; tracked in toc.json

# --- layout model -----------------------------------------------------------
# Tuned against the Existential Analysis InDesign template (vol 36+).
# --audit checks every assumption below against the actual PDF and fails loudly
# rather than quietly producing wrong markup.

FURNITURE_MAX_SIZE = 8.5   # running heads/feet and folios are 7pt
BANNER_MIN_SIZE = 20.0     # "EDITORIAL" / "BOOK REVIEWS" section banners
TITLE_MIN_SIZE = 14.0      # sans bold: article title
BOOK_TITLE_MIN_SIZE = 11.5  # sans bold: book title on a review
BYLINE_MIN_SIZE = 10.0     # sans bold: author byline
HEADING_MIN_SIZE = 11.5    # serif bold: section heading ("Abstract", "Introduction")
SUBHEADING_MIN_SIZE = 9.0  # serif bold-italic: numbered sub-heading

INDENT_MIN = 5.0           # a line is "indented" if x0 exceeds a margin by more than this
INDENT_MAX = 15.0          # ...and by no more than this (the template's indent is ~10pt)
QUOTE_RIGHT_INSET = 8.0    # a quote line falls short of the justified right edge by this
SUP_RATIO = 0.8            # a span smaller than this fraction of the line size is a <sup>

# Headings after which the body switches to hanging indent are detected
# geometrically, not by name -- see _region_is_hanging.


def _norm(s):
    return unicodedata.normalize('NFC', s)


def _is_sans(font):
    return 'Arial' in font or 'Helvetica' in font


def _is_bold(font):
    return 'Bold' in font or 'bold' in font


def _is_italic(font):
    return 'Italic' in font or 'Ital' in font or 'Oblique' in font


class Line:
    __slots__ = ('x0', 'x1', 'y0', 'y1', 'page', 'spans', 'size', 'font', 'text')

    def __init__(self, raw_line, page_index):
        spans = [s for s in raw_line['spans'] if s['text'].strip()]
        self.spans = spans
        self.x0, self.y0, self.x1, self.y1 = raw_line['bbox']
        self.page = page_index
        # Dominant span = the one contributing most characters
        dom = max(spans, key=lambda s: len(s['text']))
        self.size = round(dom['size'], 1)
        self.font = dom['font']
        self.text = _norm(''.join(s['text'] for s in spans))

    @property
    def max_size(self):
        return max(round(s['size'], 1) for s in self.spans)


def read_lines(doc):
    """Flatten a PDF into a list of Line objects in reading order."""
    out = []
    for pi, page in enumerate(doc):
        for block in page.get_text('dict')['blocks']:
            if block['type'] != 0:
                continue
            for raw in block['lines']:
                if not ''.join(s['text'] for s in raw['spans']).strip():
                    continue
                out.append(Line(raw, pi))
    return out


def page_metrics(lines):
    """Per-page left margins and justified right edge, from body text only.

    A page can have more than one left margin: text that wraps around a
    photograph runs in a narrow column with its own margin, and a single
    page-wide margin would read every one of those lines as indented. So
    collect the distinct margins: walk the left edges in order, and treat an
    edge as a new margin unless it sits an indent's width inside one already
    known."""
    metrics = {}
    by_page = {}
    for ln in lines:
        if ln.max_size <= FURNITURE_MAX_SIZE:
            continue
        by_page.setdefault(ln.page, []).append(ln)
    for pi, page_lines in by_page.items():
        margins = []
        for x in sorted(round(l.x0, 1) for l in page_lines):
            if not any(INDENT_MIN < (x - m) <= INDENT_MAX for m in margins):
                if not any(abs(x - m) <= INDENT_MIN for m in margins):
                    margins.append(x)
        metrics[pi] = {
            'margins': margins,
            'right': max(round(l.x1, 1) for l in page_lines),
        }
    return metrics


def is_indented(x0, margins):
    """True if this left edge is an indent off one of the page's margins,
    rather than a margin in its own right."""
    return any(INDENT_MIN < (x0 - m) <= INDENT_MAX for m in margins)


def classify(ln):
    """Return a role for a line: furniture, banner, title, booktitle, byline,
    strapline, heading, subheading or body."""
    if ln.max_size <= FURNITURE_MAX_SIZE:
        return 'furniture'
    sans, bold, ital = _is_sans(ln.font), _is_bold(ln.font), _is_italic(ln.font)
    if sans and bold and ln.size >= BANNER_MIN_SIZE:
        return 'banner'
    if sans and bold and ln.size >= TITLE_MIN_SIZE:
        return 'title'
    if sans and bold and ln.size >= BOOK_TITLE_MIN_SIZE:
        return 'booktitle'
    if sans and bold and ln.size >= BYLINE_MIN_SIZE:
        return 'byline'
    if sans:
        return 'strapline'
    if bold and ln.size >= HEADING_MIN_SIZE:
        return 'heading'
    if bold and ital and ln.size >= SUBHEADING_MIN_SIZE:
        return 'subheading'
    return 'body'


FULL_WIDTH_GAP = 6.0  # a justified line ends this close to the right edge


def _region_is_hanging(region):
    """References/bibliography use a hanging indent (first line flush left,
    continuations indented) -- the mirror image of body paragraphs.

    Tell them apart by what precedes each indented line. In body text an
    indented line opens a paragraph, so the line above it is the previous
    paragraph's last line and falls short of the measure. In a hanging list an
    indented line continues an entry, so the line above it is justified to the
    full measure. Counting lines instead of looking at their neighbours gets
    this wrong whenever most references happen to fit on one line."""
    hanging_votes = body_votes = 0
    for i, (_line, indented, _qw, _gap) in enumerate(region):
        if not indented or i == 0:
            continue
        prev_gap = region[i - 1][3]
        if prev_gap <= FULL_WIDTH_GAP:
            hanging_votes += 1
        else:
            body_votes += 1
    return hanging_votes > body_votes


def _mark_spans(ln):
    """Turn a line's spans into (text, bold, italic, sup) tuples."""
    out = []
    for s in ln.spans:
        size = round(s['size'], 1)
        out.append((
            _norm(s['text']),
            _is_bold(s['font']),
            _is_italic(s['font']),
            size < SUP_RATIO * ln.size,
        ))
    return out


def _join(chunks_a, chunks_b):
    """Append one line's chunks to a running paragraph.

    Two cases take no joining space: a trailing hyphen (this template has
    automatic hyphenation off, so it is always part of a compound word), and a
    URL wrapped across a line (URLs contain no spaces)."""
    if not chunks_a:
        return list(chunks_b)
    prev_text = chunks_a[-1][0].rstrip()
    last_token = prev_text.rsplit(None, 1)[-1] if prev_text.split() else ''
    mid_url = last_token.startswith(('http://', 'https://', 'www.'))
    sep = '' if (prev_text.endswith(('-', '‐', '‑')) or mid_url) else ' '
    merged = list(chunks_a)
    merged[-1] = (prev_text + sep,) + merged[-1][1:]
    merged.extend(chunks_b)
    return merged


def _render(chunks, suppress_bold=False):
    """Render (text, bold, italic, sup) chunks as inline HTML.

    suppress_bold is for headings: the whole line is bold because it *is* a
    heading, so wrapping it in <strong> again adds nothing."""
    if suppress_bold:
        chunks = [(t, False, i, s) for t, _b, i, s in chunks]
    # Merge adjacent chunks sharing a style so we don't emit <em>a</em><em>b</em>
    merged = []
    for c in chunks:
        if merged and merged[-1][1:] == c[1:]:
            merged[-1] = (merged[-1][0] + c[0],) + c[1:]
        else:
            merged.append(c)
    parts = []
    for text, bold, ital, sup in merged:
        esc = html.escape(text, quote=False)
        if not esc.strip():
            parts.append(esc)
            continue
        lead = esc[:len(esc) - len(esc.lstrip())]
        trail = esc[len(esc.rstrip()):]
        core = esc.strip()
        if sup:
            core = f'<sup>{core}</sup>'
        if ital:
            core = f'<em>{core}</em>'
        if bold:
            core = f'<strong>{core}</strong>'
        parts.append(lead + core + trail)
    return re.sub(r'\s+', ' ', ''.join(parts)).strip()


def _flush_para(buf, out, tag='p'):
    if not buf:
        return
    text = _render(buf)
    if text:
        out.append(f'<{tag}>{text}</{tag}>')
    buf.clear()


def _emit_quote(group, out):
    """A blockquote: split into paragraphs where the italic/roman style flips
    (the trailing '(ibid: 18)' citation is set roman)."""
    out.append('<blockquote>')
    buf = []
    prev_ital = None
    for ln in group:
        ital = _is_italic(ln.font)
        if prev_ital is not None and ital != prev_ital:
            _flush_para(buf, out)
        buf = _join(buf, _mark_spans(ln))
        prev_ital = ital
    _flush_para(buf, out)
    out.append('</blockquote>')


def article_html(pdf_path):
    """Convert one split PDF into body-only HTML."""
    doc = fitz.open(pdf_path)
    lines = read_lines(doc)
    metrics = page_metrics(lines)

    # Annotate each line: (line, is_indented, is_quote_width)
    annotated = []
    for ln in lines:
        m = metrics.get(ln.page)
        if m is None:
            continue
        role = classify(ln)
        indented = is_indented(round(ln.x0, 1), m['margins'])
        right_gap = m['right'] - ln.x1
        quote_width = indented and right_gap > QUOTE_RIGHT_INSET
        annotated.append((ln, role, indented, quote_width, right_gap))

    # Split into regions at headings so hanging-indent detection is local.
    # Titles and headings wrap over several lines in this template, so
    # consecutive lines of the same role belong to one element.
    non_body = ('banner', 'title', 'booktitle', 'byline', 'strapline',
                'heading', 'subheading')
    regions = []
    current = []
    for item in annotated:
        role = item[1]
        if role == 'furniture':
            continue
        if role in non_body:
            if regions and current == [] and regions[-1][0][1] == role:
                regions[-1].append(item)   # continuation of the previous heading
                continue
            if current:
                regions.append(current)
                current = []
            regions.append([item])
        else:
            current.append(item)
    if current:
        regions.append(current)

    tag_for = {'banner': 'h1', 'title': 'h1', 'booktitle': 'h1', 'byline': 'h2',
               'strapline': 'p', 'heading': 'h2', 'subheading': 'h3'}

    out = []
    for region in regions:
        role = region[0][1]
        if role in tag_for:
            chunks = []
            for ln, *_rest in region:
                chunks = _join(chunks, _mark_spans(ln))
            text = _render(chunks, suppress_bold=(role != 'strapline'))
            if text:
                tag = tag_for[role]
                out.append(f'<{tag}>{text}</{tag}>')
            continue

        body = [(line, ind, qw, gap) for (line, _role, ind, qw, gap) in region]
        hanging = _region_is_hanging(body)
        buf = []
        quote_run = []

        def flush_quote():
            nonlocal quote_run, buf
            if not quote_run:
                return
            if len(quote_run) >= 2:
                _flush_para(buf, out)
                _emit_quote(quote_run, out)
            else:
                # A lone short indented line is a normal paragraph, not a quote
                _flush_para(buf, out)
                buf = _join(buf, _mark_spans(quote_run[0]))
            quote_run = []

        prev_all_bold = None
        for line, indented, quote_width, _gap in body:
            if not hanging and quote_width:
                quote_run.append(line)
                continue
            flush_quote()
            all_bold = all(_is_bold(s['font']) for s in line.spans)
            starts_new = indented if not hanging else (not indented)
            # A wholly-bold body line is a byline or sign-off, never a
            # continuation of the roman paragraph above it.
            if all_bold or (prev_all_bold is not None and all_bold != prev_all_bold):
                starts_new = True
            if starts_new:
                _flush_para(buf, out)
            buf = _join(buf, _mark_spans(line))
            prev_all_bold = all_bold
        flush_quote()
        _flush_para(buf, out)

    return '\n\n'.join(out) + '\n'


# --- audit ------------------------------------------------------------------

def audit_article(pdf_path, section=''):
    """Check the layout model actually fits this PDF. Returns list of problems."""
    problems = []
    doc = fitz.open(pdf_path)
    lines = read_lines(doc)
    metrics = page_metrics(lines)

    roles = {}
    for ln in lines:
        if ln.page not in metrics:
            continue
        roles[classify(ln)] = roles.get(classify(ln), 0) + 1

    if not roles.get('body'):
        problems.append('no body text found (scanned PDF? wrong template?)')
    # Editorials and the book-review intro run under a section banner
    # ("EDITORIAL" / "BOOK REVIEWS") and carry no title line of their own.
    if section not in ('Editorial', 'Book Review Editorial'):
        if not roles.get('title') and not roles.get('booktitle'):
            problems.append('no title line found')
    elif not roles.get('banner'):
        problems.append(f'{section} has neither a title nor a section banner')

    # Every non-furniture character in the PDF must survive into the HTML.
    produced = article_html(pdf_path)
    stripped = re.sub(r'<[^>]+>', '', produced)
    stripped = html.unescape(stripped)

    def sig(s):
        return re.sub(r'[^0-9a-z]', '', unicodedata.normalize('NFKD', s.lower()))

    src_chars = []
    for ln in lines:
        if ln.page not in metrics:
            continue
        if classify(ln) == 'furniture':
            continue
        src_chars.append(ln.text)
    src = sig(''.join(src_chars))
    got = sig(stripped)
    if src != got:
        # Report the first divergence so it can actually be fixed
        i = 0
        while i < min(len(src), len(got)) and src[i] == got[i]:
            i += 1
        problems.append(
            f'text mismatch at char {i} (source {len(src)} chars, html {len(got)}): '
            f'source={src[max(0, i - 30):i + 30]!r} html={got[max(0, i - 30):i + 30]!r}'
        )
    return problems


# --- driver -----------------------------------------------------------------

def raw_output_path(split_pdf_path):
    return os.path.splitext(split_pdf_path)[0] + '.raw.html'


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('toc_json', nargs='+')
    ap.add_argument('--article', type=int, default=None, help='1-indexed article')
    ap.add_argument('--overwrite', action='store_true', help='regenerate existing .raw.html')
    ap.add_argument('--audit', action='store_true',
                    help='check the layout model fits; write nothing')
    args = ap.parse_args()

    total = written = skipped = failed = 0

    for toc_path in args.toc_json:
        with open(toc_path) as f:
            toc = json.load(f)
        articles = toc.get('articles', [])
        vol_iss = os.path.basename(os.path.dirname(os.path.abspath(toc_path)))

        for idx, art in enumerate(articles):
            if args.article is not None and idx + 1 != args.article:
                continue
            pdf = art.get('split_pdf')
            if not pdf or not os.path.exists(pdf):
                continue
            total += 1
            label = f'{vol_iss}/{idx + 1:02d} {art["title"][:48]}'

            if args.audit:
                problems = audit_article(pdf, art.get('section', ''))
                if problems:
                    failed += 1
                    print(f'  FAIL  {label}')
                    for p in problems:
                        print(f'          {p}')
                else:
                    print(f'  ok    {label}')
                continue

            out_path = raw_output_path(pdf)
            if os.path.exists(out_path) and not args.overwrite:
                skipped += 1
                print(f'  skip  {label} (exists)')
                continue

            problems = audit_article(pdf, art.get('section', ''))
            if problems:
                failed += 1
                print(f'  FAIL  {label}', file=sys.stderr)
                for p in problems:
                    print(f'          {p}', file=sys.stderr)
                continue

            with open(out_path, 'w') as f:
                f.write(article_html(pdf))
            art['_html_extractor'] = f'layout-v{EXTRACTOR_VERSION}'
            written += 1
            print(f'  ok    {label}')

        if not args.audit:
            with open(toc_path, 'w') as f:
                json.dump(toc, f, indent=2, ensure_ascii=False)
                f.write('\n')

    if args.audit:
        print(f'\nAudit: {total - failed}/{total} articles fit the layout model')
    else:
        print(f'\n{written} written, {skipped} skipped, {failed} failed (of {total})')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
