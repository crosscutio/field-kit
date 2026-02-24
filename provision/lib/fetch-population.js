/**
 * Download population data from multiple sources.
 *
 * @param {object} opts
 * @param {string} opts.outputDir - Directory to write population files
 * @param {string} [opts.worldpopUrl] - WorldPop GeoTIFF URL (skipped if null)
 * @param {Object<string, string>} [opts.hrslLayers] - Map of layer name → VRT URL
 * @param {string} [opts.konturUrl] - Kontur .gpkg.gz URL (skipped if null)
 *
 * Output:
 *   {outputDir}/wp-total.tif
 *   {outputDir}/hrsl-{name}.vrt
 *   {outputDir}/kontur-total.csv
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const csv = require("csv-parser");
const h3 = require("h3-js");
const { downloadToFile, downloadText, ensureDir } = require("./utils");

module.exports = async function fetchPopulation({ outputDir, worldpopUrl, hrslLayers, konturUrl }) {
  ensureDir(outputDir);

  // Each sub-source is independently try/caught so one failure doesn't block others
  if (worldpopUrl) {
    try {
      await fetchWorldPop(outputDir, worldpopUrl);
    } catch (err) {
      console.error(`    WorldPop failed: ${err.message}`);
    }
  }

  if (hrslLayers && Object.keys(hrslLayers).length > 0) {
    try {
      await fetchHRSL(outputDir, hrslLayers);
    } catch (err) {
      console.error(`    HRSL failed: ${err.message}`);
    }
  }

  if (konturUrl) {
    try {
      await fetchKontur(outputDir, konturUrl);
    } catch (err) {
      console.error(`    Kontur failed: ${err.message}`);
    }
  }
};

async function fetchWorldPop(outputDir, url) {
  const outPath = path.join(outputDir, "wp-total.tif");
  console.log(`    WorldPop: ${url}`);

  const downloaded = await downloadToFile(url, outPath);
  if (downloaded) {
    console.log(`    Saved: ${outPath}`);
  }
}

async function fetchHRSL(outputDir, layers) {
  for (const [name, url] of Object.entries(layers)) {
    const outPath = path.join(outputDir, `hrsl-${name}.vrt`);

    if (fs.existsSync(outPath)) {
      console.log(`    Already exists: hrsl-${name}.vrt`);
      continue;
    }

    console.log(`    HRSL ${name}: ${url}`);

    // VRT files are lightweight XML pointers to cloud-hosted COGs (~2-10KB)
    const vrtContent = await downloadText(url);
    fs.writeFileSync(outPath, vrtContent);
    console.log(`    Saved: ${outPath}`);
  }
}

async function fetchKontur(outputDir, url) {
  const outPath = path.join(outputDir, "kontur-total.csv");
  if (fs.existsSync(outPath)) {
    console.log("    Already exists: kontur-total.csv");
    return;
  }

  console.log(`    Kontur: ${url}`);

  // Download the .gpkg.gz file
  const fileNameParts = url.split("/");
  const gzFileName = fileNameParts[fileNameParts.length - 1];
  const gpkgGzPath = path.join(outputDir, gzFileName);
  const gpkgFileName = gzFileName.replace(".gpkg.gz", ".gpkg");
  const gpkgPath = path.join(outputDir, gpkgFileName);
  const h3CsvPath = path.join(outputDir, `${gpkgFileName}_h3.csv`);

  await downloadToFile(url, gpkgGzPath);

  // Decompress
  console.log("    Decompressing Kontur GPKG...");
  execSync(`gzip -f -d "${gpkgGzPath}"`);

  // Convert to CSV using ogr2ogr
  console.log("    Converting Kontur H3 to CSV with ogr2ogr...");
  execSync(`ogr2ogr -f CSV "${h3CsvPath}" "${gpkgPath}"`);

  // Convert H3 hexagons to lat/lon points
  console.log("    Distributing H3 hexagons to lat/lon points...");
  const writeStream = fs.createWriteStream(outPath);
  await h3CSVtoLatLongCSV(h3CsvPath, writeStream);

  // Wait for the write stream to fully close
  await new Promise((resolve, reject) => {
    writeStream.close((err) => {
      if (err) reject(err);
      else resolve();
    });
  });

  // Clean up temp files
  if (fs.existsSync(gpkgPath)) fs.unlinkSync(gpkgPath);
  if (fs.existsSync(h3CsvPath)) fs.unlinkSync(h3CsvPath);

  console.log(`    Saved: ${outPath}`);
}

/**
 * Convert H3 CSV to lat/lon CSV.
 * Matches grounds-keeper logic: resolution 10 children (~49 per hex),
 * distribute population evenly, 6 decimal places for lat/lon.
 */
function h3CSVtoLatLongCSV(inputPath, writeStream) {
  return new Promise((resolve, reject) => {
    let errored = false;
    const stream = fs.createReadStream(inputPath).pipe(csv());

    writeStream.write("Lat,Lon,Population\n");

    stream.on("data", (d) => {
      const hex = d["h3"];
      const pop = d["population"];
      const targetResolution = 10;
      const arrayOfChildren = h3.cellToChildren(hex, targetResolution);
      const numberOfChildren = arrayOfChildren.length;
      const points = arrayOfChildren.map((childHex) => h3.cellToLatLng(childHex));

      points.forEach((point) => {
        const roundedPop = (pop / numberOfChildren).toFixed(4);
        const latLongDecimals = 6;
        const line = `${point[0].toFixed(latLongDecimals)},${point[1].toFixed(latLongDecimals)},${roundedPop}`;
        writeStream.write(`${line}\n`);
      });
    });

    stream.on("error", (err) => {
      errored = true;
      reject(err);
    });

    stream.on("finish", () => {
      if (errored) return;
      resolve();
    });
  });
}
