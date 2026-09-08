# Match-Bot GUI — setup on a new machine

Match-Bot reconciles place names across datasets (villages, districts, health
facilities named differently in different sources) and forward-geocodes named
places against a GeoNames + OpenStreetMap gazetteer. This guide takes you from
a blank machine to the running web GUI. Expect ~10 minutes plus download time.

## Prerequisites

- **Python 3.9+** with `pip`
- **git**, and access to the `crosscutio/field-kit` GitHub repository
- **Internet access** at runtime — the GUI downloads gazetteer data on demand
  (GeoNames dumps, geoBoundaries, OpenStreetMap via the Overpass API), loads
  the IBM Plex Mono font and Leaflet from CDNs, and shows OpenStreetMap tiles
- Linux, macOS, or Windows (WSL works; native Windows also works, and
  `build_gui.bat` can produce a standalone .exe via PyInstaller if needed)

No GDAL, no database, no API keys.

## Install

```bash
git clone https://github.com/crosscutio/field-kit.git
cd field-kit/match-bot

# use a virtual environment (or a conda env) — the package pulls in geopandas
python3 -m venv .venv && source .venv/bin/activate

pip install -e ".[all]"
```

The `[all]` extra matters: the GUI needs `flask`, match suggestions need
`rapidfuzz`, and the map views need `geopandas`/`fiona`. A bare `pip install .`
gives you only the CLI matcher.

## Launch

```bash
match-bot-gui
```

This starts a local Flask server at **http://localhost:5000** (it tries to open
your browser automatically). Set `MATCH_BOT_PORT` to use a different port —
parallel instances on different ports coexist fine.

Everything runs and stays on your machine: uploads and outputs live in
`match_bot/gui/instance/uploads/` (gitignored), and the project state survives
page reloads while the server is running.

## The five stages

The GUI is one page that walks a community list through five stages:

1. **Set up** — upload the community list (names + admin columns), choose
   where named places come from (your own geocoded CSV, or build one from
   OSM + GeoNames for a country's admin areas), pick admin boundaries for the
   map (geoBoundaries automatically, or your own GeoJSON), and set the
   auto-accept threshold.
2. **Admin names** — harmonize admin names level by level (region, then
   district…). Each level is a list of your names with ranked candidates from
   the places file; link, mark "no equivalent", or bulk-accept above a score.
   Moving to the next level re-runs the matcher so children unblock.
3. **Auto match** — run the engine: exact and near-exact names always match,
   fuzzy suggestions at or above the threshold are accepted automatically.
   A histogram shows the best score for every community still unmatched.
4. **Link on map** — for each remaining community: numbered candidates on the
   map (press 1–9, Enter to save, S to skip, Ctrl-Z to undo), or click the map
   to drop a pin.
5. **Review & export** — the whole list with coordinates, score and who set
   it; export as `geocoded.csv`.

Undo and History are always available. Every action is journaled in
`output/history.jsonl` inside the session directory.

## Smoke test (no data required)

1. Open http://localhost:5000 and upload any CSV with a name column as the
   community list (`examples/` has small files).
2. In *Named places*, choose **Build from OSM + GeoNames**, pick a country,
   **List admin areas**, tick one, **Fetch OSM for selected**, then
   **Build named places**.

First use of a country downloads its GeoNames dump and boundary files
(seconds to a couple of minutes) and fetches OSM places per admin area from
Overpass; everything is cached under `gazetteer/data/` so subsequent builds of
the same areas are instant.

## Using your own data

- **Community list** (the names you want geocoded): a name column and,
  ideally, one or more admin columns (region, district…). An id column is
  optional — rows are numbered internally and every original column is kept
  in the export.
- **Named places**: name, matching admin columns, latitude, longitude. Or
  build them from the gazetteer.

The CLI (`python -m match_bot lookups|suggest --config …`) works on the same
lookup tables; `projects/ICR Examples/CIV_example.md` walks through it.

Keep real project data out of this repository — `projects/` is gitignored and
meant to be its own private repo (see `CLAUDE.md`).

## Optional pieces

- **Bulk gazetteer build** (`gazetteer/README.md`): pre-builds a 32-country,
  ~700k-place reference CSV for CLI/scripted workflows. Needs GDAL's
  `ogr2ogr`, `curl`, and ~6 GB of downloads. **Not needed for the GUI**, which
  fetches per-country data on demand.
- **CLI matcher**: `match-bot --help` for config-driven batch runs
  (`README.md` has the workflow).

## Gotchas

- **Session lifetime**: closing `match-bot-gui` invalidates the browser
  session; the session directory stays on disk but is not re-attached after
  a restart. Export before quitting if you want to keep results.
- **Orphaned session data**: `instance/uploads/` accumulates per-session
  directories and is never auto-cleaned. Safe to delete when the server is
  stopped.
- **Overpass rate limits**: OSM fetches are throttled and cached; building a
  whole large country in one go can be slow the first time. Per-area failures
  degrade gracefully to GeoNames-only.
- **Corporate networks**: the app needs outbound HTTPS to geonames.org,
  geoboundaries.org, overpass-api.de, tile.openstreetmap.org, and (for the UI
  itself) Google Fonts + unpkg CDNs.
