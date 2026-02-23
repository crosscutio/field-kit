# Skill: Generate Match Report

Generate a Markdown report summarizing match results for a project.

## When to use
When the user says "generate the report", "create a report", or "summarize results".

## Instructions

1. Locate the project config YAML.
2. Run the report generation:
   ```python
   from match_bot.core.config import MatcherConfig
   from match_bot.core.reporting import generate_markdown_report

   config = MatcherConfig.from_yaml('<path_to_config.yaml>')
   generate_markdown_report(config, output_path='<output_dir>/report.md')
   ```
   Or equivalently, read the lookup tables directly and construct the report.

3. **CRITICAL**: All numbers must come from reading actual lookup CSV files.
   - Read `<lookups_dir>/leaf_lookup.csv` and count `match_type` values
   - Matched types: exact, fuzzy_dist, fuzzy_score, manual
   - Unmatched types: no_candidate, reference_only
   - Match rate = matched / (matched + reference_only)

4. Write report to `<output_dir>/report.md` with:
   - Summary table (hierarchy levels + leaf)
   - Match type breakdown
   - Unmatched records listing
   - Overall match rate

5. Show the report to the user after generating.

## Required tools
Read, Write, Bash, Glob, Grep
