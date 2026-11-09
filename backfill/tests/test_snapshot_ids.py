"""snapshot_ids must pair same-title articles with their own JATS files.

Regression test for 2026-11-10: the title -> JATS lookup was a dict, so in
an issue with several same-title articles every one of them mapped to the
LAST file. The dry run for 18.2 (three reviews titled "Book Review: Zone of
the Interior") would have written Moore's and then Berke's submission id and
DOI over Thompson's file, and 29.1 Westlake's over Bazzano's.
"""

import os

from backfill.html_pipeline.tools.snapshot_ids import match_articles, update_jats

TITLE = 'Book Review: Zone of the Interior'


def _jats(path, publisher_id=None, doi=None):
    ids = ''
    if publisher_id is not None:
        ids += f'<article-id pub-id-type="publisher-id">{publisher_id}</article-id>'
    if doi is not None:
        ids += f'<article-id pub-id-type="doi">{doi}</article-id>'
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<article><front><article-meta>'
        f'{ids}<title-group><article-title>x</article-title></title-group>'
        '</article-meta></front></article>')


def _toc(*stems_titles):
    return [{'title': t, 'split_pdf': f'./x/{s}.pdf'} for s, t in stems_titles]


def _art(sub_id, title=TITLE, doi=''):
    return {'title': title, 'submission_id': sub_id, 'doi': doi,
            'volume': '18', 'issue': '2'}


def _issue_18_2(tmp_path):
    _jats(tmp_path / '15-zone.jats.xml', 1781, '10.65828/45m4r197')
    _jats(tmp_path / '16-zone.jats.xml', 1782, '10.65828/1m2jwa38')
    _jats(tmp_path / '17-zone.jats.xml', 9021, '10.65828/7dd62s48')
    _jats(tmp_path / '19-new.jats.xml')
    return _toc(('15-zone', TITLE), ('16-zone', TITLE), ('17-zone', TITLE),
                ('19-new', 'Book Review: The Interpreted World'))


def test_same_title_articles_go_to_the_file_holding_their_id(tmp_path):
    toc = _issue_18_2(tmp_path)
    arts = [_art(1781), _art(1782), _art(9021)]
    pairs, problems = match_articles(arts, toc, str(tmp_path))
    assert problems == []
    assert {a['submission_id']: os.path.basename(p) for a, p in pairs} == {
        1781: '15-zone.jats.xml', 1782: '16-zone.jats.xml', 9021: '17-zone.jats.xml'}


def test_the_18_2_snapshot_changes_only_the_new_article(tmp_path):
    toc = _issue_18_2(tmp_path)
    arts = [_art(1781, doi='10.65828/45m4r197'), _art(1782, doi='10.65828/1m2jwa38'),
            _art(9021, doi='10.65828/7dd62s48'),
            _art(9843, 'Book Review: The Interpreted World', '10.65828/xdj13t94')]
    pairs, problems = match_articles(arts, toc, str(tmp_path))
    assert problems == []
    changed = [os.path.basename(p) for a, p in pairs
               if update_jats(p, a['submission_id'], a['doi'], dry_run=True)]
    assert changed == ['19-new.jats.xml']


def test_same_title_with_no_file_holding_the_id_is_ambiguous(tmp_path):
    toc = _issue_18_2(tmp_path)
    pairs, problems = match_articles([_art(5555)], toc, str(tmp_path))
    assert pairs == []
    assert [k for k, _, _ in problems] == ['AMBIGUOUS']


def test_a_unique_title_still_matches_a_file_with_no_ids(tmp_path):
    toc = _issue_18_2(tmp_path)
    pairs, problems = match_articles(
        [_art(9843, 'Book Review: The Interpreted World')], toc, str(tmp_path))
    assert problems == [] and os.path.basename(pairs[0][1]) == '19-new.jats.xml'


def test_no_file_is_paired_with_two_submissions(tmp_path):
    _jats(tmp_path / '01-a.jats.xml', 1)
    toc = _toc(('01-a', 'Editorial'))
    pairs, problems = match_articles(
        [_art(1, 'Editorial'), _art(2, 'Editorial')], toc, str(tmp_path))
    assert [a['submission_id'] for a, _ in pairs] == [1]
    assert [k for k, _, _ in problems] == ['CLAIMED']


def test_an_unknown_title_is_a_miss(tmp_path):
    toc = _issue_18_2(tmp_path)
    _, problems = match_articles([_art(1, 'Nothing like it')], toc, str(tmp_path))
    assert [k for k, _, _ in problems] == ['MISS']
