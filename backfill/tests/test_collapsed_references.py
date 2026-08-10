"""A reference list set as one paragraph must split; prose that isn't must not.

Some authors set their references as a single paragraph with <br> between
entries. Those breaks arrive here as newlines inside one item, so the whole list
became a single citation: "The He - art of Being" (7.2) had all 18 references as
one, "Sex and Circuses" (22.2) all 25, and one article in 8.2 had 34 split
across three lumps. Live OJS had them correctly separated throughout, so the
issues could not be reimported without destroying 71 citations between them.

The split has to be conditional. 12.1 carries a letter's address block and a
block quotation under the same heading, and splitting those would invent
citations that do not exist.
"""

import pytest

from backfill.html_pipeline.pipe4_extract_citations import split_collapsed_references

# Real text from backfill/private/output/7.2/10-the-he-art-of-being.
HE_ART_OF_BEING = "\n".join([
    "Levin, D. M. The Body's Recollection of Being Routledge Kegan and Paul 1985",
    "Gendlin, E. Focusing 1978 Bantam Books",
    "Heidegger, M. Being and Time (tr. Macquarrie J and Robinson E.) 1962 London SCM",
    "Merleau-Ponty, M. The Phenomenology of Perception (tr. Smith C.) 1962 Routledge Kegan and Paul",
    "Husserl, E. Logical Investigations (tr Findlay J.) 1970 NY Humanities Press",
    "Stern, D. The Interpersonal World of the Infant 1985 Basic Books Inc",
])

# Real text from backfill/private/output/12.1/13-letter-to-the-editors — the
# addressee block of a letter, which sits under the same trailing heading.
LETTER_ADDRESS = "\n".join([
    "Messrs Simon du Plock & John Heaton",
    "Editors",
    "Journal of the Society for Existential Analysis",
    "BM Existential",
    "London WC1N 3XX",
])


def test_a_collapsed_reference_list_splits_into_its_entries():
    assert split_collapsed_references(HE_ART_OF_BEING) == HE_ART_OF_BEING.split("\n")


def test_a_letters_address_block_is_left_alone():
    assert split_collapsed_references(LETTER_ADDRESS) == [LETTER_ADDRESS]


def test_a_single_reference_is_unchanged():
    one = "Buber, M. (1937). I and Thou. Clark, Edinburgh."
    assert split_collapsed_references(one) == [one]


def test_blank_lines_do_not_become_entries():
    out = split_collapsed_references(HE_ART_OF_BEING.replace("\n", "\n\n"))
    assert out == HE_ART_OF_BEING.split("\n")
    assert all(line.strip() for line in out)


@pytest.mark.parametrize('text', ['', '   ', '\n\n'])
def test_empty_input_is_returned_untouched(text):
    assert split_collapsed_references(text) == [text]
