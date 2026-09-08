"""Point-in-polygon tagging of places with admin boundary names.

Boundaries are the source of truth for a place's admin units: whatever admin
columns the places CSV carries are ignored. Points that fall just outside
every polygon (simplified coastlines, border noise) snap to the nearest
polygon within ``SNAP_DEG``.
"""

from typing import Dict, List, Tuple

import pandas as pd

SNAP_DEG = 0.02   # ~2 km


def _shapely():
    try:
        from shapely import STRtree
        from shapely.geometry import Point, shape
    except ImportError as e:
        raise ImportError('shapely is required for boundary tagging — install the [spatial] extra') from e
    return STRtree, Point, shape


def polygons_from_geojson(geojson: Dict, name_property: str) -> Tuple[List[str], list]:
    """Parallel lists (names, shapely geometries); features without a name are skipped."""
    _, _, shape = _shapely()
    names, geoms = [], []
    for ft in geojson.get('features', []):
        name = str((ft.get('properties') or {}).get(name_property, '') or '').strip()
        geom = ft.get('geometry')
        if not name or not geom:
            continue
        try:
            geoms.append(shape(geom))
            names.append(name)
        except Exception:
            continue
    return names, geoms


def tag_points(lats, lons, names: List[str], geoms: list) -> Tuple[List[str], Dict[str, int]]:
    """Polygon name for each (lat, lon); '' when nothing contains or is near it.

    Returns (tags, stats) with stats = {inside, snapped, outside, invalid}.
    """
    STRtree, Point, _ = _shapely()
    tags: List[str] = []
    stats = {'inside': 0, 'snapped': 0, 'outside': 0, 'invalid': 0}
    if not geoms:
        return [''] * len(lats), {**stats, 'outside': len(lats)}
    tree = STRtree(geoms)
    lat = pd.to_numeric(pd.Series(lats), errors='coerce')
    lon = pd.to_numeric(pd.Series(lons), errors='coerce')
    for la, lo in zip(lat, lon):
        if pd.isna(la) or pd.isna(lo):
            tags.append('')
            stats['invalid'] += 1
            continue
        pt = Point(float(lo), float(la))
        hit = ''
        for idx in tree.query(pt, predicate='intersects'):
            hit = names[int(idx)]
            break
        if hit:
            stats['inside'] += 1
        else:
            near = tree.query_nearest(pt, max_distance=SNAP_DEG, return_distance=False)
            if len(near):
                hit = names[int(near[0])]
                stats['snapped'] += 1
            else:
                stats['outside'] += 1
        tags.append(hit)
    return tags, stats
