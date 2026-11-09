"""pipe13 spec mode: corrections made in Harbour, carried back to OJS by DOI.

plan_spec is pure: OJS state in, SQL and a redeposit list out. These pin what
it may and may not touch on the live journal.
"""

from backfill.html_pipeline.pipe13_patch_metadata import plan_spec

COLUMNS = ['author_id', 'email', 'include_in_browse', 'publication_id', 'seq',
           'user_group_id']


def _state(*authors, title='A Title', holder='Ann Smith (Author)'):
    return {'publication_id': 50, 'title': title, 'abstract': '',
            'copyright_holder': holder,
            'authors': [{'author_id': 700 + i, 'seq': i, 'given': g,
                         'family': f, 'orcid': ''}
                        for i, (g, f) in enumerate(authors)]}


EXTRAS = {50: {'doi': '10.65828/abc', 'date_published': '1996-01-01',
               'copyright_year': '1996', 'vol_iss': '8.1', 'status': 3}}


def _art(**kw):
    art = {'n': 1, 'label': '8.1 A Title', 'doi': '10.65828/abc',
           'submission_id': 5, 'title': None, 'contribs': None,
           'date_published': None, 'unpublish': False}
    art.update(kw)
    return art


def _plan(art, state, issues=(), issue_state=None, extras=EXTRAS):
    return plan_spec(list(issues), [art], {5: state}, extras,
                     issue_state or {}, COLUMNS)


def test_an_unchanged_name_keeps_the_ojs_split():
    # OJS holds "Anders Draeby | Soerensen"; the same full name split another
    # way is not a change.
    state = _state(('Anders Draeby', 'Soerensen'),
                   holder='Anders Draeby Soerensen (Author)')
    updates, blocked, changed, redeposit = _plan(
        _art(contribs=[('Anders', 'Draeby Soerensen')]), state)
    assert updates == [] and blocked == [] and changed == [] and redeposit == {}


def test_a_renamed_author_is_patched_and_redeposited():
    state = _state(('Robert', 'Hill'), holder='Robert Hill (Author)')
    updates, blocked, changed, redeposit = _plan(
        _art(contribs=[('Robert Gareth', 'Hill')]), state)
    sql = '\n'.join(u[0] for u in updates)
    assert "setting_value='Robert Gareth' WHERE author_id=700 AND setting_name='givenName'" in sql
    assert 'familyName' not in sql
    assert "'Robert Gareth Hill (Author)'" in sql
    assert changed == [5]
    assert redeposit == {'8.1': ['10.65828/abc']}


def test_an_extra_author_is_appended_after_the_last():
    state = _state(('F.A. Jenner assisted by M.C.', 'Chung'),
                   holder='F.A. Jenner assisted by M.C. Chung (Author)')
    updates, blocked, _, _ = _plan(
        _art(contribs=[('F. A.', 'Jenner'), ('Man Cheung', 'Chung')]), state)
    assert blocked == []
    insert = [u[0] for u in updates if u[0].startswith('INSERT INTO authors')]
    assert len(insert) == 1
    assert ('INSERT INTO authors (email, include_in_browse, publication_id, seq, '
            "user_group_id) SELECT '', include_in_browse, publication_id, 1, "
            'user_group_id FROM authors WHERE author_id=700;') in insert[0]
    assert "'givenName', 'Man Cheung'" in insert[0]


def test_dropping_an_author_is_refused():
    state = _state(('Ann', 'Smith'), ('Bob', 'Jones'))
    updates, blocked, _, _ = _plan(_art(contribs=[('Ann', 'Smith')]), state)
    assert updates == [] and 'removing an author' in blocked[0]


def test_a_doi_mismatch_patches_nothing():
    extras = {50: dict(EXTRAS[50], doi='10.65828/other')}
    updates, blocked, _, _ = _plan(_art(title='New'), _state(('Ann', 'Smith')),
                                   extras=extras)
    assert updates == [] and 'carries DOI' in blocked[0]


def test_a_date_sets_copyright_year_and_redeposits_without_reindex():
    updates, _, changed, redeposit = _plan(
        _art(date_published='1997-01-01'), _state(('Ann', 'Smith')))
    sql = '\n'.join(u[0] for u in updates)
    assert "date_published='1997-01-01' WHERE publication_id=50" in sql
    assert "setting_value='1997' WHERE publication_id=50 AND setting_name='copyrightYear'" in sql
    assert changed == []
    assert redeposit == {'8.1': ['10.65828/abc']}


def test_an_issue_date_redeposits_the_issue_doi():
    issue_state = {(8, 1): {'issue_id': 418, 'date_published': '1996-01-01',
                            'year': '1996', 'doi': '10.65828/4jyfsz84'}}
    updates, _, _, redeposit = plan_spec(
        [{'volume': 8, 'number': 1, 'date_published': '1997-01-01'}], [], {}, {},
        issue_state, COLUMNS)
    assert updates[0][0] == ("UPDATE issues SET date_published='1997-01-01 00:00:00', "
                             'year=1997 WHERE issue_id=418;')
    assert redeposit == {'8.1': ['10.65828/4jyfsz84']}


def test_unpublish_takes_it_offline_and_is_not_redeposited():
    updates, _, changed, redeposit = _plan(_art(unpublish=True),
                                           _state(('Ann', 'Smith')))
    assert 'UPDATE publications SET status=1 WHERE publication_id=50;' in updates[0][0]
    assert 'DELETE FROM submission_search_objects WHERE submission_id=5;' in updates[0][0]
    assert changed == [] and redeposit == {}


def test_unpublish_twice_does_nothing():
    extras = {50: dict(EXTRAS[50], status=1)}
    updates, _, _, _ = _plan(_art(unpublish=True), _state(('Ann', 'Smith')),
                             extras=extras)
    assert updates == []
