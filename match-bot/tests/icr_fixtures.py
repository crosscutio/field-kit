"""A miniature ICR SDI on disk: a STAC catalog plus kiln-shaped GeoParquet
partitions, served through file:// URLs so the real reader code runs offline."""
import json
from pathlib import Path

import pandas as pd

duckdb = __import__('pytest').importorskip('duckdb')
shapely = __import__('pytest').importorskip('shapely')
from shapely.geometry import Point, box  # noqa: E402

# admin units: country → 2 regions → 3 districts (ids follow kiln's slug paths)
UNITS = [
    ('tst', 'Testland', 'TST', 0, None, box(0, 0, 4, 2)),
    ('tst-no', 'Nord', 'NO', 1, 'tst', box(0, 0, 2, 2)),
    ('tst-su', 'Sud', 'SU', 1, 'tst', box(2, 0, 4, 2)),
    ('tst-no-101', 'Alpha', '101', 2, 'tst-no', box(0, 0, 1, 2)),
    ('tst-no-102', 'Beta', '102', 2, 'tst-no', box(1, 0, 2, 2)),
    ('tst-su-201', 'Gamma', '201', 2, 'tst-su', box(2, 0, 4, 2)),
]
# settlements: name, district id, lat, lon, status (None coordinates = not yet located)
SITES = [
    ('Kofar', 'tst-no-101', 1.0, 0.5, 'active'),
    ('Gidan', 'tst-no-101', None, None, 'active'),
    ('Rimi', 'tst-no-102', 1.2, 1.5, 'active'),
    ('Old Rimi', 'tst-no-102', 1.3, 1.6, 'inactive'),
    ('Dawa', 'tst-su-201', None, None, 'active'),
]


def _write_parquet(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.register('df', df)
    con.execute(f"COPY df TO '{path.as_posix()}' (FORMAT parquet)")


def build_sdi(root: Path) -> str:
    """Write the catalog under ``root``; returns the catalog.json file:// URL."""
    unit = {u[0]: u for u in UNITS}
    loc = root / 'parquet' / 'locations'
    admin = pd.DataFrame([{
        'id': u[0], 'name': u[1], 'pcode': u[2], 'admin_level': u[3], 'part_of': u[4],
        'type': 'admin-unit', 'status': 'active', 'geometry': shapely.to_wkb(u[5]),
    } for u in UNITS])
    rows = []
    for i, (name, dist, lat, lon, status) in enumerate(SITES):
        d = unit[dist]
        r = unit[d[4]]
        rows.append({
            'id': f'site-{i + 1}', 'name': name, 'status': status, 'type': 'settlement',
            'part_of': dist, 'ancestor_ids': ['tst', r[0], dist],
            'admin1_name': r[1], 'admin1_code': r[2], 'admin2_name': d[1], 'admin2_code': d[2],
            'position_latitude': lat, 'position_longitude': lon,
            # kiln fills lat/lon even without a position; the reader must not use them
            'lat': lat if lat is not None else 1.0, 'lon': lon if lon is not None else 1.0,
            'geometry': shapely.to_wkb(Point(lon if lon is not None else 1.0, lat if lat is not None else 1.0)),
        })
    parts = {
        ('polygon', 'admin-unit'): admin,
        ('point', 'settlement'): pd.DataFrame(rows),
    }
    links = []
    for (geom, typ), df in parts.items():
        d = loc / 'country=TST' / f'geom_type={geom}' / f'type={typ}'
        _write_parquet(df, d / 'part-0.parquet')
        name = f'country=TST_geom_type={geom}_type={typ}.json'
        (d / name).write_text(json.dumps({
            'type': 'Feature', 'stac_version': '1.1.0', 'id': name[:-5],
            'properties': {'datetime': '2026-09-09T19:02:44Z', 'geoparquet:feature_count': len(df),
                           'title': f'Testland — {typ}s'},
            'assets': {'data': {'href': './part-0.parquet', 'file:checksum': 'abc123', 'file:size': 1}},
            'links': [],
        }))
        links.append({'rel': 'item', 'href': f'./country=TST/geom_type={geom}/type={typ}/{name}'})
    (loc / 'collection.json').write_text(json.dumps({
        'type': 'Collection', 'stac_version': '1.1.0', 'id': 'parquet/locations', 'license': 'CC-BY-4.0',
        'providers': [{'name': 'Test Registry'}], 'partition:keys': ['country', 'geom_type', 'type'],
        'table:column_statistics': {'position_longitude': {'min': 'garbage'}}, 'links': links,
    }))
    (root / 'parquet' / 'catalog.json').write_text(json.dumps({
        'type': 'Catalog', 'stac_version': '1.1.0', 'id': 'parquet',
        'links': [{'rel': 'child', 'href': './locations/collection.json'}]}))
    (root / 'catalog.json').write_text(json.dumps({
        'type': 'Catalog', 'stac_version': '1.1.0', 'id': 'icr', 'title': 'Test ICR',
        'links': [{'rel': 'child', 'href': './parquet/catalog.json'}]}))
    return (root / 'catalog.json').resolve().as_uri()
