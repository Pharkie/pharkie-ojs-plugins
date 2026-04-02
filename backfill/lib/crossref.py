"""Crossref API client for matching references to DOIs.

Queries the Crossref works API with bibliographic text and scores results
for confidence-based DOI matching.
"""

import re
import sys

import requests

# Journal's own DOI prefix — skip these when checking for existing DOIs
OWN_DOI_PREFIX = '10.65828/'

# Regex to detect DOIs already present in reference text
DOI_RE = re.compile(r'10\.\d{4,}/\S+')

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


def has_existing_doi(ref_text):
    """Check if a reference already contains a DOI (excluding our own prefix).

    Returns the DOI string if found, or None.
    """
    for match in DOI_RE.finditer(ref_text):
        doi = match.group()
        # Strip trailing punctuation that's not part of the DOI
        doi = doi.rstrip('.,;:)')
        if not doi.startswith(OWN_DOI_PREFIX):
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


def _execute_query(query, email, timeout=30):
    """Execute a single Crossref API query."""
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

    try:
        resp = requests.get(
            CROSSREF_API_URL,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('message', {}).get('items', [])
    except (requests.RequestException, ValueError) as e:
        print(f"  WARNING: Crossref query failed: {e}", file=sys.stderr)
        return []


def query_crossref(ref_text, email, timeout=30):
    """Query Crossref with multiple query variants and return all candidates.

    Tries the original reference text first, then progressively cleaned
    variants. Each variant's results are added only if they bring new DOIs.
    Returns the combined, deduplicated results.

    Args:
        ref_text: Full reference text (e.g. "Author (Year). Title. Publisher.")
        email: Contact email for Crossref polite pool
        timeout: Request timeout in seconds

    Returns:
        List of result dicts with keys: DOI, score, title, author,
        published-print, container-title, type. Empty list on error.
    """
    queries = _build_queries(ref_text)

    seen_dois = set()
    all_results = []
    for query in queries:
        results = _execute_query(query, email, timeout)
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


def _check_author_match(crossref_authors, ref_text):
    """Check if any Crossref author's family name appears in the reference text.

    This catches cases where Crossref returns a review of a book rather than
    the book itself — the review author won't match the reference author.
    """
    if not crossref_authors:
        return False

    ref_lower = ref_text.lower()
    for author in crossref_authors:
        family = author.get('family', '').lower()
        if family and len(family) >= 3 and family in ref_lower:
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


# Container titles that indicate a reference work entry, not the cited work
_REFERENCE_WORK_RE = re.compile(
    r'\b(?:Dictionary|Companion|Encyclopedia|Encyclopaedia|Handbook)\b',
    re.IGNORECASE,
)


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

    # Tier assignment: matched or no_match.
    # Type mismatch = wrong work type (e.g. journal review of a book).
    if type_mismatch:
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

    return tier, similarity, details
