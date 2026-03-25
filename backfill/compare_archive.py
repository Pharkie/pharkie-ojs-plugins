#!/usr/bin/env python3
"""Compare journal PDFs across three local directories:
- securepdfs-mirror/ (downloaded from live WP)
- backfill/private/input/ (pipeline-ready copies)
- journal archive/ (our original archive)

Maps filenames to issue numbers, compares sizes and page counts.
"""

import os
import re
import sys
from pathlib import Path

# Try importing PyMuPDF for page count / first-page text comparison
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

BASE = Path(__file__).resolve().parent.parent

MIRROR_DIR = BASE / "data-export" / "securepdfs-mirror"
PREPARED_DIR = BASE / "backfill" / "prepared"
ARCHIVE_DIR = BASE / "data-export" / "journal archive"


def parse_issue_from_mirror(filename: str) -> str | None:
    """Extract issue number from mirror filename. Returns None for non-journal PDFs."""
    name = Path(filename).stem

    # Skip HC newsletters, AGM docs, admin docs
    if name.startswith("HC-") or "AGM" in name or "Chair-of" in name or "Editor-of" in name or "Motion-Form" in name or "Nomination-Form" in name:
        return None
    # Skip the commemorative supplement (not the main 35.1 issue)
    if "Commemorating" in name:
        return None
    # Skip HC date-named files (2020-April, 2021-October, etc.)
    if re.match(r'^\d{4}-(April|October|January|July|February|March|August)$', name):
        return None

    # EA1.pdf through EA30.1.pdf
    m = re.match(r'^EA(\d+(?:\.\d+)?)$', name)
    if m:
        return m.group(1)

    # Plain number: 1.pdf, 34.1.pdf
    m = re.match(r'^(\d+(?:\.\d+)?)$', name)
    if m:
        return m.group(1)

    # EA34.2.pdf
    m = re.match(r'^EA(\d+\.\d+)$', name)
    if m:
        return m.group(1)

    # EA-Journal-33.1-rj59gs.pdf, EA-Journal-35.1.pdf, EA-Journal-35.1-1.pdf
    m = re.match(r'^EA-Journal-(\d+\.\d+)', name)
    if m:
        return m.group(1)

    # EA.Journal-33.2.pdf
    m = re.match(r'^EA\.Journal-(\d+\.\d+)', name)
    if m:
        return m.group(1)

    # Existential-AnalysisJournal31.1.January2020-1.pdf
    m = re.match(r'^Existential-?AnalysisJournal\.?(\d+\.\d+)', name)
    if m:
        return m.group(1)

    # ExistentialAnalysisJournal.32.1-ycgtva.pdf
    m = re.match(r'^ExistentialAnalysisJournal\.(\d+\.\d+)', name)
    if m:
        return m.group(1)

    # SEA-Journal-32.2.pdf
    m = re.match(r'^SEA-Journal-(\d+\.\d+)', name)
    if m:
        return m.group(1)

    # Journal.35.2.July-2024.pdf
    m = re.match(r'^Journal\.(\d+\.\d+)', name)
    if m:
        return m.group(1)

    # Existential-Analysis-Journal-36.2.pdf
    m = re.match(r'^Existential-Analysis-Journal-(\d+\.\d+)', name)
    if m:
        return m.group(1)

    return None


def parse_issue_from_prepared(filename: str) -> str | None:
    """Prepared files are already normalized: 1.pdf, 6.1.pdf, etc."""
    if filename.endswith('.toc.json'):
        return None
    m = re.match(r'^(\d+(?:\.\d+)?)\.pdf$', filename)
    return m.group(1) if m else None


def parse_issue_from_archive(filename: str) -> str | None:
    """Archive: Pre 2018/EA1.pdf or 2018-25/29.1.pdf"""
    name = Path(filename).stem
    # EA prefix
    m = re.match(r'^EA(\d+(?:\.\d+)?)$', name)
    if m:
        return m.group(1)
    # Plain number
    m = re.match(r'^(\d+(?:\.\d+)?)$', name)
    if m:
        return m.group(1)
    return None


