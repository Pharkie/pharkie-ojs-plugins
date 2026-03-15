#!/usr/bin/env python3
"""Set editorial authors in all toc.json files based on editor mapping."""

import json
from pathlib import Path


def parse_vol_iss(dirname: str) -> tuple[int, int]:
    """Parse directory name like '1' or '6.2' into (vol, iss)."""
    if "." in dirname:
        vol, iss = dirname.split(".")
        return int(vol), int(iss)
    return int(dirname), 1


def get_editors(vol: int, iss: int) -> str | None:
    """Return editor name(s) for a given volume/issue, or None if no editorial expected."""
    if vol == 1:
        return "Carole Van Artsdalen & Elena Lea Zanger"
    if vol in (2, 3):
        return "Alessandra Lemma & Ernesto Spinelli"
    if vol == 4:
        return None  # No editorial article exists
    if vol <= 10 or (vol == 11 and iss == 1):
        return "Hans W. Cohn & Simon du Plock"
    if (vol == 11 and iss >= 2) or (12 <= vol <= 14):
        return "Simon du Plock & John Heaton"
    if 15 <= vol <= 17 or (vol == 18 and iss == 1):
        return "Simon du Plock & John Heaton"
    if (vol == 18 and iss >= 2) or (19 <= vol <= 25):
        return "Simon du Plock & Greg Madison"
    if vol in (26, 27):
        return "Greg Madison"
    if 28 <= vol <= 31:
        return "Martin Adams"
    if vol in (32, 33):
        return "Simon du Plock & Martin Adams"
    if vol == 34 and iss == 1:
        return "Simon du Plock, Martin Adams & Devang Vaidya"
    if (vol == 34 and iss == 2) or (35 <= vol <= 37):
        return "Simon du Plock & Martin Adams"
    return None


def main():
    output_dir = Path(__file__).parent / "output"
    updated = 0
    skipped = 0

    for toc_path in sorted(output_dir.glob("*/toc.json")):
        dirname = toc_path.parent.name
        vol, iss = parse_vol_iss(dirname)
        editors = get_editors(vol, iss)

        if editors is None:
            continue

        with open(toc_path) as f:
            data = json.load(f)

        changed = False
        for article in data.get("articles", []):
            if article.get("section") == "Editorial" and article.get("authors") is None:
                article["authors"] = editors
                changed = True

        if changed:
            with open(toc_path, "w") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.write("\n")
            updated += 1
            print(f"  Updated {dirname}: {editors}")
        else:
            skipped += 1

    print(f"\nDone. Updated: {updated}, Skipped (no null editorials): {skipped}")


if __name__ == "__main__":
    main()
