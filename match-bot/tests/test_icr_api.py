"""ICR site-list import through the GUI API, through to the export."""
import io

import pandas as pd
import pytest

from match_bot.gui.app import create_app
from tests.icr_fixtures import build_sdi


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.config['TESTING'] = True
    app.config['UPLOAD_BASE'] = tmp_path / 'uploads'
    return app.test_client()


@pytest.fixture
def catalog(tmp_path):
    return build_sdi(tmp_path / 'sdi')


def ok(r):
    d = r.get_json()
    assert r.status_code == 200 and d['ok'], d
    return d


def test_import_match_and_export(client, catalog, tmp_path):
    d = ok(client.post('/api/icr/catalog', json={'url': catalog, 'auth_mode': 'bearer', 'auth_secret': 's3cret'}))
    assert d['countries'][0]['code'] == 'TST'
    # the stored secret never travels back to the browser
    assert 'auth_secret' not in d['form']['icr'] and d['form']['icr']['auth_secret_set'] is True
    d = ok(client.post('/api/icr/options', json={'country': 'TST', 'type': 'settlement'}))
    assert d['types']['settlement'] == {'published': True, 'total': 5, 'missing': 2, 'active': 4}
    assert d['types']['facility'] == {'published': True, 'total': 2, 'missing': 2, 'active': 1}
    assert d['types']['school'] == {'published': False}

    d = ok(client.post('/api/icr/import/boundaries', json={'country': 'TST'}))
    assert d['form']['boundaries_source'] == 'icr'
    d = ok(client.post('/api/icr/import/sites', json={'country': 'TST', 'type': 'settlement',
                                                      'status': 'active', 'units': ['tst-no']}))
    # by default only the records that need a location are imported, so nothing is overwritten unasked
    assert d['icr_coverage'] == {'rows': 3, 'with_coords': 2, 'missing': 1, 'keep': 'missing'}
    assert d['target']['rows'] == 1
    d = ok(client.post('/api/icr/records', json={'keep': 'coords'}))
    assert d['target']['rows'] == 2 and d['icr_coverage']['keep'] == 'coords'
    d = ok(client.post('/api/icr/records', json={'keep': 'all'}))
    assert d['target']['rows'] == 3
    assert d['form']['target_source'] == 'icr' and d['form']['target_name_column'] == 'name'
    assert [h['column'] for h in d['form']['target_hierarchy']] == ['admin1_name', 'admin2_name']
    snap = d['form']['icr']['snapshot']
    assert [f['checksum'] for f in snap['parts']] == ['abc123', 'abc123'] and snap['datetime'].startswith('2026')

    prev = ok(client.get('/api/icr/preview?filter=none'))
    assert prev['matching'] == 1 and prev['rows'][0]['name'] == 'Gidan'

    # named places: a user csv, tagged by the registry's own admin units
    ref = pd.DataFrame([['p1', 'Kofar', 1.01, 0.52], ['p2', 'Gidan', 0.8, 0.4], ['p3', 'Rimi', 1.21, 1.49]],
                       columns=['pid', 'place', 'lat', 'lon'])
    buf = io.BytesIO(ref.to_csv(index=False).encode())
    ok(client.post('/api/upload', data={'role': 'ref', 'file': (buf, 'places.csv')}))
    ok(client.post('/api/state', json={'ref_name_column': 'place', 'ref_lat_column': 'lat', 'ref_lon_column': 'lon'}))
    d = ok(client.post('/api/tag-places', json={}))
    assert d['tagging']['stats']['admin1']['adm'].startswith('ICR') and d['tagging']['stats']['admin2']['inside'] == 3
    ok(client.post('/api/lookups', json={}))
    ok(client.post('/api/run', json={'threshold': 85, 'restrict': True}))

    rows = ok(client.get('/api/review'))['rows']
    kofar = next(r for r in rows if r['name'] == 'Kofar')
    assert kofar['lat'] and str(kofar['icr_lat']).startswith('1.0')
    out = pd.read_csv(io.BytesIO(client.get('/api/download/output/geocoded.csv').data), dtype=str)
    assert {'point_id', 'icr_latitude', 'icr_longitude', 'latitude', 'longitude'} <= set(out.columns)
    gidan = out[out['name'] == 'Gidan'].iloc[0]
    assert pd.isna(gidan['icr_latitude']) and gidan['latitude'] == '0.8'


def test_download_refuses_session_files(client, catalog):
    ok(client.post('/api/icr/catalog', json={'url': catalog, 'auth_mode': 'bearer', 'auth_secret': 's3cret'}))
    assert client.get('/api/download/form_state.json').status_code == 403
    assert client.get('/api/download/output/../form_state.json').status_code == 403


def test_bad_catalog_is_a_clear_error(client, tmp_path):
    p = tmp_path / 'nope.json'
    p.write_text('[]')
    r = client.post('/api/icr/catalog', json={'url': p.as_uri()})
    assert r.status_code == 400 and 'not a STAC catalog' in r.get_json()['error']


def test_empty_secret_keeps_the_stored_one(client, catalog, tmp_path):
    ok(client.post('/api/icr/catalog', json={'url': catalog, 'auth_mode': 'header', 'auth_secret': 'X-Key: one'}))
    ok(client.post('/api/icr/catalog', json={'url': catalog, 'auth_mode': 'header', 'auth_secret': ''}))
    sid = next((tmp_path / 'uploads').iterdir())
    assert '"auth_secret": "X-Key: one"' in (sid / 'form_state.json').read_text()


def test_record_choice_with_nothing_missing(client, catalog):
    ok(client.post('/api/icr/catalog', json={'url': catalog}))
    ok(client.post('/api/icr/import/boundaries', json={'country': 'TST'}))
    d = ok(client.post('/api/icr/import/sites', json={'country': 'TST', 'type': 'settlement',
                                                      'status': 'all', 'units': ['tst-no-102']}))
    # every row has coordinates: default to all of them, and "need coordinates" is refused
    assert d['icr_coverage']['keep'] == 'all' and d['target']['rows'] == 2
    r = client.post('/api/icr/records', json={'keep': 'missing'})
    assert r.status_code == 400 and 'No records' in r.get_json()['error']
