#!/bin/bash
# Off-site the encrypted database dumps to Cloudflare R2.
#
# 🛑 THIS REPLACES A GIT-LFS PUSH, AND THE REASON IS WORTH KNOWING BEFORE YOU
# CHANGE IT. Until 2026-08-29 the nightly OJS dump was committed into
# Pharkie/sea-ojs-private through Git LFS by a GitHub Actions workflow. Every
# night produced a distinct LFS object, and an LFS object is not freed by
# deleting the file that referenced it — the workflow's "keep 30 daily" prune
# removed pointers and nothing else. 156 objects, 11.06 GB, against a 10 GB
# account quota. GitHub's own answer to reclaiming that space is to delete the
# repository: a history rewrite does not touch the object store. So the fix was
# never a smaller retention. It was a store where deleting a file frees bytes.
#
# What this does: copies the *.sql.gz.enc dumps that backup-ojs-db.sh has
# already written AND VERIFIED into R2, then expires old ones by age. The files
# are already AES-256-CBC encrypted and stay that way, so R2 never holds
# plaintext and the key never leaves the box (/opt/backups/ojs/.backup-key, plus
# the copy in the password manager). R2 holding the ciphertext and the box
# holding the key is the point: neither one alone restores anything.
#
# 🛑 THE 4.3 GB ojs-files-*.tar.gz.enc TARBALL IS DELIBERATELY NOT UPLOADED.
# The LFS copy never carried it either. It is ~40x every DB dump combined and it
# changes slowly, so off-siting it is a cost decision rather than an oversight —
# recorded in docs/setup-guide.md so that the gap is a known one.
#
# Harbour's scripts/infra/upload-backup-r2.sh (Pharkie/sea-membership-platform)
# does the same job for its own dumps into the same bucket under `harbour/`.
# Keep the two in step: the rclone version floor, the env-only config,
# copy-not-sync and the two delete guards rest on the same reasoning in both.
#
# Usage:
#   scripts/ojs/upload-backup-r2.sh              # copy up, then expire
#   scripts/ojs/upload-backup-r2.sh --dry-run    # say what would move, write nothing
set -eo pipefail

# Overridable only so the whole path can be rehearsed against a throwaway
# directory and prefix without touching the real dumps. Production never sets it.
BACKUP_DIR="${OJS_BACKUP_DIR:-/opt/backups/ojs}"
PROJECT_DIR="/opt/pharkie-ojs-plugins"

# Days to keep. The LFS store held 30 daily + 12 weekly, and matching it means
# the change of storage is not also a quiet change of recovery window.
KEEP_DAILY_DAYS=30
KEEP_WEEKLY_DAYS=84

# 🛑 REFUSE TO EXPIRE A NEARLY-EMPTY PREFIX. Expiry is by age, so a month of
# failed uploads followed by one successful run would age every remaining object
# past the limit and delete the lot — the run that finally works would be the
# run that empties the store. At or below this many objects the expiry step is
# skipped and says so, on the principle that too many backups is not an incident.
#
# 14 is a week of floor: a night here uploads TWO dumps into daily/, ojs and wp.
# The files tarball is not uploaded (see the top), and Umami's database is
# Harbour's to back up, not this script's. In weekly/, which gains the same two
# each Sunday, 14 is seven weeks. Harbour's uploader keeps the same intent with
# its own arithmetic. Pick this from what a night actually produces rather than
# copying the number from another script.
EXPIRE_FLOOR=14

# The most one night may delete from a prefix: ten, five nights of steady-state
# expiry. More than that due, and the night deletes none of it and goes red; see
# expire().
MAX_DELETE=10
# The exit code for exactly that: the dumps reached R2 and the expiry was
# refused. backup-ojs-db.sh reads it as its own alarm, apart from "the dumps are
# still only on this box". Not 1 (every other failure here), and outside
# rclone's 0-10. Harbour's uploader uses the same number.
EXPIRY_CAPPED_EXIT=20

DRY_RUN=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
  esac
done

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- Configuration ------------------------------------------------------------
# Sourced from the same .env as backup-ojs-db.sh. That file is root-only and not
# in git; the names are documented in docs/setup-guide.md.
#
# 🛑 THIS FILE WINS OVER THE CALLER'S ENVIRONMENT — `set -a; source` overwrites
# anything already exported. That is right for cron, which has no environment at
# all, but it means you cannot rehearse this by exporting BACKUP_R2_ENABLED=true
# in your shell: the file's `false` silently wins and the run becomes a no-op
# that still exits 0. Point OJS_BACKUP_ENV_FILE at a scratch file instead.
# Production never sets it.
ENV_FILE="${OJS_BACKUP_ENV_FILE:-$PROJECT_DIR/.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null  # the box's .env, which is not in git
  source "$ENV_FILE"
  set +a
