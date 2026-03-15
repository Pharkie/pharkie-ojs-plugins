#!/bin/bash
# Split a whole-issue PDF into per-article PDFs and OJS import XML.
#
# Requires toc.json to already exist in backfill/output/<vol>.<iss>/.
# See docs/backfill-toc-guide.md for how to create toc.json files.
#
# Usage:
#   backfill/split-issue.sh <issue.pdf>                    # Split one issue
#   backfill/split-issue.sh /path/to/pdf-folder            # Split all PDFs in folder
#   backfill/split-issue.sh <issue.pdf> --no-pdfs           # XML without embedded PDFs (fast, for testing XML structure)
#   backfill/split-issue.sh <issue.pdf> --only=split        # Run one step only
#   backfill/split-issue.sh <issue.pdf> --stop-after=normalize  # Run through normalize, export review CSV, stop
#
# Steps (run in order):
#   preflight    — validate PDF is readable, extract vol/issue
#   split        — split issue PDF into one PDF per article
#   verify_split — check each split PDF's first page matches its TOC title
#   normalize    — normalize author names
#   generate_xml — generate OJS Native XML with base64-embedded PDFs
#
# Output: backfill/output/<vol>.<iss>/
#   toc.json (pre-existing), per-article PDFs, import.xml
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"

CLEANUP_FILES=()
cleanup() { rm -f "${CLEANUP_FILES[@]}"; }
trap cleanup EXIT

# --- Parse arguments ---
VALID_STEPS="preflight split verify_split normalize generate_xml"

PDFS=()
NO_PDFS=""
ONLY_STEP=""
STOP_AFTER=""
for arg in "$@"; do
  case "$arg" in
    --no-pdfs) NO_PDFS="--no-pdfs" ;;
    --only=*)
      ONLY_STEP="${arg#--only=}"
      if ! echo "$VALID_STEPS" | grep -qw "$ONLY_STEP"; then
        echo "ERROR: Unknown step '$ONLY_STEP'"
        echo "Valid steps: $VALID_STEPS"
        exit 1
      fi
      ;;
    --stop-after=*)
      STOP_AFTER="${arg#--stop-after=}"
      if ! echo "$VALID_STEPS" | grep -qw "$STOP_AFTER"; then
        echo "ERROR: Unknown step '$STOP_AFTER'"
        echo "Valid steps: $VALID_STEPS"
        exit 1
      fi
      ;;
    --help|-h)
      sed -n '2,/^set -eo/p' "$0" | head -n -1 | sed 's/^# \?//'
      exit 0
      ;;
    *) PDFS+=("$arg") ;;
  esac
done

