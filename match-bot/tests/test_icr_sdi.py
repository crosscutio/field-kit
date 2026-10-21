"""ICR SDI reader: catalog walk, sign-in headers, scoped parquet reads."""
import io
import json

import pytest

from match_bot import icr_sdi
from tests.icr_fixtures import build_sdi


@pytest.fixture
def sdi(tmp_path):
    url = build_sdi(tmp_path / 'sdi')
    cat = icr_sdi.read_catalog(url)
    ctry = icr_sdi.countries(cat)[0]
    return cat, ctry, icr_sdi.duck()


def test_auth_headers_modes():
    assert icr_sdi.auth_headers('none', 'ignored') == {}
    assert icr_sdi.auth_headers('bearer', 'tok') == {'Authorization': 'Bearer tok'}
    assert icr_sdi.auth_headers('basic', 'u:p') == {'Authorization': 'Basic dTpw'}
    assert icr_sdi.auth_headers('header', 'X-Api-Key: k1') == {'X-Api-Key': 'k1'}
    for mode, secret in [('bearer', ''), ('basic', 'nocolon'), ('header', 'novalue'), ('magic', 'x')]:
        with pytest.raises(icr_sdi.IcrError):
            icr_sdi.auth_headers(mode, secret)


def test_headers_reach_every_json_request(monkeypatch):
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=None):
        seen.append(dict(req.header_items()))
        return Resp(json.dumps({'type': 'Catalog', 'stac_version': '1.1.0', 'links': []}).encode())

    monkeypatch.setattr(icr_sdi.urllib.request, 'urlopen', fake)
    with pytest.raises(icr_sdi.IcrError, match='no partitioned location collection'):
        icr_sdi.read_catalog('https://example.org/catalog.json', {'Authorization': 'Bearer t'})
    assert seen and all(h.get('Authorization') == 'Bearer t' for h in seen)
    assert all(h.get('User-agent', '').startswith('match-bot-icr') for h in seen)


def test_catalog_walk(sdi):
    cat, ctry, _ = sdi
    assert cat['title'] == 'Test ICR' and cat['license'] == 'CC-BY-4.0'
    assert {(i['geom_type'], i['type']) for i in cat['items']} == {
        ('polygon', 'admin-unit'), ('point', 'settlement'), ('null', 'settlement'), ('null', 'facility')}
    assert ctry['code'] == 'TST' and ctry['name'] == 'Testland'
    assert sorted(ctry['types']) == ['facility', 'settlement'] and ctry['admin_units'] == 6
    # a type spans its point and null files; the null file holds the sites to locate
    st = ctry['types']['settlement']
    assert [p['geom_type'] for p in st['parts']] == ['point', 'null'] and st['count'] == 5
    assert [p['geom_type'] for p in ctry['types']['facility']['parts']] == ['null']


def test_sites_without_coordinates_are_read(sdi):
    _, ctry, con = sdi
    fac = icr_sdi.site_hrefs(ctry['types']['facility'])
    assert icr_sdi.type_summary(con, fac) == {'total': 2, 'missing': 2, 'active': 1}
    assert icr_sdi.site_counts(con, fac)['tst-su'] == {'sites': 2, 'missing': 2}
    rows = icr_sdi.read_sites(con, fac, 'active')
    assert [r['name'] for r in rows] == ['Gamma Clinic'] and rows[0]['position_latitude'] is None


def test_not_a_catalog(tmp_path):
    p = tmp_path / 'x.json'
    p.write_text('{"hello": 1}')
    with pytest.raises(icr_sdi.IcrError, match='not a STAC catalog'):
        icr_sdi.read_catalog(p.as_uri())


def test_admin_tree_and_counts(sdi):
    _, ctry, con = sdi
    units = icr_sdi.admin_units(con, ctry['admin_href'])
    counts = icr_sdi.site_counts(con, icr_sdi.site_hrefs(ctry['types']['settlement']))
    tree = icr_sdi.admin_tree(units, counts)
    assert [n['name'] for n in tree] == ['Nord', 'Sud']
    nord = tree[0]
    assert nord['sites'] == 4 and nord['missing'] == 1
    assert [c['name'] for c in nord['children']] == ['Alpha', 'Beta']
    summary = icr_sdi.type_summary(con, icr_sdi.site_hrefs(ctry['types']['settlement']))
    # missing comes from position_*, never from kiln's always-filled lat/lon
    assert summary == {'total': 5, 'missing': 2, 'active': 4}


def test_read_sites_scope_and_status(sdi):
    _, ctry, con = sdi
    href = icr_sdi.site_hrefs(ctry['types']['settlement'])
    assert len(icr_sdi.read_sites(con, href, 'all')) == 5
    active = icr_sdi.read_sites(con, href, 'active')
    assert 'Old Rimi' not in {r['name'] for r in active} and len(active) == 4
    beta = icr_sdi.read_sites(con, href, 'all', ['tst-no-102'])
    assert {r['name'] for r in beta} == {'Rimi', 'Old Rimi'}
    nord = icr_sdi.read_sites(con, href, 'active', ['tst-no'])
    assert {r['name'] for r in nord} == {'Kofar', 'Gidan', 'Rimi'}
    gidan = next(r for r in nord if r['name'] == 'Gidan')
    assert gidan['position_latitude'] is None and gidan['admin2_name'] == 'Alpha'


def test_boundaries_geojson(sdi):
    _, ctry, con = sdi
    units = icr_sdi.admin_units(con, ctry['admin_href'])
    gj = icr_sdi.boundaries_geojson(units, 2)
    assert [f['properties']['name'] for f in gj['features']] == ['Alpha', 'Beta', 'Gamma']
    assert gj['features'][0]['geometry']['type'] == 'Polygon'