fi

# 🛑 DORMANT UNTIL SWITCHED ON, and that is not a hedge — it is what lets this
# land before the bucket exists. The nightly cron reads the EXIT CODE of
# backup-ojs-db.sh to decide the Better Stack heartbeat, so a script that failed
# on missing config would page every night for a step nobody had finished
# setting up. Unset means "skip, loudly". Set means "and a failure is real".
if [ "${BACKUP_R2_ENABLED:-false}" != "true" ]; then
  log "R2 off-site is not enabled (BACKUP_R2_ENABLED != true) — skipping."
  log "  Nothing is being copied off this box. See docs/setup-guide.md."
  exit 0
fi

for var in BACKUP_R2_ACCOUNT_ID BACKUP_R2_ACCESS_KEY_ID BACKUP_R2_SECRET_ACCESS_KEY BACKUP_R2_BUCKET; do
  if [ -z "${!var}" ]; then
    log "ERROR: R2 off-site is enabled but $var is not set in $ENV_FILE"
    exit 1
  fi
done

# 🛑 DO NOT `apt-get install rclone`. Ubuntu 24.04 ships 1.60.1 (November 2022)
# and it CANNOT TALK TO R2 RELIABLY. Measured on this box 2026-08-29: a 200 KB
# PUT failed with `AccessDenied` six times out of six, and adding
# `--s3-no-check-bucket` turned it into `NotImplemented` (501) on the first
# attempt of every run. The credential was never the problem — a hand-signed
# SigV4 PUT of the same 200 KB object to the same bucket with the same key
# succeeded three times out of three. It is the old client.
#
# The version below is the floor, not a preference. Install the upstream binary:
#   curl -fsSLO https://github.com/rclone/rclone/releases/download/vX/rclone-vX-linux-amd64.zip
#   (verify against the release's SHA256SUMS), unzip, install to /usr/local/bin.
RCLONE_MIN_MAJOR=1
RCLONE_MIN_MINOR=65

# Prefer /usr/local/bin explicitly. cron's PATH is /usr/bin:/bin, so if a distro
# rclone is ever reinstalled it would silently win here and reintroduce exactly
# the failure above — at 03:00, with only a heartbeat to say so.
RCLONE="${RCLONE_BIN:-}"
if [ -z "$RCLONE" ]; then
  if [ -x /usr/local/bin/rclone ]; then RCLONE=/usr/local/bin/rclone
  else RCLONE=$(command -v rclone 2>/dev/null || true); fi
fi
if [ -z "$RCLONE" ] || [ ! -x "$RCLONE" ]; then
  log "ERROR: rclone not found. See the install note in this script — not apt."
  exit 1
fi

