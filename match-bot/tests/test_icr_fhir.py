"""Named places from ICR form responses: a fake FHIR server answers the
client's real requests, shaped like hapi.healthcampaigns.org."""
import io
import json
import urllib.parse

import pandas as pd
import pytest

from match_bot import icr_fhir, icr_sdi
from match_bot.gui.app import create_app

BASE = 'https://fhir.test/fhir'
Q_REG = 'https://icr.healthcampaigns.org/Questionnaire/espen-mda-location-registration'
Q_ODK = 'https://icr.healthcampaigns.org/Questionnaire/odk-geopoint-form'
Q_SUP = 'https://icr.healthcampaigns.org/Questionnaire/nga-ntd-mda-supervision-checklist'
EXT = 'http://hl7.org/fhir/uv/sdc/StructureDefinition/sdc-questionnaire-templateExtractValue'


def tx(link):
    return {'extension': [{'url': EXT, 'valueString': f"%resource.repeat(item).where(linkId='{link}').answer.value.first()"}]}


QUESTIONNAIRES = [
    {'resourceType': 'Questionnaire', 'id': 'espen-mda-location-registration', 'url': Q_REG,
     'name': 'EspenMDALocationRegistration', 'status': 'active',
     'contained': [{'resourceType': 'Location', 'id': 'loc-template', '_name': tx('l_location'),
                    'position': {'_latitude': tx('l_gps_lat'), '_longitude': tx('l_gps_lng')}}],
     'item': [{'linkId': 'l_location', 'text': 'Enter the village / location / site', 'type': 'string'},
              {'linkId': 'l_gps', 'text': 'GPS of the village', 'type': 'group', 'item': [
                  {'linkId': 'l_gps_lat', 'text': 'Latitude', 'type': 'decimal'},
                  {'linkId': 'l_gps_lng', 'text': 'Longitude', 'type': 'decimal'}]},
              {'linkId': 'l_ok', 'text': 'Done?', 'type': 'boolean'}]},
    {'resourceType': 'Questionnaire', 'id': 'odk-geopoint-form', 'url': Q_ODK, 'title': 'ODK village form',
     'status': 'active', 'item': [{'linkId': 'village', 'text': 'Village', 'type': 'string'},
                                  {'linkId': 'gps', 'text': 'GPS', 'type': 'string'}]},
    {'resourceType': 'Questionnaire', 'id': 'nga-ntd-mda-supervision-checklist', 'url': Q_SUP,
     'title': 'Nigeria NTD MDA supervision checklist', 'version': '0.1.0-dummy', 'status': 'draft',
     'item': [{'linkId': 'visit', 'type': 'group', 'item': [
         {'linkId': 'supervisor_designation', 'text': 'Supervisor designation', 'type': 'string'}]}]},
]


def qr(i, q, subject, camp, items, status='completed'):
    return {'resourceType': 'QuestionnaireResponse', 'id': f'r{i}', 'questionnaire': q, 'status': status,
            'subject': {'reference': subject}, 'basedOn': [{'reference': camp}] if camp else [],
            'authored': '2026-02-21', 'item': items}


def reg(name, lat, lon):
    return [{'linkId': 'l_location', 'answer': [{'valueString': name}]},
            {'linkId': 'l_gps', 'item': [{'linkId': 'l_gps_lat', 'answer': [{'valueDecimal': lat}]},
                                         {'linkId': 'l_gps_lng', 'answer': [{'valueDecimal': lon}]}]}]


