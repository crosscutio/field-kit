"""Geography-scoped gazetteer downloads for the Geocoding GUI.

Builds a small local place reference (GeoNames populated places + OSM place
nodes via the Overpass API) for a user-selected country and admin1 areas,
caching downloads in the shared ``gazetteer/data/`` layout so the GUI and the
batch scripts (``gazetteer/scripts/``) reuse each other's files.

Normalization, the place-type whitelist, and the dedup key mirror
``gazetteer/scripts/merge.py`` and ``build_osm.py`` — keep them in sync.

HTTP uses stdlib urllib only (no extra dependencies). shapely is imported
lazily and reported as a friendly error when missing.
"""

import csv
import hashlib
import json
import os
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

USER_AGENT = 'match-bot-gazetteer/1.0 (https://github.com/crosscut/field-kit)'
GEOBOUNDARIES_API = 'https://www.geoboundaries.org/api/current/gbOpen/{iso3}/{level}/'
GEONAMES_DUMP = 'https://download.geonames.org/export/dump/{iso2}.zip'
DEFAULT_OVERPASS = 'https://overpass-api.de/api/interpreter'

# Same whitelist as gazetteer/scripts/build_osm.py
PLACE_TYPES = ('city', 'town', 'village', 'hamlet', 'suburb',
               'locality', 'isolated_dwelling', 'neighbourhood')

CANONICAL_COLUMNS = ['point_id', 'name', 'latitude', 'longitude', 'admin1',
                     'admin2', 'source', 'iso3', 'feature_code', 'population',
                     'orig_id']

SOURCE_RANK = {'user': 0, 'geonames': 1, 'osm': 2}


class GazetteerError(Exception):
    """User-presentable gazetteer failure."""


def gazetteer_dir():
    """Root of the shared gazetteer data tree (env-overridable)."""
    override = os.environ.get('MATCH_BOT_GAZETTEER_DIR')
    if override:
        return Path(override)
    # match_bot/gazetteer.py -> match-bot/gazetteer/
    return Path(__file__).resolve().parent.parent / 'gazetteer'


def _data_dir(sub):
    d = gazetteer_dir() / 'data' / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def _download(url, dest, timeout=60):
    """Download url to dest atomically (tmp file + rename)."""
    tmp = dest.with_suffix(dest.suffix + '.tmp')
    with _http_get(url, timeout=timeout) as resp, open(tmp, 'wb') as f:
        f.write(resp.read())
    tmp.rename(dest)


def norm_name(s):
    """ASCII-fold, lowercase, alnum-only (same as gazetteer/scripts/merge.py)."""
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return ''.join(c for c in s.lower() if c.isalnum())


# ---------------------------------------------------------------------------
# Country list
# ---------------------------------------------------------------------------

def list_countries():
    """Bundled global country list: [{'iso3', 'iso2', 'name'}, ...]."""
    path = Path(__file__).resolve().parent / 'data' / 'countries_global.csv'
    countries = []
    with open(path, newline='', encoding='utf-8') as f:
        rows = (line for line in f if not line.startswith('#'))
        for row in csv.DictReader(rows):
            countries.append({'iso3': row['iso3'], 'iso2': row['iso2'],
                              'name': row['name']})
    return countries


def iso2_for(iso3):
    for c in list_countries():
        if c['iso3'] == iso3:
            return c['iso2']
    raise GazetteerError(f'Unknown country code "{iso3}"')


# ---------------------------------------------------------------------------
# geoBoundaries admin polygons
# ---------------------------------------------------------------------------

def ensure_boundaries(iso3, level='ADM1', log=None):
    """Download (or reuse) the geoBoundaries GeoJSON for a country/level."""
    dest = _data_dir('boundaries') / f'{iso3}_{level}.geojson'
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    api_url = GEOBOUNDARIES_API.format(iso3=iso3, level=level)
    try:
        with _http_get(api_url, timeout=30) as resp:
            meta = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise GazetteerError(
                f'geoBoundaries has no {level} boundaries for {iso3}')
        raise
    url = meta.get('simplifiedGeometryGeoJSON') or meta.get('gjDownloadURL')
    if not url:
        raise GazetteerError(
            f'geoBoundaries returned no download link for {iso3} {level}')
    if log:
        log(f'Downloading {iso3} {level} boundaries...')
    _download(url, dest, timeout=120)
    return dest


def _load_boundary_features(iso3, level='ADM1'):
    path = ensure_boundaries(iso3, level)
    with open(path, encoding='utf-8') as f:
        gj = json.load(f)
    return gj.get('features', [])


def admin1_names(iso3):
    """Sorted unique shapeNames from the country's ADM1 boundaries."""
    names = {str(f.get('properties', {}).get('shapeName', '')).strip()
             for f in _load_boundary_features(iso3)}
    return sorted(n for n in names if n)


