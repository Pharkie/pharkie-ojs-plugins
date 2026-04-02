# Crossref Reference Linking

How we match extracted references against Crossref DOIs to enrich citation metadata.

## Background

[Crossref Reference Linking](https://www.crossref.org/services/reference-linking/) enables publishers to include DOIs in reference lists, creating persistent links between citing and cited works. This is an obligation of Crossref membership — members should link references within 18 months of joining.

Our journal's references are stored as plain text `<mixed-citation>` elements in JATS XML. The `pipe4b_match_dois.py` script queries the Crossref API to find DOIs for cited works and writes them as `<pub-id pub-id-type="doi">` siblings in JATS.

## How it works

### API

We use the Crossref REST API (`https://api.crossref.org/works`) with the `query.bibliographic` parameter, which accepts free-form bibliographic text and returns scored results.

**Polite pool:** Including a `mailto:` email in the User-Agent header or as a query parameter gives access to faster rate limits. We use `User-Agent: ExistentialAnalysisBackfill/1.0 (mailto:EMAIL)`.

**Rate limiting:** We self-limit to 10 req/sec (100ms delay), well under Crossref's limits. With ~15k references needing lookup, the full corpus takes ~25 minutes.

### Multi-query strategy

A single reference text often confuses Crossref's search due to translator credits, edition notes, and parenthetical asides. We query with up to 4 variants and pick the best result across all:

1. **Original text** — the full `<mixed-citation>` content
2. **Cleaned** — strips `[bracketed years]`, `(eds.)`, `Trans. Name, I.` noise
3. **Restructured** — for editor-led "In Author. Book." references, extracts the main work (only when the reference explicitly leads with `(eds.)` or `(trans.)`)
4. **Minimal** — just `"Surname Title"` with no publisher/year/translator context

Example: `"Heidegger, M. (2000 [1953]). Introduction to Metaphysics. (Lecture delivered 1935). Trans. Fried, G. & Polt, R. New Haven: Yale University Press."` produces:
- Query 1: full text (Crossref can't find it — too noisy)
- Query 2: `Heidegger, M. (2000). Introduction to Metaphysics. New Haven: Yale University Press.` (still too noisy)
- Query 3: `Heidegger Introduction to Metaphysics` (finds the correct DOI as result #1)

### Scoring and tiers

Each Crossref candidate is scored on three signals:

| Signal | What it checks | Why it matters |
|--------|---------------|----------------|
| **Title similarity** | Does the Crossref title appear within the reference text? | Primary correctness signal |
| **Author match** | Does any Crossref author surname appear in the reference? | Catches reviews (reviewer ≠ cited author) |
| **Type mismatch** | Is a book reference matched to a `journal-article` or unrelated `book-chapter`? | Catches journal reviews of books |

Results are either **`matched`** or **`no_match`**:

- **`matched`** — high confidence, written to JATS. Requires title similarity ≥ 0.7 + author match + reasonable Crossref score, with no type mismatch.
- **`no_match`** — everything else: type mismatches, author mismatches, low scores, or nothing returned.

When multiple candidates exist, we pick matched over no_match, then by title similarity, then by Crossref score.

### Candidate selection across multi-query results

All candidates from all query variants are deduplicated by DOI and scored against the original reference text. This means a candidate from the minimal query can win if it scores better than candidates from the original query.

## Lessons learned

### What Crossref is good at

- **Books with DOIs from academic publishers** (Routledge, Cambridge UP, Yale UP, Springer) — high match rates, reliable scores.
- **Book chapters** that have their own DOIs — the chapter title is usually distinctive enough.
- **Journal articles** — strong match when the reference includes journal name + volume/issue.

### What Crossref struggles with

- **Noisy reference text** — translator credits, edition notes, lecture dates, and parenthetical asides confuse the search. The multi-query strategy mitigates this.
- **Editor-led references** — `"Fried, G. (eds.) (2000). Translators' intro. In Heidegger, M. Introduction to Metaphysics."` matches on editor names rather than the book. Restructuring helps.
- **Trade publisher books** — Vintage, Gallimard, Grasset, Penguin etc. rarely register DOIs. These are correct no-matches.
- **Book reviews vs books** — Crossref often returns a journal-article DOI for a *review* of a book rather than the book itself. The type mismatch check catches this.

### Edition policy

A DOI to a **different edition** of the same work is acceptable. The reference text already specifies which edition was cited; the DOI helps the reader find the work online. We only reject DOIs that point to a genuinely different work.

### False positive patterns

| Pattern | Example | How we catch it |
|---------|---------|-----------------|
| Journal review of a book | Crossref returns a review titled "Tête-à-Tête" for Rowley's book | Type mismatch: ref has publisher keyword + Crossref type is `journal-article` |
| Book chapter from a different book | Crossref returns "SIMONE DE BEAUVOIR AND JEAN-PAUL SARTRE" (a chapter) for the book "Tête-à-Tête" | Type mismatch: ref is standalone book (no "In" pattern) + Crossref type is `book-chapter` |
| Short title coincidence | Crossref title "Heidegger" (1 word) matches because surname appears in ref | Short title penalty: 1-2 word titles get similarity halved |

## Usage

```bash
# Phase 1: single article, verbose
python3 backfill/html_pipeline/pipe4b_match_dois.py \
  --volume 35.1 --article 02-who-do-we-think-we-are \
  --verbose --email user@example.com

# Full issue
python3 backfill/html_pipeline/pipe4b_match_dois.py \
  --volume 35.1 --verbose --email user@example.com

# All volumes
for dir in backfill/private/output/*/; do
  python3 backfill/html_pipeline/pipe4b_match_dois.py \
    --volume "$(basename "$dir")" --email user@example.com
done

# Write matched DOIs to JATS XML
python3 backfill/html_pipeline/pipe4b_match_dois.py \
  --volume 35.1 --write-jats --email user@example.com
```

## JATS format

Before:
```xml
<ref id="ref1">
  <mixed-citation>Barnett, L. (2009). When Death Enters the Therapeutic Space. London: Routledge.</mixed-citation>
</ref>
```

After:
```xml
<ref id="ref1">
  <mixed-citation>Barnett, L. (2009). When Death Enters the Therapeutic Space. London: Routledge.</mixed-citation>
  <pub-id pub-id-type="doi">10.4324/9780203891285</pub-id>
</ref>
```

The `<pub-id>` is a sibling of `<mixed-citation>`, not a child. This preserves the plain-text citation and is valid JATS 1.3. The downstream `pipe6_ojs_xml.py` only reads `mixed-citation.text`, so the new element is ignored.

## Results cache

Results are written to `doi_matches.json` in each volume directory (alongside `toc.json`). On rerun, already-matched refs are skipped. The file records the tier, DOI, Crossref score, title similarity, and whether the DOI has been written to JATS.

## Testing

- **Unit tests** (`backfill/tests/test_crossref.py`): scoring logic, DOI detection, type mismatch, author matching — all mocked, no API calls.
- **Live API tests** (`backfill/tests/test_crossref_live.py`): 9 references from a real article, asserting correct tier for each. These hit the real Crossref API.

## Crossref matching approaches

There are three ways to match references to DOIs via Crossref:

| Approach | Endpoint | Best for | Batch size |
|----------|----------|----------|------------|
| **REST API** | `api.crossref.org/works?query.bibliographic=` | Scripted, per-reference queries with rich result metadata | 1 per request |
| **XML API** | `doi.crossref.org/servlet/query` with `<unstructured_citation>` | Batch XML queries with structured or unstructured citations | Up to 5 per request |
| **Simple Text Query** | `apps.crossref.org/SimpleTextQuery` (web form) | Bulk matching of plain-text reference lists | 1,000 per submission |

We currently use the **REST API** because it returns rich metadata (score, title, author, type) needed for our multi-signal scoring. The XML API's `<unstructured_citation>` element is purpose-built for free-text reference matching and may give better results for difficult cases — worth investigating if match rates plateau.

The Simple Text Query is designed for exactly our use case (matching reference lists to DOIs) but appears to be a web form, not an API endpoint, so isn't easily scriptable.

## OJS plugin alternative

[pkp/crossrefReferenceLinking](https://github.com/pkp/crossrefReferenceLinking) is an OJS plugin (3.1.2+, Plugin Gallery) that automatically deposits article references with Crossref during DOI registration, then polls back for matched DOIs. This could handle matching and depositing without a custom script. Worth evaluating whether it handles our back-catalogue volume or only works for new submissions.

## Crossref's own matching algorithm (SBMV)

Crossref's production matching uses Search-Based Matching with Validation (SBMV), documented in two blog posts:
- [Matchmaker, matchmaker, make me a match](https://www.crossref.org/blog/matchmaker-matchmaker-make-me-a-match/) (Nov 2018) — algorithm design
- [Reference matching: for real this time](https://www.crossref.org/blog/reference-matching-for-real-this-time/) (Dec 2018) — benchmark results

**How SBMV works:**
1. Send the raw reference string to `query.bibliographic`
2. Apply a normalized score threshold (score / reference string length)
3. For each candidate, validate by checking whether year, volume, issue, pages, and first author surname from the candidate metadata appear in the original reference string
4. Return the candidate with highest validation similarity above threshold

**Benchmark:** 98.09% precision, 94.56% recall, 96.3% F1 on 2,000 unstructured reference strings. Our approach is similar but uses title containment + author + type checking rather than volume/issue/pages (which our references don't always have).

## Comparable projects

- **[OpenAPC doi-reverse-lookup](https://openapc.github.io/general/openapc/2018/01/29/doi-reverse-lookup/)** — Python script querying `query.bibliographic`, validates with Levenshtein ratio (>0.9 auto-accept, 0.8–0.9 manual review, <0.8 discard). Matched 2,400+ DOIs from 2,970 articles. Closest to our approach.
- **[sagepublishing/CrossRef_RAT](https://github.com/sagepublishing/CrossRef_RAT)** — Sage's internal tool for searching Crossref by paper title.
- **[blcc/title2doi](https://github.com/blcc/title2doi)** — Translates article title + author to DOI via Crossref.

## Depositing references back to Crossref

Once DOIs are matched, references should be deposited with Crossref to build the citation graph (enabling Cited-by backlinks via [RBoelter/citations](https://github.com/RBoelter/citations) plugin).

**Important:** Depositing new references **overwrites** any previously deposited references for that DOI. You must include all references (existing + new) in each deposit.

Options:
- **OJS Crossref plugin** — deposits references automatically during DOI registration
- **pkp/crossrefReferenceLinking** — dedicated reference deposit + matching
- **XML deposit** — manual XML submission to Crossref
- **[Register your references](https://www.crossref.org/community/special-programs/register-references/)** — Crossref special program for back-catalogue reference linking, no additional cost

## Hybrid approach (planned)

Rather than relying on either system alone, we use both for accuracy and accountability:

1. **Our script matches first** (`pipe4b_match_dois.py`) — full visibility into scores, titles, match/no_match decisions. Iterative review to tune heuristics before scaling.
2. **Deposit references to Crossref** — via OJS Crossref plugin during DOI registration. Required anyway for Cited-by backlinks. References are deposited as `<unstructured_citation>` elements in the DOI registration XML (can include our pre-matched DOI alongside the text).
3. **Poll `getResolvedRefs`** — authenticated endpoint (`doi.crossref.org/getResolvedRefs?doi=ARTICLE_DOI&usr=...&pwd=...`) returns DOIs that Crossref's SBMV matched internally.
4. **Reconcile** — compare SBMV results against our own:
   - Both agree → high confidence
   - SBMV found a DOI we missed → review and potentially add
   - We found a DOI that SBMV missed → keep ours (SBMV recall is ~95%, not 100%)
   - Neither found a DOI → likely no DOI exists

This gives two independent matchers plus full audit trail. Every decision is logged in `doi_matches.json` with both our score and Crossref's result.

**Important:** `getResolvedRefs` requires Crossref member credentials (configured in OJS admin > Crossref plugin settings). References must be deposited first — the endpoint only returns matches for references Crossref already has.

## References

- [Crossref Reference Linking](https://www.crossref.org/services/reference-linking/)
- [Crossref Cited-by](https://www.crossref.org/documentation/cited-by/)
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [API usage tips](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/)
