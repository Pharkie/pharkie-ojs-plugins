"""
Deterministic post-processing pipeline for HTML galleys.

Takes raw HTML from Haiku (full text extraction) and trims it to
body-only content suitable for OJS. All content decisions are here,
not in the Haiku prompt.

Pipeline steps for articles (in order):
1. Strip start bleed (previous article's content at top)
2. Strip article title (may span multiple HTML elements)
3. Strip authors/byline
4. Strip conference note ("Based on presentation...")
5. Strip abstract heading and paragraph(s)
6. Strip keywords line
7. Strip end bleed (next article's content at bottom)

For book reviews: extract just the target review from full-page HTML.

Uses BeautifulSoup4 for all HTML parsing and manipulation. Regex is
only used for text-level operations (word matching, prefix stripping).
"""

import os
import re
import sys

from bs4 import BeautifulSoup, Tag, NavigableString

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.citations import (
    normalise_allcaps, normalise_for_overlap, REFERENCE_HEADING_RE,
    PUBLISHER_NAMES, is_provenance, looks_like_person_name,
)

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# Minimum abstract length worth stripping. Shortest real abstract in dataset
# is 152 chars. Below 30 chars, a toc.json "abstract" is likely a fragment
# or metadata artefact — stripping it risks removing real body content.
MIN_ABSTRACT_LENGTH = 30
#
# Final HTML shorter than this is flagged as empty/broken. Shortest
# legitimate book review in the dataset is ~80 chars of body text.
# Shortest legitimate article is ~200 chars. 100 catches broken
# extractions while allowing very short book reviews.
SHORT_CONTENT_THRESHOLD = 100
# Avoid matching single words or fragments
MIN_TARGET_TEXT_LEN = 5
# Title block should be roughly title-length, not longer
TITLE_BLOCK_MAX_RATIO = 2
# Require matching at least half the title words
TITLE_WORD_MATCH_RATIO = 0.5
# Abstract overlap: fraction of abstract words that must appear in a
# candidate paragraph to consider it a match. Tolerant of OCR errors
# (e.g. "clinicians" → "citizenicians").
ABSTRACT_OVERLAP_THRESHOLD = 0.7
# Fuzzy title verification: fraction of title words (>2 chars) that must
# appear in text. Tolerant of PDF extraction artefacts (fused words,
# reordered fragments).
TITLE_FUZZY_MATCH_THRESHOLD = 0.8
# Use shared normalise_for_overlap as _clean (keeps digits for content matching)
_clean = normalise_for_overlap

# Running header text pattern (plain text, not HTML)
# Matches variations:
#   "Journal of The Society for Existential Analysis"
#   "Existential Analysis: Journal of The Society for Existential Analysis"
#   "Existential Analysis 37.1: January 2026"
#   "62 Existential Analysis: Journal of The Society for Existential Analysis"
_RUNNING_HEADER_TEXT_RE = re.compile(
    r'^\s*\d{0,4}\s*'
    r'(?:Existential\s+Analysis\s*(?:\d+\.\d+\s*)?:\s*)?'
    r'(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*$|'
    r'Journal\s+of\s+(?:The\s+|the\s+)?Society\s+for\s+Existential\s+Analysis\s*$)',
    re.IGNORECASE
)
# Keep the HTML-level regexes for backward compatibility (used by test imports)
RUNNING_HEADER_RE = re.compile(
    r'^\s*<p[^>]*>\s*(?:<(?:em|i|strong|b)>)?\s*\d{0,4}\s*'
    r'(?:Existential\s+Analysis\s*(?:\d+\.\d+\s*)?:\s*)?'
    r'(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}'
    r'|Journal\s+of\s+(?:The\s+|the\s+)?Society\s+for\s+Existential\s+Analysis)'
    r'\s*(?:</(?:em|i|strong|b)>)?\s*</p>\s*$',
    re.IGNORECASE | re.MULTILINE
)
PAGE_NUMBER_RE = re.compile(
    r'^\s*<p[^>]*>\s*\d{1,4}\s*</p>\s*$',
    re.MULTILINE
)

# Element tag sets
BLOCK_TAGS = frozenset({'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'})
HEADING_TAGS = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})
_KEYWORDS_RE = re.compile(r'^Key\s*[Ww]ords?$|^KEYWORDS?$', re.IGNORECASE)
_KEYWORDS_INLINE_RE = re.compile(r'^\s*(?:Key\s*[Ww]ords?|KEYWORDS?)\s*:', re.IGNORECASE)
_PUB_MARKERS_RE = re.compile(
    r'(?:\d{4}|pp\.?\s*\d|ISBN|\$|£|'
    + PUBLISHER_NAMES +
    r'|Oxford|Cambridge|London|New York)', re.IGNORECASE)


# ---------------------------------------------------------------
# BS4 helpers
# ---------------------------------------------------------------

def _parse(html):
    """Parse HTML fragment into BS4 soup."""
    return BeautifulSoup(html, 'html.parser')


def _serialize(soup):
    """Serialize soup back to HTML string."""
    return str(soup)


def _strip_tags(html):
    """Remove HTML tags, returning plain text."""
    return BeautifulSoup(html, 'html.parser').get_text()


def _el_text(el):
    """Get plain text content from a BS4 element."""
    return el.get_text()


def _el_clean_text(el):
    """Get cleaned (normalised) text from a BS4 element."""
    return _clean(_el_text(el))


def _is_block(el):
    """Check if a BS4 element is a block-level element."""
    return isinstance(el, Tag) and el.name in BLOCK_TAGS


def _is_heading(el):
    """Check if a BS4 element is a heading."""
    return isinstance(el, Tag) and el.name in HEADING_TAGS


def _remove_preceding(el):
    """Remove all content before an element (previous siblings)."""
    for sibling in list(el.previous_siblings):
        sibling.extract()


def _remove_from(el):
    """Remove an element and all following siblings."""
    for sibling in list(el.next_siblings):
        sibling.extract()
    el.extract()


def _top_level_blocks(soup):
    """Yield top-level block elements in document order."""
    for el in soup.children:
        if _is_block(el):
            yield el


# ---------------------------------------------------------------
# Text matching (pure text, no HTML)
# ---------------------------------------------------------------

def _text_to_regex(text, flexible=False):
    """Build a regex from text: words in order, flexible non-alpha gaps between.

    Strips toc.json prefixes (Book Review:, Obituary:, etc.) and trailing
    parentheticals before building. Returns compiled regex or None.

    If flexible=True, allows extra words between each keyword (for fuzzy
    title matching in end-bleed detection where heading text may differ
    from toc.json title).
    """
    text = _strip_toc_prefixes(text)
    words = _clean(text).split()
    if not words:
        return None
    if flexible:
        # Allow up to 5 extra words between each keyword
        gap = r'(?:\s+\S+){0,5}\s+'
    else:
        # Words must appear in order; gaps allow any non-alphanumeric chars
        # (whitespace, punctuation, line breaks, HTML residue)
        gap = r'[^a-z0-9]*'
    pattern = gap.join(re.escape(w) for w in words)
    return re.compile(pattern, re.IGNORECASE)


