# Skill: Run Matching Pipeline

Run the matching pipeline for a project.

## When to use
When the user says "run the matches", "run matching", or "match the data".

## Instructions

1. Locate the project config YAML (ask user if not obvious).
2. Run the matching pipeline:
   ```bash
   python -m match_bot match --config <path_to_config.yaml>
   ```
3. Read the leaf lookup table at `<lookups_dir>/leaf_lookup.csv` — this is the **sole source of truth** for match results.
4. Report statistics in a table:

| Metric | Value |
|--------|-------|
| Total reference records | X |
| Total target records | X |
| Matched | X |
| Unmatched reference | X |
| Unmatched target | X |
| Match rate | X% |

5. Show match type breakdown:

| Match Type | Count |
|------------|-------|
| exact | X |
| fuzzy_dist | X |
| fuzzy_score | X |
| manual | X |
| no_candidate | X |
| reference_only | X |

## Important
- **ALWAYS** read actual CSV files for numbers — never rely on console output alone.
- The lookup tables are the single source of truth. If there's a discrepancy between console output and lookup tables, the lookup tables are correct.
- After running, suggest the user run `lookups` if lookup tables don't exist yet.

## Required tools
Read, Bash, Glob, Grep
