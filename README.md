# field-kit

A collection of tools for geospatial analysts working on health campaign planning. Download country-level geospatial data for offline use, match place names across datasets, and prepare inputs for campaign workflows.

## Tools

### [provision](provision/)

Gather the geospatial data layers you need before going into the field. Pulls admin boundaries, population rasters, road networks, building footprints, and land use data from public sources into a local directory structure ready for offline analysis.

```bash
node provision/gather.js --country BEN --admin-level 2
```

### [match-bot](match-bot/)

Match place names across geospatial datasets using fuzzy name matching and geographic proximity. Take two cartographic datasets that name things differently and reconcile them.

```bash
cd match-bot
python -m match_bot match --config config.yaml
```

## Getting Started

Each tool has its own README with setup instructions and usage examples. Pick the tool you need and follow its guide.

### Prerequisites

- Node.js 18+
- Python 3.9+
- GDAL/OGR command-line tools (`ogr2ogr`)

## Licence

[MIT](LICENSE)
