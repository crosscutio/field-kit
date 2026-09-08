"""End-to-end API walk over the fixture project: upload → admin names →
auto match → link on map → review, with undo."""
import io

import pandas as pd
import pytest

from match_bot.gui.app import create_app
from tests.conftest import REF_ROWS, TARGET_ROWS


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.config['TESTING'] = True
    app.config['UPLOAD_BASE'] = tmp_path / 'uploads'
    return app.test_client()


def upload(client, role, df, name):
    buf = io.BytesIO(df.to_csv(index=False).encode())
    r = client.post('/api/upload', data={'role': role, 'file': (buf, name)})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


@pytest.fixture
def setup(client):
    t = pd.DataFrame(TARGET_ROWS, columns=['cid', 'community', 'region_name', 'district_name'])
    r = pd.DataFrame(REF_ROWS, columns=['pid', 'place', 'adm1', 'adm2', 'lat', 'lon'])
    d = upload(client, 'target', t, 'communities.csv')
    assert d['target']['rows'] == 7 and d['target']['columns'] == list(t.columns)
    upload(client, 'ref', r, 'places.csv')
    d = client.post('/api/state', json={
        'project_name': 'Fixture', 'target_name_column': 'community',
        'target_hierarchy': [{'column': 'region_name', 'label': 'region'},
                             {'column': 'district_name', 'label': 'district'}],
        'ref_name_column': 'place', 'ref_lat_column': 'lat', 'ref_lon_column': 'lon',
        'ref_hierarchy': [{'column': 'adm1', 'label': 'region'}, {'column': 'adm2', 'label': 'district'}],
        'threshold': 85, 'restrict': True,
    }).get_json()
    assert d['ready'] == {'target': True, 'ref': True, 'hierarchy_paired': True}
    assert d['levels'] == ['region', 'district', 'leaf']
    return client


def test_admin_names_walk(setup):
    c = setup
    d = c.post('/api/lookups').get_json()
    assert d['ok'] and d['has_lookups']
    assert d['stats']['region']['pending'] == 1 and d['stats']['district']['pending'] == 1

    lvl = c.get('/api/level/region').get_json()
    pend = [r for r in lvl['rows'] if r['status'] == 'pending']
    assert [r['key'] for r in pend] == ['centr'] and pend[0]['communities'] == 1
    assert pend[0]['best'] >= 90 and lvl['pending_communities'] == 1

    cands = c.get('/api/candidates?level=region&key=centr&top=5').get_json()['candidates']
    assert cands[0]['ref_key'] == 'centre'

    d = c.post('/api/link', json={'level': 'region', 'target_key': 'centr', 'ref_key': 'centre',
                                  'score': cands[0]['score'], 'target_name': 'Centr'}).get_json()
    assert d['ok'] and d['stats']['region']['pending'] == 0 and d['can_undo']

    d = c.post('/api/undo').get_json()
    assert d['ok'] and d['stats']['region']['pending'] == 1
    d = c.post('/api/accept-above', json={'level': 'region', 'threshold': 90}).get_json()
    assert d['applied'] == 1 and d['stats']['region']['pending'] == 0

    # Re-run lookups, then the district level opens up
    c.post('/api/lookups')
    lvl = c.get('/api/level/district').get_json()
    pend = [r for r in lvl['rows'] if r['status'] == 'pending']
    assert [r['key'] for r in pend] == ['gama'] and pend[0]['parents'] == 'Sud'
    d = c.post('/api/no-equivalent', json={'level': 'district', 'target_key': 'gama', 'target_name': 'Gama'}).get_json()
    assert d['stats']['district']['pending'] == 0 and d['stats']['district']['no_equivalent'] == 1
    d = c.post('/api/undo').get_json()
    assert d['stats']['district']['pending'] == 1

    hist = c.get('/api/history').get_json()['entries']
    assert hist[0]['text'].startswith('undo') and any('bulk accepted' in e['text'] for e in hist)


def test_auto_match_link_pin_review(setup):
    c = setup
    c.post('/api/lookups')
    c.post('/api/accept-above', json={'level': 'region', 'threshold': 90})
    c.post('/api/lookups')
    c.post('/api/accept-above', json={'level': 'district', 'threshold': 85})

    d = c.post('/api/run', json={'threshold': 70, 'restrict': True}).get_json()
    assert d['ok'] and d['form']['ran']
    leaf = d['stats']['leaf']
    # exact: Kola, Mira, Foo; fuzzy: Zed; suggest: Kolo 2->Kolo, Tavi->Tavy
    assert leaf['linked'] == 6 and leaf['pending'] == 1
    assert len(d['histogram']) == 10

    rows = c.get('/api/unlinked').get_json()['rows']
    assert [r['id'] for r in rows] == ['7'] and rows[0]['name'] == 'Nowhere'
    all_rows = c.get('/api/unlinked?all=1').get_json()['rows']
    assert len(all_rows) == 7

    # Re-run at a stricter threshold: only bulk-suggested rows can revert.
    # Kolo 2 (100) stays; Tavi was matched by the engine's own fuzzy pass.
    d = c.post('/api/run', json={'threshold': 85, 'restrict': True}).get_json()
    assert d['stats']['leaf']['linked'] == 6

    # A person disagrees with Tavi -> Tavy: unlink, then pick it again by hand.
    tavi = [r for r in all_rows if r['name'] == 'Tavi'][0]
    d = c.post('/api/unlink', json={'level': 'leaf', 'target_key': tavi['id'], 'target_name': 'Tavi'}).get_json()
    assert d['stats']['leaf']['pending'] == 2
    rows = c.get('/api/unlinked').get_json()['rows']
    assert {r['name'] for r in rows} == {'Tavi', 'Nowhere'}
    tavi = [r for r in rows if r['name'] == 'Tavi'][0]
    assert tavi['pool'] == 1

    cands = c.get(f"/api/candidates?level=leaf&key={tavi['id']}").get_json()['candidates']
    assert cands[0]['ref_name'] == 'tavy' and cands[0]['lat'] == '3.0'
    pts = c.get(f"/api/places?target={tavi['id']}").get_json()['points']
    assert [p['name'] for p in pts] == ['Tavy'] and pts[0]['linked'] is False

    d = c.post('/api/link', json={'level': 'leaf', 'target_key': tavi['id'], 'ref_key': cands[0]['ref_id'],
                                  'score': cands[0]['score'], 'target_name': 'Tavi'}).get_json()
    assert d['geocoded'] == 6 and d['manual'] == 1

    d = c.post('/api/pin', json={'target_id': '7', 'lat': 9.9, 'lon': 19.9, 'target_name': 'Nowhere'}).get_json()
    assert d['geocoded'] == 7 and d['pinned'] == 1
    rows = c.get('/api/unlinked').get_json()['rows']
    assert rows == []

    review = c.get('/api/review').get_json()['rows']
    by = {r['name']: r for r in review}
    assert by['Nowhere']['by'] == 'manual · pin' and by['Nowhere']['lat'] == '9.9'
    assert by['Tavi']['by'] == 'manual' and by['Kola']['by'] == 'auto'
    dl = c.get('/api/download/output/geocoded.csv')
    assert dl.status_code == 200 and b'linked_place_id' in dl.data

    d = c.post('/api/undo').get_json()          # undo the pin
    assert d['pinned'] == 0
    d = c.post('/api/undo').get_json()          # undo Tavi link
    assert d['stats']['leaf']['pending'] == 2

    d = c.post('/api/reset').get_json()
    assert d['target'] is None and d['has_lookups'] is False
