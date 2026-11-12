# Getting an issue into OJS

The **delivery** half of journal publishing: it takes a folder the content
pipeline has already produced and loads it into OJS, then mints and deposits
DOIs.

🛑 **The content half is not here any more.** Splitting the issue PDF, reading
its text layer into HTML, building the JATS and matching citations moved to
Harbour on 2026-08-18 and were deleted from this repo. They are
`membership-platform/scripts/pipeline/` — see its README to generate an issue.
This directory picks up from there.

Both repos read the same output directory (`backfill/private/output/<vol>.<iss>/`,
a symlink to the private journal-data checkout), so nothing needs copying
between them.

## The stages

| | |
|---|---|
| `pipe6_ojs_xml.py` | Bundles the folder into OJS Native XML, galleys base64-embedded |
| `pipe7_import.sh` | Loads that XML into OJS over the Docker CLI |
| `pipe8_restore.py` | Restores original submission ids, so a reimport keeps published URLs and DOIs. Skipped on an issue's first import — there is nothing to restore |
| `pipe9_issue_galleys.sh` | Attaches the whole-issue PDF to the OJS issue. `--replace` swaps the file behind an existing galley, which `pipe7 --force` never touches |
| `pipe9b_citation_dois.py` | Writes matched reference DOIs from the JATS into OJS `citation_settings` |
| `pipe9c_content_filtered.py` | Writes JATS `<custom-meta>` flags into OJS `publication_settings` |
| `pipe10_verify.py` | Compares the TOC against what OJS actually holds |
| `pipe11_assign_dois.sh` | Mints DOIs through OJS's own repository code, so suffixes match the archive |
| `pipe12_deposit_dois.sh` | Deposits them at Crossref. Assigning is not depositing |
| `pipe13_patch_metadata.py` | Fast path for metadata-only corrections, skipping a reimport |
| `pipe14_replace_text.py` | Corrected article text from the source repo (a `<base>..<head>` diff) onto the Full Text and JATS galley files in place, with a backup and `--restore`; Harbour's twin is `scripts/ops/apply-article-text.ts` |
| `tools/snapshot_ids.py` | Writes assigned ids and DOIs back into the JATS, which is what makes an issue self-describing |

`lib/` holds only what these need: `crossref.py`, `doi_validate.py` (malformed
DOIs and run-on DOI links, both of which have reached the live journal), and
`paths.py`.

## 🛑 paths.py, and why a stage must not load toc.json itself

`toc.json` records `split_pdf` relative to the repository root that generated
it. A folder Harbour made says `./scripts/pipeline/private/output/...`, which
resolves to nothing from here — and every stage derives the `.jats.xml` and
`.galley.html` from that string. Before this was fixed, `pipe6` refused the
whole issue with "22 of 22 articles have no galleys".

`load_toc()` resolves each split PDF beside its own `toc.json`, which is where
it has always physically been. **Every stage that reads a toc must go through
it**; `tests/test_paths_interop.py` fails if one goes back to a bare
`json.load`.

## Running the tests

```bash
python3 -m pytest backfill/tests/ -v
```

168 of them, plus one skipped. The content-half tests went to Harbour with the code they cover.
