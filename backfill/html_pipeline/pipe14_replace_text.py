#!/usr/bin/env python3
"""
Replace the text of published articles in OJS with corrected text from the
source repo (sea-ojs-private), in place: the "Full Text" HTML galley file and
the "JATS XML" galley file, nothing else.

Harbour's twin is `scripts/ops/apply-article-text.ts`; the source is fixed
once and both take it from there. Which articles: every
`backfill/output/<issue>/NN-slug.{galley.html,jats.xml}` that differs between
two commits of the source repo. Which submission: the OJS submission id the
JATS carries (`<article-id pub-id-type="publisher-id">`), never the title.

How the file is replaced, and why that way: pipe6 imported each `.galley.html`
wrapped in a small HTML document, and OJS keeps the bytes as a file under
/var/www/files that the `files` row points at. The inline galley plugin reads
that file on every view (no cache), so overwriting it in place, keeping its
row, path, owner and mode, changes what readers see and nothing else: no new
submission file, no new galley, no new publication version. That is what
pipe13 --galleys already does; this adds the comparison, a backup and undo.
The written HTML is wrapped exactly as pipe6 wraps it.

Each file is compared with the base and the head by its <body> content (so a
file pipe13 --galleys copied unwrapped still counts):
    unchanged  already the head's
    change     the base's: replaced by the head's
    other      neither (an earlier pipeline run, or edited since): reported
               with its word difference from the head, replaced only with
               --include-other
    missing    OJS has no such galley for the submission

Usage (from this repo's root; --repo is a checkout of sea-ojs-private):
    python3 backfill/html_pipeline/pipe14_replace_text.py \\
        --repo private --base <ref> --head <ref> --target live          # dry run
    ... --target live --confirm --out-dir <dir>      # backup to <dir>, write, reindex
    python3 backfill/html_pipeline/pipe14_replace_text.py \\
        --restore <dir> --target live [--confirm]    # put the backed-up files back

--only <issue>/<stem> (repeatable) narrows the run. --confirm refuses while the
OJS job queue holds jobs (the reindex drains the queue). After writing it
dispatches one search reindex job per changed submission and drains them.
"""

import argparse
import difflib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from pipe13_patch_metadata import (  # noqa: E402
    SCRIPT_DIR as _P13_DIR, fetch_galleys, ojs_php_cmd, queue_counts)

FILES_ROOT = '/var/www/files'
SOURCE = re.compile(r'^backfill/output/([^/]+)/(.+)\.(galley\.html|jats\.xml)$')
PUBLISHER_ID = re.compile(r'<article-id pub-id-type="publisher-id">\s*(\d+)\s*<')
BODY = re.compile(r'<body[^>]*>(.*?)</body>', re.S | re.I)


def wrap(galley):
    """The HTML document pipe6 imports a .galley.html as (load_html_galley)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Full Text</title></head>
<body>
{galley.strip()}
</body>
</html>"""


def body_of(doc):
    m = BODY.search(doc)
    return (m.group(1) if m else doc).strip()


def words(html):
    text = re.sub(r'<[^>]+>', ' ', html or '')
    for a, b in (('&nbsp;', ' '), ('&lt;', '<'), ('&gt;', '>'),
                 ('&quot;', '"'), ('&#39;', "'"), ('&amp;', '&')):
        text = text.replace(a, b)
    return text.split()


def word_changes(before, after):
    """(added, removed) words between two texts."""
    a, b = words(before), words(after)
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    ea, eb = len(a), len(b)
    while ea > i and eb > i and a[ea - 1] == b[eb - 1]:
        ea -= 1
        eb -= 1
    a, b = a[i:ea], b[i:eb]
    added = removed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, a, b, autojunk=False).get_opcodes():
        if op != 'equal':
            removed += i2 - i1
            added += j2 - j1
    return added, removed


# ── The source repo ─────────────────────────────────────────────────────────

def git_show(repo, ref, path):
    p = subprocess.run(['git', '-C', repo, 'show', f'{ref}:{path}'],
                       capture_output=True)
    if p.returncode != 0:
        return None
    text = p.stdout.decode('utf-8')
    if text.startswith('version https://git-lfs'):
        raise SystemExit(f'{path} at {ref} is a Git LFS pointer, not the file')
    return text


def changed_articles(repo, base, head, only=()):
    """Every article whose galley or JATS differs between base and head."""
    names = subprocess.run(
        ['git', '-C', repo, 'diff', '--name-only', base, head, '--',
         'backfill/output'], capture_output=True, text=True, check=True).stdout
    stems = sorted({m.group(1, 2) for m in map(SOURCE.match, names.split('\n'))
                    if m})
    out = []
    for issue, stem in stems:
        if only and f'{issue}/{stem}' not in only:
            continue
        files = {}
        for ref_name, ref in (('base', base), ('head', head)):
            files[ref_name] = {
                'galley': git_show(repo, ref, f'backfill/output/{issue}/{stem}.galley.html'),
                'jats': git_show(repo, ref, f'backfill/output/{issue}/{stem}.jats.xml'),
            }
        jats = files['head']['jats'] or files['base']['jats'] or ''
        m = PUBLISHER_ID.search(jats)
        out.append({'issue': issue, 'stem': stem,
                    'submission_id': int(m.group(1)) if m else None, **files})
    return out


