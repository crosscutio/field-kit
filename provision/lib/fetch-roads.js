/**
 * Download AI-detected road networks from MapWithAI / RapidEditor.
 *
 * Source: https://mapwith.ai/
 * Downloads GeoPackage and converts to Shapefile using ogr2ogr.
 *
 * Requires: GDAL/OGR command-line tools (ogr2ogr)
 *
 * @param {object} opts
 * @param {string} opts.outputDir - Directory to write shapefiles
 * @param {string} opts.url - Full URL to the .gpkg.tar.gz file
 *
 * Output:
 *   {outputDir}/road_data.shp (and associated files)
 */

const fs = require("fs");
const path = require("path");
const got = require("got");
const tar = require("tar-stream");
const gunzip = require("gunzip-maybe");
const { execSync } = require("child_process");
const { ensureDir } = require("./utils");

module.exports = async function fetchRoads({ outputDir, url }) {
  ensureDir(outputDir);

  const outPath = path.join(outputDir, "road_data.shp");
  if (fs.existsSync(outPath)) {
    console.log("    Already exists: road_data.shp");
    return;
  }

  console.log(`    Downloading road data: ${url}`);

  // Create a temp dir for extraction
  const tempDir = path.join(outputDir, "_temp");
  ensureDir(tempDir);

  // Stream download → gunzip → tar extract
  const extract = tar.extract();
  let gpkgName = null;

  extract.on("entry", (header, stream, next) => {
    if (header.name.endsWith(".gpkg")) {
      gpkgName = header.name;
    }
    const filePath = path.join(tempDir, path.basename(header.name));
    const writer = fs.createWriteStream(filePath);
    stream.pipe(writer);
    writer.on("finish", next);
    writer.on("error", next);
  });

  got.stream(url).pipe(gunzip()).pipe(extract);

  await new Promise((resolve, reject) => {
    extract.on("finish", resolve);
    extract.on("error", reject);
  });

  if (!gpkgName) {
    throw new Error("No .gpkg file found in tar archive");
  }

  const gpkgPath = path.join(tempDir, path.basename(gpkgName));
  console.log("    Converting GeoPackage to Shapefile with ogr2ogr...");

  execSync(
    `ogr2ogr -f "ESRI Shapefile" "${outPath}" "${gpkgPath}"`,
    { stdio: "pipe" }
  );

  // Clean up temp dir
  const tempFiles = fs.readdirSync(tempDir);
  for (const f of tempFiles) {
    fs.unlinkSync(path.join(tempDir, f));
  }
  fs.rmdirSync(tempDir);

  console.log(`    Saved: ${outPath}`);
};
