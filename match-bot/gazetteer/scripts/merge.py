#!/usr/bin/env python3
"""Merge the GeoNames and OSM references into one geocoded place-name file.

Dedupe near-identical entries (same iso3 + normalized name + admin2, rounded
coordinates). GeoNames is preferred on tie because its admin hierarchy is
already resolved. Output: geocode/out/ref_places_combined.csv
"""
import csv, os, unicodedata

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # gazetteer/
OUT = os.path.join(BASE, "out", "ref_places_combined.csv")
SRC = [os.path.join(BASE, "out", "ref_geonames.csv"),   # preferred first
       os.path.join(BASE, "out", "ref_osm.csv")]

COLS = ["source", "iso3", "place_id", "name", "admin1", "admin2",
        "latitude", "longitude", "feature_code", "population"]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


seen = set()
n_in = {"geonames": 0, "osm": 0}
n_out = 0
with open(OUT, "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(COLS)
    for path in SRC:
        if not os.path.exists(path):
            print(f"  (missing {os.path.basename(path)} - skipped)")
            continue
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            # GeoNames file uses 'geonameid' as id column; OSM uses 'geonameid' too
            for row in r:
                src = row.get("source", "")
                n_in[src] = n_in.get(src, 0) + 1
                try:
                    lat = round(float(row["latitude"]), 3)
                    lon = round(float(row["longitude"]), 3)
                except (ValueError, KeyError):
                    lat = lon = ""
                key = (row["iso3"], norm(row["name"]), norm(row.get("admin2", "")),
                       lat, lon)
                if key in seen:
                    continue
                seen.add(key)
                w.writerow([row.get("source", ""), row["iso3"],
                            row.get("geonameid", row.get("place_id", "")),
                            row["name"], row.get("admin1", ""),
                            row.get("admin2", ""), row["latitude"],
                            row["longitude"], row.get("feature_code", ""),
                            row.get("population", "")])
                n_out += 1
print(f"  input: geonames={n_in.get('geonames',0)} osm={n_in.get('osm',0)}")
print(f"  output (deduped): {n_out} -> {OUT}")
