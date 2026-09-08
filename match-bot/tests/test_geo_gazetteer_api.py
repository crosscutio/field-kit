"""Flask route tests for the gazetteer endpoints (no network — seeded cache)."""
import json

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
    '1\tAlpha\tAlpha\t\t0.5\t0.5\tP\tPPL\tTS\t\t\t\t\t\t100\t\t\t\tUTC\t2020',
    '2\tBeta\tBeta\t\t0.5\t1.5\tP\tPPL\tTS\t\t\t\t\t\t200\t\t\t\tUTC\t2020',
    '3\tOutside\tOutside\t\t5.0\t5.0\tP\tPPL\tTS\t\t\t\t\t\t0\t\t\t\tUTC\t2020',
])

OVERPASS_WEST = {'elements': [
    {'type': 'node', 'id': 11, 'lat': 0.4, 'lon': 0.4, 'tags': {'place': 'village', 'name': 'Gamma'}}]}
OVERPASS_EAST = {'elements': [
    {'type': 'node', 'id': 12, 'lat': 0.4, 'lon': 1.4, 'tags': {'place': 'town', 'name': 'Delta'}}]}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    gaz_dir = tmp_path / 'gazetteer'
    monkeypatch.setenv('MATCH_BOT_GAZETTEER_DIR', str(gaz_dir))
    bdir = gaz_dir / 'data' / 'boundaries'
    bdir.mkdir(parents=True)
    (bdir / f'{ISO3}_ADM1.geojson').write_text(json.dumps(BOUNDARIES))
    gdir = gaz_dir / 'data' / 'geonames'
    gdir.mkdir(parents=True)
    (gdir / f'{ISO2}.txt').write_text(GEONAMES_TXT)
    monkeypatch.setattr(gz, 'iso2_for', lambda iso3: ISO2)
    shapes = gz.admin1_shapes(ISO3, None)
    for name, payload in (('West', OVERPASS_WEST), ('East', OVERPASS_EAST)):
        gz.overpass_cache_path(ISO3, name, shapes[name]).write_text(json.dumps(payload))
    app = create_app()
    app.config['TESTING'] = True
    app.config['UPLOAD_BASE'] = tmp_path / 'uploads'
    return app.test_client()


def test_countries_endpoint(seeded):
    data = seeded.get('/api/gazetteer/countries').get_json()
    assert data['ok'] and len(data['countries']) > 200


def test_admin1_from_seeded_cache(seeded):
    data = seeded.post('/api/gazetteer/admin1', json={'iso3': ISO3}).get_json()
    assert data['admin1'] == ['East', 'West'] and data['cached'] is True


def test_preview_counts_selection(seeded):
    data = seeded.post('/api/gazetteer/preview', json={'iso3': ISO3, 'admin1': ['West']}).get_json()
    assert data['geonames_count'] == 1


def test_build_writes_reference_and_pairs_hierarchy(seeded):
    seeded.post('/api/state', json={'target_hierarchy': [{'column': 'reg', 'label': 'region'}]})
    r = seeded.post('/api/gazetteer/build', json={'iso3': ISO3, 'admin1': ['West', 'East']})
    data = r.get_json()
    assert r.status_code == 200, data
    assert data['count'] == 4
    form = data['form']
    assert form['ref_source'] == 'gazetteer' and form['ref_id_column'] == 'point_id'
    assert form['ref_hierarchy'] == [{'column': 'admin1', 'label': 'region'}]
    assert form['country'] == ISO3
    assert data['ref']['rows'] == 4 and 'latitude' in data['ref']['columns']

    b = seeded.get('/api/boundaries/region').get_json()
    assert b['ok'] and b['name_property'] == 'shapeName' and len(b['geojson']['features']) == 2