if [ ${#PDFS[@]} -eq 0 ]; then
  echo "Usage: backfill/split-issue.sh <issue.pdf|folder> [--no-pdfs] [--only=<step>] [--stop-after=<step>]"
  echo "Steps: $VALID_STEPS"
  exit 1
fi

# Expand folder to list of PDFs
EXPANDED_PDFS=()
for path in "${PDFS[@]}"; do
  if [ -d "$path" ]; then
    while IFS= read -r f; do
      EXPANDED_PDFS+=("$f")
    done < <(find "$path" -maxdepth 1 -name '*.pdf' -type f | sort)
  elif [ -f "$path" ]; then
    EXPANDED_PDFS+=("$path")
  else
    echo "ERROR: $path not found"
    exit 1
  fi
done

if [ ${#EXPANDED_PDFS[@]} -eq 0 ]; then
  echo "No PDF files found"
  exit 1
fi

echo "=========================================="
echo "Prepare: ${#EXPANDED_PDFS[@]} PDF(s)"
echo "Output: $OUTPUT_DIR"
[ -n "$NO_PDFS" ] && echo "Mode: --no-pdfs (XML without embedded PDFs)"
[ -n "$ONLY_STEP" ] && echo "Step: $ONLY_STEP only"
[ -n "$STOP_AFTER" ] && echo "Stop after: $STOP_AFTER"
echo "=========================================="
echo

should_run() {
  [ -z "$ONLY_STEP" ] || [ "$ONLY_STEP" = "$1" ]
}

# Issue directory name: "<vol>.<iss>" for multi-issue volumes, "<vol>" for single-issue
issue_dir_name() {
  local v="$1" s="$2"
  if [ "$v" -le 5 ] && [ "$s" -eq 1 ]; then
    echo "$v"
  else
    echo "${v}.${s}"
  fi
}

should_stop_after() {
  [ -n "$STOP_AFTER" ] && [ "$STOP_AFTER" = "$1" ]
}

FAILED=0
SUCCEEDED=0

PDF_NUM=0
for PDF in "${EXPANDED_PDFS[@]}"; do
  PDF_NUM=$((PDF_NUM + 1))
  PDF_ABS="$(cd "$(dirname "$PDF")" && pwd)/$(basename "$PDF")"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Processing ($PDF_NUM/${#EXPANDED_PDFS[@]}): $(basename "$PDF")"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Step 1: Preflight
  if should_run "preflight"; then
    echo
    echo "--- Step 1: Preflight ---"
    PREFLIGHT_TMP=$(mktemp /tmp/preflight-XXXXXX.json)
    CLEANUP_FILES+=("$PREFLIGHT_TMP")
    if ! python3 "$SCRIPT_DIR/preflight.py" "$PDF_ABS" > "$PREFLIGHT_TMP"; then
      echo "  ERROR: Preflight failed, skipping this PDF"
      rm -f "$PREFLIGHT_TMP"
      FAILED=$((FAILED + 1))
      continue
    fi
    if python3 -c "
import sys, json
data = json.load(open(sys.argv[1]))
errors = sum(len(r.get('errors', [])) for r in data)
sys.exit(1 if errors else 0)
" "$PREFLIGHT_TMP"; then
      echo "  Preflight: OK"
    else
      echo "  ERROR: Preflight failed, skipping this PDF"
      rm -f "$PREFLIGHT_TMP"
      FAILED=$((FAILED + 1))
      continue
    fi
    rm -f "$PREFLIGHT_TMP"
  fi

  if should_stop_after "preflight"; then
    SUCCEEDED=$((SUCCEEDED + 1))
    continue
  fi

  # Detect vol/iss from PDF to find output dir
  VOL=$(python3 -c "
import fitz, re, sys
doc = fitz.open(sys.argv[1])
for i in range(min(3, len(doc))):
    m = re.search(r'(\d{1,2})\.(\d{1,2})', doc[i].get_text())
    if m:
        v, s = int(m.group(1)), int(m.group(2))
        if 1 <= v <= 50 and 1 <= s <= 4:
            print(v); sys.exit()
for i in range(min(3, len(doc))):
    m = re.search(r'Analysis\s+(\d{1,2})\s', doc[i].get_text(), re.IGNORECASE)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 50:
            print(v); sys.exit()
print(0)
" "$PDF_ABS")
  ISS=$(python3 -c "
import fitz, re, sys
doc = fitz.open(sys.argv[1])
for i in range(min(3, len(doc))):
    m = re.search(r'(\d{1,2})\.(\d{1,2})', doc[i].get_text())
    if m:
        v, s = int(m.group(1)), int(m.group(2))
        if 1 <= v <= 50 and 1 <= s <= 4:
            print(s); sys.exit()
for i in range(min(3, len(doc))):
    m = re.search(r'Analysis\s+(\d{1,2})\s', doc[i].get_text(), re.IGNORECASE)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 50:
            print(1); sys.exit()
print(0)
" "$PDF_ABS")
  ISSUE_DIR="$OUTPUT_DIR/$(issue_dir_name "$VOL" "$ISS")"

  TOC_JSON="$ISSUE_DIR/toc.json"
  if [ ! -f "$TOC_JSON" ]; then
    echo "  ERROR: No toc.json found at $TOC_JSON"
    echo "  Create it first — see docs/backfill-toc-guide.md"
    FAILED=$((FAILED + 1))
    continue
  fi

  # Update source_pdf path in toc.json to point to the current PDF
  python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
data['source_pdf'] = sys.argv[2]
with open(sys.argv[1], 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
" "$TOC_JSON" "$PDF_ABS"
  echo "  Volume $VOL, Issue $ISS → $ISSUE_DIR"

  # Step 2: Split PDF
  if should_run "split"; then
    echo
    echo "--- Step 2: Split PDF ---"
    if ! python3 "$SCRIPT_DIR/split.py" "$TOC_JSON" -o "$OUTPUT_DIR"; then
      echo "  ERROR: PDF splitting failed"
      FAILED=$((FAILED + 1))
      continue
    fi
    TOC_JSON="$ISSUE_DIR/toc.json"
  fi

  if should_stop_after "split"; then
    SUCCEEDED=$((SUCCEEDED + 1))
    continue
  fi

  # Step 2b: Verify split PDFs match TOC titles
  if should_run "verify_split"; then
    echo
    echo "--- Step 2b: Verify split ---"
    if ! python3 "$SCRIPT_DIR/verify_split.py" "$TOC_JSON"; then
      echo "  WARNING: Some split PDFs don't match their TOC titles"
      echo "  Check page offsets and TOC entries before importing."
    fi
  fi

  if should_stop_after "verify_split"; then
    SUCCEEDED=$((SUCCEEDED + 1))
    continue
  fi

  # Step 3: Normalize authors
  if should_run "normalize"; then
    echo
    echo "--- Step 3: Normalize authors ---"
    python3 "$SCRIPT_DIR/author_normalize.py" "$TOC_JSON"
  fi

  # Stop after normalize: export review CSV
  if should_stop_after "normalize"; then
    REVIEW_CSV="$ISSUE_DIR/review.csv"
    echo
    echo "--- Exporting review CSV ---"
    python3 "$SCRIPT_DIR/export_review.py" "$TOC_JSON" -o "$REVIEW_CSV"
    echo
    echo "  Review CSV: $REVIEW_CSV"
    SUCCEEDED=$((SUCCEEDED + 1))
    continue
  fi

  # Step 4: Generate XML
  if should_run "generate_xml"; then
    echo
    echo "--- Step 4: Generate OJS XML ---"
    XML_OUT="$ISSUE_DIR/import.xml"
    if ! python3 "$SCRIPT_DIR/generate_xml.py" "$TOC_JSON" -o "$XML_OUT" $NO_PDFS; then
      echo "  ERROR: XML generation failed"
      FAILED=$((FAILED + 1))
      continue
    fi
  fi

  SUCCEEDED=$((SUCCEEDED + 1))
  echo
  echo "  Done: $(basename "$PDF") → $ISSUE_DIR"
  echo "  To import: backfill/import.sh $ISSUE_DIR"
done

echo
echo "=========================================="
echo "Complete: $SUCCEEDED succeeded, $FAILED failed out of ${#EXPANDED_PDFS[@]}"
echo "=========================================="

[ $FAILED -eq 0 ] || exit 1
