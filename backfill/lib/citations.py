"""Shared citation classification and reference section detection.

Used by extract_citations.py (extraction from JATS body) and
split_citation_tiers.py (classification of refs vs notes).

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
    r'^(Notes?|Endnotes?|Footnotes?:?)[.:]?$',
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
    """Extract plain text from an XML element (recursive)."""
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(extract_text_from_element(child))
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts).strip()


# ---------------------------------------------------------------
# JATS section detection (replaces HTML h2-based detection)
# ---------------------------------------------------------------

def find_jats_reference_sections(body: ET.Element, tail_only: bool = True) -> list[dict]:
    """Find reference-like sections in a JATS <body> element.

    Returns a list of dicts with 'heading', 'items', 'element' keys.
    If tail_only=True, only returns contiguous reference sections at the
    end of the body (matching strip_references.py behaviour).
    """
    ns = _ns(body.tag)
    secs = list(body)  # direct children

    # Build list of (heading_text, is_reference, index, element)
    sec_info = []
    for i, el in enumerate(secs):
        if _local(el.tag) != 'sec':
            continue
        title_el = el.find(f'{ns}title')
        heading = extract_text_from_element(title_el) if title_el is not None else ''
        is_ref = bool(REFERENCE_HEADING_RE.match(heading))
        sec_info.append((heading, is_ref, i, el))

    if not sec_info:
        return []

    if tail_only:
        # Walk backwards to find contiguous reference sections at the tail
        tail_start = None
        for si in range(len(sec_info) - 1, -1, -1):
            if sec_info[si][1]:  # is_reference
                tail_start = si
            else:
                break
        if tail_start is None:
            return []
        sec_info = sec_info[tail_start:]

    result = []
    for heading, is_ref, idx, el in sec_info:
        if not is_ref:
            continue
        items = _extract_items_from_jats_section(el)
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


def _local(tag: str) -> str:
    """Strip namespace from an element tag."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


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

# ---------------------------------------------------------------
# Confidence scoring (informational, for review/QA)
# ---------------------------------------------------------------
# These scoring functions produce a 0-100 confidence score for QA review.
# They do NOT control matching or categorisation — that's done by
# is_reference, is_note, is_author_bio, which use the named constants
# above. The numeric weights here (+12, -15, etc.) are heuristic tuning
# for a diagnostic score, not behavioural thresholds.

def citation_confidence(text: str, heading: str) -> int:
    """Score 0-100 how confident we are this is a single, clean citation."""
    score = 50

    length = len(text)
    is_notes = bool(NOTES_HEADING_RE.match(heading))
    is_refs = bool(PURE_REFERENCE_HEADING_RE.match(heading))

    if re.search(r'\(\d{4}\)', text):
        score += 12
    elif re.search(r'\b(1[89]\d{2}|20[0-2]\d)\b', text):
        score += 6

    if re.match(r'^[A-Z][a-zà-ü]+,?\s+[A-Z]\.', text):
        score += 12
    elif re.match(r'^\d+[\.\)]\s+[A-Z][a-zà-ü]+', text):
        score += 6

    if re.search(r'(' + PUBLISHER_NAMES + r')', text, re.IGNORECASE):
        score += 8
    if re.search(r'(Journal|Review|Quarterly|Bulletin|Annals|Archives)\s+of\b', text, re.IGNORECASE):
        score += 8
    if re.search(r'(pp?\.?\s*\d+[-–]\d+|\b\d+[-–]\d+\b)', text):
        score += 6
    if re.search(r'doi[:\s]|10\.\d{4,}/', text, re.IGNORECASE):
        score += 10
    if re.search(r'https?://', text):
        score += 4
    if re.search(r'\b(Trans\.|trans\.|Transl\.|ed\.|eds\.|Ed\.|Vol\.)', text):
        score += 4
    if re.search(r'(London|New York|Cambridge|Oxford|Paris|Berlin|Chicago|Boston)\s*:', text):
        score += 6
    if is_refs:
        score += 8

    if is_notes:
        score -= 10
    if length < 30:
        score -= 15
    elif length > 500:
        score -= 20
    elif length > 300:
        score -= 8

    year_count = len(re.findall(r'\b(?:1[89]\d{2}|20[0-2]\d)\b', text))
    if year_count > 3 and length > 200:
        score -= 25
    elif year_count > 2 and length > 150:
        score -= 12

    sentence_count = _count_sentences(text)
    if sentence_count > 3:
        score -= 15
    elif sentence_count > 1 and length > 200:
        score -= 8

    if is_author_bio(text):
        score -= 40
    if re.match(r'^(The|This|It|In|As|For|We|He|She|A)\s', text) and not re.match(r'^The\s', text[:20]):
        score -= 10
    if re.search(r'\b(Ibid\.?|Ibidem|Op\.?\s*cit)', text, re.IGNORECASE):
        score -= 15

    return max(0, min(100, score))


