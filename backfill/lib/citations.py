"""Shared citation classification and reference section detection.

Used by extract_citations.py (extraction from JATS body),
split_citation_tiers.py (classification of refs vs notes),
and other backfill scripts that need text normalisation, HTML
stripping, provenance detection, or XML namespace helpers.

All classification logic lives here — no duplication across scripts.
"""

import re
from html.parser import HTMLParser
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------
# Heading patterns for reference/citation sections
# ---------------------------------------------------------------

# All headings that indicate a reference/citation section (plain text matching)
REFERENCE_HEADING_RE = re.compile(
    r'^('
    r'References?'
    r'|Notes?'
    r'|Endnotes?'
    r'|Footnotes?:?'
    r'|Bibliography'
    r'|Further Reading'
    r'|Works Cited'
    r'|Notes and References'
    r'|References and Notes'
    r'|Bibliography and References'
    r'|Further References'
    r'|References and Bibliography'
    r'|References and further reading'
    r'|Selected Bibliography'
    r'|References:'
    r'|Author [Ss]tatement'
    r')(?:\s*[&;]\s*\w+)*'  # optional "& Filmography"
    r'(?:\s*\([^)]*\))?'  # optional parenthetical
    r'[.:]?$',
    re.IGNORECASE
)

# "Pure reference" headings (always extract all items)
PURE_REFERENCE_HEADING_RE = re.compile(
    r'^(References?|Bibliography|Works Cited|Further Reading'
    r'|Further References'
    r'|References and Bibliography|References and further reading'
    r'|Selected Bibliography|References:|References and Notes'
    r'|Bibliography and References'
    r'|Notes and References)(?:\s*[&;]\s*\w+)*(?:\s*\([^)]*\))?[.:]?$',
    re.IGNORECASE
)

# "Notes" headings (only extract citation-like items from these)
NOTES_HEADING_RE = re.compile(
    r'^(Notes?|Endnotes?|Footnotes?:?|Author [Ss]tatement)[.:]?$',
    re.IGNORECASE
)


# Common academic/book publishers. Used across classification functions
# to detect bibliographic references. Single source of truth — add new
# publishers here rather than in individual regexes.
PUBLISHER_NAMES = (
    'Press|Publisher|Books|University|Routledge|Sage|Springer|Wiley|Penguin|'
    'Palgrave|Harper|Random House|Vintage|Tavistock|Macmillan|Methuen|'
    'OUP|Blackwell|Faber|Karnac|Norton|Continuum|Duckworth'
)

# ---------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------

# Common abbreviations in references that end with a period but don't
# mark a sentence boundary. Used by _count_sentences().
_ABBREV_RE = re.compile(
    r'(?:'
    r'[A-Z]\.'           # Single-letter initials: J. K. R.
    r'|(?:Dr|Mr|Mrs|Ms|Prof|Rev|Vol|vol|No|no|ed|eds|trans|repr'
    r'|Dept|Inc|Ltd|Corp|Assoc|Univ|pp|ca|cf|etc|vs|approx)\.'
    r')\s+[A-Z]'
)


def _count_sentences(text):
    """Count approximate sentence boundaries, excluding common abbreviations.

    Counts [.!?] followed by space + capital letter, minus matches that
    are abbreviations (initials, Dr., Vol., pp., etc.).
    """
    raw = len(re.findall(r'[.!?]\s+[A-Z]', text))
    abbrevs = len(_ABBREV_RE.findall(text))
    return max(0, raw - abbrevs)

