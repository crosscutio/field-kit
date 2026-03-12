"""
Data loading for Match-Bot.

Loads CSV, SHP, GPKG, and GDB files into pandas DataFrames.
Only loads attribute data — no spatial operations needed for matching.
"""

from pathlib import Path
from typing import Optional

import pandas as pd


def load_dataset(path: str, fmt: Optional[str] = None, layer: Optional[str] = None) -> pd.DataFrame:
    """Load a dataset from file into a DataFrame (attributes only).

    Args:
        path: Path to the data file.
        fmt: File format hint (csv, shp, gpkg, gdb). If None, inferred from extension.
        layer: Layer name for multi-layer formats (gpkg, gdb).

    Returns:
        pandas DataFrame with attribute columns only (geometry dropped).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    if fmt is None:
        ext = p.suffix.lower()
        format_map = {
            '.csv': 'csv',
            '.geojson': 'geojson',
            '.shp': 'shp',
            '.gpkg': 'gpkg',
            '.gdb': 'gdb',
        }
        fmt = format_map.get(ext, ext.lstrip('.'))

    if fmt == 'csv':
        return pd.read_csv(p)

    # Spatial formats — load with geopandas then drop geometry
    try:
        import geopandas as gpd
    except ImportError:
        raise ImportError(
            "geopandas is required to read spatial files (shp/gpkg/gdb). "
            "Install it with: pip install geopandas"
        )

    kwargs = {}
    if layer:
        kwargs['layer'] = layer
    elif fmt == 'gpkg':
        # Auto-select first layer if not specified
        try:
            import fiona
            layers = fiona.listlayers(str(p))
            if layers:
                kwargs['layer'] = layers[0]
        except ImportError:
            pass  # Let geopandas handle it

    gdf = gpd.read_file(str(p), **kwargs)

    # Drop geometry column to return a plain DataFrame
    if 'geometry' in gdf.columns:
        return pd.DataFrame(gdf.drop(columns='geometry'))
    return pd.DataFrame(gdf)


def get_columns(path: str) -> list[str]:
    """Return attribute column names from a dataset file (any supported format)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == '.csv':
        return list(pd.read_csv(p, nrows=0).columns)
    try:
        import geopandas as gpd
    except ImportError:
        raise ImportError(
            "geopandas is required to read spatial files. "
            "Install it with: pip install geopandas"
        )
    gdf = gpd.read_file(str(p), rows=1)
    return [c for c in gdf.columns if c != 'geometry']
