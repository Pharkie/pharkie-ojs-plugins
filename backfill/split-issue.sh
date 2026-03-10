#!/bin/bash
# Split a whole-issue PDF into per-article PDFs and OJS import XML.
#
# Takes one or more issue PDFs → validates → parses TOC → splits into
# individual article PDFs → normalizes author names → generates OJS XML.
# Does NOT touch OJS — use backfill/import.sh to load into OJS.
#
# Usage:
#   backfill/split-issue.sh <issue.pdf>                    # Split one issue
#   backfill/split-issue.sh /path/to/pdf-folder            # Split all PDFs in folder
#   backfill/split-issue.sh <issue.pdf> --no-pdfs           # XML without embedded PDFs (fast, for testing XML structure)
#   backfill/split-issue.sh <issue.pdf> --only=split        # Run one step only
#   backfill/split-issue.sh <issue.pdf> --page-offset=2     # Manual page offset (when auto-detection fails)
#
# Steps (run in order):
#   preflight    — validate PDF is readable, has TOC, extract vol/issue
#   parse_toc    — extract article titles, authors, page ranges, abstracts, keywords
#   split        — split issue PDF into one PDF per article
#   verify_split — check each split PDF's first page matches its TOC title
#   normalize    — normalize author names against registry (backfill/authors.json)
#   generate_xml — generate OJS Native XML with base64-embedded PDFs
#
# Output: backfill/output/EA-vol##-iss#/
#   toc.json, per-article PDFs, import.xml
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"

CLEANUP_FILES=()
cleanup() { rm -f "${CLEANUP_FILES[@]}"; }
trap cleanup EXIT

# --- Parse arguments ---
VALID_STEPS="preflight parse_toc split verify_split normalize generate_xml"

PDFS=()
NO_PDFS=""
ONLY_STEP=""
PAGE_OFFSET=""
for arg in "$@"; do
  case "$arg" in
    --no-pdfs) NO_PDFS="--no-pdfs" ;;
    --page-offset=*)
      PAGE_OFFSET="${arg#--page-offset=}"
      ;;
    --only=*)
      ONLY_STEP="${arg#--only=}"
      if ! echo "$VALID_STEPS" | grep -qw "$ONLY_STEP"; then
        echo "ERROR: Unknown step '$ONLY_STEP'"
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
  echo "Usage: backfill/split-issue.sh <issue.pdf|folder> [--no-pdfs] [--only=<step>]"
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
[ -n "$PAGE_OFFSET" ] && echo "Page offset: $PAGE_OFFSET (manual)"
echo "=========================================="
echo

should_run() {
  [ -z "$ONLY_STEP" ] || [ "$ONLY_STEP" = "$1" ]
}

FAILED=0
SUCCEEDED=0

for PDF in "${EXPANDED_PDFS[@]}"; do
  PDF_ABS="$(cd "$(dirname "$PDF")" && pwd)/$(basename "$PDF")"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Processing: $(basename "$PDF")"
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
    # Check for errors in the JSON output
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

  # Step 2: Parse TOC
  if should_run "parse_toc"; then
    echo
    echo "--- Step 2: Parse TOC ---"
    TEMP_TOC=$(mktemp /tmp/toc-XXXXXX.json)
    CLEANUP_FILES+=("$TEMP_TOC")
    PAGE_OFFSET_ARG=""
    [ -n "$PAGE_OFFSET" ] && PAGE_OFFSET_ARG="--page-offset=$PAGE_OFFSET"
    if ! python3 "$SCRIPT_DIR/parse_toc.py" "$PDF_ABS" -o "$TEMP_TOC" $PAGE_OFFSET_ARG; then
      echo "  ERROR: TOC parsing failed"
      rm -f "$TEMP_TOC"
      FAILED=$((FAILED + 1))
      continue
    fi
    VOL=$(python3 -c "import json, sys; d=json.load(open(sys.argv[1])); print(d.get('volume', 0))" "$TEMP_TOC")
    ISS=$(python3 -c "import json, sys; d=json.load(open(sys.argv[1])); print(d.get('issue', 0))" "$TEMP_TOC")
    ISSUE_DIR="$OUTPUT_DIR/EA-vol$(printf '%02d' "$VOL")-iss${ISS}"
    mkdir -p "$ISSUE_DIR"
    mv "$TEMP_TOC" "$ISSUE_DIR/toc.json"
    echo "  Volume $VOL, Issue $ISS → $ISSUE_DIR"
  else
    # Detect vol/iss from PDF to find existing output dir
    VOL=$(python3 -c "
import fitz, re, sys
doc = fitz.open(sys.argv[1])
for i in range(min(3, len(doc))):
    m = re.search(r'(\d{1,2})\.(\d{1,2})', doc[i].get_text())
    if m:
        v, s = int(m.group(1)), int(m.group(2))
        if 1 <= v <= 50 and 1 <= s <= 4:
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
print(0)
" "$PDF_ABS")
    ISSUE_DIR="$OUTPUT_DIR/EA-vol$(printf '%02d' "$VOL")-iss${ISS}"
  fi

  TOC_JSON="$ISSUE_DIR/toc.json"
  if [ ! -f "$TOC_JSON" ]; then
    echo "  ERROR: No toc.json found at $TOC_JSON"
    FAILED=$((FAILED + 1))
    continue
  fi

  # Step 3: Split PDF
  if should_run "split"; then
    echo
    echo "--- Step 3: Split PDF ---"
    if ! python3 "$SCRIPT_DIR/split.py" "$TOC_JSON" -o "$OUTPUT_DIR"; then
      echo "  ERROR: PDF splitting failed"
      FAILED=$((FAILED + 1))
      continue
    fi
    TOC_JSON="$ISSUE_DIR/toc.json"
  fi

  # Step 3b: Verify split PDFs match TOC titles
  if should_run "verify_split"; then
    echo
    echo "--- Step 3b: Verify split ---"
    if ! python3 "$SCRIPT_DIR/verify_split.py" "$TOC_JSON"; then
      echo "  WARNING: Some split PDFs don't match their TOC titles"
      echo "  Check page offsets and TOC parsing before importing."
    fi
  fi

  # Step 4: Normalize authors
  if should_run "normalize"; then
    echo
    echo "--- Step 4: Normalize authors ---"
    python3 "$SCRIPT_DIR/author_normalize.py" "$TOC_JSON"
  fi

  # Step 5: Generate XML
  if should_run "generate_xml"; then
    echo
    echo "--- Step 5: Generate OJS XML ---"
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