RESPONSES = [
    qr(1, Q_REG, 'Location/s1', 'CarePlan/lf-takai', reg('Kofar Mata', 11.57, 9.13)),
    qr(2, Q_REG, 'Location/s1', 'CarePlan/lf-takai', reg('Kofar  Mata', 11.57001, 9.13001)),   # repeat visit
    qr(3, Q_REG, 'Location/s2', 'CarePlan/lf-takai', reg('Gidan Dawa', 191.0, 9.0)),           # out of range
    qr(4, Q_REG, 'Location/sch', 'CarePlan/sch-takai', reg('School village', 11.6, 9.2)),
    qr(5, Q_REG, 'Location/s2', 'CarePlan/lf-ajingi', reg('Unguwar Rimi', 12.09, 8.75), status='in-progress'),
    qr(6, Q_ODK, 'Location/s2', 'CarePlan/lf-ajingi', [{'linkId': 'village', 'answer': [{'valueString': 'Bogoro'}]},
                                                       {'linkId': 'gps', 'answer': [{'valueString': '1.24291 30.761 0 5'}]}]),
    qr(7, Q_SUP, 'Group/g1', None, [{'linkId': 'visit', 'item': [{'linkId': 'supervisor_designation', 'answer': [{'valueString': 'SNTD'}]}]}]),
]
INCLUDED = {
    'Location/s1': {'resourceType': 'Location', 'id': 's1', 'type': [{'coding': [{'code': 'community-distribution-point', 'display': 'Community distribution point'}]}]},
    'Location/s2': {'resourceType': 'Location', 'id': 's2', 'type': [{'coding': [{'code': 'community-distribution-point', 'display': 'Community distribution point'}]}]},
    'Location/sch': {'resourceType': 'Location', 'id': 'sch', 'type': [{'coding': [{'code': 'school', 'display': 'School'}]}]},
    'Group/g1': {'resourceType': 'Group', 'id': 'g1'},
    'CarePlan/lf-takai': {'resourceType': 'CarePlan', 'id': 'lf-takai', 'title': 'LF MDA, 2026 round — Takai LGA, Kano',
                          'extension': [{'url': 'https://icr.healthcampaigns.org/StructureDefinition/campaign-round', 'valuePositiveInt': 5}],
                          'period': {'start': '2026-02-17', 'end': '2026-03-02'}, 'partOf': [{'reference': 'CarePlan/lf-kano'}]},
    'CarePlan/lf-ajingi': {'resourceType': 'CarePlan', 'id': 'lf-ajingi', 'title': 'LF MDA, 2026 round — Ajingi LGA, Kano',
                           'partOf': [{'reference': 'CarePlan/lf-kano'}]},
    'CarePlan/sch-takai': {'resourceType': 'CarePlan', 'id': 'sch-takai', 'title': 'SCH MDA, 2026 round — Takai LGA, Kano'},
}
PARENTS = {'lf-kano': {'resourceType': 'CarePlan', 'id': 'lf-kano', 'title': 'LF MDA, 2026 round — Kano State'}}
CAPABILITY = {'resourceType': 'CapabilityStatement', 'fhirVersion': '4.0.1',
              'software': {'name': 'HAPI FHIR Server', 'version': '8.12.0'},
              'rest': [{'resource': [{'type': 'QuestionnaireResponse',
                                      'searchParam': [{'name': n} for n in ('questionnaire', 'subject', 'based-on', 'status')],
                                      'searchInclude': ['QuestionnaireResponse:subject', 'QuestionnaireResponse:based-on']}]}]}


def bundle(entries, next_url=None):
    b = {'resourceType': 'Bundle', 'type': 'searchset', 'entry': entries, 'link': []}
    if next_url:
        b['link'].append({'relation': 'next', 'url': next_url})
    return b


class FakeServer:
    """Routes the client's GETs; pages QuestionnaireResponse 3 at a time."""
    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url, dict(req.header_items())))
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        path = u.path.rsplit('/fhir/', 1)[-1]
        if path == 'metadata':
            body = CAPABILITY
        elif path == 'Questionnaire':
            body = bundle([{'resource': x} for x in QUESTIONNAIRES])
        elif path == 'CarePlan':
            ids = q['_id'][0].split(',')
            body = bundle([{'resource': PARENTS[i]} for i in ids if i in PARENTS])
        elif path == 'QuestionnaireResponse':
            forms = q['questionnaire'][0].split(',')
            rs = [r for r in RESPONSES if r['questionnaire'] in forms]
            if q.get('_summary') == ['count']:
                body = {'resourceType': 'Bundle', 'total': len(rs)}
            else:
                off = int(q.get('_offset', ['0'])[0])
                page = rs[off:off + 3]
                inc = {ref for r in page for ref in [r['subject']['reference']] + [b['reference'] for b in r['basedOn']]}
                nxt = None
                if off + 3 < len(rs):
                    nq = dict(q, _offset=[str(off + 3)])
                    nxt = BASE + '/QuestionnaireResponse?' + urllib.parse.urlencode(nq, doseq=True)
                body = bundle([{'resource': r, 'search': {'mode': 'match'}} for r in page] +
                              [{'resource': INCLUDED[ref], 'search': {'mode': 'include'}} for ref in sorted(inc)], nxt)
        else:
            raise AssertionError('unexpected ' + url)
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def server(monkeypatch):
    s = FakeServer()
    monkeypatch.setattr(icr_sdi.urllib.request, 'urlopen', s)
    return s


def test_capability(server):
    cap = icr_fhir.capability(BASE + '/', {'Authorization': 'Bearer t'})
    assert cap['software'] == 'HAPI FHIR Server 8.12.0' and cap['base'] == BASE
    assert server.calls[0][1]['Authorization'] == 'Bearer t'


def test_capability_missing_search(monkeypatch):
    cs = json.loads(json.dumps(CAPABILITY))
    cs['rest'][0]['resource'][0]['searchParam'] = [{'name': 'questionnaire'}]
    monkeypatch.setattr(icr_sdi.urllib.request, 'urlopen', lambda req, timeout=None: io.BytesIO(json.dumps(cs).encode()))
    with pytest.raises(icr_fhir.IcrError, match='subject, based-on'):
        icr_fhir.capability(BASE)


def test_questionnaires_items_counts_and_template(server):
    forms = {f['id']: f for f in icr_fhir.questionnaires(BASE)}
    reg = forms['espen-mda-location-registration']
    assert reg['responses'] == 5 and reg['title'] == 'EspenMDALocationRegistration'
    # nested items flattened, booleans/groups left out
    assert [i[0] for i in reg['items']] == ['l_location', 'l_gps_lat', 'l_gps_lng']
    assert reg['suggest'] == {'name': 'l_location', 'lat': 'l_gps_lat', 'lon': 'l_gps_lng'}
    # no template, no guess
    assert forms['odk-geopoint-form']['suggest'] == {}
    assert forms['nga-ntd-mda-supervision-checklist']['version'] == '0.1.0-dummy'


