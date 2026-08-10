"""Crossref API client for matching references to DOIs.

Queries the Crossref works API with bibliographic text and scores results
for confidence-based DOI matching.
"""

import re
import unicodedata
from difflib import SequenceMatcher
import sys

import requests

# Journal's own DOI prefix — used to identify self-citations
OWN_DOI_PREFIX = '10.65828/'

# Regex to detect DOIs already present in reference text. Deliberately greedy —
# a DOI's suffix can contain almost any printable character, so the candidate is
# taken up to whitespace and then cleaned by clean_doi().
DOI_RE = re.compile(r'10\.\d{4,}/\S+')

# A DOI is a 10.NNNN prefix and a non-empty suffix of printable, space-free
# characters. Wiley DOIs legitimately contain <, > and #, so the suffix cannot be
# restricted to an alphanumeric set without losing real DOIs.
DOI_SHAPE_RE = re.compile(r'^10\.\d{4,9}/[!-~]+$')

# Our own suffixes are exactly 8 alphanumerics (OJS) or ea.{vol}.{num}[.{seq}]
# (Harbour). Anything else under our prefix is a run-on, not a DOI we minted.
OWN_DOI_SUFFIX_RE = re.compile(r'^(?:[a-z0-9]{8}|ea\.\d+\.\d+(?:\.\d+)?)$', re.I)

# A second prefix *followed by a slash* means two DOIs ran together, as in
# "10.15697/10.5072/fk20p1509b". The slash is essential: real suffixes routinely
# end in things like ".44.10.1285" (volume.issue.page) or contain a year such as
# ".2010.", and treating those as a second DOI truncates ~30 valid DOIs.
# A trailing prefix with no slash ("10.65828/CJF3PR2310.65828") is instead caught
# by the own-prefix suffix rule, which knows what we actually mint.
_EMBEDDED_PREFIX_RE = re.compile(r'(?<=.)10\.\d{4,9}/')

# Zero-width and soft characters survive copy/paste and OCR but are never part of
# a DOI. Seen live: U+200B inside a Journal of Pacific Rim Psychology DOI.
_INVISIBLE = dict.fromkeys(map(ord, '​‌‍⁠﻿­'), None)

# Typographic look-alikes that OCR and typesetting substitute for ASCII. Seen
# live: U+00D7 for "x" in a Psychological Reports DOI.
_LOOKALIKE = {
    0x00d7: 'x',   # × multiplication sign
    0x2013: '-',   # en dash
    0x2014: '-',   # em dash
    0x2010: '-',   # hyphen
    0x2011: '-',   # non-breaking hyphen
    0x00a0: ' ',   # non-breaking space
}

# Characters that may appear inside a DOI but never legitimately end one.
_TRAILING_JUNK = '.,;:)]}>"\'/\\ \t'
_LEADING_JUNK = '([{<"\''


def is_valid_doi(doi):
    """True if `doi` is shaped like a DOI (10.NNNN prefix, printable suffix)."""
    return bool(doi) and bool(DOI_SHAPE_RE.match(doi))


def is_valid_own_doi(doi):
    """True if `doi` is one of ours AND matches a suffix pattern we actually mint.

    Catches the run-on case that shape alone cannot: "10.65828/C8ECRH72LINKS"
    is well-formed but is our DOI with the following word stuck to it.
    """
    if not is_valid_doi(doi) or not doi.lower().startswith(OWN_DOI_PREFIX):
        return False
    return bool(OWN_DOI_SUFFIX_RE.match(doi[len(OWN_DOI_PREFIX):]))


def suggest_own_doi(doi):
    """Best guess at the DOI a run-on under our own prefix was meant to be.

    Every one of the 1,496 suffixes we have registered is exactly 8
    alphanumerics, so the first 8 of an over-long suffix are almost certainly
    the intended DOI. Advisory only — used in guard messages so a human can see
    the likely fix, never written back into metadata, because a plausible but
    wrong DOI in published data is worse than no DOI at all.
    """
    if not doi or not doi.lower().startswith(OWN_DOI_PREFIX):
        return None
    match = re.match(r'[a-z0-9]{8}', doi[len(OWN_DOI_PREFIX):], re.I)
    if not match:
        return None
    candidate = OWN_DOI_PREFIX + match.group()
    return candidate if candidate.lower() != doi.lower() else None


