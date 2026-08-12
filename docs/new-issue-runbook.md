# Publishing a new issue

How to take the finished issue PDF from the production editor and get it live on
OJS. For importing historical back-issues, see [Backfill Pipeline](backfill-pipeline.md)
and [Backfill Reference](backfill-reference.md) — this runbook reuses the same
tooling, with the differences called out below.

The whole run takes roughly an hour, most of it checking rather than waiting.

## What's different from a back-issue

Back-issues were scanned, and their DOIs already existed at Crossref. A new issue
is neither of those things, and three steps change as a result.

| | Back-issue | New issue |
|---|---|---|
| HTML extraction | `pipe1_haiku_html.py` — page images through the Claude API, because the source is a scan | `pipe1d_layout_html.py` — reads the PDF's own text layer. Free, instant, identical on every run |
| DOIs | Already registered; carried in the import XML and restored by `pipe8_restore.py` | Do not exist. Minted by `pipe11_assign_dois.sh` **after** import, then deposited |
| `pipe8_restore.py` | Required — restores original submission IDs | **Skip it.** There are no prior IDs to restore |

Everything between (`pipe2` → `pipe6`) is unchanged.

## Before you start

- The issue PDF, as supplied by the production editor. Use the **electronic**
  edition, not the Amazon/print one — that has a wraparound cover and different
  trim.
- Python with `pymupdf`, `beautifulsoup4` and `requests`. The devcontainer has
  these; on a bare host, `python3 -m venv .venv && .venv/bin/pip install pymupdf
  beautifulsoup4 requests`.
- The dev OJS stack running (`docker compose up -d`).

No API key is needed. `pipe1d` does not call a model.

## 1. Stage the PDF and write the TOC

```bash
cp "<supplied file>.pdf" backfill/private/input/<vol>.<iss>.pdf
mkdir -p backfill/private/output/<vol>.<iss>
```

Write `backfill/private/output/<vol>.<iss>/toc.json` following
[the TOC guide](backfill-toc-guide.md). Note the optional `access` field: an
article's access normally follows its section, but anything can be opened or
paywalled individually — obituaries sit under Articles for citation purposes
and the editors may well want them open. Claude can draft it from the PDF; it
still needs checking against the CONTENTS page and the article pages.

Two things the CONTENTS page will not give you:

- **Individual book reviews.** The contents lists only "Book Reviews" and one
  page number. Read the review pages for each book's title, author, year,
  publisher and the reviewer's byline at the end.
- **Abstracts and keywords.** These are on each article's first page.

Watch for **the contents page and the article page disagreeing** — author-name
spellings and title capitalisation drift between the two. The article page is
usually right, but query anything you change.

Then:

```bash
python3 backfill/validate_toc.py backfill/private/output/<vol>.<iss>/toc.json
```

## 1a. First: has this already been published?

Worth thirty seconds before you touch anything. Picking this up mid-flight, or
after a handover, the honest answer is often "yes, mostly". Four checks, in order
of how much they tell you:

```bash
# 1. Is the staged input the file you were actually sent?
shasum -a 256 backfill/private/input/<vol>.<iss>.pdf "/path/to/what Dean sent.pdf"

# 2. Did the IDs get snapshotted? If every JATS carries BOTH, the import
#    completed and a future reimport will preserve URLs and DOIs.
cd private/backfill/output/<vol>.<iss>
grep -lc 'pub-id-type="publisher-id"' *.jats.xml | wc -l   # want: all of them
grep -lc 'pub-id-type="doi"' *.jats.xml | wc -l             # want: all of them

# 3. What does the private submodule say happened?
cd private && git log --oneline -3
```

**4. Check a BODY marker on the live site, never a title.** This is the one that
catches people out. Article titles and author names are *metadata* and get
hand-corrected directly in OJS when a query comes in — so the live titles can be
completely right while the body text is still from the previous export. A title
proves nothing about whether the PDF was reimported.

Pick something that exists only in the new PDF and only in the body: a corrected
URL, a sentence that was added, a changed figure caption. On 37.2 revB the two
good markers were the fixed Guardian URL (`2023/may/03`, where revA had
`2023 may/03`) and "Published 27th August." in the book review's references.
Both live meant the reimport had genuinely happened.

## 1b. If this is a corrected re-export, diff it first

Dean will sometimes send a revised PDF. Before rerunning anything, check what
actually changed — you want to know that the corrections you asked for landed
and nothing else moved:

```bash
python3 - <<'EOF'
import fitz, re, difflib
old = fitz.open('backfill/private/input/<vol>.<iss>.pdf')          # the published one
new = fitz.open('<the new file>.pdf')
flat = lambda t: re.sub(r'\s+', ' ', t).strip()
real = [i for i in range(old.page_count)
        if flat(old[i].get_text()) != flat(new[i].get_text())]
print('page count', old.page_count, '->', new.page_count)
print('pages with real content change:', real)
for i in real:
    a, b = flat(old[i].get_text()).split(), flat(new[i].get_text()).split()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag != 'equal':
            print(f"  p{i}: -{' '.join(a[i1:i2])[:50]} +{' '.join(b[j1:j2])[:50]}")
EOF
```

Normalising whitespace matters: a re-export reflows justified text on far more
pages than it changes words. On 37.2 revB, 38 pages differed but only 13 had a
real content change, and every one traced to a query we had raised.

**If the page count changed, or any article's first page moved, the page ranges
in `toc.json` are no longer valid** and must be rechecked before splitting.

**If the correction is an author's name, fix the registry FIRST.** The split's
normalise step (`backfill/private/authors.json`) fuzzy-matches names back to
their canonical form — so a corrected name in toc.json gets silently reverted
to the old spelling on the next split. Make the corrected name canonical and
demote the old one to its `variants` list, then correct toc.json. (37.2 revC:
"Jun Woo Kwon" was re-normalised back to "Kwan" until the registry was
flipped.)

## 1c. Metadata-only corrections: the fast path

When the change is metadata plus its appearances in one article's text — an
author name, a title typo — the full reimport (steps 2–7, with its search
reindex and pipe8/9b/9c retinue) is the wrong tool. Worked end-to-end on
2026-08-06 for an author name change with a re-typeset issue PDF (~15 min
once the checklist below is followed in order; issues log #42 records the
dead ends this order avoids).

**Activate the venv first** (`source .venv/bin/activate`): `split_issue.sh`
and `pipe9_issue_galleys.sh` shell out to bare `python3`, which must be the
venv's or fitz is missing and the run dies part-way.

In order:

1. **Registry first** (`backfill/private/authors.json`): make the corrected
   name canonical and demote the old spelling to its `variants` list (§1b) —
   otherwise the split normaliser silently reverts the toc.json fix.
2. **toc.json**: correct the `authors` string. If the change appears in the
   article body (a byline, a bio, a contact line — for a name change it
   always does) and the issue was Haiku-extracted, edit those lines in the
   article's `raw.html` and set `_manual_html` with a dated note so pipe1
   never re-extracts over the hand edit. pipe1d issues rerun pipe1d instead.
3. **If a re-typeset PDF arrived**: diff it (§1b), replace
   `backfill/private/input/<vol>.<iss>.pdf`, re-split
   (`split_issue.sh <input pdf>`). Then text-diff the splits against git —
   only the corrected article may differ. The other 19 PDFs will still churn
   at the byte level (PyMuPDF saves aren't deterministic): `git checkout`
   them rather than committing noise.
4. **pipe2→pipe6 as a block** — all of §4's rules apply (never pipe4 alone;
   reconcile per-article ref counts before and after).
5. **Patch OJS** — dev first when the dev stack is up, else dry-run against
   live is the review gate:

```bash
# Diff JATS against OJS and patch metadata + that article's galley files:
python3 backfill/html_pipeline/pipe13_patch_metadata.py \
    backfill/private/output/<vol>.<iss>/toc.json --target dev --article <N> --galleys
# same again with --confirm, then --target live --confirm

# The whole-issue PDF also carries the correction (issues log #37):
backfill/html_pipeline/pipe9_issue_galleys.sh --replace backfill/private/output/<vol>.<iss>
backfill/html_pipeline/pipe9_issue_galleys.sh --replace --host=sea-live backfill/private/output/<vol>.<iss>

# Crossref holds authors/title for registered DOIs — send the correction:
backfill/html_pipeline/pipe12_deposit_dois.sh <vol>.<iss> --host=sea-live \
    --redeposit=<the-article-doi> --confirm
```

   pipe13 patches title, givenName/familyName, and `copyrightHolder` (the
   "(Author)" line in DC.Rights — stamped at publish time, recomputed by
   nothing else), and dispatches a scoped reindex — no 1,400-job rebuild.
   It refuses structural changes (author count, abstract, missing
   submissions): those go through the reimport path.

   🛑 **The scoped reindex fails on its first run and has to be redispatched.**
   `UpdateSubmissionSearchJob` dies with `Call to a member function
   getContext() on null` when the worker picks it up, because there is no
   request context on the queue. The same job succeeds when pushed back
   through, so after every pipe13 run:

   ```bash
   php lib/pkp/tools/jobs.php failed --redispatch
   ```

   Skip it and two things follow: the article's searchable text stays on the
   pre-correction version, and job monitoring alerts on the failures (one per
   article patched, so a batch is a batch of alerts). Confirm it worked with a
   site search for a phrase that exists only in the corrected text, rather than
   trusting an empty `failed_jobs` table — redispatch empties that either way.
   `Skipped indexation: No suitable parser for … .pdf` in the output is normal:
   OJS indexes the HTML galley, not the PDF.

   After pipe12, `dois.status = 3`
   means Crossref confirmed; the public REST API lags the deposit by hours,
   so don't re-deposit just because api.crossref.org still shows the old
   metadata the same afternoon.