# `|| true` for the same reason as everywhere else in this script: without it, an
# rclone that cannot even print its version kills the run here, silently, instead
# of reaching the explanatory error below.
RCLONE_VER=$("$RCLONE" version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
RC_MAJOR=${RCLONE_VER%%.*}; RC_MINOR=${RCLONE_VER##*.}
if [ -z "$RCLONE_VER" ] \
   || [ "${RC_MAJOR:-0}" -lt "$RCLONE_MIN_MAJOR" ] \
   || { [ "${RC_MAJOR:-0}" -eq "$RCLONE_MIN_MAJOR" ] && [ "${RC_MINOR:-0}" -lt "$RCLONE_MIN_MINOR" ]; }; then
  log "ERROR: $RCLONE is version ${RCLONE_VER:-unknown}; R2 needs ${RCLONE_MIN_MAJOR}.${RCLONE_MIN_MINOR} or newer."
  log "  An older client fails with AccessDenied or NotImplemented against R2 — see the note above."
  exit 1
fi
log "Using $RCLONE (v$RCLONE_VER)"

# EU jurisdiction buckets live on a different hostname from the default ones,
# and getting it wrong is an AccessDenied that reads like a bad key. Harbour's
# bucket is `eu`; match whatever the bucket was actually created under.
JUR="${BACKUP_R2_JURISDICTION:-}"
ENDPOINT="https://${BACKUP_R2_ACCOUNT_ID}${JUR:+.$JUR}.r2.cloudflarestorage.com"
PREFIX="${BACKUP_R2_PREFIX:-ojs}"

# rclone is configured entirely through the environment so that no credential is
# ever written to a config file on disk.
export RCLONE_CONFIG_R2_TYPE="s3"
export RCLONE_CONFIG_R2_PROVIDER="Cloudflare"
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$BACKUP_R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$BACKUP_R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="$ENDPOINT"
export RCLONE_CONFIG_R2_REGION="auto"
export RCLONE_CONFIG_R2_ACL="private"
# 🛑 PIN THIS OFF. `set -a; source .env` exports every name in that file, and
# rclone's s3 backend picks up AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the
# environment when env_auth is on. It is off by default today, so this changes
# nothing — but the moment SES go-live puts AWS credentials in that .env, a
# default that drifted would silently sign R2 requests with the wrong key and
# fail as AccessDenied. Cheaper to state than to diagnose.
export RCLONE_CONFIG_R2_ENV_AUTH="false"
# 🛑 NO `no_check_bucket` HERE, TEMPTING THOUGH IT LOOKS. The token cannot
# create a bucket, so skipping the check reads like the right thing to do — but
# on this box it was the thing that produced `NotImplemented` (501) on the first
# attempt of every single run, on both 1.60 and in the matrix that isolated it.
# Leaving the check on is clean. If a future rclone starts failing the check
# against a bucket that plainly exists, diagnose it rather than reaching for
# this flag again.

REMOTE="R2:${BACKUP_R2_BUCKET}/${PREFIX}"

log "R2 off-site: $ENDPOINT → ${BACKUP_R2_BUCKET}/${PREFIX}"

# --- Copy ---------------------------------------------------------------------
# `copy`, never `sync`: sync mirrors deletions, and the local side keeps only 7
# daily while R2 keeps 30. A sync would faithfully delete three weeks of history
# every night, which is the opposite of what off-site storage is for.
#
# Only *.sql.gz.enc — see the note about the files tarball at the top.
copied=0
copy_dir() {
  local sub="$1"
  [ -d "$BACKUP_DIR/$sub" ] || { log "  no $sub/ directory — skipping"; return 0; }

  local args=(copy "$BACKUP_DIR/$sub" "$REMOTE/$sub"
              --include "*.sql.gz.enc" --transfers 2 --retries 3
              --stats-one-line --stats 0)
  [ -n "$DRY_RUN" ] && args+=(--dry-run)

  log "  copying $sub/ …"
  if ! "$RCLONE" "${args[@]}" 2>&1 | while read -r l; do [ -n "$l" ] && log "    $l"; done; then
    log "  ERROR: rclone copy of $sub/ failed"
    return 1
  fi
  return 0
}

fatal=0
copy_dir daily  || fatal=1
copy_dir weekly || fatal=1

if [ "$fatal" -ne 0 ]; then
  log "ERROR: R2 off-site FAILED — tonight's dumps exist only on this box."
  exit 1
fi

# --- Verify the copy actually landed -----------------------------------------
# 🛑 A GREEN rclone EXIT IS NOT PROOF THE FILE IS THERE. `copy` exits 0 when it
# has nothing to do, which is also what it does when the include pattern stops
# matching — a rename of the dump would silently upload nothing for ever and
# report success. So assert the newest local dump by name on the far side.
#
# 🛑 AND NOT THROUGH A PIPE INTO `grep -q`, WHICH INVERTS THIS CHECK. `grep -q`
# exits the instant it matches and closes the pipe; rclone then dies of SIGPIPE
# with status 141, and `pipefail` makes 141 the PIPELINE's status — so the
# condition reads FALSE exactly when the file IS found. The script would then
# announce "off-site is NOT working" about a file sitting right there, exit 1,
# and skip the expiry (because `copied` stays 0), so the store grows without
# bound behind a red heartbeat. Measured 2026-08-29: it fires from as few as
# five listed objects, and whether it fires at all depends on rclone finishing
# its write before grep quits — silent while a prefix is small, permanent once
# it is not. Found by elastic-wiles-412855 porting this to Harbour.
#
# So: no pipe, and nothing that can short-circuit. Read the listing into a
# variable and match it in the shell.
if [ -z "$DRY_RUN" ]; then
  # `|| true` is load-bearing on BOTH lines. Under `set -e` with `pipefail` an
  # assignment whose pipeline fails kills the script on the spot — so with no
  # local dump, `ls` exits 2 and the "WARNING: no local dump" branch below could
  # never be reached. The run died between the last copy and any verification,
  # leaving a red heartbeat above a log that says nothing about why.
  newest=$(ls -t "$BACKUP_DIR"/daily/ojs-*.sql.gz.enc 2>/dev/null | head -1 || true)
  if [ -n "$newest" ]; then
    name=$(basename "$newest")
    listing=$("$RCLONE" lsf "$REMOTE/daily/" 2>/dev/null || true)
    case $'\n'"$listing"$'\n' in
      *$'\n'"$name"$'\n'*)
        log "  verified in R2: daily/$name"
        copied=1
        ;;
      *)
        log "ERROR: $name is not in R2 after the copy — off-site is NOT working."
        exit 1
        ;;
    esac
  else
    log "WARNING: no local daily OJS dump to verify against"
  fi
fi

