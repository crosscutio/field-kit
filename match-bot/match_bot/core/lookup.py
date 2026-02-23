"""
Lookup table management for Match-Bot.

Lookup tables are the single source of truth for name mappings and manual
matches. This module handles loading, saving, generating, and merging
lookup tables for both hierarchy levels and leaf (name-level) matching.
"""

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .config import MatcherConfig


def load_lookup(path: str) -> pd.DataFrame:
    """Load a lookup table from CSV."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)


def save_lookup(df: pd.DataFrame, path: str):
    """Save a lookup table to CSV."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False)


def get_hierarchy_mappings(lookup_df: pd.DataFrame) -> Dict[str, str]:
    """Extract target->reference name mappings from a hierarchy lookup table.

    Returns a case-insensitive mapping (lowercase keys) of
    target_name_standardized -> reference_name for entries that have a match.
    """
    if lookup_df.empty:
        return {}

    mappings = {}
    for _, row in lookup_df.iterrows():
        target_std = row.get('target_name_standardized', '')
        ref_name = row.get('reference_name', '')
        if pd.notna(target_std) and pd.notna(ref_name) and str(target_std).strip() and str(ref_name).strip():
            mappings[str(target_std).lower()] = str(ref_name)
    return mappings


def get_manual_matches(lookup_df: pd.DataFrame) -> Dict[str, str]:
    """Extract manual match mappings from a leaf lookup table.

    Returns dict of target_id -> ref_id for entries with match_type='manual'.
    """
    if lookup_df.empty:
        return {}

    manual = lookup_df[lookup_df.get('match_type', pd.Series()) == 'manual']
    result = {}
    for _, row in manual.iterrows():
        target_id = row.get('target_id', '')
        ref_id = row.get('ref_id', '')
        if pd.notna(target_id) and pd.notna(ref_id):
            result[str(target_id)] = str(ref_id)
    return result


def generate_hierarchy_lookup(
    matched: pd.DataFrame,
    unmatched_target: pd.DataFrame,
    unmatched_ref: pd.DataFrame,
    level_label: str,
    target_col: str,
    ref_col: str,
    target_std_col: Optional[str] = None,
) -> pd.DataFrame:
    """Generate a hierarchy-level lookup table.

    Args:
        matched: DataFrame of matched records (must contain target and ref columns).
        unmatched_target: Target records not matched at this level.
        unmatched_ref: Reference records not matched at this level.
        level_label: Hierarchy level label (e.g., 'province', 'district').
        target_col: Column name for target names in matched DataFrame.
        ref_col: Column name for reference names in matched DataFrame.
        target_std_col: Column for standardized target names (defaults to target_col).

    Returns:
        DataFrame with columns: target_column, target_name_raw, target_name_standardized,
        reference_name, match_type, mapping_rationale.
    """
    if target_std_col is None:
        target_std_col = target_col

    rows = []

    # Matched entries
    if not matched.empty:
        seen = set()
        for _, row in matched.iterrows():
            t_name = str(row.get(target_col, ''))
            r_name = str(row.get(ref_col, ''))
            key = (t_name, r_name)
            if key in seen:
                continue
            seen.add(key)
            t_std = str(row.get(target_std_col, t_name))
            rows.append({
                'target_column': target_col,
                'target_name_raw': t_name,
                'target_name_standardized': t_std,
                'reference_name': r_name,
                'match_type': 'exact' if t_std.lower() == r_name.lower() else 'mapped',
                'mapping_rationale': '',
            })

    # Unmatched target entries
    if not unmatched_target.empty:
        for name in unmatched_target[target_col].dropna().unique():
            rows.append({
                'target_column': target_col,
                'target_name_raw': str(name),
                'target_name_standardized': str(name),
                'reference_name': '',
                'match_type': 'no_candidate',
                'mapping_rationale': '',
            })

    # Unmatched reference entries
    if not unmatched_ref.empty:
        for name in unmatched_ref[ref_col].dropna().unique():
            rows.append({
                'target_column': '',
                'target_name_raw': '',
                'target_name_standardized': '',
                'reference_name': str(name),
                'match_type': 'reference_only',
                'mapping_rationale': '',
            })

    return pd.DataFrame(rows)


