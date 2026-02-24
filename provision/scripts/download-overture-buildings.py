#!/usr/bin/env python3
"""
Download Overture buildings for a specific bounding box and filter by country boundary.
Uses DuckDB for efficient streaming parquet processing.

Usage: python3 download-overture-buildings.py <minx> <miny> <maxx> <maxy> <output_path> <boundary_geojson>
"""
import sys
import subprocess
import os
import json
import tempfile

import duckdb

if len(sys.argv) != 7:
    print("Usage: download-overture-buildings.py <minx> <miny> <maxx> <maxy> <output_path> <boundary_geojson>", file=sys.stderr)
    sys.exit(1)

minx, miny, maxx, maxy, output_path, boundary_geojson = sys.argv[1:7]

# Ensure output directory exists
os.makedirs(os.path.dirname(output_path), exist_ok=True)

# Download to a temp file first, then filter
temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.parquet')
temp_path = temp_file.name
temp_file.close()
download_path = temp_path

# Use overturemaps CLI to download
cmd = [
    "overturemaps", "download",
    f"--bbox={minx},{miny},{maxx},{maxy}",
    "-f", "geoparquet",
    "--type=building",
    "-o", download_path
]

print(f"Downloading Overture buildings for bbox: {minx},{miny},{maxx},{maxy}")
result = subprocess.run(cmd, check=True, capture_output=True, text=True)
print(result.stdout)
if result.stderr:
    print(result.stderr, file=sys.stderr)

print(f"Downloaded to {download_path}")

# Filter by country boundary using DuckDB
print(f"\nLoading country boundary from {boundary_geojson}...")

try:
    # Load country boundary and convert to WKT for DuckDB
    with open(boundary_geojson, 'r') as f:
        boundary_data = json.load(f)

    features = boundary_data['features']
    print(f"Found {len(features)} feature(s) in boundary")

    # Create a single geometry from all features (union)
    # We'll let DuckDB handle this via ST_Union_Agg
    geometries_wkt = []
    for feature in features:
        geom_json = json.dumps(feature['geometry'])
        geometries_wkt.append(geom_json)

    print("\nFiltering buildings using DuckDB...")

    # Initialize DuckDB with spatial extension
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")

    # Get total count first
    total_result = con.execute(f"SELECT COUNT(*) FROM read_parquet('{download_path}')").fetchone()
    total_buildings = total_result[0]
    print(f"Total buildings to process: {total_buildings:,}")

    # Create boundary geometry in DuckDB
    # Union all boundary features into single geometry
    if len(geometries_wkt) == 1:
        boundary_sql = f"ST_GeomFromGeoJSON('{geometries_wkt[0]}')"
    else:
        # Create a union of all geometries
        union_parts = [f"ST_GeomFromGeoJSON('{g}')" for g in geometries_wkt]
        boundary_sql = f"ST_Union_Agg(geom) FROM (SELECT unnest([{', '.join(union_parts)}]) as geom)"
        boundary_sql = f"(SELECT ST_Union_Agg(geom) FROM (SELECT unnest([{', '.join(union_parts)}]) as geom))"

    # Filter and write to output in one streaming operation
    # geometry column is already GEOMETRY type in GeoParquet
    query = f"""
        COPY (
            SELECT * FROM read_parquet('{download_path}')
            WHERE ST_Intersects(geometry, {boundary_sql})
        ) TO '{output_path}' (FORMAT PARQUET)
    """

    con.execute(query)

    # Get filtered count
    filtered_result = con.execute(f"SELECT COUNT(*) FROM read_parquet('{output_path}')").fetchone()
    filtered_count = filtered_result[0]

    removed_count = total_buildings - filtered_count
    kept_percentage = (filtered_count / total_buildings * 100) if total_buildings > 0 else 0

    print(f"\nFiltering complete:")
    print(f"  Total processed: {total_buildings:,}")
    print(f"  Buildings kept: {filtered_count:,} ({kept_percentage:.1f}%)")
    print(f"  Buildings removed: {removed_count:,}")

    if filtered_count == 0:
        print("WARNING: No buildings found within country boundary!")
        os.remove(download_path)
        if os.path.exists(output_path):
            os.remove(output_path)
        sys.exit(1)

    file_size = os.path.getsize(output_path)
    file_size_mb = file_size / (1024 * 1024)
    print(f"  Output file size: {file_size_mb:.2f} MB")
    print(f"\nFiltered file written to: {output_path}")

    # Clean up temp file
    os.remove(download_path)
    con.close()

except Exception as e:
    print(f"Error filtering buildings: {e}", file=sys.stderr)
    # Clean up temp file if it exists
    if os.path.exists(download_path):
        os.remove(download_path)
    raise
