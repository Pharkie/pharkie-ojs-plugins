#!/usr/bin/env python3
"""
Put an issue's articles in OJS into toc.json's running order.

OJS orders an issue's table of contents by each current publication's `seq`
(within its section). pipe6 sets seq to the article's position in toc.json, so
an article added to a published issue (pipe6 --only, pipe7 --add-articles)
arrives with its printed position while the articles after it still carry the
positions they had before it existed: two articles share a seq and the order
between them is undefined. This sets every published article's seq to its
position in toc.json.

Articles are matched by the submission id in their JATS publisher-id, else by
exact title among the issue's published articles no publisher-id claimed. It
refuses --confirm when anything is ambiguous, when a toc.json article is not
in OJS, or when a published OJS article is not in toc.json (its seq would
collide).

    python3 backfill/html_pipeline/tools/resequence_issue.py \\
        backfill/private/output/23.2/toc.json --target live            # dry run
    ... --target live --confirm --out-dir <dir>

--confirm writes resequence-rollback-<vol.iss>-<target>-<ts>.sql (every old
seq) to --out-dir first, then applies the changes in one transaction. Only
publications.seq changes. A second run says "Order already matches".
"""

import argparse
import os
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))           # html_pipeline
sys.path.insert(0, str(HERE.parent.parent))    # backfill
from lib.paths import load_toc  # noqa: E402
from pipe13_patch_metadata import DB_CMD, run_sql, norm  # noqa: E402


def jats_publisher_id(split_pdf):
    """The OJS submission id snapshot_ids.py wrote into the JATS, or None."""
    if not split_pdf:
        return None
    path = os.path.splitext(split_pdf)[0] + '.jats.xml'
    if not os.path.exists(path):
        return None
    el = ET.parse(path).find('.//{*}article-meta/{*}article-id[@pub-id-type="publisher-id"]')
    text = (el.text or '').strip() if el is not None else ''
    return int(text) if text.isdigit() else None


def ojs_articles(target, vol, num):
    """Published articles of the issue: submission id, publication id, seq, title."""
    sql = f"""
        SELECT s.submission_id, p.publication_id, p.seq,
               IFNULL(HEX((SELECT ps.setting_value FROM publication_settings ps
                           WHERE ps.publication_id = p.publication_id
                             AND ps.setting_name = 'title' AND ps.locale = 'en')), '')
        FROM submissions s
        JOIN publications p ON p.publication_id = s.current_publication_id
        JOIN issues i ON i.issue_id = p.issue_id
        JOIN journals j ON j.journal_id = i.journal_id
        WHERE j.path = 'ea' AND i.volume = {int(vol)} AND i.number = '{int(num)}'
          AND p.status = 3
        ORDER BY p.seq, s.submission_id;
    """
    rows = []
    for line in run_sql(target, sql).splitlines():
        if not line.strip():
            continue
        sid, pid, seq, title_hex = line.split('\t')
        rows.append({'sid': int(sid), 'pid': int(pid), 'seq': float(seq),
                     'title': bytes.fromhex(title_hex).decode('utf-8')})
    return rows


def plan(toc, ojs):
    """Match toc.json articles to OJS publications. Returns (changes, problems)."""
    by_sid = {r['sid']: r for r in ojs}
    claimed = set()
    matched = []  # (toc index, toc title, ojs row or None)
    problems = []
    for idx, art in enumerate(toc['articles']):
        sid = jats_publisher_id(art.get('split_pdf'))
        row = by_sid.get(sid) if sid else None
        if sid and row is None:
            problems.append(f"[{idx}] {art['title'][:60]}: JATS says submission {sid}, "
                            f"not a published article of this issue")
        matched.append([idx, art['title'], row])
        if row:
            claimed.add(row['sid'])
    for m in matched:
        if m[2] is not None:
            continue
        hits = [r for r in ojs if r['sid'] not in claimed and norm(r['title']) == norm(m[1])]
        if len(hits) == 1:
            m[2] = hits[0]
            claimed.add(hits[0]['sid'])
        elif len(hits) > 1:
            problems.append(f"[{m[0]}] {m[1][:60]}: {len(hits)} OJS articles have this title")
        else:
            problems.append(f"[{m[0]}] {m[1][:60]}: not in OJS")
    for r in ojs:
        if r['sid'] not in claimed:
            problems.append(f"OJS submission {r['sid']} (seq {r['seq']:g}) {r['title'][:60]}: "
                            f"not in toc.json")
    changes = [(idx, title, row) for idx, title, row in matched
               if row is not None and row['seq'] != idx]
    return matched, changes, problems


def main():
    ap = argparse.ArgumentParser(description="Set OJS running order to toc.json's.")
    ap.add_argument('toc_json')
    ap.add_argument('--target', choices=sorted(DB_CMD), required=True)
    ap.add_argument('--confirm', action='store_true')
    ap.add_argument('--out-dir', default='.', help='Where --confirm writes the rollback SQL')
    args = ap.parse_args()

    toc = load_toc(args.toc_json)
    vol, num = toc['volume'], toc['issue']
    ojs = ojs_articles(args.target, vol, num)
    matched, changes, problems = plan(toc, ojs)

    print(f"Vol {vol} No {num} on {args.target}: {len(toc['articles'])} in toc.json, "
          f"{len(ojs)} published in OJS")
    for idx, title, row in matched:
        if row is None:
            continue
        mark = f"seq {row['seq']:g} -> {idx}" if row['seq'] != idx else f"seq {idx}"
        print(f"  {mark:<14} {row['sid']:>6}  {title[:70]}")
    for p in problems:
        print(f"  PROBLEM {p}")
    if not changes:
        print('Order already matches.' if not problems else 'Nothing to change.')
        sys.exit(1 if problems else 0)
    print(f"{len(changes)} seq change(s).")
    if not args.confirm:
        print('DRY RUN: nothing changed. Re-run with --confirm.')
        sys.exit(1 if problems else 0)
    if problems:
        sys.exit('Refusing --confirm: resolve the problems above first.')

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    rollback = Path(args.out_dir) / f'resequence-rollback-{vol}.{num}-{args.target}-{stamp}.sql'
    rollback.write_text(
        f'-- Rollback for resequence_issue.py, Vol {vol} No {num}, {args.target}, {stamp}\n'
        'START TRANSACTION;\n'
        + ''.join(f"UPDATE publications SET seq = {row['seq']:g} WHERE publication_id = {row['pid']};\n"
                  for _, _, row in changes)
        + 'COMMIT;\n')
    print(f'Rollback written: {rollback}')
    run_sql(args.target, 'START TRANSACTION;\n'
            + ''.join(f"UPDATE publications SET seq = {idx} WHERE publication_id = {row['pid']};\n"
                      for idx, _, row in changes)
            + 'COMMIT;\n')
    after = {r['pid']: r['seq'] for r in ojs_articles(args.target, vol, num)}
    wrong = [(idx, row) for idx, _, row in changes if after.get(row['pid']) != idx]
    if wrong:
        sys.exit(f'{len(wrong)} seq value(s) did not read back as written. Stop and look.')
    print(f'Applied {len(changes)} seq change(s) in one transaction.')


if __name__ == '__main__':
    main()
