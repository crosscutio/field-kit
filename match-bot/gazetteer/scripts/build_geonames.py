#!/usr/bin/env python3
"""Build a geocoded place-name reference from GeoNames per-country dumps.

Output: geocode/out/ref_geonames.csv with columns:
  source, iso3, geonameid, name, admin1, admin2, latitude, longitude,
  feature_code, population

Only populated places (feature class 'P') are kept. Admin1/Admin2 codes are
resolved to human-readable names via the global admin code lookup files.
"""
import csv, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # gazetteer/
GN = os.path.join(BASE, "data", "geonames")
OUT = os.path.join(BASE, "out", "ref_geonames.csv")

# Load country config (iso3 -> iso2)
countries = []
with open(os.path.join(BASE, "countries.csv"), newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        countries.append((row["iso3"], row["iso2"]))

# Load admin code -> name lookups.
# admin1CodesASCII.txt: "CC.A1 \t name \t asciiname \t geonameid"
# admin2Codes.txt:      "CC.A1.A2 \t name \t asciiname \t geonameid"
admin1 = {}
with open(os.path.join(GN, "admin1CodesASCII.txt"), encoding="utf-8") as f:
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2:
            admin1[p[0]] = p[1]
admin2 = {}
with open(os.path.join(GN, "admin2Codes.txt"), encoding="utf-8") as f:
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2:
            admin2[p[0]] = p[1]

# GeoNames main table columns (tab-separated, no header):
# 0 geonameid 1 name 2 asciiname 3 alternatenames 4 lat 5 lon
# 6 feature_class 7 feature_code 8 country_code 9 cc2
# 10 admin1_code 11 admin2_code 12 admin3 13 admin4 14 population ...
rows_out = 0
with open(OUT, "w", newline="", encoding="utf-8") as out:
    w = csv.writer(out)
    w.writerow(["source", "iso3", "geonameid", "name", "admin1", "admin2",
                "latitude", "longitude", "feature_code", "population"])
    for iso3, iso2 in countries:
        path = os.path.join(GN, f"{iso2}.txt")
        if not os.path.exists(path):
            print(f"  WARN missing {iso2}.txt")
            continue
        n = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                c = line.rstrip("\n").split("\t")
                if len(c) < 15 or c[6] != "P":
                    continue
                cc, a1, a2 = c[8], c[10], c[11]
                a1name = admin1.get(f"{cc}.{a1}", a1)
                a2name = admin2.get(f"{cc}.{a1}.{a2}", a2)
                w.writerow(["geonames", iso3, c[0], c[1], a1name, a2name,
                            c[4], c[5], c[7], c[14]])
                n += 1
        rows_out += n
        print(f"  {iso3}: {n} populated places")
print(f"\nTotal: {rows_out} rows -> {OUT}")
