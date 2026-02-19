/**
 * Download admin boundaries from GeoBoundaries API.
 *
 * Source: https://www.geoboundaries.org/
 * Levels 0-4 supported.
 *
 * Output:
 *   {outputDir}/boundary/admin0.geojson
 *   {outputDir}/boundary/admin1.geojson
 *   {outputDir}/boundary/admin2.geojson
 */

const fs = require("fs");
const path = require("path");

module.exports = async function fetchBoundaries(config, outputDir) {
  const boundaryDir = path.join(outputDir, "boundary");
  fs.mkdirSync(boundaryDir, { recursive: true });

  for (let level = 0; level <= config.adminLevel; level++) {
    const url = `https://www.geoboundaries.org/api/current/gbOpen/${config.iso}/ADM${level}`;
    console.log(`  Fetching admin level ${level}: ${url}`);

    // TODO: Extract from grounds-keeper lib/push-boundary.js
    // 1. Fetch the API endpoint to get the download URL
    // 2. Download the GeoJSON from the gjDownloadURL field
    // 3. Validate features have required admin properties
    // 4. Save to {boundaryDir}/admin{level}.geojson

    const outPath = path.join(boundaryDir, `admin${level}.geojson`);
    console.log(`  → ${outPath}`);
  }
};
