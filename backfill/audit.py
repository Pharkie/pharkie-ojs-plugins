#!/usr/bin/env python3
"""
Audit all archive PDFs — produce a structured report of every issue's
readiness for the backfill pipeline.

Usage:
    python backfill/audit.py "data export/journal archive/Pre 2018/"*.pdf \
                             "data export/journal archive/2018-25/"*.pdf

Checks per PDF:
  - Basic info (pages, dimensions, file size)
  - Text extractability (total chars, chars on first 5 pages, TEXT/SCAN)
  - Cover detection ("Existential Analysis" or vol/issue on page 1)
  - TOC detection (which page has "CONTENTS"?)
  - Volume/issue extraction from filename and page text
  - Page orientation and rotation flags
  - Dimension consistency across pages
  - Preflight compatibility (would preflight.py pass?)

Outputs:
  backfill/output/audit-report.md   — human-readable markdown
  backfill/output/audit-report.json — machine-readable
"""

import sys
import os
import json
import re
import fitz  # PyMuPDF


def parse_vol_issue_from_filename(filename):
    """Extract volume and issue from filename patterns like 6.1.pdf or 29.1.pdf."""
    base = os.path.splitext(filename)[0]
    # Pattern: 29.1 or 6.1
    m = re.match(r'(\d+)\.(\d+)', base)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def extract_vol_issue_from_text(doc):
    """Try to extract volume/issue from first few pages of text."""
    for i in range(min(3, len(doc))):
        text = doc[i].get_text()
        # Pattern: "37.1" style
        m = re.search(r'(\d{1,2})\.(\d{1,2})', text)
        if m:
            vol, iss = int(m.group(1)), int(m.group(2))
            if 1 <= vol <= 50 and 1 <= iss <= 4:
                return vol, iss
        # Pattern: "Volume 37, Issue 1" style
        m = re.search(
            r'Vol(?:ume)?\.?\s*(\d+)\s*(?:No|Issue|Iss)\.?\s*(\d+)',
            text, re.IGNORECASE
        )
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


def check_cover(doc):
    """Check if page 1 looks like a cover page."""
    if len(doc) == 0:
        return False, ""
    text = doc[0].get_text()
    has_ea = bool(re.search(r'Existential\s+Analysis', text, re.IGNORECASE))
    has_vol = bool(re.search(r'Vol(?:ume)?\.?\s*\d+', text, re.IGNORECASE))
    has_journal_id = bool(re.search(r'\d{1,2}\.\d{1,2}', text))
    detected = has_ea or has_vol or has_journal_id
    snippet = text[:200].replace('\n', ' ').strip() if text.strip() else "(no text)"
    return detected, snippet


def find_toc_page(doc):
    """Find which page contains CONTENTS. Returns (0-based index, format)."""
    for i in range(min(10, len(doc))):
        text = doc[i].get_text()
        if re.search(r'^[-\s]*Contents[-\s]*$', text, re.IGNORECASE | re.MULTILINE):
            return i, "standard"
    return None, None


def measure_text(doc):
    """Measure text extractability across the document."""
    total_chars = 0
    first5_chars = 0
    per_page = []
    for i in range(len(doc)):
        chars = len(doc[i].get_text().strip())
        total_chars += chars
        if i < 5:
            first5_chars += chars
        per_page.append(chars)

    # Classification: if average chars per page in content area is < 50, it's a scan
    content_pages = per_page[2:] if len(per_page) > 2 else per_page
    avg_content = sum(content_pages) / max(len(content_pages), 1)
    classification = "TEXT" if avg_content > 100 else "SCAN"

    return {
        "total_chars": total_chars,
        "first5_chars": first5_chars,
        "avg_chars_per_page": round(total_chars / max(len(doc), 1)),
        "avg_content_chars": round(avg_content),
        "classification": classification,
    }


def check_orientation(doc):
    """Check page orientations and rotation flags."""
    landscape_pages = []
    rotated_pages = []
    for i in range(len(doc)):
        page = doc[i]
        rect = page.rect
        w, h = rect.width, rect.height
        if w > h:
            landscape_pages.append(i)
        if page.rotation != 0:
            rotated_pages.append((i, page.rotation))
    return landscape_pages, rotated_pages


