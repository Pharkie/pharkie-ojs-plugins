#!/usr/bin/env python3
"""
Build the smarter_similar_articles cache via offline hybrid TF-IDF + embedding similarity.

Replaces the stock recommendBySimilarity plugin's corpus-wide live query with
a pre-computed cache read by plugins/smarter-similar-articles at render time.

Scoring per pair = TFIDF_WEIGHT × cosine(tfidf) + EMBED_WEIGHT × cosine(embedding).
  - TF-IDF (sklearn): surfaces articles that share distinctive terminology.
    Strong on specific-string matches (hyphenated proper nouns, rare keywords).
  - Sentence embeddings (sentence-transformers, all-MiniLM-L6-v2): surfaces
    semantic neighbours, including cases where two articles discuss the same
    concept using different vocabulary.
  - Blending at 0.4 / 0.6 gives embedding the majority share while keeping
    TF-IDF's precision on proper nouns from being washed out entirely.

For each published submission:
  - Compute a TF-IDF similarity matrix (keywords×3 + title×3 + abstract)
  - Compute an embedding similarity matrix (natural text: title + keywords + abstract)
  - Blend into one matrix by weighted sum
  - Pick top 5 neighbours with the section rule applied (Book Reviews → Book
    Reviews only) and score band [MIN_SCORE, MAX_SCORE)
  - Write into smarter_similar_articles in one bulk transaction

Usage
-----
  python3 scripts/ojs/build_smarter_similar_articles.py                   # dev, full rebuild
  python3 scripts/ojs/build_smarter_similar_articles.py --target=live     # live, full rebuild
  python3 scripts/ojs/build_smarter_similar_articles.py --submission=9795 # just that article
  python3 scripts/ojs/build_smarter_similar_articles.py --affected-by=9795
      # recompute every article whose current cache points at 9795 (for use
      # after 9795 is republished with edits)
  python3 scripts/ojs/build_smarter_similar_articles.py --dry-run         # no writes

Dependencies: scikit-learn, pymysql, beautifulsoup4, sentence-transformers.
First run downloads the BAAI/bge-base-en-v1.5 model (~440 MB) into HuggingFace's
cache dir (~/.cache/huggingface). Subsequent runs hit the cache.

Background: docs/ojs-issues-log.md #26, docs/smarter-similar-articles-plugin.md,
plugins/smarter-similar-articles/.
"""

import argparse
import json
import subprocess
import sys
import time

import numpy as np
from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# Weighting: keyword list repeated N times in the text blob, so TF-IDF treats
# keyword tokens as having N*TF. Matches the intent of "editor-curated keywords
# are higher-signal than body text" without changing sklearn behaviour.
KEYWORD_WEIGHT = 3
# Title is repeated N times in the text blob alongside keywords. Titles carry
# strong topical signal for papers about specific people/concepts ("Beauvoir",
# "Sartre", "Heidegger") — without this weight, a paper whose keywords include
# 8 generic terms like "freedom" and "ethics" gets dominated by those common
# matches and its distinctive proper noun gets buried. TITLE_WEIGHT=3 was
# tuned empirically against dev: pushes the 4 Beauvoir articles in the corpus
# up the rankings for a Beauvoir paper without regressing other cases.
TITLE_WEIGHT = 3
MAX_RESULTS = 5

# Cosine-similarity thresholds used when picking neighbours (applied to the
# final hybrid score).
# MIN_SCORE: filter out weak matches. Below this, the match is noise rather
#   than a genuine topical neighbour. Tuned against the bge-base hybrid score
#   distribution (rank-1 avg ~0.62, rank-5 avg ~0.52, minimum useful threshold
#   ~0.40). At 0.40 well-clustered articles keep all 5 neighbours; articles
#   whose "best" match is already weak (editorials, tribute pieces, outliers)
#   get no sidebar rather than filler. If you switch to a different embedding
#   model, recalibrate — MiniLM-L6-v2 scores run lower and would want ~0.30.
# MAX_SCORE: filter out near-duplicate submissions. Score >= this (~1.0) means
#   identical content in both TF-IDF and embedding spaces — a duplicate import.
MIN_SCORE = 0.40
MAX_SCORE = 0.95