def _title_in_text(title, text):
    """Check if title appears in text as an ordered word sequence.

    No threshold — either the words appear in order or they don't.
    """
    if not title:
        return True
    rx = _text_to_regex(title)
    if rx is None:
        return True
    clean_text = _clean(_strip_tags(text))
    return bool(rx.search(clean_text))


def _find_block_by_text(html, target_text, search_start=0, search_end=None):
    """Find the HTML block whose text matches target_text.

    Uses ordered word-sequence matching (no threshold).
    Returns (start_pos, end_pos) of the matching block, or (None, None).

    Note: This function returns character positions for backward compatibility
    with callers that do string slicing. New code should use _find_block_in_soup.
    """
    if not target_text or len(target_text) < MIN_TARGET_TEXT_LEN:
        return None, None
    if search_end is None:
        search_end = len(html)
    region = html[search_start:search_end]
    rx = _text_to_regex(target_text)
    if rx is None:
        return None, None

    soup = _parse(region)
    for el in soup.find_all(list(BLOCK_TAGS)):
        block_text = _clean(_el_text(el))
        if rx.search(block_text):
            # Find the element's position in the region string
            el_str = str(el)
            pos = region.find(el_str)
            if pos >= 0:
                return (search_start + pos, search_start + pos + len(el_str))

    return None, None


def _find_block_in_soup(soup, target_text):
    """Find a block element in soup whose text matches target_text.

    Returns the BS4 element, or None.
    """
    if not target_text or len(target_text) < MIN_TARGET_TEXT_LEN:
        return None
    rx = _text_to_regex(target_text)
    if rx is None:
        return None
    for el in soup.find_all(list(BLOCK_TAGS)):
        if rx.search(_el_clean_text(el)):
            return el
    return None


def _find_first_body_heading(html):
    """Find the position of the first body content heading (Introduction, etc.).

    Returns the character position, or len(html) if not found.
    Skips "Abstract", "Keywords" headings, and person-name headings
    (author bylines that Haiku sometimes renders as <h2>).
    """
    skip_headings = {'abstract', 'keywords', 'key words'}
    soup = _parse(html)
    for el in soup.find_all(['h2', 'h3']):
        heading_text = _el_text(el).strip()
        if heading_text.lower() in skip_headings:
            continue
        if looks_like_person_name(heading_text):
            continue
        # Find position in original HTML string
        el_str = str(el)
        pos = html.find(el_str)
        if pos >= 0:
            return pos
    return len(html)


def _find_first_body_heading_soup(soup):
    """Find the first body content heading element in soup.

    Returns the BS4 element, or None.
    """
    skip_headings = {'abstract', 'keywords', 'key words'}
    for el in soup.find_all(['h2', 'h3']):
        heading_text = _el_text(el).strip()
        if heading_text.lower() in skip_headings:
            continue
        if looks_like_person_name(heading_text):
            continue
        return el
    return None


# ---------------------------------------------------------------
# Soup-based strip functions (mutate soup in place)
# ---------------------------------------------------------------

def _strip_start_bleed_soup(soup, own_title):
    """Remove content before the article's own title."""
    if not own_title:
        return
    el = _find_block_in_soup(soup, own_title)
    if el is not None:
        _remove_preceding(el)


def _strip_title_soup(soup, title):
    """Remove title elements from the top of the soup.

    Consumes blocks that have word overlap with the title and are
    title-sized (short) or headings. Stops at provenance notes or
    non-matching blocks.
    """
    if not title:
        return
    title_clean = _clean(title)
    if not title_clean:
        return

    title_words = set(title_clean.split())
    title_len = len(title_clean)

    to_remove = []
    matched_words = set()

    for el in _top_level_blocks(soup):
        block_text = _el_clean_text(el)
        block_words = set(block_text.split())
        overlap = title_words & block_words

        # Stop at provenance notes
        raw_text = _el_text(el).strip()
        if is_provenance(raw_text):
            break

        is_title_sized = len(block_text) <= title_len * TITLE_BLOCK_MAX_RATIO
        is_head = _is_heading(el)

        if overlap and (is_title_sized or is_head):
            matched_words |= overlap
            to_remove.append(el)
        else:
            break

    if matched_words and len(matched_words) > len(title_words) * TITLE_WORD_MATCH_RATIO:
        for el in to_remove:
            el.extract()


def _strip_subtitle_soup(soup, subtitle):
    """Remove the subtitle element from the top of the soup."""
    if not subtitle:
        return
    subtitle_clean = _clean(subtitle)
    if not subtitle_clean:
        return

    subtitle_words = set(subtitle_clean.split())

    for i, el in enumerate(_top_level_blocks(soup)):
        if i >= 5:
            break
        block_text = _el_clean_text(el)
        block_words = set(block_text.split())
        overlap = subtitle_words & block_words

        if overlap and len(overlap) > len(subtitle_words) * TITLE_WORD_MATCH_RATIO:
            el.extract()
            return
        elif block_words - subtitle_words:
            return


def _strip_authors_soup(soup, authors):
    """Remove author byline element from the soup."""
    if not authors:
        return
    # Search up to Abstract heading or first body heading
    boundary = _find_first_body_heading_soup(soup)
    # Also check for Abstract heading
    abstract_heading = soup.find(['h2', 'h3'], string=re.compile(r'^\s*Abstract\s*$', re.IGNORECASE))
    if abstract_heading:
        boundary = abstract_heading

    for variant in _author_name_variants(authors):
        rx = _text_to_regex(variant)
        if rx is None:
            continue
        for el in soup.find_all(list(BLOCK_TAGS)):
            # Stop searching past boundary
            if boundary and el == boundary:
                break
            if boundary and _el_comes_after(el, boundary):
                break
            if rx.search(_el_clean_text(el)):
                el.extract()
                return

    # Flexible pass: allow optional middle names/initials in HTML that
    # aren't in toc.json (e.g. toc has "John Heaton", HTML has "John M. Heaton")
    parts = re.split(r',\s*|\s+and\s+', authors)
    flex_parts = []
    for part in parts:
        words = _clean(part).split()
        if len(words) == 2:
            # first + last → allow optional middle names/initials between them
            flex_parts.append(words[0] + r'(?:\s+\S+)*\s+' + words[-1])
    if flex_parts:
        sep = r'[^a-z0-9]+' if len(flex_parts) > 1 else ''
        flex_rx = re.compile(sep.join(flex_parts), re.IGNORECASE)
        for el in soup.find_all(list(BLOCK_TAGS)):
            if boundary and el == boundary:
                break
            if boundary and _el_comes_after(el, boundary):
                break
            if flex_rx.search(_el_clean_text(el)):
                el.extract()
                return


def _el_comes_after(el, boundary):
    """Check if el appears after boundary in document order."""
    # Walk forward from boundary's next siblings
    for sibling in boundary.next_elements:
        if sibling is el:
            return True
    return False


