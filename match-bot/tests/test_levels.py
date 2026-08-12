"""Tests for the per-level match state layer behind the Geocoding ladder."""
import pandas as pd
import pytest

from match_bot.gui.levels import LevelState, _score

FORM = {
    'ref_id_column': 'fid', 'ref_name_column': 'fname',
    'target_id_column': 'point_id', 'target_name_column': 'name',
    'geo_lat_column': 'latitude', 'geo_lon_column': 'longitude',
    'case': 'lower', 'remove_accents': True,
    'ref_hierarchy': [{'column': 'region', 'label': 'region'}],
    'target_hierarchy': [{'column': 'admin1', 'label': 'region'}],
}


@pytest.fixture
def session_dir(tmp_path):
    (tmp_path / 'output' / 'lookups').mkdir(parents=True)
    pd.DataFrame({
        'fid': ['f1', 'f2', 'f3'],
        'fname': ['Mbabane Central', 'Ekufikeni Outreach', 'Nkhaba Clinic'],
        'region': ['Hhohho', 'Manzini', 'Shiselweni'],
    }).to_csv(tmp_path / 'ref.csv', index=False)
    pd.DataFrame({
        'point_id': ['p1', 'p3', 'p4'],
        'name': ['Mbabane Central', 'Ekukhulumeni', 'Nkaba'],
        'admin1': ['Hhohho', 'Manzini Region', 'Shiselweni District'],
        'latitude': ['-26.3', '-26.5', '-26.9'],
        'longitude': ['31.1', '31.3', '31.4'],
        'source': ['geonames', 'geonames', 'geonames'],
    }).to_csv(tmp_path / 'target.csv', index=False)
    return tmp_path


def write_leaf_outputs(sd):
    """Simulate a pipeline run: one exact match, two unmatched refs."""
    out = sd / 'output'
    pd.DataFrame([{
        '_ref_region': 'hhohho', '_target_region': 'hhohho',
        '_ref_name': 'mbabane central', '_ref_name_raw': 'Mbabane Central',
        '_ref_id': 'f1', '_target_name': 'mbabane central',
        '_target_name_raw': 'Mbabane Central', '_target_id': 'p1',
        '_match_type': 'exact', '_levenshtein_distance': 0,
        '_mapping_rationale': '',
    }]).to_csv(out / 'matched.csv', index=False)
    pd.DataFrame([
        {'_ref_region': 'manzini', '_ref_name': 'ekufikeni outreach',
         '_ref_name_raw': 'Ekufikeni Outreach', '_ref_id': 'f2'},
        {'_ref_region': 'shiselweni', '_ref_name': 'nkhaba clinic',
         '_ref_name_raw': 'Nkhaba Clinic', '_ref_id': 'f3'},
    ]).to_csv(out / 'unmatched_ref.csv', index=False)
    pd.DataFrame([
        {'_target_region': 'manzini region', '_target_name': 'ekukhulumeni',
         '_target_name_raw': 'Ekukhulumeni', '_target_id': 'p3'},
        {'_target_region': 'shiselweni district', '_target_name': 'nkaba',
         '_target_name_raw': 'Nkaba', '_target_id': 'p4'},
    ]).to_csv(out / 'unmatched_target.csv', index=False)


def test_score_strips_standalone_digits():
    # Shared trailing numbers must not manufacture a match
    assert _score('Ward 2', 'Zone 2') == 0
    assert _score('Manzini', 'Manzini Region') == 100


def test_hier_rows_exact_and_guess(session_dir):
    state = LevelState(session_dir, FORM)
    rows = {r['name']: r for r in state.hier_rows(0)}
    assert rows['Hhohho']['method'] == 'exact'
    assert rows['Manzini']['method'] is None
    assert rows['Manzini']['guess'] == 'Manzini Region'


def test_rerun_hier_links_above_threshold_only(session_dir):
    state = LevelState(session_dir, FORM)
    result = state.rerun_hier('region', 70)
    assert result == {'linked': 2, 'open': 0}
    rows = {r['name']: r for r in LevelState(session_dir, FORM).hier_rows(0)}
    assert rows['Manzini']['method'] == 'fuzzy'
    assert rows['Manzini']['target'] == 'Manzini Region'


def test_rerun_hier_keeps_manual_links(session_dir):
    state = LevelState(session_dir, FORM)
    state.link_hier('region', 'manzini', 'shiselweni district',
                    target_raw='Shiselweni District')  # deliberate odd link
    state.rerun_hier('region', 0)
    rows = {r['name']: r for r in LevelState(session_dir, FORM).hier_rows(0)}
    assert rows['Manzini']['method'] == 'manual'
    assert rows['Manzini']['target'] == 'Shiselweni District'


def test_leaf_rows_blocked_by_unlinked_parent(session_dir):
    write_leaf_outputs(session_dir)
    state = LevelState(session_dir, FORM)
    state.link_hier('region', 'manzini', 'manzini region')
    rows = {r['ref_id']: r for r in LevelState(session_dir, FORM).leaf_rows()}
    assert rows['f1']['method'] == 'exact'
    assert rows['f2']['blocked'] is False
    assert rows['f2']['guess'] == 'Ekukhulumeni'
    assert rows['f3']['blocked'] is True
    assert rows['f3']['blocked_on'] == 'region'


def test_leaf_candidates_carry_coordinates(session_dir):
    write_leaf_outputs(session_dir)
    state = LevelState(session_dir, FORM)
    state.link_hier('region', 'manzini', 'manzini region')
    cands = LevelState(session_dir, FORM).leaf_candidates('f2')
    assert cands and cands[0]['target_id'] == 'p3'
    assert cands[0]['latitude'] == '-26.5'


def test_unlink_parent_drops_child_links(session_dir):
    write_leaf_outputs(session_dir)
    state = LevelState(session_dir, FORM)
    state.link_hier('region', 'manzini', 'manzini region')
    assert LevelState(session_dir, FORM).rerun_leaf(40)['linked'] == 1

    dropped = LevelState(session_dir, FORM).unlink_hier('region', 'manzini')
    assert dropped == 1
    rows = {r['ref_id']: r for r in LevelState(session_dir, FORM).leaf_rows()}
    assert rows['f2']['method'] is None and rows['f2']['blocked'] is True
    # the dropped target returns to the pool
    un_tgt = pd.read_csv(session_dir / 'output' / 'unmatched_target.csv', dtype=str)
    assert (un_tgt['_target_id'] == 'p3').any()


def test_relink_parent_drops_child_links(session_dir):
    write_leaf_outputs(session_dir)
    state = LevelState(session_dir, FORM)
    state.link_hier('region', 'manzini', 'manzini region')
    LevelState(session_dir, FORM).rerun_leaf(40)
    # re-pointing the parent at a different gazetteer name drops the child
    dropped = LevelState(session_dir, FORM).link_hier(
        'region', 'manzini', 'shiselweni district')
    assert dropped == 1


def test_rerun_leaf_respects_threshold(session_dir):
    write_leaf_outputs(session_dir)
    state = LevelState(session_dir, FORM)
    state.link_hier('region', 'manzini', 'manzini region')
    # best candidate for f2 scores 47 — below 60, so nothing links
    assert LevelState(session_dir, FORM).rerun_leaf(60)['linked'] == 0
