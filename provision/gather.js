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

  const iso = args.country;
  const isoLower = iso.toLowerCase();
  const adminLevel = args.adminLevel || countryConfig.adminLevel || defaults.defaults.adminLevel;
  const outputDir = args.outputDir || defaults.outputDir;
  const populationYear = countryConfig.populationYear || defaults.defaults.populationYear;

  return { iso, isoLower, adminLevel, outputDir, populationYear, countryConfig, defaults };
}

/**
 * Build the flat options object for each step.
 * All URL construction happens here — modules just receive resolved values.
 */
function buildSteps(config, outputBase) {
  const { iso, isoLower, adminLevel, populationYear, countryConfig, defaults } = config;
  const hrslLayers = defaults.sources.population.hrsl.layers;

  return {
    boundary: {
      label: "Admin boundaries",
      fn: fetchBoundaries,
      opts: {
        outputDir: path.join(outputBase, "boundary"),
        iso,
        adminLevel,
      },
    },
    osm: {
      label: "OpenStreetMap data",
      skip: !countryConfig.osm ? "no 'osm' region slug in country config" : null,
      fn: fetchOSM,
      opts: {
        outputDir: path.join(outputBase, "osm"),
        url: countryConfig.osm
          ? `https://download.geofabrik.de/${countryConfig.osm}-latest-free.shp.zip`
          : null,
      },
    },
    roads: {
      label: "Road networks",
      skip: !countryConfig.roads?.url ? "no roads URL in country config" : null,
      fn: fetchRoads,
      opts: {
        outputDir: path.join(outputBase, "roads"),
        url: countryConfig.roads?.url || null,
      },
    },
    population: {
      label: "Population rasters",
      fn: fetchPopulation,
      opts: {
        outputDir: path.join(outputBase, "population"),
        worldpopUrl: countryConfig.population?.worldpop?.url ||
          `https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/${populationYear}/${iso}/v1/100m/constrained/${isoLower}_pop_${populationYear}_CN_100m_R2025A_v1.tif`,
        hrslLayers,
        konturUrl: countryConfig.population?.kontur?.url || null,
      },
    },
    landuse: {
      label: "Land use data",
      skip: !countryConfig.landuse?.url ? "no land use URL in country config" : null,
      fn: fetchLandUse,
      opts: {
        outputDir: path.join(outputBase, "land-use"),
        url: countryConfig.landuse?.url || null,
      },
    },
    buildings: {
      label: "Building footprints",
      fn: fetchBuildings,
      opts: {
        outputDir: path.join(outputBase, "buildings"),
        admin0Path: path.join(outputBase, "boundary", "admin0.geojson"),
        osmDir: path.join(outputBase, "osm"),
        overture: countryConfig.buildings?.overture?.enabled !== false,
        osm: countryConfig.buildings?.osm?.enabled !== false,
      },
    },
  };
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

  const steps = buildSteps(config, outputBase);

  // Determine which steps to run
  const stepsToRun = args.only
    ? Object.entries(steps).filter(([key]) => args.only.includes(key))
    : Object.entries(steps);

  for (const [key, step] of stepsToRun) {
    if (step.skip) {
      console.log(`⊘ Skipping ${step.label} (${step.skip})`);
      continue;
    }

    console.log(`→ ${step.label}...`);
    try {
      await step.fn(step.opts);
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
