/**
 * Download OpenStreetMap data from Geofabrik.
 *
 * Source: https://download.geofabrik.de/
 * Requires the OSM region slug (e.g. "africa/benin") in the country config.
 *
 * Output:
 *   {outputDir}/osm/*.shp (and associated .shx, .dbf, .prj files)
 */

const fs = require("fs");
const path = require("path");

module.exports = async function fetchOSM(config, outputDir) {
  if (!config.osm) {
    console.log("  Skipping OSM — no 'osm' region slug in country config");
    return;
  }

  const osmDir = path.join(outputDir, "osm");
  fs.mkdirSync(osmDir, { recursive: true });

  const url = `https://download.geofabrik.de/${config.osm}-latest-free.shp.zip`;
  console.log(`  Downloading OSM data: ${url}`);

  // TODO: Extract from grounds-keeper lib/push-osm.js
  // 1. Download the shapefile ZIP from Geofabrik
  // 2. Extract to {osmDir}/
  // 3. The ZIP contains multiple shapefiles (roads, waterways, places, etc.)
};
