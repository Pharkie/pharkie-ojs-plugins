# Similar Articles Plugin

Renders a "Related articles" sidebar on the article page footer, reading from a pre-computed cache. Drop-in replacement for the stock [`recommendBySimilarity`](https://github.com/pkp/recommendBySimilarity) plugin, intended for journals whose corpus is thematically narrow enough that the stock plugin's live similarity query becomes pathologically slow.

## When to use this

Use this plugin if your journal has **corpus-wide keywords** — terms that appear in nearly every article because they name the journal's subject. For example, an existentialism journal where "existential" is in every article; an oncology journal where "cancer" is in every article.

On such corpora, the stock `recommendBySimilarity` plugin issues a live multi-JOIN query on every article view that OR-branches over 20 keywords. When any of those keywords match a large fraction of the corpus, the query takes 60+ seconds. With 8 Apache workers, a dozen concurrent article views saturates the pool and the site hangs. See [`docs/ojs-issues-log.md`](ojs-issues-log.md) #26 for the full incident report.

This plugin avoids that by pre-computing similarity offline with TF-IDF (which automatically down-weights corpus-wide tokens via the `max_df=0.5` threshold) and storing the top N neighbours per article in a cache table. The article page footer reads from the cache in one primary-key lookup — sub-millisecond, no corpus-skew exposure.

It is an **optional, separate** plugin. The stock `recommendBySimilarity` stays installed; you disable it via OJS's plugin admin UI when you enable this one.

## Requirements

- OJS 3.5+
- Python 3.10+ with `scikit-learn`, `pymysql`, `beautifulsoup4` (only on the host that runs the offline build, not the OJS server)
- SSH or direct MySQL access from the build host to the OJS database

## Architecture

```
┌──────────────────────────┐
│  Offline builder         │
│  scripts/ojs/            │──writes───┐
│  build_similar_articles  │           │
│  sklearn TF-IDF+cosine   │           ▼
└──────────────────────────┘  ┌────────────────────┐
                              │  similar_articles  │
                              │  (cache table)     │
                              └──────────┬─────────┘
                                         │ PK lookup, <1ms
                              ┌──────────▼─────────┐
                              │  PHP plugin        │──renders──► article footer sidebar
                              │  similarArticles   │
                              └────────────────────┘
```

- Plugin code is render-only. All analysis happens offline.
- The cache table `similar_articles` holds 5 rows per submission.
- The builder is idempotent: it deletes and re-inserts either the whole table (`TRUNCATE` mode) or just the affected submissions (`WHERE submission_id IN (...)` mode) inside a single transaction.

## Installation

### Docker (dev)

Already configured in `docker-compose.yml`:

```yaml
- ./plugins/similar-articles:/var/www/html/plugins/generic/similarArticles
```

After the mount is in place, install the plugin (runs the migration):

```bash
docker compose exec ojs php lib/pkp/tools/installPluginVersion.php \
  /var/www/html/plugins/generic/similarArticles/version.xml
```

Enable it in OJS admin: **Website > Plugins > Generic > Similar Articles (Cached)** → tick. Disable the stock **Recommend Articles by Similarity** plugin at the same time.

### Manual (non-Docker / live)

1. Copy `plugins/similar-articles/` to `plugins/generic/similarArticles/` in your OJS installation. Folder must be exactly `similarArticles` (camelCase) or OJS autoloading will not find the plugin class.
2. Install the plugin:
   ```bash
   php lib/pkp/tools/installPluginVersion.php \
     plugins/generic/similarArticles/version.xml
   ```
3. Enable in OJS admin: **Website > Plugins > Generic > Similar Articles (Cached)**.
4. Disable the stock **Recommend Articles by Similarity** plugin at the same time to avoid double-rendering.
5. Run the offline builder once to populate the cache (see next section). Until it runs, the sidebar is silently absent on all articles.

## Running the offline builder

`scripts/ojs/build_similar_articles.py` connects to the OJS database, reads every published submission's title + abstract + curated keywords + section, runs TF-IDF over a weighted text blob (keywords repeated 3×), takes the top 5 neighbours by cosine similarity, and writes the result to `similar_articles`.

### Configure targets

The script has a `TARGETS` dict near the top:

```python
TARGETS = {
    'dev':  ['docker', 'compose', 'exec', '-T', 'ojs-db',
             'bash', '-c',
             'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N --raw'],
    'live': ['ssh', 'sea-live',
             'cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs-db '
             "bash -c 'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N --raw'"],
}
```

Adapt to your environment — change the SSH host, path, or DB command as needed. The script only expects each target to pipe SQL in and tab/JSON output back.

### Run it

```bash
# Full rebuild against dev
python3 scripts/ojs/build_similar_articles.py

# Full rebuild against live
python3 scripts/ojs/build_similar_articles.py --target=live

# Recompute one article (e.g. just republished)
python3 scripts/ojs/build_similar_articles.py --submission=12345

# Recompute articles whose current cache points at 12345 (use after
# --submission when republishing with significant content changes)
python3 scripts/ojs/build_similar_articles.py --submission=12345 --affected-by=12345

# Compute but do not write — useful for validation
python3 scripts/ojs/build_similar_articles.py --dry-run
```

A full rebuild on ~1400 submissions completes in ~2 seconds. Scales linearly with corpus size; `numpy` similarity matrices of a few thousand documents stay in memory easily.

### Schedule nightly rebuild

Put it in a cron or CI scheduled workflow. Example GitHub Actions workflow (see `private/.github/workflows/rebuild-similar-articles.yml` in our deployment):

```yaml
on:
  schedule:
    - cron: '15 4 * * *'
jobs:
  rebuild:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install scikit-learn pymysql beautifulsoup4
      - name: SSH setup
        run: |
          # write ~/.ssh/hetzner key, ~/.ssh/config with sea-live Host alias
          ...
      - run: python3 scripts/ojs/build_similar_articles.py --target=live
```

The script runs on the CI runner (sklearn installed there), connects to the OJS DB over SSH, and writes only the cache table rows back. The OJS server itself never needs sklearn.

## Configuration

The plugin itself has no admin UI. Tune the algorithm by editing `scripts/ojs/build_similar_articles.py`:

| Constant | Default | Effect |
|---|---|---|
| `KEYWORD_WEIGHT` | `3` | How many times the keyword list is repeated in the text blob. Higher = editor-curated keywords dominate over title/abstract. |
| `MAX_RESULTS` | `5` | Sidebar size. Also hard-capped in the PHP render (`SimilarArticlesPlugin::MAX_RESULTS`). |
| `MIN_SCORE` | `0.15` | Cosine floor. Matches below this are noise; excluded. Raise to 0.20+ for stricter filtering. |
| `MAX_SCORE` | `0.95` | Duplicate-detection ceiling. Matches at or above this are near-identical text blobs (usually duplicate imports); excluded. |
| `RESTRICTED_SECTION_ABBREVS` | `{'BR'}` | Section abbrevs whose articles are restricted to same-section recommendations only. Adjust for your section naming. |

TF-IDF parameters (inside `compute_similarity()`):

| Parameter | Default | Effect |
|---|---|---|
| `stop_words` | `'english'` | Remove English stopwords ("the", "and", ...). |
| `min_df` | `2` | Drop terms appearing in only 1 article — noise. |
| `max_df` | `0.5` | **Critical**: drop terms appearing in >50% of corpus. This is what auto-filters corpus-wide tokens and keeps narrow-journal performance sane. |
| `ngram_range` | `(1, 2)` | Unigrams + bigrams — keeps phrases like "hermeneutic phenomenology" together. |

## Monitoring

`scripts/monitoring/monitor-deep.sh` runs these checks (added for this plugin):

- **Cache coverage**: `SELECT COUNT(DISTINCT submission_id) FROM similar_articles` vs published submissions. Fails if <50%, warns if <80%. A healthy state is ~93-95% (some articles legitimately have no match above `MIN_SCORE`).
- **Cache staleness**: oldest `computed_at` in the table. Fails if >7 days, warns if >48h. Catches silent failure of the nightly rebuild.

Both checks skip silently on targets that don't have the `similar_articles` table (i.e. the plugin isn't installed there).

