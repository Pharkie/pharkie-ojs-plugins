"""Tests for backfill.lib.doi_validate — the DOI guards.

Every fixture below is drawn from a defect that reached the live journal and,
in most cases, Crossref's monthly failed-resolution report.
"""

import pytest

from backfill.lib.doi_validate import (
    bad_doi_links,
    doi_problem,
    runon_doi_links,
)


# ---------- doi_problem ----------

@pytest.mark.parametrize('doi, expect_problem', [
    ('10.65828/c8ecrh72', False),
    ('10.1037//0003-066x.55.10.1117', False),   # APA's double slash is real
    ('10.1037/0003-066x.44.10.1285', False),    # ".10.1285" is volume.page
    ('10.1080/14733145.2010.485690', False),    # ".2010." is a year
    ('10.1002/(sici)1097-4679(199912)55:12<1481::aid-jclp6>3.0.co;2-#', False),
    ('', True),
    ('10.15697/10.5072/fk20p1509b', True),      # two DOIs concatenated
    ('10.1093/oi/', True),                      # trailing separator
    ('10.2466/17.04.PR0.113×17z0', True),       # OCR look-alike
    ('10.65828/c8ecrh72LINKS', True),           # run-on under our prefix
    ('https://www.journal-psychoanalysis.eu/x/', True),
])
def test_doi_problem(doi, expect_problem):
    assert (doi_problem(doi) is not None) is expect_problem


def test_doi_problem_names_the_fix():
    assert "'10.2466/17.04.PR0.113x17z0'" in doi_problem('10.2466/17.04.PR0.113×17z0')


def test_doi_problem_suggests_the_base_for_a_run_on():
    assert "'10.65828/c8ecrh72'" in doi_problem('10.65828/c8ecrh72LINKS')


# ---------- runon_doi_links ----------

# The exact markup from "Remembering Rimas" (37.2): the href is correct, but
# there is no whitespace between </a> and the next paragraph's text.
RIMAS = (
    '<p>Kočiūnas, R. (2000). Existential experience and group therapy. '
    '11.2: 91-112. DOI: <a href="https://doi.org/10.65828/nnfvfq11">'
    'https://doi.org/10.65828/nnfvfq11</a></p><p>Kočiūnas, R. &amp; '
    'Dragan, T. (2008). The phenomenon of self-disclosure.</p>'
)


def test_runon_detects_the_live_37_2_defect():
    found = runon_doi_links(RIMAS)
    assert found == [('10.65828/nnfvfq11', 'Kočiūnas,')]


def test_runon_accepts_whitespace_after_the_link():
    ok = RIMAS.replace('</a></p><p>', '</a></p>\n<p>')
    assert runon_doi_links(ok) == []


def test_runon_ignores_a_link_at_the_end_of_the_document():
    assert runon_doi_links('<p>DOI: <a href="https://doi.org/10.65828/c8ecrh72">x</a></p>') == []


# ---------- bad_doi_links ----------

def test_bad_doi_links_catches_a_web_address_behind_the_resolver():
    # Live in 35.2.
    html = ('<p>Lothane, Z. (2010). The legacies of Schreber and Freud. '
            '<a href="https://doi.org/https://www.journal-psychoanalysis.eu/articles/x/">'
            'link</a> [Accessed 2023.]</p>')
    found = bad_doi_links(html)
    assert len(found) == 1
    assert found[0][0].startswith('https://www.journal-psychoanalysis.eu')


def test_bad_doi_links_passes_a_correct_link():
    html = '<p><a href="https://doi.org/10.65828/c8ecrh72">c8ecrh72</a> .</p>'
    assert bad_doi_links(html) == []