class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags, return plain text."""
    def __init__(self):
        super().__init__()
        self._text = []

    def handle_data(self, data):
        self._text.append(data)

    def get_text(self):
        return "".join(self._text).strip()


def strip_html(html_str: str) -> str:
    """Strip HTML tags from a string, returning plain text."""
    extractor = _HTMLTextExtractor()
    extractor.feed(html_str)
    return extractor.get_text()


def extract_text_from_element(el: ET.Element) -> str:
    """Extract plain text from an XML element (recursive).

    Also usable as a replacement for _text_content in jats_to_html.py —
    both functions do the same recursive text extraction.
    """
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(extract_text_from_element(child))
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts).strip()


# ---------------------------------------------------------------
# Text normalisation (shared across scripts)
# ---------------------------------------------------------------

def normalise_for_match(text: str) -> str:
    """Lowercase, strip non-alpha, collapse whitespace.

    General-purpose normalisation for fuzzy text matching. Strips HTML
    tags, punctuation, and digits — suitable for title/name comparison.
    """
    text = re.sub(r'<[^>]+>', '', text)  # strip HTML tags
    text = re.sub(r'[^a-zA-Z\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()


def normalise_for_overlap(text: str) -> str:
    """Lowercase, strip non-alphanumeric (keep digits), collapse whitespace.

    Like normalise_for_match but preserves digits — suitable for content
    overlap checks where years/numbers matter (e.g. abstract matching).
    """
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', text.lower())).strip()


# ---------------------------------------------------------------
# XML namespace helpers (shared across JATS scripts)
# ---------------------------------------------------------------

def local_name(tag: str) -> str:
    """Strip namespace from an XML element tag.

    '{http://...}name' → 'name', 'name' → 'name'.
    """
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


# ---------------------------------------------------------------
# Note sorting (shared by extract_citations.py and generate_jats.py)
# ---------------------------------------------------------------

def sort_notes_by_number(notes: list[str]) -> list[str]:
    """Sort notes by their leading number (e.g. '3 Text...' before '4 Text...').

    Notes without a leading number are placed at the end in original order.
    """
    def sort_key(note):
        m = re.match(r'^(\d+)[\.\)\s]', note)
        return (0, int(m.group(1))) if m else (1, 0)
    return sorted(notes, key=sort_key)


# ---------------------------------------------------------------
# JATS section detection (replaces HTML h2-based detection)
# ---------------------------------------------------------------

def _normalise_name(name: str) -> str:
    """Normalise a name for fuzzy matching: lowercase, strip punctuation, collapse spaces."""
    return normalise_for_match(name)


def _is_bio_section(el: ET.Element, ns: str, author_names: list[str] = None) -> bool:
    """Detect sections that are author bios by structure.

    Matches when the section title is a known article author (from JATS
    <front> metadata) and the first paragraph starts with a bio verb
    ("is a", "is an", "was a", "has been", etc.).

    If author_names is not provided, falls back to checking whether the
    heading looks like a person name (2-5 capitalised words).
    """
    title_el = el.find(f'{ns}title')
    if title_el is None:
        return False
    heading = extract_text_from_element(title_el).strip()
    if not heading:
        return False

    # Check if heading matches a known author
    heading_is_author = False
    if author_names:
        heading_norm = _normalise_name(heading)
        for name in author_names:
            name_norm = _normalise_name(name)
            if not name_norm:
                continue
            # Match if heading contains the author's family name or full name
            name_parts = name_norm.split()
            family_name = name_parts[-1] if name_parts else ''
            if heading_norm == name_norm or (family_name and family_name in heading_norm):
                heading_is_author = True
                break

    # Fallback: heading looks like a person name (2-5 capitalised words, short)
    if not heading_is_author:
        words = heading.split()
        if not (2 <= len(words) <= 5) or len(heading) > 60:
            return False
        section_words = {'references', 'bibliography', 'notes', 'endnotes',
                         'footnotes', 'acknowledgements', 'appendix', 'abstract',
                         'introduction', 'conclusion', 'discussion', 'method',
                         'results', 'coda', 'postscript', 'epilogue', 'about'}
        if heading.lower() in section_words or words[0].lower() in section_words:
            return False
        if not (words[0][0].isupper() and words[-1][0].isupper()):
            return False

    # First paragraph must start with a bio-verb phrase
    bio_starters = re.compile(
        r'^(is\s+a[n]?\s|was\s+a[n]?\s|has\s+been\s|works\s+|has\s+a\s+'
        r'|is\s+currently\s|is\s+a\s+|trained\s+|practices\s+|lectures\s+'
        r'|completed\s+|studied\s+|teaches\s+|holds\s+a\s+)',
        re.IGNORECASE
    )
    for child in el:
        if _local(child.tag) == 'p':
            text = extract_text_from_element(child).strip()
            if text and bio_starters.match(text):
                return True
            break  # only check first paragraph

    return False


def find_jats_reference_sections(body: ET.Element, tail_only: bool = True,
                                  author_names: list[str] = None) -> list[dict]:
    """Find reference-like sections in a JATS <body> element.

    Returns a list of dicts with 'heading', 'items', 'element' keys.
    If tail_only=True, only returns contiguous back-matter sections at the
    end of the body (references, notes, and author bio sections).

    author_names: list of article author names from JATS <front>. Used to
    detect bio sections whose heading matches a known author.
    """
    ns = _ns(body.tag)
    secs = list(body)  # direct children

    # Build list of (heading_text, is_back_matter, index, element)
    sec_info = []
    for i, el in enumerate(secs):
        if _local(el.tag) != 'sec':
            continue
        title_el = el.find(f'{ns}title')
        heading = extract_text_from_element(title_el) if title_el is not None else ''
        is_ref = bool(REFERENCE_HEADING_RE.match(heading))
        is_bio = _is_bio_section(el, ns, author_names=author_names)
        is_back = is_ref or is_bio
        sec_info.append((heading, is_back, is_bio, i, el))

    if not sec_info:
        return []

    if tail_only:
        # Walk backwards to find contiguous back-matter sections at the tail
        tail_start = None
        for si in range(len(sec_info) - 1, -1, -1):
            if sec_info[si][1]:  # is_back_matter
                tail_start = si
            else:
                break
        if tail_start is None:
            return []
        sec_info = sec_info[tail_start:]

    result = []
    for heading, is_back, is_bio, idx, el in sec_info:
        if not is_back:
            continue
        items = _extract_items_from_jats_section(el)
        # For bio sections, prepend the heading (person name) to first item
        # so is_author_bio() sees "Name is a ..." instead of just "is a ..."
        if is_bio and items:
            items[0] = heading + ' ' + items[0]
        result.append({
            'heading': heading,
            'items': items,
            'element': el,
        })

    return result


def _extract_items_from_jats_section(sec_el: ET.Element) -> list[str]:
    """Extract individual text items from a JATS <sec> element.

    Looks for <p> children (skipping <title>), extracts their text content.
    Also handles <list>/<list-item> structures.
    """
    ns = _ns(sec_el.tag)
    items = []

    # Check for list items first
    list_items = sec_el.findall(f'.//{ns}list-item')
    if list_items:
        for li in list_items:
            text = extract_text_from_element(li).strip()
            if text:
                items.append(text)
        return items

    # Fall back to <p> elements
    for child in sec_el:
        if _local(child.tag) == 'title':
            continue
        if _local(child.tag) == 'p':
            text = extract_text_from_element(child).strip()
            if text:
                items.append(text)
        elif _local(child.tag) == 'disp-quote':
            text = extract_text_from_element(child).strip()
            if text:
                items.append(text)

    return items


def _ns(tag: str) -> str:
    """Extract namespace prefix from a tag like '{http://...}name'."""
    if '}' in tag:
        return tag[:tag.index('}') + 1]
    return ''


# Internal alias — callers outside this module should use local_name()
_local = local_name


# ---------------------------------------------------------------
# Classification thresholds
# ---------------------------------------------------------------
# These control behaviour in is_reference, is_note, is_author_bio, etc.
# Named here so they can be tuned without hunting through functions.

# --- Classification thresholds with justification ---
#
# LONG_REF_THRESHOLD: body paragraphs are typically 200-800 chars; references
# rarely exceed 300 chars (dataset 95th percentile: ~280). Texts longer than
# this get extra validation (must have author+year at start) to avoid
# classifying body paragraphs as references.
LONG_REF_THRESHOLD = 300
#
# BIO_MIN_LENGTH: the "is a" bio phrase check has false positives on short
# fragments. Real bios are at least a sentence (~50+ chars). Below this,
# the phrase check is skipped (pattern-only matching still applies).
BIO_MIN_LENGTH = 50
#
# BIO_PHRASE_SEARCH_WINDOW: bio phrases like "is a practitioner" must appear
# near the start of the text (within a sentence or two). At 150 chars,
# that's roughly the first 1-2 sentences. A bio phrase buried at char 300
# is likely body text, not a bio.
BIO_PHRASE_SEARCH_WINDOW = 150
#
# AUTHOR_YEAR_SEARCH_WINDOW: in references, the author and (year) always
# appear within the first ~80 chars. "Surname, I. and Surname, I. (YYYY)"
# is the longest common format at ~60 chars. 80 gives margin.
AUTHOR_YEAR_SEARCH_WINDOW = 80
#
# MIN_CLASSIFIABLE_LENGTH: items shorter than this can't be a meaningful
# reference or note classification candidate. Shortest real note in
# dataset is "Ibid." (5 chars), but the is_note function has specific
# Ibid detection. Below 15 chars, general classification is unreliable.
MIN_CLASSIFIABLE_LENGTH = 15
#
# NOTE_MAX_SENTENCES / NOTE_LONG_TEXT: notes are typically 1-2 sentences.
# A 2+ sentence text over 350 chars is more likely a body paragraph that
# leaked into the back-matter. These work together as a compound check.
NOTE_MAX_SENTENCES = 2
NOTE_LONG_TEXT = 350
#
# REF_MIN_TITLE_WORDS: after stripping author(s) and year, the remainder
# must have at least 1 word with 3+ characters that looks like a title.
# Prevents matching on author-name-only fragments like 'Smith, J. 1995'.
REF_MIN_TITLE_WORDS = 1
#
# SUBLABEL_MAX_LENGTH: section sublabels like 'English-language references:'
# are typically 10-50 chars. Longer text is prose.
SUBLABEL_MAX_LENGTH = 50
#
# CITATION_SENTENCE_LENGTH: texts with 3+ sentences over this length are body
# paragraphs, not citations. Longest real citation in dataset is ~180 chars.
CITATION_SENTENCE_LENGTH = 200
#
# CITATION_WEAK_SIGNAL_LENGTH: longer texts need 3+ citation signals to
# qualify. Body paragraphs with 1-2 signals are common above this length.
CITATION_WEAK_SIGNAL_LENGTH = 400


# ---------------------------------------------------------------
# Item classification: junk / citation-like / bio / provenance
# ---------------------------------------------------------------

def is_junk(text: str) -> bool:
    """Filter out non-citation junk from reference sections."""
    if len(text) < MIN_CLASSIFIABLE_LENGTH:
        return True

    has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', text))
    has_author_pattern = bool(re.search(r'[A-Z][a-zà-ü]+,?\s', text))

    if not has_year and not has_author_pattern:
        return True

    if text.strip().rstrip('.').lower() in ('yours sincerely', 'yours faithfully',
                                             'kind regards', 'best wishes'):
        return True

    stripped = text.strip().rstrip('.')
    if (not has_year
            and re.match(r'^(Dr\.?\s+)?[A-Z][a-zà-ü]+(\s+[A-Z]\.?)*(\s+[A-Z][a-zà-ü-]+){0,3}$', stripped)
            and len(stripped.split()) <= 5):
        return True

    if is_author_bio(text):
        return True

    if is_provenance(text):
        return True

    return False


def is_citation_like(text: str) -> bool:
    """Check if a text item looks like it contains a citation.

    Used to filter Notes/Endnotes — keep items with year + author pattern,
    skip pure commentary.
    """
    if len(text) < MIN_CLASSIFIABLE_LENGTH:
        return False

    has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', text))
    has_author_pattern = bool(re.search(r'[A-Z][a-zà-ü]+,?\s', text))
    has_publisher = bool(re.search(
        r'(' + PUBLISHER_NAMES + r')', text, re.IGNORECASE))
    has_journal = bool(re.search(
        r'(Journal|Review|Quarterly|Analysis|Psycholog|Psychother|Existential)',
        text, re.IGNORECASE))
    has_pages = bool(re.search(r'\b\d+[-–]\d+\b', text))
    has_doi = bool(re.search(r'doi[:\s]|10\.\d{4,}/', text, re.IGNORECASE))
    has_url = bool(re.search(r'https?://', text))

    score = sum([has_year, has_author_pattern, has_publisher, has_journal,
                 has_pages, has_doi, has_url])

    sentence_count = _count_sentences(text)
    if sentence_count >= 3 and len(text) > CITATION_SENTENCE_LENGTH:
        return False
    if sentence_count >= NOTE_MAX_SENTENCES and len(text) > NOTE_LONG_TEXT:
        return False
    if len(text) > CITATION_WEAK_SIGNAL_LENGTH and score < 3:
        return False

    return score >= 2 or (score >= 1 and has_year)


# ---------------------------------------------------------------
# Person name detection
# ---------------------------------------------------------------

# Max words in a text element to be considered a potential person name
NAME_MAX_WORDS = 6

# Common English words that would not appear in a person's name.
# Only checked when the word is lowercase in the source text.
NOT_NAME_WORDS = frozenset({
    'a', 'an', 'the',                         # articles
    'in', 'on', 'at', 'to', 'by', 'of',      # prepositions
    'for', 'from', 'with', 'about', 'into',
    'as', 'or', 'but', 'nor', 'yet', 'so',   # conjunctions
    'is', 'was', 'are', 'were', 'be',         # verbs
    'some', 'towards', 'between', 'toward',
    'why', 'how', 'what', 'when', 'where',
    'not', 'its', 'it', 'this', 'that',
})

# Words that are NEVER part of a person's name, even when capitalised.
# Contrast with NOT_NAME_WORDS which only match lowercase (because "Van",
# "Le", etc. can be capitalised name particles). These words have no
# plausible name interpretation in any case.
NEVER_NAME_WORDS = frozenset({
    'on', 'in', 'at', 'to', 'by', 'of',                       # short prepositions
    'for', 'from', 'with', 'into',
    'between', 'towards', 'toward', 'through', 'about', 'against',
    'before', 'after', 'during', 'without', 'within', 'beyond',
    'its', 'his', 'her', 'their', 'our', 'my',                # possessive pronouns
})

# Nobiliary particles allowed in names (only matched when lowercase)
NAME_PARTICLES = frozenset({
    'van', 'de', 'du', 'von', 'le', 'la', 'di', 'el', 'al',
    'bin', 'del', 'der', 'dos', 'den', 'das', 'da', 'ibn',
})


# Word suffixes that are virtually never found in person names.
# Only truly safe suffixes — many endings (-ing, -ous, -ling, -ive)
# appear in real surnames (Rowling, D'Astous, Hastings, etc.).
_NON_NAME_SUFFIXES = re.compile(
    r'(?:tion|sion|ment|ness|ical|ology|ophy|istry|ence|ance)$',
    re.IGNORECASE
)

# Capitalised words that are never person names but appear in titles.
# Kept small and conservative — only add words with zero plausible
# name interpretations across cultures.
_TITLE_WORDS = frozenset({
    'debunking', 'antipsychiatry', 'psychotherapy', 'counselling',
    'counseling', 'psychiatry', 'psychology', 'phenomenology',
    'existentialism', 'philosophy', 'hermeneutics', 'ontology',
    'therapy', 'therapeutic', 'clinical', 'embodiment',
})


def _is_single_person_name(text: str) -> bool:
    """Check if text is a single person name (no 'and'/'&' connectors).

    Counts capitalised words, initials (with/without dots), and name
    particles. Returns True if at least 2 capitalised name parts found.
    """
    words = text.split()
    if len(words) < 2 or len(words) > NAME_MAX_WORDS:
        return False

    cap_count = 0
    for w in words:
        w_clean = w.rstrip('.,;:')
        if not w_clean:
            continue
        # Strip possessive 's (e.g. "Heidegger's" → "Heidegger")
        if w_clean.endswith("'s") or w_clean.endswith('\u2019s'):
            w_clean = w_clean[:-2]
        if not w_clean:
            continue
        # Nobiliary particles only match when lowercase (van, de, du).
        # "Del" as a given name should not match the particle "del".
        if w_clean[0].islower() and w_clean.lower() in NAME_PARTICLES:
            continue
        # Reject words with non-name suffixes (Conscience, Philosophical, etc.)
        if len(w_clean) >= 6 and _NON_NAME_SUFFIXES.search(w_clean):
            return False
        # Reject known title/domain words (Psychotherapy, Debunking, etc.)
        if w_clean.lower() in _TITLE_WORDS:
            return False
        # Initials with dot: "W.", "R.D.", "F."
        if re.match(r'^[A-ZÀ-Ž]\.$', w_clean) or re.match(r'^(?:[A-ZÀ-Ž]\.)+$', w_clean):
            cap_count += 1
            continue
        # Single capital letter without dot (e.g. "Kirk J Schneider")
        if re.match(r'^[A-ZÀ-Ž]$', w_clean):
            cap_count += 1
            continue
        # Capitalised word (including hyphenated: Ann-Helen, Merleau-Ponty)
        if re.match(r'^[A-ZÀ-Ž]', w_clean):
            cap_count += 1
            continue
        # Hyphenated with particle prefix: al-Rashid, el-Sayed
        if '-' in w_clean:
            prefix = w_clean.split('-')[0].lower()
            if prefix in NAME_PARTICLES:
                cap_count += 1
                continue
        # Lowercase word that isn't a particle = not a name
        return False

    return cap_count >= 2


def looks_like_person_name(text: str) -> bool:
    """Check if text looks like a person name (not a subtitle or heading).

    Works for Western and non-Western names by checking structure:
    - Short (within NAME_MAX_WORDS)
    - No common English words that wouldn't appear in names
    - Each word is capitalised, an initial, or a name particle
    - At least 2 capitalised words

    Handles: "Titos Florides", "Emmy van Deurzen", "R.D. Laing",
    "Ann-Helen Siirala", "Jiří Různička", "Del Loewenthal",
    "Mohammed al-Rashid", "F. A. Jenner"
    """
    clean = text.rstrip('.').strip()
    if not clean or len(clean) > 80:
        return False
    if clean[-1] in '?!':
        return False

    words = clean.split()
    if len(words) < 2 or len(words) > NAME_MAX_WORDS:
        return False

    # If ANY lowercase word is a common non-name word, reject
    for w in words:
        w_clean = w.rstrip('.,;:')
        if not w_clean:
            continue
        # Words that are never names, even capitalised
        if w_clean.lower() in NEVER_NAME_WORDS:
            return False
        # Other non-name words only rejected when lowercase in source
        if w_clean[0].islower() and w_clean.lower() in NOT_NAME_WORDS:
            return False

    # "A [Word]" / "An [Word]" is an English article + noun, not initial + surname.
    # NOT_NAME_WORDS catches lowercase 'an' but not capitalised 'An' (since
    # 'An' could theoretically be a name). The 2-word guard catches it here.
    if words[0] in ('A', 'An') and len(words) == 2:
        return False

    # "Name and Name" / "Name & Name" — check each half
    if ' and ' in clean or ' & ' in clean:
        parts = re.split(r'\s+(?:and|&)\s+', clean)
        if len(parts) == 2 and all(_is_single_person_name(p.strip()) for p in parts):
            return True

    return _is_single_person_name(clean)


# ---------------------------------------------------------------
# ALL CAPS normalisation
# ---------------------------------------------------------------

# Words that stay lowercase in title case (unless first word)
TITLE_CASE_LOWERCASE = frozenset({
    'a', 'an', 'the', 'and', 'but', 'or', 'nor', 'for', 'yet', 'so',
    'in', 'on', 'at', 'to', 'by', 'of', 'as', 'is', 'it',
})


def title_case_words(text: str) -> str:
    """Convert text to title case, keeping small words lowercase (except first)."""
    words = text.split()
    result = []
    for i, word in enumerate(words):
        prefix = ''
        suffix = ''
        core = word
        while core and not core[0].isalpha():
            prefix += core[0]
            core = core[1:]
        while core and not core[-1].isalpha():
            suffix = core[-1] + suffix
            core = core[:-1]

        if not core:
            result.append(word)
            continue

        lower = core.lower()
        if i > 0 and lower in TITLE_CASE_LOWERCASE:
            result.append(prefix + lower + suffix)
        else:
            result.append(prefix + core.capitalize() + suffix)

    return ' '.join(result)


def normalise_allcaps(text: str) -> str:
    """Convert ALL CAPS text to title case.

    Only triggers when every alphabetic character is uppercase.
    Also handles mixed text where the non-quoted portion is ALL CAPS.
    Returns text unchanged if it's already mixed case.
    Preserves Roman numerals (I, II, III, IV, etc.).
    """
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return text

    # Preserve Roman numerals
    if re.match(r'^[IVXLCDM]+$', text.strip()):
        return text

    if all(c.isupper() for c in alpha_chars):
        return title_case_words(text)

    # Check if text before any quote is all caps
    quote_pos = len(text)
    for q in ('"', "'", '\u201c'):
        p = text.find(q)
        if p > 0 and p < quote_pos:
            quote_pos = p
    if quote_pos > 0:
        before = text[:quote_pos]
        alpha_before = [c for c in before if c.isalpha()]
        if alpha_before and all(c.isupper() for c in alpha_before):
            normalised = title_case_words(before.rstrip())
            # Preserve the whitespace between the caps portion and the quote
            trailing_ws = before[len(before.rstrip()):]
            return normalised + trailing_ws + text[quote_pos:]

    return text


def is_author_contact(text: str) -> bool:
    """Detect author contact details (address, email, affiliation lines)."""
    return bool(re.match(
        r'^(Contact|Address|Email|E-mail|Correspondence|Tel|Telephone|Fax|Website)\s*:',
        text, re.IGNORECASE
    )) or bool(re.match(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', text.strip()))


def is_author_bio(text: str) -> bool:
    """Detect author biographical notes and contact details."""
    if is_author_contact(text):
        return True

    # Structural phrases that indicate biographical content
    bio_phrases = [
        'private practice', 'in practice', 'working in ', 'works with',
        'works as', 'works at', 'academic interests', 'has been a ',
        'Research Fellow', 'has a particular interest',
        'currently in private', 'currently a ', 'currently training',
    ]
    # Profession/role keywords that distinguish bios from body text.
    # A bio describes someone's professional role or affiliation.
    # Without one of these, "Name is/was..." is just body text.
    BIO_ROLE_WORDS = [
        'professor', 'lecturer', 'therapist', 'psychologist',
        'psychotherapist', 'counsellor', 'counselor', 'supervisor',
        'researcher', 'practitioner', 'clinician', 'psychiatrist',
        'trainer', 'coach', 'director', 'chair', 'fellow',
        'candidate', 'student', 'doctoral', 'emeritus',
        'university', 'institute', 'college', 'school',
        'nhs', 'private practice', 'visiting tutor',
        'member of', 'author of', 'writer', 'founder', 'editor',
    ]
    text_lower = text.lower()
    has_bio_phrase = (any(phrase in text for phrase in bio_phrases)
                     or any(word in text_lower for word in BIO_ROLE_WORDS))

    bio_patterns = [
        r'^[A-Z]{2,}[\s\.\-]+[A-Z][\s\.\-\w]*\b(is|was|has)\s',  # ALL CAPS: "CHARLES SCOTT is..."
        r'^[A-Z]+\s+(?:van|de|von)\s+[A-Z\-]+\s+(is|was|has)\s',  # ALL CAPS + prefix
        r'^All (three|four|five|six) authors',
    ]

    if has_bio_phrase and any(re.match(p, text) for p in bio_patterns):
        return True

    # Check if text starts with a person name followed by a bio verb
    # AND contains a profession/role indicator. Without a profession keyword,
    # "Name is/was/has..." is just body text discussing a person.
    # Reject: possessives, interview transcripts (colon after name),
    # conjunction/adverb prefixes, reference format (year in parens).
    # Also reject interview transcripts early (Name: dialogue...)
    # Reject interview transcripts and conjunction/adverb openers
    if re.match(r'^[A-Z][a-z]*\s*:', text) or re.match(r'^[A-Z]+\s*:', text):
        return False
    if re.match(r'^(However|Firstly|Secondly|Furthermore|Moreover|'
                r'Ultimately|Nevertheless|Importantly|Meanwhile|'
                r'Similarly|Conversely|Accordingly|Indeed|Notably|'
                r'Although|While|Whereas|Since|Because|After|'
                r'Afterwards)\b', text):
        return False
    # Year-in-parens pattern for rejecting references: (1999), (1980b), etc.
    _YEAR_IN_PARENS = re.compile(r'\(\d{4}[a-d]?\)')

    bio_verb = re.search(r'\b(is|was|has)\s', text[:BIO_PHRASE_SEARCH_WINDOW])
    if bio_verb:
        leading = text[:bio_verb.start()].strip().rstrip(',')
        # Reject if possessive before verb ("Name's X is...")
        if "'s " in text[:bio_verb.start()]:
            pass
        # Reject reference format ("Gans, S.(1999)...", "Kierkegaard, S. (1980b)...")
        elif _YEAR_IN_PARENS.search(text[:bio_verb.start()]):
            pass
        elif leading and looks_like_person_name(leading):
            # Must have a profession/role indicator — "Name is/was" alone
            # is body text, not a bio
            if has_bio_phrase:
                return True

    # Bio phrase near start + starts with person name
    if has_bio_phrase and len(text) > BIO_MIN_LENGTH:
        # Extract first 2-5 words as potential name
        words = text.split()
        for n in range(min(len(words), NAME_MAX_WORDS), 1, -1):
            candidate = ' '.join(words[:n])
            if looks_like_person_name(candidate):
                window = text[:BIO_PHRASE_SEARCH_WINDOW]
                window_lower = window.lower()
                early_bio = (any(phrase in window for phrase in bio_phrases)
                             or any(word in window_lower for word in BIO_ROLE_WORDS))
                if early_bio and not _YEAR_IN_PARENS.search(text[:AUTHOR_YEAR_SEARCH_WINDOW]):
                    return True
                break

    return False


def is_section_sublabel(text: str) -> bool:
    """Detect section sub-labels like 'English-language references:' within back-matter."""
    return len(text) < SUBLABEL_MAX_LENGTH and text.rstrip().endswith(':')


def is_provenance(text: str) -> bool:
    """Detect article provenance notes (conference presentations, origins).

    Single source of truth for provenance detection — used by
    extract_citations.py and extract_subtitles.py (via is_provenance_note).

    Provenance describes the origin of THIS article — not editorial text
    discussing conference papers in general. The subject must be "this
    paper/article/talk" or an equivalent first-person construction.
    """
    # Strip leading parentheses/brackets — provenance notes sometimes
    # appear in parenthetical form: "(This paper is an edited version...)"
    stripped = text.strip()
    if stripped.startswith('(') and stripped.endswith(')'):
        stripped = stripped[1:-1].strip()

    provenance_patterns = [
        # "This paper was originally presented at..."
        r'^(This|A version of this|An earlier version of this|A shorter version of this)\s+'
        r'(article|paper|chapter|essay|lecture|talk)\s+(is|was)\s',
        # "This paper/article was given/presented/delivered..."
        r'^(?:This\s+)?(?:paper|article|talk|presentation)\s+(?:was\s+)?(?:given|presented|delivered)',
        # "Presentation, 19 November 2011..." / "Presentation given"
        r'^Presentation[,\s]',
        # "Based on a presentation/keynote/talk/version..."
        r'^Based\s+on\s+(?:a\s+)?(?:presentation|version|talk|paper|keynote)',
        # "Paper/Talk/Keynote given/delivered/presented at..."
        r'^(?:Paper|Talk|Keynote)\s+(?:given|delivered|presented)',
        # "Adapted/Revised/Expanded from..."
        r'^(?:Adapted|Revised|Expanded)\s+(?:from|version)',
    ]
    if any(re.search(p, stripped, re.IGNORECASE) for p in provenance_patterns):
        return True

    # Conference name patterns — only match when the text is about THIS article's
    # provenance (subject is "this paper/article"), not editorial text that
    # merely mentions a conference. Require a self-referential subject.
    conference_patterns = [
        r'Society\s+for\s+Existential\s+Analysis\s+(?:Annual\s+)?Conference',
        r'World\s+Congress\s+for\s+Exist',
        r'Annual\s+Conference',
    ]
    # Subject must refer to THIS article — not other papers in general
    self_referential = re.compile(
        r'(?:^This\s+(?:paper|article|talk|lecture|essay)\s|'
        r'^A\s+version\s|^Based\s+on\s|'
        r'^(?:This\s+)?(?:paper|article)\s+was\s)',
        re.IGNORECASE,
    )
    for cp in conference_patterns:
        if re.search(cp, stripped, re.IGNORECASE) and self_referential.search(stripped):
            return True

    return False


# ---------------------------------------------------------------
# Tier classification: reference vs note (from split_citation_tiers)
# ---------------------------------------------------------------

def is_note(text: str) -> str | None:
    """Return a reason string if this item is a Note, else None.

    Rules checked in order — first match wins.
    """
    if re.match(r'^(See |see |cf\.\s|Cf\.\s)', text):
        return 'see-crossref'

    if len(text) < 80 and re.search(r'\b(Ibid\.?|Ibidem|Op\.?\s*cit)', text, re.IGNORECASE):
        return 'ibid'

    stripped_num = re.sub(r'^\d+[\.\)\s]+', '', text).strip()
    if _is_short_surname_year(stripped_num):
        return 'short-ref'

    if re.match(r'^\d+[\.\)\s]', text):
        after_num = re.sub(r'^\d+[\.\)\s]+', '', text).strip()
        if _is_numbered_commentary(after_num):
            return 'numbered-commentary'

    # Roman numeral prefix + commentary (i, ii, iii, iv, ... xvi, etc.)
    if re.match(r'^[ivxlc]+\s', text, re.IGNORECASE):
        after_roman = re.sub(r'^[ivxlc]+\s+', '', text, flags=re.IGNORECASE).strip()
        if _is_numbered_commentary(after_roman):
            return 'roman-numeral-commentary'

    # Superscript numeral prefix + commentary (¹, ², ³, etc.)
    if re.match(r'^[¹²³⁴⁵⁶⁷⁸⁹⁰]+\s', text):
        after_super = re.sub(r'^[¹²³⁴⁵⁶⁷⁸⁹⁰]+\s+', '', text).strip()
        if _is_numbered_commentary(after_super):
            return 'superscript-commentary'

    if is_author_bio(text):
        return 'author-bio'

    if is_provenance(text):
        return 'provenance'

    # Author statements, funding, COI, ethics disclosures
    if re.match(
        r'^(Author statement|Funding statement|Funding:|'
        r'Conflicts? of interest|Declaration of interest|'
        r'Ethical approval|Ethics statement|'
        r'Data availability)',
        text, re.IGNORECASE,
    ):
        return 'author-statement'

    if re.match(r'^https?://', text.strip()) or re.match(r'^\d+\s+https?://', text.strip()):
        return 'url-only'

    if re.match(r'^Contact:', text) or re.match(r'^https?://orcid\.org/', text):
        return 'contact-info'

    # Contact/editorial addresses
    if re.match(r'^(Contact address|Messrs)\b', text):
        return 'contact-info'

    name_only = text.strip().rstrip('.')
    if re.match(r'^[A-Z][a-zà-ž]+\s+[A-Z][a-zà-ž-]+$', name_only) and len(name_only) < 40:
        return 'name-only'

    return None


def is_reference(text: str) -> bool:
    """Check if item is a proper bibliographic reference.

    Must have: author pattern + year.
    """
    # Strip soft hyphens (PDF artefact from word-wrapping)
    text = text.replace('\u00ad', '')
    clean = re.sub(r'^[\d\*•·–—\-]+[\.\)\s]*', '', text).strip()

    # Reject common English words that aren't surnames
    if re.match(r'^(It|In|As|At|An|If|Is|Or|On|So|Do|No|My|He|We|But|Yet|For|The|This|That|'
                r'What|Which|Where|When|How|Why|From|With|About|After|Before|Between|'
                r'During|Into|Through|Under|There|Their|These|Those|Here|Very|Also|'
                r'Although|However|Therefore|Furthermore|Moreover|Nevertheless)\s', clean):
        return False

    # Rule 1: Author name at or near start
    has_author = bool(re.match(
        r"^(?:van\s+(?:den?\s+)?|de\s+|von\s+|du\s+|le\s+|la\s+|al-|ben-|St\.\s+)?"
        r"(?:Mc|Mac|Di|Du|Le|De|O'|D')?"
        r"[A-ZÀ-Ž\u0400-\u04FF]"
        r"[a-zà-ž\u0400-\u04FF'ğışçöüřžščůķīūė]+"
        r"(?:[–—-][A-ZÀ-Ž][a-zà-ž'ğışçöüřžščůķīūė]+)?"
        r"[,\.\s]+",
        clean
    )) or bool(re.match(
        r'^[A-Z][a-zà-ž]+\s+(?:de |van |von )?[A-Z][a-zà-ž]+(?:-[A-Z][a-zà-ž]+)?\s*[,\.\(]',
        clean
    )) or bool(re.match(
        r'^[A-Z][A-Z\s]+,', clean
    )) or bool(re.match(
        r'^[A-Z]\.?\s+[A-Z][a-zà-ž]+', clean
    )) or bool(re.match(
        r'^[-–—•]\s+\(?\d{4}', clean
    )) or bool(re.match(
        r'^[A-Z]{2,6}[\.\s]', clean
    )) or bool(re.match(
        r'^(Plato|Anonymous|Aristotle|Homer|Euripides|Sophocles|Heraclitus|Parmenides|Shakespeare|Machiavelli)\b',
        clean
    )) or bool(re.match(
        r'^\([A-Z]', clean
    )) or bool(re.match(
        r'^[\u0400-\u04FF]', clean
    )) or bool(re.match(
        r'^["\'][A-Z]', clean
    )) or bool(re.match(
        r'^hooks,', clean
    )) or bool(re.match(
        r'^[A-Z][a-zà-ž]+,\s+[A-Z][a-z]+\s+[A-Z]', clean
    )) or bool(re.match(
        r'^[A-Z][a-z]+[A-Z][a-z]+', clean
    )) or bool(re.match(
        r'^From:', clean
    )) or bool(re.match(
        r'^[¹²³⁴⁵⁶⁷⁸⁹⁰ⁱⁱⁱ]+\s', clean
    )) or bool(re.match(
        r'^[ivxlc]+\s', clean, re.IGNORECASE
    ))

    if not has_author:
        return False

    # Rule 2: Contains a year
    has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)[a-d]?\b', clean))
    has_year_fuzzy = has_year or bool(re.search(r'\b1\s?\d{3}\b', clean))
    has_year_fuzzy = has_year_fuzzy or bool(re.search(r'\b[lI]\d{3}\b', clean))
    has_year_fuzzy = has_year_fuzzy or bool(re.search(r'\b(forthcoming|in press|in print|n\.d\.?|undated)\b', clean, re.IGNORECASE))
    has_year_fuzzy = has_year_fuzzy or bool(re.search(r'\(\d{4}', clean))

    if not has_year_fuzzy:
        has_publisher = bool(re.search(
            r'\b(' + PUBLISHER_NAMES + r')\b', clean, re.IGNORECASE))
        has_place = bool(re.search(
            r'(London|New York|Cambridge|Oxford|Paris|Berlin|Edinburgh|Boston|Chicago)',
            clean, re.IGNORECASE))
        if not (has_publisher or has_place):
            return False

    cite_refs = len(re.findall(r'[A-Z][a-z]+\s*[\(,]\s*\d{4}', clean))
    semicolon_refs = len(re.findall(r';\s*[A-Z][a-z]+', clean))
    if cite_refs >= 3:
        return False
    if (cite_refs + semicolon_refs) >= 4:
        return False

    # Long texts: require author-like start + year to avoid classifying
    # body paragraphs as references. Accept multiple author formats:
    # "Surname, I. (YYYY)", "Surname I. and Surname, I. (YYYY)", etc.
    if len(clean) > LONG_REF_THRESHOLD:
        has_author_year_start = bool(re.match(
            r'^[A-ZÀ-Ž][a-zà-ž\u015b\u0107\u017c\u0142\u0144]+[,\s]+[A-Z]\.?.*?\(\d{4}\)',
            clean[:AUTHOR_YEAR_SEARCH_WINDOW]
        ))
        if not has_author_year_start:
            return False

    remainder = clean
    remainder = re.sub(r'^\d+[\.\)\s]*', '', remainder).strip()
    # Strip one or more authors: "Surname, I., Surname, I. and Surname, I."
    # Repeat to handle multiple comma-separated authors
    _AUTHOR_PAT = (r'(?:van\s+(?:den?\s+)?|de\s+|von\s+|du\s+)?'
                   r"(?:Mc|Mac|Di|Du|O'|D')?"
                   r'[A-ZÀ-Ž\u0400-\u04FF][a-zà-ž\u0400-\u04FF\']+(?:[–—-][A-Z][a-zà-ž]+)?'
                   r'[,\.\s]+(?:[A-Z]\.?\s*)?')
    remainder = re.sub(r'^' + _AUTHOR_PAT + r'(?:(?:,\s*|\s+and\s+)' + _AUTHOR_PAT + r')*',
                       '', remainder).strip()
    remainder = re.sub(r'\(?\d{4}[a-d]?(?:\s*\[\d{4}[a-d]?\])?\)?[,\.\s:;]*', '', remainder).strip()
    remainder = re.sub(r'^(?:pp?\.?\s*)?\d+[-–]?\d*[,\.\s]*', '', remainder).strip()
    remainder = re.sub(r'^(?:n\.d\.?|forthcoming|in press)[,\.\s]*', '', remainder, flags=re.IGNORECASE).strip()

    title_words = re.findall(r'[A-Za-zÀ-žà-ž\u0400-\u04FF]{3,}', remainder)
    if len(title_words) < REF_MIN_TITLE_WORDS:
        return False

    return True


def classify(text: str) -> str:
    """Classify a citation item as 'reference' or 'note'."""
    note_reason = is_note(text)
    if note_reason:
        return 'note'
    if is_reference(text):
        return 'reference'
    return 'note'


# ---------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------

def _is_short_surname_year(text: str) -> bool:
    """Match: Surname year [: page] pattern with no further content."""
    return bool(re.match(
        r'^[A-Z][a-zà-ü]+(?:\s+and\s+[A-Z][a-zà-ü]+)?'
        r'(?:[,\s]+(?:[A-Z][a-zà-ü]+\s*[&]\s*[A-Z][a-zà-ü]+\s+)?'
        r'\d{4}[a-d]?(?:\s*\[?\d{4}[a-d]?\]?)?'
        r'(?:[,;\s]+(?:or\s+)?\d{4}[a-d]?(?:\s*\[?\d{4}[a-d]?\]?)?)*'
        r')+'
        r'(?:[:\s]+(?:pp?\.?\s*)?\d+[-–]?\d*(?:[-–]\d+)?(?:\s*,\s*\d+[-–]?\d*)*)?'
        r'(?:\s*;\s*my translation)?'
        r'[.\s]*$',
        text
    ))


def _is_numbered_commentary(after_num: str) -> bool:
    """Check if text after stripping number prefix is commentary, not citation."""
    has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', after_num))
    has_author_year = bool(re.search(r'[A-Z][a-z]+.*\d{4}', after_num[:80]))

    if has_year or has_author_year:
        starts_with_author = bool(re.match(
            r"^(?:van\s+(?:den?\s+)?|de\s+|von\s+|du\s+)?"
            r"(?:Mc|Mac|Di|Du|O'|D')?"
            r"[A-ZÀ-Ž][a-zà-ž'ğışçöüřžščůķīūė]+"
            r"(?:[–—-][A-Z][a-zà-ž]+)?"
            r"\s*[,\.\(]",
            after_num
        )) or bool(re.match(
            r'^[A-Z][A-Z\s]+,', after_num
        )) or bool(re.match(
            r'^[A-Z]\.?\s+[A-Z][a-zà-ž]+', after_num
        )) or bool(re.match(
            r'^[-–—•]\s+\(?\d{4}', after_num
        ))

        if starts_with_author:
            return False
        return True

    sentence_count = _count_sentences(after_num)
    if sentence_count >= 2 or len(after_num) > CITATION_SENTENCE_LENGTH:
        return True

    return False