# ── The plan (pure) ─────────────────────────────────────────────────────────

def classify(current, base, head):
    """One galley file: unchanged / change / other, by <body> content."""
    now = body_of(current)
    if now == body_of(head):
        return 'unchanged'
    if now == body_of(base):
        return 'change'
    return 'other'


def plan(articles, galleys, current, include_other=False):
    """What to write.

    articles: changed_articles(); galleys: {submission_id: [(label, container
    path, mime)]} (pipe13 fetch_galleys); current: {container path: str}.
    Returns (writes, report): writes is [(path, new text, submission id)],
    report one dict per galley file (tag, galley, state, note).
    """
    writes, report = [], []

    def row(tag, galley, state, note=''):
        report.append({'tag': tag, 'galley': galley, 'state': state, 'note': note})

    for art in articles:
        tag = f"{art['issue']}/{art['stem']}"
        sid = art['submission_id']
        if not all(art[r][k] for r in ('base', 'head') for k in ('galley', 'jats')):
            row(tag, '-', 'skipped', "a file was added or removed (a new article is pipe6/pipe7's job)")
            continue
        if sid is None:
            row(tag, '-', 'skipped', 'the JATS carries no submission id')
            continue
        by_kind = {}
        for _label, path, mime in galleys.get(sid, []):
            if mime == 'text/html':
                by_kind['html'] = path
            elif 'xml' in mime:
                by_kind['jats'] = path
        for kind, label, new, base in (
                ('html', 'Full Text', wrap(art['head']['galley']), wrap(art['base']['galley'])),
                ('jats', 'JATS XML', art['head']['jats'], art['base']['jats'])):
            path = by_kind.get(kind)
            if not path or path not in current:
                row(tag, label, 'missing', f'submission {sid} has no {label} galley on disk')
                continue
            state = classify(current[path], base, new)
            write = state == 'change' or (state == 'other' and include_other)
            note = f'submission {sid}'
            if state != 'unchanged':
                added, removed = word_changes(body_of(current[path]), body_of(new))
                note = f'+{added} -{removed} words  {note}'
            if write and state == 'other':
                note += '  (written: --include-other)'
            row(tag, label, state, note)
            if write:
                writes.append((path, new, sid))
    return writes, report


# ── OJS ─────────────────────────────────────────────────────────────────────

def ojs_cmd(target, command):
    if target == 'dev':
        return ['docker', 'compose', 'exec', '-T', 'ojs', 'bash', '-c', command]
    return ['ssh', 'sea-live',
            f"cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs bash -c '{command}'"]


def rel(path):
    assert path.startswith(FILES_ROOT + '/'), path
    return path[len(FILES_ROOT) + 1:]


def read_files(target, paths):
    """{container path: (text, TarInfo)} for every path that exists."""
    found = {}
    paths = sorted(set(paths))
    for i in range(0, len(paths), 200):
        chunk = paths[i:i + 200]
        names = ' '.join(f'"{rel(p)}"' for p in chunk)
        p = subprocess.run(ojs_cmd(target, f'cd {FILES_ROOT} && tar --ignore-failed-read -cf - {names} 2>/dev/null; true'),
                           capture_output=True, timeout=600)
        with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    data = tar.extractfile(member).read()
                    found[f'{FILES_ROOT}/{member.name}'] = (data.decode('utf-8'), member)
    return found


