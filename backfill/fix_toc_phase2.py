#!/usr/bin/env python3
"""Phase 2: Fix verification findings across toc.json files.

Fixes:
1. PAGE_OFF_BY_1: title found at pdf_page_start+1 not pdf_page_start
2. Specific page range bugs (vols 10.1, 11.1, 12.2, 15.1)
3. Gap fixes (vols 18.2, 20.1, 21.1)
4. Vol 30.1 +4 offset (appendix pages)
5. Honorifics in reviewer/book_author fields
6. Missing book review entry (vol 12.2)

Usage: python3 backfill/fix_toc_phase2.py [--dry-run]
"""

import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

BASE = Path("backfill/private/output")
PDF_BASE = Path("backfill/private/input")
DRY_RUN = "--dry-run" in sys.argv
changes = []


def log(vol, msg):
    changes.append(f"  [{vol}] {msg}")


def load_toc(vol):
    with open(BASE / vol / "toc.json") as f:
        return json.load(f)


def save_toc(vol, data):
    if DRY_RUN:
        return
    path = BASE / vol / "toc.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")


def normalize(s):
    """Normalize text for matching: lowercase, straighten quotes, collapse whitespace."""
    s = s.lower()
    s = s.replace("\u2019", "'").replace("\u2018", "'")  # curly quotes
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"\s+", " ", s)
    return s


def title_on_page(doc, page_idx, title):
    """Check if title text appears on a PDF page."""
    if page_idx < 0 or page_idx >= len(doc):
        return False
    text = normalize(doc[page_idx].get_text())

    # Clean title for matching
    clean = re.sub(r"^Book Review:\s*", "", title, flags=re.IGNORECASE)
    words = clean.split()

    # Try first N words as a phrase (min 2 words)
    for n in range(min(6, len(words)), 1, -1):
        fragment = normalize(" ".join(words[:n]))
        if fragment in text:
            return True

    # Try matching distinctive words (4+ chars)
    long_words = [normalize(w).strip(",.;:'\"()?!") for w in words if len(w) >= 4]
    long_words = [w for w in long_words if w]  # remove empty after strip
    if long_words:
        matches = sum(1 for w in long_words if w in text)
        if matches >= min(3, len(long_words)):
            return True

    return False


# =====================================================================
# 1. PAGE_OFF_BY_1
# =====================================================================
def fix_off_by_one():
    print("\n--- PAGE_OFF_BY_1 ---")
    volumes = ["4", "6.1", "6.2", "8.1", "8.2", "9.1", "9.2", "15.2", "16.1", "17.1"]
    total = 0

    for vol in volumes:
        pdf_path = PDF_BASE / f"{vol}.pdf"
        if not pdf_path.exists():
            print(f"  [{vol}] PDF missing, skip")
            continue

        toc = load_toc(vol)
        doc = fitz.open(str(pdf_path))
        fixed = 0

        for i, entry in enumerate(toc["articles"]):
            title = entry["title"]
            start = entry["pdf_page_start"]

            # Skip generic titles
            if title in ("Editorial", "Book Reviews", "Letter to the Editors"):
                continue

            on_current = title_on_page(doc, start, title)
            on_next = title_on_page(doc, start + 1, title)

            if not on_current and on_next:
                log(vol, f"[{i}] '{title[:55]}' start {start}→{start+1}")
                entry["pdf_page_start"] = start + 1
                fixed += 1
            elif not on_current and not on_next:
                log(vol, f"[{i}] WARN: '{title[:55]}' not found on {start} or {start+1}")

        doc.close()
        if fixed:
            save_toc(vol, toc)
            total += fixed
            print(f"  [{vol}] {fixed} entries fixed")
        else:
            print(f"  [{vol}] clean")

    print(f"  Total off-by-one: {total}")


# =====================================================================
# 2. Vol 10.1: overlapping entries at page 136
# =====================================================================
def fix_10_1():
    """Both reviews are by Martin Milton as a combined review spanning pages 137-142.
    Previous review (Self-awareness) ends on page 136/137. Next review (Existence in Black)
    starts on page 143. Both entries share the same page range."""
    print("\n--- Vol 10.1: overlapping entries ---")
    vol = "10.1"
    toc = load_toc(vol)

    # Entry 13: Deconstructing Psychopathology — start 136, end 136
    # Entry 14: Psychoanalytic Culture — start 136, end 152
    # Both are reviewed together by Martin Milton on pages 137-142
    e13 = toc["articles"][13]
    e14 = toc["articles"][14]

    log(vol, f"[13] Deconstructing Psychopathology: 136-136 → 137-142")
    e13["pdf_page_start"] = 137
    e13["pdf_page_end"] = 142

    log(vol, f"[14] Psychoanalytic Culture: 136-152 → 137-142")
    e14["pdf_page_start"] = 137
    e14["pdf_page_end"] = 142

    save_toc(vol, toc)


