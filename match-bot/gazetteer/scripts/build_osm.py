#!/usr/bin/env python3
"""Build a geocoded place-name reference from OSM (Geofabrik) country PBFs.

For each country:
  1. ogr2ogr extracts place nodes (city/town/village/hamlet/suburb/locality/...)
     from the .osm.pbf 'points' layer to a temp CSV with lon/lat.
  2. Points are spatial-joined to geoBoundaries ADM1 and ADM2 polygons to
     attach human-readable admin names.

Output: geocode/out/ref_osm.csv with columns matching the GeoNames reference:
  source, iso3, geonameid(=osm_id), name, admin1, admin2,
  latitude, longitude, feature_code(=place), population(empty)
"""
import csv, os, subprocess, sys, tempfile

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # gazetteer/
PBF = os.path.join(BASE, "data", "osm")
BND = os.path.join(BASE, "data", "boundaries")
OUT = os.path.join(BASE, "out", "ref_osm.csv")

PLACE_TYPES = ("city", "town", "village", "hamlet", "suburb",
               "locality", "isolated_dwelling", "neighbourhood")
WHERE = ("place IN (" + ",".join(f"'{p}'" for p in PLACE_TYPES) +
         ") AND name IS NOT NULL")


def load_countries():
    out = []
    with open(os.path.join(BASE, "countries.csv"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(row["iso3"])
    return out


def extract_points(iso3, tmpdir):
    """Run ogr2ogr to pull place points -> GeoDataFrame, or None if no PBF."""
    pbf = os.path.join(PBF, f"{iso3}.osm.pbf")
    if not os.path.exists(pbf) or os.path.getsize(pbf) == 0:
        return None
    csv_path = os.path.join(tmpdir, f"{iso3}_pts.csv")
    cmd = ["ogr2ogr", "-f", "CSV", csv_path, pbf, "points",
           "-where", WHERE, "-lco", "GEOMETRY=AS_XY", "-lco", "SEPARATOR=COMMA"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(csv_path):
        print(f"  {iso3}: ogr2ogr FAILED: {r.stderr.strip()[:200]}")
        return None
    df = pd.read_csv(csv_path, low_memory=False)
    if df.empty:
        return None
    # ogr2ogr AS_XY writes X (lon) and Y (lat) columns
    df = df.rename(columns={"X": "lon", "Y": "lat"})
    df = df.dropna(subset=["lon", "lat", "name"])
    gdf = gpd.GeoDataFrame(
        df, geometry=[Point(xy) for xy in zip(df.lon, df.lat)], crs="EPSG:4326")
    return gdf


def tag_admin(gdf, iso3, level, col_out):
    """Spatial-join admin shapeName from a geoBoundaries file onto gdf."""
    path = os.path.join(BND, f"{iso3}_{level}.geojson")
    gdf[col_out] = ""
    if not os.path.exists(path):
        return gdf
    try:
        adm = gpd.read_file(path)[["shapeName", "geometry"]].to_crs("EPSG:4326")
    except Exception as e:
        print(f"  {iso3}: {level} read failed: {e}")
        return gdf
    adm = adm.rename(columns={"shapeName": col_out})
    joined = gpd.sjoin(gdf.drop(columns=[col_out]), adm,
                       how="left", predicate="within")
    # sjoin can duplicate points on overlapping polygons; keep first
    joined = joined[~joined.index.duplicated(keep="first")]
    gdf[col_out] = joined[col_out].fillna("").values
    return gdf


def main():
    countries = load_countries()
    total = 0
    with open(OUT, "w", newline="", encoding="utf-8") as out, \
            tempfile.TemporaryDirectory() as tmp:
        w = csv.writer(out)
        w.writerow(["source", "iso3", "geonameid", "name", "admin1", "admin2",
                    "latitude", "longitude", "feature_code", "population"])
        for iso3 in countries:
            gdf = extract_points(iso3, tmp)
            if gdf is None:
                print(f"  {iso3}: no PBF / no points (skipped)")
                continue
            gdf = tag_admin(gdf, iso3, "ADM1", "admin1")
            gdf = tag_admin(gdf, iso3, "ADM2", "admin2")
            n = 0
            for _, r in gdf.iterrows():
                w.writerow(["osm", iso3, r.get("osm_id", ""), r["name"],
                            r.get("admin1", ""), r.get("admin2", ""),
                            f"{r['lat']:.6f}", f"{r['lon']:.6f}",
                            r.get("place", ""), ""])
                n += 1
            total += n
            print(f"  {iso3}: {n} OSM places")
    print(f"\nTotal: {total} rows -> {OUT}")


if __name__ == "__main__":
    main()
