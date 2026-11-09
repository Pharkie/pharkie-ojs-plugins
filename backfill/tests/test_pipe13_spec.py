"""pipe13 spec mode: corrections made in Harbour, carried back to OJS by DOI.

plan_spec is pure: OJS state in, SQL (with its undo) and a redeposit list
out. These pin what it may and may not touch on the live journal.
"""

from backfill.html_pipeline.pipe13_patch_metadata import (
    plan_spec, stamp_sql, transaction)

COLUMNS = ['author_id', 'email', 'include_in_browse', 'publication_id', 'seq',
           'user_group_id']


def _state(*authors, title='A Title', holder='Ann Smith (Author)', **kw):
    st = {'publication_id': 50, 'doi': '10.65828/abc',
          'date_published': '1996-01-01', 'copyright_year': '1996',
          'title_raw': title, 'holder_raw': holder, 'vol_iss': '8.1',
          'status': 3, 'submission_status': 3,
          'pub_last_modified': '2026-09-01 10:00:00',
          'sub_last_modified': '2026-09-01 10:00:00',
          'authors': [{'author_id': 700 + i, 'seq': i, 'given': g, 'family': f}
                      for i, (g, f) in enumerate(authors)]}
    st.update(kw)
    return st


def _art(**kw):
    art = {'n': 1, 'label': '8.1 A Title', 'doi': '10.65828/abc',
           'submission_id': 5, 'title': None, 'contribs': None,
           'date_published': None, 'unpublish': False}
    art.update(kw)
    return art


def _plan(art, state, issues=(), issue_state=None):
    return plan_spec(list(issues), [art], {5: state}, issue_state or {}, COLUMNS)


def _sql(plan):
    return '\n'.join(c['sql'] for c in plan['changes'])


def _undo(plan):
    return '\n'.join(c['undo'] for c in plan['changes'])


def test_an_unchanged_name_keeps_the_ojs_split():
    # OJS holds "Anders Draeby | Soerensen"; the same full name split another
    # way is not a change.
    plan = _plan(_art(contribs=[('Anders', 'Draeby Soerensen')]),
                 _state(('Anders Draeby', 'Soerensen'),
                        holder='Anders Draeby Soerensen (Author)'))
    assert plan['changes'] == [] and plan['blocked'] == []
    assert plan['reindex'] == [] and plan['redeposit'] == {}
    assert plan['touched']['publications'] == {}


def test_exact_split_corrects_the_split_alone():
    plan = _plan(_art(contribs=[('Yaqui Andrés', 'Martínez Robles')], exact_split=True),
                 _state(('Yaqui Andrés Martínez', 'Robles'),
                        holder='Yaqui Andrés Martínez Robles (Author)'))
    sql, undo = _sql(plan), _undo(plan)
    assert "'givenName', 'Yaqui Andrés') ON DUPLICATE KEY UPDATE setting_value='Yaqui Andrés';" in sql
    assert "'familyName', 'Martínez Robles') ON DUPLICATE KEY UPDATE setting_value='Martínez Robles';" in sql
    assert 'copyrightHolder' not in sql
    assert "'givenName', 'Yaqui Andrés Martínez')" in undo
    assert "'familyName', 'Robles')" in undo
    assert plan['redeposit'] == {'8.1': ['10.65828/abc']}


def test_exact_split_already_right_is_no_change():
    plan = _plan(_art(contribs=[('Wilfried', 'Ver Eecke')], exact_split=True),
                 _state(('Wilfried', 'Ver Eecke'), holder='Wilfried Ver Eecke (Author)'))
    assert plan['changes'] == [] and plan['redeposit'] == {}


def test_a_renamed_author_is_upserted_redeposited_and_undoable():
    plan = _plan(_art(contribs=[('Robert Gareth', 'Hill')]),
                 _state(('Robert', 'Hill'), holder='Robert Hill (Author)'))
    sql, undo = _sql(plan), _undo(plan)
    assert ("VALUES (700, 'en', 'givenName', 'Robert Gareth') "
            "ON DUPLICATE KEY UPDATE setting_value='Robert Gareth';") in sql
    assert 'familyName' not in sql
    assert "'Robert Gareth Hill (Author)'" in sql
    assert "VALUES (700, 'en', 'givenName', 'Robert')" in undo
    assert "setting_value='Robert Hill (Author)'" in undo
    assert plan['reindex'] == [5]
    assert plan['redeposit'] == {'8.1': ['10.65828/abc']}
    assert plan['touched']['submissions'] == {5: '2026-09-01 10:00:00'}


def test_a_name_with_no_old_row_is_deleted_on_undo():
    plan = _plan(_art(contribs=[('Ann', 'Smith')]), _state(('Ann', None)))
    assert ("DELETE FROM author_settings WHERE author_id=700 AND "
            "setting_name='familyName' AND locale='en';") in _undo(plan)