def clean_doi(raw):
    """Normalise a DOI lifted out of free text; return None if it isn't one.

    Reference text hands us DOIs that have picked up their surroundings: a
    closing bracket, a trailing full stop, the next reference's prefix, an OCR
    look-alike, an invisible character. Strip what cannot belong before the DOI
    is stored, displayed or deposited.
    """
    if not raw:
        return None
    doi = raw.translate(_INVISIBLE).translate(_LOOKALIKE).strip()
    # Wrapping characters first: a leading "[" would otherwise make the DOI's own
    # prefix look like an embedded second one.
    doi = doi.lstrip(_LEADING_JUNK).rstrip(_TRAILING_JUNK)
    match = _EMBEDDED_PREFIX_RE.search(doi)
    if match:
        doi = doi[:match.start()].rstrip(_TRAILING_JUNK)
    if not is_valid_doi(doi):
        return None
    # Our own DOIs have a known shape, so a run-on is detectable and fatal.
    if doi.lower().startswith(OWN_DOI_PREFIX) and not is_valid_own_doi(doi):
        return None
    return doi

CROSSREF_API_URL = 'https://api.crossref.org/works'

# Confidence tiers
TIER_MATCHED = 'matched'
TIER_NO_MATCH = 'no_match'

# Default delay between API requests (seconds)
DEFAULT_DELAY = 0.1

# Scoring thresholds — calibrated on 274 refs across 3 volumes (2026-04-02).
# Exact title containment (sim=1.0): strong evidence, low Crossref score OK
MIN_SCORE_EXACT_TITLE = 20
# High similarity (sim >= 0.8): moderate Crossref score needed
MIN_SCORE_HIGH_SIM = 40
# Lower similarity (sim >= 0.7): high Crossref score needed
MIN_SCORE_MED_SIM = 60
# Single-word titles get similarity halved (too generic to trust)
SINGLE_WORD_TITLE_PENALTY = 0.5


def strip_doi_from_text(text, doi):
    """Remove a DOI from reference text when it will be stored as <pub-id>.

    Strips the DOI along with surrounding prefixes (doi:, DOI:, https://doi.org/)
    and trailing cruft ([Accessed...], trailing punctuation).
    """
    if doi not in text:
        return text
    # Pattern: optional "doi:" or "DOI: https://doi.org/" prefix,
    # the DOI itself, optional trailing ". [Accessed...]" or punctuation
    pattern = (
        r'\s*'
        r'(?:(?:doi|DOI)\s*:\s*)?'          # optional doi: prefix
        r'(?:(?:https?://)?doi\.org/)?'       # optional [https://]doi.org/
        + re.escape(doi)
        + r'\.?'                             # optional trailing dot
        + r'(?:\s*\[Accessed[^\]]*\]\.?)?'   # optional [Accessed...] suffix
    )
    result = re.sub(pattern, '', text).strip()
    # Clean trailing punctuation artifacts
    result = re.sub(r'\.\s*$', '.', result)
    result = result.rstrip('. ') + '.'
    return result


def has_existing_doi(ref_text):
    """Check if a reference already contains a DOI.

    Returns the DOI string if found, or None.
    """
    for match in DOI_RE.finditer(ref_text):
        doi = clean_doi(match.group())
        if doi:
            return doi
    return None


