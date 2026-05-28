#!/bin/bash
set -u
cd "$(dirname "$0")/../data/boundaries"
tail -n +2 ../../countries.csv | while IFS=, read -r iso3 iso2 slug name; do
  iso3=$(echo "$iso3" | tr -d '\r ')
  for lvl in ADM1 ADM2; do
    out="${iso3}_${lvl}.geojson"
    [ -s "$out" ] && { echo "SKIP $iso3 $lvl"; continue; }
    url=$(curl -s -f "https://www.geoboundaries.org/api/current/gbOpen/${iso3}/${lvl}/" 2>/dev/null \
          | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('simplifiedGeometryGeoJSON') or d.get('gjDownloadURL') or '')" 2>/dev/null)
    if [ -n "$url" ] && curl -s -f -L -o "$out" "$url"; then
      echo "OK   $iso3 $lvl"
    else
      echo "MISS $iso3 $lvl"; rm -f "$out"
    fi
  done
done
echo "ALL BOUNDARIES DONE"