def note_confidence(text: str) -> int:
    """Score 0-100 how confident we are this is a note/endnote."""
    score = 50

    # Strong note signals
    note_reason = is_note(text)
    if note_reason:
        score += 30  # caught by a specific rule

    # Numbered prefix (typical endnote)
    if re.match(r'^\d+[\.\)\s]', text):
        score += 10

    # Prose characteristics (multiple sentences)
    sentence_count = _count_sentences(text)
    if sentence_count >= 2:
        score += 10

    # "See" / "cf." cross-references
    if re.match(r'^(See |see |cf\.|Cf\.)', text):
        score += 15

    # Ibid
    if re.search(r'\b(Ibid\.?|Ibidem|Op\.?\s*cit)', text, re.IGNORECASE):
        score += 15

    # Negative: looks like a reference (penalise)
    if is_reference(text) and not note_reason:
        score -= 30

    return max(0, min(100, score))


def bio_confidence(text: str) -> int:
    """Score 0-100 how confident we are this is an author bio."""
    score = 50

    # Name + "is/was/has" pattern (strongest signal)
    if re.match(r'^[A-Z][A-Z\s\.\-]+\b(is|was|has)\s', text):
        score += 35  # ALL CAPS name
    elif re.match(r'^[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]+.*?\s+(is|was|has)\s', text):
        score += 30  # Mixed case name

    # Bio phrases
    bio_phrases = ['private practice', 'practitioner', 'works with',
                   'academic interests', 'Research Fellow', 'currently a']
    matches = sum(1 for p in bio_phrases if p in text)
    score += matches * 8

    # Credentials
    if re.search(r'\b(PhD|MA|MSc|UKCP|BPS|BACP|MBPsS)\b', text):
        score += 10

    # Length: bios are typically 50-300 chars
    if 50 < len(text) < 400:
        score += 5

    # Negative: has year in parens (more like a citation)
    if re.search(r'\(\d{4}\)', text[:AUTHOR_YEAR_SEARCH_WINDOW]):
        score -= 20

    return max(0, min(100, score))


def provenance_confidence(text: str) -> int:
    """Score 0-100 how confident we are this is a provenance note."""
    score = 50

    if re.match(r'^This (article|paper|chapter|essay|lecture|talk)\s+(is|was)\s', text):
        score += 40

    if re.search(r'(delivered|presented|given)\s+(at|as|to)', text):
        score += 10

    if re.search(r'(revised|adapted|based on|version of)', text, re.IGNORECASE):
        score += 10

    return max(0, min(100, score))


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
    if sentence_count >= 3 and len(text) > 200:
        return False
    if sentence_count >= NOTE_MAX_SENTENCES and len(text) > NOTE_LONG_TEXT:
        return False
    if len(text) > 400 and score < 3:
        return False

    return score >= 2 or (score >= 1 and has_year)


