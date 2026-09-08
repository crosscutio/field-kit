import contextlib
import io

import pandas as pd

from match_bot.core import suggest as sg
from match_bot.core.lookup import load_lookup
from tests.conftest import run_lookups


def leaf(config):
    return pd.read_csv(config.lookups_dir / 'leaf_lookup.csv', dtype=str).fillna('')


def test_score_pair_strips_standalone_digits():
    assert sg.score_pair('Ward 2', 'Zone 2') < 60
    assert sg.score_pair('Gagnoa 1', 'Gagnoa') == 100


def test_hierarchy_suggest_then_apply_unblocks_children(project_with_lookups):
    cfg = project_with_lookups
    region = sg.suggest(cfg, 'region', threshold=70)
    assert [(s['target_name'].lower(), s['ref_name']) for s in region] == [('centr', 'centre')]
    assert region[0]['target_key'] == 'centr'

    res = sg.apply(cfg, 'region', region)
    assert res['applied'] == 1 and res['dropped'] == 1
    df = load_lookup(str(cfg.lookups_dir / 'region_lookup.csv'))
    row = df[df['target_name_raw'] == 'Centr'].iloc[0]
    assert row['match_type'] == 'manual' and row['reference_name'] == 'centre'
    assert row['mapping_rationale'].startswith('auto: suggest score=')
    assert 'centre' not in set(df[df['match_type'] == 'reference_only']['reference_name'])

    run_lookups(cfg)
    # Foo (Centr/Eps) now exact-matches R7 because the region is harmonized.
    lf = leaf(cfg)
    assert lf[lf['target_id'] == 'T6'].iloc[0]['match_type'] == 'exact'

    district = sg.suggest(cfg, 'district', threshold=70)
    assert [(s['target_name'].lower(), s['ref_name']) for s in district] == [('gama', 'gamma')]
    assert district[0]['parents'] == {'region': 'sud'}


def test_leaf_suggest_is_scoped_and_one_to_one(project_with_lookups):
    cfg = project_with_lookups
    out = sg.suggest(cfg, 'leaf', threshold=60)
    pairs = {(s['target_id'], s['ref_id']) for s in out}
    # Kolo 2 -> Kolo within Alpha (digits stripped). Zed already fuzzy-matched
    # Zedd in the lookups pass. Tavi cannot match Tavy (Gama != Gamma yet).
    assert ('T2', 'R2') in pairs
    assert not any(t in ('T3', 'T5') for t, _ in pairs)
    # Kola (T1) exact-matched R1, so R9 (Kola, Ouest) is not proposed for it
    assert not any(r == 'R1' for _, r in pairs)


def test_candidates_leaf_restricted_and_unrestricted(project_with_lookups):
    cfg = project_with_lookups
    restricted = sg.candidates(cfg, 'leaf', 'T5', restrict=True, top=3)
    assert restricted == []  # parent group (sud, gama) has no reference_only rows
    open_ = sg.candidates(cfg, 'leaf', 'T5', restrict=False, top=3)
    assert open_ and open_[0]['ref_id'] == 'R6' and open_[0]['ref_name'] == 'tavy'
    assert open_[0]['score'] >= 70
    assert open_[0]['ref_parents'] == {'region': 'sud', 'district': 'gamma'}


def test_candidates_hierarchy(project_with_lookups):
    cfg = project_with_lookups
    cands = sg.candidates(cfg, 'region', 'centr', restrict=True, top=5)
    names = [c['ref_name'] for c in cands]
    assert names[0] == 'centre'
    assert 'ouest' in names  # every reference_only region is a candidate at the top level
    assert cands[0]['ref_key'] == 'centre'


def test_best_scores_for_histogram(project_with_lookups):
    cfg = project_with_lookups
    scores = sg.best_scores(cfg, 'leaf', restrict=True)
    assert set(scores) == {'T2', 'T5', 'T6', 'T7'}
    assert scores['T2'] == 100         # Kolo 2 vs Kolo
    assert scores['T5'] == 0           # no candidate in its parent group


def test_cli_wrapper_still_prints(project_with_lookups, capsys):
    from match_bot.scripts.suggest_matches import run
    run(project_with_lookups, level='region', threshold=70)
    out = capsys.readouterr().out
    assert "'Centr'" in out and "'centre'" in out and 'Found 1 potential region matches' in out
