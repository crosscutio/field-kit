"""Flask route tests for the gazetteer endpoints (no network — seeded cache)."""
import io
import json

import pandas as pd
import pytest

from match_bot import gazetteer as gz
from match_bot.gui.app import create_app

ISO3, ISO2 = 'TST', 'TS'

BOUNDARIES = {
    'type': 'FeatureCollection',
    'features': [
        {'type': 'Feature', 'properties': {'shapeName': 'West'},
         'geometry': {'type': 'Polygon',
                      'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}},
        {'type': 'Feature', 'properties': {'shapeName': 'East'},
         'geometry': {'type': 'Polygon',
                      'coordinates': [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]}},
    ],
}

GEONAMES_TXT = '\n'.join([
    # id name ascii alt lat lon fclass fcode cc cc2 a1 a2 a3 a4 pop (cols 0-14)
    '1\tAlpha\tAlpha\t\t0.5\t0.5\tP\tPPL\tTS\t\t\t\t\t\t100\t\t\t\tUTC\t2020',
    '2\tBeta\tBeta\t\t0.5\t1.5\tP\tPPL\tTS\t\t\t\t\t\t200\t\t\t\tUTC\t2020',
    '3\tOutside\tOutside\t\t5.0\t5.0\tP\tPPL\tTS\t\t\t\t\t\t0\t\t\t\tUTC\t2020',
])

OVERPASS_WEST = {'elements': [
    {'type': 'node', 'id': 11, 'lat': 0.4, 'lon': 0.4,
     'tags': {'place': 'village', 'name': 'Gamma'}},
]}
OVERPASS_EAST = {'elements': [
    {'type': 'node', 'id': 12, 'lat': 0.4, 'lon': 1.4,
     'tags': {'place': 'town', 'name': 'Delta'}},
]}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """App test client + gazetteer dir seeded so no network is needed."""
    gaz_dir = tmp_path / 'gazetteer'
    monkeypatch.setenv('MATCH_BOT_GAZETTEER_DIR', str(gaz_dir))

    bdir = gaz_dir / 'data' / 'boundaries'
    bdir.mkdir(parents=True)
    (bdir / f'{ISO3}_ADM1.geojson').write_text(json.dumps(BOUNDARIES))

    gdir = gaz_dir / 'data' / 'geonames'
    gdir.mkdir(parents=True)
    (gdir / f'{ISO2}.txt').write_text(GEONAMES_TXT)

    # Bundled country list has no TST — patch the module lookup
    monkeypatch.setattr(gz, 'iso2_for', lambda iso3: ISO2)

    shapes = gz.admin1_shapes(ISO3, None)
    for name, payload in (('West', OVERPASS_WEST), ('East', OVERPASS_EAST)):
        cache = gz.overpass_cache_path(ISO3, name, shapes[name])
        cache.write_text(json.dumps(payload))

    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()
    # Route session uploads into tmp too
    app.config['UPLOAD_BASE'] = tmp_path / 'uploads'
    return client


def _form(**over):
    base = {
        'mode': 'geo',
        'ref_id_column': 'rid', 'ref_name_column': 'rname',
        'target_id_column': '', 'target_name_column': '',
        'geo_lat_column': '', 'geo_lon_column': '',
        'ref_hierarchy': [], 'target_hierarchy': [],
    }
    base.update(over)
    return base


def test_countries_endpoint(seeded):
    r = seeded.get('/api/geo/gazetteer/countries')
    data = r.get_json()
    assert r.status_code == 200 and len(data['countries']) > 200


def test_admin1_from_seeded_cache(seeded):
    r = seeded.post('/api/geo/gazetteer/admin1', json={'iso3': ISO3})
    data = r.get_json()
    assert r.status_code == 200
    assert data['admin1'] == ['East', 'West'] and data['cached'] is True


def test_preview_counts_selection(seeded):
    r = seeded.post('/api/geo/gazetteer/preview',
                    json={'iso3': ISO3, 'admin1': ['West']})
    data = r.get_json()
    assert data['geonames_count'] == 1 and data['warning'] is None


def test_build_produces_combined_target(seeded):
    r = seeded.post('/api/geo/gazetteer/build',
                    json=_form(iso3=ISO3, admin1=['West', 'East']))
    data = r.get_json()
    assert r.status_code == 200, data
    assert data['counts'] == {'user': 0, 'geonames': 2, 'osm': 2,
                              'dropped_duplicates': 0, 'dropped_invalid': 0,
                              'total': 4}
    assert data['preset']['target_id_column'] == 'point_id'
    assert data['preset']['suggested_hierarchy'] == [
        {'column': 'admin1', 'label': 'admin1'}]

    # target.csv exists in the geo session dir with canonical schema
    with seeded.session_transaction() as sess:
        sid = sess['sid']
    from match_bot.gui import app as app_mod  # noqa: F401  (path check below)
    # Points endpoint serves it with _source flags
    r2 = seeded.get('/api/geo/points?id_col=point_id&name_col=name'
                    '&lat_col=latitude&lon_col=longitude&mode=geo')
    fc = r2.get_json()
    assert len(fc['features']) == 4
    sources = {f['properties']['id']: f['properties']['_source']
               for f in fc['features']}
    assert sources['gn:1'] == 'geonames' and sources['osm:11'] == 'osm'


def test_build_blends_user_upload_and_clears_outputs(seeded):
    # Upload a user points CSV first (plain flow)
    csv_bytes = b'pid,pname,y,x\nU1,Alpha,0.5001,0.5001\nU2,Unique,0.9,0.9\n'
    r = seeded.post('/api/columns', data={
        'prefix': 'target', 'mode': 'geo',
        'file': (io.BytesIO(csv_bytes), 'mypoints.csv'),
    })
    assert r.get_json()['needs_reblend'] is False

    # Simulate stale match output that must be cleared
    with seeded.session_transaction() as sess:
        sid = sess['sid']
    import flask
    # Find the session dir on disk via the app's instance path
    from pathlib import Path
    app = seeded.application
    out_dir = Path(app.instance_path) / 'uploads' / sid / 'geocoding' / 'output'
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'matched.csv').write_text('_ref_id\nstale\n')
    (out_dir / 'manual_geocode.csv').write_text(
        'ref_id,target_id,latitude,longitude\nR1,,1.0,1.0\n')

    r = seeded.post('/api/geo/gazetteer/build', json=_form(
        iso3=ISO3, admin1=['West', 'East'],
        target_id_column='pid', target_name_column='pname',
        geo_lat_column='y', geo_lon_column='x',
        ref_hierarchy=[{'column': 'prov', 'label': 'province'}]))
    data = r.get_json()
    assert r.status_code == 200, data
    # User "Alpha" at same rounded cell as gn Alpha -> user wins, gn dropped
    assert data['counts']['user'] == 2
    assert data['counts']['dropped_duplicates'] == 1
    assert data['counts']['total'] == 5
    assert data['preset']['suggested_hierarchy'][0]['label'] == 'province'

    sd = out_dir.parent
    target = pd.read_csv(sd / 'target.csv', dtype=str)
    assert 'user:U1' in set(target['point_id'])
    assert 'gn:1' not in set(target['point_id'])  # deduped against user Alpha
    assert (sd / 'user_points.csv').exists()      # original preserved
    assert not (out_dir / 'matched.csv').exists()          # cleared
    assert (out_dir / 'manual_geocode.csv').exists()       # kept


def test_upload_after_build_lands_in_user_points(seeded):
    r = seeded.post('/api/geo/gazetteer/build',
                    json=_form(iso3=ISO3, admin1=['West']))
    assert r.status_code == 200
    csv_bytes = b'pid,pname,y,x\nU9,Late Upload,0.2,0.2\n'
    r = seeded.post('/api/columns', data={
        'prefix': 'target', 'mode': 'geo',
        'file': (io.BytesIO(csv_bytes), 'late.csv'),
    })
    data = r.get_json()
    assert data['needs_reblend'] is True

    with seeded.session_transaction() as sess:
        sid = sess['sid']
    from pathlib import Path
    sd = Path(seeded.application.instance_path) / 'uploads' / sid / 'geocoding'
    assert (sd / 'user_points.csv').exists()
    # Generated target.csv untouched (still canonical)
    target = pd.read_csv(sd / 'target.csv', dtype=str)
    assert 'point_id' in target.columns


def test_clear_restores_user_points(seeded):
    csv_bytes = b'pid,pname,y,x\nU1,Mine,0.5,0.5\n'
    seeded.post('/api/columns', data={
        'prefix': 'target', 'mode': 'geo',
        'file': (io.BytesIO(csv_bytes), 'mine.csv'),
    })
    seeded.post('/api/geo/gazetteer/build', json=_form(
        iso3=ISO3, admin1=['West'],
        target_id_column='pid', target_name_column='pname',
        geo_lat_column='y', geo_lon_column='x'))
    r = seeded.post('/api/geo/gazetteer/clear', json={'mode': 'geo'})
    data = r.get_json()
    assert data['restored'] is True and data['columns'] == ['pid', 'pname', 'y', 'x']


def test_plain_upload_flow_unchanged_without_gazetteer(seeded):
    csv_bytes = b'pid,pname,y,x\nU1,Mine,0.5,0.5\n'
    r = seeded.post('/api/columns', data={
        'prefix': 'target', 'mode': 'geo',
        'file': (io.BytesIO(csv_bytes), 'mine.csv'),
    })
    data = r.get_json()
    assert data['needs_reblend'] is False
    assert data['columns'] == ['pid', 'pname', 'y', 'x']
    with seeded.session_transaction() as sess:
        sid = sess['sid']
    from pathlib import Path
    sd = Path(seeded.application.instance_path) / 'uploads' / sid / 'geocoding'
    assert (sd / 'target.csv').exists()
    assert not (sd / 'user_points.csv').exists()


def test_oom_guard_blocks_huge_ungrouped_match(seeded, tmp_path):
    # Build a big fake target.csv directly in the session dir
    seeded.post('/api/geo/gazetteer/build', json=_form(iso3=ISO3, admin1=['West']))
    with seeded.session_transaction() as sess:
        sid = sess['sid']
    from pathlib import Path
    sd = Path(seeded.application.instance_path) / 'uploads' / sid / 'geocoding'
    big = pd.DataFrame({'point_id': [f'gn:{i}' for i in range(6000)],
                        'name': 'x', 'latitude': '0.5', 'longitude': '0.5'})
    big.to_csv(sd / 'target.csv', index=False)
    (sd / 'ref.csv').write_text('rid,rname\nR1,x\n')

    r = seeded.post('/api/run/match?mode=geo', json=_form(
        iso3=ISO3, target_id_column='point_id', target_name_column='name',
        geo_lat_column='latitude', geo_lon_column='longitude'))
    assert r.status_code == 400
    assert 'hierarchy' in r.get_json()['error']
