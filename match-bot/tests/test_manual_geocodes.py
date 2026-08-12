"""Unit tests for the manual-geocode merge (_merge_manual_geocodes)."""
import pandas as pd

from match_bot.gui.app import _merge_manual_geocodes, MANUAL_GEOCODE_COLUMNS

MATCH_COLS = ['_target_name', '_target_name_raw', '_target_id',
              '_ref_name', '_ref_name_raw', '_ref_id',
              '_match_type', '_levenshtein_distance', '_mapping_rationale']
UNREF_COLS = ['_ref_id', '_ref_name', '_ref_name_raw']
UNTGT_COLS = ['_target_id', '_target_name', '_target_name_raw']


def mk(rows, cols):
    return pd.DataFrame(rows, columns=cols).fillna('').astype(str)


def run(manual_rows, matched, un_ref, un_tgt, ref_ids, tgt_ids):
    manual = pd.DataFrame(manual_rows, columns=MANUAL_GEOCODE_COLUMNS).astype(str)
    return _merge_manual_geocodes(manual, matched, un_ref, un_tgt, ref_ids, tgt_ids)


def test_point_match_applied():
    matched = mk([['pn1', 'PN1', 'T1', 'rn1', 'RN1', 'R1', 'exact', '', '']], MATCH_COLS)
    un_ref = mk([['R2', 'rn2', 'RN2']], UNREF_COLS)
    un_tgt = mk([['T2', 'pn2', 'PN2']], UNTGT_COLS)
    m, ur, ut, mo, logs = run([['R2', 'T2', '10.0', '20.0']],
                              matched, un_ref, un_tgt, {'R1', 'R2'}, {'T1', 'T2'})
    assert len(m) == 2 and len(ur) == 0 and len(ut) == 0
    row = m[m['_ref_id'] == 'R2'].iloc[0]
    assert row['_target_id'] == 'T2' and row['_match_type'] == 'manual'
    assert row['_ref_name_raw'] == 'RN2' and row['_target_name_raw'] == 'PN2'
    assert len(mo) == 1


def test_coordinate_match_applied():
    matched = mk([], MATCH_COLS)
    un_ref = mk([['R2', 'rn2', 'RN2']], UNREF_COLS)
    un_tgt = mk([['T2', 'pn2', 'PN2']], UNTGT_COLS)
    m, ur, ut, mo, logs = run([['R2', '', '10.0', '20.0']],
                              matched, un_ref, un_tgt, {'R2'}, {'T2'})
    assert len(m) == 1 and len(ur) == 0 and len(ut) == 1
    row = m.iloc[0]
    assert row['_ref_id'] == 'R2' and row['_target_id'] == '' and row['_match_type'] == 'manual'


def test_manual_wins_over_fuzzy_same_ref():
    matched = mk([['pn1', 'PN1', 'T1', 'rn1', 'RN1', 'R1', 'fuzzy_dist', '1', '']], MATCH_COLS)
    un_ref = mk([], UNREF_COLS)
    un_tgt = mk([['T2', 'pn2', 'PN2']], UNTGT_COLS)
    m, ur, ut, mo, logs = run([['R1', 'T2', '10.0', '20.0']],
                              matched, un_ref, un_tgt, {'R1'}, {'T1', 'T2'})
    assert len(m) == 1
    row = m.iloc[0]
    assert row['_ref_id'] == 'R1' and row['_target_id'] == 'T2' and row['_match_type'] == 'manual'
    assert list(ut['_target_id']) == ['T1']
    assert len(ur) == 0


def test_manual_wins_over_fuzzy_same_point():
    matched = mk([['pn1', 'PN1', 'T1', 'rn2', 'RN2', 'R2', 'fuzzy_score', '', '']], MATCH_COLS)
    un_ref = mk([['R1', 'rn1', 'RN1']], UNREF_COLS)
    un_tgt = mk([], UNTGT_COLS)
    m, ur, ut, mo, logs = run([['R1', 'T1', '10.0', '20.0']],
                              matched, un_ref, un_tgt, {'R1', 'R2'}, {'T1'})
    assert len(m) == 1
    row = m.iloc[0]
    assert row['_ref_id'] == 'R1' and row['_target_id'] == 'T1'
    assert list(ur['_ref_id']) == ['R2']
    assert len(ut) == 0


def test_fuzzy_rematch_same_pair_idempotent():
    matched = mk([['pn2', 'PN2', 'T2', 'rn2', 'RN2', 'R2', 'exact', '', '']], MATCH_COLS)
    un_ref = mk([], UNREF_COLS)
    un_tgt = mk([], UNTGT_COLS)
    m, ur, ut, mo, logs = run([['R2', 'T2', '10.0', '20.0']],
                              matched, un_ref, un_tgt, {'R2'}, {'T2'})
    assert len(m) == 1 and len(ur) == 0 and len(ut) == 0
    assert m.iloc[0]['_match_type'] == 'manual'


def test_vanished_ref_dropped():
    m, ur, ut, mo, logs = run([['GONE', '', '1', '2']],
                              mk([], MATCH_COLS), mk([], UNREF_COLS),
                              mk([], UNTGT_COLS), {'R1'}, set())
    assert len(mo) == 0 and any('dropped' in l for l in logs)


def test_vanished_point_degrades_to_coords():
    matched = mk([], MATCH_COLS)
    un_ref = mk([['R1', 'rn1', 'RN1']], UNREF_COLS)
    un_tgt = mk([], UNTGT_COLS)
    m, ur, ut, mo, logs = run([['R1', 'TGONE', '10.0', '20.0']],
                              matched, un_ref, un_tgt, {'R1'}, {'T9'})
    assert len(m) == 1
    assert m.iloc[0]['_target_id'] == ''
    assert mo.iloc[0]['target_id'] == ''
    assert any('coordinate-only' in l for l in logs)


def test_coordinate_match_survives_rerun():
    matched = mk([['pn1', 'PN1', 'T1', 'rn1', 'RN1', 'R1', 'exact', '', '']], MATCH_COLS)
    un_ref = mk([['R2', 'rn2', 'RN2'], ['R3', 'rn3', 'RN3']], UNREF_COLS)
    un_tgt = mk([['T2', 'pn2', 'PN2']], UNTGT_COLS)
    m, ur, ut, mo, logs = run([['R2', '', '10.0', '20.0']],
                              matched, un_ref, un_tgt, {'R1', 'R2', 'R3'}, {'T1', 'T2'})
    assert len(m) == 2
    assert list(ur['_ref_id']) == ['R3']
    assert list(ut['_target_id']) == ['T2']
