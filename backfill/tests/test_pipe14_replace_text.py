"""pipe14: corrected text from the source repo onto OJS galley files.

The plan is pure (source + OJS state in, files to write out); these pin what
it may overwrite and that a written file is what pipe6 would have imported.
"""

import io
import os
import subprocess
import tarfile

from backfill.html_pipeline import pipe14_replace_text as p14

BASE = 'The patient could not foresee the end.'
HEAD = 'The patient could only foresee the end.'
HTML = '/var/www/files/journals/1/articles/9758/69becabfbad1a.html'
XML = '/var/www/files/journals/1/articles/9758/69becabfbad1b.xml'


def galley(s):
    return f'<section><p>{s}</p></section>\n'


def jats(s):
    return ('<article><front><article-meta>'
            '<article-id pub-id-type="publisher-id">9758</article-id>'
            f'</article-meta></front><body><p>{s}</p></body></article>')


def article(**kw):
    a = {'issue': '36.1', 'stem': '05-responsibility', 'submission_id': 9758,
         'base': {'galley': galley(BASE), 'jats': jats(BASE)},
         'head': {'galley': galley(HEAD), 'jats': jats(HEAD)}}
    a.update(kw)
    return a


GALLEYS = {9758: [('PDF', '/var/www/files/x.pdf', 'application/pdf'),
                  ('Full Text', HTML, 'text/html'),
                  ('JATS XML', XML, 'text/xml')]}


def test_wrap_is_what_pipe6_imports(tmp_path):
    from backfill.html_pipeline.pipe6_ojs_xml import load_html_galley
    (tmp_path / 'a.pdf').write_bytes(b'%PDF')
    (tmp_path / 'a.galley.html').write_text('  ' + galley(HEAD))
    assert p14.wrap('  ' + galley(HEAD)) == load_html_galley(str(tmp_path / 'a.pdf'))


def test_base_files_are_replaced_by_the_head_wrapped():
    writes, report = p14.plan([article()], GALLEYS,
                              {HTML: p14.wrap(galley(BASE)), XML: jats(BASE)})
    assert [(w[0], w[2]) for w in writes] == [(HTML, 9758), (XML, 9758)]
    assert writes[0][1] == p14.wrap(galley(HEAD))
    assert writes[1][1] == jats(HEAD)
    assert [r['state'] for r in report] == ['change', 'change']
    assert report[0]['note'].startswith('+1 -1 words')


def test_head_files_are_left_alone_so_a_rerun_writes_nothing():
    writes, report = p14.plan([article()], GALLEYS,
                              {HTML: p14.wrap(galley(HEAD)), XML: jats(HEAD)})
    assert writes == []
    assert [r['state'] for r in report] == ['unchanged', 'unchanged']


def test_an_unwrapped_copy_counts_by_its_body():
    # pipe13 --galleys copied .galley.html over the file without the wrapper.
    writes, report = p14.plan([article()], GALLEYS,
                              {HTML: galley(BASE), XML: jats(BASE)})
    assert report[0]['state'] == 'change'
    assert writes[0][1] == p14.wrap(galley(HEAD))


def test_other_text_is_reported_and_only_written_when_asked():
    current = {HTML: p14.wrap(galley('An earlier run said something else.')), XML: jats(BASE)}
    writes, report = p14.plan([article()], GALLEYS, current)
    assert report[0]['state'] == 'other'
    assert [w[0] for w in writes] == [XML]
    writes, report = p14.plan([article()], GALLEYS, current, include_other=True)
    assert [w[0] for w in writes] == [HTML, XML]
    assert 'include-other' in report[0]['note']


def test_missing_galleys_new_articles_and_no_id_are_never_written():
    writes, report = p14.plan([article()], {9758: []}, {})
    assert writes == [] and [r['state'] for r in report] == ['missing', 'missing']
    writes, report = p14.plan([article(base={'galley': None, 'jats': None})], GALLEYS, {})
    assert writes == [] and report[0]['state'] == 'skipped'
    writes, report = p14.plan([article(submission_id=None)], GALLEYS, {})
    assert writes == [] and report[0]['state'] == 'skipped'


def test_word_changes():
    assert p14.word_changes('<p>a b c d</p>', '<p>a x c d e</p>') == (2, 1)
    assert p14.word_changes('<p>same</p>', '<p>same</p>') == (0, 0)