# =====================================================================
# 3. Vol 11.1: expand book review page ranges
# =====================================================================
def fix_11_1():
    print("\n--- Vol 11.1: book review ranges ---")
    vol = "11.1"
    toc = load_toc(vol)
    pdf_path = PDF_BASE / f"{vol}.pdf"
    doc = fitz.open(str(pdf_path))

    # Entry 14: Plato Not Prozac — 169-169 → should end before Heart and Soul (175)
    # Entry 15: Heart and Soul — 175-175 → should end before Emotions (182)
    # Entry 16: Emotions — 182-182 → should end near end of PDF (189)

    e14 = toc["articles"][14]
    e15 = toc["articles"][15]
    e16 = toc["articles"][16]

    new_end_14 = e15["pdf_page_start"] - 1  # 174
    new_end_15 = e16["pdf_page_start"] - 1  # 181

    # Find actual last page of content for Emotions review
    new_end_16 = e16["pdf_page_end"]
    for p in range(toc["total_pdf_pages"] - 1, e16["pdf_page_start"], -1):
        text = doc[p].get_text().strip()
        if len(text) > 100:
            new_end_16 = p
            break

    log(vol, f"[14] Plato Not Prozac end {e14['pdf_page_end']}→{new_end_14}")
    log(vol, f"[15] Heart and Soul end {e15['pdf_page_end']}→{new_end_15}")
    log(vol, f"[16] Emotions end {e16['pdf_page_end']}→{new_end_16}")

    e14["pdf_page_end"] = new_end_14
    e15["pdf_page_end"] = new_end_15
    e16["pdf_page_end"] = new_end_16

    doc.close()
    save_toc(vol, toc)


# =====================================================================
# 4. Vol 12.2: Momma truncation + missing Heidegger review
# =====================================================================
def fix_12_2():
    print("\n--- Vol 12.2: Momma + missing Heidegger review ---")
    vol = "12.2"
    toc = load_toc(vol)
    pdf_path = PDF_BASE / f"{vol}.pdf"
    doc = fitz.open(str(pdf_path))

    # Momma: entry 12, currently 165-165, actually spans 165-167
    # Missing Heidegger/Holderlin: pages 168-171 (reviewer: Miles Groth)
    # Page 171 is shared: Heidegger review ends, Philosophy review starts
    # Philosophy for Counselling: entry 13, currently starts at 171 (correct)

    # Fix Momma end page
    log(vol, f"[12] Momma end {toc['articles'][12]['pdf_page_end']}→167")
    toc["articles"][12]["pdf_page_end"] = 167

    # Add missing Heidegger/Holderlin review entry
    new_entry = {
        "title": "Book Review: Elucidations of Hölderlin's Poetry",
        "book_title": "Elucidations of Hölderlin's Poetry",
        "book_author": "Martin Heidegger, translated by Keith Hoeller",
        "book_year": 2000,
        "publisher": "Amherst: Humanity Books",
        "pdf_page_start": 168,
        "section": "Book Reviews",
        "pdf_page_end": 171,
        "reviewer": "Miles Groth",
        "authors": "Miles Groth",
    }

    log(vol, f"Adding: '{new_entry['title']}' pp 168-171 by Miles Groth")
    toc["articles"].insert(13, new_entry)

    doc.close()
    save_toc(vol, toc)


# =====================================================================
# 5. Vol 15.1: out-of-bounds page
# =====================================================================
def fix_15_1():
    print("\n--- Vol 15.1: out-of-bounds ---")
    vol = "15.1"
    toc = load_toc(vol)
    last = toc["articles"][-1]
    total = toc["total_pdf_pages"]

    if last["pdf_page_end"] >= total:
        new_end = total - 1
        log(vol, f"'{last['title']}' end {last['pdf_page_end']}→{new_end}")
        last["pdf_page_end"] = new_end
        save_toc(vol, toc)


