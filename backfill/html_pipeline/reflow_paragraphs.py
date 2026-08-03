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
import xml.etree.ElementTree as ET
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
    # Section headings count as furniture for MATCHING purposes: they sit in the
    # PDF's line stream between the paragraph runs they introduce, but they are
    # <title> in the JATS, never <p>. Leaving them in makes every multi-section
    # run look discontiguous.
    out = {JOURNAL_TITLE}
    out.update(re.sub(r"\s+", " ", TAG.sub("", t)).strip()
               for t in re.findall(r"<title>(.*?)</title>", xml, re.S))
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


# The trailing-space convention belongs to a typesetting workflow, not to PDFs in
# general. Newer born-digital issues mark wrapped lines with a trailing space;
# older volumes do not, and there EVERY line looks like a paragraph end, so
# reflowing shreds correct prose into one paragraph per line — the exact defect
# it exists to repair, inflicted on healthy articles.
#
# Measured over the whole corpus before this gate existed: of the articles the
# tool would change, all 24 it improved had a ratio of 0.65 or higher (median
# 0.80), while the 48 it made worse had a median of 0.00. Below the threshold
# the PDF cannot answer the question and the article is left alone.
MIN_WRAP_RATIO = 0.65


def wrap_ratio(pdf_path: Path) -> float:
    """Share of non-blank lines that end with a space, i.e. that are wrapped."""
    doc = fitz.open(pdf_path)
    text = "".join(doc[i].get_text() for i in range(doc.page_count))
    doc.close()
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return 0.0
    return sum(1 for l in lines if l.endswith(" ")) / len(lines)


def pdf_paragraphs(pdf_path: Path, furniture: set[str] | None = None) -> list[str]:
    """Paragraphs as the PDF itself delimits them: a trailing space continues.

    Furniture is dropped at LINE level, before paragraphs are assembled, which
    is the only place it can be. A running head lands wherever the page breaks,
    so it usually falls in the middle of a wrapped paragraph and gets glued into
    it — "...a state of anxiety The Twelve-Day War Experience: A
    phenomenological-existential reappraisal and agitation...". Filtering
    assembled paragraphs, as an earlier version did, never sees it: the header
    is not a paragraph, it is a splinter inside one. Removing the line first
    also lets the text either side rejoin, which is what the page does visually.
    """
    furniture = furniture or set()
    doc = fitz.open(pdf_path)
    text = "".join(doc[i].get_text() for i in range(doc.page_count))
    doc.close()

    paragraphs, current = [], ""
    for line in text.split("\n"):
        if is_furniture(line, furniture):
            continue
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


# A paragraph that ends mid-sentence is the visible symptom of the defect, so it
# is also the measure of whether a repair helped.
TERMINAL = re.compile(r"[.!?:;”\"’')\]]\s*$")


def count_midsentence(xml: str) -> int:
    body = xml[xml.find("<body>") : xml.rfind("</body>")]
    paras = [re.sub(r"\s+", " ", TAG.sub("", m)).strip() for m in PARAGRAPH.findall(body)]
    return sum(1 for p in paras if p and not TERMINAL.search(p))


def paragraph_runs(body: str) -> list[tuple[str, list]]:
    """Every maximal run of consecutive <p>, tagged with its heading.

    A run, not a section. Block quotes sit between paragraphs — three sections of
    37.2/13 are built that way — so treating a whole heading as one span
    swallowed the <disp-quote> and had to refuse. Per-run also means one awkward
    run no longer costs the rest of the section.
    """
    runs, run, title = [], [], "(untitled)"
    for m in re.finditer(r"<title>.*?</title>|<p>.*?</p>|<[^>]+>|[^<\s][^<]*", body, re.S):
        frag = m.group(0)
        if frag.startswith("<p>"):
            run.append(m)
            continue
        if len(run) >= 2:
            runs.append((title, run))
        run = []
        if frag.startswith("<title>"):
            title = re.sub(r"\s+", " ", TAG.sub("", frag)).strip()
    if len(run) >= 2:
        runs.append((title, run))
    return runs


def reflow(xml: str, pdf_path: Path) -> tuple[str, list[str], list[str]]:
    """Returns (new_xml, runs_rebuilt, runs_refused)."""
    original = xml
    ratio = wrap_ratio(pdf_path)
    if ratio < MIN_WRAP_RATIO:
        return xml, [], [
            f"PDF does not mark wrapped lines ({ratio:.0%} of lines end with a "
            f"space, need {MIN_WRAP_RATIO:.0%}) — cannot tell a wrap from a "
            f"paragraph break, so leaving it alone"
        ]

    furniture = furniture_strings(xml)
    pdf_paras = pdf_paragraphs(pdf_path, furniture)
    body_start, body_end = xml.find("<body>"), xml.rfind("</body>")
    if body_start < 0:
        return xml, [], ["no <body>"]

    rebuilt, refused, edits = [], [], []
    body = xml[body_start:body_end]

    for title, run in paragraph_runs(body):
        # A reference list is already one entry per paragraph by design — that is
        # not wrapped prose and must never be re-flowed into a block.
        if re.search(r"\b(references|bibliography|notes|endnotes)\b", title, re.I):
            continue

        paras = [PARAGRAPH.match(m.group(0)).group(1) for m in run]
        combined = squash("".join(paras))

        # The contiguous run of PDF paragraphs that reconstructs this run.
        targets = None
        for i in range(len(pdf_paras)):
            acc = ""
            for j in range(i, len(pdf_paras)):
                acc += squash(pdf_paras[j])
                if acc == combined:
                    targets = pdf_paras[i : j + 1]
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

        pieces = resplit(" ".join(paras), targets)
        if pieces is None:
            refused.append(f"{title} — text did not line up when re-splitting")
            continue

        edits.append((body_start + run[0].start(), body_start + run[-1].end(),
                      "\n".join(f"<p>{p}</p>" for p in pieces)))
        rebuilt.append(f"{title}: {len(paras)} -> {len(pieces)} paragraphs")

    for a, b, text in reversed(edits):
        xml = xml[:a] + text + xml[b:]

    # Acceptance test, on the defect itself rather than on a proxy: reflowing is
    # only worth doing if FEWER paragraphs end mid-sentence afterwards. Where it
    # ends up with more, the PDF's paragraphs are not the article's — verse is
    # the clearest case, since a poem's line breaks are the author's and the
    # page wraps them like any other text (37.1/06, "poetic reflections", went
    # from 4 mid-sentence paragraphs to 38). Twenty-two articles behaved this
    # way; each is now declined whole rather than half-improved.
    if edits and count_midsentence(xml) > count_midsentence(original):
        return original, [], [
            "reflow left MORE paragraphs ending mid-sentence than it found — "
            "the PDF's paragraph structure is not this article's, so declining it"
        ]
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
            if args.write:
                # Both gates, every file: the text must be unchanged, and the
                # result must still parse. Reflow only moves paragraph
                # boundaries, so anything else is a bug and must not reach disk.
                if squash(new_xml) != squash(xml):
                    print("    ABORTED — reflow changed the text, not just the markup")
                    continue
                try:
                    ET.fromstring(new_xml)
                except ET.ParseError as e:
                    print(f"    ABORTED — result is not well-formed XML: {e}")
                    continue
                f.write_text(new_xml, encoding="utf-8")

    print(f"\n{'Rebuilt' if args.write else 'Would rebuild'} {total_changed} article(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