def _clean_query(query):
    """Strip noise from query text that confuses Crossref's search.

    Removes:
    - Parenthetical asides: "(Lecture delivered 1935)", "(1967)", "(eds.)"
    - Translator credits: "Trans. Capuzzi, F."
    - Edition notes: "[1947]", "[1976 2nd ed. revised and expanded]"
    """
    # Strip square-bracketed content: [1947], [2006], [1976 2nd ed. ...]
    query = re.sub(r'\[.*?\]', '', query)
    # Strip parenthetical asides that aren't years:
    # "(Lecture delivered 1935)", "(eds.)", "(ed.)", "(trans.)"
    query = re.sub(r'\((?:Lecture|ed|eds|trans)[\s.].*?\)', '', query,
                   flags=re.IGNORECASE)
    # Strip standalone "(ed.)" / "(eds.)"
    query = re.sub(r'\(eds?\.?\)', '', query, flags=re.IGNORECASE)
    # Strip "Trans. Name, I." or "Trans. Name, I. & Name, I." with optional year
    query = re.sub(
        r',?\s*Trans\.\s+'
        r'[A-ZÀ-Ý][a-zà-ÿ]+,\s*[A-Z]\.?'  # first translator
        r'(?:\s*&\s*[A-ZÀ-Ý][a-zà-ÿ]+,\s*[A-Z]\.?[A-Z]?\.?)*'  # optional additional
        r'(?:\s*\(\d{4}\))?'  # optional year
        r'\.?',
        '', query,
    )
    # Clean up artifacts: repeated punctuation, dangling "&"
    query = re.sub(r'\.\s*\.', '.', query)
    query = re.sub(r',\s*&\s*,', ',', query)
    query = re.sub(r'\(\s+\)', '', query)
    # Collapse whitespace
    query = re.sub(r'\s+', ' ', query).strip()
    return query


# Detects references that lead with an editor/translator role rather than an
# author, e.g. "Fried, G. & Polt, R. (eds.) (2000). Translators' introduction.
# In Heidegger, M. Introduction to Metaphysics."
# Only triggers when the leading name has an explicit role marker (eds/ed/trans).
_EDITOR_LED_IN_BOOK_RE = re.compile(
    r'^[A-ZÀ-Ý][a-zà-ÿ]+,.*?'     # leading name
    r'\((?:eds?\.?|trans\.?)\)'      # explicit role marker: (eds.), (ed.), (trans.)
    r'.*?\.\s+In\s+'                 # then ". In "
    r'([A-ZÀ-Ý][a-zà-ÿ]+,\s*.+)',  # capture main author onwards
    re.IGNORECASE,
)


def _restructure_in_book_query(query):
    """Restructure editor/translator-led references to lead with the main work.

    Only applies when the reference explicitly leads with an editor/translator
    role (eds./ed./trans.) before "In Author. Title." — indicating the main
    work is the book, not the chapter.

    Transforms:
        "Fried, G. & Polt, R. (eds.) (2000). Translators' intro. In Heidegger,
         M. Introduction to Metaphysics. Yale University Press."
    Into:
        "Heidegger, M. Introduction to Metaphysics. Yale University Press."

    Does NOT transform author-led chapter references like:
        "Heidegger, M. (1966). Only a god can save us. In Stassen, M. (ed.)..."
    """
    m = _EDITOR_LED_IN_BOOK_RE.match(query)
    if m:
        return m.group(1).strip()
    return query


# Extracts "Surname. Title." from a standard reference
_AUTHOR_TITLE_RE = re.compile(
    r'^([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ]+)*),'  # surname(s)
    r'.*?(?:\(\d{4}.*?\)\s*\.?\s*|\s+)'                    # skip initials, year (optional)
    r'(.+?)\.'                                               # title (up to first period)
)


def _minimal_query(query):
    """Extract just 'Surname Title' for a minimal Crossref query.

    Sometimes Crossref finds books better with less context — publisher
    names, translator credits, and dates can confuse the search.
    """
    # Try with year first: "Surname, I. (Year). Title."
    m = re.match(
        r'([A-ZÀ-Ý][a-zà-ÿ]+),\s*\S+\s*\(\d{4}.*?\)\.?\s*(.+?)\.', query)
    if not m:
        # Try without year: "Surname, I. Title."
        m = re.match(
            r'([A-ZÀ-Ý][a-zà-ÿ]+),\s*\S+\.?\s+(.+?)\.', query)
    if m:
        surname = m.group(1)
        title = m.group(2).strip()
        if len(title.split()) >= 2:
            return f'{surname} {title}'
    return None


