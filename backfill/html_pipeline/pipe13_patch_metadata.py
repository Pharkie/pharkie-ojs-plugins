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
name, so OJS's own given/family split is kept for a name that is unchanged,
unless the article sets "exact_split": true (the split itself is the
correction, e.g. given "Yaqui Andrés", family "Martínez Robles").
A spec with more authors than OJS appends the extra ones (fewer is refused).
"date_published" also sets copyrightYear; an issue's also sets its year.
"unpublish" takes the article off the site through OJS's own unpublish
(pipe13_unpublish.php). --confirm refuses unless the job queue and
failed_jobs are empty; it writes a rollback SQL file (every old value) and the
pipe12 --redeposit line per issue to --out-dir, applies all SQL in one
transaction, then reindexes and redispatches exactly its own failed search
jobs. --target rehearse runs the SQL path against a throwaway MariaDB on
sea-live (container ojs-rehearse-db) holding a restored dump. Galleys are
never touched in spec mode, and JATS / toc.json /
authors.json keep the old values: a later reimport reverts the correction
unless those are flipped too.

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
        'mysql --default-character-set=utf8mb4 -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N',
    ],
    'live': [
        'ssh', 'sea-live',
        'cd /opt/pharkie-ojs-plugins && docker compose exec -T ojs-db '
        'bash -c \'mysql --default-character-set=utf8mb4 -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -N\'',
    ],
}

# --target rehearse: a throwaway MariaDB on sea-live holding a restored dump
# (no OJS app beside it, no network, empty root password). Only the SQL path
# runs there; the OJS PHP steps are skipped. See the docstring.
REHEARSE_CONTAINER = 'ojs-rehearse-db'
DB_CMD['rehearse'] = [
    'ssh', 'sea-live',
    f'docker exec -i {REHEARSE_CONTAINER} mariadb --default-character-set=utf8mb4 '
    '-u root ojs -N',
]

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
     AND g.setting_name='givenName' AND g.locale='en'
LEFT JOIN author_settings f ON f.author_id=a.author_id
     AND f.setting_name='familyName' AND f.locale='en'
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
                {'author_id': int(author_id), 'seq': int(float(seq)),
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


def unhex(h):
    return bytes.fromhex(h).decode('utf-8') if h and h != 'NULL' else ''


def sql_str(value):
    """A SQL literal for a value read back from the DB (None -> NULL)."""
    return 'NULL' if value is None else f"'{esc(value)}'"


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
            'exact_split': bool(art.get('exact_split')),
        })
    return spec.get('issues', []), articles