def check_dimensions(doc, tolerance=5.0):
    """Check page dimension consistency.

    tolerance: max point difference to consider dimensions "close enough".
    Scanned PDFs routinely vary by 1-2pts — only flag truly different sizes.
    """
    dims = []
    for i in range(len(doc)):
        rect = doc[i].rect
        dims.append((round(rect.width, 1), round(rect.height, 1)))
    unique = list(set(dims))
    # Get the most common dimension
    from collections import Counter
    counts = Counter(dims)
    most_common = counts.most_common(1)[0] if counts else ((0, 0), 0)
    primary_w, primary_h = most_common[0]

    # Check if all dimensions are within tolerance of the primary
    all_close = all(
        abs(w - primary_w) <= tolerance and abs(h - primary_h) <= tolerance
        for w, h in unique
    )

    return {
        "primary": f"{primary_w}x{primary_h}",
        "primary_count": most_common[1],
        "total_pages": len(doc),
        "consistent": len(unique) == 1,
        "within_tolerance": all_close,
        "unique_dimensions": [f"{w}x{h}" for w, h in unique],
    }


def run_preflight_check(doc, filepath):
    """Simulate preflight.py checks without importing it."""
    errors = []
    warnings = []

    # Page count check
    if len(doc) < 20:
        warnings.append(f"Only {len(doc)} pages — unusually short")
    if len(doc) > 400:
        warnings.append(f"{len(doc)} pages — unusually long")

    # Text extraction check (same logic as preflight.py)
    total_chars = 0
    pages_checked = 0
    start = min(5, len(doc) - 1)
    for i in range(start, min(start + 5, len(doc))):
        text = doc[i].get_text()
        total_chars += len(text.strip())
        pages_checked += 1
    avg_chars = total_chars / max(pages_checked, 1)
    if avg_chars <= 100:
        errors.append("Low text content — scanned/OCR needed")

    # TOC detection
    toc_found = False
    for i in range(min(10, len(doc))):
        text = doc[i].get_text()
        if re.search(r'^[-\s]*Contents[-\s]*$', text, re.IGNORECASE | re.MULTILINE):
            toc_found = True
            break
    if not toc_found:
        warnings.append("No CONTENTS page found")

    # Vol/issue detection
    vol, iss = extract_vol_issue_from_text(doc)
    if vol is None:
        warnings.append("Could not detect volume/issue from text")

    would_pass = len(errors) == 0
    return {
        "would_pass": would_pass,
        "errors": errors,
        "warnings": warnings,
    }


def audit_pdf(filepath):
    """Run full audit on a single PDF."""
    filename = os.path.basename(filepath)
    result = {
        "file": filename,
        "path": os.path.abspath(filepath),
        "size_mb": round(os.path.getsize(filepath) / (1024 * 1024), 1),
    }

    try:
        doc = fitz.open(filepath)
    except Exception as e:
        result["error"] = f"Cannot open PDF: {e}"
        result["status"] = "ERROR"
        return result

    result["pages"] = len(doc)

    # Basic dimensions
    dims = check_dimensions(doc)
    result["dimensions"] = dims

    # Text analysis
    text_info = measure_text(doc)
    result["text"] = text_info

    # Cover detection
    has_cover, cover_snippet = check_cover(doc)
    result["cover"] = {
        "detected": has_cover,
        "page1_snippet": cover_snippet,
    }

    # TOC detection
    toc_page, toc_format = find_toc_page(doc)
    result["toc"] = {
        "page": toc_page,
        "format": toc_format,
    }

    # Volume/issue from filename vs text
    fn_vol, fn_iss = parse_vol_issue_from_filename(filename)
    text_vol, text_iss = extract_vol_issue_from_text(doc)
    match = None
    if fn_vol is not None and text_vol is not None:
        match = (fn_vol == text_vol) and (fn_iss == text_iss or fn_iss is None)
    result["vol_issue"] = {
        "from_filename": f"{fn_vol}.{fn_iss}" if fn_iss else str(fn_vol),
        "from_text": f"{text_vol}.{text_iss}" if text_vol else None,
        "match": match,
    }

    # Orientation check
    landscape_pages, rotated_pages = check_orientation(doc)
    result["orientation"] = {
        "landscape_pages": len(landscape_pages),
        "landscape_indices": landscape_pages[:10],  # cap for readability
        "rotated_pages": [(idx, rot) for idx, rot in rotated_pages[:10]],
    }

    # Preflight compatibility
    result["preflight"] = run_preflight_check(doc, filepath)

    # Overall status — separate pipeline blockers from informational notes
    problems = []  # blockers: prevent pipeline from processing this PDF
    notes = []     # informational: worth knowing but won't block pipeline

    if text_info["classification"] == "SCAN":
        problems.append("SCAN (no extractable text)")
    if toc_page is None:
        # Only a blocker if the PDF has text (scans already flagged above)
        if text_info["classification"] == "TEXT":
            problems.append("no CONTENTS page")
        else:
            pass  # already covered by SCAN flag
    if len(landscape_pages) > 0:
        problems.append(f"{len(landscape_pages)} landscape pages")
    if not result["preflight"]["would_pass"] and text_info["classification"] == "TEXT":
        problems.append("preflight would fail")

    # Informational notes (don't block pipeline)
    if not has_cover:
        if text_info["classification"] == "TEXT":
            notes.append("no cover detected (vol/issue from filename OK)")
        # Scans already flagged — no cover is expected
    if not dims["consistent"] and not dims["within_tolerance"]:
        notes.append(f"mixed page dimensions ({', '.join(dims['unique_dimensions'])})")
    elif not dims["consistent"]:
        notes.append("minor dimension variance (within tolerance)")

    if problems:
        result["status"] = "PROBLEM"
        result["problems"] = problems
    else:
        result["status"] = "OK"
        result["problems"] = []
    result["notes"] = notes

    doc.close()
    return result


