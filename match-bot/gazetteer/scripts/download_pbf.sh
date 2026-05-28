#!/bin/bash
set -u
cd "$(dirname "$0")/../data/osm"
tail -n +2 ../../countries.csv | while IFS=, read -r iso3 iso2 slug name; do
  slug=$(echo "$slug" | tr -d '\r')
  url="https://download.geofabrik.de/africa/${slug}-latest.osm.pbf"
  out="${iso3}.osm.pbf"
  if [ -s "$out" ]; then echo "SKIP $iso3 (exists)"; continue; fi
  if curl -s -f -L -o "$out" "$url"; then
    echo "OK   $iso3 $(du -h "$out" | cut -f1)"
  else
    echo "FAIL $iso3 $url"; rm -f "$out"
  fi
done
echo "ALL DOWNLOADS DONE"