def admin1_shapes(iso3, names=None, level='ADM1'):
    """{shapeName: shapely geometry} for the selected (or all) admin areas."""
    try:
        from shapely.geometry import shape
    except ImportError:
        raise GazetteerError(
            'shapely is required for gazetteer area filtering — install the '
            '[spatial] extra')
    wanted = set(names) if names else None
    shapes = {}
    for feat in _load_boundary_features(iso3, level):
        name = str(feat.get('properties', {}).get('shapeName', '')).strip()
        if not name or (wanted is not None and name not in wanted):
            continue
        geom = shape(feat['geometry'])
        shapes[name] = shapes[name].union(geom) if name in shapes else geom
    if wanted is not None:
        missing = wanted - set(shapes)
        if missing:
            raise GazetteerError(
                f'Admin areas not found in {iso3} boundaries: '
                f'{", ".join(sorted(missing))}')
    return shapes


# ---------------------------------------------------------------------------
# GeoNames
# ---------------------------------------------------------------------------

def ensure_geonames(iso2, log=None):
    """Download (or reuse) the per-country GeoNames dump, extracted to .txt."""
    gn_dir = _data_dir('geonames')
    txt = gn_dir / f'{iso2}.txt'
    if txt.exists() and txt.stat().st_size > 0:
        return txt
    zip_path = gn_dir / f'{iso2}.zip'
    if not (zip_path.exists() and zip_path.stat().st_size > 0):
        if log:
            log(f'Downloading GeoNames dump for {iso2}...')
        try:
            _download(GEONAMES_DUMP.format(iso2=iso2), zip_path, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise GazetteerError(f'GeoNames has no dump for "{iso2}"')
            raise
    with zipfile.ZipFile(zip_path) as z:
        z.extract(f'{iso2}.txt', gn_dir)
    return txt


def parse_geonames(txt_path, iso3):
    """Populated places (feature class P) from a GeoNames country dump.

    admin1/admin2 are left blank — they are assigned from geoBoundaries
    polygons in clip_to_admin so both sources share one admin vocabulary
    (same decision as gazetteer/scripts/retag_admin.py).
    """
    rows = []
    with open(txt_path, encoding='utf-8') as f:
        for line in f:
            c = line.rstrip('\n').split('\t')
            if len(c) < 15 or c[6] != 'P':
                continue
            rows.append({
                'point_id': f'gn:{c[0]}',
                'name': c[1],
                'latitude': c[4],
                'longitude': c[5],
                'admin1': '',
                'admin2': '',
                'source': 'geonames',
                'iso3': iso3,
                'feature_code': c[7],
                'population': c[14],
                'orig_id': c[0],
            })
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS).astype(str)


# ---------------------------------------------------------------------------
# OSM via Overpass
# ---------------------------------------------------------------------------

def overpass_cache_path(iso3, admin_name, geom):
    bounds_hash = hashlib.sha1(repr(geom.bounds).encode()).hexdigest()[:8]
    d = _data_dir('overpass') / iso3
    d.mkdir(parents=True, exist_ok=True)
    slug = norm_name(admin_name) or 'all'
    return d / f'{slug}.{bounds_hash}.json'


def _overpass_cached(path):
    if not (path.exists() and path.stat().st_size > 0):
        return False
    try:
        with open(path, encoding='utf-8') as f:
            return 'elements' in json.load(f)
    except (json.JSONDecodeError, OSError):
        return False


# Overpass politeness: minimum gap between consecutive requests, and the
# HTTP statuses worth retrying (rate limit + gateway congestion).
_OVERPASS_MIN_GAP = 3.0
_OVERPASS_RETRY_CODES = (429, 502, 503, 504)
_OVERPASS_BACKOFFS = (0, 15, 45, 90)
_last_overpass_request = [0.0]


def _overpass_throttle():
    elapsed = time.time() - _last_overpass_request[0]
    if elapsed < _OVERPASS_MIN_GAP:
        time.sleep(_OVERPASS_MIN_GAP - elapsed)
    _last_overpass_request[0] = time.time()


