# Skill: Suggest Manual Matches

Analyze unmatched records and suggest potential manual matches to improve match rates.

## When to use
When the user asks for suggestions to improve matching, wants to find additional manual matches, or says "suggest matches".

## Instructions

1. Locate the project config YAML.
2. Run the suggest script:
   ```bash
   python -m match_bot suggest --config <path_to_config.yaml> [--level leaf|<hierarchy_label>] [--threshold 70]
   ```
3. Alternatively, for more interactive analysis, read the leaf lookup directly:
   - Load `<lookups_dir>/leaf_lookup.csv`
   - Identify `no_candidate` (unmatched target) and `reference_only` (unmatched reference) rows
   - Group unmatched pairs by parent hierarchy
   - Use rapidfuzz to score potential matches within each group
   - Present suggestions grouped by parent, sorted by score

4. Present suggestions in a clear format:
   ```
   ## Province: Katanga > District: Likasi

   | Score | Target Name | Reference Name | Action |
   |-------|------------|----------------|--------|
   | 92 | "KAMBOVE" | "KAMBOWE" | Likely match |
   | 85 | "LIKASI CENTRE" | "LIKASI" | Check if same |
   ```

5. Ask user which suggestions to apply, then use the `update-table` skill.

## Important
- Focus on **leaf-level** name matching unless user specifies a hierarchy level.
- Only suggest pairs within the same parent hierarchy group.
- Do NOT suggest matches for records that already have a match_type of 'manual'.
- Analyze existing manual match patterns first to avoid duplicate suggestions.
- Score >= 80: likely match. 70-79: possible. < 70: unlikely.

## Required tools
Read, Bash, Grep, Glob
