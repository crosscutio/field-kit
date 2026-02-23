# Skill: Update Lookup Table

Apply manual matches to a lookup table and document changes.

## When to use
When the user says "update the table/lookup with matches/suggestions", or after `suggest-matches` when user wants to apply suggestions.

## Important distinction
- **"Update"** = add manual matches to the CSV (use this skill)
- **"Regenerate"** = re-run the Python scripts (do NOT use this skill)

## Instructions

1. Read the current lookup CSV (leaf or hierarchy level).
2. For each manual match to apply:
   a. Find the target row by `target_id` or `target_name_raw`.
   b. Fill in the reference fields (`ref_name`, `ref_id`, and hierarchy `ref_*` columns).
   c. Set `match_type` to `manual`.
   d. Clear `unmatched` (set to empty string).
   e. Add a brief `mapping_rationale` explaining why this match is correct.
3. Save the updated CSV.
4. Create or update notes file at `<lookups_dir>/notes/mapping_notes.md`.

### Notes file format

Each entry in the notes file should include:

```markdown
### <target_name> → <ref_name>
- **Target**: <target_name> (ID: <target_id>)
- **Reference**: <ref_name> (ID: <ref_id>)
- **Rationale**: <brief explanation>
- **Evidence**: <source if applicable>
- **Date**: <YYYY-MM-DD>
```

## Important
- Do NOT modify rows where `match_type` is already `manual` unless explicitly asked.
- Do NOT modify the overall structure of the CSV (column order, etc.).
- Always use the Edit tool to modify existing files, not Write.
- MUST update BOTH the CSV AND the notes file together.

## Required tools
Read, Edit, Write
