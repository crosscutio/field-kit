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


def norm_id(v) -> str:
    """Canonical string form of an id: '850.0' -> '850', NaN -> ''."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    s = str(v)
    if s.lower() == 'nan':
        return ''
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except (ValueError, TypeError):
        pass
    return s


def load_lookup(path: str) -> pd.DataFrame:
    """Load a lookup table from CSV. Id columns are read back as canonical
    strings so a numeric id never turns into '5.0' on a round trip."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    for col in ('target_id', 'ref_id'):
        if col in df.columns:
            df[col] = df[col].map(norm_id).astype(object)
    return df


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

    def _norm_id(v) -> str:
        # Normalize numeric IDs that pandas may have read as floats (e.g. '850.0')
        # back to the canonical string form ('850') used elsewhere in the pipeline.
        s = str(v)
        try:
            f = float(s)
            if f.is_integer():
                return str(int(f))
        except (ValueError, TypeError):
            pass
        return s

    manual = lookup_df[lookup_df.get('match_type', pd.Series()) == 'manual']
    result = {}
    for _, row in manual.iterrows():
        target_id = row.get('target_id', '')
        ref_id = row.get('ref_id', '')
        if pd.notna(target_id) and pd.notna(ref_id):
            result[_norm_id(target_id)] = _norm_id(ref_id)
    return result


