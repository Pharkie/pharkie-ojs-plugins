"""The journal PDFs resolve through backfill/pdfs.sha256 and the store, or the tree.

The PDFs are leaving git. A checkout then carries ``backfill/pdfs.sha256``
(``<sha256>  output/37.2/01-editorial.pdf``) and the bytes are in
``$SEA_JOURNAL_PDFS/store/<sha256>.pdf``. Until the cut-over they are tracked
files. Every OJS stage that reads a PDF must work with both layouts, and a PDF
the corpus says exists but cannot be read must stop the run, naming the path
and hash. The old behaviour was a quiet skip: pipe6 built the article with no
galleys, pipe9 printed SKIP.

Fixtures are a few bytes in tmp_path; no real store is read.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.html_pipeline.pipe6_ojs_xml import find_issue_pdf, generate_xml  # noqa: E402
from backfill.lib.paths import MissingPdf, article_pdf, load_toc, resolve_pdf  # noqa: E402

PATHS_PY = os.path.join(os.path.dirname(__file__), '..', 'lib', 'paths.py')
PDF = b'%PDF-1.4\n% article bytes\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / 'journal-pdfs'
    (root / 'store').mkdir(parents=True)
    monkeypatch.setenv('SEA_JOURNAL_PDFS', str(root))
    return root / 'store'


def put(store, data):
    digest = sha(data)
    (store / f'{digest}.pdf').write_bytes(data)
    return digest


def corpus(tmp_path, manifest=None, tracked=False):
    """A backfill/ folder with one issue, 37.2, and one article."""
    backfill = tmp_path / 'checkout' / 'backfill'
    issue = backfill / 'output' / '37.2'
    issue.mkdir(parents=True)
    (backfill / 'input').mkdir()
    (issue / '01-editorial.jats.xml').write_text('<article/>')
    (issue / '01-editorial.galley.html').write_text('<p>Editorial</p>')
    if tracked:
        (issue / '01-editorial.pdf').write_bytes(PDF)
    if manifest is not None:
        (backfill / 'pdfs.sha256').write_text(
            ''.join(f'{h}  {p}\n' for p, h in sorted(manifest.items())))
    toc = issue / 'toc.json'
    toc.write_text(json.dumps({
        'volume': 37, 'issue': 2, 'date_published': '2026-07-01',
        'articles': [{'title': 'Editorial', 'authors': 'Jane Smith',
                      'section': 'Editorial', 'journal_page_start': 3,
                      'journal_page_end': 4,
                      'split_pdf': './scripts/pipeline/private/output/37.2/01-editorial.pdf'}],
    }))
    return backfill, issue, toc


def embedded_pdf(xml):
    """The base64 PDF pipe6 embedded for the article, decoded."""
    start = xml.index('extension="pdf">')
    start = xml.index('<embed encoding="base64">', start) + len('<embed encoding="base64">')
    return base64.b64decode(xml[start:xml.index('</embed>', start)])


# --- manifest + store ------------------------------------------------------

def test_a_listed_pdf_resolves_to_the_store(tmp_path, store):
    digest = put(store, PDF)
    _, issue, toc = corpus(tmp_path, {'output/37.2/01-editorial.pdf': digest})

    tree_path = load_toc(str(toc))['articles'][0]['split_pdf']

    # split_pdf stays the tree path, so the JATS beside it is still found...
    assert tree_path == str(issue / '01-editorial.pdf')
    assert not os.path.exists(tree_path)
    # ...and the bytes come from the store.
    assert resolve_pdf(tree_path) == str(store / f'{digest}.pdf')


def test_pipe6_embeds_the_store_copy(tmp_path, store):
    digest = put(store, PDF)
    _, _, toc = corpus(tmp_path, {'output/37.2/01-editorial.pdf': digest})

    xml = generate_xml(load_toc(str(toc)), toc_json_path=str(toc), skip_issue_galley=True)

    assert embedded_pdf(xml) == PDF
    assert '<name locale="en">01-editorial.pdf</name>' in xml


def test_the_manifest_wins_over_a_stale_tree_copy(tmp_path, store):
    """After `git rm --cached` the old file stays on disk; the manifest is read first."""
    new = b'%PDF-1.4\n% re-cut\n'
    digest = put(store, new)
    _, issue, _ = corpus(tmp_path, {'output/37.2/01-editorial.pdf': digest}, tracked=True)

    assert open(resolve_pdf(str(issue / '01-editorial.pdf')), 'rb').read() == new


def test_the_issue_pdf_resolves_through_the_manifest(tmp_path, store):
    digest = put(store, b'%PDF-1.4\n% print\n')
    _, _, toc = corpus(tmp_path, {'input/37.2.pdf': digest})

    assert find_issue_pdf(str(toc), 37, 2) == str(store / f'{digest}.pdf')


# --- tracked-file fallback -------------------------------------------------

def test_without_a_manifest_the_tracked_file_is_used(tmp_path, store):
    _, issue, toc = corpus(tmp_path, tracked=True)

    tree_path = article_pdf(str(toc), {'split_pdf': './backfill/private/output/37.2/01-editorial.pdf'})

    assert resolve_pdf(tree_path) == str(issue / '01-editorial.pdf')
    xml = generate_xml(load_toc(str(toc)), toc_json_path=str(toc), skip_issue_galley=True)
    assert embedded_pdf(xml) == PDF


def test_a_path_the_manifest_does_not_list_falls_back_to_the_tree(tmp_path, store):
    _, issue, _ = corpus(tmp_path, {'input/37.2.pdf': put(store, b'x')}, tracked=True)

    assert resolve_pdf(str(issue / '01-editorial.pdf')) == str(issue / '01-editorial.pdf')


def test_an_unsmudged_lfs_pointer_reads_through_the_store(tmp_path, store):
    digest = put(store, PDF)
    _, issue, _ = corpus(tmp_path)
    (issue / '01-editorial.pdf').write_text(
        f'version https://git-lfs.github.com/spec/v1\noid sha256:{digest}\nsize {len(PDF)}\n')

    assert resolve_pdf(str(issue / '01-editorial.pdf')) == str(store / f'{digest}.pdf')


# --- missing: fail loudly with the path and hash ---------------------------

def test_a_listed_pdf_missing_from_the_store_fails_loudly(tmp_path, store):
    digest = sha(PDF)  # listed, never put in the store
    _, issue, toc = corpus(tmp_path, {'output/37.2/01-editorial.pdf': digest})

    with pytest.raises(MissingPdf) as e:
        resolve_pdf(str(issue / '01-editorial.pdf'))
    assert digest in str(e.value)
    assert '01-editorial.pdf' in str(e.value)

    # pipe6 stops rather than building the article without its galleys.
    with pytest.raises(MissingPdf):
        generate_xml(load_toc(str(toc)), toc_json_path=str(toc), skip_issue_galley=True)


def test_pipe6_cli_exits_non_zero_and_writes_no_xml(tmp_path, store):
    digest = sha(PDF)
    _, issue, toc = corpus(tmp_path, {'output/37.2/01-editorial.pdf': digest})
    pipe6 = os.path.join(os.path.dirname(__file__), '..', 'html_pipeline', 'pipe6_ojs_xml.py')

    run = subprocess.run([sys.executable, pipe6, str(toc), '--no-issue-galley'],
                         capture_output=True, text=True, env=os.environ.copy())

    assert run.returncode != 0
    assert digest in run.stderr
    assert not (issue / 'import.xml').exists()


def test_a_store_file_that_does_not_hash_right_fails(tmp_path, store):
    digest = sha(PDF)
    (store / f'{digest}.pdf').write_bytes(b'%PDF-1.4\n% edited in place\n')
    _, issue, _ = corpus(tmp_path, {'output/37.2/01-editorial.pdf': digest})

    with pytest.raises(MissingPdf, match=digest):
        resolve_pdf(str(issue / '01-editorial.pdf'))


def test_a_pdf_in_neither_layout_fails(tmp_path, store):
    _, issue, toc = corpus(tmp_path)

    with pytest.raises(MissingPdf, match='01-editorial.pdf'):
        resolve_pdf(str(issue / '01-editorial.pdf'))
    with pytest.raises(MissingPdf, match='37.2'):
        find_issue_pdf(str(toc), 37, 2)
    # Optional lookups (a presaved issue galley, a --no-issue-galley cover)
    # may be absent from the corpus...
    assert resolve_pdf(str(issue / 'issue-galley.pdf'), required=False) is None
    assert find_issue_pdf(str(toc), 37, 2, required=False) is None


def test_an_optional_pdf_the_manifest_lists_still_fails_if_the_store_lacks_it(tmp_path, store):
    digest = sha(b'%PDF-1.4\n% redacted issue galley\n')
    _, issue, _ = corpus(tmp_path, {'output/37.2/issue-galley.pdf': digest})

    with pytest.raises(MissingPdf, match=digest):
        resolve_pdf(str(issue / 'issue-galley.pdf'), required=False)


def test_the_shell_resolver_pipe9_uses(tmp_path, store):
    """pipe9 calls `paths.py resolve`: a path on stdout, or exit 2 naming the hash."""
    present = put(store, b'%PDF-1.4\n% print\n')
    absent = sha(b'gone')
    backfill, issue, _ = corpus(tmp_path, {'input/37.2.pdf': present,
                                           'output/37.2/issue-galley.pdf': absent})

    def run(*args):
        return subprocess.run([sys.executable, PATHS_PY, 'resolve', *args],
                              capture_output=True, text=True, env=os.environ.copy())

    ok = run(str(backfill / 'input' / '37.2.pdf'))
    assert ok.returncode == 0 and ok.stdout.strip() == str(store / f'{present}.pdf')

    gone = run('--optional', str(issue / 'issue-galley.pdf'))
    assert gone.returncode == 2 and absent in gone.stderr

    none = run('--optional', str(backfill / 'input' / '37.1.pdf'))
    assert none.returncode == 0 and none.stdout == ''

    required = run(str(backfill / 'input' / '37.1.pdf'))
    assert required.returncode == 2 and '37.1.pdf' in required.stderr


def test_pipe9_resolves_every_pdf_through_paths_py():
    """pipe9 used to test `-f` on the input path and print SKIP."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'html_pipeline',
                            'pipe9_issue_galleys.sh'), encoding='utf-8').read()
    assert 'resolve --optional "$ISSUE_DIR/issue-galley.pdf"' in src
    assert 'SKIP: No source PDF' not in src
    assert '[ -f "$PRESAVED" ]' not in src


def test_pipe13_reads_the_pdf_galley_through_the_resolver():
    """pipe13 --galleys copies the article PDF into OJS; it must find the store copy."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'html_pipeline',
                            'pipe13_patch_metadata.py'), encoding='utf-8').read()
    assert "if key == 'application/pdf' and local:" in src
    assert 'local = resolve_pdf(local)' in src