# Hybrid blend weights. TFIDF_WEIGHT + EMBED_WEIGHT should sum to 1.
# Chosen 0.4 / 0.6 after corpus-wide evaluation: embeddings legitimately beat
# TF-IDF on philosopher-cluster recall (Kierkegaard 73% vs 41%, Heidegger 58%
# vs 45%) but TF-IDF holds ground on specific-string matches (Merleau-Ponty
# 59% vs 33%). At 0.4 / 0.6 the blend tracks embeddings when they cluster
# well, falls back to TF-IDF anchoring on the proper-noun cases.
TFIDF_WEIGHT = 0.4
EMBED_WEIGHT = 0.6

# sentence-transformers model. bge-base-en-v1.5 is 110M params, ~440 MB
# download, 768-dim embeddings, encodes 1400 short docs in ~2 min on CPU.
# Chosen over MiniLM-L6-v2 after corpus-wide evaluation: bge-base wins
# notably on philosopher clusters (Heidegger 72% vs 58%, Laing 76% vs 62%,
# Merleau-Ponty 50% vs 33%) and qualitatively on user-flagged cases
# (9805 Beauvoir paper: 5/5 Beauvoir articles in top 5 vs 4/5 with MiniLM).
# Aggregate recall is a wash (51% vs 52%) but per-case quality is better.
# MiniLM is still the right choice for larger corpora (~10k+) where the
# inference time cost matters; for ~1400 articles bge-base is practically free.
EMBED_MODEL = 'BAAI/bge-base-en-v1.5'

# Section abbrevs whose articles are restricted to same-section recommendations.
# Current value: Book Reviews only (BR). Book Review Editorial (bookeditorial)
# is an editorial section and sees broad recommendations like other editorials.
# Change this set if the journal adds more review-type sections.
RESTRICTED_SECTION_ABBREVS = frozenset({'BR'})

# Bulk INSERT chunk size — stays well under default MySQL max_allowed_packet
# (64MB). 1400 articles * 5 rows = 7000 inserts; one chunk comfortably.
INSERT_CHUNK = 1000

TARGETS = {
    'dev': [
        'docker', 'compose', 'exec', '-T', 'ojs-db',
        'bash', '-c',
        'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N --raw',
    ],
    'live': [
        'ssh', 'sea-live',
        'cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs-db '
        "bash -c 'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N --raw'",
    ],
}


class SqlError(Exception):
    pass


