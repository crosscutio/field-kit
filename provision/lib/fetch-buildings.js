/**
 * Download building footprint data from Overture Maps and OpenStreetMap.
 *
 * Sources:
 *   - Overture Maps (aggregated footprints, downloaded via overturemaps CLI + DuckDB)
 *   - OSM buildings (extracted from Geofabrik shapefiles)
 *
 * Requires: Python 3.9+ with overturemaps, geopandas, duckdb packages
 *
 * Output:
 *   {outputDir}/buildings/overture-buildings.parquet
 *   {outputDir}/buildings/osm-buildings.csv
 */

const fs = require("fs");
const path = require("path");

module.exports = async function fetchBuildings(config, outputDir) {
  const buildingsDir = path.join(outputDir, "buildings");
  fs.mkdirSync(buildingsDir, { recursive: true });

  // --- Overture Maps ---
  if (config.sources.buildings?.overture?.enabled !== false) {
    await fetchOverture(config, buildingsDir);
  }

  // --- OSM Buildings ---
  if (config.sources.buildings?.osm?.enabled !== false) {
    await fetchOSMBuildings(config, buildingsDir, outputDir);
  }
};

async function fetchOverture(config, buildingsDir) {
  console.log("  Overture Maps buildings...");

  // TODO: Extract from grounds-keeper lib/push-overture-buildings.js
  // Uses two Python scripts:
  // 1. download-overture-buildings.py — uses overturemaps CLI to download
  //    buildings within the country's bounding box, then filters by admin-0
  //    boundary using DuckDB spatial queries
  // 2. Saves as {buildingsDir}/overture-buildings.parquet
  //
  // Python dependencies: overturemaps, geopandas, pyarrow, duckdb
}

async function fetchOSMBuildings(config, buildingsDir, outputDir) {
  console.log("  OSM buildings...");

  // TODO: Extract from grounds-keeper lib/push-osm-buildings.js
  // 1. Extract building polygons from the OSM shapefiles (already downloaded
  //    in fetch-osm step — look in {outputDir}/osm/)
  // 2. Filter to building features
  // 3. Save as {buildingsDir}/osm-buildings.csv
}
