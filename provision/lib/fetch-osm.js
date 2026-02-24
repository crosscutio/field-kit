/**
 * Download OpenStreetMap data from Geofabrik.
 *
 * Source: https://download.geofabrik.de/
 *
 * @param {object} opts
 * @param {string} opts.outputDir - Directory to write shapefiles
 * @param {string} opts.url - Full Geofabrik ZIP URL
 *
 * Output:
 *   {outputDir}/*.shp (and associated .shx, .dbf, .prj files)
 */

const fs = require("fs");
const path = require("path");
const got = require("got");
const unzipper = require("unzipper");
const { ensureDir } = require("./utils");

module.exports = async function fetchOSM({ outputDir, url }) {
  ensureDir(outputDir);

  // Check for a marker file that indicates extraction is complete
  const markerPath = path.join(outputDir, ".complete");
  if (fs.existsSync(markerPath)) {
    console.log("    Already exists: OSM shapefiles");
    return;
  }

  console.log(`    Downloading OSM data: ${url}`);

  const zip = got.stream(url).pipe(unzipper.Parse({ forceStream: true }));

  for await (const entry of zip) {
    if (entry.type === "Directory") {
      entry.autodrain();
      continue;
    }
    const fileName = path.basename(entry.path);
    const outPath = path.join(outputDir, fileName);
    console.log(`    Extracting: ${fileName}`);
    await new Promise((resolve, reject) => {
      const ws = fs.createWriteStream(outPath);
      entry.pipe(ws);
      ws.on("finish", resolve);
      ws.on("error", reject);
    });
  }

  // Write marker file
  fs.writeFileSync(markerPath, new Date().toISOString());
  console.log(`    Extracted to: ${outputDir}`);
};