def run_sql(target: str, sql: str, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(
            TARGETS[target],
            input=sql,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise SqlError(
            f'SQL call timed out after {timeout}s on target={target}. '
            f'Likely causes: SSH connection stalled, DB overloaded, huge result set. '
            f'SQL first 200 chars: {sql[:200]!r}'
        ) from e
    except FileNotFoundError as e:
        # docker/ssh binary missing or wrong path — surface clearly
        raise SqlError(f'Failed to invoke target {target}: {e}') from e
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or '<no stderr>'
        raise SqlError(f'SQL failed (exit {proc.returncode}): {stderr}')
    return proc.stdout


def fetch_submissions(target: str) -> list[dict]:
    """One row per published submission, with keywords joined in Python.

    Two separate queries — keyword subquery needs all three index columns
    (symbolic, assoc_type, assoc_id) to be fast, and joining from the
    submissions side can't satisfy that because we don't know assoc_type
    statically. Easier to fetch both sides in bulk and merge.
    """
    submissions_sql = r'''
SELECT JSON_OBJECT(
    'submission_id',  s.submission_id,
    'publication_id', s.current_publication_id,
    'title',          REGEXP_REPLACE(IFNULL(ps_title.setting_value, ''), '[[:space:]]+', ' '),
    'abstract',       REGEXP_REPLACE(IFNULL(ps_abstract.setting_value, ''), '[[:space:]]+', ' '),
    'section_title',  IFNULL(sec_title.setting_value, ''),
    'section_abbrev', IFNULL(sec_abbrev.setting_value, '')
)
FROM submissions s
JOIN publications p ON p.publication_id = s.current_publication_id
JOIN sections sec ON sec.section_id = p.section_id
LEFT JOIN publication_settings ps_title
    ON ps_title.publication_id = p.publication_id
    AND ps_title.setting_name = 'title'
    AND ps_title.locale = 'en'
LEFT JOIN publication_settings ps_abstract
    ON ps_abstract.publication_id = p.publication_id
    AND ps_abstract.setting_name = 'abstract'
    AND ps_abstract.locale = 'en'
LEFT JOIN section_settings sec_title
    ON sec_title.section_id = sec.section_id
    AND sec_title.setting_name = 'title'
    AND sec_title.locale = 'en'
LEFT JOIN section_settings sec_abbrev
    ON sec_abbrev.section_id = sec.section_id
    AND sec_abbrev.setting_name = 'abbrev'
    AND sec_abbrev.locale = 'en'
WHERE s.status = 3 AND s.current_publication_id IS NOT NULL
ORDER BY s.submission_id;
'''
    keywords_sql = r'''
SELECT cv.assoc_id, cves.setting_value
FROM controlled_vocabs cv
JOIN controlled_vocab_entries cve ON cve.controlled_vocab_id = cv.controlled_vocab_id
JOIN controlled_vocab_entry_settings cves ON cves.controlled_vocab_entry_id = cve.controlled_vocab_entry_id
WHERE cv.symbolic = 'submissionKeyword'
  AND cves.setting_name = 'name';
'''
    submissions_out = run_sql(target, submissions_sql)
    keywords_out = run_sql(target, keywords_sql)

    # Build keywords lookup: publication_id -> [keyword, ...]
    keywords_by_pub: dict[int, list[str]] = {}
    for line in keywords_out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t', 1)
        if len(parts) != 2:
            continue
        try:
            pub_id = int(parts[0])
        except ValueError:
            continue
        kw = parts[1].strip()
        if kw:
            keywords_by_pub.setdefault(pub_id, []).append(kw)

    subs: list[dict] = []
    for line in submissions_out.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            print(f'  Skipping unparseable row: {e}', file=sys.stderr)
            continue
        row['keywords'] = keywords_by_pub.get(row['publication_id'], [])
        subs.append(row)
    return subs


def strip_html(text: str) -> str:
    if not text or not text.strip():
        return ''
    return BeautifulSoup(text, 'html.parser').get_text(' ', strip=True)


def build_corpus_text(sub: dict) -> str:
    """TF-IDF input: keywords ×3, title ×3, abstract once.

    The weighting only matters for TF-IDF — TF-IDF treats term frequency as
    a linear signal, so repetition amplifies. Don't use this for embeddings:
    transformer encoders understand phrase importance natively and repetition
    there just dilutes with redundant tokens.
    """
    keyword_part = (' '.join(sub['keywords']) + ' ') * KEYWORD_WEIGHT if sub['keywords'] else ''
    title = (sub.get('title', '') or '') + ' '
    title_part = title * TITLE_WEIGHT
    abstract = strip_html(sub.get('abstract', '') or '')
    return ' '.join(p for p in (keyword_part, title_part, abstract) if p.strip()).strip()


def build_embed_text(sub: dict) -> str:
    """Embedding input: natural text, one copy. No artificial weighting."""
    title = sub.get('title') or ''
    keywords = ', '.join(sub['keywords']) if sub['keywords'] else ''
    abstract = strip_html(sub.get('abstract') or '')
    parts = [p for p in (title, keywords, abstract) if p.strip()]
    return '\n\n'.join(parts)


def is_review(sub: dict) -> bool:
    return (sub.get('section_abbrev') or '').strip() in RESTRICTED_SECTION_ABBREVS


def compute_tfidf_similarity(subs: list[dict]) -> np.ndarray:
    texts = [build_corpus_text(s) for s in subs]
    # Guard against a corpus where every text ends up empty after stopword /
    # min_df / max_df pruning — TfidfVectorizer would raise 'empty vocabulary'
    # and abort the whole build. Realistically this only happens in tiny
    # test corpora, but fail with a clear message rather than a cryptic trace.
    non_empty = sum(1 for t in texts if t.strip())
    if non_empty < 2:
        raise SystemExit(
            f'ERROR: TF-IDF corpus has only {non_empty} non-empty documents '
            f'out of {len(texts)} submissions. Check that title/abstract/'
            f'keywords are populated on your published submissions.'
        )
    vectorizer = TfidfVectorizer(
        stop_words='english',
        min_df=2,
        max_df=0.5,
        ngram_range=(1, 2),
    )
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError as e:
        # Usually "After pruning, no terms remain" — corpus too narrow
        # for the min_df/max_df config. Relax or fix upstream.
        raise SystemExit(
            f'ERROR: TF-IDF vectorizer rejected the corpus: {e}\n'
            f'Likely causes: corpus too small for min_df=2, or every token '
            f'appears in >50% of documents (max_df=0.5 filter removes all).'
        ) from e
    return cosine_similarity(matrix)


def compute_embed_similarity(subs: list[dict]) -> np.ndarray:
    """Normalised-embedding cosine similarity. Loads the model lazily.

    Raises SystemExit(1) with a clear message on model download / load failure
    rather than letting a bare exception propagate through main() and leaving
    operators guessing whether the DB connection was at fault.
    """
    # Import inside function so --help and unit tests don't need the model.
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(EMBED_MODEL)
    except Exception as e:
        raise SystemExit(
            f'ERROR: Failed to load embedding model {EMBED_MODEL!r}: {e}\n'
            f'Causes: no network access to HuggingFace, disk full, '
            f'~/.cache/huggingface permissions, model name typo, '
            f'or sentence-transformers version mismatch.'
        ) from e
    texts = [build_embed_text(s) for s in subs]
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
    )
    return embeddings @ embeddings.T   # cosine because normalised


def compute_hybrid_similarity(subs: list[dict]) -> np.ndarray:
    """Weighted blend of TF-IDF and embedding similarities."""
    t0 = time.time()
    tfidf_sims = compute_tfidf_similarity(subs)
    print(f'  TF-IDF similarity: {tfidf_sims.shape} ({time.time() - t0:.1f}s)', flush=True)

    t0 = time.time()
    embed_sims = compute_embed_similarity(subs)
    print(f'  Embedding similarity: {embed_sims.shape} ({time.time() - t0:.1f}s)', flush=True)

    return TFIDF_WEIGHT * tfidf_sims + EMBED_WEIGHT * embed_sims


def pick_neighbours(sims: np.ndarray, subs: list[dict], src_idx: int) -> list[tuple[int, float]]:
    row = sims[src_idx].copy()
    row[src_idx] = -1  # self-exclude
    if is_review(subs[src_idx]):
        # Book-review articles only recommend other reviews
        for i, s in enumerate(subs):
            if not is_review(s):
                row[i] = -1
    # Apply score band: drop duplicates (>= MAX_SCORE) and weak matches (< MIN_SCORE)
    row[row >= MAX_SCORE] = -1
    top_idx = np.argsort(-row)[:MAX_RESULTS]
    return [
        (subs[i]['submission_id'], float(row[i]))
        for i in top_idx
        if row[i] >= MIN_SCORE
    ]


def write_bulk(target: str, neighbours_by_id: dict[int, list[tuple[int, float]]], full: bool, dry_run: bool) -> None:
    if dry_run:
        return
    if not neighbours_by_id:
        return

    # DELETE then bulk INSERT inside a single transaction. Intentionally NOT
    # using TRUNCATE even for full rebuild — TRUNCATE implicitly commits in
    # MySQL/MariaDB, breaking the transaction so an INSERT failure after it
    # would leave the table empty with no rollback. DELETE respects the
    # transaction boundary; the whole write is all-or-nothing.
    if full:
        delete_sql = 'DELETE FROM smarter_similar_articles;'
    else:
        ids = ','.join(str(int(sid)) for sid in neighbours_by_id)
        delete_sql = f'DELETE FROM smarter_similar_articles WHERE submission_id IN ({ids});'

    # Defence-in-depth: coerce to int before string interpolation. Python's
    # json.loads already produces ints for JSON numbers, but if an upstream
    # change ever routes a non-int (bug, mocked test, corrupt data), this
    # prevents the string from landing in SQL unsanitised.
    rows: list[str] = []
    for sid, neighbours in neighbours_by_id.items():
        sid_i = int(sid)
        for rank, (sim_id, score) in enumerate(neighbours, start=1):
            rows.append(f'({sid_i}, {int(sim_id)}, {rank}, {float(score):.4f})')

    insert_chunks = []
    for i in range(0, len(rows), INSERT_CHUNK):
        chunk = rows[i:i + INSERT_CHUNK]
        insert_chunks.append(
            'INSERT INTO smarter_similar_articles (submission_id, similar_id, rank, score) VALUES\n'
            + ',\n'.join(chunk) + ';'
        )

    sql = 'START TRANSACTION;\n' + delete_sql + '\n' + '\n'.join(insert_chunks) + '\nCOMMIT;\n'
    run_sql(target, sql)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', choices=list(TARGETS), default='dev')
    ap.add_argument('--submission', type=int,
                    help='Recompute only this submission.')
    ap.add_argument('--affected-by', type=int,
                    help='Also recompute every submission whose current cache points at this submission. '
                         'Use after --submission when republishing an article.')
    ap.add_argument('--dry-run', action='store_true', help='Compute but do not write.')
    args = ap.parse_args()

    print(f'Target: {args.target}', flush=True)

    t0 = time.time()
    subs = fetch_submissions(args.target)
    print(f'  Fetched {len(subs)} published submissions ({time.time() - t0:.1f}s)', flush=True)

    if len(subs) < 2:
        print('ERROR: Not enough submissions to compute similarity.', file=sys.stderr)
        return 1

    kw_coverage = sum(1 for s in subs if s['keywords']) / len(subs)
    abs_coverage = sum(1 for s in subs if (s.get('abstract') or '').strip()) / len(subs)
    review_count = sum(1 for s in subs if is_review(s))
    print(f'  Coverage: keywords={kw_coverage:.1%}, abstracts={abs_coverage:.1%}, reviews={review_count}')

    sims = compute_hybrid_similarity(subs)
    print(f'  Hybrid blend: {TFIDF_WEIGHT} × TF-IDF + {EMBED_WEIGHT} × embeddings', flush=True)

    # Decide which submissions to (re)compute
    if args.submission:
        targets = {args.submission}
        if args.affected_by:
            out = run_sql(
                args.target,
                f'SELECT DISTINCT submission_id FROM smarter_similar_articles WHERE similar_id = {args.affected_by};',
            )
            for tok in out.split():
                if tok.strip().isdigit():
                    targets.add(int(tok.strip()))
        subs_to_write = [s for s in subs if s['submission_id'] in targets]
        mode_full = False
    else:
        subs_to_write = subs
        mode_full = True

    id_to_idx = {s['submission_id']: i for i, s in enumerate(subs)}

    neighbours_by_id: dict[int, list[tuple[int, float]]] = {}
    empty = 0
    for sub in subs_to_write:
        idx = id_to_idx.get(sub['submission_id'])
        if idx is None:
            continue
        neighbours = pick_neighbours(sims, subs, idx)
        if not neighbours:
            empty += 1
            continue
        neighbours_by_id[sub['submission_id']] = neighbours

    t0 = time.time()
    write_bulk(args.target, neighbours_by_id, full=mode_full, dry_run=args.dry_run)
    dry_note = ' [dry-run, no writes]' if args.dry_run else ''
    print(
        f'  Wrote {len(neighbours_by_id)} articles ({empty} empty) '
        f'({time.time() - t0:.1f}s){dry_note}',
        flush=True,
    )

    return 0


if __name__ == '__main__':
    sys.exit(main())
