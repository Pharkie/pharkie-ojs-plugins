"""Adding articles to an issue OJS already holds: pipe6 --only, pipe7
--add-articles's argument guards, and resequence_issue.py's matching.

The OJS side (the native import adding to an existing issue, pipe11
--submissions) was run against OJS 3.5.0.3 by hand on 9 Nov 2026; it needs a
running OJS, so it is not repeated here."""

import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backfill' / 'html_pipeline' / 'tools'))

from backfill.html_pipeline.pipe6_ojs_xml import generate_xml  # noqa: E402
import resequence_issue  # noqa: E402

NS = {'ojs': 'http://pkp.sfu.ca'}


def _toc(tmp_path):
    arts = []
    for idx, (title, section) in enumerate([
        ('Editorial', 'Editorial'),
        ('An Article', 'Articles'),
        ('Book Review: Host', 'Book Reviews'),
        ('Book Review: New One', 'Book Reviews'),
        ('Book Review: After', 'Book Reviews'),
    ]):
        stem = f'{idx:02d}-x{idx}'
        pdf = tmp_path / f'{stem}.pdf'
        pdf.write_bytes(b'%PDF-1.4 test')
        (tmp_path / f'{stem}.galley.html').write_text('<html><body><p>t</p></body></html>')
        arts.append({'title': title, 'authors': 'Jo Bloggs', 'section': section,
                     'split_pdf': str(pdf)})
    return {'volume': 23, 'issue': 2, 'date': 'July 2012', 'articles': arts}


class TestPipe6Only:
    def test_only_the_named_articles_with_their_toc_position(self, tmp_path):
        root = ET.fromstring(generate_xml(_toc(tmp_path), only={'03-x3'}))
        pubs = root.findall('.//ojs:publication', NS)
        assert [p.find('ojs:title', NS).text for p in pubs] == ['Book Review: New One']
        assert pubs[0].get('seq') == '3'
        assert pubs[0].get('date_published') == '2012-07-01'

    def test_issue_identification_kept_galley_and_cover_dropped(self, tmp_path):
        root = ET.fromstring(generate_xml(_toc(tmp_path), only={'03-x3'}))
        ident = root.find('.//ojs:issue_identification', NS)
        assert [ident.find(f'ojs:{t}', NS).text for t in ('volume', 'number', 'year')] == ['23', '2', '2012']
        assert root.find('.//ojs:issue_galleys', NS) is None
        assert root.find('.//ojs:covers', NS) is None

    def test_sections_only_for_the_named_articles(self, tmp_path):
        root = ET.fromstring(generate_xml(_toc(tmp_path), only={'03-x3'}))
        refs = [s.get('ref') for s in root.findall('.//ojs:sections/ojs:section', NS)]
        assert refs == ['BR']

    def test_without_only_every_article(self, tmp_path):
        root = ET.fromstring(generate_xml(_toc(tmp_path), skip_issue_galley=True))
        assert len(root.findall('.//ojs:article', NS)) == 5


PIPE7 = ROOT / 'backfill' / 'html_pipeline' / 'pipe7_import.sh'


def _pipe7(*args):
    return subprocess.run(['bash', str(PIPE7), *args], capture_output=True, text=True)


class TestPipe7AddArticlesGuards:
    def test_needs_both_containers_named(self, tmp_path):
        p = _pipe7(str(tmp_path), '--add-articles', '--container=x')
        assert p.returncode == 1
        assert 'needs both --container' in p.stdout

    def test_refuses_force(self, tmp_path):
        p = _pipe7(str(tmp_path), '--add-articles', '--container=x', '--db-container=y', '--force')
        assert p.returncode == 1
        assert 'cannot be combined' in p.stdout


def _row(sid, seq, title):
    return {'sid': sid, 'pid': sid + 1000, 'seq': float(seq), 'title': title}


class TestResequencePlan:
    def _toc(self, titles):
        return {'articles': [{'title': t, 'split_pdf': ''} for t in titles]}

    def test_new_article_shifts_the_ones_after_it(self):
        ojs = [_row(1, 0, 'Host'), _row(2, 1, 'After'), _row(9, 1, 'New One')]
        _, changes, problems = resequence_issue.plan(self._toc(['Host', 'New One', 'After']), ojs)
        assert problems == []
        assert [(idx, row['sid']) for idx, _, row in changes] == [(2, 2)]

    def test_already_in_order(self):
        ojs = [_row(1, 0, 'Host'), _row(9, 1, 'New One'), _row(2, 2, 'After')]
        _, changes, problems = resequence_issue.plan(self._toc(['Host', 'New One', 'After']), ojs)
        assert changes == [] and problems == []

    def test_problems_both_ways(self):
        ojs = [_row(1, 0, 'Host'), _row(2, 1, 'Only In OJS')]
        _, _, problems = resequence_issue.plan(self._toc(['Host', 'Only In Toc']), ojs)
        assert any('Only In Toc' in p and 'not in OJS' in p for p in problems)
        assert any('submission 2' in p and 'not in toc.json' in p for p in problems)

    def test_duplicate_titles_are_ambiguous(self):
        ojs = [_row(1, 0, 'Same'), _row(2, 1, 'Same')]
        _, _, problems = resequence_issue.plan(self._toc(['Same']), ojs)
        assert any('2 OJS articles have this title' in p for p in problems)

    def test_publisher_id_wins_over_title(self, monkeypatch):
        monkeypatch.setattr(resequence_issue, 'jats_publisher_id',
                            lambda p: 2 if p == 'b' else None)
        toc = {'articles': [{'title': 'Old Title', 'split_pdf': 'b'},
                            {'title': 'Host', 'split_pdf': 'a'}]}
        ojs = [_row(1, 0, 'Host'), _row(2, 1, 'Corrected Title')]
        _, changes, problems = resequence_issue.plan(toc, ojs)
        assert problems == []
        assert sorted((idx, row['sid']) for idx, _, row in changes) == [(0, 2), (1, 1)]