def _strip_abstract_soup(soup, abstract):
    """Remove abstract heading and paragraph from the soup."""
    if not abstract or len(abstract) < MIN_ABSTRACT_LENGTH:
        return

    # Remove "Abstract" heading
    for heading in soup.find_all(['h2', 'h3']):
        if re.match(r'^\s*Abstract\s*$', _el_text(heading), re.IGNORECASE):
            heading.extract()
            break

    # Find and remove abstract paragraph using fuzzy matching
    boundary = _find_first_body_heading_soup(soup)
    abs_clean = _clean(abstract)
    abs_words = set(abs_clean.split())
    if not abs_words:
        return

    for el in soup.find_all('p'):
        # Stop at first body heading
        if boundary and (el == boundary or _el_comes_after(el, boundary)):
            break
        block_text = _el_clean_text(el)
        block_words = set(block_text.split())
        if not block_words:
            continue
        overlap = len(abs_words & block_words) / len(abs_words)
        if overlap > ABSTRACT_OVERLAP_THRESHOLD:
            el.extract()
            return


def _strip_keywords_soup(soup):
    """Remove keywords heading + paragraph, or standalone keywords paragraph."""
    # Pattern 1: Heading (h2/h3 with "Keywords" text) + following paragraph
    for heading in soup.find_all(['h2', 'h3']):
        if _KEYWORDS_RE.match(_el_text(heading).strip()):
            # Remove the heading and the following <p> sibling
            next_p = heading.find_next_sibling('p')
            heading.extract()
            if next_p:
                next_p.extract()
            return

    # Pattern 2: Standalone paragraph starting with "Keywords:"
    for p in soup.find_all('p'):
        if _KEYWORDS_INLINE_RE.match(_el_text(p)):
            p.extract()
            return


# How far into an article the closing stretch begins, as a fraction of its
# block elements. A real bleed sits in the tail; an article discussing another
# book mentions it anywhere.
_BLEED_TAIL_FRACTION = 0.4

# Leading marks that introduce a book review's bibliographic header. Both star
# characters occur in the corpus.
_REVIEW_BULLET_RE = re.compile(r'^\s*[\*★☆●•]\s*')


def _squash(text):
    """Lowercase, drop punctuation, collapse spaces — for tolerant comparison.

    Titles in toc.json and in the printed header disagree on punctuation often
    enough to matter: "Levinas: An Introduction" against "Levinas. An
    Introduction", "Living & Relating: ..." against "Living & Relating; ...".
    """
    # Apostrophes are dropped rather than spaced, so this agrees with _clean:
    # "Nietzsche's" has to squash to "nietzsches", not "nietzsche s", or a title
    # carrying a possessive never matches the printed header.
    text = text.lower().replace("'", '').replace('’', '')
    return ' '.join(re.sub(r'[^a-z0-9]', ' ', text).split())


def _squash_tight(text):
    """Reduce to letters and digits only — no spaces, no punctuation.

    Needed because the two sides disagree on what punctuation becomes. _clean
    deletes it ("R.D.Laing" -> "rdlaing", "Nietzsche's" -> "nietzsches") while a
    space-preserving squash would give "r d laing" and "nietzsche s". Dropping
    separators entirely makes the comparison agree either way, which is what
    lets a toc title match the header as it was actually printed.
    """
    return re.sub(r'[^a-z0-9]', '', text.lower())


def _starts_with_title(text, title):
    """True if `text` opens with `title`, ignoring punctuation and any bullet."""
    body = _squash_tight(_REVIEW_BULLET_RE.sub('', text))
    want = _squash_tight(title)
    # 10 characters of letters and digits is roughly three words — short enough
    # to admit "R.D.Laing: A Biography", long enough not to match on a stray
    # phrase.
    if len(want) < 10 or len(body) < 10:
        return False
    return body.startswith(want[:min(len(want), len(body))])


def _strip_end_bleed_soup(soup, next_title, own_title=''):
    """Remove content from the next article at the end."""
    if not next_title:
        return
    # Adjacent articles sharing a title (a book reviewed twice, or a two-part
    # piece) give no usable signal — the match would land on this article's own
    # opening. 18.2 "Zone of the Interior" is the case in point.
    if own_title and _squash(own_title) == _squash(next_title):
        return
    # Find last back-matter heading
    last_backmatter = None
    for heading in soup.find_all(['h2', 'h3']):
        if REFERENCE_HEADING_RE.match(_el_text(heading).strip()):
            last_backmatter = heading

    # Search for next title from last back-matter heading onwards.
    # Use flexible matching — heading text in the HTML often differs
    # from toc.json title (extra words, journal name inserted, etc.)
    rx = _text_to_regex(next_title, flexible=True)
    if rx is None:
        return

    if last_backmatter:
        # Search headings at or after the last backmatter heading, plus
        # non-heading blocks that follow a LATER heading (i.e. after the
        # references/notes section ends).  Don't match <p> tags that are
        # part of the reference list itself — a citation like "Sigal
        # (1976). Zone of the Interior. Pomona." would falsely trigger
        # end-bleed removal for a same-titled next article.
        search_els = []
        start_searching = False
        past_backmatter = False
        for el in soup.find_all(list(BLOCK_TAGS)):
            if el == last_backmatter:
                start_searching = True
                continue
            if not start_searching:
                continue
            if el.name in HEADING_TAGS:
                # A heading after the backmatter section signals we're
                # past the references — could be author byline or bleed
                if not REFERENCE_HEADING_RE.match(_el_text(el).strip()):
                    past_backmatter = True
                search_els.append(el)
            elif past_backmatter:
                # Only include non-heading elements after we've left
                # the reference section
                search_els.append(el)
    else:
        # No backmatter heading. Headings alone used to be the only thing
        # searched here, to avoid truncating an article that merely mentions
        # the next one. But short book reviews usually have no reference
        # section and the next review simply begins in a <p>, so nothing ever
        # matched and the bleed stayed: 53 articles across 19 volumes carried
        # the following review's opening (2026-08-10 survey).
        #
        # So paragraphs in the closing stretch are searched too, under a
        # stricter test — they must *start* with the next title, not merely
        # contain it. Measured over the whole corpus against the articles whose
        # committed output had been hand-trimmed, that finds 51 of 55 real
        # bleeds with one wrong match; matching anywhere in a paragraph instead
        # collapses precision to 4%, because "... by <Author>" is everywhere.
        blocks = soup.find_all(list(BLOCK_TAGS))
        tail_from = int(len(blocks) * _BLEED_TAIL_FRACTION)
        search_els = [el for el in blocks if el.name in HEADING_TAGS]
        search_els += [el for i, el in enumerate(blocks)
                       if i >= tail_from and el.name not in HEADING_TAGS]

    for el in search_els:
        if el.name in HEADING_TAGS or last_backmatter:
            matched = bool(rx.search(_el_clean_text(el)))
        else:
            matched = _starts_with_title(_el_clean_text(el), next_title)
        if matched:
            # Walk up to find the top-level container (e.g. a <div> wrapping
            # the next article's content) so we remove the whole block, not
            # just the heading inside it.
            target = el
            while target.parent and target.parent != soup:
                target = target.parent
            _remove_from(target)
            return


