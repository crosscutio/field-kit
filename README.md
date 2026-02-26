# field-kit

Our team uses these tools regularly when pre-processing geospatial data across multiple country boundaries for health planning (ex: NTD, Malaria, IMmunization). If you do similar work — gathering public geospatial datasets and reconciling place names across sources — field-kit may be useful to you too.

## Tools

### [provision](provision/)

When starting a new geospatial analysis project, you often need admin boundaries, population data, roads, buildings, and land use before going into the field — but these come from 6+ different sources, each with different formats and download methods. Provision downloads everything for a given country with one command. This is great for geospatial analysts who want to quickly set up a project for analysis.

```bash
node provision/gather.js --country BEN --admin-level 2
```

### [match-bot](match-bot/)

Geospatial datasets from different sources almost always name the same villages, districts, and health facilities differently. Match-bot helps to reconcile them using fuzzy name matching and optimal one-to-one assignment, organized by administrative hierarchy.

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
