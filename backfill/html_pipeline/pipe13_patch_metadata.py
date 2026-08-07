#!/usr/bin/env python3
"""
Patch article metadata (author names, title) from JATS into OJS in place.

The fast path for metadata-only corrections — a misspelt author, a title
typo — that previously required a full issue reimport (pipe7 --force, a
search reindex storm, pipe8/9b/9c). JATS stays the single source of truth:
edit toc.json (and the authors registry — fix that FIRST or the split-step
normaliser will revert you), rerun pipe1d–pipe5 for the issue so the JATS
and galleys are right, then run this instead of pipe6+pipe7.

What it patches:  givenName / familyName (matched by author order), title,
and copyrightHolder (stamped from author names at publish time, so a name
fix follows the changed names into it by string replacement).
With --galleys it ALSO replaces each scoped article's galley files (PDF,
Full Text HTML, JATS XML) with the regenerated pipeline outputs — needed
whenever the correction appears in the article text itself (a byline, a
bio line, a running head), which is almost always true for author names.
What it reports but will NOT patch:  abstract differences, author count
changes, missing submissions — those are structural and need the reimport
path.

After patching it dispatches a scoped search reindex (one job per changed
submission, via pipe13_reindex.php) and drains just those jobs — no
1,400-job rebuild.

Usage:
    # Dry run (default) — show what differs, change nothing:
    python3 backfill/html_pipeline/pipe13_patch_metadata.py \
        backfill/private/output/37.2/toc.json --target dev

    # Apply on dev:
    python3 backfill/html_pipeline/pipe13_patch_metadata.py \
        backfill/private/output/37.2/toc.json --target dev --confirm

    # Apply on live:
    python3 backfill/html_pipeline/pipe13_patch_metadata.py \
        backfill/private/output/37.2/toc.json --target live --confirm

    # Scope to one article (1-indexed position in toc.json):
    ... --article 3

    # Author-name fix that appears in the article text too (the usual case):
    ... --article 3 --galleys --confirm

    # Skip the scoped reindex (e.g. batching several patches):
    ... --confirm --no-reindex
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent

DB_CMD = {
    'dev': [
        'docker', 'compose', 'exec', '-T', 'ojs-db',
        'bash', '-c',
        'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N',
    ],
    'live': [
        'ssh', 'sea-live',
        'cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs-db '
        'bash -c \'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N\'',
    ],
}

# php reads the script from stdin; ids arrive as argv after `--`.
def ojs_php_cmd(target, ids):
    id_args = ' '.join(str(i) for i in ids)
    if target == 'dev':
        return (['docker', 'compose', 'exec', '-T', 'ojs', 'php', '--']
                + [str(i) for i in ids])
    return ['ssh', 'sea-live',
            f'cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs '
            f'php -- {id_args}']


class SqlError(Exception):
    pass


def run_sql(target, sql):
    cmd = list(DB_CMD[target])
    try:
        proc = subprocess.run(cmd, input=sql, capture_output=True, text=True,
                              timeout=120)
    except subprocess.TimeoutExpired:
        raise SqlError('SQL timed out after 120 seconds')
    stderr = '\n'.join(l for l in proc.stderr.splitlines()
                       if 'password on the command line' not in l).strip()
    if proc.returncode != 0:
        raise SqlError(f'SQL failed (exit {proc.returncode}): {stderr}')
    if stderr:
        print(f'  SQL warning: {stderr}', file=sys.stderr)
    return proc.stdout


def esc(s):
    return s.replace('\\', '\\\\').replace("'", "\\'")


def norm(text):
    """Normalise for comparison: strip tags, collapse whitespace."""
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()


def jats_text(el):
    return norm(''.join(el.itertext())) if el is not None else ''


def load_jats(toc_path, only_index=None):
    """Read publisher-id, title, abstract and contribs from each JATS file."""
    toc = json.load(open(toc_path))
    articles = []
    for i, art in enumerate(toc['articles'], 1):
        if only_index is not None and i != only_index:
            continue
        jats_path = os.path.splitext(art['split_pdf'])[0] + '.jats.xml'
        # split_pdf paths are repo-root-relative (like pipe6 reads them), not
        # toc-dir-relative; fall back to the toc dir only if root-relative
        # doesn't resolve.
        if not os.path.isabs(jats_path) and not os.path.exists(jats_path):
            jats_path = os.path.join(os.path.dirname(os.path.abspath(toc_path)),
                                     jats_path)
        tree = ET.parse(jats_path)
        pid_el = tree.find('.//{*}article-id[@pub-id-type="publisher-id"]')
        publisher_id = (pid_el.text or '').strip() if pid_el is not None else ''
        if not publisher_id:
            print(f'ERROR: no publisher-id in {os.path.basename(jats_path)} — '
                  'run snapshot_ids.py first; this tool only patches published '
                  'articles.', file=sys.stderr)
            sys.exit(1)
        contribs = []
        contrib_orcids = []
        for contrib_el in tree.findall('.//{*}contrib-group/{*}contrib'):
            name_el = contrib_el.find('{*}name')
            if name_el is None:
                continue
            given = jats_text(name_el.find('{*}given-names'))
            family = jats_text(name_el.find('{*}surname'))
            contribs.append((given, family))
            oid_el = contrib_el.find('{*}contrib-id[@contrib-id-type="orcid"]')
            contrib_orcids.append(jats_text(oid_el) if oid_el is not None else '')
        base = jats_path[:-len('.jats.xml')]
        articles.append({
            'n': i,
            'label': f"{art.get('title', '?')[:52]} — {art.get('authors', '?')}",
            'submission_id': int(publisher_id),
            'title': jats_text(tree.find('.//{*}article-title')),
            'abstract': jats_text(tree.find('.//{*}abstract')),
            'contribs': contribs,
            'contrib_orcids': contrib_orcids,
            # Local regenerated galley sources, keyed like the DB mimetypes.
            'galley_sources': {
                'application/pdf': base + '.pdf',
                'text/html': base + '.galley.html',
                'xml': base + '.jats.xml',
            },
        })
    return articles


def fetch_db_state(target, submission_ids):
    ids = ','.join(str(i) for i in submission_ids)
    out = run_sql(target, f"""