_RUNNING_HEADER_INLINE_RE = re.compile(
    r'^\s*\d{0,4}\s*'
    r'Existential\s+Analysis\s+\d+\.\d+\s*:\s*'
    r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*',
    re.IGNORECASE
)


def _strip_running_headers_soup(soup):
    """Remove running headers and bare page numbers from print layout."""
    for el in list(soup.find_all(['p', 'h1', 'h2', 'h3', 'h4'])):
        text = _el_text(el).strip()
        # Standalone running header: remove entire element
        if _RUNNING_HEADER_TEXT_RE.match(text):
            el.decompose()
            continue
        # Inline running header prefix: "Existential Analysis 13.1: January 2002 Article Title..."
        # Also handles page-number prefix: "82 Existential Analysis 28.1: ..."
        # Strip the header prefix from the element's leading text, keep the rest
        if _RUNNING_HEADER_INLINE_RE.match(text):
            # Find the first text node and strip the prefix from it
            if el.string:
                el.string = _RUNNING_HEADER_INLINE_RE.sub('', el.string).lstrip()
            elif el.contents:
                from bs4 import NavigableString
                first = el.contents[0]
                if isinstance(first, NavigableString) and _RUNNING_HEADER_INLINE_RE.match(str(first)):
                    first.replace_with(_RUNNING_HEADER_INLINE_RE.sub('', str(first)).lstrip())
        # Bare page number: standalone 1-4 digit number (only in <p>)
        if el.name == 'p' and re.match(r'^\d{1,4}$', _el_text(el).strip()):
            el.decompose()


# Contact details are what marks a paragraph as an author bio: an article's own
# prose never carries an email address and an ORCID.
_FUSED_BIO_CONTACT = re.compile(r'\bContact:|[\w.+-]+@[\w.-]+\.\w+|orcid\.org/')


def _fix_bio_contact_spacing_soup(soup):
    """Ensure a <br/> inside a bio doesn't concatenate the words around it.

    Haiku outputs bios with <br/> as the visual line break:
      ...freelance writer.<br/>Contact: user@example.com<br/>https://orcid.org/...
    Bios are stored as PLAIN TEXT (pipe3 writes `<bio><p>{escape(bio)}</p></bio>`),
    so every tag inside one is dropped — and a dropped <br/> with no substitute
    welds the two lines together. That is where "...freelance writer.Contact:
    charles@..." came from: seven articles in the corpus, and it would have
    recurred on every issue.

    An earlier version only handled email<br/>URL, which is why the far more
    common prose<br/>Contact: case survived. Any <br/> between two word
    characters now becomes a separator: ". " between an email and a URL,
    otherwise a plain space.

    Confined to paragraphs carrying contact details, because a <br/> in ordinary
    prose is a real line break — pipe3 emits it as <break/> and JATS renders it.
    Only bio text is flattened to a plain string, so only bio text loses it.
    """
    for br in list(soup.find_all('br')):
        para = br.find_parent('p')
        if para is None or not _FUSED_BIO_CONTACT.search(para.get_text()):
            continue
        prev_text = br.previous_sibling
        next_text = br.next_sibling
        if not (isinstance(prev_text, NavigableString)
                and isinstance(next_text, NavigableString)):
            continue
        before, after = str(prev_text), str(next_text)
        # \S, not \w: the line before the break usually ENDS a sentence, so the
        # character to its left is a full stop ("...freelance writer.<br/>").
        # Requiring a word character there was why this case slipped through.
        if not (re.search(r'\S\s*$', before) and re.match(r'\s*\S', after)):
            continue
        if re.search(r'[\w.+-]+@[\w.-]+\.\w+\s*$', before) and re.match(r'\s*https?://', after):
            # An email and a URL are two units on one contact line, not a
            # sentence boundary — ". " is what reads correctly between them.
            br.replace_with('. ')
        else:
            # Don't double up where the markup already carries a space.
            spaced = before.endswith((' ', '\n')) or after.startswith((' ', '\n'))
            br.replace_with('' if spaced else ' ')


# The no-tag variant of the email<br/>URL case above: pipe1d records a line
# break only when the typesetter left a trailing space, and a contact line set
# flush ("Contact: user@example.com" / "https://orcid.org/...") has none — so
# the two lines weld directly with no <br/> for _fix_bio_contact_spacing_soup
# to replace. Bios have carried ORCID lines since 32.1 (52 across the corpus,
# private/backfill/orcid-inventory.txt); 37.2/03 was merely the first set
# flush against the margin, which is what exposed this.
_WELDED_EMAIL_URL = re.compile(r'([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})(https?://)')


def _fix_welded_email_url_soup(soup):
    """Separate an email welded directly to a following URL in a contact line.

    Same separator as the <br/> rule: an email and a URL are two units on one
    contact line, not a sentence boundary — ". " is what reads correctly.
    Confined to paragraphs carrying contact details, like the rule above.
    """
    for para in soup.find_all('p'):
        if not _FUSED_BIO_CONTACT.search(para.get_text()):
            continue
        for text in para.find_all(string=True):
            fixed = _WELDED_EMAIL_URL.sub(r'\1. \2', str(text))
            if fixed != str(text):
                text.replace_with(fixed)


# An iD that has been captured as structured metadata (the toc `orcids` map ->
# JATS contrib-id -> OJS author record and Harbour journal_authors) is rendered
# at the top of the article as a linked, badged iD. Left in the bio as well it
# reads twice, and the bio copy is the worse one: bare text, not a link.
# Matches "https://orcid.org/<id>", a bare "<id>", and the "ORCID:" label form
# some issues use (34.2 prints the label BEFORE the contact line, later issues
# print the URL after it), plus any separator stranded by the removal.
def _orcid_text_pattern(oid):
    return re.compile(
        r'\s*(?:ORCID(?:\s+iD)?\s*:?\s*)?'
        r'(?:https?://(?:www\.)?orcid\.org/)?' + re.escape(oid) + r'\.?',
        re.I)


def _strip_recorded_orcids_soup(soup, article):
    """Remove ORCIDs from bio text when they are held as metadata instead.

    Only iDs listed in the article's `orcids` map are removed — an iD the map
    does not carry is the only copy there is, so it stays. The contact email
    stays either way: nothing else publishes it.
    """
    orcids = (article or {}).get('orcids') or {}
    ids = [url.rstrip('/').rsplit('/', 1)[-1] for url in orcids.values()]
    if not ids:
        return
    patterns = [_orcid_text_pattern(oid) for oid in ids]
    for text in list(soup.find_all(string=True)):
        s = original = str(text)
        for pat in patterns:
            s = pat.sub('', s)
        if s == original:
            continue
        # Tidy what the removal stranded: a dangling separator before a close,
        # and doubled spaces mid-line. Keep leading/trailing single spaces so
        # adjacent inline tags don't weld (see _fix_bio_contact_spacing_soup).
        s = re.sub(r'[ \t]{2,}', ' ', s)
        s = re.sub(r'\s+([.,;])', r'\1', s)
        text.replace_with(s)


