"""The crossref::doi wipe in pipe9b must never exceed the issues being rewritten.

Regression test for 2026-08-10: the DELETE was unconditional while --issue
scoped only the INSERT, so `pipe9b --target live --issue 37.2 --confirm` deleted
every reference DOI in the journal (6,945 rows) and wrote back 146, reporting
success.
"""

from backfill.html_pipeline.pipe9b_citation_dois import scoped_publication_ids

# (normalised title, volume, issue) -> publication_id, as fetch_all_citations
# builds it. Volume and issue are strings, straight from the DB.
PUB_LOOKUP = {
    ('remembering rimas', '37', '2'): 9834,
    ('editorial', '37', '2'): 9820,
    ('a twice-told protest', '34', '2'): 9710,
    ('memento mori', '10', '2'): 8703,
}


def _ref(volume, issue):
    return {'volume': volume, 'issue': issue}


def test_scope_is_limited_to_the_issue_being_rewritten():
    ids = scoped_publication_ids([_ref('37', '2')], PUB_LOOKUP)
    assert ids == [9820, 9834]


def test_scope_excludes_other_issues():
    ids = scoped_publication_ids([_ref('37', '2')], PUB_LOOKUP)
    assert 9710 not in ids and 8703 not in ids


def test_a_full_run_covers_every_publication():
    refs = [_ref('37', '2'), _ref('34', '2'), _ref('10', '2')]
    assert scoped_publication_ids(refs, PUB_LOOKUP) == sorted(PUB_LOOKUP.values())


def test_no_refs_means_no_scope_so_the_caller_refuses_to_delete():
    assert scoped_publication_ids([], PUB_LOOKUP) == []


def test_an_unknown_issue_scopes_to_nothing_rather_than_everything():
    assert scoped_publication_ids([_ref('99', '9')], PUB_LOOKUP) == []
