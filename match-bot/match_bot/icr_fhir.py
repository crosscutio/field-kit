"""Named places from ICR campaign form responses on a FHIR R4 server.

Every response is an ``ICRCampaignFormResponse`` (QuestionnaireResponse):
``questionnaire`` is the form, ``subject`` the Location/Group it is about,
``basedOn`` the campaign (an ICRCampaign CarePlan) and ``item`` the answers.
linkIds are specific to each form, so the user maps name / latitude /
longitude once per form; the only pre-fill is what the form itself declares
in an SDC extraction template (``templateExtractValue`` on a contained
Location's ``name`` / ``position.latitude`` / ``position.longitude``).
The tool never guesses whether a form records coordinates.
"""

import re
import urllib.parse
import unicodedata

from match_bot.icr_sdi import IcrError, auth_headers, http_json  # noqa: F401  (re-exported)

PAGE = 1000            # HAPI's maximum page size
MAX_PAGES = 500
TEXT_TYPES = ('string', 'text')
NUMBER_TYPES = ('decimal', 'integer')
EXTRACT_VALUE = 'sdc-questionnaire-templateExtractValue'
ROUND_EXT = 'campaign-round'
NEEDED_PARAMS = ('questionnaire', 'subject', 'based-on')


def _url(base, path, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')}, doseq=True)
    return base.rstrip('/') + '/' + path + ('?' + q if q else '')


def _pages(url, headers):
    """Yield every entry of a searchset, following ``link[next]``."""
    for _ in range(MAX_PAGES):
        b = http_json(url, headers)
        if not isinstance(b, dict) or b.get('resourceType') != 'Bundle':
            raise IcrError(f'{url} did not return a FHIR Bundle')
        yield from b.get('entry', [])
        url = next((l['url'] for l in b.get('link', []) if l.get('relation') == 'next'), None)
        if not url:
            return
    raise IcrError('Too many pages. Narrow the selection.')


# ---- connection ---------------------------------------------------------

def capability(base, headers=None):
    """Check the server can answer the queries this flow needs."""
    base = (base or '').strip().rstrip('/')
    if not base:
        raise IcrError('Enter the FHIR server address')
    cs = http_json(base + '/metadata', headers)
    if not isinstance(cs, dict) or cs.get('resourceType') != 'CapabilityStatement':
        raise IcrError(f'No CapabilityStatement came back from {base}/metadata')
    qr = {}
    for rest in cs.get('rest', []):
        for r in rest.get('resource', []):
            if r.get('type') == 'QuestionnaireResponse':
                qr = r
    params = {p.get('name') for p in qr.get('searchParam', [])}
    includes = set(qr.get('searchInclude', []))
    missing = [p for p in NEEDED_PARAMS if p not in params]
    if not qr:
        raise IcrError('This server does not serve QuestionnaireResponse')
    if missing:
        raise IcrError('This server cannot search responses by ' + ', '.join(missing))
    sw = cs.get('software') or {}
    impl = cs.get('implementation') or {}
    return {
        'base': base, 'software': ' '.join(x for x in (sw.get('name'), sw.get('version')) if x) or 'FHIR server',
        'fhir_version': cs.get('fhirVersion', ''), 'name': impl.get('description') or cs.get('name') or '',
        'includes': sorted(i for i in includes if i.startswith('QuestionnaireResponse:')),
    }


# ---- forms ---------------------------------------------------------------

def flatten_items(items, out=None):
    """[linkId, text, type] for every answerable text/number question."""
    out = [] if out is None else out
    for it in items or []:
        if it.get('type') in TEXT_TYPES + NUMBER_TYPES:
            out.append([it.get('linkId', ''), (it.get('text') or '').strip(), it.get('type')])
        flatten_items(it.get('item'), out)
    return out


def _extract_link(ext_holder):
    for e in (ext_holder or {}).get('extension', []):
        if str(e.get('url', '')).endswith(EXTRACT_VALUE):
            m = re.search(r"linkId\s*=\s*'([^']+)'", e.get('valueString', ''))
            if m:
                return m.group(1)
    return None


def template_suggestions(q):
    """{'name'|'lat'|'lon': linkId} declared by a contained Location template."""
    out = {}
    for c in q.get('contained', []):
        if c.get('resourceType') != 'Location':
            continue
        name = _extract_link(c.get('_name'))
        pos = c.get('position') or {}
        lat, lon = _extract_link(pos.get('_latitude')), _extract_link(pos.get('_longitude'))
        if name and lat and lon:
            return {'name': name, 'lat': lat, 'lon': lon}
        out.update({k: v for k, v in (('name', name), ('lat', lat), ('lon', lon)) if v})
    return out


def questionnaires(base, headers=None):
    """Every form on the server with its questions and response count."""
    forms = []
    for e in _pages(_url(base, 'Questionnaire', _count=200), headers):
        q = e.get('resource') or {}
        if q.get('resourceType') != 'Questionnaire':
            continue
        url = q.get('url') or _url(base, 'Questionnaire/' + q.get('id', ''))
        forms.append({
            'url': url, 'id': q.get('id', ''), 'title': q.get('title') or q.get('name') or q.get('id', ''),
            'version': q.get('version', ''), 'status': q.get('status', ''),
            'items': flatten_items(q.get('item')), 'suggest': template_suggestions(q),
        })
    for f in forms:
        b = http_json(_url(base, 'QuestionnaireResponse', questionnaire=f['url'], _summary='count'), headers)
        f['responses'] = int((b or {}).get('total') or 0)
    return sorted(forms, key=lambda f: (-f['responses'], f['title'].lower()))


# ---- scan: what the chosen forms' responses are about --------------------

def _ref(r):
    return (r or {}).get('reference', '')


def subject_type(res):
    """(key, label) for a response subject: Location.type, or a Group."""
    if not res:
        return ('unknown', 'Unknown subjects')
    if res.get('resourceType') == 'Group':
        return ('group', 'Delivery groups')
    for t in res.get('type', []):
        for c in t.get('coding', []):
            if c.get('code'):
                return (c['code'], c.get('display') or c['code'])
    return ('location', 'Locations (untyped)')


def _campaign(cp):
    rnd = next((e.get('valuePositiveInt') or e.get('valueInteger') for e in cp.get('extension', [])
                if str(e.get('url', '')).endswith(ROUND_EXT)), None)
    per = cp.get('period') or {}
    return {'title': cp.get('title') or cp.get('id', ''), 'round': rnd,
            'start': per.get('start', ''), 'end': per.get('end', ''),
            'parent': _ref((cp.get('partOf') or [{}])[0])}


def _responses(base, headers, urls):
    """Responses for the given forms, with their subjects and campaigns included."""
    found, included = [], {}
    url = _url(base, 'QuestionnaireResponse', questionnaire=','.join(urls), _count=PAGE,
               _include=['QuestionnaireResponse:subject', 'QuestionnaireResponse:based-on'])
    for e in _pages(url, headers):
        r = e.get('resource') or {}
        if r.get('resourceType') == 'QuestionnaireResponse' and (e.get('search') or {}).get('mode', 'match') == 'match':
            found.append(r)
        elif r.get('resourceType'):
            included[f"{r['resourceType']}/{r.get('id')}"] = r
    return found, included


def scan(base, headers, urls):
    """Subject types, campaigns (grouped by parent) and statuses of the
    responses to ``urls``, with counts per (form, subject, campaign, status)."""
    found, inc = _responses(base, headers, urls)
    camps, cells = {}, {}
    for r in found:
        st_key, st_label = subject_type(inc.get(_ref(r.get('subject'))))
        based = [_ref(b) for b in r.get('basedOn', [])] or ['']
        for cref in based:
            if cref and cref not in camps and cref in inc:
                camps[cref] = _campaign(inc[cref])
            key = (r.get('questionnaire', ''), st_key, cref, r.get('status', ''))
            cell = cells.setdefault(key, {'n': 0, 'label': st_label})
            cell['n'] += 1
    parents = {c['parent'] for c in camps.values() if c['parent'] and c['parent'] not in camps}
    if parents:
        ids = ','.join(p.split('/', 1)[1] for p in parents)
        for e in _pages(_url(base, 'CarePlan', _id=ids, _count=PAGE), headers):
            cp = e.get('resource') or {}
            camps[f"CarePlan/{cp.get('id')}"] = _campaign(cp)
    return {
        'cells': [{'form': k[0], 'subject': k[1], 'subject_label': v['label'], 'campaign': k[2],
                   'status': k[3], 'n': v['n']} for k, v in cells.items()],
        'campaigns': camps,
    }


# ---- pull ------------------------------------------------------------------

def _answers(items, out=None):
    """linkId → first answer value, from anywhere in the item tree."""
    out = {} if out is None else out
    for it in items or []:
        for a in it.get('answer', []):
            for k in ('valueString', 'valueDecimal', 'valueInteger', 'valueText'):
                if k in a and it.get('linkId') not in out:
                    out[it['linkId']] = a[k]
            _answers(a.get('item'), out)
        _answers(it.get('item'), out)
    return out


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def read_point(ans, mapping):
    """(name, lat, lon) from one response, or None when any part is missing
    or out of range. The same item for lat and lon is an ODK geopoint string."""
    name = str(ans.get(mapping['name'], '') or '').strip()
    if mapping['lat'] == mapping['lon']:
        parts = str(ans.get(mapping['lat'], '') or '').split()
        lat, lon = (_num(parts[0]), _num(parts[1])) if len(parts) >= 2 else (None, None)
    else:
        lat, lon = _num(ans.get(mapping['lat'])), _num(ans.get(mapping['lon']))
    if not name or lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return name, lat, lon


def _norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', s).split())


def pull(base, headers, selection, mapping, form_titles=None):
    """Named places from the selected responses.

    ``selection``: {'forms': [url], 'subjects': [key], 'campaigns': [ref],
    'statuses': [status] or []}; ``mapping``: {form url: {name, lat, lon}}.
    Repeat visits (same normalised name, point rounded to 4 dp) are pooled.
    """
    form_titles = form_titles or {}
    forms = [u for u in selection.get('forms', []) if u in mapping]
    if not forms:
        raise IcrError('Map at least one questionnaire before pulling')
    subjects, camps = set(selection.get('subjects') or []), set(selection.get('campaigns') or [])
    statuses = set(selection.get('statuses') or [])
    found, inc = _responses(base, headers, forms)
    titles = {ref: _campaign(cp)['title'] for ref, cp in inc.items() if ref.startswith('CarePlan/')}
    places, seen = [], {}
    read = skipped = pooled = 0
    for r in found:
        if subjects and subject_type(inc.get(_ref(r.get('subject'))))[0] not in subjects:
            continue
        based = [_ref(b) for b in r.get('basedOn', [])]
        if camps and not camps.intersection(based or ['']):
            continue
        if statuses and r.get('status') not in statuses:
            continue
        read += 1
        form = r.get('questionnaire', '')
        pt = read_point(_answers(r.get('item')), mapping[form]) if form in mapping else None
        if not pt:
            skipped += 1
            continue
        name, lat, lon = pt
        key = (_norm(name), round(lat, 4), round(lon, 4))
        if key in seen:
            pooled += 1
            continue
        seen[key] = True
        places.append({'point_id': 'qr:' + r.get('id', ''), 'name': name,
                       'latitude': lat, 'longitude': lon, 'source': 'icr-forms',
                       'form': form_titles.get(form, form.rsplit('/', 1)[-1]),
                       'campaign': titles.get(based[0], '') if based else '',
                       'authored': str(r.get('authored', ''))[:10]})
    return {'places': places, 'read': read, 'skipped': skipped, 'pooled': pooled}
