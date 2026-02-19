/**
 * Download land use / land cover classification data.
 *
 * Source: ESA Africa Land Cover (or Planetary Computer)
 *
 * Output:
 *   {outputDir}/land-use/landuse.tif
 */

const fs = require("fs");
const path = require("path");

module.exports = async function fetchLandUse(config, outputDir) {
  const landUseDir = path.join(outputDir, "land-use");
  fs.mkdirSync(landUseDir, { recursive: true });

  const url = config.sources.landuse?.url;
  if (!url) {
    console.log("  Skipping land use — no URL configured");
    return;
  }

  console.log(`  Land use: ${url}`);

  // TODO: Extract from grounds-keeper lib/push-land-use.js
  // 1. Download the GeoTIFF
  // 2. Optionally clip to country boundary for smaller file size
  // 3. Save to {landUseDir}/landuse.tif
};
