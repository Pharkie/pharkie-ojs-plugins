#!/usr/bin/env python3
"""
Generate two demo OJS Native XML import files with copyright-free content.

These fixtures let the full e2e test suite run without the private repo's
real journal content. The generated issues contain:
  - Open-access editorials with PDF + HTML galleys
  - Paywalled articles with PDF + HTML galleys + citations
  - Open-access book reviews
  - Issue-level PDF galley (for whole-issue purchase tests)
  - Shared author surname across 3+ articles (for search tests)
  - Shared title keyword across 2+ articles (for title search tests)

Usage:
    python fixtures/generate-sample-issues.py

Output:
    fixtures/sample-issues/1.1.xml   (Demo Vol 1 No 1)
    fixtures/sample-issues/1.2.xml   (Demo Vol 1 No 2)
"""

import base64
import io
import os
import textwrap
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from fpdf import FPDF

OUTDIR = os.path.join(os.path.dirname(__file__), 'sample-issues')


# ── PDF generation ───────────────────────────────────────────────────

def _latin1_safe(text: str) -> str:
    """Replace common Unicode chars with latin-1 equivalents for core PDF fonts."""
    return (text
            .replace('\u2014', '-')   # em dash
            .replace('\u2013', '-')   # en dash
            .replace('\u2018', "'")   # left single quote
            .replace('\u2019', "'")   # right single quote
            .replace('\u201c', '"')   # left double quote
            .replace('\u201d', '"')   # right double quote
            .replace('\u2026', '...') # ellipsis
            )


