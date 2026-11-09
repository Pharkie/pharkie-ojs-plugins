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

Spec mode (--spec <file.json>, no toc.json): corrections made in Harbour,
the content's successor, carried back to OJS without regenerating JATS. The
spec names each article by DOI and OJS submission id and gives the state
wanted (backfill/tests/test_pipe13_spec.py shows the shape):

    {"issues":   [{"volume": 8, "number": 1, "date_published": "1997-01-01"}],
     "articles": [{"doi": "10.65828/bdgrc507", "submission_id": 8599,
                   "label": "7.2 Some Idle and Random Thoughts",
                   "authors": [["F. A.", "Jenner"], ["Man Cheung", "Chung"]],
                   "title": "...", "date_published": "1997-01-01",
                   "unpublish": true}]}

Every key but doi and submission_id is optional. Authors compare by full
name, so OJS's own given/family split is kept for a name that is unchanged.
A spec with more authors than OJS appends the extra ones (fewer is refused).
"date_published" also sets copyrightYear; an issue's also sets its year.
"unpublish" takes the article off the site: status queued, out of search.
After --confirm it prints the pipe12 --redeposit command for each issue.
Galleys are never touched in spec mode.

    python3 backfill/html_pipeline/pipe13_patch_metadata.py \
        --spec corrections.json --target live            # dry run
    ... --spec corrections.json --target live --confirm
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.paths import load_toc  # noqa: E402

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
    toc = load_toc(toc_path)
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
            pub_id, author_id, seq, given, family, orcid = line.split('\t', 5)
            by_pub[int(pub_id)]['authors'].append(
                {'author_id': int(author_id), 'seq': int(seq),
                 'given': given.strip(),
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


def full_name(given, family):
    return ' '.join(f'{given} {family}'.split())


def load_spec(spec_path):
    with open(spec_path, encoding='utf-8') as fh:
        spec = json.load(fh)
    articles = []
    for i, art in enumerate(spec.get('articles', []), 1):
        articles.append({
            'n': i,
            'label': art.get('label') or art['doi'],
            'doi': art['doi'].strip().lower(),
            'submission_id': int(art['submission_id']),
            'title': art.get('title'),
            'contribs': ([tuple(a) for a in art['authors']]
                         if 'authors' in art else None),
            'date_published': art.get('date_published'),
            'unpublish': bool(art.get('unpublish')),
        })
    return spec.get('issues', []), articles


def fetch_spec_extras(target, publication_ids):
    """publication_id -> {doi, date_published, copyright_year, vol_iss, status}."""
    ids = ','.join(str(i) for i in publication_ids)
    out = run_sql(target, f"""
SELECT p.publication_id, COALESCE(d.doi,''), COALESCE(DATE(p.date_published),''),
       COALESCE(cy.setting_value,''), CONCAT(i.volume,'.',i.number), p.status
FROM publications p
LEFT JOIN dois d ON d.doi_id = p.doi_id
LEFT JOIN issues i ON i.issue_id = p.issue_id
LEFT JOIN publication_settings cy ON cy.publication_id = p.publication_id
     AND cy.setting_name='copyrightYear'
WHERE p.publication_id IN ({ids});
""")
    extras = {}
    for line in out.splitlines():
        if not line:
            continue
        pub_id, doi, date, year, vol_iss, status = line.split('\t', 5)
        extras[int(pub_id)] = {'doi': doi.strip().lower(), 'date_published': date,
                               'copyright_year': year.strip(),
                               'vol_iss': vol_iss, 'status': int(status)}
    return extras


def fetch_issue_state(target, issues):
    """(volume, number) -> {issue_id, date_published, year, doi}."""
    if not issues:
        return {}
    where = ' OR '.join(f"(i.volume={int(s['volume'])} AND i.number='{int(s['number'])}')"
                        for s in issues)
    out = run_sql(target, f"""
SELECT i.issue_id, i.volume, i.number, COALESCE(DATE(i.date_published),''),
       COALESCE(i.year,''), COALESCE(d.doi,'')
FROM issues i LEFT JOIN dois d ON d.doi_id = i.doi_id
WHERE {where};
""")
    state = {}
    for line in out.splitlines():
        if not line:
            continue
        issue_id, vol, num, date, year, doi = line.split('\t', 5)
        state[(int(vol), int(num))] = {'issue_id': int(issue_id),
                                       'date_published': date, 'year': year,
                                       'doi': doi.strip().lower()}
    return state


def author_columns(target):
    out = run_sql(target, 'SHOW COLUMNS FROM authors;')
    return [line.split('\t', 1)[0] for line in out.splitlines() if line]


def plan_spec(issues, articles, state, extras, issue_state, columns):
    """Compare a spec with OJS. Returns (updates, blocked, changed, redeposit).

    updates: [(sql, description)]; changed: submission ids whose search entry
    needs rebuilding; redeposit: vol.iss -> [DOI] for Crossref.
    """
    updates, blocked, changed = [], [], []
    redeposit = {}

    for spec_issue in issues:
        key = (int(spec_issue['volume']), int(spec_issue['number']))
        tag = f'[issue {key[0]}.{key[1]}]'
        db = issue_state.get(key)
        if db is None:
            blocked.append(f'{tag}: not in the DB')
            continue
        want = spec_issue.get('date_published')
        if want and (db['date_published'] != want or db['year'] != want[:4]):
            updates.append((
                f"UPDATE issues SET date_published='{esc(want)} 00:00:00', "
                f"year={int(want[:4])} WHERE issue_id={db['issue_id']};",
                f"{tag}\n    date: '{db['date_published']}' ({db['year']}) "
                f"-> '{want}' ({want[:4]})"))
            if db['doi']:
                redeposit.setdefault(f'{key[0]}.{key[1]}', []).append(db['doi'])

    for art in articles:
        tag = f"[{art['n']:02d}] {art['label']}"
        db = state.get(art['submission_id'])
        if db is None:
            blocked.append(f"{tag}: submission {art['submission_id']} not in the DB")
            continue
        pub_id = db['publication_id']
        ex = extras.get(pub_id, {})
        if ex.get('doi') != art['doi']:
            blocked.append(f"{tag}: submission {art['submission_id']} carries DOI "
                           f"'{ex.get('doi', '')}', the spec says '{art['doi']}'"
                           ' — nothing patched for it')
            continue
        changed_here = False
        crossref_here = False

        if art['unpublish']:
            if ex.get('status') == 3:
                sid = art['submission_id']
                updates.append((
                    f'UPDATE publications SET status=1 WHERE publication_id={pub_id};\n'
                    f'UPDATE submissions SET status=1 WHERE submission_id={sid};\n'
                    'DELETE k FROM submission_search_object_keywords k JOIN '
                    'submission_search_objects o ON o.object_id=k.object_id '
                    f'WHERE o.submission_id={sid};\n'
                    f'DELETE FROM submission_search_objects WHERE submission_id={sid};',
                    f'{tag}\n    unpublish: published (3) -> queued (1), out of search'))
            continue

        if art['title'] is not None and norm(art['title']) != db['title']:
            updates.append((
                f"UPDATE publication_settings SET setting_value='{esc(art['title'])}' "
                f"WHERE publication_id={pub_id} AND setting_name='title' AND locale='en';",
                f"{tag}\n    title: '{db['title'][:60]}' -> '{art['title'][:60]}'"))
            changed_here = crossref_here = True

        contribs = art['contribs']
        if contribs is not None:
            have = db['authors']
            if len(contribs) < len(have):
                blocked.append(f'{tag}: spec has {len(contribs)} author(s), OJS '
                               f'{len(have)} — removing an author is not patched')
                continue
            for (given, family), row in zip(contribs, have):
                if full_name(given, family) == full_name(row['given'], row['family']):
                    continue
                for field, want, old in (('givenName', given, row['given']),
                                         ('familyName', family, row['family'])):
                    if want != old:
                        updates.append((
                            f"UPDATE author_settings SET setting_value='{esc(want)}' "
                            f"WHERE author_id={row['author_id']} "
                            f"AND setting_name='{field}';",
                            f"{tag}\n    {field}: '{old}' -> '{want}'"))
                changed_here = crossref_here = True
            if len(contribs) > len(have):
                last = have[-1]
                cols = [c for c in columns if c != 'author_id']
                for k, (given, family) in enumerate(contribs[len(have):], 1):
                    picks = ', '.join(
                        f"{last['seq'] + k}" if c == 'seq'
                        else "''" if c == 'email' else c for c in cols)
                    updates.append((
                        f"INSERT INTO authors ({', '.join(cols)}) SELECT {picks} "
                        f"FROM authors WHERE author_id={last['author_id']};\n"
                        'SET @new_author = LAST_INSERT_ID();\n'
                        'INSERT INTO author_settings (author_id, locale, setting_name, '
                        f"setting_value) VALUES (@new_author, 'en', 'givenName', "
                        f"'{esc(given)}'), (@new_author, 'en', 'familyName', "
                        f"'{esc(family)}');",
                        f"{tag}\n    add author {len(have) + k}: '{full_name(given, family)}'"))
                changed_here = crossref_here = True
            holder = db.get('copyright_holder', '')
            expected = f'{full_name(*contribs[0])} (Author)'
            if holder and holder != expected:
                updates.append((
                    f"UPDATE publication_settings SET setting_value='{esc(expected)}' "
                    f"WHERE publication_id={pub_id} "
                    "AND setting_name='copyrightHolder' AND locale='en';",
                    f"{tag}\n    copyrightHolder: '{holder}' -> '{expected}'"))
                changed_here = True

        want_date = art['date_published']
        if want_date and ex.get('date_published') != want_date:
            updates.append((
                f"UPDATE publications SET date_published='{esc(want_date)}' "
                f'WHERE publication_id={pub_id};',
                f"{tag}\n    date: '{ex.get('date_published')}' -> '{want_date}'"))
            crossref_here = True
        if want_date and ex.get('copyright_year') not in ('', want_date[:4]):
            updates.append((
                f"UPDATE publication_settings SET setting_value='{want_date[:4]}' "
                f"WHERE publication_id={pub_id} AND setting_name='copyrightYear';",
                f"{tag}\n    copyrightYear: '{ex['copyright_year']}' -> '{want_date[:4]}'"))

        if changed_here:
            changed.append(art['submission_id'])
        if crossref_here:
            redeposit.setdefault(ex['vol_iss'], []).append(art['doi'])
    return updates, blocked, changed, redeposit


def run_spec(args):
    issues, articles = load_spec(args.spec)
    state = fetch_db_state(args.target, [a['submission_id'] for a in articles])
    extras = fetch_spec_extras(args.target,
                               [v['publication_id'] for v in state.values()])
    issue_state = fetch_issue_state(args.target, issues)
    needs_columns = any(
        a['contribs'] and a['submission_id'] in state
        and len(a['contribs']) > len(state[a['submission_id']]['authors'])
        for a in articles)
    columns = author_columns(args.target) if needs_columns else []
    updates, blocked, changed, redeposit = plan_spec(
        issues, articles, state, extras, issue_state, columns)

    print(f'Compared {len(articles)} article(s) and {len(issues)} issue(s) '
          f'against {args.target}.\n')
    if blocked:
        print('NOT patched:')
        for b in blocked:
            print(f'  {b}')
        print()
    if not updates:
        print('No differences to patch.')
        return
    print(f'{len(updates)} change(s):')
    for _, desc in updates:
        print(f'  {desc}')
    n_dois = sum(len(v) for v in redeposit.values())
    print(f'\n{len(set(changed))} submission(s) to reindex; {n_dois} DOI(s) '
          f'to redeposit across {len(redeposit)} issue(s).')
    if not args.confirm:
        print('\nDRY RUN — nothing written. Re-run with --confirm.')
        return
    run_sql(args.target, '\n'.join(sql for sql, _ in updates))
    print(f'\nApplied {len(updates)} change(s) on {args.target}.')
    if changed and not args.no_reindex:
        reindex(args.target, sorted(set(changed)))
    host = ' --host=sea-live' if args.target == 'live' else ''
    print('\nCrossref still holds the old metadata. Redeposit (dry run, then '
          'add --confirm):')
    for vol_iss in sorted(redeposit, key=lambda v: [int(x) for x in v.split('.')]):
        dois = ','.join(sorted(set(redeposit[vol_iss])))
        print(f'  backfill/html_pipeline/pipe12_deposit_dois.sh {vol_iss}{host} '
              f'--redeposit={dois}')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('toc_json', nargs='?')
    ap.add_argument('--spec', help='a JSON spec of corrections made in Harbour '
                                   '(see "Spec mode" above), in place of toc_json')
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
    if bool(args.spec) == bool(args.toc_json):
        ap.error('give a toc.json or --spec, not both')
    if args.spec:
        if args.galleys or args.article is not None:
            ap.error('--galleys and --article apply to toc.json mode only')
        run_spec(args)
        return

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