def _split_fused_author_bios_soup(soup):
    """Give an author bio its own <p> when extraction welded it to the body.

    pipe4 promotes a trailing paragraph to <bio> only when the WHOLE paragraph
    reads as a bio. For eleven articles Haiku never broke the line, so the bio
    sat inside the article's closing paragraph — "...as we traverse our dark
    nights of the soul.<strong>Carla Willig</strong> is Professor Emerita at
    City..." — and the classifier, correctly, saw one body paragraph.

    Splitting it here (pipe2, before JATS exists) means pipe4's existing
    trailing-bio scan does the rest, and the repair survives a rerun. The
    alternative — hand-editing the JATS — is silently lost the next time pipe3
    regenerates it from the post-processed HTML.

    Conservative on purpose: only splits at a <strong> that is followed by
    contact details and is not already at the start of its paragraph.
    """
    # A worklist, not a snapshot: a two-author run splits into body + bio, and
    # that new bio paragraph still holds the second author, so it goes back in
    # the queue. 37.2/02 (Willig and Vincent) is the case that needs this.
    queue = list(soup.find_all('p'))
    while queue:
        para = queue.pop(0)
        for strong in para.find_all('strong', recursive=False):
            # Already starts the paragraph — pipe4's own scan handles it.
            if strong.previous_sibling is None:
                continue
            tail = ''.join(str(s) for s in strong.next_siblings)
            if not _FUSED_BIO_CONTACT.search(BeautifulSoup(tail, 'html.parser').get_text()):
                continue
            bio = soup.new_tag('p')
            for node in [strong] + list(strong.next_siblings):
                bio.append(node.extract())
            # These bios have no <br/> before "Contact:" — the line break was
            # never in the extraction at all, so the words are welded directly
            # ("...United States.Contact: michael@..."). Nothing downstream can
            # tell that apart from a word, so separate it here.
            for text in bio.find_all(string=True):
                spaced = re.sub(r'([\w,.\)])(Contact:)', r'\1 \2', str(text))
                if spaced != str(text):
                    text.replace_with(spaced)
            # The body paragraph now ends on the space that used to sit before
            # the author's name.
            if para.contents and isinstance(para.contents[-1], NavigableString):
                para.contents[-1].replace_with(str(para.contents[-1]).rstrip())
            para.insert_after(bio)
            queue.append(bio)
            break


def _strip_heading_sups_soup(soup):
    """Strip footnote superscripts from headings."""
    for heading in soup.find_all(list(HEADING_TAGS)):
        for sup in heading.find_all('sup'):
            sup.decompose()


def _normalise_headings_soup(soup):
    """Normalise ALL CAPS headings to title case."""
    for heading in soup.find_all(list(HEADING_TAGS)):
        # Only process headings with simple text content (no nested tags
        # that would be lost by replacing .string)
        if heading.string is not None:
            heading.string = normalise_allcaps(heading.string)
        else:
            # Mixed content (e.g. <h2><em>TEXT</em></h2>) — process each
            # text node individually
            for text_node in heading.find_all(string=True):
                if text_node.strip():
                    text_node.replace_with(normalise_allcaps(str(text_node)))


_NOTES_HEADING_RE = re.compile(r'^(Notes?|Endnotes?|Footnotes?)\s*$', re.IGNORECASE)


def _splice_notes_soup(soup, back_matter_sections):
    """Replace notes <ol> with PyMuPDF-extracted notes if Haiku dropped any.

    Compares <li> count in the existing notes section against the expected
    count from PyMuPDF PDF extraction. If the HTML has fewer notes, replaces
    the entire <ol> (and ensures an <h2> heading exists).
    """
    notes_sections = [s for s in back_matter_sections
                      if _NOTES_HEADING_RE.match(s['heading'])
                      and s['is_numbered']]
    if not notes_sections:
        return

    expected_notes = notes_sections[0]['items']
    expected_count = len(expected_notes)
    heading_text = notes_sections[0]['heading']

    # Find the Notes heading in the soup
    notes_h2 = None
    refs_h2 = None
    for h2 in soup.find_all('h2'):
        text = _el_text(h2).strip()
        if not notes_h2 and _NOTES_HEADING_RE.match(text):
            notes_h2 = h2
        if not refs_h2 and REFERENCE_HEADING_RE.match(text):
            refs_h2 = h2

    # Find the <ol> that belongs to the notes section
    notes_ol = None
    if notes_h2:
        # Look for the first <ol> after the notes heading
        for sib in notes_h2.next_siblings:
            if isinstance(sib, Tag):
                if sib.name == 'ol':
                    notes_ol = sib
                    break
                # Stop at next heading or references
                if sib.name in HEADING_TAGS:
                    break
    else:
        # No heading — look for a bare <ol> before References
        if refs_h2:
            for el in refs_h2.previous_siblings:
                if isinstance(el, Tag) and el.name == 'ol':
                    notes_ol = el
                    break
                if isinstance(el, Tag) and el.name in HEADING_TAGS:
                    break

    # Count existing notes — check both <ol>/<li> and <sup>-numbered paragraphs
    actual_count = len(notes_ol.find_all('li')) if notes_ol else 0

    if actual_count == 0 and notes_h2:
        # Haiku sometimes renders notes as <p><sup>1</sup>...</p> instead of <ol>
        sup_re = re.compile(r'^\d+$')
        for sib in notes_h2.next_siblings:
            if isinstance(sib, Tag):
                if sib.name in HEADING_TAGS:
                    break
                sup = sib.find('sup')
                if sup and sup_re.match(sup.get_text(strip=True)):
                    actual_count += 1

    if actual_count >= expected_count:
        return

    # Build replacement <ol> with PyMuPDF notes (may contain <em> tags)
    new_ol = soup.new_tag('ol')
    for note in expected_notes:
        li = soup.new_tag('li')
        # Parse note HTML (may contain <em> tags) into the <li>
        note_soup = BeautifulSoup(note, 'html.parser')
        for child in list(note_soup.children):
            li.append(child)
        new_ol.append(li)

    if notes_ol:
        notes_ol.replace_with(new_ol)
    elif notes_h2:
        notes_h2.insert_after(new_ol)
    else:
        # No heading exists — create one and insert before refs or at end
        new_h2 = soup.new_tag('h2')
        new_h2.string = heading_text
        if refs_h2:
            refs_h2.insert_before(new_h2)
            new_h2.insert_after(new_ol)
        else:
            soup.append(new_h2)
            soup.append(new_ol)


def _strip_book_reviews_heading_soup(soup):
    """Remove 'BOOK REVIEWS' heading."""
    for heading in soup.find_all(['h1', 'h2', 'h3']):
        if re.match(r'^\s*BOOK\s+REVIEWS?\s*$', _el_text(heading), re.IGNORECASE):
            heading.extract()
            return


