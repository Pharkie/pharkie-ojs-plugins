#!/usr/bin/env python3
"""Data quality fixes for all toc.json files.

Fixes:
1. Strip Dr/PhD from author names
2. Normalise "Simon Du Plock" → "Simon du Plock"
3. Fix junk Book Review Editorial authors
4. Strip trailing punctuation from titles
5. Clean publication metadata from one title (20.1)
6. Normalise Letter title variants
7. Split Letters entries into individual letters
8. Add Part designations to serialised articles
9. Fix missing page numbers (16.2 Editorial)
"""

import json
import re
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"

# Track all changes for reporting
changes: list[str] = []


def log(vol_iss: str, msg: str):
    changes.append(f"  {vol_iss}: {msg}")


def fix_dr_phd(authors: str) -> str:
    """Strip Dr/Dr./PhD./PhD from author names."""
    # Handle "Daniel Burston, PhD." pattern
    result = re.sub(r',\s*PhD\.?', '', authors)
    # Handle "Dr." or "Dr " prefix (with or without period)
    result = re.sub(r'\bDr\.?\s+', '', result)
    return result.strip()


def fix_du_plock(authors: str) -> str:
    """Normalise 'Du Plock' → 'du Plock'."""
    return authors.replace('Du Plock', 'du Plock')


def strip_trailing_punct(title: str) -> str:
    """Strip trailing . or , unless preceded by a single uppercase letter (initials)."""
    # Don't touch if it ends with an uppercase letter + period (initials like M.H., R.)
    if re.search(r'[A-Z]\.\s*$', title):
        return title
    return re.sub(r'[.,]\s*$', '', title)


