# Provision

Our team uses this tool regularly when pre-processing data across multiple country boundaries for health campaign planning. If you do similar geospatial pre-processing work — gathering admin boundaries, population data, road networks, building footprints, and land use data from public sources — provision can save you the effort of downloading from each source individually.

One command, one country ISO code — provision pulls from GeoBoundaries, WorldPop, Meta HRSL, Geofabrik, Overture Maps, and ESA into an organized local directory.

## What it downloads

| Data | Source | Format |
|------|--------|--------|
| Admin boundaries | [GeoBoundaries](https://www.geoboundaries.org/) | GeoJSON |
| Road networks | [OpenStreetMap](https://download.geofabrik.de/) (Geofabrik) | Shapefile |
| AI-detected roads | [MapWithAI](https://mapwith.ai/) (RapidEditor) | Shapefile |
| Population rasters | [WorldPop](https://www.worldpop.org/), [Meta HRSL](https://dataforgood.facebook.com/dfg/tools/high-resolution-population-density-maps), [Kontur](https://www.kontur.io/portfolio/population-dataset/) | GeoTIFF, VRT, CSV |
| Land use / land cover | [ESA](https://www.esa.int/) | GeoTIFF |
| Building footprints | [Overture Maps](https://overturemaps.org/), [OSM](https://www.openstreetmap.org/) | Parquet, CSV |

## Prerequisites

**Always required:**
- Node.js 18+
- ~5–50 GB disk space depending on country size

**For roads step** (MapWithAI conversion):
- GDAL/OGR command-line tools (`ogr2ogr`)

**For Kontur population** (H3 → lat/lon conversion):
- GDAL/OGR command-line tools (`ogr2ogr`, `gzip`)

**For Overture buildings:**
- Python 3.9+
- Python packages: `overturemaps`, `duckdb`

**For OSM buildings** (shapefile → CSV conversion):
- GDAL/OGR command-line tools (`ogr2ogr`)

## Quick start

1. Install dependencies:

```bash
cd provision
npm install
```

2. Run with a country ISO code:

```bash
node gather.js --country BEN --admin-level 2
```

This downloads all available data for Benin at admin level 2 into the `output/` directory.

## CLI reference

| Flag | Description | Default | Example |
|------|-------------|---------|---------|
| `--country <ISO>` | 3-letter ISO country code (required) | — | `--country BEN` |
| `--admin-level <N>` | Admin boundary level, 0–4 | `2` | `--admin-level 3` |
| `--config <path>` | Path to country config JSON | `countries/{ISO}.json` | `--config my-config.json` |
| `--output <dir>` | Output directory | `./output` | `--output /data/geo` |
| `--only <steps>` | Comma-separated list of steps to run | all steps | `--only boundary,population` |

### Available steps

`boundary`, `osm`, `roads`, `population`, `landuse`, `buildings`

### Examples

```bash
# Download everything for Eritrea
node gather.js --country ERI --admin-level 2

# Only download boundaries and population data
node gather.js --country BEN --only boundary,population

# Use a custom config and output directory
node gather.js --country RWA --config countries/rwanda.json --output /data/geo

# Download admin level 3 boundaries only
node gather.js --country KEN --admin-level 3 --only boundary
```

## Configuration

### What works without a country config

These sources derive their URLs from the 3-letter ISO code automatically:

- **Boundaries** — GeoBoundaries API uses the 3-letter ISO
- **WorldPop** — URL built from ISO + population year
- **HRSL** — global VRT URLs, no country code needed
- **Overture buildings** — bbox computed from admin0 boundary

These sources require a country config with explicit URLs:

- **OSM** — needs Geofabrik region slug (e.g., `"africa/benin"`)
- **Roads** — MapWithAI URL uses 2-letter ISO code
- **Kontur** — URL uses 2-letter ISO + release date
- **Land use** — no standard URL pattern

Steps with missing URLs are skipped with a message.

### Precedence

Configuration is merged in order (later overrides earlier):

1. **`config.default.json`** — HRSL layer URLs and global defaults
2. **`countries/{ISO}.json`** — country-specific URLs and overrides
3. **CLI flags** — `--admin-level`, `--output`

### `config.default.json`

Contains settings that apply to all countries:

- `outputDir` — where downloaded files are saved (default: `./output`)
- `sources.population.hrsl.layers` — HRSL VRT URLs (global, same for all countries)
- `defaults.adminLevel` — default admin level (2)
- `defaults.populationYear` — year for WorldPop data (2025)

### Country config files

Create a file in `countries/{ISO}.json` to provide country-specific URLs. See `countries/example-benin.json` for a complete example.

Only include fields that apply — everything else uses defaults or is skipped.

### Adding a new country

Create `countries/{ISO}.json` with any of these fields:

```json
{
  "iso": "BEN",
  "adminLevel": 2,
  "populationYear": 2025,
  "osm": "africa/benin",
  "roads": {
    "url": "https://mapwith.ai/country_exports/{ISO2}_mapwithai_road_data.gpkg.tar.gz"
  },
  "population": {
    "worldpop": {
      "url": "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/{YEAR}/{ISO}/v1/100m/constrained/{iso}_pop_{YEAR}_CN_100m_R2025A_v1.tif"
    },
    "kontur": {
      "url": "https://geodata-eu-central-1-kontur-public.s3.amazonaws.com/kontur_datasets/kontur_population_{ISO2}_{DATE}.gpkg.gz"
    }
  },
  "landuse": {
    "url": "https://2016africalandcover20m.esrin.esa.int/download.php"
  }
}
```

Where to find the URLs:

| Field | Where to look |
|-------|---------------|
| `osm` | [Geofabrik downloads](https://download.geofabrik.de/) — use the path after the domain, e.g., `"africa/benin"` |
| `roads.url` | [MapWithAI exports](https://mapwith.ai/) — replace 2-letter ISO in `{ISO2}_mapwithai_road_data.gpkg.tar.gz` |
| `population.worldpop.url` | [WorldPop](https://hub.worldpop.org/) — only needed if the default URL doesn't work for this country |
| `population.kontur.url` | [Kontur population](https://data.humdata.org/organization/kontur) — find the country's `.gpkg.gz` download link |
| `landuse.url` | [ESA land cover](https://www.esa.int/) — direct download link to the GeoTIFF |

### Disabling a source

In the country config, set `enabled: false` on the buildings sub-sources:

```json
{
  "buildings": {
    "overture": { "enabled": false }
  }
}
```

## Output structure

```
output/{ISO}/
├── boundary/
│   ├── admin0.geojson         # Country outline
│   ├── admin1.geojson         # First-level divisions
│   └── admin2.geojson         # Second-level divisions
├── osm/
│   ├── gis_osm_buildings_a_free_1.shp
│   ├── gis_osm_roads_free_1.shp
│   ├── gis_osm_waterways_free_1.shp
│   └── ... (shapefiles + .shx, .dbf, .prj, .cpg)
├── roads/
│   ├── road_data.shp
│   ├── road_data.shx
│   ├── road_data.dbf
│   └── road_data.prj
├── population/
│   ├── wp-total.tif           # WorldPop 100m constrained (~50-500 MB)
│   ├── hrsl-total.vrt         # Meta HRSL total pop (~2-10 KB each)
│   ├── hrsl-under-five.vrt
│   ├── hrsl-sixty-plus.vrt
│   ├── hrsl-men.vrt
│   ├── hrsl-women.vrt
│   ├── hrsl-women-15-to-49.vrt
│   ├── hrsl-youth-15-to-24.vrt
│   └── kontur-total.csv       # H3→lat/lon points (~50-500 MB)
├── land-use/
│   └── landuse.tif
└── buildings/
    ├── overture-buildings.parquet
    └── osm-buildings.csv
```

## Data sources

### Admin boundaries (GeoBoundaries)

Downloads GeoJSON boundary files for admin levels 0 through the specified level. Each feature has `admin{N}Name` and `admin{N}Id` properties for all levels from 0 up to its own level. Child features are linked to parents via centroid-in-polygon matching.

### OSM data (Geofabrik)

Full Geofabrik shapefile extract for the country's region. The ZIP contains multiple shapefiles covering roads, waterways, places, buildings, land use, railways, and more. Requires the `osm` field in the country config (the Geofabrik region slug, e.g., `"africa/benin"`). Skipped if no slug is configured.

### AI-detected roads (MapWithAI)

MapWithAI/RapidEditor exports of AI-detected road networks. Downloaded as a `.gpkg.tar.gz` archive, extracted, and converted to Shapefile format using `ogr2ogr`. The URL typically uses the 2-letter ISO code.

### Population

Three sources are downloaded to give analysts options:

- **WorldPop**: Constrained individual-countries population estimates at 100m resolution. Single GeoTIFF file per country.
- **Meta HRSL**: High Resolution Settlement Layer with 7 demographic breakdowns (total, under-5, 60+, men, women, women 15–49, youth 15–24). Downloaded as VRT files — lightweight XML pointers (~2–10 KB) to cloud-hosted Cloud-Optimized GeoTIFFs. Can be used directly by GDAL-aware tools; for offline use, clip the actual tiles with `gdal_translate`.
- **Kontur**: H3-based population estimates in GeoPackage format. Downloaded, decompressed, converted to CSV via `ogr2ogr`, then H3 hexagons are expanded to resolution-10 children (~49 points per hex) with population distributed evenly. Output is a lat/lon/population CSV.

### Land use (ESA)

ESA land cover classification data as a GeoTIFF. Skipped if no URL is configured.

### Buildings

- **Overture Maps**: Aggregated building footprints downloaded via the `overturemaps` CLI and filtered to the country boundary using DuckDB spatial queries. Requires Python 3.9+ and the `overturemaps` and `duckdb` packages. Uses the admin0 boundary from the boundary step to compute bbox and filter.
- **OSM buildings**: Extracted from the Geofabrik shapefiles (requires the OSM step to have run first). Converts `gis_osm_buildings_a_free_1.shp` to CSV using `ogr2ogr`.

## Idempotency

Each step checks if its output files already exist before downloading. Re-running the same command will skip already-completed downloads. To force a re-download, delete the specific output file or directory.

## Importing into other projects

Each fetch module takes a flat options object with resolved URLs and output paths — no config awareness. This makes them importable by other codebases that already have their own URL sources:

```js
const fetchPopulation = require('provision/lib/fetch-population');
const fetchRoads = require('provision/lib/fetch-roads');

// Pass your own URLs directly
await fetchPopulation({
  outputDir: './data/population',
  worldpopUrl: 'https://data.worldpop.org/.../ben_pop_2025_CN_100m_R2025A_v1.tif',
  hrslLayers: { total: 'https://...hrsl_general-latest.vrt' },
  konturUrl: 'https://...kontur_population_BJ_20231101.gpkg.gz',
});

await fetchRoads({
  outputDir: './data/roads',
  url: 'https://mapwith.ai/country_exports/BJ_mapwithai_road_data.gpkg.tar.gz',
});
```

`gather.js` is the CLI wrapper that reads config files and builds these options — but the modules themselves don't depend on it.
