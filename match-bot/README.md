# Match-Bot

If you're working with geospatial datasets from different sources, you'll find that the same villages, districts, and health facilities are often named differently across datasets. Match-bot reconciles them.

It uses Levenshtein distance, Soundex similarity, and the Hungarian algorithm for optimal one-to-one assignment, organized by an N-level administrative hierarchy.

## How It Works

Match-Bot takes two datasets — a **reference** list of named places and a **target** dataset — and finds the best one-to-one match between them. Matching proceeds in steps:

1. **Manual matches** — applied first from previous lookup edits (always preserved).
2. **Exact match** — names that are identical after standardization.
3. **Fuzzy match (distance)** — names within a Levenshtein edit distance threshold (default: 1).
4. **Fuzzy match (score)** — names where distance/length is below a score threshold (default: 0.25).

At each step, the Hungarian algorithm ensures globally optimal one-to-one assignments within each hierarchy group (e.g., within each province+district combination).

## Quick Start

### Install dependencies

```bash
cd match-bot
pip install -r requirements.txt
```

### Create a config

```bash
cp examples/sample_config.yaml config.yaml
# Edit config.yaml to point to your reference and target files
```

Place your data files in `input/`:
```
input/
├── reference_data.csv
└── boundaries.shp
```

### Run matching

```bash
python -m match_bot match --config config.yaml
```

### Generate lookup tables

```bash
python -m match_bot lookups --config config.yaml
```

This creates `output/lookups/leaf_lookup.csv` — the single source of truth for all match results.

### Suggest matches for unmatched records

```bash
python -m match_bot suggest --config config.yaml --threshold 70
```

## Configuration

The YAML config defines both datasets, their column mappings, hierarchy levels, standardization rules, and matching thresholds. See [`examples/sample_config.yaml`](examples/sample_config.yaml) for the full schema.

```yaml
project_name: "My Project"

reference:
  file: "input/reference.csv"
  columns:
    id: Community_ID
    name: Community
    hierarchy:
      - column: Admin_1
        label: province
      - column: Admin_2
        label: district

target:
  file: "input/boundaries.shp"
  format: shp
  columns:
    id: ADM3_ID
    name: ADM3_NAME
    hierarchy:
      - column: ADM1_NAME
        label: province       # Labels tie the two datasets together
      - column: ADM2_NAME
        label: district

standardization:
  case: lower
  remove_accents: true

matching:
  levenshtein_distance_threshold: 1
  levenshtein_score_threshold: 0.25
  validate_numbers: true

paths:
  lookups_dir: output/lookups
  output_dir: output
```

The hierarchy can have 0, 1, or N levels. Labels must match between reference and target.

## Outputs

| File | Description |
|------|-------------|
| `output/matched.csv` | All matched records with IDs and match metadata |
| `output/lookups/leaf_lookup.csv` | **Source of truth.** Every record with match status, metrics, and a `mapping_rationale` column for manual edits |
| `output/lookups/{label}_lookup.csv` | Hierarchy-level lookup tables (one per level) |
| `output/report.md` | Markdown summary with match rates and unmatched listings |

### Leaf Lookup Schema

| Column | Description |
|--------|-------------|
| `target_{label}` | Target hierarchy value (one per level) |
| `target_name_raw` | Original target name before standardization |
| `target_name_standardized` | Target name after normalization |
| `target_id` | Target record identifier |
| `ref_{label}` | Matched reference hierarchy value |
| `ref_name` | Matched reference name |
| `ref_id` | Matched reference identifier |
| `match_type` | `exact`, `fuzzy_dist`, `fuzzy_score`, `manual`, `no_candidate`, or `reference_only` |
| `levenshtein_distance` | Edit distance between matched names |
| `levenshtein_score` | Distance / name length |
| `soundex_distance` | Hamming distance between Soundex codes |
| `unmatched` | `x` if unmatched, empty if matched |
| `mapping_rationale` | Free text for documenting manual match decisions |

## Iterative Workflow

Match-Bot is designed for iterative refinement:

1. **Run matching** — get automatic matches.
2. **Generate lookups** — create the leaf lookup CSV.
3. **Review** — open `leaf_lookup.csv` in a spreadsheet. For unmatched records (`unmatched=x`):
   - Find the correct reference match
   - Fill in `ref_name`, `ref_id`, and `ref_{label}` columns
   - Set `match_type` to `manual`
   - Clear `unmatched`
   - Add a note in `mapping_rationale`
4. **Re-run matching** — manual matches are preserved and applied first.
5. **Get suggestions** — use `python -m match_bot suggest` to find likely matches among remaining unmatched records.
6. **Repeat** until satisfied with the match rate.

## Using with Claude Code

Match-Bot includes Claude Code skills for an interactive workflow. When running Claude Code from the `match-bot/` directory:

- **"Run the matches"** — runs the pipeline and reports statistics
- **"Suggest manual matches"** — analyzes unmatched records and presents suggestions with confidence scores
- **"Update the lookup with suggestions 1 and 3"** — applies manual matches to the CSV
- **"Generate the report"** — creates a Markdown summary

## Prerequisites

- Python 3.9+

Required:
```
pip install pyyaml jellyfish scipy pandas
```

Optional (for spatial file formats):
```
pip install geopandas fiona
```

Optional (for match suggestions):
```
pip install rapidfuzz
```

## Supported Input Formats

- CSV
- Shapefile (.shp)
- GeoPackage (.gpkg)
- File Geodatabase (.gdb)

Geometry columns are dropped — only attribute data is used for matching.
