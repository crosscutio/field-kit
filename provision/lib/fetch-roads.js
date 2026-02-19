/**
 * Download AI-detected road networks from MapWithAI / RapidEditor.
 *
 * Source: https://mapwith.ai/
 * Downloads GeoPackage and converts to Shapefile using ogr2ogr.
 *
 * Requires: GDAL/OGR command-line tools (ogr2ogr)
 *
 * Output:
 *   {outputDir}/roads/road_data.shp (and associated files)
 */

const fs = require("fs");
const path = require("path");

module.exports = async function fetchRoads(config, outputDir) {
  const roadsDir = path.join(outputDir, "roads");
  fs.mkdirSync(roadsDir, { recursive: true });

  // URL uses 2-letter ISO code — derive from config or country config override
  const url =
    config.sources.roads?.url ||
    `https://mapwith.ai/country_exports/${config.iso}_mapwithai_road_data.gpkg.tar.gz`;

  console.log(`  Downloading road data: ${url}`);

  // TODO: Extract from grounds-keeper lib/push-facebook-roads.js
  // 1. Download the .gpkg.tar.gz file
  // 2. Extract the GeoPackage
  // 3. Convert to Shapefile: ogr2ogr -f "ESRI Shapefile" {roadsDir}/road_data.shp input.gpkg
  // 4. Clean up temp files
};