def test_changed_articles_reads_both_commits(tmp_path, monkeypatch):
    # 🛑 Under the pre-commit hook git exports GIT_DIR and GIT_INDEX_FILE, and
    # they beat `git -C`: without this the throwaway repo's commits land on
    # the branch being committed (and `git init` marks the repo bare).
    for key in [k for k in os.environ if k.startswith('GIT_')]:
        monkeypatch.delenv(key)
    repo = str(tmp_path)

    def git(*a):
        subprocess.run(['git', '-C', repo, '-c', 'user.email=t@example.org',
                        '-c', 'user.name=T', *a], check=True, capture_output=True)

    git('init', '-q')
    d = tmp_path / 'backfill/output/36.1'
    d.mkdir(parents=True)

    def put(stem, s):
        (d / f'{stem}.galley.html').write_text(galley(s))
        (d / f'{stem}.jats.xml').write_text(jats(s))

    put('05-responsibility', BASE)
    put('06-untouched', 'Nothing changes.')
    git('add', '-A')
    git('commit', '-qm', 'base')
    put('05-responsibility', HEAD)
    git('commit', '-qam', 'head')
    assert p14.changed_articles(repo, 'HEAD~1', 'HEAD') == [article()]


def test_written_files_keep_the_old_owner_and_mode(monkeypatch):
    sent = {}

    def fake_run(cmd, input=None, **kw):
        sent['tar'] = input
        return subprocess.CompletedProcess(cmd, 0, b'', b'')

    monkeypatch.setattr(p14.subprocess, 'run', fake_run)
    old = tarfile.TarInfo('journals/1/articles/9758/69becabfbad1a.html')
    old.mode, old.uid, old.gid, old.uname, old.gname = 0o644, 33, 33, 'www-data', 'www-data'
    p14.write_files('dev', [(HTML, b'new', old)])
    with tarfile.open(fileobj=io.BytesIO(sent['tar'])) as tar:
        [m] = tar.getmembers()
        assert (m.name, m.mode, m.uid, m.uname) == (old.name, 0o644, 33, 'www-data')
        assert tar.extractfile(m).read() == b'new'


def test_restore_puts_the_backup_back(tmp_path, monkeypatch):
    old = tarfile.TarInfo('journals/1/articles/9758/69becabfbad1a.html')
    old.mode, old.uid, old.gid, old.uname, old.gname = 0o644, 33, 33, 'www-data', 'www-data'
    p14.save_backup(tmp_path, 'aaa', 'bbb', [(HTML, p14.wrap(galley(BASE)), old)])
    put = []
    monkeypatch.setattr(p14, 'write_files', lambda target, files: put.extend(files))
    monkeypatch.setattr(p14, 'reindex', lambda target, ids: put.append(('reindex', ids)) or True)

    class Args:
        restore, target, confirm, no_reindex = str(tmp_path), 'dev', True, False

    p14.restore(Args)
    path, data, info = put[0]
    assert path == HTML and data.decode() == p14.wrap(galley(BASE))
    assert (info.uid, info.mode) == (33, 0o644)
    assert put[1] == ('reindex', [9758])


# ── --exact-bytes: a change only in the JATS structure ──────────────────────

def jats_refs(doi):
    """Same <body> text whatever the DOI: only the <back> reference differs."""
    return ('<article><front><article-meta>'
            '<article-id pub-id-type="publisher-id">9758</article-id>'
            '</article-meta></front><body><p>Same text.</p></body><back><ref-list>'
            f'<ref><mixed-citation>A. Author.<pub-id pub-id-type="doi">{doi}</pub-id>'
            '</mixed-citation></ref></ref-list></back></article>')


WRONG, RIGHT = '10.1/wrong', '10.1/right'


def pubid_article():
    return article(base={'galley': galley('Same.'), 'jats': jats_refs(WRONG)},
                   head={'galley': galley('Same.'), 'jats': jats_refs(RIGHT)})


def pubid_current():
    return {HTML: p14.wrap(galley('Same.')), XML: jats_refs(WRONG)}


def test_a_pub_id_only_change_is_invisible_to_the_text_comparison():
    writes, report = p14.plan([pubid_article()], GALLEYS, pubid_current())
    assert writes == []
    assert [r['state'] for r in report] == ['unchanged', 'unchanged']


def test_exact_bytes_replaces_the_jats_and_says_why():
    writes, report = p14.plan([pubid_article()], GALLEYS, pubid_current(),
                              exact=('jats',))
    assert [(w[0], w[1]) for w in writes] == [(XML, jats_refs(RIGHT))]
    assert [r['state'] for r in report] == ['unchanged', 'bytes-only']
    assert 'bytes differ only' in report[1]['note']


