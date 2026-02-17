"""
Geometry repair and column-flattening utilities for geospatial DataFrames.
"""

from shapely.validation import make_valid


def fix_geometry(gdf):
    """Repair invalid geometries in a GeoDataFrame.

    Attempts ``buffer(0)`` first (fast, handles most self-intersections),
    then falls back to ``shapely.validation.make_valid``. Rows whose
    geometry cannot be salvaged are dropped.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Input GeoDataFrame (not modified in place).

    Returns
    -------
    geopandas.GeoDataFrame
        Copy with repaired geometries; rows with unfixable geometry removed.
    """
    fixed_geoms = []
    for geom in gdf.geometry:
        if geom.is_valid:
            fixed_geoms.append(geom)
        else:
            try:
                fixed_geom = geom.buffer(0)
                if not fixed_geom.is_valid:
                    fixed_geom = make_valid(geom)
                fixed_geoms.append(fixed_geom)
            except Exception as e:
                print("Failed to fix geometry:", e)
                fixed_geoms.append(None)
    gdf = gdf.copy()
    gdf["geometry"] = fixed_geoms
    return gdf.dropna(subset=["geometry"])


def flatten_column(gdf, column):
    """Replace list-valued cells in *column* with their first element.

    Shapefiles cannot store list-type attributes. This helper converts any
    list cells to their first element (or ``None`` if empty), leaving
    non-list cells untouched.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Modified **in place** and returned.
    column : str
        Column name to flatten.

    Returns
    -------
    geopandas.GeoDataFrame
        The same GeoDataFrame, modified in place.
    """
    if column in gdf.columns and gdf[column].apply(lambda x: isinstance(x, list)).any():
        gdf[column] = gdf[column].apply(lambda x: x[0] if isinstance(x, list) and x else None)
    return gdf