# =====================================================================
# 6. Honorifics in reviewer/book_author fields
# =====================================================================
def fix_honorifics():
    print("\n--- Honorifics ---")
    fixes = [
        ("4", "reviewer", "Daniel Burston, PhD.", "Daniel Burston"),
        ("14.1", "reviewer", "Dr Shamil Wanigaratne", "Shamil Wanigaratne"),
        ("15.1", "reviewer", "Dr. Robert Hill", "Robert Hill"),
        ("16.2", "reviewer", "Dr. Jenny Corless", "Jenny Corless"),
        ("18.1", "reviewer", "Dr R.G. Hill", "R.G. Hill"),
        ("18.1", "reviewer", "Dr R G Hill", "R G Hill"),
        ("30.2", "book_author", "Dr. Gordon Marino", "Gordon Marino"),
        ("34.1", "reviewer", "Dr Martin Adams", "Martin Adams"),
    ]

    vols_to_save = {}
    for vol, field, old, new in fixes:
        if vol not in vols_to_save:
            vols_to_save[vol] = load_toc(vol)
        toc = vols_to_save[vol]
        for entry in toc["articles"]:
            if entry.get(field) == old:
                entry[field] = new
                log(vol, f"{field}: '{old}'→'{new}'")

    for vol, toc in vols_to_save.items():
        save_toc(vol, toc)


# =====================================================================
# 7. Gap fixes: extend page ranges
# =====================================================================
def fix_gaps():
    print("\n--- Gap fixes ---")

    gap_fixes = [
        ("18.2", "Zone of the Interior", "pdf_page_end", 177),
        ("20.1", "Staring at the Sun", "pdf_page_end", 170),
        ("21.1", "When Death Enters", "pdf_page_end", 152),
    ]

    for vol, title_fragment, field, new_val in gap_fixes:
        toc = load_toc(vol)
        for entry in toc["articles"]:
            if title_fragment in entry.get("title", ""):
                old_val = entry[field]
                entry[field] = new_val
                log(vol, f"'{title_fragment}' {field} {old_val}→{new_val}")
                break
        save_toc(vol, toc)


# =====================================================================
# 8. Vol 30.1: +4 offset for entries after appendix pages
# =====================================================================
def fix_30_1():
    """Entries 7-15+ need +4 offset due to 4 appendix pages (79i-79iv) inserted
    between entries 6 and 7."""
    print("\n--- Vol 30.1: +4 offset ---")
    vol = "30.1"
    toc = load_toc(vol)
    pdf_path = PDF_BASE / f"{vol}.pdf"
    doc = fitz.open(str(pdf_path))

    for i in range(7, len(toc["articles"])):
        entry = toc["articles"][i]
        old_s = entry["pdf_page_start"]
        old_e = entry["pdf_page_end"]

        # Verify: title should be on old_s+4, not old_s
        on_current = title_on_page(doc, old_s, entry["title"])
        on_plus4 = title_on_page(doc, old_s + 4, entry["title"])

        if not on_current and on_plus4:
            entry["pdf_page_start"] = old_s + 4
            entry["pdf_page_end"] = old_e + 4
            log(vol, f"[{i}] '{entry['title'][:45]}' {old_s}-{old_e}→{old_s+4}-{old_e+4}")
        elif on_current:
            pass  # Already correct
        elif not on_current and not on_plus4:
            # title_on_page may fail for multi-line titles — apply +4 if in
            # the known affected range (entries 7-15) and title not on current page
            if 7 <= i <= 15:
                entry["pdf_page_start"] = old_s + 4
                entry["pdf_page_end"] = old_e + 4
                log(vol, f"[{i}] '{entry['title'][:45]}' {old_s}-{old_e}→{old_s+4}-{old_e+4} (range-based)")

    doc.close()
    save_toc(vol, toc)


# =====================================================================
def main():
    mode = "DRY RUN" if DRY_RUN else "LIVE"
    print(f"=== Phase 2 toc.json fixes ({mode}) ===")

    fix_off_by_one()
    fix_10_1()
    fix_11_1()
    fix_12_2()
    fix_15_1()
    fix_honorifics()
    fix_gaps()
    fix_30_1()

    print(f"\n=== Summary ===")
    for c in changes:
        print(c)
    print(f"\nTotal: {len(changes)} changes {'(dry run)' if DRY_RUN else 'applied'}")


if __name__ == "__main__":
    main()
