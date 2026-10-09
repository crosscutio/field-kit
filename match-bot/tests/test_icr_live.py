"""Live checks against the public example registry (run with -m network)."""
import pytest

from match_bot import icr_fhir, icr_sdi

pytestmark = pytest.mark.network

SDI = 'https://sdi.healthcampaigns.org/catalog.json'
FHIR = 'https://hapi.healthcampaigns.org/fhir'


def test_sdi_catalog_and_scoped_read():
    cat = icr_sdi.read_catalog(SDI)
    nga = next(c for c in icr_sdi.countries(cat) if c['code'] == 'NGA')
    assert 'settlement' in nga['types'] and nga['admin_units'] > 0
    con = icr_sdi.duck()
    units = icr_sdi.admin_units(con, nga['admin_href'])
    takai = next(u for u in units if u['name'] == 'Takai' and u['admin_level'] == 2)
    rows = icr_sdi.read_sites(con, nga['types']['settlement']['href'], 'active', [takai['id']])
    assert rows and all(r['admin2_name'] == 'Takai' for r in rows)


def test_fhir_forms():
    cap = icr_fhir.capability(FHIR)
    assert cap['fhir_version'].startswith('4')
    forms = {f['id']: f for f in icr_fhir.questionnaires(FHIR)}
    assert forms['espen-mda-location-registration']['suggest'] == {
        'name': 'l_location', 'lat': 'l_gps_lat', 'lon': 'l_gps_lng'}