def _strip_book_listing_soup(soup):
    """Strip book title headings and publication detail paragraphs from start.

    For multi-book reviews, the body begins with <h1>Title</h1><p>Author,
    year. Publisher.</p> pairs for each book.  Since the titles are already
    in the OJS article title, strip them from the body.

    Publication detail lines are short (author, year, publisher) — typically
    under 150 chars.  Body paragraphs are much longer and must not be stripped.
    """
    max_pub_detail_len = 150
    to_remove = []
    for el in soup.children:
        if not isinstance(el, Tag):
            # skip NavigableString (whitespace)
            if isinstance(el, str) and el.strip() == '':
                to_remove.append(el)
                continue
            break
        if el.name in HEADING_TAGS:
            to_remove.append(el)
        elif (el.name == 'p'
              and len(_el_text(el)) <= max_pub_detail_len
              and _PUB_MARKERS_RE.search(_el_text(el))):
            to_remove.append(el)
        else:
            break
    for el in to_remove:
        el.extract()


def _strip_contents_section_soup(soup):
    """Remove a CONTENTS/TOC section that lists all articles in an issue.

    Some raw editorial HTML begins with a full table of contents before the
    actual article body. The TOC ends at an <hr> tag or the first real
    section heading (EDITORIAL, BOOK REVIEWS, etc.).
    """
    contents_heading = None
    for heading in soup.find_all(['h1', 'h2']):
        if re.match(r'^\s*CONTENTS\s*$', _el_text(heading).strip(), re.IGNORECASE):
            contents_heading = heading
            break
    if contents_heading is None:
        return

    _SECTION_HEADINGS_RE = re.compile(
        r'^\s*(EDITORIAL|BOOK\s+REVIEWS?)\s*$', re.IGNORECASE)

    to_remove = [contents_heading]
    for sibling in list(contents_heading.next_siblings):
        if not hasattr(sibling, 'name'):
            to_remove.append(sibling)
            continue
        if sibling.name == 'hr':
            to_remove.append(sibling)
            break
        if sibling.name in ('h1', 'h2') and \
                _SECTION_HEADINGS_RE.match(_el_text(sibling).strip()):
            break
        to_remove.append(sibling)

    for el in to_remove:
        el.extract()


def _strip_book_review_editorial_tail_soup(soup):
    """Remove individual book reviews after the editorial intro.

    For Book Review Editorial articles, the raw HTML contains the editorial
    intro paragraphs followed by individual review headings and content.
    Strip from the first <h2> that isn't a section-level heading.
    """
    _SECTION_RE = re.compile(
        r'^\s*(BOOK\s+REVIEWS?|EDITORIAL)\s*$', re.IGNORECASE)
    for heading in soup.find_all(['h2', 'h3']):
        text = _el_text(heading).strip()
        if _SECTION_RE.match(text):
            continue
        # This is the first individual review heading — remove from here
        _remove_from(heading)
        return


# ---------------------------------------------------------------
# Public string-based API (backward compatible)
# ---------------------------------------------------------------

def strip_start_bleed(html, own_title):
    """Remove content from the previous article at the start of the HTML.

    Strategy: find this article's own title. Everything before it is
    bleed from the previous article. This is more reliable than searching
    for the previous article's title, because the bleed is the TAIL of
    the previous article (body text, refs) — not its title.
    """
    if not own_title:
        return html
    soup = _parse(html)
    _strip_start_bleed_soup(soup, own_title)
    return _serialize(soup)


def strip_title(html, title):
    """Remove the article's own title from the HTML.

    Handles multi-element titles (h1/h2 + subtitle paragraphs). Only
    consumes blocks from the top that are short enough to be title/subtitle
    elements (headings or short paragraphs). Stops at the first long
    body paragraph regardless of word overlap.
    """
    if not title:
        return html
    soup = _parse(html)
    _strip_title_soup(soup, title)
    return _serialize(soup)


def strip_subtitle(html, subtitle):
    """Remove the article's subtitle from the HTML body."""
    if not subtitle:
        return html
    soup = _parse(html)
    _strip_subtitle_soup(soup, subtitle)
    return _serialize(soup)


def strip_authors(html, authors):
    """Remove author byline from the HTML.

    Authors appear near the top, after the title. Search up to the
    Abstract heading or first body heading.
    """
    if not authors:
        return html
    soup = _parse(html)
    _strip_authors_soup(soup, authors)
    return _serialize(soup)


def strip_abstract(html, abstract):
    """Remove abstract heading and paragraph(s) from the HTML.

    Uses fuzzy word-overlap matching (not ordered-word regex) because
    Haiku OCR may introduce errors in the abstract text (e.g. "clinicians"
    → "citizenicians"). 80% word overlap is tolerant of OCR errors.
    """
    if not abstract or len(abstract) < MIN_ABSTRACT_LENGTH:
        return html
    soup = _parse(html)
    _strip_abstract_soup(soup, abstract)
    return _serialize(soup)


def strip_keywords(html):
    """Remove the Keywords / Key Words line from the HTML.

    Handles both:
    - <p>Keywords: term1, term2, ...</p>
    - <h2>Key Words</h2>\\n<p>term1, term2, ...</p>
    """
    soup = _parse(html)
    _strip_keywords_soup(soup)
    return _serialize(soup)


def strip_end_bleed(html, next_title):
    """Remove content from the next article at the end of the HTML."""
    if not next_title:
        return html
    soup = _parse(html)
    _strip_end_bleed_soup(soup, next_title)
    return _serialize(soup)


# ---------------------------------------------------------------
# Book review post-processing
# ---------------------------------------------------------------

def _find_book_publication_details(html, book_title, search_start=0,
                                   require_heading=False,
                                   allow_para_start=True):
    """Find where a book review's publication details start.

    Looks for the book title in a block that also contains publication
    markers (publisher, year, page count, price). This distinguishes
    the actual review from an editorial intro that merely mentions the title.

    If require_heading is True, only matches where the title appears in a
    heading element (or a heading's next sibling has pub markers).  This
    prevents matching inline citations in reference lists that happen to
    contain the same title with a year/publisher.

    Returns (start_pos, end_pos) or (None, None).
    """
    clean_book = re.sub(r'^Book Review:\s*', '', book_title, flags=re.IGNORECASE)
    book_parts = [p.strip() for p in clean_book.split('/') if p.strip()]
    rx_parts = [_text_to_regex(p) for p in book_parts if p]
    rx_parts = [r for r in rx_parts if r is not None]
    if not rx_parts:
        return None, None

    region = html[search_start:]
    soup = _parse(region)

    # Find the earliest block matching ANY part with publication details
    for el in soup.find_all(list(BLOCK_TAGS - {'blockquote'})):
        block_text = _el_clean_text(el)
        raw_block = _el_text(el)
        has_title = any(rx.search(block_text) for rx in rx_parts)
        if not has_title:
            continue
        is_heading = el.name in HEADING_TAGS
        # When require_heading is set, only match headings (or paragraphs
        # whose preceding heading contains the title).  This prevents
        # reference citations like "Sigal (2005). Zone of the Interior.
        # Pomona." from being mistaken for the next review's header.
        if require_heading and not is_heading:
            # A paragraph that *opens* with the title is the next review's
            # header; one that merely contains it is a citation, which is what
            # this guard was protecting against ("Sigal (2005). Zone of the
            # Interior. Pomona." begins with the author, not the title).
            #
            # Requiring a heading outright was too strict: most reviews open in
            # a <p>, so no cut point was ever found and the review kept the
            # following one's opening. 51 book reviews across 19 volumes were
            # carrying it (2026-08-10 survey).
            if not (allow_para_start
                    and any(_starts_with_title(block_text, part) for part in book_parts)):
                # Check if the preceding heading contains the title
                prev_heading = el.find_previous_sibling(list(HEADING_TAGS))
                if not prev_heading or not any(
                        rx.search(_el_clean_text(prev_heading)) for rx in rx_parts):
                    continue
        has_pub = bool(_PUB_MARKERS_RE.search(raw_block))
        is_short_heading = len(block_text.split()) <= 10
        # For headings, also check the next sibling for pub markers
        # (book title in <h1>, pub details in the following <p>)
        if not has_pub and is_heading:
            next_sib = el.find_next_sibling(list(BLOCK_TAGS))
            if next_sib:
                has_pub = bool(_PUB_MARKERS_RE.search(_el_text(next_sib)))
        if has_pub or is_short_heading:
            el_str = str(el)
            pos = region.find(el_str)
            if pos >= 0:
                return (search_start + pos, search_start + pos + len(el_str))

    # Fallback: find earliest block matching any part without pub markers
    if not require_heading:
        for el in soup.find_all(list(BLOCK_TAGS - {'blockquote'})):
            block_text = _el_clean_text(el)
            if any(rx.search(block_text) for rx in rx_parts):
                el_str = str(el)
                pos = region.find(el_str)
                if pos >= 0:
                    return (search_start + pos, search_start + pos + len(el_str))

    return None, None


