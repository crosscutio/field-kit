import pandas as pd

from match_bot.core import links, suggest as sg
from match_bot.core.lookup import load_lookup
from tests.conftest import run_lookups


def leaf(cfg):
    return pd.read_csv(cfg.lookups_dir / 'leaf_lookup.csv', dtype=str).fillna('')


def hier(cfg, label):
    return pd.read_csv(cfg.lookups_dir / f'{label}_lookup.csv', dtype=str).fillna('')


def test_link_leaf_is_one_to_one_and_unlink_restores(project_with_lookups):
    cfg = project_with_lookups
    res = links.link(cfg, 'leaf', 'T5', 'R6', rationale='manual: picked score=75')
    assert res['ok']
    df = leaf(cfg)
    row = df[df['target_id'] == 'T5'].iloc[0]
    assert row['match_type'] == 'manual' and row['ref_id'] == 'R6' and row['ref_name'] == 'tavy'
    assert row['ref_district'] == 'gamma' and row['unmatched'] == ''
    assert 'R6' not in set(df[df['match_type'] == 'reference_only']['ref_id'].astype(str))
    assert row['levenshtein_distance'] != ''

    # 1-to-1: R6 is consumed
    res2 = links.link(cfg, 'leaf', 'T7', 'R6')
    assert not res2['ok']

    res3 = links.unlink(cfg, 'leaf', 'T5')
    assert res3['ok'] and res3['ref_id'] == 'R6'
    df = leaf(cfg)
    row = df[df['target_id'] == 'T5'].iloc[0]
    assert row['match_type'] == 'no_candidate' and row['ref_id'] == '' and row['unmatched'] == 'x'
    restored = df[(df['match_type'] == 'reference_only') & (df['ref_id'].astype(str) == 'R6')]
    assert len(restored) == 1 and restored.iloc[0]['ref_district'] == 'gamma'


def test_unlink_auto_match_and_survive_regeneration(project_with_lookups):
    cfg = project_with_lookups
    assert links.unlink(cfg, 'leaf', 'T1')['ok']    # exact match Kola->R1
    df = leaf(cfg)
    assert df[df['target_id'] == 'T1'].iloc[0]['match_type'] == 'no_candidate'
    # Regenerating re-matches it (nothing pins the unlink); that is expected.
    run_lookups(cfg)
    assert leaf(cfg)[lambda d: d['target_id'] == 'T1'].iloc[0]['match_type'] == 'exact'


def test_manual_link_survives_regeneration(project_with_lookups):
    cfg = project_with_lookups
    links.link(cfg, 'leaf', 'T5', 'R6')
    run_lookups(cfg)
    row = leaf(cfg)[lambda d: d['target_id'] == 'T5'].iloc[0]
    assert row['match_type'] == 'manual' and row['ref_id'] == 'R6'


def test_no_equivalent_persists(project_with_lookups):
    cfg = project_with_lookups
    assert links.set_no_equivalent(cfg, 'leaf', 'T7')['ok']
    row = leaf(cfg)[lambda d: d['target_id'] == 'T7'].iloc[0]
    assert row['match_type'] == 'no_equivalent' and row['unmatched'] == 'x'
    run_lookups(cfg)
    row = leaf(cfg)[lambda d: d['target_id'] == 'T7'].iloc[0]
    assert row['match_type'] == 'no_equivalent'
    assert 'T7' not in {s['target_id'] for s in sg.suggest(cfg, 'leaf', threshold=0)}
    assert links.clear_no_equivalent(cfg, 'leaf', 'T7')['ok']
    assert leaf(cfg)[lambda d: d['target_id'] == 'T7'].iloc[0]['match_type'] == 'no_candidate'


def test_hierarchy_link_unlink_no_equivalent(project_with_lookups):
    cfg = project_with_lookups
    assert links.link(cfg, 'region', 'centr', 'centre')['ok']
    df = hier(cfg, 'region')
    row = df[df['target_name_standardized'] == 'centr'].iloc[0]
    assert row['match_type'] == 'manual' and row['reference_name'] == 'centre'
    assert 'centre' not in set(df[df['match_type'] == 'reference_only']['reference_name'])
    assert not links.link(cfg, 'region', 'nord', 'centre')['ok']

    assert links.unlink(cfg, 'region', 'centr')['ok']
    df = hier(cfg, 'region')
    assert df[df['target_name_standardized'] == 'centr'].iloc[0]['match_type'] == 'no_candidate'
    assert 'centre' in set(df[df['match_type'] == 'reference_only']['reference_name'])

    assert links.set_no_equivalent(cfg, 'region', 'centr')['ok']
    run_lookups(cfg)
    df = hier(cfg, 'region')
    assert df[df['target_name_standardized'] == 'centr'].iloc[0]['match_type'] == 'no_equivalent'
    assert 'centr' not in {s['target_key'] for s in sg.suggest(cfg, 'region', threshold=0)}


def test_revert_auto_suggestions(project_with_lookups):
    cfg = project_with_lookups
    applied = sg.apply(cfg, 'leaf', sg.suggest(cfg, 'leaf', threshold=60))
    assert applied['applied'] >= 1
    links.link(cfg, 'leaf', 'T5', 'R6', rationale='manual: picked')
    reverted = links.revert_auto(cfg, 'leaf')
    assert 'T2' in reverted and 'T5' not in reverted
    df = leaf(cfg)
    assert df[df['target_id'] == 'T2'].iloc[0]['match_type'] == 'no_candidate'
    assert df[df['target_id'] == 'T5'].iloc[0]['match_type'] == 'manual'
    assert 'R2' in set(df[df['match_type'] == 'reference_only']['ref_id'].astype(str))