## Troubleshooting

**Sidebar doesn't appear on any article.**

1. Plugin enabled? `SELECT * FROM plugin_settings WHERE plugin_name = 'similararticlesplugin' AND setting_name = 'enabled'` should return `1`.
2. Cache populated? `SELECT COUNT(*) FROM similar_articles` should be > 0.
3. Smarty compile cache stale? `rm -rf cache/t_compile/*` (inside the OJS container).
4. Migration ran? `SHOW TABLES LIKE 'similar_articles'` should return a row.

**Sidebar absent on a specific article.**

Expected on ~5% of articles: if the article's text blob has no term in common with anything else after `min_df`/`max_df` filtering, it has no neighbours above `MIN_SCORE`. The plugin deliberately renders nothing rather than showing filler.

**Neighbours look unrelated.**

Likely causes:
- The article has very sparse text (no abstract, short title, no curated keywords). TF-IDF can only work with what's there.
- A keyword you think is distinctive is in fact corpus-wide — check with `SELECT keyword_text, COUNT(DISTINCT ...) FROM submission_search_keyword_list JOIN ... GROUP BY keyword_text HAVING COUNT(DISTINCT ...) > <threshold>`.

Raise `KEYWORD_WEIGHT` if curated keywords should carry more weight; lower `MIN_SCORE` if you'd rather see weak matches than nothing.

**Duplicates appearing in a sidebar.**

`MAX_SCORE` should filter score-1.0 matches (identical text blobs). If near-duplicates at 0.93-0.95 leak through, lower `MAX_SCORE` to 0.90.

## Comparison with stock `recommendBySimilarity`

|  | Stock `recommendBySimilarity` | `similarArticles` (this plugin) |
|---|---|---|
| Where similarity is computed | On every article view, in SQL | Once, in Python, offline |
| Term weighting | None — raw term presence | TF-IDF with `max_df` filter |
| Query at render time | Multi-JOIN over submission_search_objects + LIKE %...% on publication_settings + author_settings | Primary-key lookup against `similar_articles` |
| Corpus-skew behaviour | Collapses (60-2000s per query) | Unaffected — skewed terms auto-filtered at index time |
| Freshness on publish | Immediate | Up to nightly-rebuild interval (typically 24h) |
| Plugin settings UI | Yes (number of recommendations) | No — tune via `build_similar_articles.py` constants |
| OJS version | 3.x | 3.5+ (uses Laravel migrations) |
