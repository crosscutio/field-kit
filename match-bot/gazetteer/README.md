# Gazetteer

A persistent reference of geocoded place names — a merged **GeoNames + OpenStreetMap** point gazetteer with a single, geometry-derived **geoBoundaries** admin vocabulary. Built once, reused across projects that need to forward-geocode community/village names by fuzzy matching them against a place reference.

Currently covers **32 countries in sub-Saharan Africa** (see `countries.csv`); the build is per-country so any country Geofabrik publishes can be added in minutes.

## Layout

```
gazetteer/
├── countries.csv        # iso3, iso2, geofabrik_slug, country_name — one row per included country
├── scripts/             # build pipeline (committed)
├── data/                # raw downloads (gitignored, kept on disk)
└── out/                 # derived reference CSVs (gitignored)
```

`data/` (~5.7 GB) and `out/` (~150 MB) are deliberately **gitignored** — they're fully regenerable from `countries.csv` + the scripts. Keeping them on disk avoids re-downloading 5.7 GB of PBFs every time.

## The canonical output: `out/ref_admintagged.csv`

Single source of truth — point your matcher at this file.

| Column | Description |
|---|---|
| `source` | `geonames` or `osm` |
| `iso3` | ISO 3166-1 alpha-3 country code |
| `place_id` | source identifier (geonameid or osm_id) |
| `name` | place name |
| `admin1` | geoBoundaries ADM1 shapeName (spatial-join derived — one consistent vocab regardless of source) |
| `admin2` | geoBoundaries ADM2 shapeName (same) |
| `latitude`, `longitude` | EPSG:4326 |
| `feature_code` | GeoNames feature_code or OSM `place=*` tag |
| `population` | GeoNames population, else blank |

Sizes (current build): GeoNames 385,811 places + OSM 315,116 = **695,526 deduped rows**. Per-country counts of populated places are in the build logs.

## Rebuild order

External dependencies on `PATH`: `python3` (geopandas, rapidfuzz), `ogr2ogr` (GDAL with the OSM driver), `curl`. No `unzip` needed (handled in Python).

```bash
cd match-bot/gazetteer
scripts/download_pbf.sh           # Geofabrik per-country .osm.pbf (~5.7 GB total for the current list)
scripts/download_boundaries.sh    # geoBoundaries ADM1/ADM2 GeoJSON per country
python3 scripts/build_geonames.py # GeoNames per-country dumps -> out/ref_geonames.csv
python3 scripts/build_osm.py      # ogr2ogr place points -> out/ref_osm.csv
python3 scripts/merge.py          # combined + dedup -> out/ref_places_combined.csv
python3 scripts/retag_admin.py    # spatial join admin1/admin2 -> out/ref_admintagged.csv
```

Every step is idempotent — re-running won't grow or corrupt outputs. Downloads skip files that already exist.

## Adding a country

1. Append a row to `countries.csv` with `iso3,iso2,geofabrik_slug,country_name`. Look up the Geofabrik slug at <https://download.geofabrik.de/> (e.g., `kenya`, `senegal-and-gambia`, `congo-democratic-republic`).
2. Re-run `download_pbf.sh` (downloads only the new country), then `download_boundaries.sh`, then the four Python scripts in order. They process all rows in `countries.csv` but skip work for files that already exist.

## Why the build looks the way it does

A few non-obvious decisions, documented so they aren't reinvented:

- **Three admin vocabularies → one canonical.** GeoNames and OSM nodes don't carry their admin parent in compatible ways. `retag_admin.py` spatial-joins **every** place point (both sources) to geoBoundaries ADM1/ADM2 polygons, so `admin1`/`admin2` in `ref_admintagged.csv` is one consistent geometry-derived vocabulary. Without this, downstream hierarchy matching breaks.
- **Per-country admin tier**, decided by the consumer. Different gazetteers/datasets put their "province" at different geoBoundaries tiers (e.g., Côte d'Ivoire's *régions* are geoBoundaries ADM2, not ADM1; same for Niger's *départements*). We tag both tiers at build time; the consumer (e.g., the ESPEN pipeline in `scratch/ESPEN Surveys/matchrun/`) auto-selects which tier matches its own admin vocab better.
- **~1.4% of points fall outside ADM1 polygons** (coastal nodes, polygon simplification gaps) and end up with blank `admin1`. Acceptable tradeoff for simpler boundaries; consumers must tolerate blanks.
- **Hungarian k×k memory.** `match-bot`'s core fuzzy matcher allocates a square `k×k` cost matrix sized to `max(targets, refs)` per group. On a 4 GB machine, ungrouped country-level matching OOMs for Nigeria/Uganda/DRC. Consumers must group at admin1 (or admin2 for countries with too-coarse ADM1, like Uganda's 4-region split). This is why `admin1`/`admin2` are pre-computed here.

## Future state: `geocode-places` skill

Designed but not yet implemented. A `match-bot/.claude/skills/geocode-places/SKILL.md` will accept an input CSV (rows with `name` + optional `admin_1`, `admin_2`, `country_iso3`) and produce that CSV augmented with `latitude`, `longitude`, and provenance columns.

Steps (the working prototype lives in `match-bot/scratch/ESPEN Surveys/matchrun/`):

1. For each country present, ensure rows exist in `out/ref_admintagged.csv`; if not, append to `countries.csv` and rebuild that country.
2. Per country, auto-select admin tier (ADM1 vs ADM2) by best fuzzy alignment between input admin names and reference admin vocab — see `matchrun/build_admin1_crosswalk.py:score_level`.
3. Build the admin crosswalk (rapidfuzz + FR/EN tokenization + a `OVERRIDES` dict for stubborn cases), write per-country `admin_lookup.csv` that match-bot consumes as a hierarchy lookup.
4. Run match-bot per country via subprocess (OS-level memory isolation) with the gazetteer as reference and a single-level hierarchy keyed on the chosen admin tier — see `matchrun/run_all.py`.
5. Optionally fall back from a finer match key (e.g. `Site`) to a coarser one (e.g. `Community`) — see how `fill_hybrid.py` combines two runs.

Output: input CSV + `latitude`, `longitude`, `geocode_source`, `geocode_match_type`, `geocode_ref_name`, `geocode_lev_distance`, optionally `geocode_match_level` for hybrid runs.

Follow the existing skill convention in `match-bot/.claude/skills/` (Markdown headers, no YAML frontmatter, a `Required tools` line).