def test_scan_pages_subjects_and_campaigns(server):
    res = icr_fhir.scan(BASE, {}, [Q_REG, Q_SUP])
    by = {}
    for c in res['cells']:
        by[c['subject']] = by.get(c['subject'], 0) + c['n']
    assert by == {'community-distribution-point': 4, 'school': 1, 'group': 1}
    assert sum(1 for u, _ in server.calls if '_offset' in u) == 1          # followed the next link
    camps = res['campaigns']
    assert camps['CarePlan/lf-takai']['round'] == 5 and camps['CarePlan/lf-takai']['parent'] == 'CarePlan/lf-kano'
    assert camps['CarePlan/lf-kano']['title'] == 'LF MDA, 2026 round — Kano State'   # parent fetched by _id
    assert {c['status'] for c in res['cells']} == {'completed', 'in-progress'}
    assert any(c['campaign'] == '' and c['subject'] == 'group' for c in res['cells'])


def test_pull_reads_pools_and_skips(server):
    mapping = {Q_REG: {'name': 'l_location', 'lat': 'l_gps_lat', 'lon': 'l_gps_lng'},
               Q_ODK: {'name': 'village', 'lat': 'gps', 'lon': 'gps'}}
    sel = {'forms': [Q_REG, Q_ODK], 'subjects': ['community-distribution-point'],
           'campaigns': ['CarePlan/lf-takai', 'CarePlan/lf-ajingi'], 'statuses': []}
    res = icr_fhir.pull(BASE, {}, sel, mapping, {Q_REG: 'Registration'})
    names = {p['name']: p for p in res['places']}
    assert set(names) == {'Kofar Mata', 'Unguwar Rimi', 'Bogoro'}       # school filtered out by subject
    assert res['read'] == 5 and res['pooled'] == 1 and res['skipped'] == 1   # r2 pooled, r3 out of range
    assert names['Bogoro']['latitude'] == 1.24291 and names['Bogoro']['longitude'] == 30.761   # ODK geopoint
    assert names['Kofar Mata']['point_id'] == 'qr:r1' and names['Kofar Mata']['form'] == 'Registration'
    assert names['Kofar Mata']['campaign'] == 'LF MDA, 2026 round — Takai LGA, Kano'
    only_done = icr_fhir.pull(BASE, {}, dict(sel, statuses=['completed']), mapping)
    assert 'Unguwar Rimi' not in {p['name'] for p in only_done['places']}


def test_pull_needs_a_mapping(server):
    with pytest.raises(icr_fhir.IcrError):
        icr_fhir.pull(BASE, {}, {'forms': [Q_SUP]}, {})


def test_api_pull_writes_named_places(server, tmp_path):
    app = create_app()
    app.config['TESTING'] = True
    app.config['UPLOAD_BASE'] = tmp_path / 'uploads'
    c = app.test_client()
    d = c.post('/api/fhir/connect', json={'url': BASE, 'auth_mode': 'bearer', 'auth_secret': 'tok'}).get_json()
    assert d['ok'] and d['form']['fhir']['auth_secret_set'] and 'auth_secret' not in d['form']['fhir']
    assert c.post('/api/fhir/forms', json={}).get_json()['ok']
    scan = c.post('/api/fhir/scan', json={'forms': [Q_REG]}).get_json()
    assert scan['ok'] and scan['campaigns']
    n_calls = len(server.calls)
    assert c.post('/api/fhir/scan', json={'forms': [Q_REG]}).get_json()['ok']
    assert len(server.calls) == n_calls                                   # cached per session
    # every request after connect carried the stored token
    assert all(h.get('Authorization') == 'Bearer tok' for _, h in server.calls)

    body = {'forms': [Q_REG], 'subjects': ['community-distribution-point', 'school'],
            'campaigns': ['CarePlan/lf-takai', 'CarePlan/sch-takai', 'CarePlan/lf-ajingi'], 'statuses': []}
    r = c.post('/api/fhir/pull', json=dict(body, mapping={}))
    assert r.status_code == 400 and 'every questionnaire' in r.get_json()['error']
    d = c.post('/api/fhir/pull', json=dict(body, mapping={Q_REG: {'name': 'l_location', 'lat': 'l_gps_lat', 'lon': 'l_gps_lng'}})).get_json()
    assert d['ok'] and d['count'] == 3
    f = d['form']
    assert f['ref_source'] == 'icr-forms' and f['ref_id_column'] == 'point_id' and f['ref_lat_column'] == 'latitude'
    assert f['fhir']['mapping'][Q_REG]['lat'] == 'l_gps_lat'
    ref = pd.read_csv(next((tmp_path / 'uploads').iterdir()) / 'ref.csv', dtype=str)
    assert {'point_id', 'name', 'latitude', 'longitude', 'source', 'form', 'campaign', 'authored'} <= set(ref.columns)
    assert set(ref['source']) == {'icr-forms'}