def fetch_overpass_admin(iso3, admin_name, geom, log=None,
                         endpoint=None, timeout=180):
    """Fetch OSM place nodes for one admin area's bbox (cached).

    Returns the cache path. Point-in-polygon refinement happens later in
    clip_to_admin; the query uses the bbox to keep the URL small. Requests
    are throttled and retried with backoff (honoring Retry-After) because
    consecutive large queries trip the public server's rate limits.
    """
    cache = overpass_cache_path(iso3, admin_name, geom)
    if _overpass_cached(cache):
        if log:
            log(f'OSM {admin_name}: cached')
        return cache

    endpoint = endpoint or os.environ.get('OVERPASS_URL', DEFAULT_OVERPASS)
    minx, miny, maxx, maxy = geom.bounds
    query = (
        '[out:json][timeout:120];'
        f'node["place"~"^({"|".join(PLACE_TYPES)})$"]["name"]'
        f'({miny},{minx},{maxy},{maxx});'
        'out body;'
    )
    data = ('data=' + urllib.parse.quote(query)).encode()

    last_err = None
    for backoff in _OVERPASS_BACKOFFS:
        if backoff:
            time.sleep(backoff)
        _overpass_throttle()
        try:
            req = urllib.request.Request(
                endpoint, data=data, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
            tmp = cache.with_suffix('.tmp')
            tmp.write_bytes(payload)
            tmp.rename(cache)
            if not _overpass_cached(cache):
                cache.unlink(missing_ok=True)
                raise GazetteerError(
                    f'Overpass returned an unusable response for {admin_name}')
            return cache
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in _OVERPASS_RETRY_CODES:
                break
            retry_after = e.headers.get('Retry-After') if e.headers else None
            if retry_after:
                try:
                    time.sleep(min(float(retry_after), 120))
                except ValueError:
                    pass
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
    raise GazetteerError(
        f'Overpass unavailable for {admin_name} ({last_err})')


def parse_overpass(json_path, iso3):
    """OSM place nodes from a cached Overpass response, canonical schema."""
    with open(json_path, encoding='utf-8') as f:
        payload = json.load(f)
    rows = []
    for el in payload.get('elements', []):
        tags = el.get('tags', {})
        name = tags.get('name', '')
        place = tags.get('place', '')
        if not name or place not in PLACE_TYPES:
            continue
        if 'lat' not in el or 'lon' not in el:
            continue
        rows.append({
            'point_id': f'osm:{el["id"]}',
            'name': name,
            'latitude': str(el['lat']),
            'longitude': str(el['lon']),
            'admin1': '',
            'admin2': '',
            'source': 'osm',
            'iso3': iso3,
            'feature_code': place,
            'population': tags.get('population', ''),
        })
    df = pd.DataFrame(rows, columns=CANONICAL_COLUMNS[:-1]).astype(str)
    df['orig_id'] = df['point_id'].str.replace('osm:', '', regex=False)
    return df[CANONICAL_COLUMNS]


# ---------------------------------------------------------------------------
# Geometry scoping / merging / blending
# ---------------------------------------------------------------------------

def clip_to_admin(df, shapes):
    """Assign admin1 from the selected polygons; drop points outside all.

    shapes: {admin_name: shapely geometry}. Iterative prepared-geometry
    containment — selections are a handful of polygons, fast enough without
    a geopandas sjoin.
    """
    if df.empty:
        return df.copy()
    try:
        from shapely.geometry import Point
        from shapely.prepared import prep
    except ImportError:
        raise GazetteerError(
            'shapely is required for gazetteer area filtering — install the '
            '[spatial] extra')
    prepared = {name: prep(geom) for name, geom in shapes.items()}
    lat = pd.to_numeric(df['latitude'], errors='coerce')
    lon = pd.to_numeric(df['longitude'], errors='coerce')
    admin_out = []
    for la, lo in zip(lat, lon):
        hit = ''
        if pd.notna(la) and pd.notna(lo):
            pt = Point(lo, la)
            for name, pgeom in prepared.items():
                if pgeom.contains(pt):
                    hit = name
                    break
        admin_out.append(hit)
    out = df.copy()
    out['admin1'] = admin_out
    return out[out['admin1'] != ''].reset_index(drop=True)


def _dedup_key(df, include_iso3=True):
    lat = pd.to_numeric(df['latitude'], errors='coerce').round(3)
    lon = pd.to_numeric(df['longitude'], errors='coerce').round(3)
    key = pd.DataFrame({
        'k_name': df['name'].map(norm_name),
        'k_lat': lat,
        'k_lon': lon,
    }, index=df.index)
    if include_iso3:
        key.insert(0, 'k_iso3', df['iso3'])
        key['k_admin2'] = df['admin2'].map(norm_name)
    return key


def merge_sources(geonames_df, osm_df):
    """Concatenate + dedup; GeoNames wins (same key as scripts/merge.py)."""
    combined = pd.concat([geonames_df, osm_df], ignore_index=True)
    if combined.empty:
        return combined
    key = _dedup_key(combined)
    keep = ~key.duplicated()
    return combined[keep.values].reset_index(drop=True)


def blend_points(user_df, gaz_df):
    """Combine canonical-schema user and gazetteer points into one pool.

    Key: (norm(name), round(lat,3), round(lon,3)) — ~110 m cell. Priority on
    collision: user > geonames > osm. Rows with unparseable coordinates:
    user rows are kept (still fuzzy-matchable by name), gazetteer rows are
    dropped (coordinates are their entire value).

    Returns (combined_df, stats).
    """
    frames = [df for df in (user_df, gaz_df) if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame(columns=CANONICAL_COLUMNS), {
            'user': 0, 'geonames': 0, 'osm': 0,
            'dropped_duplicates': 0, 'dropped_invalid': 0}
    combined = pd.concat(frames, ignore_index=True)

    lat = pd.to_numeric(combined['latitude'], errors='coerce')
    lon = pd.to_numeric(combined['longitude'], errors='coerce')
    invalid = lat.isna() | lon.isna() | lat.abs().gt(90) | lon.abs().gt(180)
    drop_invalid = invalid & (combined['source'] != 'user')
    n_invalid = int(drop_invalid.sum())
    combined = combined[~drop_invalid].reset_index(drop=True)

    rank = combined['source'].map(SOURCE_RANK).fillna(9)
    combined = combined.iloc[rank.sort_values(kind='stable').index].reset_index(drop=True)

    key = _dedup_key(combined, include_iso3=False)
    dup = key.duplicated()
    n_dup = int(dup.sum())
    combined = combined[~dup.values].reset_index(drop=True)

    stats = {
        'user': int((combined['source'] == 'user').sum()),
        'geonames': int((combined['source'] == 'geonames').sum()),
        'osm': int((combined['source'] == 'osm').sum()),
        'dropped_duplicates': n_dup,
        'dropped_invalid': n_invalid,
    }
    return combined[CANONICAL_COLUMNS], stats


def normalize_user_points(df, id_col, name_col, lat_col, lon_col,
                          hier_cols=None):
    """User CSV -> canonical schema. hier_cols: [(column, label), ...] — the
    first two mapped columns fill admin1/admin2 so hierarchy grouping keeps
    working on the combined file."""
    out = pd.DataFrame(index=df.index, columns=CANONICAL_COLUMNS, dtype=object)
    out['orig_id'] = df[id_col].astype(str)
    out['point_id'] = 'user:' + df[id_col].astype(str)
    out['name'] = df[name_col].astype(str) if name_col in df.columns else ''
    out['latitude'] = df[lat_col].astype(str) if lat_col in df.columns else ''
    out['longitude'] = df[lon_col].astype(str) if lon_col in df.columns else ''
    out['source'] = 'user'
    out['iso3'] = ''
    out['feature_code'] = ''
    out['population'] = ''
    out['admin1'] = ''
    out['admin2'] = ''
    for i, (col, _label) in enumerate(hier_cols or []):
        if i > 1:
            break
        if col in df.columns:
            out[f'admin{i + 1}'] = df[col].astype(str)
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def count_geonames_in_selection(iso3, admin_names_sel, log=None):
    """Preview: GeoNames populated-place count inside the selected areas.

    Downloads boundaries/GeoNames on cache miss (small, seconds); no Overpass.
    """
    iso2 = iso2_for(iso3)
    shapes = admin1_shapes(iso3, admin_names_sel or None)
    txt = ensure_geonames(iso2, log=log)
    gn = parse_geonames(txt, iso3)
    clipped = clip_to_admin(gn, shapes)
    return {'geonames_count': int(len(clipped)),
            'admin_count': len(shapes)}


def assemble_gazetteer(iso3, admin_names_sel, skip_osm_admins=None, log=None):
    """Build the scoped gazetteer from local caches only (no network).

    Expects boundaries + GeoNames + per-admin Overpass caches to exist
    (fetched by the preview / fetch-osm steps). Missing Overpass caches for
    non-skipped admins raise; skipped admins get GeoNames only.
    """
    skip = set(skip_osm_admins or [])
    iso2 = iso2_for(iso3)
    shapes = admin1_shapes(iso3, admin_names_sel or None)

    gn_txt = _data_dir('geonames') / f'{iso2}.txt'
    if gn_txt.exists():
        gn = clip_to_admin(parse_geonames(gn_txt, iso3), shapes)
    else:
        gn = pd.DataFrame(columns=CANONICAL_COLUMNS)
        if log:
            log(f'GeoNames data missing for {iso2} — OSM only')

    osm_frames = []
    for name, geom in shapes.items():
        if name in skip:
            continue
        cache = overpass_cache_path(iso3, name, geom)
        if not _overpass_cached(cache):
            raise GazetteerError(
                f'OSM data for "{name}" has not been fetched yet')
        osm_frames.append(parse_overpass(cache, iso3))
    if osm_frames:
        osm = clip_to_admin(pd.concat(osm_frames, ignore_index=True), shapes)
    else:
        osm = pd.DataFrame(columns=CANONICAL_COLUMNS)

    merged = merge_sources(gn, osm)
    if log:
        log(f'Gazetteer: {int((merged["source"] == "geonames").sum())} GeoNames '
            f'+ {int((merged["source"] == "osm").sum())} OSM places '
            f'in {len(shapes)} area(s)')
    return merged
