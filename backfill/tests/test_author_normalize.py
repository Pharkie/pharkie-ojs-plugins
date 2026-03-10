"""Tests for backfill/author_normalize.py — author name normalization."""

import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backfill.author_normalize import (
    normalize_key,
    extract_surname,
    extract_first_initial,
    split_multiple_authors,
    similarity,
    AuthorRegistry,
)


class TestNormalizeKey:
    def test_lowercase(self):
        assert normalize_key('Kim Loliya') == 'kim loliya'

    def test_strip_accents(self):
        assert normalize_key('Aleksandar Dimitrijević') == 'aleksandar dimitrijevic'

    def test_collapse_whitespace(self):
        assert normalize_key('  Emmy   van   Deurzen  ') == 'emmy van deurzen'

    def test_preserves_particles(self):
        assert normalize_key('Emmy van Deurzen') == 'emmy van deurzen'

    def test_empty_string(self):
        assert normalize_key('') == ''


class TestExtractSurname:
    def test_simple_name(self):
        assert extract_surname('Kim Loliya') == 'loliya'

    def test_particle_van(self):
        assert extract_surname('Emmy van Deurzen') == 'van deurzen'

    def test_particle_von(self):
        assert extract_surname('Carl von Weizsäcker') == 'von weizsäcker'

    def test_particle_de(self):
        assert extract_surname('Pierre de Fermat') == 'de fermat'

    def test_single_name(self):
        assert extract_surname('Madonna') == 'madonna'

    def test_middle_initial(self):
        """Middle initial should not be treated as surname."""
        assert extract_surname('Michael R. Montgomery') == 'montgomery'


class TestExtractFirstInitial:
    def test_simple(self):
        assert extract_first_initial('Kim Loliya') == 'k'

    def test_with_particle(self):
        assert extract_first_initial('Emmy van Deurzen') == 'e'


class TestSplitMultipleAuthors:
    def test_single_author(self):
        assert split_multiple_authors('Kim Loliya') == ['Kim Loliya']

    def test_two_authors(self):
        result = split_multiple_authors('Sheba Boakye-Duah & Neresia Osbourne')
        assert result == ['Sheba Boakye-Duah', 'Neresia Osbourne']

    def test_three_authors(self):
        result = split_multiple_authors('A & B & C')
        assert result == ['A', 'B', 'C']

    def test_empty_string(self):
        assert split_multiple_authors('') == []

    def test_none(self):
        assert split_multiple_authors(None) == []

    def test_whitespace_trimmed(self):
        result = split_multiple_authors('  Alice  &  Bob  ')
        assert result == ['Alice', 'Bob']


class TestSimilarity:
    def test_identical(self):
        assert similarity('hello', 'hello') == 1.0

    def test_completely_different(self):
        score = similarity('abc', 'xyz')
        assert score < 0.5

    def test_similar_strings(self):
        score = similarity('Montgomery', 'Montgomry')
        assert score > 0.8

    def test_case_insensitive(self):
        assert similarity('Hello', 'hello') == 1.0


class TestAuthorRegistry:
    """Tests using a temporary registry file."""

    def _make_registry(self, data=None):
        """Create a registry with a temp file."""
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        if data:
            json.dump(data, tmp)
        else:
            tmp.write('{}')
        tmp.close()
        return AuthorRegistry(path=tmp.name), tmp.name

    def test_lookup_exact(self):
        registry, path = self._make_registry({
            'Emmy van Deurzen': {'variants': ['E. van Deurzen'], 'articles': 5},
        })
        try:
            canonical, match_type = registry.lookup('Emmy van Deurzen')
            assert match_type == 'exact'
            assert canonical == 'Emmy van Deurzen'
        finally:
            os.unlink(path)

    def test_lookup_exact_via_variant(self):
        registry, path = self._make_registry({
            'Emmy van Deurzen': {'variants': ['E. van Deurzen'], 'articles': 5},
        })
        try:
            canonical, match_type = registry.lookup('E. van Deurzen')
            assert match_type == 'exact'
            assert canonical == 'Emmy van Deurzen'
        finally:
            os.unlink(path)

    def test_lookup_exact_normalized(self):
        """Lookup with different casing/accents still matches."""
        registry, path = self._make_registry({
            'Aleksandar Dimitrijević': {'variants': [], 'articles': 1},
        })
        try:
            canonical, match_type = registry.lookup('Aleksandar Dimitrijevic')
            # After normalization, accented and non-accented match
            assert match_type == 'exact'
            assert canonical == 'Aleksandar Dimitrijević'
        finally:
            os.unlink(path)

    def test_lookup_fuzzy_surname_initial(self):
        """Surname + first initial match returns fuzzy."""
        registry, path = self._make_registry({
            'Kim Loliya': {'variants': [], 'articles': 3},
        })
        try:
            canonical, match_type = registry.lookup('K. Loliya')
            assert match_type == 'fuzzy'
            assert canonical == 'Kim Loliya'
        finally:
            os.unlink(path)

    def test_lookup_new(self):
        registry, path = self._make_registry({})
        try:
            canonical, match_type = registry.lookup('Brand New Author')
            assert match_type == 'new'
            assert canonical == 'Brand New Author'
        finally:
            os.unlink(path)

    def test_lookup_empty(self):
        registry, path = self._make_registry({})
        try:
            canonical, match_type = registry.lookup('')
            assert match_type == 'empty'
        finally:
            os.unlink(path)

    def test_add_variant(self):
        registry, path = self._make_registry({
            'Kim Loliya': {'variants': [], 'articles': 1},
        })
        try:
            registry.add('Kim Loliya', variant='K. Loliya')
            assert 'K. Loliya' in registry.entries['Kim Loliya']['variants']
            # After adding variant, lookup should be exact
            canonical, match_type = registry.lookup('K. Loliya')
            assert match_type == 'exact'
            assert canonical == 'Kim Loliya'
        finally:
            os.unlink(path)

    def test_add_new_author(self):
        registry, path = self._make_registry({})
        try:
            registry.add('New Person')
            assert 'New Person' in registry.entries
            assert registry.entries['New Person']['variants'] == []
            assert registry.entries['New Person']['articles'] == 0
        finally:
            os.unlink(path)

    def test_increment(self):
        registry, path = self._make_registry({
            'Kim Loliya': {'variants': [], 'articles': 2},
        })
        try:
            registry.increment('Kim Loliya')
            assert registry.entries['Kim Loliya']['articles'] == 3
        finally:
            os.unlink(path)

    def test_save_and_reload(self):
        registry, path = self._make_registry({})
        try:
            registry.add('Test Author')
            registry.increment('Test Author')
            registry.save()

            registry2 = AuthorRegistry(path=path)
            assert 'Test Author' in registry2.entries
            assert registry2.entries['Test Author']['articles'] == 1
        finally:
            os.unlink(path)

    def test_ambiguous_match(self):
        """Two authors with same surname and initial returns ambiguous."""
        registry, path = self._make_registry({
            'John Smith': {'variants': [], 'articles': 2},
            'Jane Smith': {'variants': [], 'articles': 1},
        })
        try:
            canonical, match_type = registry.lookup('J. Smith')
            assert match_type == 'ambiguous'
            assert isinstance(canonical, list)
            assert len(canonical) == 2
        finally:
            os.unlink(path)