def _build_queries(ref_text):
    """Build a list of query variants to try against Crossref.

    Returns a list of query strings, from most specific to most cleaned.
    The caller should try each and pick the best result across all.
    """
    base = DOI_RE.sub('', ref_text)
    base = re.sub(r'^\d+[\.\)]\s*', '', base).strip()

    queries = [base]

    # Cleaned variant: strip translator/edition noise
    cleaned = _clean_query(base)
    if cleaned != base:
        queries.append(cleaned)

    # Editor-led "In book" restructuring
    restructured = _restructure_in_book_query(base)
    if restructured != base:
        queries.append(restructured)

    # Minimal variant: just "Surname Title" — catches cases where all other
    # queries are too noisy for Crossref (e.g. translator credits, lecture dates)
    minimal = _minimal_query(base)
    if minimal and minimal not in queries:
        queries.append(minimal)

    # Also try minimal from the restructured query (for editor-led refs,
    # the restructured version has the real author)
    if restructured != base:
        minimal_r = _minimal_query(restructured)
        if minimal_r and minimal_r not in queries:
            queries.append(minimal_r)

    return queries


MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0  # seconds


def _execute_query(query, email, timeout=30):
    """Execute a single Crossref API query with adaptive backoff on 429."""
    if not query or len(query) < 10:
        return []

    params = {
        'query.bibliographic': query,
        'rows': 5,
        'select': 'DOI,score,title,author,published-print,container-title,type',
        'mailto': email,
    }

    headers = {
        'User-Agent': f'ExistentialAnalysisBackfill/1.0 (mailto:{email})',
    }

    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                CROSSREF_API_URL,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code == 429:
                import time
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            data = resp.json()
            return data.get('message', {}).get('items', [])
        except (requests.RequestException, ValueError) as e:
            if attempt < MAX_RETRIES - 1 and '429' in str(e):
                import time
                time.sleep(backoff)
                backoff *= 2
                continue
            print(f"  WARNING: Crossref query failed: {e}", file=sys.stderr)
            return []
    print(f"  WARNING: Crossref query exhausted retries for: {query[:60]}",
          file=sys.stderr)
    return []


def query_crossref(ref_text, email, timeout=30):
    """Query Crossref with multiple query variants and return all candidates.

    Sends all query variants concurrently (within Crossref's 10 req/sec
    rate limit), then deduplicates results by DOI.

    Args:
        ref_text: Full reference text (e.g. "Author (Year). Title. Publisher.")
        email: Contact email for Crossref polite pool
        timeout: Request timeout in seconds

    Returns:
        List of result dicts with keys: DOI, score, title, author,
        published-print, container-title, type. Empty list on error.
    """
    queries = _build_queries(ref_text)

    # Send all query variants concurrently for speed (~0.4s latency per
    # call, rate limit is 10/s, so 2-5 concurrent requests are fine)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        futures = [pool.submit(_execute_query, q, email, timeout)
                   for q in queries]
        all_query_results = [f.result() for f in futures]

    seen_dois = set()
    all_results = []
    for results in all_query_results:
        for r in results:
            doi = r.get('DOI', '')
            if doi not in seen_dois:
                seen_dois.add(doi)
                all_results.append(r)

    return all_results


