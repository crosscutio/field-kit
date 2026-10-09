"""Read an Integrated Campaign Registry (ICR) SDI: the STAC catalog and the
GeoParquet location files that kiln publishes from the registry.

Layout (kiln + Portolan):
``catalog.json`` → child catalogs → a Collection whose ``partition:keys`` are
``country / geom_type / type``, with one ``rel:item`` per partition file
(``.../country=NGA/geom_type=point/type=settlement/part-0.parquet``).
Admin units are ``type=admin-unit`` polygons; every other row carries its
admin ancestry denormalised (``admin1_name``/``admin1_code``, ``ancestor_ids``).

JSON is fetched with stdlib urllib; parquet is queried in place with DuckDB
(``httpfs``), so only the columns and row groups a query needs are read.
The collection's ``table:column_statistics`` are not used — kiln writes them
misaligned with the columns.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = 'match-bot-icr/1.0 (https://github.com/crosscut/field-kit)'
AUTH_MODES = ('none', 'bearer', 'basic', 'header')
SITE_TYPES = ('settlement', 'facility', 'school')


class IcrError(Exception):
    """User-presentable registry failure."""


def auth_headers(mode, secret):
    """HTTP headers for one sign-in mode (shared with the FHIR client)."""
    mode = mode or 'none'
    secret = (secret or '').strip()
    if mode not in AUTH_MODES:
        raise IcrError(f'Unknown sign-in mode {mode!r}')
    if mode == 'none':
        return {}
    if not secret:
        raise IcrError('Enter the sign-in details, or choose "No sign-in"')
    if mode == 'bearer':
        return {'Authorization': 'Bearer ' + secret}
    if mode == 'basic':
        if ':' not in secret:
            raise IcrError('Enter the username and password as username:password')
        return {'Authorization': 'Basic ' + base64.b64encode(secret.encode()).decode()}
    name, sep, value = secret.partition(':')
    if not sep or not name.strip():
        raise IcrError('Enter a custom header as one line, for example "X-Api-Key: value"')
    return {name.strip(): value.strip()}


def http_json(url, headers=None, timeout=60):
    """GET a JSON document; HTTP errors become IcrError with a useful hint."""
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                               'Accept': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise IcrError(f'{url} refused access ({e.code}). Check the sign-in.') from e
        raise IcrError(f'{url} answered {e.code} {e.reason}') from e
    except ValueError as e:
        raise IcrError(f'{url} did not return JSON') from e


def _links(doc, rel):
    return [l['href'] for l in doc.get('links', []) if l.get('rel') == rel and l.get('href')]


def _find_collection(url, headers, depth=0):
    """Depth-first walk from a catalog to the first partitioned collection."""
    doc = http_json(url, headers)
    if not isinstance(doc, dict):
        return None
    if doc.get('type') == 'Collection' and doc.get('partition:keys'):
        return url, doc
    if depth >= 4:
        return None
    for href in _links(doc, 'child'):
        found = _find_collection(urllib.parse.urljoin(url, href), headers, depth + 1)
        if found:
            return found
    return None


def _partition_values(href):
    """{'country': 'NGA', 'geom_type': 'point', 'type': 'settlement'} from a hive path."""
    out = {}
    for part in urllib.parse.urlparse(href).path.split('/'):
        if '=' in part and not part.endswith('.json') and not part.endswith('.parquet'):
            k, v = part.split('=', 1)
            out[k] = v
    return out


def read_catalog(url, headers=None):
    """Walk the catalog to the location collection and describe its files.

    Returns ``{title, providers, license, updated, collection, items}`` where
    each item is ``{country, geom_type, type, href, count, datetime, checksum,
    size, title}``.
    """
    url = (url or '').strip()
    if not url:
        raise IcrError('Enter the catalog address')
    root = http_json(url, headers)
    if not isinstance(root, dict) or root.get('type') not in ('Catalog', 'Collection') or 'stac_version' not in root:
        raise IcrError(f'{url} is not a STAC catalog')
    found = _find_collection(url, headers)
    if not found:
        raise IcrError('This catalog has no partitioned location collection (parquet/locations)')
    coll_url, coll = found
    items = []
    for href in _links(coll, 'item'):
        item_url = urllib.parse.urljoin(coll_url, href)
        item = http_json(item_url, headers)
        props = item.get('properties', {})
        data = (item.get('assets') or {}).get('data') or {}
        if not data.get('href'):
            continue
        data_href = urllib.parse.urljoin(item_url, data['href'])
        part = _partition_values(data_href)
        items.append({
            'country': part.get('country', ''), 'geom_type': part.get('geom_type', ''),
            'type': part.get('type', ''), 'href': data_href,
            'count': props.get('geoparquet:feature_count'),
            'datetime': props.get('datetime') or item.get('datetime'),
            'checksum': data.get('file:checksum'), 'size': data.get('file:size'),
            'title': props.get('title') or item.get('title') or '',
        })
    if not items:
        raise IcrError('The location collection lists no files')
    return {
        'title': root.get('title') or coll.get('title') or 'Registry catalog',
        'providers': [p.get('name') for p in coll.get('providers', []) if p.get('name')],
        'license': coll.get('license', ''),
        'updated': coll.get('updated') or root.get('updated') or max(
            (i['datetime'] or '' for i in items), default=''),
        'collection': coll_url,
        'items': items,
    }


def countries(catalog):
    """Countries published, named from the item titles ("Nigeria — settlements")."""
    out = {}
    for it in catalog['items']:
        c = out.setdefault(it['country'], {'code': it['country'], 'name': it['country'],
                                           'types': {}, 'admin_units': 0, 'admin_href': None})
        title = it.get('title') or ''
        if ' — ' in title:
            c['name'] = title.split(' — ')[0].strip() or c['name']
        if it['type'] == 'admin-unit':
            c['admin_units'] = it['count'] or 0
            c['admin_href'] = it['href']
        elif it['geom_type'] == 'point' and it['type'] in SITE_TYPES:
            c['types'][it['type']] = {'count': it['count'], 'href': it['href'],
                                      'datetime': it['datetime'], 'checksum': it['checksum']}
    return sorted(out.values(), key=lambda c: c['name'])


# ---- DuckDB ---------------------------------------------------------------

def _sql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def _src(href):
    """read_parquet() argument: local path for file:// URLs, else the URL."""
    if href.startswith('file://'):
        return _sql_str(urllib.request.url2pathname(urllib.parse.urlparse(href).path))
    return _sql_str(href)