SELECT s.submission_id, p.publication_id,
       REPLACE(REPLACE(COALESCE(t.setting_value,''), '\\t', ' '), '\\n', ' '),
       REPLACE(REPLACE(COALESCE(ch.setting_value,''), '\\t', ' '), '\\n', ' '),
       REPLACE(REPLACE(COALESCE(ab.setting_value,''), '\\t', ' '), '\\n', ' ')
FROM submissions s
JOIN publications p ON p.publication_id = s.current_publication_id
LEFT JOIN publication_settings t ON t.publication_id = p.publication_id
     AND t.setting_name='title' AND t.locale='en'
LEFT JOIN publication_settings ch ON ch.publication_id = p.publication_id
     AND ch.setting_name='copyrightHolder' AND ch.locale='en'
LEFT JOIN publication_settings ab ON ab.publication_id = p.publication_id
     AND ab.setting_name='abstract' AND ab.locale='en'
WHERE s.submission_id IN ({ids});
""")
    state = {}
    for line in out.splitlines():
        if not line:
            continue
        sid, pub_id, title, copyright_holder, abstract = line.split('\t', 4)
        state[int(sid)] = {'publication_id': int(pub_id),
                           'title': norm(title), 'abstract': norm(abstract),
                           'copyright_holder': copyright_holder.strip(),
                           'authors': []}
    if state:
        pub_ids = ','.join(str(v['publication_id']) for v in state.values())
        out = run_sql(target, f"""
SELECT a.publication_id, a.author_id, a.seq,
       COALESCE(g.setting_value,''), COALESCE(f.setting_value,''),
       COALESCE(o.setting_value,'')
FROM authors a
LEFT JOIN author_settings g ON g.author_id=a.author_id
     AND g.setting_name='givenName'
LEFT JOIN author_settings f ON f.author_id=a.author_id
     AND f.setting_name='familyName'
LEFT JOIN author_settings o ON o.author_id=a.author_id
     AND o.setting_name='orcid'
WHERE a.publication_id IN ({pub_ids})
ORDER BY a.publication_id, a.seq;
""")
        by_pub = {v['publication_id']: v for v in state.values()}
        for line in out.splitlines():
            if not line:
                continue
            pub_id, author_id, _seq, given, family, orcid = line.split('\t', 5)
            by_pub[int(pub_id)]['authors'].append(
                {'author_id': int(author_id), 'given': given.strip(),
                 'family': family.strip(), 'orcid': orcid.strip()})
    return state


def fetch_galleys(target, submission_ids):
    """Map submission_id -> [(label, container_path, mimetype)]."""
    ids = ','.join(str(i) for i in submission_ids)
    out = run_sql(target, f"""
