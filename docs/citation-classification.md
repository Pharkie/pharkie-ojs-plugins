# Citation classification rules

How extracted items from article reference sections are classified into four categories. Each item goes into exactly one category, which determines where it appears in JATS and how it's rendered in OJS.

## Categories

| Category | JATS element | OJS rendering | Count |
|---|---|---|---|
| **Reference** | `<ref-list><ref><mixed-citation>` | OJS citations table → "References" section (rendered by OJS natively) | 14,874 |
| **Note** | `<fn-group><fn>` | HTML galley body → "Notes" section (rendered inline) | 2,123 |
| **Author bio** | `<bio>` | HTML galley body → paragraph at end | 37 |
| **Provenance** | `<notes notes-type="provenance">` | HTML galley body → italicised paragraph at end | 4 |

## Classification order

```
Item extracted from reference section
  │
  ├─ is_author_bio()?  → author bio
  ├─ is_provenance()?  → provenance
  │
  │  Then for remaining items:
  │
  ├─ is_note()?        → note    (reject: definitely not a reference)
  ├─ is_reference()?   → reference (confirm: has author + year + title)
  └─ neither?          → note    (default: not confident enough to call it a reference)
```

The note check runs before the reference check as a **negative filter** — it catches items that look superficially like references (have a year, have a name) but are actually commentary, cross-references, or fragments. Without this filter, prose like "7. Perls studied theater with Max Reinhardt..." would incorrectly pass `is_reference()` because it has an author-like pattern.

## Author bio rules (`is_author_bio`)

Matches if ANY of these patterns appear:

- ALL CAPS name + "is/was/has" → `CHARLES SCOTT is Professor of...`
- Mixed case name + "is/was/has" → `Stephen Diamond is a clinical...`
- Starts with "Dr." or "Professor" + name
- Name + credentials (PhD, MA, MSc, UKCP, BPS)
- "All three/four/five authors..."
- Bio phrases ("private practice", "working in", "academic interests", etc.) in first 200 chars of a name-like start, without a year in parentheses near the start

## Provenance rules (`is_provenance`)

Matches: `^This (article|paper|chapter|essay|lecture|talk) (is|was) ...`

## Note rules (`is_note`) — checked first as negative filter

12 rules, checked in order. First match wins → classified as note.

| # | Rule | Example | Regex/logic |
|---|---|---|---|
| 1 | Cross-reference | "See also Chapter 3" | `^(See\|see\|cf\.\|Cf\.)` |
| 2 | Ibid/Op cit | "Ibid., p.300" | Short (<80 chars) + `\b(Ibid\|Ibidem\|Op\.?\s*cit)` |
| 3 | Short surname+year only | "Heidegger 1927: 45" | Surname + year + optional page, nothing else |
| 4 | Numbered commentary | "8 Freud maintains that..." | Starts with digit, text after number doesn't start with author pattern (prose with embedded citations) |
| 5 | Roman numeral commentary | "xii Tourette reportedly..." | Starts with roman numeral (i, ii, ..., xvi), text is commentary not citation |
| 6 | Superscript commentary | "¹ Readers interested..." | Starts with superscript digit, text is commentary not citation |
| 7 | Author bio | "SCOTT is Professor..." | Same as `is_author_bio()` above |
| 8 | Provenance | "This paper was first..." | Same as `is_provenance()` above |
| 9 | Standalone URL | "https://example.com" | `^https?://` with no author/title |
| 10 | Contact info | "Contact: email@..." | `^Contact:`, `^Contact address`, `^Messrs`, or ORCID URL |
| 11 | Name only | "Simon du Plock" | Single name (<40 chars), no citation content |

**Rule 4 detail** (most complex): A numbered item like "4 Binswanger, L. (1968)..." is a legitimate reference (number prefix + author pattern). But "8 Freud maintains that this view..." is commentary (number prefix + prose). The test: after stripping the number, does the text start with an author pattern (Surname, Initial)?

## Reference rules (`is_reference`) — checked second as positive match

ALL of these must pass:

### Rule 1: Author pattern at or near start

Must match one of ~15 author patterns:

- Standard: `Surname, X.` with diacritics, apostrophes, hyphens
- Prefixes: van, de, von, du, le, la, al-, ben-, St., D', O', Mc, Mac
- First-name-first: `Paul de Man,`
- ALL CAPS: `HEIDEGGER,`
- Initial + surname: `M. Heidegger`
- Continuation: `— (1987a) Title...`
- Institutional: `BBC`, `WHO`, `BACP` (2-6 uppercase letters)
- Classical: Plato, Aristotle, Homer, etc.
- Cyrillic: starts with Cyrillic capital
- Quoted title start: `"Title"`
- Mid-capital: VeneKlasen, LeBon, DuBois

### Rule 2: Contains a year

- Standard: 1800–2029 with optional letter suffix (1977a)
- Fuzzy: spaced year (1 973), OCR errors (l996), "forthcoming", "in press", "n.d."
- **Exception**: no year required if text has a publisher name or place of publication (e.g. "Aquinas, Thomas. Summa Theologiae. London: Routledge.")

### Rule 3: Not multiple embedded citations

- Reject if 3+ Author(Year) patterns found (commentary citing multiple works)
- Reject if 4+ combined Author(Year) + semicolon-separated refs

### Rule 4: Has title content

After stripping author + year + page refs, at least 3 meaningful words must remain. Prevents bare "Smith, 1992" from being classified as a reference.

### Rule 5: Length gate

Over 300 characters must have a clear structured start (Surname, I. (Year)) — long items without this are likely prose.

## Implementation

All classification logic lives in `backfill/lib/citations.py`. The functions are:

- `classify(text)` → `'reference'` or `'note'`
- `is_note(text)` → reason string or `None`
- `is_reference(text)` → `True`/`False`
- `is_author_bio(text)` → `True`/`False`
- `is_provenance(text)` → `True`/`False`

These are used by:
- `extract_citations.py` — classifies items during extraction from JATS body