def issue_sort_key(issue: str) -> tuple:
    """Sort key: (vol, sub) e.g. '6.1' -> (6, 1), '1' -> (1, 0)"""
    parts = issue.split('.')
    vol = int(parts[0])
    sub = int(parts[1]) if len(parts) > 1 else 0
    return (vol, sub)


def get_page_count(filepath: Path) -> int | None:
    if not HAS_FITZ:
        return None
    try:
        doc = fitz.open(str(filepath))
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return None


def get_first_page_text(filepath: Path, max_chars=200) -> str:
    if not HAS_FITZ:
        return ""
    try:
        doc = fitz.open(str(filepath))
        text = doc[0].get_text()[:max_chars].strip()
        doc.close()
        return text
    except Exception:
        return ""


def scan_mirror() -> dict[str, list[tuple[Path, int]]]:
    """Returns {issue: [(filepath, size), ...]}. Multiple files may map to same issue."""
    results: dict[str, list[tuple[Path, int]]] = {}
    for root, _, files in os.walk(MIRROR_DIR):
        for f in files:
            if not f.lower().endswith('.pdf'):
                continue
            issue = parse_issue_from_mirror(f)
            if issue is None:
                continue
            fp = Path(root) / f
            results.setdefault(issue, []).append((fp, fp.stat().st_size))
    return results


def scan_prepared() -> dict[str, tuple[Path, int]]:
    results = {}
    for f in os.listdir(PREPARED_DIR):
        issue = parse_issue_from_prepared(f)
        if issue is None:
            continue
        fp = PREPARED_DIR / f
        results[issue] = (fp, fp.stat().st_size)
    return results


def scan_archive() -> dict[str, tuple[Path, int]]:
    results = {}
    for root, _, files in os.walk(ARCHIVE_DIR):
        for f in files:
            if not f.lower().endswith('.pdf'):
                continue
            issue = parse_issue_from_archive(f)
            if issue is None:
                continue
            fp = Path(root) / f
            results[issue] = (fp, fp.stat().st_size)
    return results