def make_article_pdf(title: str, authors: list[dict], pages: str, abstract: str | None = None) -> bytes:
    """Generate a simple single-page PDF with article title and placeholder text."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=25)

    # Title
    pdf.set_font('Helvetica', 'B', 16)
    pdf.multi_cell(0, 8, _latin1_safe(title), align='C')
    pdf.ln(4)

    # Authors
    author_names = _latin1_safe('; '.join(f"{a['given']} {a['family']}" for a in authors))
    pdf.set_font('Helvetica', 'I', 11)
    pdf.cell(0, 6, author_names, align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)

    # Pages
    pdf.set_font('Helvetica', '', 9)
    pdf.cell(0, 5, f'Pages {pages}', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(6)

    # Abstract
    if abstract:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(0, 6, 'Abstract', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 10)
        # Strip HTML tags and replace non-latin1 chars
        import re
        plain = re.sub(r'<[^>]+>', '', abstract)
        plain = _latin1_safe(plain)
        pdf.multi_cell(0, 5, plain)
        pdf.ln(4)

    # Placeholder body
    pdf.set_font('Helvetica', '', 10)
    pdf.multi_cell(0, 5, textwrap.dedent("""\
        This is a demo article generated for development and testing purposes. \
        It contains copyright-free placeholder content and is not intended for \
        publication or distribution.

        The full text of this article would appear here in a production environment. \
        This PDF serves as a galley file within the OJS (Open Journal Systems) \
        test instance."""))

    pdf.ln(4)
    pdf.set_font('Helvetica', 'I', 9)
    pdf.cell(0, 5, 'Demo fixture - pharkie-ojs-plugins', align='C')

    return pdf.output()


def make_issue_pdf(volume: int, number: int, year: int, articles: list[dict]) -> bytes:
    """Generate a simple issue-level PDF with table of contents."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=25)

    pdf.set_font('Helvetica', 'B', 18)
    pdf.cell(0, 10, 'Existential Analysis', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 12)
    pdf.cell(0, 8, f'Volume {volume}, Number {number}, {year}', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(8)

    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 8, 'Table of Contents', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(4)

    current_section = None
    for art in articles:
        if art['section'] != current_section:
            current_section = art['section']
            pdf.set_font('Helvetica', 'B', 11)
            pdf.cell(0, 7, current_section, new_x='LMARGIN', new_y='NEXT')

        author_names = '; '.join(f"{a['given']} {a['family']}" for a in art['authors'])
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 6, f"  {_latin1_safe(art['title'])}", new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', 'I', 9)
        pdf.cell(0, 5, f"    {author_names}  -  pp. {art['pages']}", new_x='LMARGIN', new_y='NEXT')

    pdf.ln(8)
    pdf.set_font('Helvetica', 'I', 9)
    pdf.cell(0, 5, 'Demo fixture - pharkie-ojs-plugins', align='C')

    return pdf.output()


def html_galley(body_html: str) -> tuple[str, int]:
    """Wrap body HTML in a full document and return (base64, size)."""
    doc = (
        '<!DOCTYPE html>\n<html lang="en">\n'
        '<head><meta charset="utf-8"><title>Full Text</title></head>\n'
        f'<body>\n{body_html}\n</body>\n</html>'
    )
    raw = doc.encode('utf-8')
    return base64.b64encode(raw).decode(), len(raw)


# ── Section definitions ──────────────────────────────────────────────
SECTIONS = {
    'Editorial': {
        'ref': 'ED', 'abbrev': 'ED', 'seq': 0,
        'meta_reviewed': '0', 'abstracts_not_required': '1',
    },
    'Articles': {
        'ref': 'ART', 'abbrev': 'ART', 'seq': 1,
        'meta_reviewed': '1', 'abstracts_not_required': '0',
    },
    'Book Review Editorial': {
        'ref': 'bookeditorial', 'abbrev': 'bookeditorial', 'seq': 2,
        'meta_reviewed': '0', 'abstracts_not_required': '1',
    },
    'Book Reviews': {
        'ref': 'BR', 'abbrev': 'BR', 'seq': 3,
        'meta_reviewed': '0', 'abstracts_not_required': '1',
    },
}

# access_status: 0 = paywalled (inherits journal subscription), 1 = open
SECTION_ACCESS = {
    'Editorial': '1',
    'Articles': '0',
    'Book Review Editorial': '1',
    'Book Reviews': '1',
}


# ── Demo content (all copyright-free / original) ────────────────────

ISSUE_1_ARTICLES = [
    {
        'section': 'Editorial',
        'title': 'On the Meaning of Philosophical Inquiry',
        'authors': [
            {'given': 'Alice', 'family': 'Smith', 'email': 'a.smith@demo.invalid', 'country': 'GB'},
            {'given': 'Robert', 'family': 'Chen', 'email': 'r.chen@demo.invalid', 'country': 'GB'},
        ],
        'pages': '1-3',
        'abstract': None,
        'keywords': [],
        'citations': [],
        'html_body': (
            '<h2>Editorial Introduction</h2>\n'
            '<p>This issue brings together diverse perspectives on existential '
            'thought and its practical applications in therapeutic settings. '
            'We are pleased to present work that bridges philosophical inquiry '
            'and clinical practice.</p>\n'
            '<p>The contributors explore themes of authenticity, freedom, '
            'responsibility, and the search for meaning in contemporary life. '
            'Each article offers a unique lens through which to examine the '
            'human condition.</p>\n'
            '<p>We invite readers to engage critically with these ideas and '
            'consider how they might inform both professional practice and '
            'personal reflection.</p>'
        ),
    },
    {
        'section': 'Articles',
        'title': 'Existential Perspectives on Authenticity in Modern Life',
        'authors': [
            {'given': 'James', 'family': 'Smith', 'email': 'j.smith@demo.invalid', 'country': 'GB'},
        ],
        'pages': '4-18',
        'abstract': (
            '<p>This paper examines the concept of authenticity as it appears '
            'in existential philosophy and its relevance to contemporary '
            'therapeutic practice. Drawing on Heidegger, Sartre, and Kierkegaard, '
            'we propose a framework for understanding authentic engagement '
            'with the challenges of modern life.</p>'
        ),
        'keywords': ['existential', 'authenticity', 'Heidegger', 'therapeutic practice'],
        'citations': [
            'Heidegger, M. (1927). Being and Time. Trans. J. Macquarrie & E. Robinson. New York: Harper & Row.',
            'Sartre, J.-P. (1943). Being and Nothingness. Trans. H. Barnes. New York: Philosophical Library.',
            'Kierkegaard, S. (1843). Either/Or. Trans. H. V. Hong & E. H. Hong. Princeton: Princeton University Press.',
            'May, R. (1958). Existence: A New Dimension in Psychiatry and Psychology. New York: Basic Books.',
            'Yalom, I. D. (1980). Existential Psychotherapy. New York: Basic Books.',
            'Spinelli, E. (2005). The Interpreted World: An Introduction to Phenomenological Psychology. London: Sage.',
            'Cooper, M. (2003). Existential Therapies. London: Sage Publications.',
            'Buber, M. (1923). I and Thou. Trans. R. G. Smith. Edinburgh: T. & T. Clark.',
        ],
        'html_body': (
            '<h2>Introduction</h2>\n'
            '<p>The question of authenticity has occupied existential thinkers '
            'since Kierkegaard first articulated the challenge of becoming a '
            'genuine self amid the pressures of social conformity. This paper '
            'traces the development of authenticity as a philosophical and '
            'therapeutic concept.</p>\n'
            '<h2>Theoretical Framework</h2>\n'
            '<p>Heidegger\'s concept of Eigentlichkeit (ownedness) provides '
            'the foundation for understanding authentic existence. For Heidegger, '
            'authenticity involves owning one\'s possibilities in the face of '
            'anxiety and the awareness of finitude.</p>\n'
            '<p>Sartre extended this analysis through his concept of bad faith '
            '(mauvaise foi), arguing that inauthenticity consists in denying '
            'one\'s freedom and responsibility for self-creation.</p>\n'
            '<h2>Clinical Applications</h2>\n'
            '<p>In therapeutic practice, the pursuit of authenticity manifests '
            'as a willingness to confront anxiety rather than flee from it. '
            'The therapist\'s role is to accompany the client in this '
            'confrontation, not to provide ready-made answers.</p>\n'
            '<h2>Notes</h2>\n'
            '<p>1. The term "authenticity" is used throughout this paper in its '
            'existential-philosophical sense, which differs from colloquial usage.</p>\n'
            '<p>2. All translations from German sources are the author\'s own unless '
            'otherwise noted.</p>\n'
            '<h2>Conclusion</h2>\n'
            '<p>Authenticity remains a vital concept for existential therapy, '
            'offering both a diagnostic lens and a therapeutic aspiration.</p>'
        ),
    },
    {
        'section': 'Articles',
        'title': 'Freedom and Responsibility in Existential Counselling',
        'authors': [
            {'given': 'Maria', 'family': 'Smith', 'email': 'm.smith@demo.invalid', 'country': 'US'},
        ],
        'pages': '19-32',
        'abstract': (
            '<p>This article explores the interplay between freedom and '
            'responsibility in existential counselling. Through case studies '
            'and philosophical analysis, we argue that genuine therapeutic '
            'change requires confronting both the possibilities and the '
            'burdens of human freedom.</p>'
        ),
        'keywords': ['existential', 'freedom', 'responsibility', 'counselling'],
        'citations': [
            'Frankl, V. E. (1946). Man\'s Search for Meaning. Boston: Beacon Press.',
            'Sartre, J.-P. (1946). Existentialism Is a Humanism. Trans. C. Macomber. New Haven: Yale University Press.',
            'May, R. (1969). Love and Will. New York: W. W. Norton.',
            'van Deurzen, E. (2002). Existential Counselling and Psychotherapy in Practice. London: Sage.',
            'Frankl, V. E. (1969). The Will to Meaning. New York: Plume.',
            'Cooper, M. (2003). Existential Therapies. London: Sage Publications.',
        ],
        'html_body': (
            '<h2>Introduction</h2>\n'
            '<p>Freedom is both the foundation and the central challenge of '
            'existential counselling. Clients often enter therapy seeking relief '
            'from the anxiety that accompanies the recognition of their own '
            'freedom to choose.</p>\n'
            '<h2>The Paradox of Freedom</h2>\n'
            '<p>Sartre famously declared that we are "condemned to be free." '
            'This paradox — that freedom is inescapable and yet experienced as '
            'a burden — lies at the heart of existential therapeutic work.</p>\n'
            '<p>Frankl complemented this view by emphasising the will to meaning: '
            'freedom finds its direction through the discovery of purpose.</p>\n'
            '<h2>Case Studies</h2>\n'
            '<p>The following cases illustrate how the freedom-responsibility '
            'dialectic manifests in clinical practice and how therapists can '
            'work with these themes effectively.</p>\n'
            '<h2>Conclusion</h2>\n'
            '<p>Existential counselling invites clients to embrace their freedom '
            'not as an abstract philosophical concept but as a lived reality '
            'with concrete implications for how they conduct their lives.</p>'
        ),
    },
    {
        'section': 'Articles',
        'title': 'Existential Anxiety and the Therapeutic Relationship',
        'authors': [
            {'given': 'David', 'family': 'Smith', 'email': 'd.smith@demo.invalid', 'country': 'GB'},
            {'given': 'Helen', 'family': 'Patel', 'email': 'h.patel@demo.invalid', 'country': 'GB'},
        ],
        'pages': '33-48',
        'abstract': (
            '<p>This paper investigates the role of existential anxiety in the '
            'therapeutic relationship. We argue that anxiety, rather than being '
            'merely a symptom to be eliminated, serves as a gateway to deeper '
            'self-understanding and authentic encounter between therapist and '
            'client.</p>'
        ),
        'keywords': ['existential', 'anxiety', 'therapeutic relationship', 'encounter'],
        'citations': [
            'Tillich, P. (1952). The Courage to Be. New Haven: Yale University Press.',
            'May, R. (1950). The Meaning of Anxiety. New York: Ronald Press.',
            'Boss, M. (1963). Psychoanalysis and Daseinsanalysis. Trans. L. Lefebre. New York: Basic Books.',
            'Bugental, J. F. T. (1981). The Search for Authenticity. New York: Irvington.',
            'van Deurzen, E. (1997). Everyday Mysteries: Existential Dimensions of Psychotherapy. London: Routledge.',
            'Yalom, I. D. (1980). Existential Psychotherapy. New York: Basic Books.',
            'Spinelli, E. (2007). Practising Existential Psychotherapy. London: Sage.',
        ],
        'html_body': (
            '<h2>Introduction</h2>\n'
            '<p>Anxiety occupies a privileged position in existential thought. '
            'Unlike clinical approaches that view anxiety primarily as pathology, '
            'the existential tradition understands it as a fundamental disclosure '
            'of the human condition.</p>\n'
            '<h2>Anxiety as Revelation</h2>\n'
            '<p>Tillich distinguished between existential anxiety (ontological, '
            'inherent to being) and neurotic anxiety (pathological, avoidant). '
            'The therapeutic task involves helping clients distinguish between '
            'these and engage constructively with existential anxiety.</p>\n'
            '<h2>The Therapeutic Encounter</h2>\n'
            '<p>When both therapist and client can tolerate existential anxiety '
            'without fleeing into technique or reassurance, a genuine encounter '
            'becomes possible. This encounter is itself therapeutic.</p>\n'
            '<h2>Notes</h2>\n'
            '<p>1. We use "anxiety" throughout in its existential sense (Angst), '
            'distinct from the clinical disorder category.</p>\n'
            '<h2>Conclusion</h2>\n'
            '<p>Existential anxiety, properly understood and held within the '
            'therapeutic relationship, becomes a catalyst for growth rather '
            'than a problem to be solved.</p>'
        ),
    },
    {
        'section': 'Book Reviews',
        'title': 'Review of "The Courage to Exist" by P. Thompson',
        'authors': [
            {'given': 'Sarah', 'family': 'Williams', 'email': 's.williams@demo.invalid', 'country': 'GB'},
        ],
        'pages': '49-50',
        'abstract': None,
        'keywords': [],
        'citations': [],
        'html_body': (
            '<h2>Book Review</h2>\n'
            '<p>Thompson\'s latest work offers a compelling exploration of '
            'existential courage in everyday life. Drawing on both philosophical '
            'sources and personal narrative, the book succeeds in making '
            'complex ideas accessible to a general readership.</p>\n'
            '<p>The chapters on facing mortality and embracing uncertainty are '
            'particularly strong, combining philosophical rigour with genuine '
            'warmth and humanity.</p>\n'
            '<p>Recommended for practitioners and general readers alike.</p>'
        ),
    },
]

ISSUE_2_ARTICLES = [
    {
        'section': 'Editorial',
        'title': 'Bridging Theory and Practice in Existential Thought',
        'authors': [
            {'given': 'Alice', 'family': 'Smith', 'email': 'a.smith@demo.invalid', 'country': 'GB'},
            {'given': 'Robert', 'family': 'Chen', 'email': 'r.chen@demo.invalid', 'country': 'GB'},
        ],
        'pages': '1-2',
        'abstract': None,
        'keywords': [],
        'citations': [],
        'html_body': (
            '<h2>Editorial</h2>\n'
            '<p>In this issue we continue our exploration of existential '
            'approaches to therapy and philosophy. The contributors examine '
            'phenomenological method, cross-cultural perspectives, and the '
            'ethics of therapeutic encounter.</p>\n'
            '<p>We are grateful to our reviewers and contributors for their '
            'dedication to advancing existential thought in both academic '
            'and clinical contexts.</p>'
        ),
    },
    {
        'section': 'Articles',
        'title': 'Phenomenological Method in Existential Research',
        'authors': [
            {'given': 'Thomas', 'family': 'Brown', 'email': 't.brown@demo.invalid', 'country': 'US'},
        ],
        'pages': '3-16',
        'abstract': (
            '<p>This paper reviews phenomenological research methods as applied '
            'to existential psychology. We examine the contributions of Husserl, '
            'Merleau-Ponty, and Giorgi, and propose methodological guidelines '
            'for researchers in the existential tradition.</p>'
        ),
        'keywords': ['phenomenology', 'existential', 'research methods', 'Husserl'],
        'citations': [
            'Husserl, E. (1913). Ideas Pertaining to a Pure Phenomenology. Trans. F. Kersten. The Hague: Nijhoff.',
            'Merleau-Ponty, M. (1945). Phenomenology of Perception. Trans. C. Smith. London: Routledge.',
            'Giorgi, A. (2009). The Descriptive Phenomenological Method in Psychology. Pittsburgh: Duquesne University Press.',
            'Moustakas, C. (1994). Phenomenological Research Methods. Thousand Oaks, CA: Sage.',
            'van Manen, M. (1990). Researching Lived Experience. Albany, NY: SUNY Press.',
        ],
        'html_body': (
            '<h2>Introduction</h2>\n'
            '<p>The relationship between existential philosophy and empirical '
            'research has long been a source of creative tension. This paper '
            'examines how phenomenological method can serve existential inquiry '
            'without reducing lived experience to mere data.</p>\n'
            '<h2>Historical Context</h2>\n'
            '<p>Husserl\'s call to return "to the things themselves" established '
            'the foundation for phenomenological research. His method of '
            'epoché — bracketing assumptions — remains central to '
            'existential-phenomenological inquiry.</p>\n'
            '<h2>Contemporary Methods</h2>\n'
            '<p>Giorgi\'s descriptive phenomenological method and interpretive '
            'phenomenological analysis (IPA) represent two major approaches '
            'currently used in existential research.</p>\n'
            '<h2>Conclusion</h2>\n'
            '<p>Phenomenological method, properly applied, honours both the '
            'rigour of research and the richness of lived experience.</p>'
        ),
    },
    {
        'section': 'Articles',
        'title': 'Cross-Cultural Dimensions of Existential Therapy',
        'authors': [
            {'given': 'Yuki', 'family': 'Tanaka', 'email': 'y.tanaka@demo.invalid', 'country': 'JP'},
            {'given': 'David', 'family': 'Smith', 'email': 'd.smith2@demo.invalid', 'country': 'GB'},
        ],
        'pages': '17-30',
        'abstract': (
            '<p>Existential therapy is often criticised as a Western-centric '
            'approach. This paper challenges that view by examining parallels '
            'between existential philosophy and Eastern thought, particularly '
            'Zen Buddhism and Japanese concepts of impermanence.</p>'
        ),
        'keywords': ['existential', 'cross-cultural', 'Zen Buddhism', 'impermanence'],
        'citations': [
            'Nishitani, K. (1982). Religion and Nothingness. Trans. J. Van Bragt. Berkeley: University of California Press.',
            'Suzuki, D. T. (1956). Zen Buddhism. New York: Doubleday.',
            'van Deurzen, E. (2010). Everyday Mysteries. 2nd ed. London: Routledge.',
            'Vos, J. (2018). Meaning in Life: An Evidence-Based Handbook for Practitioners. London: Palgrave.',
            'Cooper, M. (2003). Existential Therapies. London: Sage Publications.',
        ],
        'html_body': (
            '<h2>Introduction</h2>\n'
            '<p>The accusation that existential therapy is exclusively a product '
            'of Western philosophy overlooks significant convergences between '
            'existential thought and Eastern philosophical traditions.</p>\n'
            '<h2>Parallels with Zen Buddhism</h2>\n'
            '<p>Both existential philosophy and Zen Buddhism emphasise direct '
            'engagement with experience over abstract theorising. The Zen '
            'concept of mu (nothingness) resonates with Heidegger\'s analysis '
            'of das Nichts.</p>\n'
            '<h2>Implications for Practice</h2>\n'
            '<p>A cross-cultural existential therapy can draw on diverse '
            'philosophical resources while remaining attentive to the '
            'particular cultural context of each client.</p>\n'
            '<h2>Conclusion</h2>\n'
            '<p>Existential therapy, enriched by cross-cultural dialogue, '
            'becomes a more inclusive and globally relevant approach.</p>'
        ),
    },
    {
        'section': 'Book Reviews',
        'title': 'Review of "Anxiety and the Human Condition" by L. Marcus',
        'authors': [
            {'given': 'Emily', 'family': 'Jones', 'email': 'e.jones@demo.invalid', 'country': 'GB'},
        ],
        'pages': '31-32',
        'abstract': None,
        'keywords': [],
        'citations': [],
        'html_body': (
            '<h2>Book Review</h2>\n'
            '<p>Marcus offers a wide-ranging survey of how anxiety has been '
            'understood across the history of philosophy, from Kierkegaard '
            'to contemporary neuroscience.</p>\n'
            '<p>The book\'s strength lies in its careful attention to the '
            'distinction between productive and pathological anxiety. '
            'A valuable addition to any practitioner\'s library.</p>'
        ),
    },
]


# ── XML generation ───────────────────────────────────────────────────

NS = 'http://pkp.sfu.ca'
XSI = 'http://www.w3.org/2001/XMLSchema-instance'


def _id_counter():
    """Simple incrementing ID generator."""
    n = 100
    while True:
        yield n
        n += 1


def build_issue_xml(
    volume: int,
    number: int,
    year: int,
    date_published: str,
    articles: list[dict],
    include_issue_galley: bool = True,
) -> str:
    """Build a complete OJS Native XML import string for one issue."""

    ids = _id_counter()

    # Determine which sections are needed
    used_sections = sorted(
        {a['section'] for a in articles},
        key=lambda s: SECTIONS[s]['seq'],
    )

    root = ET.Element('issues')
    root.set('xmlns', NS)
    root.set('xmlns:xsi', XSI)
    root.set('xsi:schemaLocation', f'{NS} native.xsd')

    issue = ET.SubElement(root, 'issue')
    issue.set('xmlns:xsi', XSI)
    issue.set('published', '1')
    issue.set('current', '0')
    issue.set('access_status', '2')  # journal-level: subscription
    issue.set('url_path', '')

    ET.SubElement(issue, 'id', type='internal', advice='ignore').text = str(next(ids))

    ident = ET.SubElement(issue, 'issue_identification')
    ET.SubElement(ident, 'volume').text = str(volume)
    ET.SubElement(ident, 'number').text = str(number)
    ET.SubElement(ident, 'year').text = str(year)
    ET.SubElement(ident, 'title', locale='en').text = 'Existential Analysis'

    ET.SubElement(issue, 'date_published').text = date_published
    ET.SubElement(issue, 'last_modified').text = date_published

    # Sections
    sections_el = ET.SubElement(issue, 'sections')
    for sec_name in used_sections:
        sec = SECTIONS[sec_name]
        s = ET.SubElement(sections_el, 'section',
                          ref=sec['ref'], seq=str(sec['seq']),
                          editor_restricted='0', meta_indexed='1',
                          meta_reviewed=sec['meta_reviewed'],
                          abstracts_not_required=sec['abstracts_not_required'],
                          hide_title='0', hide_author='0',
                          abstract_word_count='0')
        ET.SubElement(s, 'abbrev', locale='en').text = sec['abbrev']
        ET.SubElement(s, 'title', locale='en').text = sec_name

    # Issue galley (whole-issue PDF)
    if include_issue_galley:
        issue_pdf = make_issue_pdf(volume, number, year, articles)
        issue_pdf_b64 = base64.b64encode(issue_pdf).decode()
        igs = ET.SubElement(issue, 'issue_galleys')
        ig = ET.SubElement(igs, 'issue_galley', locale='en')
        ET.SubElement(ig, 'label').text = 'PDF'
        igf = ET.SubElement(ig, 'issue_file')
        ET.SubElement(igf, 'file_name').text = f'vol-{volume}-iss-{number}.pdf'
        ET.SubElement(igf, 'file_type').text = 'application/pdf'
        ET.SubElement(igf, 'file_size').text = str(len(issue_pdf))
        ET.SubElement(igf, 'content_type').text = '1'
        ET.SubElement(igf, 'original_file_name').text = f'{volume}.{number}.pdf'
        ET.SubElement(igf, 'date_uploaded').text = date_published
        ET.SubElement(igf, 'date_modified').text = date_published
        ET.SubElement(igf, 'embed', encoding='base64').text = issue_pdf_b64

    # Articles
    articles_el = ET.SubElement(issue, 'articles')

    for seq, art in enumerate(articles):
        sec_name = art['section']
        sec = SECTIONS[sec_name]
        access = SECTION_ACCESS[sec_name]

        art_id = next(ids)
        pub_id = art_id  # publication ID = article ID for simplicity

        article_el = ET.SubElement(articles_el, 'article')
        article_el.set('xmlns:xsi', XSI)
        article_el.set('locale', 'en')
        article_el.set('date_submitted', date_published)
        article_el.set('status', '3')
        article_el.set('submission_progress', '')
        article_el.set('current_publication_id', str(pub_id))
        article_el.set('stage', 'production')

        ET.SubElement(article_el, 'id', type='internal', advice='ignore').text = str(art_id)

        # PDF submission file
        pdf_sf_id = next(ids)
        pdf_file_id = next(ids)
        sf = ET.SubElement(article_el, 'submission_file')
        sf.set('xmlns:xsi', XSI)
        sf.set('id', str(pdf_sf_id))
        sf.set('created_at', date_published)
        sf.set('file_id', str(pdf_file_id))
        sf.set('stage', 'proof')
        sf.set('updated_at', date_published)
        sf.set('viewable', 'false')
        sf.set('genre', 'Article Text')
        sf.set('uploader', 'admin')
        sf.set('xsi:schemaLocation', f'{NS} native.xsd')
        slug = art['title'][:30].lower().replace(' ', '-').strip('-')
        article_pdf = make_article_pdf(art['title'], art['authors'], art['pages'], art.get('abstract'))
        article_pdf_b64 = base64.b64encode(article_pdf).decode()
        ET.SubElement(sf, 'name', locale='en').text = f'{seq:02d}-{slug}.pdf'
        f_el = ET.SubElement(sf, 'file', id=str(pdf_file_id),
                             filesize=str(len(article_pdf)), extension='pdf')
        ET.SubElement(f_el, 'embed', encoding='base64').text = article_pdf_b64

        # HTML submission file
        html_b64, html_size = html_galley(art['html_body'])
        html_sf_id = next(ids)
        html_file_id = next(ids)
        hsf = ET.SubElement(article_el, 'submission_file')
        hsf.set('xmlns:xsi', XSI)
        hsf.set('id', str(html_sf_id))
        hsf.set('created_at', date_published)
        hsf.set('file_id', str(html_file_id))
        hsf.set('stage', 'proof')
        hsf.set('updated_at', date_published)
        hsf.set('viewable', 'false')
        hsf.set('genre', 'Article Text')
        hsf.set('uploader', 'admin')
        hsf.set('xsi:schemaLocation', f'{NS} native.xsd')
        ET.SubElement(hsf, 'name', locale='en').text = f'{seq:02d}-{slug}.html'
        hf_el = ET.SubElement(hsf, 'file', id=str(html_file_id),
                              filesize=str(html_size), extension='html')
        ET.SubElement(hf_el, 'embed', encoding='base64').text = html_b64

        # Publication
        pub = ET.SubElement(article_el, 'publication')
        pub.set('xmlns:xsi', XSI)
        pub.set('version', '1')
        pub.set('status', '3')
        pub.set('url_path', '')
        pub.set('seq', str(seq))
        pub.set('access_status', access)
        pub.set('date_published', date_published)
        pub.set('section_ref', sec['ref'])
        pub.set('xsi:schemaLocation', f'{NS} native.xsd')

        ET.SubElement(pub, 'id', type='internal', advice='ignore').text = str(pub_id)
        ET.SubElement(pub, 'title', locale='en').text = art['title']

        if art.get('abstract'):
            ET.SubElement(pub, 'abstract', locale='en').text = art['abstract']

        # Copyright
        first_author = art['authors'][0]
        copyright_name = f"{first_author['given']} {first_author['family']}"
        ET.SubElement(pub, 'copyrightHolder', locale='en').text = copyright_name
        ET.SubElement(pub, 'copyrightYear').text = str(year)

        if art.get('keywords'):
            kw_el = ET.SubElement(pub, 'keywords', locale='en')
            for kw in art['keywords']:
                ET.SubElement(kw_el, 'keyword').text = kw

        if art.get('pages'):
            ET.SubElement(pub, 'pages').text = art['pages']

        # Authors
        authors_el = ET.SubElement(pub, 'authors')
        authors_el.set('xmlns:xsi', XSI)
        authors_el.set('xsi:schemaLocation', f'{NS} native.xsd')
        for aseq, author in enumerate(art['authors']):
            a_el = ET.SubElement(authors_el, 'author',
                                 include_in_browse='true',
                                 user_group_ref='Author',
                                 seq=str(aseq),
                                 id=str(next(ids)))
            ET.SubElement(a_el, 'givenname', locale='en').text = author['given']
            ET.SubElement(a_el, 'familyname', locale='en').text = author['family']
            ET.SubElement(a_el, 'country').text = author['country']
            ET.SubElement(a_el, 'email').text = author['email']

        # Citations
        if art.get('citations'):
            cit_el = ET.SubElement(pub, 'citations')
            for cit in art['citations']:
                ET.SubElement(cit_el, 'citation').text = cit

        # PDF galley
        pdf_galley = ET.SubElement(pub, 'article_galley')
        pdf_galley.set('xmlns:xsi', XSI)
        pdf_galley.set('locale', 'en')
        pdf_galley.set('approved', 'false')
        pdf_galley.set('xsi:schemaLocation', f'{NS} native.xsd')
        ET.SubElement(pdf_galley, 'id', type='internal', advice='ignore').text = str(next(ids))
        ET.SubElement(pdf_galley, 'name', locale='en').text = 'PDF'
        ET.SubElement(pdf_galley, 'seq').text = '0'
        ET.SubElement(pdf_galley, 'submission_file_ref', id=str(pdf_sf_id))

        # HTML galley ("Full Text")
        html_galley_el = ET.SubElement(pub, 'article_galley')
        html_galley_el.set('xmlns:xsi', XSI)
        html_galley_el.set('locale', 'en')
        html_galley_el.set('approved', 'false')
        html_galley_el.set('xsi:schemaLocation', f'{NS} native.xsd')
        ET.SubElement(html_galley_el, 'id', type='internal', advice='ignore').text = str(next(ids))
        ET.SubElement(html_galley_el, 'name', locale='en').text = 'Full Text'
        ET.SubElement(html_galley_el, 'seq').text = '1'
        ET.SubElement(html_galley_el, 'submission_file_ref', id=str(html_sf_id))

    # Serialize
    ET.indent(root, space='  ')
    xml_decl = '<?xml version="1.0" encoding="utf-8"?>\n'
    return xml_decl + ET.tostring(root, encoding='unicode')


def main():
    os.makedirs(OUTDIR, exist_ok=True)

    # Issue 1: Vol 1 No 1
    xml1 = build_issue_xml(
        volume=1, number=1, year=2024,
        date_published='2024-01-15',
        articles=ISSUE_1_ARTICLES,
        include_issue_galley=True,
    )
    path1 = os.path.join(OUTDIR, '1.1.xml')
    with open(path1, 'w', encoding='utf-8') as f:
        f.write(xml1)
    print(f'Wrote {path1}')

    # Issue 2: Vol 1 No 2
    xml2 = build_issue_xml(
        volume=1, number=2, year=2024,
        date_published='2024-07-15',
        articles=ISSUE_2_ARTICLES,
        include_issue_galley=True,
    )
    path2 = os.path.join(OUTDIR, '1.2.xml')
    with open(path2, 'w', encoding='utf-8') as f:
        f.write(xml2)
    print(f'Wrote {path2}')

    # Summary
    total = len(ISSUE_1_ARTICLES) + len(ISSUE_2_ARTICLES)
    print(f'\nGenerated 2 issues with {total} articles total.')
    print('Author "Smith" appears in:', sum(
        1 for a in ISSUE_1_ARTICLES + ISSUE_2_ARTICLES
        for au in a['authors'] if au['family'] == 'Smith'
    ), 'author slots across articles')


if __name__ == '__main__':
    main()
