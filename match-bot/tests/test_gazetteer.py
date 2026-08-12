"""Unit tests for match_bot.gazetteer (no network)."""
import json

import pandas as pd
import pytest

from match_bot import gazetteer as gz


# ---------------------------------------------------------------------------
# parse_geonames
# ---------------------------------------------------------------------------

GEONAMES_LINES = [
    # geonameid name asciiname alternates lat lon fclass fcode cc cc2 a1 a2 a3 a4 pop ...
    '100\tMbabane\tMbabane\t\t-26.31\t31.13\tP\tPPLC\tSZ\t\tHH\t\t\t\t95000\t\t\t\tAfrica/Mbabane\t2020-01-01',
    '101\tLobamba Hill\tLobamba Hill\t\t-26.44\t31.20\tT\tHLL\tSZ\t\tHH\t\t\t\t0\t\t\t\tAfrica/Mbabane\t2020-01-01',
    '102\tManzini\tManzini\t\t-26.49\t31.38\tP\tPPL\tSZ\t\tMA\t\t\t\t110000\t\t\t\tAfrica/Mbabane\t2020-01-01',
    '103\tSome Stream\tSome Stream\t\t-26.5\t31.4\tH\tSTM\tSZ\t\tMA\t\t\t\t0\t\t\t\tAfrica/Mbabane\t2020-01-01',
    'short\tline',
]


def test_parse_geonames_keeps_only_populated_places(tmp_path):
    txt = tmp_path / 'SZ.txt'
    txt.write_text('\n'.join(GEONAMES_LINES), encoding='utf-8')
    df = gz.parse_geonames(txt, 'SWZ')
    assert list(df['name']) == ['Mbabane', 'Manzini']
    assert list(df['point_id']) == ['gn:100', 'gn:102']
    assert set(df['source']) == {'geonames'}
    assert set(df['iso3']) == {'SWZ'}
    assert list(df.columns) == gz.CANONICAL_COLUMNS
    assert (df['admin1'] == '').all()  # assigned later by polygons


# ---------------------------------------------------------------------------
# parse_overpass
# ---------------------------------------------------------------------------

def test_parse_overpass_honors_whitelist(tmp_path):
    payload = {'elements': [
        {'type': 'node', 'id': 1, 'lat': -26.3, 'lon': 31.1,
         'tags': {'place': 'village', 'name': 'Ekukhanyeni'}},
        {'type': 'node', 'id': 2, 'lat': -26.4, 'lon': 31.2,
         'tags': {'place': 'farm', 'name': 'Not A Place Type'}},
        {'type': 'node', 'id': 3, 'lat': -26.5, 'lon': 31.3,
         'tags': {'place': 'town'}},  # no name
        {'type': 'node', 'id': 4, 'lat': -26.6, 'lon': 31.4,
         'tags': {'place': 'town', 'name': 'Siteki', 'population': '6152'}},
    ]}
    p = tmp_path / 'resp.json'
    p.write_text(json.dumps(payload), encoding='utf-8')
    df = gz.parse_overpass(p, 'SWZ')
    assert list(df['name']) == ['Ekukhanyeni', 'Siteki']
    assert list(df['point_id']) == ['osm:1', 'osm:4']
    assert df.loc[df['name'] == 'Siteki', 'population'].iloc[0] == '6152'


# ---------------------------------------------------------------------------
# clip_to_admin
# ---------------------------------------------------------------------------

def _squares():
    from shapely.geometry import box
    return {'West': box(0, 0, 1, 1), 'East': box(1, 0, 2, 1)}


def _pts(rows):
    df = pd.DataFrame(rows, columns=['point_id', 'name', 'latitude', 'longitude'])
    for col in gz.CANONICAL_COLUMNS:
        if col not in df.columns:
            df[col] = ''
    return df[gz.CANONICAL_COLUMNS].astype(str)