def _end_of_pub_details(html, start, pub_end):
    """Find the end of a book review's publication details block.

    Starting from the heading at *start*, walks past consecutive sibling
    paragraphs that contain publication markers (year, pp., publisher, price).
    Returns a position safely past all of them so that a subsequent search
    for the next review title won't hit inline body mentions.
    """
    if pub_end is None:
        return start + 1
    soup = _parse(html[start:])
    heading = soup.find(list(HEADING_TAGS))
    skip = pub_end
    if heading:
        sib = heading.find_next_sibling(list(BLOCK_TAGS))
        while sib and _PUB_MARKERS_RE.search(_el_text(sib)):
            sib_str = str(sib)
            pos = html.find(sib_str, start)
            if pos >= 0:
                skip = max(skip, pos + len(sib_str))
            sib = sib.find_next_sibling(list(BLOCK_TAGS))
    return skip


def extract_book_review(html, book_title, next_book_title=None,
                        is_combined_review=False):
    """Extract a single book review from full-page HTML extraction.

    Handles:
    - Editorial intros that mention book titles before the actual reviews
    - Multi-book reviews ("Title A / Title B" in one piece)
    - Combined reviews (consecutive entries sharing same pages)
    - Shared pages with adjacent reviews

    For combined reviews (is_combined_review=True), the "next" title is
    the first non-combined review, not the next entry in the same group.
    """
    if not book_title:
        return html

    start, pub_end = _find_book_publication_details(html, book_title)
    if start is None:
        return html

    review_end = len(html)
    if next_book_title and not is_combined_review:
        # Skip past the current review's publication details block so that
        # inline mentions of the same title in body text don't match as
        # the "next review" start (e.g. multiple reviews of the same book).
        next_search = _end_of_pub_details(html, start, pub_end)
        # When the next entry carries the same title (a book reviewed more than
        # once — 18.2 has three of "Zone of the Interior"), a paragraph opening
        # with that title is far more likely to be this review's own prose
        # ("Zone of the Interior is as much about Sigal's own life...") than the
        # next review's header. Fall back to headings only, which is what the
        # original guard did and what the same-title fixture expects.
        same_title = _squash(_strip_toc_prefixes(next_book_title)) == _squash(
            _strip_toc_prefixes(book_title))
        next_start, _ = _find_book_publication_details(
            html, next_book_title, search_start=next_search,
            require_heading=True, allow_para_start=not same_title)
        if next_start is not None:
            review_end = next_start

    return html[start:review_end].strip()


def _author_name_variants(authors):
    """Generate matching variants for an author name string.

    Handles cases where HTML drops middle names/initials that appear in
    toc.json. E.g. "Luis M. Rodriguez" → also try "Luis Rodriguez",
    "Edgar Agrela Correia" → also try "Edgar Correia".

    Returns a list of name strings to try, from most specific to least.
    """
    variants = [authors]
    parts = re.split(r',\s*|\s+and\s+', authors)
    short_parts = []
    for part in parts:
        words = part.strip().split()
        if len(words) > 2:
            short_parts.append(f'{words[0]} {words[-1]}')
        else:
            short_parts.append(part.strip())
    short_variant = ', '.join(short_parts) if ',' in authors else ' and '.join(short_parts) if ' and ' in authors else short_parts[0]
    if short_variant != authors:
        variants.append(short_variant)
    return variants


# ---------------------------------------------------------------
# Editorial post-processing
# ---------------------------------------------------------------

def postprocess_editorial(html, article):
    """Post-process an editorial: strip title, keep body."""
    soup = _parse(html)
    _strip_start_bleed_soup(soup, article.get('title', ''))
    _strip_title_soup(soup, article.get('title', ''))
    _strip_authors_soup(soup, article.get('authors', ''))
    _strip_end_bleed_soup(soup, article.get('_next_title', ''), article.get('title', ''))
    return _serialize(soup)


def postprocess_book_review_editorial(html, article):
    """Post-process a book review editorial (section intro).

    Strip the "BOOK REVIEWS" heading, keep the editorial body.
    """
    soup = _parse(html)
    _strip_contents_section_soup(soup)
    _strip_start_bleed_soup(soup, article.get('title', ''))
    _strip_book_reviews_heading_soup(soup)
    _strip_book_review_editorial_tail_soup(soup)
    return _serialize(soup)


# ---------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------

