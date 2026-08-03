#!/usr/bin/env python3
"""Rebuild paragraphs that the extractor split at PDF line-wrap points.

THE DEFECT
----------
For some articles the layout extractor emitted one <p> per PHYSICAL LINE of the
PDF, so a reader gets a ragged column of 70-character fragments:

    <p>Another kind of emplacement that leads us to the components of embodiment</p>
    <p>and mood is 'space-attuned'. From Binswanger's viewpoint, this space</p>
    <p>is in harmony with our mood or temperament (for example, in joy place</p>

and, at the same time, it JOINED across real paragraph breaks without a space —
"the new space as expanding.A distinct feature of mood was..." — so it is
splitting where the text continues and joining where it stops. Exactly inverted.

Reported on 37.2/13 (Mohammadi, The Twelve-Day War Experience) 2026-08-03.

GROUND TRUTH
------------
The PDF text layer records the difference explicitly. A wrapped line ends with a
SPACE before its newline; a line that ends a paragraph does not:

    "...components of embodiment \n"      <- wrapped, continues
    "...the new space as expanding.\n"    <- paragraph ends here

Checked on the source PDF: 610 wrapped lines against 243 paragraph ends. So the
paragraph boundaries are read off the PDF, never guessed from sentence
punctuation — a paragraph can end without a full stop, and a sentence can end
mid-paragraph at a wrap point (32 such lines in this article alone).

HOW IT WORKS
------------
Per <sec>, the existing <p> inner HTML is concatenated into one stream, then
re-split at the PDF's paragraph boundaries. Inline markup survives, because the
stream is cut by plain-text offset and the tags are carried along — <italic>
Befindlichkeit</italic> stays italic.

Refuses rather than guesses: a section whose reassembled text does not match the
PDF's, ignoring whitespace, is left exactly as it was and reported. Being wrong
here would silently reword published scholarship.

USAGE
    python3 reflow_paragraphs.py <article.jats.xml>            # report only
    python3 reflow_paragraphs.py --write <article.jats.xml>    # rewrite in place
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF is required: pip install pymupdf")

PARAGRAPH = re.compile(r"<p>(.*?)</p>", re.S)
TAG = re.compile(r"<[^>]+>")


# Bullets differ between the two sides and must not decide a match. The PDF
# renders them from a symbol font, so they arrive as control characters (\x07 in
# 37.2/13) or private-use codepoints; the extractor drops them entirely. Same
# for the discretionary hyphens and soft spaces PDF text layers carry.
NOISE = re.compile(r"[\x00-\x1f­•▪●◦-]")


def squash(text: str) -> str:
    """Plain text, whitespace- and bullet-insensitive: the key for every match."""
    return NOISE.sub("", re.sub(r"\s+", "", TAG.sub("", text)))


# Page furniture sits in the MIDDLE of a section's text stream, because it lands
# wherever a page happens to break. Left in, a section's paragraphs stop looking
# contiguous and every match fails. The running heads are the article's own
# title, its authors, the journal's title, and a bare page number — so they are
# read off the JATS rather than hard-coded, and this works for any article.
JOURNAL_TITLE = "Existential Analysis: Journal of The Society for Existential Analysis"


def furniture_strings(xml: str) -> set[str]:
    out = {JOURNAL_TITLE}
    title = re.search(r"<article-title>(.*?)</article-title>", xml, re.S)
    if title:
        out.add(re.sub(r"\s+", " ", TAG.sub("", title.group(1))).strip())
    given = re.findall(r"<given-names>([^<]*)</given-names>", xml)
    surnames = re.findall(r"<surname>([^<]*)</surname>", xml)
    for g, s in zip(given, surnames):
        out.add(f"{g} {s}".strip())
        out.add(s.strip())
    return {squash(o) for o in out if o}


def is_furniture(paragraph: str, furniture: set[str]) -> bool:
    p = paragraph.strip()
    return bool(re.fullmatch(r"\d{1,4}", p)) or squash(p) in furniture


def pdf_paragraphs(pdf_path: Path) -> list[str]:
    """Paragraphs as the PDF itself delimits them: a trailing space continues."""
    doc = fitz.open(pdf_path)
    text = "".join(doc[i].get_text() for i in range(doc.page_count))
    doc.close()

    paragraphs, current = [], ""
    for line in text.split("\n"):
        if not line.strip():
            if current:
                paragraphs.append(current.strip())
                current = ""
            continue
        current += line if line.endswith(" ") else line + "\n"
        if not line.endswith(" "):
            paragraphs.append(current.strip())
            current = ""
    if current:
        paragraphs.append(current.strip())
    return [p.replace("\n", " ") for p in paragraphs if p.strip()]


def resplit(stream: str, targets: list[str]) -> list[str] | None:
    """Cut `stream` (HTML) into pieces whose plain text matches `targets`.

    Walks the stream character by character, counting only non-tag,
    non-whitespace characters, and cuts when the running count reaches the
    length of the current target. Returns None if the text doesn't line up.
    """
    pieces, pos = [], 0
    for target in targets:
        want = len(squash(target))
        if want == 0:
            continue
        seen, i, in_tag = 0, pos, False
        while i < len(stream) and seen < want:
            ch = stream[i]
            if ch == "<":
                in_tag = True
            elif ch == ">":
                in_tag = False
            elif not in_tag and not ch.isspace() and not NOISE.match(ch):
                # Count exactly what squash() counts. When these two disagreed,
                # every section matched the PDF and then failed to re-split.
                seen += 1
            i += 1
        if seen < want:
            return None
        # Carry any trailing tag close (e.g. "</italic>") with this piece.
        while i < len(stream) and stream[i] in " \n\t":
            i += 1
        pieces.append(stream[pos:i].strip())
        pos = i
    remainder = stream[pos:]
    if squash(remainder):
        return None
    return pieces


def reflow(xml: str, pdf_path: Path) -> tuple[str, list[str], list[str]]:
    """Returns (new_xml, sections_rebuilt, sections_refused)."""
    furniture = furniture_strings(xml)
    pdf_paras = [p for p in pdf_paragraphs(pdf_path) if not is_furniture(p, furniture)]
    body_start, body_end = xml.find("<body>"), xml.rfind("</body>")
    if body_start < 0:
        return xml, [], ["no <body>"]

    rebuilt, refused = [], []
    out, cursor = [], body_start

    for sec in re.finditer(r"<sec>(.*?)</sec>", xml[body_start:body_end], re.S):
        inner = sec.group(1)
        title_m = re.search(r"<title>(.*?)</title>", inner, re.S)
        title = TAG.sub("", title_m.group(1)).strip() if title_m else "(untitled)"
        paras = PARAGRAPH.findall(inner)
        if len(paras) < 2:
            continue

        combined = squash("".join(paras))
        # Find the run of PDF paragraphs that reconstructs this section.
        targets, acc, start = None, "", None
        for i in range(len(pdf_paras)):
            acc, start = "", i
            for j in range(i, len(pdf_paras)):
                acc += squash(pdf_paras[j])
                if acc == combined:
                    targets = pdf_paras[start : j + 1]
                    break
                if not combined.startswith(acc):
                    break
            if targets:
                break

        if not targets:
            refused.append(f"{title} — could not match against the PDF")
            continue
        if len(targets) == len(paras):
            continue  # already correct

        stream = " ".join(paras)
        pieces = resplit(stream, targets)
        if pieces is None:
            refused.append(f"{title} — text did not line up when re-splitting")
            continue

        new_inner = inner
        first = re.search(r"<p>", inner)
        last = list(PARAGRAPH.finditer(inner))[-1]
        replacement = "\n".join(f"<p>{p}</p>" for p in pieces)
        new_inner = inner[: first.start()] + replacement + inner[last.end() :]

        abs_start = body_start + sec.start()
        abs_end = body_start + sec.end()
        out.append((abs_start, abs_end, "<sec>" + new_inner + "</sec>"))
        rebuilt.append(f"{title}: {len(paras)} -> {len(pieces)} paragraphs")

    for abs_start, abs_end, text in reversed(out):
        xml = xml[:abs_start] + text + xml[abs_end:]
    return xml, rebuilt, refused


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    total_changed = 0
    for f in map(Path, args.files):
        pdf = Path(str(f).replace(".jats.xml", ".pdf"))
        if not pdf.exists():
            print(f"  SKIP {f.name[:52]} — no PDF beside it")
            continue
        xml = f.read_text(encoding="utf-8")
        new_xml, rebuilt, refused = reflow(xml, pdf)
        if not rebuilt and not refused:
            continue
        print(f"  {f.parent.name}/{f.name[:54]}")
        for r in rebuilt:
            print(f"    {'FIX ' if args.write else 'WOULD'} {r}")
        for r in refused:
            print(f"    SKIP {r}")
        if rebuilt:
            total_changed += 1
            if args.write and squash(new_xml) == squash(xml):
                f.write_text(new_xml, encoding="utf-8")
            elif args.write:
                print("    ABORTED — reflow changed the text, not just the markup")

    print(f"\n{'Rebuilt' if args.write else 'Would rebuild'} {total_changed} article(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