6. **Sweep for copies the pipeline doesn't own** (all found the hard way,
   issues log #42): orphaned `authors` rows from pre-pipe8-era imports still
   carry the old name (harmless to readers, but the name is still in the DB
   — sweep `author_settings` for it and delete the orphans); the search
   index keeps the old name's keywords in `submission_search_keyword_list`
   after the scoped reindex unmaps them (dead dictionary entries, no
   cleanup needed — verify `submission_search_object_keywords` no longer
   references them).

7. **Harbour** holds five more copies of the article and one of the issue
   PDF — follow membership-platform's
   `docs/journal-content-corrections.md` (§Author name changes for renames:
   the author row's `source_ref` is a name-slug and must be recomputed, or
   the next import duplicates the author and orphans the member link).

8. **Member data is not article data.** OJS `users`, the live WP account,
   and Harbour's `members` row may all still carry the old name/email —
   renaming a person's *account* is theirs and the membership secretary's
   call, not a side effect of a publication correction. Check, report,
   don't touch.

## 2. Split into per-article PDFs

```bash
backfill/split_pipeline/split_issue.sh backfill/private/input/<vol>.<iss>.pdf
```

Check the report: every article should verify, and each split PDF should end on
its author's byline or its reference list — not part-way through, and not
carrying the back matter (advertising rates, "Publications received", the
membership page). See `private/backfill-lessons-learned.md` for the failure modes
worth knowing.

## 3. Extract the HTML

```bash
python3 backfill/html_pipeline/pipe1d_layout_html.py backfill/private/output/<vol>.<iss>/toc.json --audit
python3 backfill/html_pipeline/pipe1d_layout_html.py backfill/private/output/<vol>.<iss>/toc.json
```

It also lifts any photographs out of the PDF, saving them as
`<slug>-figN.jpg` beside the split PDF and placing them in the flow. The run
reports how many need alt text; supply it per article in toc.json:

```json
"figures": [{"alt": "Portrait photograph of Rimantas Antanas Kocinas."}]
```

Nobody can write a useful description of a photograph from its bounding box,
so an image without alt text ships as `alt=""` rather than something invented.
Look at the extracted files and write the alt text — it takes a minute and it
is the difference between a screen-reader user getting the picture or not.

A re-export can change how the photographs are stored. 37.2 revB came back with
its two portraits as `DeviceN(1, DeviceCMYK, Black)` separations where revA had
them in a plain colourspace, and JPEG accepts only Grayscale, RGB or CMYK — so
the run crashed part-way through. `pipe1d` now converts anything unusual (single
channel to greyscale, everything else to RGB) and `--audit` encodes each figure
without writing it, so the same surprise fails the audit rather than the run.

`--audit` checks the layout model against the actual PDF before writing
anything. It compares every non-furniture character in the source against the
generated HTML, so a mismatch means text was dropped or invented. **A failing
audit is a stop sign, not a warning** — the template has probably changed, and
the sizes at the top of `pipe1d_layout_html.py` need revisiting. Run the audit
again until it is clean.

If the issue is a scan rather than born-digital, the audit will report no body
text. Use `pipe1_haiku_html.py` for that issue instead.

## 4. The rest of the pipeline

Unchanged from the backfill, and all of it is free and rerunnable:

```bash
V=<vol>.<iss>
python3 backfill/html_pipeline/pipe2_postprocess.py backfill/private/output/$V/toc.json --verify
python3 backfill/html_pipeline/pipe3_generate_jats.py backfill/private/output/$V/toc.json
python3 backfill/html_pipeline/pipe4_extract_citations.py --extract --volume $V
python3 backfill/html_pipeline/pipe4b_match_dois.py --volume $V --email <your email>
python3 backfill/html_pipeline/pipe5_galley_html.py backfill/private/output/$V/toc.json
python3 backfill/html_pipeline/pipe6_ojs_xml.py backfill/private/output/$V/toc.json
```

