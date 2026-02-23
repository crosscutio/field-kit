"""
Fuzzy matching algorithms for Match-Bot.

Provides Levenshtein distance, Soundex/Hamming distance, and the Hungarian
algorithm for optimal one-to-one assignment. Supports N-level hierarchy
grouping via `group_columns`.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

# Optional dependencies - checked at runtime when needed
jellyfish = None
linear_sum_assignment = None


def _check_jellyfish():
    """Lazily import jellyfish and raise helpful error if not available."""
    global jellyfish
    if jellyfish is None:
        try:
            import jellyfish as jf
            jellyfish = jf
        except ImportError:
            raise ImportError(
                "jellyfish is required for fuzzy matching. "
                "Install it with: pip install jellyfish"
            )
    return jellyfish


def _check_scipy():
    """Lazily import scipy.optimize.linear_sum_assignment."""
    global linear_sum_assignment
    if linear_sum_assignment is None:
        try:
            from scipy.optimize import linear_sum_assignment as lsa
            linear_sum_assignment = lsa
        except ImportError:
            raise ImportError(
                "scipy is required for optimal assignment. "
                "Install it with: pip install scipy"
            )
    return linear_sum_assignment


def levenshtein(s1: str, s2: str) -> int:
    """Compute the Levenshtein (edit) distance between two strings."""
    if s1 == s2:
        return 0
    if len(s1) == 0:
        return len(s2)
    if len(s2) == 0:
        return len(s1)

    rows = len(s1) + 1
    cols = len(s2) + 1
    distance_matrix = [[0] * cols for _ in range(rows)]

    for i in range(1, rows):
        distance_matrix[i][0] = i
    for j in range(1, cols):
        distance_matrix[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            distance_matrix[i][j] = min(
                distance_matrix[i - 1][j] + 1,
                distance_matrix[i][j - 1] + 1,
                distance_matrix[i - 1][j - 1] + cost,
            )

    return distance_matrix[-1][-1]


def hamming_distance(s1: str, s2: str) -> int:
    """Compute the Hamming distance between two equal-length strings."""
    if len(s1) != len(s2):
        raise ValueError("Hamming distance requires equal-length strings")
    return sum(ch1 != ch2 for ch1, ch2 in zip(s1, s2))


def compute_levenshtein_score(df: pd.DataFrame, distance_col: str = 'levenshtein_distance',
                              text_col: str = 'name') -> pd.Series:
    """Compute normalized Levenshtein score (distance / length)."""
    return df[distance_col] / df[text_col].str.len()


def validate_number_match(df: pd.DataFrame,
                          source_col: str = 'name',
                          match_col: str = 'closest_levenshtein_match') -> pd.DataFrame:
    """Filter matches to ensure numeric portions of names match."""
    result = df.copy()
    result['_source_numbers'] = (
        result[source_col].str.extract(r'(\d+)', expand=False).fillna('_NO_NUMBER_')
    )
    result['_match_numbers'] = (
        result[match_col].str.extract(r'(\d+)', expand=False).fillna('_NO_NUMBER_')
    )
    result = result[result['_source_numbers'] == result['_match_numbers']]
    result = result.drop(columns=['_source_numbers', '_match_numbers'])
    return result


def fuzzy_match_1_to_1(
    reference_dataset: pd.DataFrame,
    dataset_to_match: pd.DataFrame,
    group_columns: Optional[List[str]] = None,
    id_col: str = 'id',
    match_col: str = 'name',
    ref_id_col: str = 'ref_id',
) -> pd.DataFrame:
    """
    Perform one-to-one fuzzy matching using the Hungarian algorithm.

    Each reference record is matched to at most one record in dataset_to_match,
    finding the globally optimal assignment that minimizes total matching cost
    within each group defined by group_columns.

    Args:
        reference_dataset: DataFrame with reference/canonical names.
        dataset_to_match: DataFrame with names to be matched.
        group_columns: List of column names to group by before matching.
            If None or empty, all records are matched in a single group.
        id_col: Column name for the identifier in dataset_to_match.
        match_col: Column name for the text to match.
        ref_id_col: Column name for the reference identifier.

    Returns:
        DataFrame with columns: id, *group_columns, name,
        closest_levenshtein_match, levenshtein_distance, ref_id_lev,
        soundex_distance_lev_match, closest_soundex_match,
        soundex_distance, ref_id_soundex.
    """
    jf = _check_jellyfish()
    lsa = _check_scipy()

    if group_columns is None:
        group_columns = []

    # Drop rows with NA in match column
    dataset_to_match = dataset_to_match.dropna(subset=[match_col])
    reference_dataset = reference_dataset.dropna(subset=[match_col])

    records = []

    if group_columns:
        groups = dataset_to_match.groupby(group_columns, sort=False)
    else:
        # Single group containing all rows
        groups = [('__all__', dataset_to_match)]

    for group_key, grp in groups:
        # Normalize group_key to tuple
        if not group_columns:
            group_vals = {}
        elif len(group_columns) == 1:
            group_vals = {group_columns[0]: group_key}
        else:
            group_vals = dict(zip(group_columns, group_key))

        sites = grp[match_col].tolist()
        ids = grp[id_col].tolist()

        # Filter reference to same group
        if group_columns:
            mask = pd.Series(True, index=reference_dataset.index)
            for col in group_columns:
                val = group_vals[col]
                mask = mask & (reference_dataset[col] == val)
            refs = reference_dataset[mask]
        else:
            refs = reference_dataset

        ref_names = refs[match_col].tolist()
        ref_ids = refs[ref_id_col].tolist()

        n, m = len(sites), len(ref_names)

        # No candidates — everything stays None
        if m == 0:
            for id_val, site in zip(ids, sites):
                row = {'id': id_val, 'name': site}
                row.update(group_vals)
                row.update({
                    'closest_levenshtein_match': None,
                    'levenshtein_distance': None,
                    'soundex_distance_lev_match': None,
                    'ref_id_lev': None,
                    'closest_soundex_match': None,
                    'soundex_distance': None,
                    'ref_id_soundex': None,
                })
                records.append(row)
            continue

        k = max(n, m)

        # --- Levenshtein cost matrix ---
        lev_costs = [[levenshtein(s, r) for r in ref_names] for s in sites]
        pad_lev = (max(max(row) for row in lev_costs) + 1) if lev_costs else 1
        cost_lev = np.full((k, k), pad_lev, dtype=int)
        cost_lev[:n, :m] = lev_costs
        row_lev, col_lev = lsa(cost_lev)

        # --- Soundex cost matrix ---
        site_sdx = [jf.soundex(s) for s in sites]
        ref_sdx = [jf.soundex(r) for r in ref_names]
        sdx_costs = [
            [hamming_distance(ss, rs) for rs in ref_sdx]
            for ss in site_sdx
        ]
        pad_sdx = (max(max(row) for row in sdx_costs) + 1) if sdx_costs else 1
        cost_sdx = np.full((k, k), pad_sdx, dtype=int)
        cost_sdx[:n, :m] = sdx_costs
        row_sdx, col_sdx = lsa(cost_sdx)

        # Assemble output rows
        for i, (id_val, site) in enumerate(zip(ids, sites)):
            j_lev = next((c for r, c in zip(row_lev, col_lev) if r == i and c < m), None)
            if j_lev is not None:
                best_lev_name = ref_names[j_lev]
                best_lev_dist = levenshtein(site, best_lev_name)
                best_ref_id_lev = ref_ids[j_lev]
            else:
                best_lev_name = None
                best_lev_dist = None
                best_ref_id_lev = None

            j_sdx = next((c for r, c in zip(row_sdx, col_sdx) if r == i and c < m), None)
            if j_sdx is not None:
                best_sdx_name = ref_names[j_sdx]
                best_sdx_dist = hamming_distance(site_sdx[i], ref_sdx[j_sdx])
                best_ref_id_sdx = ref_ids[j_sdx]
            else:
                best_sdx_name = None
                best_sdx_dist = None
                best_ref_id_sdx = None

            soundex_distance_lev_match = None
            if best_lev_name is not None:
                sdx_site = jf.soundex(site)
                sdx_lev = jf.soundex(best_lev_name)
                soundex_distance_lev_match = hamming_distance(sdx_site, sdx_lev)

            row = {'id': id_val, 'name': site}
            row.update(group_vals)
            row.update({
                'closest_levenshtein_match': best_lev_name,
                'levenshtein_distance': best_lev_dist,
                'soundex_distance_lev_match': soundex_distance_lev_match,
                'ref_id_lev': best_ref_id_lev,
                'closest_soundex_match': best_sdx_name,
                'soundex_distance': best_sdx_dist,
                'ref_id_soundex': best_ref_id_sdx,
            })
            records.append(row)

    return pd.DataFrame(records)
