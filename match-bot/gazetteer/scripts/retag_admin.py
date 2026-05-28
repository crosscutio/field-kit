#!/usr/bin/env python3
"""Re-tag the combined reference with a single, geometry-derived admin1/admin2
vocabulary (geoBoundaries) so all rows share one consistent scheme.

Reads out/ref_places_combined.csv, spatial-joins every point per country to
geoBoundaries ADM1 and ADM2 polygons, and writes out/ref_admintagged.csv with
admin1/admin2 replaced by the geoBoundaries shapeName (blank if outside all
polygons). Processed per country to keep memory low.
"""
import csv, os
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # gazetteer/
COMBINED = os.path.join(BASE, "out", "ref_places_combined.csv")
BND = os.path.join(BASE, "data", "boundaries")
OUT = os.path.join(BASE, "out", "ref_admintagged.csv")

COLS = ["source", "iso3", "place_id", "name", "admin1", "admin2",
        "latitude", "longitude", "feature_code", "population"]


def load_adm(iso3, level, col):
    path = os.path.join(BND, f"{iso3}_{level}.geojson")
    if not os.path.exists(path):
        return None
    try:
        g = gpd.read_file(path)[["shapeName", "geometry"]].to_crs("EPSG:4326")
        return g.rename(columns={"shapeName": col})
    except Exception as e:
        print(f"  {iso3} {level}: read failed {e}")
        return None


# Read combined grouped by country
df = pd.read_csv(COMBINED, dtype=str, keep_default_na=False)
maxgroup = {}
with open(OUT, "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(COLS)
    for iso3, grp in df.groupby("iso3"):
        grp = grp.copy()
        grp["_lat"] = pd.to_numeric(grp["latitude"], errors="coerce")
        grp["_lon"] = pd.to_numeric(grp["longitude"], errors="coerce")
        grp = grp.dropna(subset=["_lat", "_lon"])
        gdf = gpd.GeoDataFrame(
            grp, geometry=[Point(xy) for xy in zip(grp["_lon"], grp["_lat"])],
            crs="EPSG:4326")
        for level, col in (("ADM1", "admin1"), ("ADM2", "admin2")):
            adm = load_adm(iso3, level, col)
            gdf[col] = ""
            if adm is None:
                continue
            j = gpd.sjoin(gdf[["geometry"]], adm, how="left", predicate="within")
            j = j[~j.index.duplicated(keep="first")]
            gdf[col] = j[col].fillna("").values
        # write
        n = 0
        for _, r in gdf.iterrows():
            w.writerow([r["source"], r["iso3"], r["place_id"], r["name"],
                        r["admin1"], r["admin2"], r["latitude"], r["longitude"],
                        r["feature_code"], r["population"]])
            n += 1
        # track largest (iso3, admin1) reference group for RAM sanity
        vc = gdf["admin1"].value_counts()
        if len(vc):
            maxgroup[iso3] = (vc.index[0], int(vc.iloc[0]))
        print(f"  {iso3}: {n} rows, largest admin1 group={maxgroup.get(iso3)}")

g = max(maxgroup.values(), key=lambda x: x[1]) if maxgroup else (None, 0)
print(f"\nGlobal largest (iso3,admin1) ref group: {g[1]} places "
      f"-> Hungarian matrix {g[1]**2*8/1e6:.0f} MB")
print(f"Wrote {OUT}")
