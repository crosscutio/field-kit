#!/usr/bin/env node

/**
 * Gather geospatial data for a country.
 *
 * Usage:
 *   node gather.js --country BEN --admin-level 2
 *   node gather.js --country BEN --config countries/benin.json
 *   node gather.js --country BEN --only boundary,population
 */

const fs = require("fs");
const path = require("path");

const fetchBoundaries = require("./lib/fetch-boundaries");
const fetchOSM = require("./lib/fetch-osm");
const fetchRoads = require("./lib/fetch-roads");
const fetchPopulation = require("./lib/fetch-population");
const fetchLandUse = require("./lib/fetch-land-use");
const fetchBuildings = require("./lib/fetch-buildings");

const STEPS = {
  boundary: { fn: fetchBoundaries, label: "Admin boundaries" },
  osm: { fn: fetchOSM, label: "OpenStreetMap data" },
  roads: { fn: fetchRoads, label: "Road networks" },
  population: { fn: fetchPopulation, label: "Population rasters" },
  landuse: { fn: fetchLandUse, label: "Land use data" },
  buildings: { fn: fetchBuildings, label: "Building footprints" },
};

function parseArgs() {
  const args = process.argv.slice(2);
  const parsed = {};

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case "--country":
        parsed.country = args[++i];
        break;
      case "--admin-level":
        parsed.adminLevel = parseInt(args[++i], 10);
        break;
      case "--config":
        parsed.configPath = args[++i];
        break;
      case "--output":
        parsed.outputDir = args[++i];
        break;
      case "--only":
        parsed.only = args[++i].split(",");
        break;
      case "--help":
        console.log(`
Usage: node gather.js --country <ISO> [options]

Options:
  --country <ISO>       3-letter ISO country code (required)
  --admin-level <N>     Admin boundary level, 0-4 (default: 2)
  --config <path>       Path to country config JSON
  --output <dir>        Output directory (default: ./output)
  --only <steps>        Comma-separated list of steps to run:
                        boundary, osm, roads, population, landuse, buildings
`);
        process.exit(0);
    }
  }

  return parsed;
}

function loadConfig(args) {
  // Load defaults
  const defaults = JSON.parse(
    fs.readFileSync(path.join(__dirname, "config.default.json"), "utf-8")
  );

  // Load country config if it exists
  let countryConfig = {};
  const configPath =
    args.configPath ||
    path.join(__dirname, "countries", `${args.country}.json`);

  if (fs.existsSync(configPath)) {
    countryConfig = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    console.log(`Loaded country config: ${configPath}`);
  } else {
    console.log(
      `No country config found at ${configPath}, using defaults only`
    );
  }

  // Merge: CLI args > country config > defaults
  return {
    iso: args.country,
    adminLevel: args.adminLevel || countryConfig.adminLevel || defaults.defaults.adminLevel,
    outputDir: args.outputDir || defaults.outputDir,
    populationYear: countryConfig.populationYear || defaults.defaults.populationYear,
    osm: countryConfig.osm || null,
    sources: deepMerge(defaults.sources, countryConfig),
  };
}

function deepMerge(target, source) {
  const result = { ...target };
  for (const key of Object.keys(source)) {
    if (
      source[key] &&
      typeof source[key] === "object" &&
      !Array.isArray(source[key]) &&
      target[key] &&
      typeof target[key] === "object"
    ) {
      result[key] = deepMerge(target[key], source[key]);
    } else if (key !== "iso" && key !== "adminLevel" && key !== "osm" && key !== "populationYear") {
      result[key] = source[key];
    }
  }
  return result;
}

async function main() {
  const args = parseArgs();

  if (!args.country) {
    console.error("Error: --country is required. Run with --help for usage.");
    process.exit(1);
  }

  const config = loadConfig(args);
  const outputBase = path.resolve(config.outputDir, config.iso);

  console.log(`\nGathering data for ${config.iso} (admin level ${config.adminLevel})`);
  console.log(`Output directory: ${outputBase}\n`);

  fs.mkdirSync(outputBase, { recursive: true });

  // Determine which steps to run
  const stepsToRun = args.only
    ? Object.entries(STEPS).filter(([key]) => args.only.includes(key))
    : Object.entries(STEPS);

  for (const [key, step] of stepsToRun) {
    const sourceConfig = config.sources[key];
    if (sourceConfig && sourceConfig.enabled === false) {
      console.log(`⊘ Skipping ${step.label} (disabled in config)`);
      continue;
    }

    console.log(`→ ${step.label}...`);
    try {
      await step.fn(config, outputBase);
      console.log(`  ✓ ${step.label} done\n`);
    } catch (err) {
      console.error(`  ✗ ${step.label} failed: ${err.message}\n`);
    }
  }

  console.log(`\nDone. Files saved to ${outputBase}/`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