def fetch_spec_state(target, submission_ids):
    """submission_id -> the OJS state spec mode compares and can undo.

    Text comes back as HEX so a tab, newline or non-ASCII byte in a title or
    name survives the trip intact (the rollback file writes it back verbatim).
    """
    ids = ','.join(str(i) for i in submission_ids)
    out = run_sql(target, f"""
SELECT s.submission_id, p.publication_id, COALESCE(d.doi,''),
       COALESCE(p.date_published,'NULL'), COALESCE(HEX(cy.setting_value),'NULL'),
       COALESCE(HEX(t.setting_value),'NULL'), COALESCE(HEX(ch.setting_value),'NULL'),
       CONCAT(i.volume,'.',i.number), p.status, s.status,
       COALESCE(p.last_modified,'NULL'), COALESCE(s.last_modified,'NULL')
FROM submissions s
JOIN publications p ON p.publication_id = s.current_publication_id
LEFT JOIN dois d ON d.doi_id = p.doi_id
LEFT JOIN issues i ON i.issue_id = p.issue_id
LEFT JOIN publication_settings t ON t.publication_id = p.publication_id
     AND t.setting_name='title' AND t.locale='en'
LEFT JOIN publication_settings ch ON ch.publication_id = p.publication_id
     AND ch.setting_name='copyrightHolder' AND ch.locale='en'
LEFT JOIN publication_settings cy ON cy.publication_id = p.publication_id
     AND cy.setting_name='copyrightYear'
WHERE s.submission_id IN ({ids});
""")
    state = {}
    for line in out.splitlines():
        if not line:
            continue
        (sid, pub_id, doi, date, year, title, holder, vol_iss, pstatus, sstatus,
         plm, slm) = line.split('\t')
        state[int(sid)] = {
            'publication_id': int(pub_id), 'doi': doi.strip().lower(),
            'date_published': None if date == 'NULL' else date,
            'copyright_year': None if year == 'NULL' else unhex(year),
            'title_raw': None if title == 'NULL' else unhex(title),
            'holder_raw': None if holder == 'NULL' else unhex(holder),
            'vol_iss': vol_iss, 'status': int(pstatus),
            'submission_status': int(sstatus),
            'pub_last_modified': None if plm == 'NULL' else plm,
            'sub_last_modified': None if slm == 'NULL' else slm,
            'authors': []}
    if state:
        pub_ids = ','.join(str(v['publication_id']) for v in state.values())
        out = run_sql(target, f"""
SELECT a.publication_id, a.author_id, a.seq,
       COALESCE(HEX(g.setting_value),'NULL'), COALESCE(HEX(f.setting_value),'NULL')
FROM authors a
LEFT JOIN author_settings g ON g.author_id=a.author_id
     AND g.setting_name='givenName' AND g.locale='en'
LEFT JOIN author_settings f ON f.author_id=a.author_id
     AND f.setting_name='familyName' AND f.locale='en'
WHERE a.publication_id IN ({pub_ids})
ORDER BY a.publication_id, a.seq;
""")
        by_pub = {v['publication_id']: v for v in state.values()}
        for line in out.splitlines():
            if not line:
                continue
            pub_id, author_id, seq, given, family = line.split('\t')
            by_pub[int(pub_id)]['authors'].append({
                'author_id': int(author_id), 'seq': int(float(seq)),
                'given': None if given == 'NULL' else unhex(given),
                'family': None if family == 'NULL' else unhex(family)})
    return state


def fetch_issue_state(target, issues):
    """(volume, number) -> {issue_id, date_published, year, doi, last_modified}."""
    if not issues:
        return {}
    where = ' OR '.join(f"(i.volume={int(s['volume'])} AND i.number='{int(s['number'])}')"
                        for s in issues)
    out = run_sql(target, f"""
SELECT i.issue_id, i.volume, i.number, COALESCE(i.date_published,'NULL'),
       COALESCE(i.year,'NULL'), COALESCE(d.doi,''), COALESCE(i.last_modified,'NULL')
FROM issues i LEFT JOIN dois d ON d.doi_id = i.doi_id
WHERE {where};
""")
    state = {}
    for line in out.splitlines():
        if not line:
            continue
        issue_id, vol, num, date, year, doi, lm = line.split('\t')
        state[(int(vol), int(num))] = {
            'issue_id': int(issue_id),
            'date_published': None if date == 'NULL' else date,
            'year': None if year == 'NULL' else year,
            'doi': doi.strip().lower(),
            'last_modified': None if lm == 'NULL' else lm}
    return state


def author_columns(target):
    out = run_sql(target, 'SHOW COLUMNS FROM authors;')
    return [line.split('\t', 1)[0] for line in out.splitlines() if line]


def upsert_author_setting(author_id, field, value):
    return ('INSERT INTO author_settings (author_id, locale, setting_name, '
            f"setting_value) VALUES ({author_id}, 'en', '{field}', '{esc(value)}') "
            f"ON DUPLICATE KEY UPDATE setting_value='{esc(value)}';")


def restore_author_setting(author_id, field, old):
    if old is None:
        return (f'DELETE FROM author_settings WHERE author_id={author_id} '
                f"AND setting_name='{field}' AND locale='en';")
    return upsert_author_setting(author_id, field, old)


