/**
 * Download admin boundaries from GeoBoundaries API.
 *
 * Source: https://www.geoboundaries.org/
 * Levels 0-4 supported.
 *
 * @param {object} opts
 * @param {string} opts.outputDir - Directory to write boundary files
 * @param {string} opts.iso - 3-letter ISO country code
 * @param {number} opts.adminLevel - Max admin level to download (0-4)
 *
 * Output:
 *   {outputDir}/admin0.geojson
 *   {outputDir}/admin1.geojson
 *   {outputDir}/admin2.geojson
 */

const fs = require("fs");
const path = require("path");
const { downloadText, ensureDir } = require("./utils");

module.exports = async function fetchBoundaries({ outputDir, iso, adminLevel }) {
  ensureDir(outputDir);

  for (let level = 0; level <= adminLevel; level++) {
    const outPath = path.join(outputDir, `admin${level}.geojson`);

    if (fs.existsSync(outPath)) {
      console.log(`    Already exists: admin${level}.geojson`);
      continue;
    }

    const apiUrl = `https://www.geoboundaries.org/api/current/gbOpen/${iso}/ADM${level}`;
    console.log(`    Fetching admin level ${level}: ${apiUrl}`);

    const apiBody = await downloadText(apiUrl);
    const apiJson = JSON.parse(apiBody);

    console.log(`    Download URL: ${apiJson.gjDownloadURL}`);
    console.log(`    Boundary year: ${apiJson.boundaryYearRepresented || "unknown"}`);

    const geojsonStr = await downloadText(apiJson.gjDownloadURL);
    const geojson = JSON.parse(geojsonStr);

    // Add admin-level properties matching grounds-keeper format
    geojson.features.forEach((f, idx) => {
      const props = {};
      props[`admin${level}Name`] = f.properties.shapeName;
      props[`admin${level}Id`] = idx;
      f.properties = props;
    });

    // For levels > 0, attach parent properties by finding which parent
    // polygon each feature's representative point falls in
    if (level > 0) {
      const parentPath = path.join(outputDir, `admin${level - 1}.geojson`);
      if (fs.existsSync(parentPath)) {
        const parent = JSON.parse(fs.readFileSync(parentPath, "utf-8"));
        geojson.features.forEach((f) => {
          const found = findParent(f, parent);
          if (found) {
            f.properties = Object.assign({}, found.properties, f.properties);
          }
        });
      }
    }

    fs.writeFileSync(outPath, JSON.stringify(geojson));
    console.log(`    Saved: ${outPath}`);
  }
};

/**
 * Find the parent feature that contains this feature's representative point.
 */
function findParent(feature, parentGeojson) {
  const point = roughCentroid(feature);
  if (!point) return null;

  for (const parent of parentGeojson.features) {
    if (pointInFeature(point, parent)) {
      return parent;
    }
  }
  // Fallback: if centroid doesn't land in any parent (boundary edge cases),
  // return the first parent (single-country admin0)
  if (parentGeojson.features.length === 1) {
    return parentGeojson.features[0];
  }
  return null;
}

function roughCentroid(feature) {
  const coords = getFirstRing(feature.geometry);
  if (!coords || coords.length === 0) return null;

  let sumX = 0, sumY = 0;
  for (const c of coords) {
    sumX += c[0];
    sumY += c[1];
  }
  return [sumX / coords.length, sumY / coords.length];
}

function getFirstRing(geometry) {
  if (!geometry) return null;
  if (geometry.type === "Polygon") return geometry.coordinates[0];
  if (geometry.type === "MultiPolygon") return geometry.coordinates[0][0];
  return null;
}

function pointInFeature(point, feature) {
  const geom = feature.geometry;
  if (!geom) return false;

  const rings =
    geom.type === "Polygon"
      ? [geom.coordinates[0]]
      : geom.type === "MultiPolygon"
      ? geom.coordinates.map((p) => p[0])
      : [];

  for (const ring of rings) {
    if (pointInRing(point, ring)) return true;
  }
  return false;
}

function pointInRing(point, ring) {
  const [x, y] = point;
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}