# --- Expire old objects -------------------------------------------------------
# Only after a verified upload. A run that could not put tonight's dump in R2
# has no business deleting the dumps that are already there.
expire() {
  local sub="$1" days="$2" count listing
  # Same trap as the verification above: an unguarded assignment from a failing
  # pipeline kills the whole script under `set -e` + `pipefail`. A momentary R2
  # error here would have taken the run down without a word rather than skipping
  # one expiry, so read it defensively and let the floor below decide.
  listing=$("$RCLONE" lsf "$REMOTE/$sub/" 2>/dev/null || true)
  count=$(printf '%s' "$listing" | grep -c . || true)
  if [ "${count:-0}" -le "$EXPIRE_FLOOR" ]; then
    log "  $sub/: $count objects, at or below the floor of $EXPIRE_FLOOR — not expiring"
    return 0
  fi
  # 🛑 THE CAP IS COUNTED HERE, BEFORE ANYTHING IS DELETED, NOT LEFT TO
  # rclone's --max-delete. The floor above is only the cheap half. In a steady
  # state two objects age out of daily/ per night (ojs and wp), so a run with
  # more than MAX_DELETE due has misunderstood something: a clock jump,
  # a changed prefix, a restored-from-cold store whose mtimes are all old. It
  # deletes none of them, says so, and turns the night red, and the weekly
  # expiry is not attempted. The cause is still there tomorrow, so tomorrow
  # refuses and is red too, until someone looks.
  #
  # 🛑 rclone's own cap cannot do this job. `delete --max-delete 10` deletes
  # ten, then each of its 8 checkers takes one more object, fails fatally and
  # stops reading; once its 8-slot queue is full the listing blocks for ever.
  # Measured with 1.73.2: 26 due deletes ten and exits 7, 27 due never exits,
  # and operations.Delete in v1.75.0 has the same unguarded send. A clock jump
  # puts some 90 objects past the age in daily/, so the 03:00 backup would hang
  # with nothing in the log after "expiring daily/", and no heartbeat ping at
  # all. Under 27 it exits 7, which a `|| true` on the delete would turn into a
  # green night.
  #
  # The flag stays on the delete as a backstop for a count that went stale in
  # the seconds between, and its exit 7 is the same alarm. Any OTHER failure of
  # the delete or of the count stays a warning: nothing is lost, and the next
  # night tries again.
  local due n
  if ! due=$("$RCLONE" lsf -R --files-only --min-age "${days}d" "$REMOTE/$sub/" 2>/dev/null); then
    log "  WARNING: could not list what is due in $sub/, so not expiring it tonight."
    return 0
  fi
  n=$(printf '%s' "$due" | grep -c . || true)
  if [ "${n:-0}" -gt "$MAX_DELETE" ]; then
    log "ERROR: $n objects in $sub/ are older than ${days}d, more than the $MAX_DELETE a night may delete. Deleting none of them."
    capped="$sub"
    return 0
  fi
  local args=(delete "$REMOTE/$sub/" --min-age "${days}d" --retries 3 --max-delete "$MAX_DELETE")
  [ -n "$DRY_RUN" ] && args+=(--dry-run)
  log "  expiring $sub/ older than ${days}d ($n of $count objects)…"
  local out rc=0 l
  out=$("$RCLONE" "${args[@]}" 2>&1) || rc=$?
  # `if`, not `[ -n ] && log`: a loop whose last line is blank would return 1.
  while IFS= read -r l; do if [ -n "$l" ]; then log "    $l"; fi; done <<< "$out"
  if [ "$rc" -eq 7 ] || [[ "$out" == *"max-delete threshold reached"* ]]; then
    log "ERROR: rclone's own --max-delete cap stopped the expiry of $sub/ (see above)."
    capped="$sub"
  elif [ "$rc" -ne 0 ]; then
    log "  WARNING: expiring $sub/ failed (rclone exit $rc). Nothing was lost; the next night tries again."
  fi
  return 0
}

capped=""

if [ -n "$DRY_RUN" ] || [ "$copied" = "1" ]; then
  expire daily  "$KEEP_DAILY_DAYS"
  if [ -n "$capped" ]; then
    log "  not expiring weekly/: daily/ says something is wrong, so nothing more is deleted tonight."
  else
    expire weekly "$KEEP_WEEKLY_DAYS"
  fi
else
  log "  skipping expiry: nothing was verified as uploaded this run"
fi

if [ -n "$capped" ]; then
  if [ -n "$DRY_RUN" ]; then
    log "ERROR: a real run would refuse to expire $capped/ (see above). See docs/setup-guide.md."
  else
    log "ERROR: R2 expiry refused on $capped/: more was due than a night may delete (see above)."
    log "  Tonight's dumps are in R2. Each night refuses, and is red, until the cause is found."
    log "  See docs/setup-guide.md, Off-site backups."
  fi
  exit "$EXPIRY_CAPPED_EXIT"
fi

log "R2 off-site complete."