def process_file(toc_path: Path):
    dirname = toc_path.parent.name

    with open(toc_path) as f:
        data = json.load(f)

    modified = False

    for article in data.get("articles", []):
        title = article.get("title", "") or ""
        authors = article.get("authors", "") or ""
        section = article.get("section", "") or ""

        # --- Fix 1: Strip Dr/PhD from author names ---
        if re.search(r'\bDr\.?\s|PhD\.?', authors):
            new_authors = fix_dr_phd(authors)
            log(dirname, f"Dr/PhD: {authors!r} → {new_authors!r}")
            article["authors"] = new_authors
            authors = new_authors
            modified = True

        # --- Fix 2: Normalise "Du Plock" → "du Plock" ---
        if 'Du Plock' in authors:
            new_authors = fix_du_plock(authors)
            log(dirname, f"Du Plock: {authors!r} → {new_authors!r}")
            article["authors"] = new_authors
            authors = new_authors
            modified = True

        # --- Fix 3: Junk Book Review Editorial authors ---
        if section == "Book Review Editorial" and title == "Book Reviews":
            junk_fixes = {
                "20.1": ("Irvin D. Yalom (2008) Piatkus £14.99", "Martin Adams"),
                "20.2": ("Emmy van Deurzen. (2009). Sage. Pb £20.99.", "Martin Adams"),
                "21.1": ("Perspectives in Psychotherapy and Counselling.", "Martin Adams"),
                "21.2": ("Routledge", "Martin Adams"),
                "22.1": ("Heidegger and a Metaphysics of Feeling", "Martin Adams"),
                "22.2": ("Stop Making Excuses", "Martin Adams"),
                "23.1": ("Studies", "Martin Adams"),
                "28.2": ("Books.", "Ondine Smulders"),
            }
            if dirname in junk_fixes:
                expected_junk, correct = junk_fixes[dirname]
                if authors == expected_junk:
                    log(dirname, f"Book Review Editorial: {authors!r} → {correct!r}")
                    article["authors"] = correct
                    modified = True
            # 28.1 has None/blank author
            if dirname == "28.1" and not authors:
                log(dirname, "Book Review Editorial: (blank) → 'Martin Adams'")
                article["authors"] = "Martin Adams"
                modified = True

        # --- Fix 4: Strip trailing punctuation from titles ---
        # Skip the 20.1 title that's handled in Fix 5
        if dirname == "20.1" and "Brave New Worlding" in title:
            pass  # Handled below
        elif title and re.search(r'[.,]\s*$', title):
            new_title = strip_trailing_punct(title)
            if new_title != title:
                log(dirname, f"Trailing punct: {title!r} → {new_title!r}")
                article["title"] = new_title
                title = new_title
                modified = True

        # --- Fix 5: Clean publication metadata from title (20.1) ---
        if dirname == "20.1" and "Brave New Worlding" in title:
            old_title = title
            new_title = "Brave New Worlding: A Response to Practising Existential Psychotherapy: The Relational World by Ernesto Spinelli"
            if title != new_title:
                log(dirname, f"Title cleanup: {old_title!r} → {new_title!r}")
                article["title"] = new_title
                modified = True

        # --- Fix 6: Normalise Letter title variants ---
        letter_variants = {
            "Letter to The Editors",
            "Letters to the Editors",
            "Letters To The Editor",
            "Letters To The Editors",
        }
        if title in letter_variants:
            new_title = "Letter to the Editors"
            log(dirname, f"Letter title: {title!r} → {new_title!r}")
            article["title"] = new_title
            title = new_title
            modified = True

        # --- Fix 8: Add Part designations to serialised articles ---
        if dirname == "24.2" and title == "Being Sexual: Human Sexuality Revisited":
            new_title = "Being Sexual: Human Sexuality Revisited (Part 1)"
            log(dirname, f"Serialised: {title!r} → {new_title!r}")
            article["title"] = new_title
            modified = True

        if dirname == "25.1" and title == "Being Sexual: Human Sexuality Revisited":
            new_title = "Being Sexual: Human Sexuality Revisited (Part 2)"
            log(dirname, f"Serialised: {title!r} → {new_title!r}")
            article["title"] = new_title
            modified = True

        if dirname == "30.2" and "International Bibliography of the Writings of Medard Boss" in title:
            old = title
            new_title = title + " (Part 1)" if "(Part" not in title else title
            if new_title != old:
                log(dirname, f"Serialised: {old!r} → {new_title!r}")
                article["title"] = new_title
                modified = True

        if dirname == "31.1" and "International Bibliography of the Writings of Medard Boss" in title:
            old = title
            new_title = title + " (Part 2)" if "(Part" not in title else title
            if new_title != old:
                log(dirname, f"Serialised: {old!r} → {new_title!r}")
                article["title"] = new_title
                modified = True

        # --- Fix 9: Fix missing page numbers (16.2 Editorial) ---
        if (dirname == "16.2" and section == "Editorial"
                and article.get("pdf_page_start") == 0
                and article.get("pdf_page_end") == 0):
            # PDF page 0 is correct — editorial is on first page of PDF.
            # But 0 displays as blank in spreadsheet. Use 1-indexed convention
            # if other editorials use 1-indexed. Check: most editorials use
            # pdf_page_start=3 or 4, which are 0-indexed PDF pages.
            # Actually 0 IS the correct 0-indexed PDF page here.
            # The next article starts at pdf_page_start=1, confirming 0-indexing.
            # Leave as-is — 0 is correct for this file's convention.
            pass

    # --- Fix 7: Split Letters entries into individual letters ---
    new_articles = []
    for article in data.get("articles", []):
        title = article.get("title", "") or ""

        # 12.1: "Letters" → 1 letter by Martin Milton
        if dirname == "12.1" and title == "Letters":
            log(dirname, "Split Letters → 1 individual letter (Martin Milton)")
            new_articles.append({
                "title": "Letter to the Editors",
                "authors": "Martin Milton",
                "section": "Articles",
                "pdf_page_start": 164,
                "pdf_page_end": 167,
            })
            modified = True
            continue

        # 13.2: single letter, fix pages and add author
        if dirname == "13.2" and title == "Letter to the Editors" and not article.get("authors"):
            log(dirname, "Fix Letter: add author Martin Milton, pages 159-160")
            article["authors"] = "Martin Milton"
            article["pdf_page_start"] = 159
            article["pdf_page_end"] = 160
            modified = True

        # 14.2: split into 3 letters
        if dirname == "14.2" and title == "Letter to the Editors" and not article.get("authors"):
            log(dirname, "Split Letters → 3 individual letters")
            new_articles.extend([
                {
                    "title": "Letter to the Editors",
                    "authors": "Hugh Hetherington",
                    "section": "Articles",
                    "pdf_page_start": 200,
                    "pdf_page_end": 202,
                },
                {
                    "title": "Letter to the Editors",
                    "authors": "Sam Stephens",
                    "section": "Articles",
                    "pdf_page_start": 203,
                    "pdf_page_end": 203,
                },
                {
                    "title": "Letter to the Editors",
                    "authors": "Brian Uhlin",
                    "section": "Articles",
                    "pdf_page_start": 204,
                    "pdf_page_end": 211,
                },
            ])
            modified = True
            continue

        # 15.1: single letter, fix pages and add author
        if dirname == "15.1" and title == "Letter to the Editors" and not article.get("authors"):
            log(dirname, "Fix Letter: add author Anthony Stadlen, pages 184-185")
            article["authors"] = "Anthony Stadlen"
            article["pdf_page_start"] = 184
            article["pdf_page_end"] = 185
            modified = True

        # 29.2: split into 2 letters (title already normalised by Fix 6)
        if dirname == "29.2" and "Letter to the Editor" in title and not article.get("authors"):
            log(dirname, "Split Letters → 2 individual letters")
            new_articles.extend([
                {
                    "title": "Letter to the Editor",
                    "authors": "Daphne Hampson",
                    "section": "Articles",
                    "pdf_page_start": 156,
                    "pdf_page_end": 157,
                },
                {
                    "title": "Letter to the Editor",
                    "authors": "Ernesto Spinelli",
                    "section": "Articles",
                    "pdf_page_start": 157,
                    "pdf_page_end": 158,
                },
            ])
            modified = True
            continue

        new_articles.append(article)

    data["articles"] = new_articles

    if modified:
        with open(toc_path, "w") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.write("\n")

    return modified


def main():
    files_modified = 0
    for toc_path in sorted(OUTPUT_DIR.glob("*/toc.json")):
        if process_file(toc_path):
            files_modified += 1

    # Print grouped by fix type
    print("Changes applied:")
    for change in changes:
        print(change)
    print(f"\nTotal: {len(changes)} changes across {files_modified} files")


if __name__ == "__main__":
    main()