SELECT s.submission_id, g.label, f.path, f.mimetype
FROM publication_galleys g
JOIN submission_files sf ON sf.submission_file_id=g.submission_file_id
JOIN files f ON f.file_id=sf.file_id
JOIN publications p ON p.publication_id=g.publication_id
JOIN submissions s ON s.current_publication_id=p.publication_id
WHERE s.submission_id IN ({ids});
""")
    galleys = {}
    for line in out.splitlines():
        if not line:
            continue
        sid, label, path, mime = line.split('\t', 3)
        galleys.setdefault(int(sid), []).append(
            (label, f'/var/www/files/{path}', mime))
    return galleys


def replace_galley_files(target, copies):
    """copies: [(local_path, container_path, description)]."""
    for local, dest, desc in copies:
        if target == 'dev':
            subprocess.run(['docker', 'compose', 'cp', local, f'ojs:{dest}'],
                           check=True, capture_output=True, timeout=300)
            subprocess.run(['docker', 'compose', 'exec', '-T', 'ojs',
                            'chown', 'www-data:www-data', dest],
                           check=True, capture_output=True, timeout=60)
        else:
            tmp = '/tmp/_pipe13_galley'
            subprocess.run(['scp', '-q', local, f'sea-live:{tmp}'],
                           check=True, timeout=300)
            subprocess.run(
                ['ssh', 'sea-live',
                 f'cd /opt/pharkie-ojs-plugins && docker compose cp {tmp} '
                 f'ojs:{dest} && docker compose exec -T ojs chown '
                 f'www-data:www-data {dest} && rm -f {tmp}'],
                check=True, capture_output=True, timeout=300)
        print(f'  replaced {desc}')


def reindex(target, submission_ids):
    php_src = (SCRIPT_DIR / 'pipe13_reindex.php').read_text()
    cmd = ojs_php_cmd(target, submission_ids)
    proc = subprocess.run(cmd, input=php_src, capture_output=True, text=True,
                          timeout=300)
    print(proc.stdout.strip() or proc.stderr.strip())
    if proc.returncode != 0:
        print('WARNING: reindex dispatch failed — run it manually or accept '
              'a stale search entry for these articles.', file=sys.stderr)
        return
    # Drain just these jobs: one `run --once` per job, plus a couple spare.
    drain = 'cd /var/www/html && php lib/pkp/tools/jobs.php run --once'
    for _ in range(len(submission_ids) + 2):
        if target == 'dev':
            p = subprocess.run(['docker', 'compose', 'exec', '-T', 'ojs',
                                'bash', '-c', drain],
                               capture_output=True, text=True, timeout=120)
        else:
            p = subprocess.run(['ssh', 'sea-live',
                                'cd /opt/pharkie-ojs-plugins && docker compose '
                                f'exec -T ojs bash -c "{drain}"'],
                               capture_output=True, text=True, timeout=120)
        if 'No jobs' in (p.stdout + p.stderr):
            break
        time.sleep(1)
    print(f'Reindexed {len(submission_ids)} submission(s).')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('toc_json')
    ap.add_argument('--target', required=True, choices=['dev', 'live'])
    ap.add_argument('--article', type=int, default=None,
                    help='1-indexed article in toc.json')
    ap.add_argument('--confirm', action='store_true',
                    help='apply changes (default: dry run)')
    ap.add_argument('--galleys', action='store_true',
                    help='also replace galley files (PDF/HTML/JATS) for every '
                         'article in scope from the regenerated local outputs')
    ap.add_argument('--no-reindex', action='store_true',
                    help='skip the scoped search reindex after patching')
    args = ap.parse_args()

    if args.galleys and args.article is None:
        print('--galleys replaces files for EVERY article in scope; '
              'pass --article N to scope it, or accept the whole issue.',
              file=sys.stderr)

    articles = load_jats(args.toc_json, args.article)
    state = fetch_db_state(args.target, [a['submission_id'] for a in articles])

    updates = []          # (sql, human description)
    blocked = []          # structural differences needing a reimport
    changed_submissions = []

    for art in articles:
        db = state.get(art['submission_id'])
        tag = f"[{art['n']:02d}] {art['label']}"
        if db is None:
            blocked.append(f'{tag}: submission {art["submission_id"]} not in '
                           f'{args.target} DB — needs import, not patch')
            continue
        changed_here = False
        if art['title'] != db['title']:
            updates.append((
                f"UPDATE publication_settings SET setting_value='{esc(art['title'])}' "
                f"WHERE publication_id={db['publication_id']} "
                f"AND setting_name='title' AND locale='en';",
                f"{tag}\n    title: '{db['title'][:60]}' -> '{art['title'][:60]}'"))
            changed_here = True
        if len(art['contribs']) != len(db['authors']):
            blocked.append(f'{tag}: author count differs (JATS '
                           f'{len(art["contribs"])}, DB {len(db["authors"])}) — '
                           'structural, use the reimport path')
            continue
        for (given, family), row in zip(art['contribs'], db['authors']):
            for field, want, have in (('givenName', given, row['given']),
                                      ('familyName', family, row['family'])):
                if want != have:
                    updates.append((
                        f"UPDATE author_settings SET setting_value='{esc(want)}' "
                        f"WHERE author_id={row['author_id']} "
                        f"AND setting_name='{field}';",
                        f"{tag}\n    {field}: '{have}' -> '{want}'"))
                    changed_here = True
        # ORCIDs flow one way, JATS -> DB, and only when the JATS carries one:
        # an orcid row usually doesn't exist yet (upsert, not UPDATE), and a
        # JATS with no contrib-id must not strip an iD already in OJS.
        for want_orcid, row in zip(art.get('contrib_orcids', []), db['authors']):
            if want_orcid and want_orcid != row['orcid']:
                updates.append((
                    f"INSERT INTO author_settings (author_id, locale, setting_name, setting_value) "
                    f"VALUES ({row['author_id']}, '', 'orcid', '{esc(want_orcid)}') "
                    f"ON DUPLICATE KEY UPDATE setting_value='{esc(want_orcid)}';",
                    f"{tag}\n    orcid: '{row['orcid']}' -> '{want_orcid}'"))
                changed_here = True
        # copyrightHolder is stamped at publish time as
        # "<first author> (Author)" — every one of the archive's 1,422 rows
        # follows that form — and nothing recomputes it after an author
        # rename (found 2026-08-06: the 33.1 name change left the old name
        # in DC.Rights). Compare against the expected form so drift heals
        # even when the author rows were already patched in an earlier run.
        holder = db.get('copyright_holder', '')
        if art['contribs'] and holder:
            expected = f"{art['contribs'][0][0]} {art['contribs'][0][1]}".strip()
            expected = f'{expected} (Author)'
            if holder != expected:
                updates.append((
                    f"UPDATE publication_settings SET setting_value='{esc(expected)}' "
                    f"WHERE publication_id={db['publication_id']} "
                    f"AND setting_name='copyrightHolder' AND locale='en';",
                    f"{tag}\n    copyrightHolder: '{holder}' -> '{expected}'"))
                changed_here = True
        if art['abstract'] and db['abstract'] and art['abstract'] != db['abstract']:
            blocked.append(f'{tag}: abstract differs — not patched by this '
                           'tool, use the reimport path if intended')
        if changed_here:
            changed_submissions.append(art['submission_id'])

    # Galley file replacement: applies to every article in scope (the
    # correction usually lives in the text as well as the metadata).
    copies = []
    if args.galleys:
        galleys = fetch_galleys(args.target,
                                [a['submission_id'] for a in articles
                                 if a['submission_id'] in state])
        for art in articles:
            for label, dest, mime in galleys.get(art['submission_id'], []):
                key = 'xml' if 'xml' in mime else mime
                local = art['galley_sources'].get(key)
                if not local or not os.path.exists(local):
                    blocked.append(f"[{art['n']:02d}] no local file for "
                                   f"'{label}' galley ({mime}) — expected "
                                   f'{local}')
                    continue
                copies.append((local, dest,
                               f"[{art['n']:02d}] {label} galley "
                               f'({os.path.basename(local)} -> {dest})'))
            changed_submissions.append(art['submission_id'])

    print(f'Compared {len(articles)} article(s) against {args.target}.\n')
    if blocked:
        print('NOT patchable here:')
        for b in blocked:
            print(f'  {b}')
        print()
    if not updates and not copies:
        print('No metadata differences to patch.')
        return
    if updates:
        print(f'{len(updates)} metadata change(s):')
        for _, desc in updates:
            print(f'  {desc}')
    if copies:
        print(f'{len(copies)} galley file replacement(s):')
        for _, _, desc in copies:
            print(f'  {desc}')

    if not args.confirm:
        print('\nDRY RUN — nothing written. Re-run with --confirm.')
        return

    if updates:
        run_sql(args.target, '\n'.join(sql for sql, _ in updates))
        print(f'\nApplied {len(updates)} update(s) on {args.target}.')
    if copies:
        replace_galley_files(args.target, copies)
    if args.no_reindex:
        print('Reindex skipped (--no-reindex): search still holds the old '
              'metadata for these articles.')
    else:
        reindex(args.target, sorted(set(changed_submissions)))
    print('Remember: OJS is patched, but Crossref metadata is not — if an '
          'author or title changed on an article with a registered DOI, '
          'redeposit it: pipe12_deposit_dois.sh <vol.iss> --redeposit <doi>.')


if __name__ == '__main__':
    main()
