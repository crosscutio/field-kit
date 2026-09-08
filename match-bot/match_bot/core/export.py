"""Geocoded output: the target (community) file plus linked coordinates."""

from typing import Optional

import pandas as pd

from .data_loader import load_dataset
from .links import _norm_id, _s, is_auto, MANUAL_PREFIX
from .lookup import load_lookup
from .suggest import score_from_rationale, score_pair

EXPORT_COLUMNS = ['linked_place_id', 'linked_place_name', 'latitude', 'longitude',
                  'score', 'set_by', 'match_type']


def _set_by(match_type, rationale) -> str:
    if match_type in ('exact', 'fuzzy_dist', 'fuzzy_score'):
        return 'auto'
    if match_type == 'manual':
        r = _s(rationale).strip().lower()
        if r.startswith(MANUAL_PREFIX):
            return 'manual'
        return 'auto' if is_auto(r) or not r else 'manual'
    if match_type == 'no_equivalent':
        return 'no equivalent'
    return 'pending'


def geocode_rows(config, pins: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Original target rows with EXPORT_COLUMNS appended. Pins win over lookups."""
    tcfg, rcfg = config.target, config.reference
    target = load_dataset(str(config.resolve_file(tcfg.file)), tcfg.format, tcfg.layer)
    ref = load_dataset(str(config.resolve_file(rcfg.file)), rcfg.format, rcfg.layer)
    lat_col, lon_col = rcfg.latitude_column, rcfg.longitude_column

    places = {}
    for _, r in ref.iterrows():
        places[_norm_id(r[rcfg.id_column])] = (
            _s(r[rcfg.name_column]),
            _s(r[lat_col]) if lat_col in ref.columns else '',
            _s(r[lon_col]) if lon_col in ref.columns else '',
        )

    leaf = load_lookup(str(config.lookups_dir / 'leaf_lookup.csv'))
    linked = {}
    if not leaf.empty:
        for _, row in leaf[leaf['match_type'] != 'reference_only'].iterrows():
            linked[_norm_id(row.get('target_id'))] = row

    pin_map = {}
    if pins is not None and not pins.empty:
        for _, p in pins.iterrows():
            pin_map[_norm_id(p['target_id'])] = (_s(p['latitude']), _s(p['longitude']))

    extra = []
    for _, t in target.iterrows():
        tid = _norm_id(t[tcfg.id_column])
        if tid in pin_map:
            lat, lon = pin_map[tid]
            extra.append(['', 'dropped pin', lat, lon, '', 'manual · pin', 'pin'])
            continue
        row = linked.get(tid)
        mt = _s(row.get('match_type')) if row is not None else ''
        rid = _norm_id(row.get('ref_id')) if row is not None else ''
        if row is not None and rid and mt in ('exact', 'fuzzy_dist', 'fuzzy_score', 'manual'):
            name, lat, lon = places.get(rid, (_s(row.get('ref_name')), '', ''))
            score = score_from_rationale(row.get('mapping_rationale'))
            if score is None:
                score = 100 if mt == 'exact' else int(round(score_pair(
                    row.get('target_name_standardized'), row.get('ref_name'))))
            extra.append([rid, name, lat, lon, score, _set_by(mt, row.get('mapping_rationale')), mt])
        else:
            extra.append(['', '', '', '', '', _set_by(mt, None), mt or 'no_candidate'])
    out = target.copy()
    for i, col in enumerate(EXPORT_COLUMNS):
        out[col] = [e[i] for e in extra]
    return out


def write_geocoded(config, pins: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    df = geocode_rows(config, pins)
    out = config.output_dir / 'geocoded.csv'
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return df
