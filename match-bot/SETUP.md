# Match-Bot GUI — setup on a new machine

Match-Bot reconciles place names across datasets (villages, districts, health
facilities named differently in different sources) and forward-geocodes named
places against a GeoNames + OpenStreetMap gazetteer. This guide takes you from
a blank machine to the running web GUI. Expect ~10 minutes plus download time.

## Prerequisites

- **Python 3.9+** with `pip`
- **git**, and access to the `crosscutio/field-kit` GitHub repository
- **Internet access** at runtime — the geocoding tab downloads gazetteer data
  on demand (GeoNames dumps, geoBoundaries, OpenStreetMap via the Overpass
  API), and the GUI loads fonts/Bootstrap/Leaflet from CDNs
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
`match_bot/gui/instance/uploads/` (gitignored), and session state survives page
reloads while the server is running. Work you want to keep across restarts is
saved via **Save Config** (a YAML file that captures datasets, settings, and
your hand-made match links).

## Smoke test (Geocoding tab, no data required)

1. Open http://localhost:5000/geocoding
2. Click **Full setup** (top right)
3. Under *Points dataset*, in the gazetteer section: pick a country (e.g.
   Chad), click **Load admin areas**, tick one admin area, click
   **Add gazetteer points**

First use of a country downloads its GeoNames dump and boundary files
(seconds to a couple of minutes) and fetches OSM places per admin area from
Overpass; everything is cached under `gazetteer/data/` so subsequent builds of
the same areas are instant. If this step produces points on the map, the
install is good.

## Using your own data

Two CSVs, both loaded through **Full setup**:

- **Reference** (the names you want geocoded): an ID column and a name column;
  optionally admin-hierarchy columns (district, commune, …)
- **Points** (candidate locations, if you have them): ID, name, latitude,
  longitude. Optional — the gazetteer can be your points pool instead, or be
  blended with your CSV.

Pick the columns in the UI, optionally pair a hierarchy level (a reference
column against a points column) under *Matching*, then **Run match**. Review
suggested matches level by level in the table; leaf-level matches become
coordinates, exportable via **Export**.

Keep real project data out of this repository — `projects/` is gitignored and
meant to be its own private repo (see `CLAUDE.md`).

## Optional pieces

- **Chat assistant** ("Ask Claude" box): requires the `claude` CLI installed
  and authenticated on PATH. Absent CLI = the box politely fails; everything
  else works.
- **Bulk gazetteer build** (`gazetteer/README.md`): pre-builds a 32-country,
  ~700k-place reference CSV for CLI/scripted workflows. Needs GDAL's
  `ogr2ogr`, `curl`, and ~6 GB of downloads. **Not needed for the GUI**, which
  fetches per-country data on demand.
- **CLI matcher**: `match-bot --help` for config-driven batch runs
  (`README.md` has the workflow).

## Gotchas

- **Session lifetime**: closing `match-bot-gui` invalidates the browser
  session; in-progress GUI state is not restored after a restart. Save a
  config YAML before quitting if you want to resume.
- **Orphaned session data**: `instance/uploads/` accumulates per-session
  directories and is never auto-cleaned. Safe to delete when the server is
  stopped.
- **Overpass rate limits**: OSM fetches are throttled and cached; building a
  whole large country in one go can be slow the first time. Per-area failures
  degrade gracefully to GeoNames-only.
- **Corporate networks**: the app needs outbound HTTPS to geonames.org,
  geoboundaries.org, overpass-api.de, and (for the UI itself) Google Fonts +
  jsdelivr + unpkg CDNs.
