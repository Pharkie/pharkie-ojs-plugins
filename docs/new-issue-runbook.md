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

## 5. Import to dev and check it

> **Nothing else may be touching the box.** It is 2 vCPUs and 3.8 GB running
> thirteen containers. An import running alongside a CI deploy exhausted it and
> took every site down, sshd included (issues log #39). Before you start:
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
1. Pause the Better Stack monitors.
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
9. Unpause monitors, then `scripts/monitoring/content-check.sh --host=sea-live`.

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

## Where this should end up

The production editor should not need any of this. The shape of the eventual
job: upload the issue PDF, the pipeline runs, and the result is presented for
approval before publishing. Every step above except writing `toc.json` is
already non-interactive, and `toc.json` is the piece that still needs a person —
which is the right place to put the review, since it is where the errors are.