def test_exact_bytes_html_is_a_separate_ask():
    cur = pubid_current()
    cur[HTML] = '<html><body>' + galley('Same.') + '</body></html>'  # unwrapped copy
    writes, _ = p14.plan([pubid_article()], GALLEYS, cur, exact=('jats',))
    assert [w[0] for w in writes] == [XML]
    writes, report = p14.plan([pubid_article()], GALLEYS, cur, exact=('jats', 'html'))
    assert [w[0] for w in writes] == [HTML, XML]
    assert report[0]['state'] == 'bytes-only'


def test_exact_bytes_leaves_a_file_already_at_the_head_alone():
    cur = pubid_current()
    cur[XML] = jats_refs(RIGHT)
    writes, report = p14.plan([pubid_article()], GALLEYS, cur, exact=('jats',))
    assert writes == [] and report[1]['state'] == 'unchanged'


def run_main(monkeypatch, tmp_path, argv, on_disk):
    """main() against a fake OJS: returns (stdout, files written)."""
    import sys
    wrote = []
    monkeypatch.setattr(p14, 'backups_dir', lambda d: tmp_path / 'bk')
    monkeypatch.setattr(p14, 'changed_articles', lambda *a, **k: [pubid_article()])
    monkeypatch.setattr(p14, 'fetch_galleys', lambda t, ids: GALLEYS)
    info = tarfile.TarInfo('x')
    info.mode, info.uid, info.gid, info.uname, info.gname = 0o644, 33, 33, 'www-data', 'www-data'
    monkeypatch.setattr(p14, 'read_files',
                        lambda t, paths: {p: (on_disk[p], info) for p in paths})
    monkeypatch.setattr(p14, 'queue_counts', lambda t: (0, 0))
    monkeypatch.setattr(p14, 'write_files', lambda t, files: wrote.extend(files))
    monkeypatch.setattr(p14, 'reindex', lambda t, ids: True)
    monkeypatch.setattr(sys, 'argv', ['pipe14', '--target', 'dev', '--repo', 'r',
                                      '--base', 'a', '--head', 'b'] + argv)
    return wrote


def test_dry_run_reports_why_and_writes_nothing(monkeypatch, tmp_path, capsys):
    wrote = run_main(monkeypatch, tmp_path, ['--exact-bytes'], pubid_current())
    p14.main()
    out = capsys.readouterr().out
    assert 'JATS XML  bytes-only  +0 -0 words  bytes differ only' in out
    assert 'DRY RUN' in out and wrote == []


def test_write_backs_up_and_restore_puts_it_back(monkeypatch, tmp_path, capsys):
    wrote = run_main(monkeypatch, tmp_path, ['--exact-bytes', '--confirm',
                                             '--out-dir', str(tmp_path / 'bk')],
                     pubid_current())
    p14.main()
    assert [(p, d) for p, d, _ in wrote] == [(XML, jats_refs(RIGHT).encode())]
    wrote.clear()
    assert (tmp_path / 'bk' / 'files' / p14.rel(XML)).read_text() == jats_refs(WRONG)

    class Args:
        restore, target, confirm, no_reindex = str(tmp_path / 'bk'), 'dev', True, True

    p14.restore(Args)
    assert [(p, d) for p, d, _ in wrote] == [(XML, jats_refs(WRONG).encode())]


def test_without_the_flag_the_same_run_writes_nothing(monkeypatch, tmp_path, capsys):
    wrote = run_main(monkeypatch, tmp_path, [], pubid_current())
    p14.main()
    assert wrote == [] and '0 file(s) to write' in capsys.readouterr().out


def test_exact_bytes_html_needs_exact_bytes(monkeypatch, tmp_path):
    import pytest
    run_main(monkeypatch, tmp_path, ['--exact-bytes-html'], pubid_current())
    with pytest.raises(SystemExit):
        p14.main()


def test_the_out_dir_guard_still_holds_with_exact_bytes(monkeypatch, tmp_path):
    import pytest
    import sys
    monkeypatch.setattr(sys, 'argv', [
        'pipe14', '--target', 'dev', '--repo', 'r', '--base', 'a', '--head', 'b',
        '--exact-bytes', '--confirm', '--out-dir', str(tmp_path / 'elsewhere')])
    with pytest.raises(SystemExit, match='--out-dir must be inside'):
        p14.main()
