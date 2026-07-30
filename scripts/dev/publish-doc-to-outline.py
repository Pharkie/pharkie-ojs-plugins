#!/usr/bin/env python3
"""
Publish a repo markdown doc to Outline, keeping the repo copy canonical.

    python3 scripts/dev/publish-doc-to-outline.py docs/new-issue-runbook.md <outline-doc-id>
    ... --dry-run     # print what would be sent, change nothing

Two transforms, both needed because a repo doc and a wiki page are not the same
artefact:

1. Relative links are rewritten to absolute GitHub URLs. Doing this by hand last
   time produced two broken links: CLAUDE.md pointed at docs/CLAUDE.md when it
   lives at the repo root, and migration-import.md pointed into THIS repo when it
   lives in membership-platform. So the path is resolved properly rather than
   prefixed.
2. Hard-wrapped prose is unwrapped. A 78-column wrap reads correctly in a repo and
   badly in a browser. Code fences, tables, lists, headings and quotes are left
   exactly as they are.

The token is read from the macOS keychain (`sea-outline-api`), never passed on the
command line, so it stays out of shell history and `ps`.
"""
import argparse, json, os, posixpath, re, subprocess, sys, urllib.request

OUTLINE = "https://docs.existentialanalysis.org.uk"
THIS_REPO = "https://github.com/Pharkie/pharkie-ojs-plugins/blob/main"
# Sibling repos, keyed by the leading path segment a relative link uses to escape.
SIBLING_REPOS = {"membership-platform": "https://github.com/Pharkie/sea-membership-platform/blob/main"}


def absolutise(md: str, doc_path: str) -> str:
    """Rewrite relative markdown links to absolute GitHub URLs."""
    doc_dir = posixpath.dirname(doc_path)

    def fix(m: re.Match[str]) -> str:
        label, target = m.group(1), m.group(2)
        if re.match(r"^(https?:|mailto:|#)", target):
            return m.group(0)
        path, _, frag = target.partition("#")
        resolved = posixpath.normpath(posixpath.join(doc_dir, path))
        # A link that climbed out of this repo lands on a sibling checkout.
        for name, base in SIBLING_REPOS.items():
            if resolved.startswith(f"../{name}/") or resolved.startswith(f"{name}/"):
                rest = resolved.split(f"{name}/", 1)[1]
                return f"[{label}]({base}/{rest}{'#' + frag if frag else ''})"
        resolved = resolved.lstrip("./")
        return f"[{label}]({THIS_REPO}/{resolved}{'#' + frag if frag else ''})"

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", fix, md)


def unwrap(md: str) -> str:
    """Join hard-wrapped prose lines; leave every structural block alone."""
    out, in_fence = [], False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        structural = in_fence or not line.strip() or re.match(r"^\s*(#|[-*+]\s|\d+\.\s|>|\||\s{4,}\S)", line)
        if structural or not out or not out[-1].strip():
            out.append(line)
            continue
        prev = out[-1]
        if re.match(r"^\s*(#|[-*+]\s|\d+\.\s|>|\||\s{4,}\S)", prev) or prev.lstrip().startswith("```"):
            out.append(line)
        else:
            out[-1] = prev.rstrip() + " " + line.strip()
    return "\n".join(out)


def api(path: str, payload: dict, token: str) -> dict:
    req = urllib.request.Request(
        f"{OUTLINE}/api/{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("outline_id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    raw = open(a.doc).read()
    body = unwrap(absolutise(raw, a.doc))
    # Outline holds the title separately, so the H1 would render twice.
    body = re.sub(r"\A#\s+.*\n+", "", body)

    if a.dry_run:
        print(body)
        return 0

    token = subprocess.run(
        ["security", "find-generic-password", "-s", "sea-outline-api", "-w"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    before = api("documents.info", {"id": a.outline_id}, token)["data"]
    after = api("documents.update", {"id": a.outline_id, "text": body}, token)["data"]
    print(f"Published {a.doc} -> {after['title']!r}")
    print(f"  {len(before['text'])} chars -> {len(after['text'])}")
    print(f"  {OUTLINE}/doc/{after.get('urlId') or after['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