def write_files(target, files):
    """files: [(container path, bytes, TarInfo of the file it replaces)].

    One tar into the container, each entry carrying the old file's owner and
    mode, so OJS reads it exactly as before.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        for path, data, old in files:
            info = tarfile.TarInfo(rel(path))
            info.size = len(data)
            info.mode, info.uid, info.gid = old.mode, old.uid, old.gid
            info.uname, info.gname = old.uname, old.gname
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    p = subprocess.run(ojs_cmd(target, f'tar -xpf - --same-owner -C {FILES_ROOT}'),
                       input=buf.getvalue(), capture_output=True, timeout=600)
    if p.returncode != 0:
        raise SystemExit(f'writing into OJS failed: {p.stderr.decode()[:500]}')


def reindex(target, ids):
    php_src = (_P13_DIR / 'pipe13_reindex.php').read_text()
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        p = subprocess.run(ojs_php_cmd(target, chunk), input=php_src,
                           capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            print('WARNING: reindex dispatch failed; search keeps the old words '
                  'for these articles until a reindex.', file=sys.stderr)
            return
    drain = ('cd /var/www/html && for i in $(seq %d); do '
             'php lib/pkp/tools/jobs.php run --once 2>&1 | grep -q "No jobs" && break; done'
             % (len(ids) + 2))
    subprocess.run(ojs_cmd(target, drain), capture_output=True, timeout=7200)
    print(f'Reindexed {len(ids)} submission(s).')


# ── Main ────────────────────────────────────────────────────────────────────

def save_backup(out_dir, base, head, entries):
    """entries: [(container path, old text, TarInfo)]."""
    out = Path(out_dir)
    (out / 'files').mkdir(parents=True, exist_ok=True)
    manifest = {'base': base, 'head': head, 'files': []}
    for path, old, info in entries:
        local = out / 'files' / rel(path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(old, encoding='utf-8')
        manifest['files'].append({'path': path, 'backup': str(local.relative_to(out)),
                                  'mode': info.mode, 'uid': info.uid, 'gid': info.gid,
                                  'uname': info.uname, 'gname': info.gname})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1))


def restore(args):
    out = Path(args.restore)
    manifest = json.loads((out / 'manifest.json').read_text())
    files = []
    for f in manifest['files']:
        info = tarfile.TarInfo(rel(f['path']))
        info.mode, info.uid, info.gid = f['mode'], f['uid'], f['gid']
        info.uname, info.gname = f['uname'], f['gname']
        files.append((f['path'], (out / f['backup']).read_bytes(), info))
    print(f"{len(files)} file(s) to put back ({manifest['base']}..{manifest['head']} undone).")
    if not args.confirm:
        print('DRY RUN: nothing written. Re-run with --confirm.')
        return
    write_files(args.target, files)
    ids = sorted({int(p.split('/articles/')[1].split('/')[0]) for p, _, _ in files if '/articles/' in p})
    if not args.no_reindex:
        reindex(args.target, ids)
    print('Restored.')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--target', choices=['dev', 'live'], required=True)
    ap.add_argument('--repo', help='a checkout of sea-ojs-private')
    ap.add_argument('--base')
    ap.add_argument('--head')
    ap.add_argument('--only', action='append', default=[])
    ap.add_argument('--include-other', action='store_true',
                    help='also replace files that are neither the base nor the head')
    ap.add_argument('--confirm', action='store_true')
    ap.add_argument('--out-dir', help='where --confirm keeps the backup and manifest')
    ap.add_argument('--no-reindex', action='store_true')
    ap.add_argument('--restore', metavar='DIR')
    args = ap.parse_args()
    if args.restore:
        return restore(args)
    if not (args.repo and args.base and args.head):
        ap.error('--repo, --base and --head are needed (or --restore)')
    if args.confirm and not args.out_dir:
        ap.error('--confirm needs --out-dir for the backup')

    articles = changed_articles(args.repo, args.base, args.head, set(args.only))
    ids = sorted({a['submission_id'] for a in articles if a['submission_id']})
    galleys = {}
    for i in range(0, len(ids), 300):
        galleys.update(fetch_galleys(args.target, ids[i:i + 300]))
    paths = [p for g in galleys.values() for (_, p, m) in g if m == 'text/html' or 'xml' in m]
    on_disk = read_files(args.target, paths)
    writes, report = plan(articles, galleys, {p: t for p, (t, _) in on_disk.items()},
                          args.include_other)
    states = {}
    for r in report:
        print(f"{r['tag']}  {r['galley']}  {r['state']}  {r['note']}".rstrip())
        states[r['state']] = states.get(r['state'], 0) + 1
    print(f"\n{len(articles)} article(s), {args.base}..{args.head} on {args.target}: "
          + ', '.join(f'{n} {k}' for k, n in sorted(states.items())))
    print(f'{len(writes)} file(s) to write.')
    if not writes:
        return
    if not args.confirm:
        print('DRY RUN: nothing written. Re-run with --confirm --out-dir <dir>.')
        return
    jobs, _ = queue_counts(args.target)
    if jobs:
        raise SystemExit(f'Refusing --confirm: the OJS job queue holds {jobs} job(s); '
                         'the reindex would drain them too.')
    save_backup(args.out_dir, args.base, args.head,
                [(p, on_disk[p][0], on_disk[p][1]) for p, _, _ in writes])
    print(f'Backed up {len(writes)} file(s) to {args.out_dir}.')
    write_files(args.target, [(p, new.encode('utf-8'), on_disk[p][1]) for p, new, _ in writes])
    print(f'Wrote {len(writes)} file(s).')
    if args.no_reindex:
        print('Reindex skipped (--no-reindex): search holds the old words.')
    else:
        reindex(args.target, sorted({sid for _, _, sid in writes}))
    print(f'Undo: python3 backfill/html_pipeline/pipe14_replace_text.py '
          f'--restore {args.out_dir} --target {args.target} --confirm')


if __name__ == '__main__':
    main()
