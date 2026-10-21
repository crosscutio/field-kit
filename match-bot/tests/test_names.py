"""Number-aware name parsing. Pairs are taken from the Cross River settlement
master list and its GRID3 / MDA supervision references, where the same
number is written 2, II, _2, glued, or as a word."""
import pandas as pd
import pytest

from match_bot.core.fuzzy import fuzzy_match_1_to_1, validate_number_match
from match_bot.core.names import canonical, numbers_compatible, parse_name
from match_bot.core.suggest import score_pair

SAME = [
    ('OFUTOP 2', 'OFUTOP_2'),
    ('Kaku 2', 'Kaku Ii'),
    ('ISONG INYANG 2', 'ISONG INYANG2'),
    ('ISONG INYANG 2', 'Isong Inyang Ii'),
    ('EBEDENG 11', 'EBEDENG 2'),          # digits typed for a Roman numeral
    ('Esit Ikot 1&2', 'Esit Ikot 2'),      # range
    ('Ikot Eyo Two( 2)', 'Ikot Eyo 2'),
    ('CAMP 9', 'Camp_9'),
    ('PATIGI 1V', 'Patigi IV'),
    ('AMANA II Main Town', 'Amana 2'),     # numeral mid-name
    ('Okoroba 2', 'Okoroba Ii Mbianang'),
]

DIFFERENT = [
    ('OFUTOP 1', 'OFUTOP_2'),
    ('OLD NETIM II', 'OLD_NETIM_I'),
    ('OKWABANG 3', 'OKWABANG_1'),
    ('CAMP 9', 'CAMP_4'),
    ('OLD NETIM I', 'Old Netim'),          # one-sided
    ('MKPARA', 'MKPARA_2'),                # one-sided
    ('village 12', 'village 13'),
    ('EBEDENG 1', 'EBEDENG 11'),
    ('MKPARA', 'Mkpara Ii Village'),       # one-sided, numeral mid-name
]


@pytest.mark.parametrize('a,b', SAME)
def test_same_number_any_spelling(a, b):
    assert numbers_compatible(a, b)
    assert score_pair(a, b) >= 90


@pytest.mark.parametrize('a,b', DIFFERENT)
def test_different_or_one_sided_numbers_never_pair(a, b):
    assert not numbers_compatible(a, b)
    assert score_pair(a, b) == 0


@pytest.mark.parametrize('name', ['FOUR CORNER', 'ONE MAN COUNTRY', 'I D P Camp', 'Kwarbai A',
                                  'J I BARRACK', 'SEN. F. I. GIWA'])
def test_words_that_only_look_like_numbers_are_kept(name):
    assert parse_name(name).numbers == frozenset()


def test_canonical_spelling():
    for s in ('OFUTOP_2', 'Ofutop II', 'ofutop2', 'Ofutop Two'):
        assert canonical(s) == 'ofutop 2'
    assert canonical('Ikot Eyo Two( 2)') == 'ikot eyo 2'
    assert canonical('12') == '12'
    assert canonical('Mbobui') == 'mbobui'
    assert canonical("Doctor's Quarters") == "doctor's quarters"


def test_unnumbered_names_score_as_before():
    # Punctuation stays, so a name buried in a slash list isn't a perfect subset.
    assert score_pair('Akugom', 'Lekpankom/ Akugom/ Kotani/ Esekati.') < 50


def test_hungarian_pairs_siblings_by_number():
    # By edit distance alone "ofutop 1" (1 edit) beats the misspelt
    # "offuttop 2" (2 edits) for the reference "ofutop 2" -- the wrong sibling.
    targets = pd.DataFrame({'id': ['t1', 't2', 't3'],
                            'name': ['ofutop 1', 'offuttop 2', 'ofutop 3']})
    refs = pd.DataFrame({'ref_id': ['r2'], 'name': ['ofutop 2']})
    ungated = fuzzy_match_1_to_1(refs, targets).set_index('id')
    assert ungated.loc['t1', 'ref_id_lev'] == 'r2'
    out = fuzzy_match_1_to_1(refs, targets, number_aware=True).set_index('id')
    assert out.loc['t2', 'ref_id_lev'] == 'r2'
    assert out.loc['t1', 'ref_id_lev'] is None   # its sibling's point is off limits
    assert out.loc['t3', 'ref_id_lev'] is None


def test_validate_number_match_uses_all_numbers():
    df = pd.DataFrame({'name': ['zone 1 2', 'zone 1 2', 'kolo'],
                       'closest_levenshtein_match': ['zone 1 3', 'zone 3', 'kolo 2']})
    kept = validate_number_match(df)
    assert kept['closest_levenshtein_match'].tolist() == ['zone 1 3']