def _normalise_title(text):
    """Lowercase, normalize ampersands, strip punctuation/whitespace."""
    text = text.lower()
    text = text.replace('&', 'and')
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _title_similarity(crossref_title, ref_text):
    """Compute a title similarity score (0.0 to 1.0).

    Checks whether the Crossref title appears within the reference text.
    Penalises very short titles (1-2 words) which match trivially.
    """
    cr = _normalise_title(crossref_title)
    ref = _normalise_title(ref_text)

    if not cr or not ref:
        return 0.0

    cr_words = cr.split()
    ref_words = set(ref.split())
    n_cr_words = len(cr_words)

    if n_cr_words == 0:
        return 0.0

    # Full containment as substring (strong signal for 2+ word titles)
    if n_cr_words >= 2 and cr in ref:
        return 1.0

    # Word overlap ratio
    overlap = len(set(cr_words) & ref_words)
    raw_sim = overlap / n_cr_words

    # Penalise single-word titles: "Heidegger" or "Introduction" match
    # too easily. 2-word titles like "Cartesian Meditations" are distinctive
    # enough (and must still pass author match + type check).
    if n_cr_words <= 1:
        raw_sim *= SINGLE_WORD_TITLE_PENALTY

    return min(raw_sim, 1.0)


def _fold(text):
    """Lowercase and strip diacritics, so "Rédei" and "Redei" compare equal."""
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).lower()


# Matches split4_normalize_authors' fuzzy threshold for the same job.
_AUTHOR_SIMILARITY = 0.85
_AUTHOR_FUZZY_MIN_LEN = 5
_WORD_RE = re.compile(r'[^\W\d_]+', re.UNICODE)
# Same particle list pipe6_ojs_xml uses when splitting author names.
_NAME_PARTICLES = {'van', 'de', 'du', 'von', 'di', 'la', 'le', 'el', 'den', 'der'}


def _check_author_match(crossref_authors, ref_text):
    """Check if any Crossref author's family name appears in the reference text.

    This catches cases where Crossref returns a review of a book rather than
    the book itself — the review author won't match the reference author.

    Accents are folded, and a near-miss on a long surname still counts. A plain
    substring test rejected perfect matches against our own archive: some of
    this journal's metadata was deposited with mangled author names, so
    Crossref holds "Koèiûnas" where the reference correctly says "Kočiūnas".
    Folding leaves those one character apart ("koeiunas" / "kociunas"), which
    is a near-miss rather than a match, and a paper with title similarity 1.0
    in the right journal was being scored no_match on the strength of it.
    """
    if not crossref_authors:
        return False

    ref_folded = _fold(ref_text)
    ref_words = None  # built lazily; most matches never need it

    for author in crossref_authors:
        family = author.get('family', '')
        if not family or len(family) < 3:
            continue
        folded = _fold(family)
        candidates = [folded]
        # References often invert the particle: Crossref has "van Deurzen"
        # where the reference reads "Deurzen, E. van". Try the bare surname.
        head, _, rest = folded.partition(' ')
        if rest and head in _NAME_PARTICLES:
            candidates.append(rest)
        if any(c in ref_folded for c in candidates):
            return True
        folded = candidates[-1]
        if len(folded) < _AUTHOR_FUZZY_MIN_LEN:
            continue
        if ref_words is None:
            ref_words = [w for w in _WORD_RE.findall(ref_folded)
                         if len(w) >= _AUTHOR_FUZZY_MIN_LEN]
        for word in ref_words:
            if SequenceMatcher(None, folded, word).ratio() >= _AUTHOR_SIMILARITY:
                return True
    return False


# Patterns that indicate a reference is citing a book (not a journal article)
_BOOK_PUBLISHER_RE = re.compile(
    r'\b(?:Press|Publisher|Books?|Verlag|Éditions?|Gallimard|Grasset|Vintage'
    r'|Routledge|Sage|Springer|Wiley|Blackwell|Penguin|Harper|Random House'
    r'|Oxford University|Cambridge University|Yale University'
    r'|Princeton University|Harvard University)\b',
    re.IGNORECASE,
)
_JOURNAL_SIGNAL_RE = re.compile(
    r'\b(?:Journal|Review|Quarterly|Bulletin|Annals|Studies|Analysis)\b.*\d+\s*\(\d+\)',
    re.IGNORECASE,
)
# Matches volume(issue) patterns like "15(2)" or "16, 2" at the end of a ref
_VOL_ISSUE_RE = re.compile(r'\d+\s*[\(,]\s*\d+\s*\)?')


