/**
 * Download population data from multiple sources.
 *
 * Sources:
 *   - WorldPop (constrained 100m GeoTIFF)
 *   - Meta HRSL (Cloud-Optimized GeoTIFF via VRT)
 *   - Kontur (H3-based population, GeoPackage)
 *
 * Output:
 *   {outputDir}/population/wp-total.tif
 *   {outputDir}/population/hrsl-total.vrt
 *   {outputDir}/population/hrsl-under-five.vrt
 *   {outputDir}/population/hrsl-women.vrt
 *   ...
 *   {outputDir}/population/kontur-total.csv
 */

const fs = require("fs");
const path = require("path");

module.exports = async function fetchPopulation(config, outputDir) {
  const popDir = path.join(outputDir, "population");
  fs.mkdirSync(popDir, { recursive: true });

  // --- WorldPop ---
  await fetchWorldPop(config, popDir);

  // --- Meta HRSL ---
  await fetchHRSL(config, popDir);

  // --- Kontur ---
  await fetchKontur(config, popDir);
};

async function fetchWorldPop(config, popDir) {
  const iso = config.iso;
  const year = config.populationYear || 2025;
  const isoLower = iso.toLowerCase();

  const url =
    config.sources.population?.worldpop?.url ||
    `https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/${year}/${iso}/v1/100m/constrained/${isoLower}_pop_${year}_CN_100m_R2025A_v1.tif`;

  console.log(`  WorldPop: ${url}`);

  // TODO: Extract from grounds-keeper lib/push-raster-population.js
  // 1. Download the GeoTIFF
  // 2. Save to {popDir}/wp-total.tif
}

async function fetchHRSL(config, popDir) {
  const layers = config.sources.population?.hrsl?.layers || {};

  for (const [name, url] of Object.entries(layers)) {
    console.log(`  HRSL ${name}: ${url}`);

    // TODO: Extract from grounds-keeper lib/push-population-src.js
    // 1. Download the VRT file (or reference it — VRTs are cloud-native)
    // 2. Save/reference as {popDir}/hrsl-${name}.vrt
    // Note: VRT files are lightweight pointers to cloud-hosted COGs.
    //       For offline use, the actual GeoTIFF tiles need to be clipped
    //       to the country boundary using gdal_translate or rasterio.
  }
}

async function fetchKontur(config, popDir) {
  const url = config.sources.population?.kontur?.url;
  if (!url) {
    console.log("  Skipping Kontur — no URL configured");
    return;
  }

  console.log(`  Kontur: ${url}`);

  // TODO: Extract from grounds-keeper lib/push-population-src.js
  // 1. Download the .gpkg.gz file
  // 2. Decompress
  // 3. Convert H3 hexagons to lat/lon points (using h3-js)
  // 4. Save as {popDir}/kontur-total.csv
}
