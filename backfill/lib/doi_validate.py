"""Guards against malformed DOIs and DOI links that break when read as text.

Two failure modes have reached the live journal and surfaced in Crossref's
monthly resolution report:

1. **A malformed DOI stored against a reference.** Greedy extraction swallowed a
   closing bracket, the next reference's prefix, or an OCR look-alike, and the
   bad value was written to JATS and deposited. Live examples: four Proust
   references carrying ``10.15697/10.5072/fk20p1509b`` (two DOIs run together),
   and ``10.2466/17.04.PR0.113×17z0`` (U+00D7 for "x").

2. **A well-formed DOI link whose markup runs into the following text.** The
   ``href`` is correct, so no check on the DOI *value* can see it, but there is
   no whitespace between ``</a>`` and the next word — so anything reading the
   page as text (a crawler, a copy/paste, a PDF converter) requests the DOI with
   that word stuck on the end. This is what put ``10.65828/C8ECRH72LINKS`` and
   ``10.65828/CJF3PR2310.65828`` into the failed-resolution report, and it was
   live in 37.2 as ``10.65828/nnfvfq11Kočiūnas,``.

Both checks run in the pipeline (pipe6, before ``import.xml`` is written) and
stand alone as a pre-publication check — see ``main()``.
"""

import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.lib.crossref import (  # noqa: E402
    OWN_DOI_PREFIX,
    clean_doi,
    is_valid_own_doi,
    suggest_own_doi,
)