def _coverage_section(results):
    """Generate coverage/gap analysis lines for the markdown report."""
    lines = []
    # Parse all vol.issue combos
    single_vols = set()
    paired = {}
    for r in results:
        vi = r["vol_issue"]["from_filename"]
        if "." in vi:
            parts = vi.split(".")
            vol, iss = int(parts[0]), int(parts[1])
            paired.setdefault(vol, set()).add(iss)
        else:
            try:
                single_vols.add(int(vi))
            except ValueError:
                pass

    all_vols = single_vols | set(paired.keys())
    if not all_vols:
        return lines

    min_v, max_v = min(all_vols), max(all_vols)
    total_issues = len(single_vols) + sum(len(v) for v in paired.values())

    lines.append("## Coverage\n")
    lines.append(f"**Volumes {min_v}–{max_v}** ({total_issues} issues across {len(results)} PDFs)\n")
    lines.append(f"- Single-issue volumes: {', '.join(str(v) for v in sorted(single_vols))}")
    lines.append(f"- Two-issue volumes: {min(paired)}–{max(paired)}")
    lines.append("")

    # Find gaps
    missing = []
    for v in range(min_v, max_v + 1):
        if v in single_vols:
            continue
        if v in paired:
            for iss in (1, 2):
                if iss not in paired[v]:
                    missing.append(f"Vol {v} Issue {iss}")
        else:
            missing.append(f"Vol {v} (no issues at all)")

    if missing:
        lines.append(f"**Missing ({len(missing)}):**\n")
        for m in missing:
            lines.append(f"- {m}")
    else:
        lines.append("**No gaps** — all expected issues present.")
    lines.append("")
    return lines


