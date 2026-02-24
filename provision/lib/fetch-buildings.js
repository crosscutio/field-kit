/**
 * Download building footprint data from Overture Maps and OpenStreetMap.
 *
 * @param {object} opts
 * @param {string} opts.outputDir - Directory to write building files
 * @param {string} opts.admin0Path - Path to admin0.geojson (for Overture bbox + filter)
 * @param {string} opts.osmDir - Path to OSM shapefiles directory (for OSM buildings)
 * @param {boolean} [opts.overture=true] - Whether to fetch Overture buildings
 * @param {boolean} [opts.osm=true] - Whether to fetch OSM buildings
 *
 * Output:
 *   {outputDir}/overture-buildings.parquet
 *   {outputDir}/osm-buildings.csv
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const { ensureDir } = require("./utils");

module.exports = async function fetchBuildings({ outputDir, admin0Path, osmDir, overture = true, osm = true }) {
  ensureDir(outputDir);

  if (overture) {
    try {
      await fetchOverture(outputDir, admin0Path);
    } catch (err) {
      console.error(`    Overture buildings failed: ${err.message}`);
    }
  }

  if (osm) {
    try {
      await fetchOSMBuildings(outputDir, osmDir);
    } catch (err) {
      console.error(`    OSM buildings failed: ${err.message}`);
    }
  }
};

async function fetchOverture(outputDir, admin0Path) {
  console.log("    Overture Maps buildings...");

  const outPath = path.join(outputDir, "overture-buildings.parquet");
  if (fs.existsSync(outPath)) {
    console.log("    Already exists: overture-buildings.parquet");
    return;
  }

  if (!admin0Path || !fs.existsSync(admin0Path)) {
    console.log("    Skipping Overture — admin0.geojson not found (run boundary step first)");
    return;
  }

  const admin0 = JSON.parse(fs.readFileSync(admin0Path, "utf-8"));

  // Calculate bounding box from admin0 features
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const feature of admin0.features) {
    const coords = getAllCoords(feature.geometry);
    for (const [x, y] of coords) {
      if (x < minx) minx = x;
      if (y < miny) miny = y;
      if (x > maxx) maxx = x;
      if (y > maxy) maxy = y;
    }
  }

  const scriptPath = path.join(__dirname, "..", "scripts", "download-overture-buildings.py");
  if (!fs.existsSync(scriptPath)) {
    throw new Error(`Python script not found: ${scriptPath}`);
  }

  const cmd = `python3 "${scriptPath}" ${minx} ${miny} ${maxx} ${maxy} "${outPath}" "${admin0Path}"`;
  console.log(`    Running: ${cmd}`);
  execSync(cmd, { stdio: "inherit" });

  if (fs.existsSync(outPath)) {
    const stats = fs.statSync(outPath);
    console.log(`    Saved: ${outPath} (${(stats.size / 1024 / 1024).toFixed(2)} MB)`);
  }
}

async function fetchOSMBuildings(outputDir, osmDir) {
  console.log("    OSM buildings...");

  const outPath = path.join(outputDir, "osm-buildings.csv");
  if (fs.existsSync(outPath)) {
    console.log("    Already exists: osm-buildings.csv");
    return;
  }

  if (!osmDir || !fs.existsSync(osmDir)) {
    console.log("    Skipping OSM buildings — OSM directory not found (run osm step first)");
    return;
  }

  const buildingsShp = findFile(osmDir, "gis_osm_buildings_a_free_1.shp");
  if (!buildingsShp) {
    console.log("    Skipping OSM buildings — gis_osm_buildings_a_free_1.shp not found");
    return;
  }

  console.log("    Converting OSM buildings shapefile to CSV...");
  execSync(
    `ogr2ogr -f CSV "${outPath}" "${buildingsShp}"`,
    { stdio: "pipe" }
  );

  if (fs.existsSync(outPath)) {
    console.log(`    Saved: ${outPath}`);
  }
}

function findFile(dir, fileName) {
  if (!fs.existsSync(dir)) return null;
  const entries = fs.readdirSync(dir);
  for (const entry of entries) {
    if (entry === fileName) {
      return path.join(dir, entry);
    }
  }
  return null;
}

function getAllCoords(geometry) {
  if (!geometry) return [];
  const coords = [];
  function walk(arr) {
    if (typeof arr[0] === "number") {
      coords.push(arr);
    } else {
      for (const item of arr) walk(item);
    }
  }
  walk(geometry.coordinates);
  return coords;
}