# A DOI link in generated galley HTML.
_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href=["\']https?://(?:dx\.)?doi\.org/(?P<doi>[^"\']+)["\'][^>]*>'
    r'(?P<label>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

# Tags sitting between a closing </a> and the next run of text.
_INTERVENING_TAGS_RE = re.compile(r'(?:<[^>]*>)*')

# A doi.org address written into reference prose.
_DOI_URL_RE = re.compile(r'https?://(?:dx\.)?doi\.org/(\S+)', re.IGNORECASE)


def doi_problem(doi):
    """Return a human-readable reason `doi` is unusable, or None if it is fine.

    Checks shape for any DOI, and additionally checks the suffix pattern for our
    own prefix — where a run-on is otherwise undetectable, because the result is
    still a well-formed DOI that simply does not exist.
    """
    if not doi or not doi.strip():
        return 'empty'
    raw = doi.strip()
    cleaned = clean_doi(raw)
    # Our own prefix gets the stricter suffix rule first, so the message names
    # the actual fault rather than reporting it as generically unusable.
    if raw.lower().startswith(OWN_DOI_PREFIX) and not is_valid_own_doi(raw):
        guess = cleaned or suggest_own_doi(raw)
        hint = f', probably {guess!r}' if guess else ''
        return f'our prefix but not a suffix we mint (run-on?){hint}'
    if cleaned is None:
        return 'not a usable DOI'
    # Anything cleaning had to change was carrying its surroundings: a bracket,
    # a trailing separator, an OCR look-alike, or a second DOI run onto it.
    if cleaned != raw:
        return f'malformed, should be {cleaned!r}'
    return None


def bad_dois_in_jats(jats_path):
    """Find unusable <pub-id pub-id-type="doi"> values in one JATS file.

    Returns a list of (doi, reason, reference_text) tuples.
    """
    try:
        tree = ET.parse(jats_path)
    except ET.ParseError as exc:
        return [('', f'unparseable JATS: {exc}', '')]

    problems = []
    for ref in tree.iter():
        if not ref.tag.endswith('ref'):
            continue
        for pub_id in ref.iter():
            if not pub_id.tag.endswith('pub-id'):
                continue
            if pub_id.get('pub-id-type') != 'doi':
                continue
            doi = (pub_id.text or '').strip()
            reason = doi_problem(doi)
            if reason:
                text = ' '.join(''.join(ref.itertext()).split())[:120]
                problems.append((doi, reason, text))

        # A reference can also carry a doi.org address in its prose. OJS renders
        # that as a link, so a broken one reaches readers even though no pub-id
        # is wrong. Only flag addresses that are not DOIs at all — a DOI written
        # mid-sentence legitimately ends in a full stop, and flagging those would
        # bury the real faults.
        ref_text = ' '.join(''.join(ref.itertext()).split())
        for candidate in _DOI_URL_RE.findall(ref_text):
            if clean_doi(candidate) is None:
                problems.append((
                    candidate,
                    'reference text links doi.org to something that is not a DOI',
                    ref_text[:120],
                ))
    return problems


def runon_doi_links(html):
    """Find DOI links that run into the following text with no whitespace.

    Returns a list of (doi, what_a_text_extractor_would_append) tuples. The href
    itself is correct in every case — the defect is the absence of whitespace
    after ``</a>``, which only shows up once the markup is flattened to text.
    """
    problems = []
    for match in _ANCHOR_RE.finditer(html):
        rest = html[match.end():]
        # Skip tags that carry no text of their own; they do not separate words.
        tags = _INTERVENING_TAGS_RE.match(rest)
        following = rest[tags.end():] if tags else rest
        if not following:
            continue
        # A block-level tag does introduce a visual break, but text extractors
        # routinely drop it, so it is not a safe separator. Whitespace is.
        if following[0].isspace():
            continue
        run_on = re.match(r'[^\s<]+', following)
        if run_on:
            problems.append((match.group('doi'), run_on.group()))
    return problems


def bad_doi_links(html):
    """Find doi.org links whose DOI is unusable, whatever the markup around it.

    Distinct from runon_doi_links(): here the href itself is wrong. Live example
    in 35.2, where a reference carries a plain web address behind the DOI
    resolver — ``https://doi.org/https://www.journal-psychoanalysis.eu/...``.
    """
    problems = []
    for match in _ANCHOR_RE.finditer(html):
        doi = match.group('doi')
        reason = doi_problem(doi)
        if reason:
            problems.append((doi, reason))
    return problems


def check_article(jats_path=None, galley_html_path=None):
    """Run both guards for one article. Returns a list of message strings."""
    messages = []
    if jats_path and os.path.exists(jats_path):
        for doi, reason, text in bad_dois_in_jats(jats_path):
            messages.append(
                f'{os.path.basename(jats_path)}: reference DOI {doi!r} — {reason}'
                + (f'\n      ref: {text}' if text else '')
            )
    if galley_html_path and os.path.exists(galley_html_path):
        with open(galley_html_path, encoding='utf-8') as handle:
            html = handle.read()
        for doi, reason in bad_doi_links(html):
            messages.append(
                f'{os.path.basename(galley_html_path)}: DOI link {doi!r} — {reason}'
            )
        for doi, run_on in runon_doi_links(html):
            messages.append(
                f'{os.path.basename(galley_html_path)}: DOI link {doi!r} runs into '
                f'the next text — a reader copying it gets "{doi}{run_on}". '
                f'Add whitespace after the closing </a>.'
            )
    return messages


def check_volume_dir(vol_dir):
    """Run both guards over every article in an output volume directory."""
    messages = []
    for jats_path in sorted(glob.glob(os.path.join(vol_dir, '*.jats.xml'))):
        stem = jats_path[: -len('.jats.xml')]
        messages += check_article(jats_path, stem + '.galley.html')
    return messages


def main():
    parser = argparse.ArgumentParser(
        description='Check generated articles for malformed DOIs and DOI links '
                    'that break when the page is read as text.')
    parser.add_argument('paths', nargs='+',
                        help='volume output directories, or individual .jats.xml '
                             'or .galley.html files')
    args = parser.parse_args()

    messages = []
    for path in args.paths:
        if os.path.isdir(path):
            messages += check_volume_dir(path)
        elif path.endswith('.jats.xml'):
            stem = path[: -len('.jats.xml')]
            messages += check_article(path, stem + '.galley.html')
        else:
            messages += check_article(None, path)

    if messages:
        print(f'DOI check FAILED — {len(messages)} problem(s):\n', file=sys.stderr)
        for message in messages:
            print(f'  - {message}', file=sys.stderr)
        return 1
    print('DOI check passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