def fmt_size(size: int | None) -> str:
    if size is None:
        return "—"
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def main():
    mirror = scan_mirror()
    prepared = scan_prepared()
    archive = scan_archive()

    all_issues = sorted(
        set(mirror.keys()) | set(prepared.keys()) | set(archive.keys()),
        key=issue_sort_key
    )

    # Build table rows
    rows = []
    mismatches = []

    for issue in all_issues:
        m_entries = mirror.get(issue, [])
        p_entry = prepared.get(issue)
        a_entry = archive.get(issue)

        # Pick the "best" mirror entry (largest file, or the one without random suffixes)
        if m_entries:
            # Prefer the simplest filename, then largest
            m_entries_sorted = sorted(m_entries, key=lambda x: (len(x[0].name), -x[1]))
            m_path, m_size = m_entries_sorted[0]
        else:
            m_path, m_size = None, None

        p_path, p_size = p_entry if p_entry else (None, None)
        a_path, a_size = a_entry if a_entry else (None, None)

        # Determine status
        if m_path and not p_path and not a_path:
            status = "MIRROR-ONLY"
        elif not m_path and (p_path or a_path):
            status = "LOCAL-ONLY"
        elif m_path and p_path:
            if m_size == p_size:
                status = "MATCH"
            elif p_size and m_size and p_size > m_size:
                status = "OCR'd"  # prepared is larger, likely OCR'd version
            else:
                status = "DIFFERENT"
        elif m_path and a_path and not p_path:
            status = "NOT-PREPARED"
        else:
            status = "?"

        # Mirror duplicates note
        dup_note = ""
        if len(m_entries) > 1:
            dup_note = f" ({len(m_entries)} files on server)"

        rows.append({
            'issue': issue,
            'm_size': m_size,
            'p_size': p_size,
            'a_size': a_size,
            'm_path': m_path,
            'p_path': p_path,
            'a_path': a_path,
            'status': status,
            'dup_note': dup_note,
        })

        if status in ("DIFFERENT", "OCR'd"):
            mismatches.append(rows[-1])

    # Print main table
    print(f"\n{'Issue':<8} {'Mirror':>10} {'Prepared':>10} {'Archive':>10}  Status")
    print("-" * 60)
    for r in rows:
        print(f"{r['issue']:<8} {fmt_size(r['m_size']):>10} {fmt_size(r['p_size']):>10} {fmt_size(r['a_size']):>10}  {r['status']}{r['dup_note']}")

    # Summary
    statuses = [r['status'] for r in rows]
    print(f"\nTotal issues: {len(rows)}")
    print(f"  MATCH:        {statuses.count('MATCH')}")
    ocr_count = statuses.count("OCR'd")
    print(f"  OCR'd:        {ocr_count}")
    print(f"  DIFFERENT:    {statuses.count('DIFFERENT')}")
    print(f"  MIRROR-ONLY:  {statuses.count('MIRROR-ONLY')}")
    print(f"  NOT-PREPARED: {statuses.count('NOT-PREPARED')}")
    print(f"  LOCAL-ONLY:   {statuses.count('LOCAL-ONLY')}")

    # Detailed mismatch analysis
    if mismatches and HAS_FITZ:
        print(f"\n{'='*60}")
        print("MISMATCH DETAILS (page counts + first-page text)")
        print(f"{'='*60}")
        for r in mismatches:
            print(f"\n--- Issue {r['issue']} ({r['status']}) ---")
            for label, path, size in [
                ("Mirror", r['m_path'], r['m_size']),
                ("Prepared", r['p_path'], r['p_size']),
                ("Archive", r['a_path'], r['a_size']),
            ]:
                if path is None:
                    continue
                pages = get_page_count(path)
                text = get_first_page_text(path, 150)
                text_oneline = text.replace('\n', ' ')[:100]
                print(f"  {label:>10}: {fmt_size(size):>10}, {pages} pages")
                print(f"             {path.name}")
                if text_oneline:
                    print(f"             \"{text_oneline}\"")

        # Check if mirror has duplicates worth noting
        print(f"\n{'='*60}")
        print("MIRROR DUPLICATES")
        print(f"{'='*60}")
        for issue in sorted(mirror.keys(), key=issue_sort_key):
            entries = mirror[issue]
            if len(entries) > 1:
                print(f"\n  Issue {issue}:")
                for path, size in sorted(entries, key=lambda x: x[0].name):
                    pages = get_page_count(path)
                    print(f"    {path.name}: {fmt_size(size)}, {pages} pages")

    # Recommendations
    print(f"\n{'='*60}")
    print("RECOMMENDATIONS")
    print(f"{'='*60}")
    for r in rows:
        if r['status'] == 'MIRROR-ONLY':
            print(f"  Issue {r['issue']}: COPY from mirror to prepared/")
        elif r['status'] == 'NOT-PREPARED':
            print(f"  Issue {r['issue']}: COPY from archive or mirror to prepared/")
        elif r['status'] == 'DIFFERENT':
            print(f"  Issue {r['issue']}: INVESTIGATE — size differs between mirror and prepared")

    # For DIFFERENT issues, compare mirror vs archive too
    for r in rows:
        if r['status'] in ('DIFFERENT', 'OCR\'d') and r['a_path'] and r['m_path']:
            if r['a_size'] == r['m_size']:
                print(f"  Issue {r['issue']}: mirror matches archive (prepared is the different one)")
            else:
                print(f"  Issue {r['issue']}: mirror differs from archive too")


if __name__ == "__main__":
    main()