def _ref_has_in_pattern(ref_text):
    """Check if reference cites a chapter within a book (has 'In Author' pattern)."""
    return bool(re.search(r'\.\s+In\s+[A-ZÀ-Ý]', ref_text))


def _is_type_mismatch(ref_text, cr_type):
    """Detect when Crossref returned a wrong type of work.

    Catches false positives: reviews, chapters from different books,
    and encyclopedia/dictionary entries about an author.
    """
    # These types are almost never the cited work:
    # - dataset: APA PsycINFO records *about* a book, not the book itself
    # - component: sub-parts of other works (figures, supplementary data)
    # - reference-entry: encyclopedia/dictionary entries, usually *about* an
    #   author rather than the cited work. Could legitimately match if someone
    #   cites a dictionary entry, but those are rare and would need the ref
    #   to explicitly mention the dictionary/encyclopedia.
    if cr_type in ('dataset', 'component', 'reference-entry'):
        return True

    # If the reference looks like a journal article...
    if _JOURNAL_SIGNAL_RE.search(ref_text):
        # ...matched to journal-article is fine (no mismatch)
        if cr_type == 'journal-article':
            return False
        # ...matched to book-chapter = likely a same-author chapter on a
        # similar topic, not the cited journal article
        if cr_type == 'book-chapter':
            return True

    # If the reference has book publisher keywords, it's citing a book
    ref_is_book = bool(_BOOK_PUBLISHER_RE.search(ref_text))
    if not ref_is_book:
        return False

    # Book reference matched to journal-article = likely a review
    if cr_type == 'journal-article':
        return True

    # Book reference (without "In" pattern = standalone book) matched to
    # book-chapter = likely a chapter from a different book
    if cr_type == 'book-chapter' and not _ref_has_in_pattern(ref_text):
        return True

    return False


def _is_container_mismatch(cr_container, ref_text, crossref_score):
    """Detect when the Crossref container doesn't match the reference.

    Returns True (= reject) when all of:
    - Crossref has a container name (journal/book series)
    - The container has significant words (not just "Vol" etc.)
    - None of those words appear in the reference text
    - The Crossref score is low (< 50) — high-score matches are more
      likely correct even if container doesn't appear verbatim

    This catches false positives like "Letter to the editor. The Guardian"
    matching to "Biologicals", or "The Mirror and the Hammer" matching to
    something in "The Political Psyche".
    """
    if not cr_container or crossref_score >= 50:
        return False

    # Never reject matches from our own journal (JSEA abbreviation
    # doesn't match "Existential Analysis" container name)
    if 'existential analysis' in cr_container.lower():
        return False

    # Extract significant words from the Crossref container
    stop_words = {'the', 'and', 'for', 'vol', 'new', 'int', 'its'}
    container_words = [
        w.lower() for w in re.findall(r'[A-Za-z]{4,}', cr_container)
        if w.lower() not in stop_words
    ]
    if not container_words:
        return False

    # Check if any container word appears in the reference text
    ref_lower = ref_text.lower()
    # Also check common abbreviations of our journal
    if any(abbr in ref_lower for abbr in ('jsea', 'j.s.e.a', 'existential analysis')):
        if 'existential' in cr_container.lower():
            return False

    return not any(w in ref_lower for w in container_words)


# Container titles that indicate a reference work entry, not the cited work
_REFERENCE_WORK_RE = re.compile(
    r'\b(?:Dictionary|Companion|Encyclopedia|Encyclopaedia|Handbook)\b',
    re.IGNORECASE,
)


# Detects references citing our own journal
OWN_JOURNAL_RE = re.compile(r'\bExistential\s+Analysis\b', re.IGNORECASE)