`pipe4b` queries Crossref for each reference and takes a few minutes. Expect
roughly 40% of references to match a DOI — that is the rate across the archive,
not a sign something is wrong.

**Run these in order, as a block.** `pipe3` rewrites the JATS from scratch,
putting references back in the body and wiping citations and DOIs; `pipe4`
expects exactly that state. Running `pipe4` on its own against already-extracted
JATS finds an empty body and re-derives the back matter from the leftovers — on
37.2 that quietly cost one article four of its ten references (issues log #40).

**Check per article, not in total.** That same mistake left the issue total
unchanged, because one article gained exactly what another lost. Before
importing, diff the counts:

```bash
python3 - <<'EOF'
import json, os
from xml.etree import ElementTree as ET
toc = json.load(open('backfill/private/output/<vol>.<iss>/toc.json'))
for a in toc['articles']:
    f = os.path.splitext(a['split_pdf'])[0] + '.jats.xml'
    print(f"{len(ET.parse(f).findall('.//{*}ref')):>4}  {os.path.basename(f)}")
EOF
```

**`pipe6` blocks on bad DOIs.** It refuses to write `import.xml` if any DOI is
malformed, or if a DOI link's markup runs into the following word. Both faults
import cleanly and look right on the page — they only surface months later in
Crossref's monthly resolution report, once the DOI has been deposited and cited.
Run the check on its own at any point:

```bash
python3 backfill/lib/doi_validate.py backfill/private/output/<vol>.<iss>
```

Fix the source (`raw.html`, or `toc.json` for a `_manual_html` article) and rerun
pipe2→pipe6. `--allow-bad-dois` overrides the block and should stay unused: the
fault reaches Crossref and readers. What it catches, all of it live at some
point: two DOIs concatenated by greedy extraction (7.2, 10.2), an OCR `×` for
`x` and a zero-width space (34.2), a web address behind the DOI resolver (35.2),
and — the one that generates the report entries — a DOI link with no whitespace
after `</a>`, so anything reading the page as text requests the DOI with the
next word stuck to it (37.2, the Kočiūnas obituary).

## 5. Import to dev and check it

> **Nothing else may be touching the box.** It is 2 vCPUs and 3.8 GB, plus a 3 GB
> swapfile added 2026-08-04, running thirteen containers. An import running
> alongside a CI deploy exhausted it and took every site down, sshd included
> (issues log #39). **The swapfile did not repeal this rule** — it stopped the
> hard freezes, but the constraint that bit was CPU: both vCPUs pinned at 200%
> with near-zero disk I/O, which no amount of swap helps. Before you start:
> `gh run list --limit 1` in both repos, and don't push while an import runs.


```bash
bash backfill/html_pipeline/pipe7_import.sh backfill/private/output/$V
python3 backfill/html_pipeline/pipe10_verify.py backfill/private/output/$V/toc.json --docker
```

`pipe7` also sets the new issue as the journal's current issue and reorders the
archive. Then review in the browser (and in [Archive Checker](archive-checker-plugin.md)):
the issue TOC, a few article pages, and at least one Full Text galley end to end
against the PDF. Check the section split and the paywall labels — Editorial and
Book Reviews should be open, Articles paywalled.

If you need to fix something, correct the source (`toc.json`, or the pipeline)
and rerun from the affected step, then `pipe7_import.sh ... --force`. Add
`--no-reindex` when the body text has not changed.

## 6. Mint the DOIs

**Only once the content is final.** A `--force` reimport deletes and recreates
the articles, which discards their DOIs and mints different ones next time.

```bash
bash backfill/html_pipeline/pipe11_assign_dois.sh $V --dry-run
bash backfill/html_pipeline/pipe11_assign_dois.sh $V
python3 backfill/html_pipeline/tools/snapshot_ids.py --target dev --issue $V
```

`pipe11` uses OJS's own repository code, so suffixes follow the journal's
configured pattern and match the rest of the archive. It skips anything that
already has a DOI, so it is safe to rerun.

`snapshot_ids.py` is the step that makes this durable: it writes the assigned
submission IDs and DOIs back into the JATS files, so the issue becomes
self-describing. From then on it behaves exactly like a back-issue — a future
reimport carries the same IDs and DOIs, and `pipe8_restore.py` applies again.
**Do not skip it**, or a later reimport will silently change published DOIs.

Then write the citation DOIs into the database:

```bash
python3 backfill/html_pipeline/pipe9b_citation_dois.py --target dev
```

## 7. Deploy to live

Follow "Specific issues changed" in [`CLAUDE.md`](../CLAUDE.md#deploying-to-live),
with the new-issue differences:

0. **Check nothing else is running.** `gh run list --limit 1` in both repos —
   a CI deploy building alongside the import will take the box down (#39).
1. Pause the Better Stack monitors: `scripts/monitoring/maintenance-window.sh --pause`
2. `scripts/dev/backfill-remote.sh --host=sea-live --sync-only`
3. On the box: `pipe7_import.sh <issue dir>` (no `--force` — the issue is new).
4. **Skip `pipe8_restore.py`** on the first deploy. There are no prior live IDs;
   running it does nothing useful. It applies from the second deploy onwards,
   once `snapshot_ids.py` has been run against live.
5. **Nothing to mint.** `snapshot_ids.py` put the dev-minted DOIs into the JATS
   at step 6, so `pipe6` wrote them into the import XML and live came up already
   carrying them. Dev and live agree, which is what you want. (`pipe11` is only
   needed if you skipped the dev pass.)
6. `python3 backfill/html_pipeline/tools/snapshot_ids.py --target live --issue <vol>.<iss>`
   — now capture live's *submission ids* into the JATS. The DOIs already match;
   this is what protects live URLs on any future reimport.
7. `pipe9b_citation_dois.py --target live --confirm`
8. `pipe9c_content_filtered.py --target live --confirm`
9. `scripts/monitoring/maintenance-window.sh --resume`, then
   `scripts/monitoring/content-check.sh --host=sea-live`.

## 8. Deposit the DOIs at Crossref

Assigning is not depositing.

```bash
bash backfill/html_pipeline/pipe12_deposit_dois.sh $V --host=sea-live            # list
bash backfill/html_pipeline/pipe12_deposit_dois.sh $V --host=sea-live --confirm  # send
```

Scoped to one issue on purpose: OJS's own "deposit all" sweeps up every DOI
needing deposit in the journal, including unrelated stale ones.

Crossref rate-limits bursts and will 429 the odd one even with the built-in
pacing. **Re-run the command** — it is idempotent and picks up only what has not
registered. Then check nothing is left in error:

```bash
ssh sea-live 'cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs-db bash -c "mysql -u root -p\$MYSQL_ROOT_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT status, COUNT(*) FROM dois GROUP BY status;\""'
```

Status 3 is registered, 4 is error, 5 is stale. Verify by resolution rather than
by the status column — OJS marks a DOI registered on a successful HTTP response,
which really means *submitted*:

```bash
curl -sI https://doi.org/<the-doi> | head -1
```

A 302 to the article page is the real confirmation. Crossref's REST API lags by
hours, so a 404 from `api.crossref.org` right after depositing is normal.

## 9. Bring the new site up to date

Harbour's journal content is imported from OJS, so the new issue reaches
newsite.existentialanalysis.org.uk by rerunning the importer. It upserts on
`source_ref`, so it adds the new issue and leaves everything else alone —
**never pass `--wipe`**, which clears all journal rows. Dry-run first and read
the report. See [`membership-platform/docs/migration-import.md`](../../membership-platform/docs/migration-import.md).

Afterwards, rerun `scripts/migrate/link-authors-members.ts` so the issue's
authors are linked to member records where the names match.

## Republishing this page to Outline

The repo copy is canonical. To push it to the knowledge base:

```bash
python3 scripts/dev/publish-doc-to-outline.py docs/new-issue-runbook.md 2e168ab2-e50c-4bcb-988f-f3143a98ce23
#   --dry-run   prints what would be sent and changes nothing
```

It rewrites relative links to absolute GitHub URLs and unwraps hard-wrapped
prose, leaving code, tables and lists alone. Doing that by hand produced two
broken links: `CLAUDE.md` pointed at `docs/CLAUDE.md` when it is at the repo root,
and `migration-import.md` pointed inside this repo when it lives in
membership-platform. The script resolves paths rather than prefixing them, and
knows membership-platform is a different repo.

The token comes from the keychain (`sea-outline-api`), so it never appears in a
command line.

## Where this should end up

The production editor should not need any of this. The shape of the eventual
job: upload the issue PDF, the pipeline runs, and the result is presented for
approval before publishing. Every step above except writing `toc.json` is
already non-interactive, and `toc.json` is the piece that still needs a person —
which is the right place to put the review, since it is where the errors are.