def plan_spec(issues, articles, state, issue_state, columns):
    """Compare a spec with OJS. Pure: no DB access.

    Returns a dict:
      changes   [{sql, undo, desc}] — SQL to apply in one transaction, and the
                statement that puts each back
      unpublish [(submission_id, label)] — taken off through OJS's own
                Repo::publication()->unpublish (pipe13_unpublish.php)
      blocked   [str]
      reindex   [submission_id] — search text changed
      redeposit {vol.iss: [doi]} — Crossref holds the old metadata
      touched   publications, submissions and issues whose last_modified the
                apply step stamps (and the rollback restores)
    """
    changes, blocked, reindex_ids, unpublish = [], [], [], []
    redeposit = {}
    touched = {'publications': {}, 'submissions': {}, 'issues': {}}

    def change(sql, undo, desc):
        changes.append({'sql': sql, 'undo': undo, 'desc': desc})

    for spec_issue in issues:
        key = (int(spec_issue['volume']), int(spec_issue['number']))
        tag = f'[issue {key[0]}.{key[1]}]'
        db = issue_state.get(key)
        if db is None:
            blocked.append(f'{tag}: not in the DB')
            continue
        want = spec_issue.get('date_published')
        have = (db['date_published'] or '')[:10]
        if want and (have != want or db['year'] != want[:4]):
            iid = db['issue_id']
            change(f"UPDATE issues SET date_published='{esc(want)} 00:00:00', "
                   f'year={int(want[:4])} WHERE issue_id={iid};',
                   f"UPDATE issues SET date_published={sql_str(db['date_published'])}, "
                   f"year={db['year'] or 'NULL'} WHERE issue_id={iid};",
                   f"{tag}\n    date: '{have}' ({db['year']}) -> '{want}' ({want[:4]})")
            touched['issues'][iid] = db['last_modified']
            if db['doi']:
                redeposit.setdefault(f'{key[0]}.{key[1]}', []).append(db['doi'])

    for art in articles:
        tag = f"[{art['n']:02d}] {art['label']}"
        sid = art['submission_id']
        db = state.get(sid)
        if db is None:
            blocked.append(f'{tag}: submission {sid} not in the DB')
            continue
        pub_id = db['publication_id']
        if db['doi'] != art['doi']:
            blocked.append(f"{tag}: submission {sid} carries DOI '{db['doi']}', "
                           f"the spec says '{art['doi']}' — nothing patched for it")
            continue

        if art['unpublish']:
            if db['status'] == 3:
                unpublish.append((sid, art['label']))
            continue

        text_changed = crossref = False
        n_before = len(changes)

        if art['title'] is not None and norm(art['title']) != norm(db['title_raw']):
            change(f"UPDATE publication_settings SET setting_value='{esc(art['title'])}' "
                   f"WHERE publication_id={pub_id} AND setting_name='title' AND locale='en';",
                   f"UPDATE publication_settings SET setting_value={sql_str(db['title_raw'])} "
                   f"WHERE publication_id={pub_id} AND setting_name='title' AND locale='en';",
                   f"{tag}\n    title: '{norm(db['title_raw'])[:60]}' -> '{art['title'][:60]}'")
            text_changed = crossref = True

        contribs = art['contribs']
        if contribs is not None:
            have = db['authors']
            if len(contribs) < len(have):
                blocked.append(f'{tag}: spec has {len(contribs)} author(s), OJS '
                               f'{len(have)} — removing an author is not patched')
                del changes[n_before:]
                continue
            for (given, family), row in zip(contribs, have):
                # The same full name split another way is not a change, unless
                # the spec says the split itself is the correction
                # ("exact_split": a family name of two words, say).
                if full_name(given, family) == full_name(row['given'] or '',
                                                         row['family'] or '') and (
                        not art.get('exact_split')
                        or (given, family) == (row['given'] or '', row['family'] or '')):
                    continue
                for field, want, old in (('givenName', given, row['given']),
                                         ('familyName', family, row['family'])):
                    if want != (old or ''):
                        change(upsert_author_setting(row['author_id'], field, want),
                               restore_author_setting(row['author_id'], field, old),
                               f"{tag}\n    {field}: '{old or ''}' -> '{want}'")
                text_changed = crossref = True
            if len(contribs) > len(have):
                last = have[-1]
                cols = [c for c in columns if c != 'author_id']
                for k, (given, family) in enumerate(contribs[len(have):], 1):
                    seq = last['seq'] + k
                    picks = ', '.join(str(seq) if c == 'seq'
                                      else "''" if c == 'email' else c for c in cols)
                    change(f"INSERT INTO authors ({', '.join(cols)}) SELECT {picks} "
                           f"FROM authors WHERE author_id={last['author_id']};\n"
                           'SET @new_author = LAST_INSERT_ID();\n'
                           'INSERT INTO author_settings (author_id, locale, setting_name, '
                           f"setting_value) VALUES (@new_author, 'en', 'givenName', "
                           f"'{esc(given)}'), (@new_author, 'en', 'familyName', "
                           f"'{esc(family)}');",
                           'DELETE s FROM author_settings s JOIN authors a ON '
                           f'a.author_id=s.author_id WHERE a.publication_id={pub_id} '
                           f'AND a.seq={seq};\n'
                           f'DELETE FROM authors WHERE publication_id={pub_id} AND seq={seq};',
                           f"{tag}\n    add author {len(have) + k}: '{full_name(given, family)}'")
                text_changed = crossref = True
            holder = db['holder_raw']
            expected = f'{full_name(*contribs[0])} (Author)'
            if holder and holder != expected:
                change(f"UPDATE publication_settings SET setting_value='{esc(expected)}' "
                       f"WHERE publication_id={pub_id} "
                       "AND setting_name='copyrightHolder' AND locale='en';",
                       f"UPDATE publication_settings SET setting_value={sql_str(holder)} "
                       f"WHERE publication_id={pub_id} "
                       "AND setting_name='copyrightHolder' AND locale='en';",
                       f"{tag}\n    copyrightHolder: '{holder}' -> '{expected}'")

        want_date = art['date_published']
        have_date = (db['date_published'] or '')[:10]
        if want_date and have_date != want_date:
            change(f"UPDATE publications SET date_published='{esc(want_date)}' "
                   f'WHERE publication_id={pub_id};',
                   f"UPDATE publications SET date_published={sql_str(db['date_published'])} "
                   f'WHERE publication_id={pub_id};',
                   f"{tag}\n    date: '{have_date}' -> '{want_date}'")
            crossref = True
        if want_date and db['copyright_year'] not in (None, want_date[:4]):
            change(f"UPDATE publication_settings SET setting_value='{want_date[:4]}' "
                   f"WHERE publication_id={pub_id} AND setting_name='copyrightYear';",
                   f"UPDATE publication_settings SET setting_value={sql_str(db['copyright_year'])} "
                   f"WHERE publication_id={pub_id} AND setting_name='copyrightYear';",
                   f"{tag}\n    copyrightYear: '{db['copyright_year']}' -> '{want_date[:4]}'")

        if len(changes) > n_before:
            touched['publications'][pub_id] = db['pub_last_modified']
            touched['submissions'][sid] = db['sub_last_modified']
        if text_changed:
            reindex_ids.append(sid)
        if crossref:
            redeposit.setdefault(db['vol_iss'], []).append(art['doi'])

    return {'changes': changes, 'unpublish': unpublish, 'blocked': blocked,
            'reindex': reindex_ids, 'redeposit': redeposit, 'touched': touched}


