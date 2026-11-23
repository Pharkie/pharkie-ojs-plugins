# Reclaiming the Git LFS storage in `sea-ojs-private`

**Status: PREPARED, NOT RUN.** Every command below is destructive in a way that
needs a decision first. Nothing here has been executed.

**Journal PDFs, 23 Nov 2026: they are leaving git too.** Adam decided the print
issues (`backfill/input/*.pdf`) and the per-article PDFs
(`backfill/output/**/*.pdf`) get their own data management: a content-addressed
store on the Mac (`$SEA_JOURNAL_PDFS/store/<sha256>.pdf`, default
`~/dev/SEA/journal-pdfs`), a copy in R2, and `backfill/pdfs.sha256` in git
(an LFS oid is the content sha256). A new PDF goes in with
`tools/pdfs.sh add` in sea-ojs-private. The OJS stages here already read either
layout through `backfill/lib/paths.py`. Untracking them (`git rm --cached`, the
two `.gitattributes` lines) is a later cut-over in sea-ojs-private; their past
LFS objects stay billed until history is cleared, which waits for a full restore
from R2. The "should stay" below predates this.

## What is actually stored

Measured 2026-08-29 from a pointer-only clone (`GIT_LFS_SKIP_SMUDGE=1`), across
all refs:

| Path | Objects | Size |
|---|---:|---:|
| `backups/**/*.sql.gz.enc` | 156 | **11.06 GB** |
| `backfill/**/*.pdf` (journal PDFs) | 1,579 | 0.62 GB |
| **Total** | 1,735 | **11.68 GB** |

Every object id is distinct, so there is no deduplication to recover. GitHub was
reporting ~90% of the 10 GB free allowance around the same date; treat the exact
figure as approximate and the ratio as the point. **The nightly database dumps
are ~95% of the storage.** The journal PDFs are a rounding error and should stay.

The dumps arrived through `.github/workflows/backup.yml`, which pulled the
previous night's encrypted dump off the box and committed it through LFS. It
pruned the working tree to 30 daily — which is why the repo looked small and the
bill did not.

That workflow was **disabled on 2026-08-29** and the off-site copy now goes to
Cloudflare R2 instead (`scripts/ojs/upload-backup-r2.sh`, and the *Off-site
backups* section of [`setup-guide.md`](setup-guide.md)). Nothing new is being
added to LFS. What follows is only about the 11 GB already there.

## 🛑 The thing that makes this awkward

**Rewriting history does not reclaim any storage.** This is the trap, and it is
the opposite of what everyone expects:

