/**
 * Download land use / land cover classification data.
 *
 * @param {object} opts
 * @param {string} opts.outputDir - Directory to write landuse file
 * @param {string} opts.url - URL to the GeoTIFF file
 *
 * Output:
 *   {outputDir}/landuse.tif
 */

const path = require("path");
const { downloadToFile, ensureDir } = require("./utils");

module.exports = async function fetchLandUse({ outputDir, url }) {
  ensureDir(outputDir);

  const outPath = path.join(outputDir, "landuse.tif");
  console.log(`    Land use: ${url}`);

  const downloaded = await downloadToFile(url, outPath);
  if (downloaded) {
    console.log(`    Saved: ${outPath}`);
  }
};