def stamp_sql(touched):
    """last_modified = now on everything changed, and its undo."""
    keys = {'publications': 'publication_id', 'submissions': 'submission_id',
            'issues': 'issue_id'}
    apply, undo = [], []
    for table, col in keys.items():
        rows = touched[table]
        if not rows:
            continue
        ids = ','.join(str(i) for i in sorted(rows))
        apply.append(f'UPDATE {table} SET last_modified=NOW() WHERE {col} IN ({ids});')
        for i, old in sorted(rows.items()):
            undo.append(f'UPDATE {table} SET last_modified={sql_str(old)} WHERE {col}={i};')
    return apply, undo


def transaction(statements):
    return '\n'.join(['SET NAMES utf8mb4;', 'START TRANSACTION;', *statements,
                      'COMMIT;'])


def queue_counts(target):
    out = run_sql(target, 'SELECT (SELECT COUNT(*) FROM jobs), '
                          '(SELECT COUNT(*) FROM failed_jobs);')
    jobs, failed = out.split()
    return int(jobs), int(failed)


def failed_search_jobs(target):
    out = run_sql(target, 'SELECT COUNT(*), SUM(payload LIKE '
                          "'%UpdateSubmissionSearchJob%') FROM failed_jobs;")
    total, search = out.split()
    return int(total), int(search if search != 'NULL' else 0)