def postprocess_article(html, article, pdf_path=None):
    """Run the full post-processing pipeline on raw HTML.

    Routes to the appropriate pipeline based on article section type:
    - Articles: strip title, authors, abstract, keywords, conference note, bleed
    - Book Reviews: extract target review from shared pages
    - Editorial: strip title, keep body
    - Book Review Editorial: strip heading, keep editorial body

    Args:
        html: raw HTML from Haiku (full extraction)
        article: toc.json article dict
        pdf_path: path to split PDF (for ref verification)
    """
    # Skip post-processing for content-filtered articles (PyMuPDF fallback).
    # These have no HTML structure — the pipeline would mangle them.
    if '<!-- AUTO-EXTRACTED:' in html[:100]:
        return html

    section = article.get('section', '')

    if section in ('Book Reviews', 'Book Review'):
        is_combined = (
            article.get('pdf_page_start') is not None
            and article.get('pdf_page_start') == article.get('_next_page_start')
            and article.get('pdf_page_end') == article.get('_next_page_end')
        )
        html = extract_book_review(
            html,
            book_title=article.get('title', ''),
            next_book_title=article.get('_next_title', ''),
            is_combined_review=is_combined,
        )

    # Parse once for all remaining operations
    soup = _parse(html)

    # Strip CONTENTS/TOC sections before any other processing
    _strip_contents_section_soup(soup)

    # Strip book title headings and publication detail lines from
    # book review bodies — these are metadata already in toc.json/OJS title.
    if section in ('Book Reviews', 'Book Review'):
        _strip_book_listing_soup(soup)

    if section == 'Book Review Editorial':
        _strip_start_bleed_soup(soup, article.get('title', ''))
        _strip_book_reviews_heading_soup(soup)
        _strip_book_review_editorial_tail_soup(soup)
    elif section == 'Editorial':
        _strip_start_bleed_soup(soup, article.get('title', ''))
        _strip_title_soup(soup, article.get('title', ''))
        _strip_authors_soup(soup, article.get('authors', ''))
    elif section not in ('Book Reviews', 'Book Review'):
        # Standard article
        _strip_start_bleed_soup(soup, article.get('title', ''))
        _strip_title_soup(soup, article.get('title', ''))
        _strip_subtitle_soup(soup, article.get('subtitle', ''))
        _strip_authors_soup(soup, article.get('authors', ''))
        # Conference/presentation notes are preserved in the body — they flow
        # into JATS and are extracted as provenance by extract_citations.py.
        _strip_abstract_soup(soup, article.get('abstract', ''))
        _strip_keywords_soup(soup)
        # Second pass: Haiku sometimes renders the title twice (h1 + h2).
        _strip_title_soup(soup, article.get('title', ''))
        _strip_subtitle_soup(soup, article.get('subtitle', ''))

    # End-bleed stripping applies to ALL section types
    _strip_end_bleed_soup(soup, article.get('_next_title', ''), article.get('title', ''))

    # Strip footnote superscripts from headings
    _strip_heading_sups_soup(soup)

    # Normalise ALL CAPS headings to title case
    _normalise_headings_soup(soup)

    # Strip running headers and bare page numbers
    _strip_running_headers_soup(soup)

    # Give a bio welded to the body its own <p>, so pipe4 can classify it
    _split_fused_author_bios_soup(soup)

    # Fix contact detail spacing (a <br/> inside a bio must not weld two words)
    _fix_bio_contact_spacing_soup(soup)

    # Fix an email welded straight to a URL where no <br/> was extracted at all
    _fix_welded_email_url_soup(soup)

    # Drop bio-text ORCIDs that are now carried as structured metadata. Runs
    # AFTER the weld repairs so the iD is a separable token by this point.
    _strip_recorded_orcids_soup(soup, article)

    # Splice complete notes from PyMuPDF if Haiku dropped any.
    if pdf_path and os.path.exists(pdf_path):
        from lib.pdf_utils import extract_pdf_back_matter
        back_matter = extract_pdf_back_matter(
            pdf_path, title=article.get('title'),
            authors=article.get('authors'))
        if back_matter:
            _splice_notes_soup(soup, back_matter)

    html = _serialize(soup)
    html = re.sub(r'\n{3,}', '\n\n', html).strip()

    # Strip control characters that break XML parsing downstream.
    # These occasionally appear in Haiku-extracted text from scanned PDFs.
    html = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', html)

    return html


# ---------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------

_SECTION_HEADINGS = frozenset({
    'letters to the editors', 'letters to the editor', 'letter to the editors',
    'letter to the editor', 'letters', 'editorial', 'book reviews',
    'book review editorial', 'obituary',
})


def _strip_toc_prefixes(title):
    """Strip toc.json prefixes that don't appear in the PDF/HTML.

    Same logic as split.py title_in_split_pdf — kept in sync.
    """
    title = re.sub(
        r'^(Book Reviews?|Film Review|Exhibition Report|Poem'
        r'|Personally Speaking|Obituary|Essay Review'
        r'|Letter to the Editors?|Responses?( to)?'
        r'|Professor|Prof\.?)\s*:?\s*',
        '', title, flags=re.IGNORECASE
    ).strip()
    title = re.sub(
        r'^(Professor|Prof\.?)\s*:?\s*',
        '', title, flags=re.IGNORECASE
    ).strip()
    title = re.sub(r'\s*\([^)]+\)\s*$', '', title)
    return title


def _title_in_text_fuzzy(title, text):
    """Check if title appears in text using fuzzy matching.

    Uses substring match or 80% word overlap — different from the ordered-
    sequence _title_in_text used elsewhere, because verification needs to
    tolerate PDF extraction artefacts (fused words, reordered fragments).
    """
    clean_title = _clean(title)
    clean_text = _clean(text)
    if not clean_title:
        return True
    if clean_title in clean_text:
        return True
    title_words = [w for w in clean_title.split() if len(w) > 2]
    if not title_words:
        return True
    found = sum(1 for w in title_words if w in clean_text)
    return found / len(title_words) >= TITLE_FUZZY_MATCH_THRESHOLD


def verify_postprocessed(raw_html, final_html, article):
    """Verify post-processing produced correct output.

    Returns list of warning strings (empty = all good).
    """
    warnings = []
    title = article.get('title', '')
    stripped_title = _strip_toc_prefixes(title)
    section = article.get('section', '')
    is_book_review = section in ('Book Reviews', 'Book Review')
    raw_text = _strip_tags(raw_html)
    final_text = _strip_tags(final_html)

    if stripped_title and not _title_in_text_fuzzy(stripped_title, raw_text):
        if not any(h in raw_text.lower() for h in _SECTION_HEADINGS):
            warnings.append(f'TITLE_NOT_IN_RAW: "{stripped_title[:50]}" not found in raw HTML')

    if is_book_review and stripped_title:
        parts = [p.strip() for p in stripped_title.split('/') if p.strip()]
        if not any(_title_in_text_fuzzy(p, final_text) for p in parts):
            warnings.append(f'BOOK_TITLE_MISSING: "{stripped_title[:50]}" not in final HTML')

    if len(final_text.strip()) < SHORT_CONTENT_THRESHOLD:
        warnings.append(f'EMPTY_OUTPUT: final HTML has only {len(final_text.strip())} chars')

    return warnings


def pdf_has_formal_refs(pdf_path):
    """Check if PDF has a standalone back-matter heading."""
    if fitz is None:
        return False
    doc = fitz.open(pdf_path)
    pdf_text = ''.join(p.get_text() for p in doc)
    doc.close()
    for line in pdf_text.split('\n'):
        if REFERENCE_HEADING_RE.match(line.strip()):
            return True
    return False


def html_has_refs(html):
    """Check if HTML contains a back-matter section."""
    soup = _parse(html)
    for heading in soup.find_all(['h2', 'h3']):
        if REFERENCE_HEADING_RE.match(_el_text(heading).strip()):
            return True
    for p in soup.find_all('p'):
        strong = p.find('strong')
        if strong and REFERENCE_HEADING_RE.match(_el_text(strong).strip()):
            return True
    return False


def check_missing_refs(html, pdf_path):
    """Return True if PDF has formal References heading but HTML is missing them."""
    return pdf_has_formal_refs(pdf_path) and not html_has_refs(html)
