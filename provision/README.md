# Provision

Gather the geospatial data layers you need before going into the field. Pulls from public data sources into a local directory structure ready for offline analysis.

## What it downloads

| Data | Source | Format |
|------|--------|--------|
| Admin boundaries | [GeoBoundaries](https://www.geoboundaries.org/) | GeoJSON |
| Road networks | [OpenStreetMap](https://download.geofabrik.de/) (Geofabrik) | Shapefile |
| AI-detected roads | [MapWithAI](https://mapwith.ai/) (RapidEditor) | Shapefile |
| Population rasters | [WorldPop](https://www.worldpop.org/), [Meta HRSL](https://dataforgood.facebook.com/dfg/tools/high-resolution-population-density-maps), [Kontur](https://www.kontur.io/portfolio/population-dataset/) | GeoTIFF, CSV |
| Land use / land cover | [ESA](https://www.esa.int/) | GeoTIFF |
| Building footprints | [Overture Maps](https://overturemaps.org/), [OSM](https://www.openstreetmap.org/) | Parquet, CSV |

## Prerequisites

- Node.js 18+
- Python 3.9+ (for Overture building downloads)
- GDAL/OGR command-line tools (`ogr2ogr` for road data conversion)
- ~5-50 GB disk space depending on country size

## Quick start

1. Install dependencies:

```bash
cd gather-data
npm install
```

2. Run with a country ISO code:

```bash
node gather.js --country BEN --admin-level 2
```

This downloads all available data for Benin at admin level 2 into the `output/` directory.

## Configuration

### Country config files

Each country can have a config file in `countries/` that specifies data source URLs and options. See `countries/example-benin.json` for a complete example.

If no country config exists, the tool uses `config.default.json` to construct URLs from the ISO code.

### `config.default.json`

Default settings that apply to all countries unless overridden:

- `outputDir` — where downloaded files are saved (default: `./output`)
- `sources` — URL templates for each data source
- `options` — which data sources to download

### Overriding defaults

Create a file in `countries/{ISO}.json` to override any default for a specific country:

```json
{
  "iso": "BEN",
  "adminLevel": 2,
  "osm": "africa/benin",
  "populationYear": 2025
}
```

Only include fields you want to override — everything else falls back to `config.default.json`.

## Output structure

```
output/{ISO}/
├── boundary/
│   ├── admin0.geojson
│   ├── admin1.geojson
│   └── admin2.geojson
├── osm/
│   └── (shapefiles)
├── roads/
│   └── (shapefiles)
├── population/
│   ├── wp-total.tif
│   ├── hrsl-total.vrt
│   ├── hrsl-under-five.vrt
│   ├── hrsl-women.vrt
│   └── kontur-total.csv
├── land-use/
│   └── landuse.tif
└── buildings/
    ├── overture-buildings.parquet
    └── osm-buildings.csv
```

## Data sources

### Admin boundaries

Downloaded from the GeoBoundaries API. Supports admin levels 0-4.

### Road networks

- **OSM roads**: Full Geofabrik shapefile extract for the country's region
- **AI-detected roads**: MapWithAI/RapidEditor exports (converted from GeoPackage to Shapefile via `ogr2ogr`)

### Population

Multiple sources are downloaded to give analysts options:

- **WorldPop**: Constrained population estimates at 100m resolution
- **Meta HRSL**: High Resolution Settlement Layer with demographic breakdowns (total, under-5, 60+, men, women, reproductive age, youth)
- **Kontur**: H3-based population estimates

### Land use

ESA land cover classification data.

### Buildings

- **Overture Maps**: Aggregated building footprints (requires Python + DuckDB)
- **OSM buildings**: Extracted from OpenStreetMap data