def ojs_shell(target, command, timeout=300):
    if target == 'dev':
        cmd = ['docker', 'compose', 'exec', '-T', 'ojs', 'bash', '-c', command]
    else:
        cmd = ['ssh', 'sea-live', 'cd /opt/pharkie-ojs-plugins && docker compose '
               f'exec -T ojs bash -c "{command}"']
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def drain(target, n):
    for _ in range(n + 2):
        p = ojs_shell(target, 'cd /var/www/html && php lib/pkp/tools/jobs.php run --once',
                      timeout=120)
        if 'No jobs' in (p.stdout + p.stderr):
            return
        time.sleep(1)


def unpublish_via_ojs(target, ids):
    php_src = (SCRIPT_DIR / 'pipe13_unpublish.php').read_text()
    cmd = ojs_php_cmd(target, ['--confirm', *ids])
    proc = subprocess.run(cmd, input=php_src, capture_output=True, text=True,
                          timeout=300)
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        raise SystemExit('ERROR: unpublish failed — the SQL changes above are '
                         'committed; check the two articles by hand.')


def run_spec(args):
    issues, articles = load_spec(args.spec)
    state = fetch_spec_state(args.target, [a['submission_id'] for a in articles])
    issue_state = fetch_issue_state(args.target, issues)
    needs_columns = any(
        a['contribs'] and a['submission_id'] in state
        and len(a['contribs']) > len(state[a['submission_id']]['authors'])
        for a in articles)
    columns = author_columns(args.target) if needs_columns else []
    plan = plan_spec(issues, articles, state, issue_state, columns)
    changes, blocked = plan['changes'], plan['blocked']

    print(f'Compared {len(articles)} article(s) and {len(issues)} issue(s) '
          f'against {args.target}.\n')
    if blocked:
        print('NOT patched:')
        for b in blocked:
            print(f'  {b}')
        print()
    if not changes and not plan['unpublish']:
        print('No differences to patch.')
        return
    print(f'{len(changes)} change(s):')
    for c in changes:
        print(f"  {c['desc']}")
    for sid, label in plan['unpublish']:
        print(f'  [unpublish] {label}\n    submission {sid}: published -> queued, '
              "through OJS's own unpublish")
    redeposit = plan['redeposit']
    n_dois = sum(len(v) for v in redeposit.values())
    expect_failed = len(set(plan['reindex'])) + len(plan['unpublish'])
    print(f"\nSUMMARY {len(changes)} change(s), {len(plan['unpublish'])} unpublish, "
          f"{len(set(plan['reindex']))} reindex, {n_dois} DOI(s) to redeposit "
          f'across {len(redeposit)} issue(s), {len(blocked)} not patched')

    host = '' if args.target == 'dev' else ' --host=sea-live'
    pipe12_lines = [
        f'bash backfill/html_pipeline/pipe12_deposit_dois.sh {v}{host} '
        f"--redeposit={','.join(sorted(set(redeposit[v])))}"
        for v in sorted(redeposit, key=lambda v: [int(x) for x in v.split('.')])]

    if not args.confirm:
        print('\nDRY RUN — nothing written. Re-run with --confirm.')
        return
    if blocked:
        raise SystemExit('Refusing --confirm while anything is NOT patched.')
    jobs, failed = (0, 0) if args.target == 'rehearse' else queue_counts(args.target)
    if jobs or failed:
        raise SystemExit(f'Refusing --confirm: the job queue holds {jobs} job(s) '
                         f'and {failed} failed job(s). The reindex step redispatches '
                         'failures, so it must start from empty. Drain or look first.')

    stamp, unstamp = stamp_sql(plan['touched'])
    stamp_id = time.strftime('%Y%m%d-%H%M%S')
    out_dir = Path(args.out_dir or '.')
    rollback = out_dir / f'pipe13-rollback-{args.target}-{stamp_id}.sql'
    rollback.write_text(
        f'-- Undo for pipe13 --spec {args.spec} on {args.target}, {stamp_id}.\n'
        '-- Restores every old value it changed. Unpublished articles are put\n'
        '-- back by the status flips at the end; then run pipe13_reindex.php for\n'
        '-- them, drain, and redeposit the DOIs again.\n'
        + transaction([c['undo'] for c in reversed(changes)] + unstamp + [
            f'UPDATE publications p JOIN submissions s ON s.current_publication_id='
            f'p.publication_id SET p.status=3, s.status=3 WHERE s.submission_id={sid};'
            for sid, _ in plan['unpublish']]) + '\n', encoding='utf-8')
    deposit_file = out_dir / f'pipe13-redeposit-{args.target}-{stamp_id}.txt'
    deposit_file.write_text('\n'.join(pipe12_lines) + '\n', encoding='utf-8')
    print(f'\nRollback SQL: {rollback}\npipe12 lines: {deposit_file}')

    if changes:
        run_sql(args.target, transaction([c['sql'] for c in changes] + stamp))
        print(f'Applied {len(changes)} change(s) on {args.target} in one transaction.')
    if args.target == 'rehearse':
        # No OJS app here: stand in for its unpublish with the status flips
        # it makes, so the dry run after reads clean. Reindex, queue and the
        # OJS unpublish itself are not rehearsed.
        if plan['unpublish']:
            run_sql(args.target, transaction([
                'UPDATE publications p JOIN submissions s ON s.current_publication_id='
                f'p.publication_id SET p.status=1, s.status=1 WHERE s.submission_id={sid};'
                for sid, _ in plan['unpublish']]))
            print(f"Rehearsal: {len(plan['unpublish'])} unpublish(es) stood in for by "
                  'status flips (the OJS path is not rehearsed here).')
        print(f'Rehearsal done. pipe12 lines: {deposit_file}')
        return
    if plan['unpublish']:
        unpublish_via_ojs(args.target, [sid for sid, _ in plan['unpublish']])

    if plan['reindex'] and not args.no_reindex:
        reindex(args.target, sorted(set(plan['reindex'])))
    drain(args.target, expect_failed)
    total, search = failed_search_jobs(args.target)
    print(f'Failed jobs after the first pass: {total} ({search} search reindex); '
          f'at most {expect_failed}, all search reindex.')
    if total == search and total <= expect_failed:
        ojs_shell(args.target, 'cd /var/www/html && php lib/pkp/tools/jobs.php '
                               'failed --redispatch')
        drain(args.target, expect_failed)
        total, search = failed_search_jobs(args.target)
        jobs, _ = queue_counts(args.target)
        print(f'After redispatch: {total} failed, {jobs} queued (both should be 0).')
    else:
        print('NOT redispatching: the failures are not exactly this run\'s '
              'search jobs. Look at failed_jobs before anything else.')
    print('\nCrossref still holds the old metadata. Redeposit with the lines in '
          f'{deposit_file} (dry run, then add --confirm).')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('toc_json', nargs='?')
    ap.add_argument('--spec', help='a JSON spec of corrections made in Harbour '
                                   '(see "Spec mode" above), in place of toc_json')
    ap.add_argument('--target', required=True, choices=['dev', 'live', 'rehearse'])
    ap.add_argument('--out-dir', help='spec mode: where --confirm writes the '
                                      'rollback SQL and the pipe12 lines '
                                      '(default: the current directory)')
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
    if args.target == 'rehearse' and not args.spec:
        ap.error('--target rehearse is for --spec only')
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