def test_clip_to_admin_assigns_and_drops():
    df = _pts([
        ['gn:1', 'InWest', '0.5', '0.5'],
        ['gn:2', 'InEast', '0.5', '1.5'],
        ['gn:3', 'Outside', '5.0', '5.0'],
        ['gn:4', 'BadCoords', '', ''],
    ])
    out = gz.clip_to_admin(df, _squares())
    assert list(out['name']) == ['InWest', 'InEast']
    assert list(out['admin1']) == ['West', 'East']


# ---------------------------------------------------------------------------
# merge_sources
# ---------------------------------------------------------------------------

def test_merge_sources_geonames_wins_on_duplicate():
    gn = _pts([['gn:1', 'Mbabane', '-26.3100', '31.1300']])
    gn['source'] = 'geonames'
    gn['iso3'] = 'SWZ'
    osm = _pts([
        ['osm:9', 'MBABANE', '-26.3102', '31.1301'],   # same rounded cell
        ['osm:8', 'Siteki', '-26.4500', '31.9500'],
    ])
    osm['source'] = 'osm'
    osm['iso3'] = 'SWZ'
    out = gz.merge_sources(gn, osm)
    assert list(out['point_id']) == ['gn:1', 'osm:8']


def test_merge_sources_near_miss_survives():
    gn = _pts([['gn:1', 'Mbabane', '-26.310', '31.130']])
    gn['source'] = 'geonames'
    gn['iso3'] = 'SWZ'
    osm = _pts([['osm:9', 'Mbabane', '-26.312', '31.130']])  # 0.002 deg away
    osm['source'] = 'osm'
    osm['iso3'] = 'SWZ'
    out = gz.merge_sources(gn, osm)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Overpass caching / retry
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_overpass_uses_cache(tmp_path, monkeypatch):
    from shapely.geometry import box
    monkeypatch.setenv('MATCH_BOT_GAZETTEER_DIR', str(tmp_path))
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        return _FakeResponse(json.dumps({'elements': []}).encode())

    monkeypatch.setattr(gz.urllib.request, 'urlopen', fake_urlopen)
    geom = box(31, -27, 32, -26)
    p1 = gz.fetch_overpass_admin('SWZ', 'Hhohho', geom)
    p2 = gz.fetch_overpass_admin('SWZ', 'Hhohho', geom)
    assert p1 == p2
    assert len(calls) == 1  # second call served from cache


def test_fetch_overpass_retries_on_429(tmp_path, monkeypatch):
    import urllib.error
    from shapely.geometry import box
    monkeypatch.setenv('MATCH_BOT_GAZETTEER_DIR', str(tmp_path))
    monkeypatch.setattr(gz.time, 'sleep', lambda s: None)
    attempts = []

    def flaky_urlopen(req, timeout=0):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(req.full_url, 429, 'Too Many', {}, None)
        return _FakeResponse(json.dumps({'elements': []}).encode())

    monkeypatch.setattr(gz.urllib.request, 'urlopen', flaky_urlopen)
    geom = box(31, -27, 32, -26)
    path = gz.fetch_overpass_admin('SWZ', 'Lubombo', geom)
    assert len(attempts) == 3
    assert path.exists()


def test_fetch_overpass_gives_up_after_retries(tmp_path, monkeypatch):
    import urllib.error
    from shapely.geometry import box
    monkeypatch.setenv('MATCH_BOT_GAZETTEER_DIR', str(tmp_path))
    monkeypatch.setattr(gz.time, 'sleep', lambda s: None)

    def always_429(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 429, 'Too Many', {}, None)

    monkeypatch.setattr(gz.urllib.request, 'urlopen', always_429)
    geom = box(31, -27, 32, -26)
    with pytest.raises(gz.GazetteerError, match='Overpass unavailable'):
        gz.fetch_overpass_admin('SWZ', 'Manzini', geom)


# ---------------------------------------------------------------------------
# Country list
# ---------------------------------------------------------------------------

def test_list_countries_bundled():
    countries = gz.list_countries()
    assert len(countries) > 200
    by_iso3 = {c['iso3']: c for c in countries}
    assert by_iso3['SWZ']['iso2'] == 'SZ'
    assert gz.iso2_for('NGA') == 'NG'
    with pytest.raises(gz.GazetteerError):
        gz.iso2_for('XXX')