def generate_hierarchy_lookup(
    target: pd.DataFrame,
    ref: pd.DataFrame,
    level_index: int,
    level_label: str,
) -> pd.DataFrame:
    """Generate a hierarchy-level lookup table from distinct hierarchy values.

    The lookup is computed from the full target and reference data at the given
    hierarchy level — independent of leaf-level match outcomes. A target name
    that exists (post-mapping) in the reference at the same level is recorded
    as matched; one that does not is `no_candidate`. Reference names absent
    from the target are recorded as `reference_only`.

    Args:
        target: Pipeline target DataFrame with `h{i}` (standardized, post-mapping)
            and `target_{label}_raw` columns.
        ref: Pipeline reference DataFrame with `h{i}` (standardized) and
            `ref_{label}_raw` columns.
        level_index: Index of the hierarchy level (0 = top).
        level_label: Hierarchy level label (e.g., 'province', 'district').

    Returns:
        DataFrame with columns: target_column, target_name_raw,
        target_name_standardized, reference_name, match_type, mapping_rationale.
    """
    h_col = f'h{level_index}'
    target_col_label = f'_target_{level_label}'
    t_raw_col = f'target_{level_label}_raw'
    r_raw_col = f'ref_{level_label}_raw'

    def _distinct(df: pd.DataFrame, raw_col: str) -> pd.DataFrame:
        if df is None or df.empty or h_col not in df.columns:
            return pd.DataFrame(columns=[h_col, raw_col])
        cols = [h_col]
        if raw_col in df.columns:
            cols.append(raw_col)
        out = df[cols].dropna(subset=[h_col])
        return out.drop_duplicates(subset=[h_col]).reset_index(drop=True)

    t_pairs = _distinct(target, t_raw_col)
    r_pairs = _distinct(ref, r_raw_col)

    ref_index = {str(v).lower(): str(v) for v in r_pairs[h_col]} if not r_pairs.empty else {}
    target_keys = {str(v).lower() for v in t_pairs[h_col]} if not t_pairs.empty else set()

    rows = []
    for _, t in t_pairs.iterrows():
        t_std = str(t[h_col])
        t_raw = str(t.get(t_raw_col, t_std)) if t_raw_col in t_pairs.columns else t_std
        key = t_std.lower()
        if key in ref_index:
            rows.append({
                'target_column': target_col_label,
                'target_name_raw': t_raw,
                'target_name_standardized': t_std,
                'reference_name': ref_index[key],
                'match_type': 'exact',
                'mapping_rationale': '',
            })
        else:
            rows.append({
                'target_column': target_col_label,
                'target_name_raw': t_raw,
                'target_name_standardized': t_std,
                'reference_name': '',
                'match_type': 'no_candidate',
                'mapping_rationale': '',
            })

    for _, r in r_pairs.iterrows():
        r_std = str(r[h_col])
        if r_std.lower() not in target_keys:
            rows.append({
                'target_column': '',
                'target_name_raw': '',
                'target_name_standardized': '',
                'reference_name': r_std,
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
    deepest_label = hierarchy_labels[-1] if hierarchy_labels else None
    cross_col = f'cross_{deepest_label}' if deepest_label else None
    rows = []

    def _is_cross(target_val, ref_val):
        t = '' if pd.isna(target_val) else str(target_val).strip().lower()
        r = '' if pd.isna(ref_val) else str(ref_val).strip().lower()
        return 'x' if t and r and t != r else ''

    # Metrics helpers — used to backfill any matched row missing lev/soundex.
    from .fuzzy import levenshtein, hamming_distance
    try:
        import jellyfish as _jf
    except ImportError:
        _jf = None

    def _compute_metrics(t_name, r_name):
        t = '' if t_name is None else str(t_name).strip()
        r = '' if r_name is None else str(r_name).strip().lower()
        if not t or not r:
            return None, None, None
        d = levenshtein(t, r)
        score = round(d / max(len(t), 1), 3)
        sdx = None
        if _jf is not None:
            try:
                sdx = hamming_distance(_jf.soundex(t), _jf.soundex(r))
            except Exception:
                sdx = None
        return d, score, sdx

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
        if pd.isna(lev_dist):
            lev_dist = None
        if lev_dist is None and frow is not None:
            lev_dist = frow.get('levenshtein_distance')
            if pd.isna(lev_dist):
                lev_dist = None

        entry['match_type'] = row.get('_match_type', 'exact')

        sdx = None
        if frow is not None:
            sdx = frow.get('soundex_distance_lev_match')
            if pd.isna(sdx):
                sdx = None

        # Backfill any missing metric by computing directly from name strings.
        # This ensures every matched row (including manual/forced) carries
        # levenshtein_distance, levenshtein_score, and soundex_distance.
        if lev_dist is None or sdx is None:
            d2, s2, sdx2 = _compute_metrics(
                entry['target_name_standardized'], entry['ref_name']
            )
            if lev_dist is None:
                lev_dist = d2
            if sdx is None:
                sdx = sdx2

        entry['levenshtein_distance'] = lev_dist
        entry['levenshtein_score'] = None
        if lev_dist is not None and entry['target_name_standardized']:
            name_len = len(str(entry['target_name_standardized']))
            if name_len > 0:
                entry['levenshtein_score'] = round(lev_dist / name_len, 3)

        entry['soundex_distance'] = sdx
        entry['unmatched'] = ''
        entry['mapping_rationale'] = row.get('_mapping_rationale', '')
        if cross_col:
            entry[cross_col] = _is_cross(
                entry.get(f'target_{deepest_label}'),
                entry.get(f'ref_{deepest_label}'),
            )
        entry['forced_pass'] = ''

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
        if cross_col:
            entry[cross_col] = ''
        entry['forced_pass'] = ''
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
        if cross_col:
            entry[cross_col] = ''
        entry['forced_pass'] = ''
        rows.append(entry)

    return pd.DataFrame(rows)


def preserve_manual_matches(new_lookup: pd.DataFrame, existing_lookup: pd.DataFrame) -> pd.DataFrame:
    """Merge manual matches from an existing lookup into a newly generated one.

    Manual entries (match_type='manual' / 'no_equivalent' or a non-empty
    mapping_rationale) in the existing lookup override the corresponding
    rows in new_lookup.

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
        (existing_lookup.get('match_type', pd.Series()).isin(['manual', 'no_equivalent'])) |
        (existing_lookup.get('mapping_rationale', pd.Series(dtype='str')).fillna('').str.strip() != '')
    )
    manual_entries = existing_lookup[manual_mask]

    if manual_entries.empty:
        return result

    def _norm_id(v) -> str:
        # Float-typed IDs (e.g. '730.0' from a CSV with NaNs) compared to the
        # pipeline's int-typed IDs ('730') would never match. Normalize.
        s = str(v)
        try:
            f = float(s)
            if f.is_integer():
                return str(int(f))
        except (ValueError, TypeError):
            pass
        return s

    # Try to match by target_id first, then by target_name_raw
    for _, manual_row in manual_entries.iterrows():
        target_id = _norm_id(manual_row.get('target_id', ''))
        target_name = str(manual_row.get('target_name_raw', ''))

        matched_idx = None
        if target_id:
            mask = result.get('target_id', pd.Series()).astype(str).map(_norm_id) == target_id
            if mask.any():
                matched_idx = result[mask].index[0]
        if matched_idx is None and target_name:
            mask = result.get('target_name_raw', pd.Series()) == target_name
            if mask.any():
                matched_idx = result[mask].index[0]

        if matched_idx is not None:
            # Override with manual entry fields, but only when the existing
            # value is non-null. Otherwise NaN cells in the saved lookup would
            # clobber freshly-computed values (e.g. lev/soundex metrics).
            for col in manual_row.index:
                if col not in result.columns:
                    continue
                v = manual_row[col]
                if pd.isna(v):
                    continue
                if isinstance(v, str) and not v.strip():
                    continue
                result.at[matched_idx, col] = v
            if str(manual_row.get('match_type', '')) == 'no_equivalent':
                # A person decided there is no counterpart; keep it out of
                # the candidate pools but still counted as unmatched.
                result.at[matched_idx, 'match_type'] = 'no_equivalent'
                if 'unmatched' in result.columns:
                    result.at[matched_idx, 'unmatched'] = 'x'
            else:
                result.at[matched_idx, 'match_type'] = 'manual'
                if 'unmatched' in result.columns:
                    result.at[matched_idx, 'unmatched'] = ''
        else:
            # Manual entry for a record not in new lookup — append it
            result = pd.concat([result, manual_row.to_frame().T], ignore_index=True)

    return result
