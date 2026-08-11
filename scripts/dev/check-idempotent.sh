#!/bin/bash
# Check that the generated backfill output can be reproduced from its own source.
#
# Why this exists: the committed output used to disagree with what the pipeline
# produces from the committed raw.html, because articles had been fixed by hand
# downstream. Nothing detected that. It stayed invisible until someone reran a
# volume, at which point the hand-fix vanished and the defect came back — 12.1
# turned five paragraphs of a book review into references that way on
# 2026-08-10, and 47 of 70 volumes were in the same state.
#
# So: regenerate, compare, restore. A volume that does not reproduce is a
# landmine for whoever reprocesses it next, whether or not the live site is
# currently correct.
#
# Usage:
#   scripts/dev/check-idempotent.sh                 # every volume
#   scripts/dev/check-idempotent.sh 12.1 37.2       # named volumes
#   scripts/dev/check-idempotent.sh --keep 12.1     # leave the regenerated files in place
#
# Exit codes: 0 reproduces exactly, 1 drift found, 2 setup problem.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="$REPO_ROOT/backfill/private/output"
PRIVATE_REPO="$REPO_ROOT/private"
PYTHON="$REPO_ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3

KEEP=0
VOLUMES=()
set +u
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) VOLUMES+=("$arg") ;;
  esac
done

if [ ! -d "$PRIVATE_REPO/.git" ]; then
  echo "ERROR: $PRIVATE_REPO is not a git repo — nothing to compare against." >&2
  exit 2
fi

# Refuse to run over uncommitted work: the comparison is against git, and the
# restore at the end would throw that work away.
if ! git -C "$PRIVATE_REPO" diff --quiet -- backfill/output/; then
  echo "ERROR: backfill/output has uncommitted changes." >&2
  echo "       Commit or stash them first — this script restores from git when it finishes." >&2
  exit 2
fi

TOCS=()
if [ ${#VOLUMES[@]} -eq 0 ]; then
  # No mapfile: macOS ships bash 3.2.
  while IFS= read -r toc; do
    TOCS+=("$toc")
  done < <(find "$OUTPUT_DIR" -mindepth 2 -maxdepth 2 -name toc.json | sort)
else
  for v in "${VOLUMES[@]}"; do
    [ -f "$OUTPUT_DIR/$v/toc.json" ] && TOCS+=("$OUTPUT_DIR/$v/toc.json") \
      || { echo "ERROR: no such volume: $v" >&2; exit 2; }
  done
fi

echo "Regenerating ${#TOCS[@]} volume(s) from source..."
for toc in "${TOCS[@]}"; do
  "$PYTHON" "$REPO_ROOT/backfill/html_pipeline/pipe2_postprocess.py" "$toc" >/dev/null 2>&1
done

"$PYTHON" - "$PRIVATE_REPO" <<'PY'
import html, re, subprocess, sys
repo = sys.argv[1]
def norm(t): return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', t)).split())

changed = subprocess.run(['git', '-C', repo, 'diff', '--name-only', '--', 'backfill/output/'],
                         capture_output=True, text=True).stdout.split()
changed = [f for f in changed if f.endswith('.post.html')]

content, markup = [], []
for f in changed:
    old = subprocess.run(['git', '-C', repo, 'show', f'HEAD:{f}'],
                         capture_output=True, text=True).stdout
    with open(f'{repo}/{f}', encoding='utf-8') as fh:
        new = fh.read()
    (markup if norm(old) == norm(new) else content).append((f, len(norm(new)) - len(norm(old))))

if not changed:
    print('\nPASS — every volume reproduces exactly from its own source.')
    sys.exit(0)

print(f'\nDRIFT — {len(changed)} file(s) do not reproduce:\n')
if content:
    print(f'  {len(content)} with CONTENT differences (a rerun would change what readers see):')
    for f, d in sorted(content, key=lambda x: abs(x[1]), reverse=True):
        vol, name = f.split('/')[-2], f.split('/')[-1]
        print(f'    {d:+7} chars  {vol:6} {name[:58]}')
if markup:
    print(f'\n  {len(markup)} with markup-only differences (visible text identical):')
    for f, _ in markup[:10]:
        print(f'             {f.split("/")[-2]:6} {f.split("/")[-1][:58]}')
    if len(markup) > 10:
        print(f'             ... and {len(markup) - 10} more')
print('\n  A file listed here is fixed downstream of the pipeline, so the fix is lost')
print('  on the next rerun. Fix it in raw.html or in lib/postprocess.py, not in the output.')
sys.exit(1)
PY
STATUS=$?

if [ "$KEEP" -eq 0 ]; then
  git -C "$PRIVATE_REPO" checkout -- backfill/output/
  echo "(regenerated files restored from git)"
else
  echo "(--keep: regenerated files left in place)"
fi

exit $STATUS