def is_author_bio(text: str) -> bool:
    """Detect author biographical notes."""
    bio_phrases = [
        'is a ', 'is an ', 'was a ', 'was an ',
        'private practice', 'in practice', 'practitioner',
        'working in', 'works with', 'works as',
        'academic interests', 'has been a ',
        'Research Fellow', 'has a particular interest',
        'currently in private', 'currently a ',
    ]
    has_bio_phrase = any(phrase in text for phrase in bio_phrases)

    # Words that start a sentence but aren't person names — exclude from bio patterns
    _NOT_NAME = (r'(?!(?:The|This|That|What|Which|Where|When|How|Why|For|From|'
                 r'With|About|After|Before|Between|During|Into|Through|Under|'
                 r'As|If|In|On|At|By|To|So|Or|An|No|It|We|He|She|They)\s)')

    bio_patterns = [
        r'^[A-Z][A-Z\s\.\-]+\b(is|was|has)\s',  # ALL CAPS: "CHARLES SCOTT is..."
        r'^[A-Z]+\s+(?:van|de|von)\s+[A-Z\-]+\s+(is|was|has)\s',  # ALL CAPS + prefix
        # Mixed case name (with optional prefix: van, de, du, von):
        r'^' + _NOT_NAME + r'[A-Z][a-zà-ü]+\s+(?:(?:van|de|du|von|le|la)\s+)?[A-Z][a-zà-ü]+(?:-[A-Z][a-zà-ü]+)?(?:\s+[A-Z][a-zà-ü]+)?\s+(is|was|has)\s',
        # Name + credentials + "is": "Bo Jacobsen, dr. phil., Ph.D., is..." / "Hans W. Cohn, PhD, is..."
        r'^' + _NOT_NAME + r'[A-Z][a-zà-ü]+\s+(?:[A-Z]\.?\s+)?[A-Z][a-zà-ü]+.*?(?:PhD|Ph\.D\.|dr\.\s*phil|MSc|MA|UKCP|BPS|BACP).*?\b(is|was|has)\s',
        r'^All (three|four|five|six) authors',
        r'^(Dr\.?|Professor)\s+[A-Z][a-zà-ü]+\s+(?:[A-Z]\.?\s+)?[A-Z][a-zà-ü]+.*?\s+(is|was|has)\s',
        r'^' + _NOT_NAME + r'[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]+\s+(PhD|MA|MSc|UKCP|BPS)',
    ]

    if any(re.match(p, text) for p in bio_patterns):
        return True

    # Bio phrase near start + starts with person's name (not just any word)
    # Must be: Name Name (is|was|has) — the name patterns above catch the strict cases,
    # this catches softer ones like "Dr John Smith is a practitioner in private practice"
    if has_bio_phrase and len(text) > BIO_MIN_LENGTH:
        # Must start with a plausible person name (2+ capitalised words)
        starts_with_name = bool(re.match(
            r'^(?:Dr\.?\s+|Professor\s+)?' + _NOT_NAME + r'[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]', text))
        # Bio phrase must appear in first 150 chars (not buried deep in prose)
        early_bio = any(phrase in text[:BIO_PHRASE_SEARCH_WINDOW] for phrase in bio_phrases)
        if starts_with_name and early_bio and not re.search(r'\(\d{4}\)', text[:AUTHOR_YEAR_SEARCH_WINDOW]):
            return True

    return False


def is_section_sublabel(text: str) -> bool:
    """Detect section sub-labels like 'English-language references:' within back-matter."""
    return len(text) < SUBLABEL_MAX_LENGTH and text.rstrip().endswith(':')


def is_provenance(text: str) -> bool:
    """Detect article provenance notes."""
    return bool(re.match(
        r'^(This|A version of this|An earlier version of this|A shorter version of this)\s+'
        r'(article|paper|chapter|essay|lecture|talk)\s+(is|was)\s', text
    ))


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
    if sentence_count >= 2 or len(after_num) > 200:
        return True

    return False
