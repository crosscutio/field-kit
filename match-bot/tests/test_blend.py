"""Unit tests for gazetteer blending and user-point normalization."""
import pandas as pd

from match_bot import gazetteer as gz


def _canon(rows):
    df = pd.DataFrame(rows, columns=['point_id', 'name', 'latitude',
                                     'longitude', 'source'])
    for col in gz.CANONICAL_COLUMNS:
        if col not in df.columns:
            df[col] = ''
    return df[gz.CANONICAL_COLUMNS].astype(str)


def test_user_beats_gazetteer_on_same_key():
    user = _canon([['user:A', 'Mbabane', '-26.3100', '31.1300', 'user']])
    gaz = _canon([
        ['gn:1', 'MBABANE', '-26.3101', '31.1302', 'geonames'],  # same cell
        ['osm:2', 'Siteki', '-26.45', '31.95', 'osm'],
    ])
    combined, stats = gz.blend_points(user, gaz)
    assert list(combined['point_id']) == ['user:A', 'osm:2']
    assert stats == {'user': 1, 'geonames': 0, 'osm': 1,
                     'dropped_duplicates': 1, 'dropped_invalid': 0}


def test_geonames_beats_osm():
    gaz = _canon([
        ['osm:2', 'Mbabane', '-26.310', '31.130', 'osm'],
        ['gn:1', 'Mbabane', '-26.310', '31.130', 'geonames'],
    ])
    combined, stats = gz.blend_points(None, gaz)
    assert list(combined['point_id']) == ['gn:1']
    assert stats['dropped_duplicates'] == 1


def test_invalid_coords_user_kept_gazetteer_dropped():
    user = _canon([['user:A', 'No Coords Facility', '', '', 'user']])
    gaz = _canon([
        ['gn:1', 'Broken Point', 'abc', '31.1', 'geonames'],
        ['gn:2', 'Out Of Range', '95.0', '31.1', 'geonames'],
        ['gn:3', 'Fine', '-26.4', '31.2', 'geonames'],
    ])
    combined, stats = gz.blend_points(user, gaz)
    assert set(combined['point_id']) == {'user:A', 'gn:3'}
    assert stats['dropped_invalid'] == 2


def test_empty_inputs():
    combined, stats = gz.blend_points(None, None)
    assert combined.empty
    assert stats['user'] == 0 and stats['dropped_duplicates'] == 0


def test_normalize_user_points_maps_hierarchy():
    df = pd.DataFrame({
        'fid': ['R1', 'R2'],
        'fname': ['Clinic A', 'Clinic B'],
        'y': ['-26.1', '-26.2'],
        'x': ['31.1', '31.2'],
        'prov': ['Hhohho', 'Manzini'],
    })
    out = gz.normalize_user_points(df, 'fid', 'fname', 'y', 'x',
                                   hier_cols=[('prov', 'province')])
    assert list(out['point_id']) == ['user:R1', 'user:R2']
    assert list(out['orig_id']) == ['R1', 'R2']
    assert list(out['admin1']) == ['Hhohho', 'Manzini']
    assert list(out['source'].unique()) == ['user']
    assert list(out.columns) == gz.CANONICAL_COLUMNS
