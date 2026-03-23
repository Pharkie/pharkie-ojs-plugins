#!/usr/bin/env python3
"""Split toc.json citations into 'references' and 'notes'.

Reads the flat 'citations' array from each article in toc.json and splits
into two fields based on deterministic classification rules:

  - references: full bibliographic citations (Crossref-compatible)
  - notes: everything else (display-only)

Usage:
    python3 backfill/split_citation_tiers.py --dry-run   # preview
    python3 backfill/split_citation_tiers.py              # write to toc.json
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"


# ---------------------------------------------------------------
# Notes rules (checked first — if ANY match, item is a Note)
# ---------------------------------------------------------------

def is_note(text: str) -> str | None:
    """Return a reason string if this item is a Note, else None.

    Rules checked in order — first match wins.
    """
    # Rule 1: Cross-reference ("See X", "cf. X")
    if re.match(r'^(See |see |cf\.\s|Cf\.\s)', text):
        return 'see-crossref'

    # Rule 2: Ibid / Ibidem / Op cit as main content
    stripped = re.sub(r'^\d+[\.\)\s]+', '', text).strip()
    if len(text) < 80 and re.search(r'\b(Ibid\.?|Ibidem|Op\.?\s*cit)', text, re.IGNORECASE):
        return 'ibid'

    # Rule 3: Short surname+year only (no title/publisher/journal)
    stripped_num = re.sub(r'^\d+[\.\)\s]+', '', text).strip()
    if _is_short_surname_year(stripped_num):
        return 'short-ref'

    # Rule 4: Numbered commentary (starts with digit, no citation markers, prose-like)
    if re.match(r'^\d+[\.\)\s]', text):
        after_num = re.sub(r'^\d+[\.\)\s]+', '', text).strip()
        if _is_numbered_commentary(after_num):
            return 'numbered-commentary'

    # Rule 5: Author bio
    if _is_author_bio(text):
        return 'author-bio'

    # Rule 6: Article provenance note
    if re.match(r'^This (article|paper|chapter|essay|lecture|talk)\s+(is|was)\s', text):
        return 'provenance'

    # Rule 7: Standalone URL (no author/title, just a link)
    if re.match(r'^https?://', text.strip()) or re.match(r'^\d+\s+https?://', text.strip()):
        return 'url-only'

    # Rule 8: Contact info / author name only
    if re.match(r'^Contact:', text) or re.match(r'^https?://orcid\.org/', text):
        return 'contact-info'

    # Rule 9: Just a person's name (no citation content)
    name_only = text.strip().rstrip('.')
    if re.match(r'^[A-Z][a-zà-ž]+\s+[A-Z][a-zà-ž-]+$', name_only) and len(name_only) < 40:
        return 'name-only'

    return None


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
        return False  # has citation markers, keep as potential reference

    sentence_count = len(re.findall(r'[.!?]\s+[A-Z]', after_num))
    if sentence_count >= 2 or len(after_num) > 200:
        return True

    return False


def _is_author_bio(text: str) -> bool:
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

    bio_patterns = [
        r'^[A-Z][A-Z\s\.\-]+\b(is|was|has)\s',  # ALL CAPS with hyphens
        r'^[A-Z]+\s+(?:van|de|von)\s+[A-Z\-]+\s+(is|was|has)\s',  # "EMMY van DEURZEN-SMITH is"
        r'^[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]+(\s+[A-Z][a-zà-ü]+)?\s+(is|was|has)\s',
        r'^All (three|four|five|six) authors',
        r'^(Dr\.?|Professor)\s+[A-Z][a-z]',
        r'^[A-Z][a-zà-ü]+\s+[A-Z][a-zà-ü]+\s+(PhD|MA|MSc|UKCP|BPS)',
    ]

    if any(re.match(p, text) for p in bio_patterns):
        return True

    if has_bio_phrase and re.match(r'^[A-Z][a-zà-ü]+\s', text) and len(text) > 50:
        if not re.search(r'\(\d{4}\)', text[:80]):
            return True

    return False


# ---------------------------------------------------------------
# References rules (must pass ALL to be a Reference)
# ---------------------------------------------------------------

def is_reference(text: str) -> bool:
    """Check if item is a proper bibliographic reference.

    Must have: author pattern + year. Title is a bonus but not required —
    Crossref can match from author + year alone via unstructured_citation.
    """
    # Strip leading number prefix if present (including stuck-to-word: "1Indeed" → "Indeed")
    clean = re.sub(r'^\d+[\.\)\s]*', '', text).strip()

    # Rule 1: Author name at or near start
    has_author = bool(re.match(
        # Standard: "Surname, X" — covers diacritics, apostrophes, hyphens
        # Prefixes: van, de, von, du, le, la, al-, ben-, St., D', O', Mc, Mac, Di, Du, Le, De
        r"^(?:van\s+(?:den?\s+)?|de\s+|von\s+|du\s+|le\s+|la\s+|al-|ben-|St\.\s+)?"
        r"(?:Mc|Mac|Di|Du|Le|De|O'|D')?"
        r"[A-ZÀ-Ž\u0400-\u04FF]"
        r"[a-zà-ž\u0400-\u04FF'ğışçöüřžščůķīūė]+"
        r"(?:[–—-][A-ZÀ-Ž][a-zà-ž'ğışçöüřžščůķīūė]+)?"  # hyphen/dash in surname
        r"[,\.\s]+",
        clean
    )) or bool(re.match(
        # First name first: "Paul de Man," or "Martin Heidegger,"
        r'^[A-Z][a-zà-ž]+\s+(?:de |van |von )?[A-Z][a-zà-ž]+(?:-[A-Z][a-zà-ž]+)?\s*[,\.\(]',
        clean
    )) or bool(re.match(
        r'^[A-Z][A-Z\s]+,', clean  # ALL CAPS surname
    )) or bool(re.match(
        # Initial + surname: "M. Heidegger" or "S. Mulhall"
        r'^[A-Z]\.?\s+[A-Z][a-zà-ž]+', clean
    )) or bool(re.match(
        # Dash/bullet continuation: "- (1987a) Title..." or "• (1992b) Title..."
        r'^[-–—•]\s+\(?\d{4}', clean
    )) or bool(re.match(
        # Institutional/acronym authors: "BBC", "PSA", "WHO", "BACP", "NHS", "NSPCC"
        r'^[A-Z]{2,6}[\.\s]', clean
    )) or bool(re.match(
        # Single-name/classical authors
        r'^(Plato|Anonymous|Aristotle|Homer|Euripides|Sophocles|Heraclitus|Parmenides|Shakespeare|Machiavelli)\b',
        clean
    )) or bool(re.match(
        # Parenthetical prefix: "(Freud GW)" "(ed. Name)"
        r'^\([A-Z]', clean
    )) or bool(re.match(
        # Cyrillic: starts with Cyrillic capital
        r'^[\u0400-\u04FF]', clean
    )) or bool(re.match(
        # Quoted title start: '"Title"' or "'Title'"
        r'^["\'][A-Z]', clean
    )) or bool(re.match(
        # Lowercase deliberate author: "hooks, b." "bell hooks"
        r'^hooks,', clean
    )) or bool(re.match(
        # "Surname, Title" pattern (no first name): "Nietzsche, The Birth of Tragedy"
        r'^[A-Z][a-zà-ž]+,\s+[A-Z][a-z]+\s+[A-Z]', clean
    )) or bool(re.match(
        # Mid-capital surnames: "VeneKlasen", "LeBon", "LaBreck", "DuBois"
        r'^[A-Z][a-z]+[A-Z][a-z]+', clean
    )) or bool(re.match(
        # "From:" prefix
        r'^From:', clean
    )) or bool(re.match(
        # Superscript numeral prefix: "¹²", "⁴", "iii", "iv", "vi"
        r'^[¹²³⁴⁵⁶⁷⁸⁹⁰ⁱⁱⁱ]+\s', clean
    )) or bool(re.match(
        # Roman numeral prefix: "i ", "ii ", "iii ", "iv ", "xii "
        r'^[ivxlc]+\s', clean, re.IGNORECASE
    ))

    if not has_author:
        return False

    # Rule 2: Contains a year (1800-2029)
    # Handle: year with letter suffix (1977a), "1 973" (space), "l996" (lowercase L), "forthcoming"
    has_year = bool(re.search(r'\b(1[89]\d{2}|20[0-2]\d)[a-d]?\b', clean))
    has_year_fuzzy = has_year or bool(re.search(r'\b1\s?\d{3}\b', clean))
    has_year_fuzzy = has_year_fuzzy or bool(re.search(r'\b[lI]\d{3}\b', clean))
    has_year_fuzzy = has_year_fuzzy or bool(re.search(r'\b(forthcoming|in press|n\.d\.?|undated)\b', clean, re.IGNORECASE))
    # Year in parens with any format: "(1977a)" "(1962/1977)" "(forthcoming)"
    has_year_fuzzy = has_year_fuzzy or bool(re.search(r'\(\d{4}', clean))

    if not has_year_fuzzy:
        # No year at all — could still be a reference if it has other strong signals
        # e.g. "Aquinas, Thomas. Summa Theologiae" or "Laing, R.D. Interpersonal Perception. London: Tavistock."
        has_publisher = bool(re.search(
            r'(Press|Publisher|Books|Routledge|Sage|Springer|Penguin|Wiley|'
            r'Tavistock|Macmillan|Methuen|OUP|Blackwell|Faber|Harper)',
            clean, re.IGNORECASE))
        has_place = bool(re.search(
            r'(London|New York|Cambridge|Oxford|Paris|Berlin|Edinburgh|Boston|Chicago)',
            clean, re.IGNORECASE))
        if not (has_publisher or has_place):
            return False

    # Author + year matched — but is this a bibliographic entry or prose commentary?
    # Key difference: commentary has multiple (Author, Year) citations embedded in prose.
    # A real reference has ONE author+year at the start, then title+publisher.

    # Count how many distinct (Author, Year) or Author (Year) patterns appear
    cite_refs = len(re.findall(r'[A-Z][a-z]+\s*[\(,]\s*\d{4}', clean))
    # Also count semicolon-separated year clusters: "Laing, 1960; May, 1992; Heidegger, 2010"
    semicolon_refs = len(re.findall(r';\s*[A-Z][a-z]+', clean))

    # Multiple embedded citations in one item = commentary note
    if cite_refs >= 3:
        return False
    if (cite_refs + semicolon_refs) >= 4:
        return False

    # Length gate: over 300 chars must have clear structured start
    if len(clean) > 300:
        if not re.match(r'^[A-Z][a-zà-ž]+,\s+[A-Z]\..*?\(\d{4}\)', clean[:60]):
            return False

    # MUST have title-like text: at least 3 words that aren't just
    # author names, years, page numbers, or place/publisher names.
    # Strip the author+year part and check what remains.
    # Remove: leading author (up to year), year in parens, page refs
    remainder = clean

    # Strip leading number prefix
    remainder = re.sub(r'^\d+[\.\)\s]*', '', remainder).strip()
    # Strip author name (Surname, I. or Surname Initial.)
    remainder = re.sub(r'^(?:van\s+(?:den?\s+)?|de\s+|von\s+|du\s+)?'
                       r"(?:Mc|Mac|Di|Du|O'|D')?"
                       r'[A-ZÀ-Ž][a-zà-ž\']+(?:[–—-][A-Z][a-zà-ž]+)?'
                       r'[,\.\s]+(?:[A-Z]\.?\s*(?:and\s+[A-Z]\.?\s*)?)?', '', remainder, count=1).strip()
    # Strip year (with optional brackets, letters)
    remainder = re.sub(r'\(?\d{4}[a-d]?(?:\s*\[\d{4}[a-d]?\])?\)?[,\.\s:;]*', '', remainder).strip()
    # Strip page references
    remainder = re.sub(r'^(?:pp?\.?\s*)?\d+[-–]?\d*[,\.\s]*', '', remainder).strip()
    # Strip "n.d." "forthcoming"
    remainder = re.sub(r'^(?:n\.d\.?|forthcoming|in press)[,\.\s]*', '', remainder, flags=re.IGNORECASE).strip()

    # Count meaningful words remaining (not just punctuation, numbers, or very short fragments)
    title_words = re.findall(r'[A-Za-zÀ-žà-ž\u0400-\u04FF]{3,}', remainder)

    if len(title_words) < 3:
        return False

    return True

    return False


# ---------------------------------------------------------------
# Anystyle validation
# ---------------------------------------------------------------

def _has_anystyle() -> bool:
    """Check if anystyle CLI is available."""
    return shutil.which('anystyle') is not None


def anystyle_validate(texts: list[str]) -> list[bool]:
    """Batch-validate references via anystyle CRF parser.

    Returns a list of booleans — True if anystyle extracted author + title
    (i.e. it's a real bibliographic reference), False otherwise.

    Processes all texts in one batch for speed.
    """
    if not texts:
        return []

    # Write one reference per line to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                     encoding='utf-8') as f:
        for text in texts:
            # Strip leading number prefix — anystyle handles raw reference text
            clean = re.sub(r'^\d+[\.\)\s]*', '', text).strip()
            # Replace newlines (anystyle expects one per line)
            f.write(clean.replace('\n', ' ') + '\n')
        tmppath = f.name

    try:
        result = subprocess.run(
            ['anystyle', '--stdout', '-f', 'json', 'parse', tmppath],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"  anystyle error: {result.stderr[:200]}", file=sys.stderr)
            return [True] * len(texts)  # fail open — keep as references

        parsed = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"  anystyle failed: {e}", file=sys.stderr)
        return [True] * len(texts)  # fail open
    finally:
        os.unlink(tmppath)

    # Validate each parsed result
    results = []
    for i, entry in enumerate(parsed):
        has_author = bool(entry.get('author'))
        has_title = bool(entry.get('title') or entry.get('container-title'))
        results.append(has_author and has_title)

    # Pad if anystyle returned fewer results than expected
    while len(results) < len(texts):
        results.append(True)  # fail open

    return results


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def classify(text: str) -> str:
    """Classify a citation item as 'reference' or 'note'."""
    # Check Notes rules first
    note_reason = is_note(text)
    if note_reason:
        return 'note'

    # Check References rules
    if is_reference(text):
        return 'reference'

    # Falls through both → Note by default
    return 'note'


def main():
    dry_run = '--dry-run' in sys.argv
    verbose = '--verbose' in sys.argv
    skip_anystyle = '--no-anystyle' in sys.argv

    use_anystyle = not skip_anystyle and _has_anystyle()
    if not skip_anystyle and not _has_anystyle():
        print("WARNING: anystyle not found — skipping validation. Install: gem install anystyle anystyle-cli",
              file=sys.stderr)

    toc_files = sorted(OUTPUT_DIR.glob('*/toc.json'))

    def vol_sort_key(path):
        name = path.parent.name
        parts = name.split('.')
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 1)
        except ValueError:
            return (999, 999)

    toc_files = sorted(toc_files, key=vol_sort_key)

    stats = Counter()
    note_reasons = Counter()
    borderline = []  # Items that fell through to note by default

    # Phase 1: Deterministic classification
    # Collect all candidate references for batch anystyle validation
    all_candidate_refs = []  # (toc_path_idx, art_idx, citation_idx, text)
    toc_data = []  # loaded toc data for each file

    for toc_idx, toc_path in enumerate(toc_files):
        with open(toc_path) as f:
            toc = json.load(f)
        toc_data.append((toc_path, toc))

        vol = toc.get('volume', 0)
        iss = toc.get('issue', 0)

        for art_idx, art in enumerate(toc.get('articles', [])):
            citations = art.get('citations', [])
            if not citations:
                continue

            references = []
            notes = []

            for cit_idx, text in enumerate(citations):
                note_reason = is_note(text)
                if note_reason:
                    notes.append(text)
                    note_reasons[note_reason] += 1
                    stats['notes'] += 1
                elif is_reference(text):
                    references.append(text)
                    all_candidate_refs.append((toc_idx, art_idx, cit_idx, text))
                    stats['references'] += 1
                else:
                    notes.append(text)
                    stats['notes_default'] += 1
                    if len(borderline) < 50:
                        borderline.append({
                            'vol': f'{vol}.{iss}',
                            'text': text[:120],
                        })

            # Store preliminary classification
            art['_refs_preliminary'] = references
            art['_notes_preliminary'] = notes

    # Phase 2: Anystyle validation of candidate references
    demoted = 0
    demoted_examples = []
    if use_anystyle and all_candidate_refs:
        print(f"Validating {len(all_candidate_refs)} references with anystyle...")
        ref_texts = [r[3] for r in all_candidate_refs]
        valid = anystyle_validate(ref_texts)

        # Build a set of (toc_idx, text) pairs that failed validation
        failed_set = set()
        for i, (toc_idx, art_idx, cit_idx, text) in enumerate(all_candidate_refs):
            if not valid[i]:
                failed_set.add((toc_idx, art_idx, text))

        # Reclassify: move failed references to notes
        for toc_idx, (toc_path, toc) in enumerate(toc_data):
            vol = toc.get('volume', 0)
            iss = toc.get('issue', 0)
            for art_idx, art in enumerate(toc.get('articles', [])):
                refs = art.get('_refs_preliminary', [])
                notes = art.get('_notes_preliminary', [])
                if not refs:
                    continue

                new_refs = []
                for text in refs:
                    if (toc_idx, art_idx, text) in failed_set:
                        notes.append(text)
                        demoted += 1
                        stats['references'] -= 1
                        stats['notes_anystyle'] += 1
                        if len(demoted_examples) < 30:
                            demoted_examples.append({
                                'vol': f'{vol}.{iss}',
                                'text': text[:120],
                            })
                    else:
                        new_refs.append(text)

                art['_refs_preliminary'] = new_refs
                art['_notes_preliminary'] = notes

    # Phase 3: Write results
    for toc_idx, (toc_path, toc) in enumerate(toc_data):
        vol = toc.get('volume', 0)
        iss = toc.get('issue', 0)
        modified = False

        for art in toc.get('articles', []):
            refs = art.pop('_refs_preliminary', [])
            notes = art.pop('_notes_preliminary', [])
            if not art.get('citations'):
                continue

            if not dry_run:
                art['references'] = refs
                art['notes'] = notes
                modified = True

            if verbose:
                title = art.get('title', '')[:40]
                print(f"  Vol {vol}.{iss} | {title} | {len(refs)} refs, {len(notes)} notes")

        if modified and not dry_run:
            with open(toc_path, 'w') as f:
                json.dump(toc, f, indent=2, ensure_ascii=False)
                f.write('\n')

    # Summary
    total = stats['references'] + stats['notes'] + stats['notes_default'] + stats.get('notes_anystyle', 0)
    print("=" * 60)
    print("CITATION TIER SPLIT" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)
    print(f"Total items:          {total}")
    print(f"→ References:         {stats['references']}")
    print(f"→ Notes (by rule):    {stats['notes']}")
    print(f"→ Notes (by default): {stats['notes_default']}")
    if use_anystyle:
        print(f"→ Notes (anystyle):   {stats.get('notes_anystyle', 0)}")
    elif not skip_anystyle:
        print(f"  (anystyle not available — install ruby + gem install anystyle anystyle-cli)")
    print()
    print("Note reasons:")
    for reason, count in note_reasons.most_common():
        print(f"  {reason:25s} {count:5d}")

    if demoted_examples:
        print(f"\nDemoted by anystyle ({demoted} items — no author+title found):")
        for d in demoted_examples[:20]:
            print(f"  Vol {d['vol']} | {d['text']}")

    if borderline:
        print(f"\nFell through to Notes by default ({stats['notes_default']} items):")
        for b in borderline[:20]:
            print(f"  Vol {b['vol']} | {b['text']}")

    if dry_run:
        print(f"\nDRY RUN — no files modified.")
    else:
        print(f"\nDone — wrote 'references' and 'notes' to toc.json.")


if __name__ == '__main__':
    main()