def test_non_ascii_names_pass_through_untouched():
    plan = _plan(_art(contribs=[('Rimantas', 'Kočiūnas')]),
                 _state(('Rimantas', 'Koèiûnas'), holder='Rimantas Koèiûnas (Author)'))
    assert "'Kočiūnas'" in _sql(plan) and "'Koèiûnas'" in _undo(plan)


def test_an_extra_author_is_appended_after_the_last_and_removed_on_undo():
    plan = _plan(_art(contribs=[('F. A.', 'Jenner'), ('Man Cheung', 'Chung')]),
                 _state(('F.A. Jenner assisted by M.C.', 'Chung'),
                        holder='F.A. Jenner assisted by M.C. Chung (Author)'))
    assert plan['blocked'] == []
    insert = [c for c in plan['changes'] if c['sql'].startswith('INSERT INTO authors')]
    assert len(insert) == 1
    assert ('INSERT INTO authors (email, include_in_browse, publication_id, seq, '
            "user_group_id) SELECT '', include_in_browse, publication_id, 1, "
            'user_group_id FROM authors WHERE author_id=700;') in insert[0]['sql']
    assert "'givenName', 'Man Cheung'" in insert[0]['sql']
    assert 'DELETE FROM authors WHERE publication_id=50 AND seq=1;' in insert[0]['undo']


def test_dropping_an_author_is_refused_and_plans_nothing_for_it():
    plan = _plan(_art(title='New', contribs=[('Ann', 'Smith')]),
                 _state(('Ann', 'Smith'), ('Bob', 'Jones')))
    assert plan['changes'] == [] and 'removing an author' in plan['blocked'][0]


def test_a_doi_mismatch_patches_nothing():
    plan = _plan(_art(title='New'), _state(('Ann', 'Smith'), doi='10.65828/other'))
    assert plan['changes'] == [] and 'carries DOI' in plan['blocked'][0]


def test_a_date_sets_copyright_year_and_redeposits_without_reindex():
    plan = _plan(_art(date_published='1997-01-01'), _state(('Ann', 'Smith')))
    sql = _sql(plan)
    assert "date_published='1997-01-01' WHERE publication_id=50" in sql
    assert "setting_value='1997' WHERE publication_id=50 AND setting_name='copyrightYear'" in sql
    assert "date_published='1996-01-01' WHERE publication_id=50" in _undo(plan)
    assert plan['reindex'] == []
    assert plan['redeposit'] == {'8.1': ['10.65828/abc']}


def test_an_issue_date_redeposits_the_issue_doi_and_is_undoable():
    issue_state = {(8, 1): {'issue_id': 418, 'date_published': '1996-01-01 00:00:00',
                            'year': '1996', 'doi': '10.65828/4jyfsz84',
                            'last_modified': '2026-09-01 10:00:00'}}
    plan = plan_spec([{'volume': 8, 'number': 1, 'date_published': '1997-01-01'}],
                     [], {}, issue_state, COLUMNS)
    c = plan['changes'][0]
    assert c['sql'] == ("UPDATE issues SET date_published='1997-01-01 00:00:00', "
                        'year=1997 WHERE issue_id=418;')
    assert c['undo'] == ("UPDATE issues SET date_published='1996-01-01 00:00:00', "
                         'year=1996 WHERE issue_id=418;')
    assert plan['redeposit'] == {'8.1': ['10.65828/4jyfsz84']}
    assert plan['touched']['issues'] == {418: '2026-09-01 10:00:00'}


def test_unpublish_goes_through_ojs_not_sql_and_is_not_redeposited():
    plan = _plan(_art(unpublish=True), _state(('Ann', 'Smith')))
    assert plan['changes'] == [] and plan['unpublish'] == [(5, '8.1 A Title')]
    assert plan['redeposit'] == {}


def test_unpublish_twice_does_nothing():
    plan = _plan(_art(unpublish=True), _state(('Ann', 'Smith'), status=1))
    assert plan['unpublish'] == []


def test_last_modified_is_stamped_and_restored():
    apply, undo = stamp_sql({'publications': {50: '2026-09-01 10:00:00'},
                             'submissions': {5: None}, 'issues': {}})
    assert apply == ['UPDATE publications SET last_modified=NOW() WHERE publication_id IN (50);',
                     'UPDATE submissions SET last_modified=NOW() WHERE submission_id IN (5);']
    assert undo == ["UPDATE publications SET last_modified='2026-09-01 10:00:00' WHERE publication_id=50;",
                    'UPDATE submissions SET last_modified=NULL WHERE submission_id=5;']


def test_the_batch_is_one_utf8mb4_transaction():
    sql = transaction(['UPDATE x SET y=1;'])
    assert sql.splitlines() == ['SET NAMES utf8mb4;', 'START TRANSACTION;',
                                'UPDATE x SET y=1;', 'COMMIT;']