def generate_markdown(results, output_path):
    """Generate a human-readable markdown report."""
    ok = [r for r in results if r["status"] == "OK"]
    problems = [r for r in results if r["status"] == "PROBLEM"]
    errors = [r for r in results if r["status"] == "ERROR"]

    lines = []
    lines.append("# PDF Archive Audit Report\n")
    lines.append(f"**Total PDFs:** {len(results)}\n")
    lines.append(f"- OK: {len(ok)}")
    lines.append(f"- Problems: {len(problems)}")
    lines.append(f"- Errors: {len(errors)}")
    lines.append("")

    # Coverage analysis
    lines.extend(_coverage_section(results))

    # Problem PDFs section
    if problems or errors:
        lines.append("## Problem PDFs\n")
        lines.append("| File | Pages | Size | Status | Problems |")
        lines.append("|------|-------|------|--------|----------|")
        for r in sorted(problems + errors, key=lambda x: x["file"]):
            if r["status"] == "ERROR":
                lines.append(
                    f"| {r['file']} | — | {r['size_mb']}MB "
                    f"| ERROR | {r.get('error', '?')} |"
                )
            else:
                prob_str = "; ".join(r["problems"])
                lines.append(
                    f"| {r['file']} | {r['pages']} | {r['size_mb']}MB "
                    f"| PROBLEM | {prob_str} |"
                )
        lines.append("")

    # Detailed problem analysis
    if problems:
        lines.append("### Problem Details\n")
        for r in sorted(problems, key=lambda x: x["file"]):
            lines.append(f"**{r['file']}** (Vol {r['vol_issue']['from_filename']})")
            lines.append(f"- Pages: {r['pages']}, Size: {r['size_mb']}MB")
            lines.append(f"- Text: {r['text']['classification']} "
                         f"(avg {r['text']['avg_content_chars']} chars/page)")
            lines.append(f"- Cover: {'yes' if r['cover']['detected'] else 'no'}")
            toc = r['toc']
            lines.append(f"- TOC: {'page ' + str(toc['page']) if toc['page'] is not None else 'none'}")
            if r["orientation"]["landscape_pages"] > 0:
                lines.append(f"- Landscape pages: {r['orientation']['landscape_pages']}")
            if not r["dimensions"]["consistent"]:
                lines.append(f"- Dimensions: {', '.join(r['dimensions']['unique_dimensions'])}")
            lines.append(f"- Problems: {', '.join(r['problems'])}")
            lines.append("")

    # OK PDFs summary table
    lines.append("## Compatible PDFs\n")
    lines.append("| File | Vol.Iss | Pages | Size | Text | TOC | Cover | Preflight |")
    lines.append("|------|---------|-------|------|------|-----|-------|-----------|")
    for r in sorted(ok, key=lambda x: x["file"]):
        vi = r["vol_issue"]["from_filename"]
        toc_str = f"p{r['toc']['page']}" if r['toc']['page'] is not None else "—"
        pf = "pass" if r["preflight"]["would_pass"] else "FAIL"
        lines.append(
            f"| {r['file']} | {vi} | {r['pages']} | {r['size_mb']}MB "
            f"| {r['text']['classification']} | {toc_str} "
            f"| {'yes' if r['cover']['detected'] else 'no'} | {pf} |"
        )
    lines.append("")

    # Notes for OK PDFs (informational, not blockers)
    noted = [r for r in ok if r.get("notes") or r["preflight"]["warnings"]]
    if noted:
        lines.append("### Notes (compatible PDFs)\n")
        for r in sorted(noted, key=lambda x: x["file"]):
            all_notes = r.get("notes", []) + [
                f"preflight warning: {w}" for w in r["preflight"]["warnings"]
            ]
            if all_notes:
                lines.append(f"- **{r['file']}**: {'; '.join(all_notes)}")
        lines.append("")

    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))
    return output_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python backfill/audit.py <pdf1> <pdf2> ... or <folder>",
              file=sys.stderr)
        print("  e.g.: python backfill/audit.py \"data export/journal archive/Pre 2018/\"*.pdf",
              file=sys.stderr)
        sys.exit(1)

    # Collect PDF paths from arguments (supports glob-expanded args and directories)
    files = []
    for arg in sys.argv[1:]:
        if os.path.isfile(arg) and arg.lower().endswith('.pdf'):
            files.append(arg)
        elif os.path.isdir(arg):
            files.extend(
                sorted(
                    os.path.join(arg, f)
                    for f in os.listdir(arg)
                    if f.lower().endswith('.pdf')
                )
            )
        else:
            print(f"Warning: skipping {arg} (not a PDF file or directory)",
                  file=sys.stderr)

    if not files:
        print("No PDF files found", file=sys.stderr)
        sys.exit(1)

    # Deduplicate and sort
    files = sorted(set(files))

    print(f"Auditing {len(files)} PDFs...\n", file=sys.stderr)

    results = []
    for f in files:
        name = os.path.basename(f)
        print(f"  [{len(results)+1}/{len(files)}] {name}...", file=sys.stderr)
        results.append(audit_pdf(f))

    # Summary to stderr
    ok = sum(1 for r in results if r["status"] == "OK")
    prob = sum(1 for r in results if r["status"] == "PROBLEM")
    err = sum(1 for r in results if r["status"] == "ERROR")
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Audit complete: {len(results)} PDFs — {ok} OK, {prob} problems, {err} errors",
          file=sys.stderr)
    for r in results:
        icon = "OK" if r["status"] == "OK" else "!!"
        print(f"  [{icon}] {r['file']}", end="", file=sys.stderr)
        if r.get("problems"):
            print(f"  — {', '.join(r['problems'])}", end="", file=sys.stderr)
        elif r.get("error"):
            print(f"  — {r['error']}", end="", file=sys.stderr)
        print(file=sys.stderr)

    # Output files
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)

    json_path = os.path.join(output_dir, "audit-report.json")
    with open(json_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nJSON report: {json_path}", file=sys.stderr)

    md_path = os.path.join(output_dir, "audit-report.md")
    generate_markdown(results, md_path)
    print(f"Markdown report: {md_path}", file=sys.stderr)

    # Also dump JSON to stdout for piping
    json.dump(results, sys.stdout, indent=2)


if __name__ == '__main__':
    main()
