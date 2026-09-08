# Quick start — install locally and run the Côte d'Ivoire demo

About 15 minutes, plus one-time downloads.

## 1. Install

You need Python 3.9 or newer, git, and internet access (the tool fetches
boundaries and map tiles on demand).

```bash
git clone https://github.com/crosscutio/field-kit.git
cd field-kit/match-bot

python3 -m venv .venv && source .venv/bin/activate     # or a conda env
pip install -e ".[all]"
```

`[all]` pulls in Flask (the GUI), rapidfuzz (suggestions) and shapely via
geopandas (assigning places to admin polygons). A bare `pip install .` gives
you only the command-line matcher.

## 2. Get the demo files

The demo data lives in the private `projects/` repository, which sits inside
`match-bot/projects/` (see `CLAUDE.md`). You need three files from
`projects/ICR Examples/`:

| File | Role |
|---|---|
| `subIU_Names_CIV.csv` | the community list (2,253 sub-IU names with region and health district) |
| `cote_divoire_osm_places.csv` | named places from OpenStreetMap with coordinates (10,771 rows) |
| `CIV_example.md` | the same workflow run from the command line, for comparison |

## 3. Launch

```bash
match-bot-gui
```

Your browser opens at http://localhost:5000. Set `MATCH_BOT_PORT` to use a
different port. Everything stays on your machine.

## 4. Walk the demo

**Stage 1 · Set up**

1. *Your community list* — drop `subIU_Names_CIV.csv` on the page. Set
   **Community name** to `Community`. Make sure there are two admin levels:
   `Admin_1` labelled `region` and `IU` labelled `district` (use
   **+ add admin level** if a row is missing). Continue.
2. *Named places* — choose **Upload my own geocoded list** and drop
   `cote_divoire_osm_places.csv`. Set **Place name** `NAME`, **Latitude**
   `latitude`, **Longitude** `longitude`. Ignore the file's admin columns; the
   tool will not use them.
3. *Admin boundaries* — leave **geoBoundaries (automatic)** selected and pick
   **Côte d'Ivoire** as the country. The tool downloads ADM1–ADM3 once and
   suggests a level for each of your admin levels (region → ADM2,
   district → ADM3 for this dataset). Click **Assign admin units from
   boundaries**; every place is placed inside a polygon and the polygon names
   become its region and district. Continue.
4. *Matching rules* — keep the threshold at 0.85 and the parent-unit
   restriction on. **Continue to admin names** runs the first matching pass.

**Stage 2 · Admin names**

Region first: the five names without an exact match are listed with their
best candidate. Click **Accept suggestions above 0.90** to take the safe ones
(Boukani → Bounkani, Haut Sassandra → Haut-Sassandra), then decide the rest by
hand or mark them **No equivalent** (Abidjan 1 and 2 have no counterpart in
the places file). **Continue to district →** re-runs the matcher so the
district level opens up; repeat there. The map shows the candidate polygon for
whichever name is selected.

**Stage 3 · Auto match**

Click **Run fuzzy matching**. Exact and near-exact names always match; fuzzy
suggestions at or above the threshold are accepted automatically. The
histogram shows the best score for every community still open; move the
slider and **Re-run** to see the effect.

**Stage 4 · Link on map**

Each remaining community shows up to three numbered candidates from inside
its district (outlined on the map). Press **1–3** to choose, **Enter** to
save, **S** to skip, or click anywhere on the map to drop a pin. **Ctrl-Z**
undoes.

**Stage 5 · Review & export**

Filter by how each row was set, then **Export csv** to download
`geocoded.csv`: your original columns plus the linked place, coordinates,
score and who set it.

## Where things are saved

Uploads, lookup tables, pins and the action history live in
`match_bot/gui/instance/uploads/<session>/`. Reloading the page restores the
project; restarting the server keeps the files but the browser needs to start
a **New** project to reattach. Export before quitting if you want to keep
results.

## Compare with the command line

The same lookup tables can be driven from the CLI; `CIV_example.md` walks
through `python -m match_bot lookups` and `suggest --level region --apply`
on this dataset and shows the expected counts (457 → 557 → 608 matches).

## Troubleshooting

- **No country in the boundaries list** — the dropdown is sorted by common
  name; Côte d'Ivoire is under C.
- **"shapely is required"** — reinstall with `pip install -e ".[all]"`.
- **Boundary download fails** — geoboundaries.org must be reachable; files
  are cached under `gazetteer/data/boundaries/` after the first fetch.
- **A district's outline looks too small** — geoBoundaries has no
  département layer for Côte d'Ivoire, so districts map to sous-préfectures.
  Upload your own district GeoJSON in Set up, or untick *Restrict to parent
  admin unit* in Stage 3 to search the whole region.