> After you remove files from Git LFS, the Git LFS objects still exist on the
> remote storage and will continue to count toward your Git LFS storage quota.
> — [GitHub Docs](https://docs.github.com/en/repositories/working-with-files/managing-large-files/removing-files-from-git-large-file-storage)

`git filter-repo` or BFG plus a force push removes the **pointers**. The objects
stay in GitHub's LFS store, and there is no API, UI or `git` command that deletes
them. GitHub names exactly two routes: **delete and recreate the repository**, or
**ask Support to purge them**.

So "rewrite the history and force-push" is not a smaller version of this job. It
is a job that achieves nothing on its own, and it would still be true after it
had thrown the history away.

One more billing detail worth knowing before picking a date: deleting objects
part-way through a month does not recalculate that month. The usage figure moves
on the 1st.

## Option A — delete and recreate the repository (reclaims everything)

Net LFS afterwards ≈ **0.62 GB** (the journal PDFs, re-pushed).

**What it destroys**, checked against the live repo on 2026-08-29:

- **9 Actions secrets.** They cannot be read back out of GitHub. Eight are
  recoverable: `SSH_PRIVATE_KEY` is `~/.ssh/hetzner-backup` on Adam's Mac
  (fingerprint verified against `github-actions-backup` in the box's
  `authorized_keys`); `CADDY_WP_AUTH_PASS`, `BETTERSTACK_HB_DAILY`,
  `BETTERSTACK_HB_HOURLY`, `LIVE_OJS_URL` and `LIVE_WP_HOME` are all in the
  box's `/opt/pharkie-ojs-plugins/.env`; `VPS_HOST` is the box; `KNOWN_HOSTS`
  rebuilds with `ssh-keyscan`.

  🛑 **`SSH_MONITOR_KEY` IS NOT RECOVERABLE, and deleting the repo destroys it.**
  Checked 2026-08-29: the `github-actions-monitor` entry in the box's
  `authorized_keys` has fingerprint `SHA256:kGf1TgKs1GGKYrGBvb/zaq0p1nSZI23fCuXz341/fi0`,
  which matches **none** of the seven keys on Adam's Mac, and no key material
  exists anywhere in this repo or the private one. The private half lives only
  inside the GitHub secret, and a secret is write-only.

  This is fixable but it is WORK, and it must happen in the same sitting:
  generate a new keypair, add the public half to the box, then set the private
  half as the new repo's secret. Copy the restriction verbatim — the existing
  line is `command="/usr/local/bin/monitor-shell.sh",restrict`, and a key added
  without it is an unrestricted root login rather than a monitoring key.

  ```bash
  ssh-keygen -t ed25519 -N '' -C github-actions-monitor-v2 -f ~/.ssh/hetzner-monitor
  ssh sea-live "printf 'command=\"/usr/local/bin/monitor-shell.sh\",restrict %s\n' \
    \"$(cat ~/.ssh/hetzner-monitor.pub)\" >> /root/.ssh/authorized_keys"
  ssh -i ~/.ssh/hetzner-monitor root@<box> true      # PROVE IT WORKS FIRST
  # …then, and only then, delete the repo. Afterwards:
  gh secret set SSH_MONITOR_KEY -R Pharkie/sea-ojs-private < ~/.ssh/hetzner-monitor
  ```

  Prove the new key works **before** anything is deleted. Testing it afterwards
  means discovering a typo with the monitoring already down and no old key to
  fall back on.
- **One closed PR** (`chore: Configure Renovate`) and all workflow run history.
- Nothing else: no issues, no forks, no stars, no deploy keys, no branch
  protection, one branch.

🛑 **The real risk is not the repo, it is the monitoring.** Three of the four
workflows there are live: `monitor-daily.yml`, `monitor-rerun.yml` and
`rebuild-smarter-similar-articles.yml`. Between deleting the repo and re-adding
the secrets, the OJS/WP checks are silently not running and their Better Stack
heartbeats will go red. Do this in one sitting, and expect the heartbeat alerts.

### Where it stands — steps 1 and 2 are DONE

Adam chose this route on 2026-08-29, and everything that can be done without
destroying anything has been:

- **The rewritten repository is ready** at
  `~/dev/SEA/sea-ojs-private-lfs-cleanup/rewritten-repo` — 156 backup pointers
  removed, no commit mentioning `backups/`, all 1,579 journal PDF objects
  fetched (620 MB) and `git lfs fsck` clean, `origin` already set.
- **`.github/workflows/backup.yml` has been deleted in that rewrite.** It was the
  thing that caused all this and R2 replaced it. That removes the last use of
  `SSH_PRIVATE_KEY`, so **the recreated repo needs six secrets, not nine** —
  `CADDY_WP_AUTH_PASS` and `KNOWN_HOSTS` turned out to be referenced by no
  workflow at all.
- **The replacement monitoring key exists and is proven.**
  `~/.ssh/hetzner-monitor` (fingerprint `SHA256:ind5ops4B9022vkPLRhbPVQjh/AFZptkGpaDK6QV91I`)
  is installed in both `/root/.ssh/authorized_keys` and
  `/home/deploy/.ssh/authorized_keys` with the same
  `command="/usr/local/bin/monitor-shell.sh",restrict` as the old one, and was
  tested end to end: an allowed command works as both users, a real
  `cd /opt/harbour/app && …` shape works, an interactive session is rejected and
  so is an off-list command. **It works while the old key still works**, which is
  the only order in which that is worth knowing.
- **`restore-secrets.sh --check`** in the same directory resolves all six values
  from their real homes without touching GitHub. It was run on 2026-08-29 and all
  six came back. Re-run it immediately before the delete.

### What is left — ONE command, and it is Adam's

🛑 **The original ordering in this doc was wrong and Adam corrected it.** It said
delete, then recreate, then push. His objection: *"create the new one, make sure
it's all working before we delete the old one."* He is right, and it has already
paid for itself twice on the real run:

1. **The LFS push to a second repo might have been refused** with the account
   over quota. It was not — 1,579 objects, 638 MB, clean — but under the old
   ordering that unknown would have been discovered with the original already
   deleted and no way back.
2. **The first monitoring run on the replacement FAILED**, and the cause was a
   wrong value in `restore-secrets.sh`: `LIVE_WP_HOME` had been mapped to the
   box's `WP_HOME`, which is the `wp-staging` mirror behind basic auth. Two WP
   tests returned 401 while fifteen passed. The right value is the **live
   community WordPress on Krystal**, which the spec documents in its own header
   — the box is not the live WP. Under the old ordering that would have been a
   broken secret in a recreated repo with no original left to diff against.

So the rule this leaves behind: **when the replacement can be built alongside,
build it alongside.** The name is the only thing that forces an ordering, and a
rename at the end is cheaper than a restore from the middle.

**Already done** (2026-08-29), all non-destructive:

- `Pharkie/sea-ojs-private-v2` created, rewritten history pushed with all 1,579
  journal PDF objects.
- Six secrets installed by `restore-secrets.sh` (`TARGET_REPO=…-v2`).
- `monitor-daily` dispatched and **green**. The first dispatch was run with the
  heartbeat secrets deliberately removed, so a failing test could not page the
  Society: `ping_heartbeat` returns early on an empty URL and
  `register_heartbeat` only appends a non-empty one — both checked in the source
  rather than assumed.
- Parity checked: **7,458 paths identical** once `backups/` and `backup.yml` are
  excluded, same two tags, same single branch, three workflows, six secrets.

**The remaining step is Adam's to run.** Permanently destroying a repository is
not an action a session should take on anyone's say-so, including its own:

```bash
gh repo delete Pharkie/sea-ojs-private --yes
```

Everything either side of it is scripted. Immediately afterwards:

```bash
gh repo rename sea-ojs-private -R Pharkie/sea-ojs-private-v2 --yes   # name restored
gh workflow run monitor-daily.yml -R Pharkie/sea-ojs-private          # prove it survived
```

Then, once that run is green and NOT before, retire the superseded key — until
this runs, the box still trusts a private key last seen inside a deleted secret.
The `$` anchor matters: it matches `github-actions-monitor` and not
`github-actions-monitor-v2`.

```bash
ssh sea-live "sed -i '/github-actions-monitor$/d' /root/.ssh/authorized_keys \
                                                  /home/deploy/.ssh/authorized_keys"
```

Two secrets are deliberately not carried over: `SSH_PRIVATE_KEY` (its only user,
`backup.yml`, is deleted) and `CADDY_WP_AUTH_PASS` and `KNOWN_HOSTS`, which no
workflow referenced.

Storage does not drop instantly — reports of a 17 GB repo taking about fifteen
minutes. Check the account's billing page rather than assuming it failed.

Keep `lfs-rescue.git` until the new repo has been verified end to end.

## Option B — ask GitHub Support to purge the objects

Non-destructive: the repo, its secrets, its history and its run logs all survive.
Rewrite the history first (step 2 above, force-pushed), then open a ticket
quoting the "unable to delete the repository" line from the doc linked above and
naming `backups/**/*.sql.gz.enc`.

Slower and not guaranteed — but it is the only option that does not take the
monitoring down, and there is no deadline now that nothing new is being added.

## Option C — buy a data pack

$5/month per 50 GB. Removes the problem without removing the data, and pays
indefinitely to store database dumps that R2 now holds anyway. Worth naming only
so that "do nothing" is a considered choice rather than a default.

## Decision

**A — delete and recreate.** Chosen by Adam on 2026-08-29, after being told that
`SSH_MONITOR_KEY` could not be recovered and that the historical dumps would be
discarded rather than archived. B and C stay written down because if the delete
goes wrong halfway, they are what is left.
