#!/usr/bin/env python3
"""Repair author bios that the extraction left inside the article body.

WHY THIS EXISTS
---------------
pipe1 extraction populates `author_bios` in toc.json, pipe3 writes them into
<back> as <bio><p>…</p></bio>, and the toc.json fields are then dropped — "JATS
is the source of truth" (pipe3_generate_jats.py). For a handful of articles the
extraction never found the bios, so they stayed inside a body paragraph and are
now frozen into the JATS. The reader gets the closing sentence of the article
running straight into "Michael R. Montgomery is an existential psychoanalyst…".

Not fixable further upstream: OJS holds no author biographies at all (0 of 20,667
author rows), and no toc.json still carries author_bios. The JATS files ARE the
upstream artefact.

TWO SEPARATE DEFECTS, found by measurement
------------------------------------------
Grepping `[a-z]\\.Contact:` matches 18 articles, but they are not one problem:

  RUN-ON (11)  the bio is inside <body>, no <bio> element at all. The real defect.
  SPACING (7)  the bio is correctly in <bio>; only the space before "Contact:" is
               missing ("…freelance writer.Contact: charles@…"). Cosmetic.

Both are repaired here, reported separately, because conflating them overstates
the damage — most of the 18 are already structurally correct.

WHAT THE RUN-ON REPAIR DOES

  before  …become a vessel for something sacred.<bold>Michael R. Montgomery</bold>
          is an existential psychoanalyst… United States.Contact: michael@…</p>

  after   …become a vessel for something sacred.</p>
          …
          <back>… <bio><p>Michael R. Montgomery is an existential psychoanalyst…
          United States. Contact: michael@…</p></bio></back>

Bios are placed after <ref-list>/<fn-group> and before any provenance note, which
is where pipe3 puts them, and the <bold> markup is dropped: all 881 bios in the
corpus are plain text.

THE BOUNDARY, and why it is safe
--------------------------------
A bio run starts at a <bold> whose text — up to the next <bold> — contains
"Contact:". That is what a bio is in this corpus and what body prose never is: an
article's own paragraphs do not carry an email address and an ORCID. Each further
<bold> in the run starts the next author.

The run is not always in the LAST paragraph. Four articles carry a short
"Dedication" or "Acknowledgements" section after the bios, so every body
paragraph is scanned and the last qualifying one is used — an earlier version
looked only at the final paragraph and silently skipped those four.

Refuses rather than guesses: no qualifying paragraph, or a run longer than
TAIL_LIMIT, is reported and skipped. Bad separation is recoverable; a body
paragraph moved into a bio is not.

USAGE
    python3 fix_runon_bios.py <file.jats.xml>...            # report only
    python3 fix_runon_bios.py --write <file.jats.xml>...    # rewrite in place
"""

import argparse
import re
import sys
from pathlib import Path

# A bio run is the tail of a paragraph, not the bulk of one. Well past the longest
# real run in the corpus (~900 chars, two authors) and far short of a body
# paragraph, so an unexpected shape is refused instead of mangled.
TAIL_LIMIT = 2500

BOLD = re.compile(r"<bold>")
PARAGRAPH = re.compile(r"<p>(.*?)</p>", re.S)
BIO_ELEMENT = re.compile(r"(<bio>\s*<p>)(.*?)(</p>\s*</bio>)", re.S)
# "…freelance writer.Contact: charles@…" — a missing space in the source PDF, not
# something the pipeline introduced, but this is the moment it can be put right.
MISSING_SPACE = re.compile(r"([a-z,.\)])(Contact:)")
# The same glue defect one step later: "…orcid.org/0009-0007-7136-9453This paper is
# adapted from an essay…" — the bio ends at the ORCID and a provenance note has been
# run onto it. An email or ORCID is never followed by more bio prose with no space,
# so the boundary is unambiguous. One article in the corpus (37.2/05).
PROVENANCE_GLUE = re.compile(
    r"((?:orcid\.org/[\dX-]+)|(?:[\w.+-]+@[\w.-]+\.[a-z]{2,}))(?=[A-Z][a-z])"
)


def bio_run_start(paragraph: str) -> int | None:
    """Index where the trailing bio run begins, or None if this isn't one."""
    marks = [m.start() for m in BOLD.finditer(paragraph)]
    for i, start in enumerate(marks):
        end = marks[i + 1] if i + 1 < len(marks) else len(paragraph)
        if "Contact:" in paragraph[start:end]:
            return start
    return None