def generate_leaf_lookup(
    matched: pd.DataFrame,
    unmatched_target: pd.DataFrame,
    unmatched_ref: pd.DataFrame,
    config: MatcherConfig,
    fuzzy_results: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Generate the leaf-level (name) lookup table.

    Args:
        matched: DataFrame of matched records from the pipeline.
        unmatched_target: Unmatched target records.
        unmatched_ref: Unmatched reference records.
        config: MatcherConfig for column name access.
        fuzzy_results: Optional fuzzy match results for distance metrics.

    Returns:
        DataFrame with full lookup schema including hierarchy columns,
        match metrics, match_type, and mapping_rationale.
    """
    hierarchy_labels = config.hierarchy_labels
    rows = []

    # Build fuzzy distance lookup if available
    fuzzy_lookup = {}
    if fuzzy_results is not None and not fuzzy_results.empty:
        for _, row in fuzzy_results.iterrows():
            tid = str(row.get('id', ''))
            fuzzy_lookup[tid] = row

    # Matched records
    for _, row in matched.iterrows():
        entry = {}
        # Target hierarchy columns
        for label in hierarchy_labels:
            entry[f'target_{label}'] = row.get(f'_target_{label}', '')
        entry['target_name_raw'] = row.get('_target_name_raw', row.get('_target_name', ''))
        entry['target_name_standardized'] = row.get('_target_name', '')
        entry['target_id'] = str(row.get('_target_id', ''))

        # Reference hierarchy columns
        for label in hierarchy_labels:
            entry[f'ref_{label}'] = row.get(f'_ref_{label}', '')
        entry['ref_name'] = row.get('_ref_name', '')
        entry['ref_id'] = str(row.get('_ref_id', ''))

        # Match metrics
        tid = str(row.get('_target_id', ''))
        frow = fuzzy_lookup.get(tid)
        lev_dist = row.get('_levenshtein_distance')
        if lev_dist is None and frow is not None:
            lev_dist = frow.get('levenshtein_distance')

        entry['match_type'] = row.get('_match_type', 'exact')
        entry['levenshtein_distance'] = lev_dist
        entry['levenshtein_score'] = None
        if lev_dist is not None and entry['target_name_standardized']:
            name_len = len(str(entry['target_name_standardized']))
            if name_len > 0:
                entry['levenshtein_score'] = round(lev_dist / name_len, 3)

        sdx = None
        if frow is not None:
            sdx = frow.get('soundex_distance_lev_match')
        entry['soundex_distance'] = sdx
        entry['unmatched'] = ''
        entry['mapping_rationale'] = row.get('_mapping_rationale', '')

        rows.append(entry)

    # Unmatched target records
    for _, row in unmatched_target.iterrows():
        entry = {}
        for label in hierarchy_labels:
            entry[f'target_{label}'] = row.get(f'_target_{label}', '')
        entry['target_name_raw'] = row.get('_target_name_raw', row.get('_target_name', ''))
        entry['target_name_standardized'] = row.get('_target_name', '')
        entry['target_id'] = str(row.get('_target_id', ''))
        for label in hierarchy_labels:
            entry[f'ref_{label}'] = ''
        entry['ref_name'] = ''
        entry['ref_id'] = ''
        entry['match_type'] = 'no_candidate'
        entry['levenshtein_distance'] = None
        entry['levenshtein_score'] = None
        entry['soundex_distance'] = None
        entry['unmatched'] = 'x'
        entry['mapping_rationale'] = ''
        rows.append(entry)

    # Unmatched reference records (reference_only)
    for _, row in unmatched_ref.iterrows():
        entry = {}
        for label in hierarchy_labels:
            entry[f'target_{label}'] = ''
        entry['target_name_raw'] = ''
        entry['target_name_standardized'] = ''
        entry['target_id'] = ''
        for label in hierarchy_labels:
            entry[f'ref_{label}'] = row.get(f'_ref_{label}', '')
        entry['ref_name'] = row.get('_ref_name', '')
        entry['ref_id'] = str(row.get('_ref_id', ''))
        entry['match_type'] = 'reference_only'
        entry['levenshtein_distance'] = None
        entry['levenshtein_score'] = None
        entry['soundex_distance'] = None
        entry['unmatched'] = 'x'
        entry['mapping_rationale'] = ''
        rows.append(entry)

    return pd.DataFrame(rows)


def preserve_manual_matches(new_lookup: pd.DataFrame, existing_lookup: pd.DataFrame) -> pd.DataFrame:
    """Merge manual matches from an existing lookup into a newly generated one.

    Manual entries (match_type='manual' or non-empty mapping_rationale) in the
    existing lookup override the corresponding rows in new_lookup.

    Args:
        new_lookup: Freshly generated lookup table.
        existing_lookup: Previous lookup table that may contain manual edits.

    Returns:
        Updated lookup with manual matches preserved.
    """
    if existing_lookup.empty:
        return new_lookup

    result = new_lookup.copy()

    # Identify manual entries in existing lookup
    manual_mask = (
        (existing_lookup.get('match_type', pd.Series()) == 'manual') |
        (existing_lookup.get('mapping_rationale', pd.Series(dtype='str')).fillna('').str.strip() != '')
    )
    manual_entries = existing_lookup[manual_mask]

    if manual_entries.empty:
        return result

    # Try to match by target_id first, then by target_name_raw
    for _, manual_row in manual_entries.iterrows():
        target_id = str(manual_row.get('target_id', ''))
        target_name = str(manual_row.get('target_name_raw', ''))

        matched_idx = None
        if target_id:
            mask = result.get('target_id', pd.Series()) == target_id
            if mask.any():
                matched_idx = result[mask].index[0]
        if matched_idx is None and target_name:
            mask = result.get('target_name_raw', pd.Series()) == target_name
            if mask.any():
                matched_idx = result[mask].index[0]

        if matched_idx is not None:
            # Override with manual entry fields
            for col in manual_row.index:
                if col in result.columns:
                    result.at[matched_idx, col] = manual_row[col]
            result.at[matched_idx, 'match_type'] = 'manual'
            result.at[matched_idx, 'unmatched'] = ''
        else:
            # Manual entry for a record not in new lookup — append it
            result = pd.concat([result, manual_row.to_frame().T], ignore_index=True)

    return result