def score_match(result, ref_text):
    """Score a single Crossref result against the original reference text.

    Args:
        result: A Crossref result dict (from query_crossref)
        ref_text: The original reference text

    Returns:
        Tuple of (tier, similarity, details_dict) where tier is one of
        TIER_MATCHED, TIER_NO_MATCH.
    """
    crossref_score = result.get('score', 0)
    doi = result.get('DOI', '')

    # Extract title from Crossref result
    titles = result.get('title', [])
    crossref_title = titles[0] if titles else ''

    similarity = _title_similarity(crossref_title, ref_text) if crossref_title else 0.0

    # Extract container title (journal/book name)
    containers = result.get('container-title', [])
    container = containers[0] if containers else ''

    # Extract author info
    authors = result.get('author', [])
    author_str = '; '.join(
        f"{a.get('family', '')}, {a.get('given', '')}"
        for a in authors[:3]
    ) if authors else ''

    # Check if the reference author appears in the Crossref result's authors.
    # If Crossref has no authors (common for books), treat as neutral (True).
    author_match = _check_author_match(authors, ref_text) if authors else True
    cr_type = result.get('type', '')

    details = {
        'matched_doi': doi,
        'crossref_score': crossref_score,
        'crossref_title': crossref_title,
        'crossref_container': container,
        'crossref_authors': author_str,
        'crossref_type': cr_type,
        'title_similarity': round(similarity, 3),
        'author_match': author_match,
    }

    # Check if the Crossref result is from a reference work (dictionary,
    # companion, encyclopedia) that the reference doesn't cite. These are
    # entries *about* the cited work/author, not the cited work itself.
    if container and _REFERENCE_WORK_RE.search(container):
        if not _REFERENCE_WORK_RE.search(ref_text):
            details['type_mismatch'] = True
            return TIER_NO_MATCH, similarity, details

    # Detect type mismatch: reference looks like a book but Crossref returned
    # a journal-article (likely a review of the book, not the book itself)
    type_mismatch = _is_type_mismatch(ref_text, cr_type)
    details['type_mismatch'] = type_mismatch

    # Detect container mismatch: if the reference mentions a specific
    # journal/publisher and the Crossref result is from a completely
    # different one, this is likely a false positive (e.g. "Letter to the
    # editor. The Guardian" matching a biology journal letter).
    container_mismatch = _is_container_mismatch(
        container, ref_text, crossref_score)
    details['container_mismatch'] = container_mismatch

    # Tier assignment: matched or no_match.
    # Type or container mismatch = wrong work.
    if type_mismatch or container_mismatch:
        tier = TIER_NO_MATCH
    # High similarity + author match is the strongest signal.
    # Very high similarity (1.0 = exact title containment) can accept lower
    # Crossref scores — the title match itself is strong evidence.
    elif similarity >= 1.0 and author_match and crossref_score >= MIN_SCORE_EXACT_TITLE:
        tier = TIER_MATCHED
    elif similarity >= 0.8 and author_match and crossref_score >= MIN_SCORE_HIGH_SIM:
        tier = TIER_MATCHED
    elif similarity >= 0.7 and author_match and crossref_score >= MIN_SCORE_MED_SIM:
        tier = TIER_MATCHED
    else:
        tier = TIER_NO_MATCH

    # Self-citation preference: when the reference cites our journal
    # and the Crossref result is from our journal, flag it.
    # Used as tiebreaker in candidate ranking (prefer our journal's DOI)
    # and to boost NO_MATCH → MATCHED when thresholds are borderline.
    ref_cites_own_journal = bool(OWN_JOURNAL_RE.search(ref_text))
    result_is_own_journal = bool(
        container and OWN_JOURNAL_RE.search(container)
    )
    if ref_cites_own_journal and result_is_own_journal and not type_mismatch:
        if similarity >= 0.7 and author_match:
            details['self_citation_boost'] = True
            if tier == TIER_NO_MATCH:
                tier = TIER_MATCHED

    return tier, similarity, details