def duck(headers=None):
    """A DuckDB connection that sends ``headers`` on every HTTP(S) read."""
    try:
        import duckdb
    except ImportError as e:
        raise IcrError('Reading the registry needs DuckDB 1.5+: pip install "duckdb>=1.5"') from e
    conn = duckdb.connect()
    try:
        conn.execute('LOAD httpfs')
    except Exception:
        try:
            conn.execute('INSTALL httpfs')
            conn.execute('LOAD httpfs')
        except Exception as e:
            raise IcrError("DuckDB's httpfs extension is missing and could not be downloaded "
                           f'from extensions.duckdb.org: {e}') from e
    hdrs = {'User-Agent': USER_AGENT, **(headers or {})}
    pairs = ', '.join(f'{_sql_str(k)}: {_sql_str(v)}' for k, v in hdrs.items())
    conn.execute(f'CREATE SECRET icr_http (TYPE http, EXTRA_HTTP_HEADERS MAP {{{pairs}}})')
    return conn


def _query(conn, sql, params=None):
    try:
        cur = conn.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        msg = str(e)
        if '401' in msg or '403' in msg:
            raise IcrError('The registry refused access to its files. Check the sign-in.') from e
        raise IcrError(f'Could not read the registry files: {msg.splitlines()[0]}') from e


def admin_units(conn, href):
    """Admin units of one country: id, name, pcode, admin_level, part_of, wkb."""
    return _query(conn, f'''
        SELECT id, name, pcode, admin_level, part_of, geometry AS wkb
        FROM read_parquet({_src(href)})
        WHERE admin_level IS NOT NULL
        ORDER BY admin_level, name''')


def admin_tree(units, site_counts=None):
    """Level-1 units with their level-2 children, plus site counts if given."""
    site_counts = site_counts or {}
    def node(u):
        c = site_counts.get(u['id'], {})
        return {'id': u['id'], 'name': u['name'], 'code': u['pcode'],
                'sites': c.get('sites', 0), 'missing': c.get('missing', 0), 'children': []}
    top = {u['id']: node(u) for u in units if u['admin_level'] == 1}
    for u in units:
        if u['admin_level'] == 2 and u['part_of'] in top:
            top[u['part_of']]['children'].append(node(u))
    return sorted(top.values(), key=lambda n: n['name'] or '')


def site_counts(conn, href):
    """{admin unit id: {sites, missing}} — missing = no position yet."""
    rows = _query(conn, f'''
        SELECT a AS id, count(*) AS sites,
               count(*) FILTER (WHERE position_latitude IS NULL OR position_longitude IS NULL) AS missing
        FROM (SELECT unnest(ancestor_ids) AS a, position_latitude, position_longitude
              FROM read_parquet({_src(href)}))
        GROUP BY a''')
    return {r['id']: {'sites': int(r['sites']), 'missing': int(r['missing'])} for r in rows}


def type_summary(conn, href):
    """Rows on record and rows without coordinates for one site partition."""
    r = _query(conn, f'''
        SELECT count(*) AS total,
               count(*) FILTER (WHERE position_latitude IS NULL OR position_longitude IS NULL) AS missing,
               count(*) FILTER (WHERE status = 'active') AS active
        FROM read_parquet({_src(href)})''')[0]
    return {k: int(v or 0) for k, v in r.items()}


def read_sites(conn, href, status='active', unit_ids=None):
    """Site rows in scope. ``unit_ids`` are admin-unit ids at any level (matched
    against each row's ancestors); None means the whole country."""
    where, params = [], []
    if status == 'active':
        where.append("status = 'active'")
    if unit_ids:
        where.append('list_has_any(ancestor_ids, ?::VARCHAR[])')
        params.append(list(unit_ids))
    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    return _query(conn, f'''
        SELECT id, name, status, admin1_name, admin1_code, admin2_name, admin2_code,
               position_latitude, position_longitude
        FROM read_parquet({_src(href)}) {clause}
        ORDER BY admin1_name, admin2_name, name''', params)


def boundaries_geojson(units, level):
    """GeoJSON FeatureCollection of the admin units at one level."""
    try:
        import shapely
        from shapely.geometry import mapping
    except ImportError as e:
        raise IcrError('Admin boundaries need shapely (pip install geopandas)') from e
    feats = []
    for u in units:
        if u['admin_level'] != level or u['wkb'] is None:
            continue
        geom = shapely.from_wkb(bytes(u['wkb']))
        feats.append({'type': 'Feature', 'geometry': mapping(geom),
                      'properties': {'name': u['name'], 'id': u['id'], 'pcode': u['pcode']}})
    return {'type': 'FeatureCollection', 'features': feats}