def split_bios(run: str) -> tuple[list[str], str | None]:
    """One entry per author, plus any provenance note glued to the last of them."""
    marks = [m.start() for m in BOLD.finditer(run)]
    out = []
    for i, start in enumerate(marks):
        end = marks[i + 1] if i + 1 < len(marks) else len(run)
        # Corpus convention: bios carry no markup, so the bold on the name goes.
        bio = re.sub(r"</?bold>", "", run[start:end]).strip()
        bio = MISSING_SPACE.sub(r"\1 \2", bio)
        if bio:
            out.append(bio)

    provenance = None
    if out:
        glue = PROVENANCE_GLUE.search(out[-1])
        if glue:
            provenance = out[-1][glue.end() :].strip()
            out[-1] = out[-1][: glue.end()].strip()
    return out, provenance


def insert_bios(xml: str, bios: list[str], provenance: str | None) -> str:
    """Place <bio> elements where pipe3 places them: end of <back>, after the
    references and notes, before any provenance; creating <back> if there is none."""
    block = "\n".join(f"<bio><p>{b}</p></bio>" for b in bios)
    if provenance:
        block += f'\n<notes notes-type="provenance"><p>{provenance}</p></notes>'
    existing = xml.find('<notes notes-type="provenance"')
    if existing >= 0:
        return xml[:existing] + block + "\n" + xml[existing:]
    if "</back>" in xml:
        return xml.replace("</back>", f"{block}\n</back>", 1)
    return xml.replace("</body>", f"</body>\n<back>\n{block}\n</back>", 1)


def move_runon_bios(xml: str) -> tuple[str, list[str], str | None, str | None]:
    """Returns (new_xml, bios_moved, provenance_split_off, refusal_reason)."""
    body_start, body_end = xml.find("<body>"), xml.rfind("</body>")
    if body_start < 0 or body_end < 0:
        return xml, [], None, "no <body>"

    body = xml[body_start:body_end]
    # The LAST qualifying paragraph: bios sit at the end of the prose, but a
    # dedication or acknowledgement can follow them.
    match = None
    for m in PARAGRAPH.finditer(body):
        if bio_run_start(m.group(1)) is not None:
            match = m
    if match is None:
        return xml, [], None, "no bio run in <body>"

    paragraph = match.group(1)
    at = bio_run_start(paragraph)
    run = paragraph[at:]
    if len(run) > TAIL_LIMIT:
        return xml, [], None, f"bio run is {len(run)} chars, over the {TAIL_LIMIT} limit"

    bios, provenance = split_bios(run)
    if not bios:
        return xml, [], None, "bio run split to nothing"

    kept = paragraph[:at].rstrip()
    new_body = body[: match.start()] + f"<p>{kept}</p>" + body[match.end() :]
    fixed = insert_bios(xml[:body_start] + new_body + xml[body_end:], bios, provenance)
    return fixed, bios, provenance, None


def fix_bio_spacing(xml: str) -> tuple[str, int]:
    """Add the missing space before "Contact:" inside existing <bio> elements."""
    count = 0

    def repair(m: re.Match) -> str:
        nonlocal count
        fixed, n = MISSING_SPACE.subn(r"\1 \2", m.group(2))
        count += n
        return m.group(1) + fixed + m.group(3)

    return BIO_ELEMENT.sub(repair, xml), count


def flatten(bio: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", bio)).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--write", action="store_true", help="rewrite in place (default: report only)")
    args = ap.parse_args()
    verb = "FIX " if args.write else "WOULD"

    moved = spaced = skipped = 0
    for path in map(Path, args.files):
        original = path.read_text(encoding="utf-8")
        xml, note = original, None

        if "<bio>" in xml:
            xml, n = fix_bio_spacing(xml)
            if n:
                spaced += 1
                note = f"{verb} spacing: {n} missing space(s) before Contact:"
        else:
            xml, bios, provenance, refusal = move_runon_bios(xml)
            if refusal:
                skipped += 1
                note = f"SKIP {refusal}"
            else:
                moved += 1
                note = f"{verb} run-on: {len(bios)} bio(s) moved to <back>"
                for b in bios:
                    flat = flatten(b)
                    note += f"\n         · {flat[:100]}{'…' if len(flat) > 100 else ''}"
                if provenance:
                    note += f"\n         + provenance split off: {flatten(provenance)[:100]}…"

        if note:
            print(f"  {path.parent.name}/{path.name[:56]}\n    {note}")
        if args.write and xml != original:
            path.write_text(xml, encoding="utf-8")

    tense = "Moved" if args.write else "Would move"
    print(f"\n{tense} bios in {moved} article(s); spacing in {spaced}; skipped {skipped}.")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
