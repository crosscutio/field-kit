#!/usr/bin/env python3
"""
Suggest manual matches for unmatched records.

Analyzes unmatched records at a specified level and suggests potential
matches using fuzzy string similarity, grouped by parent hierarchy.

Usage:
    python -m match_bot suggest --config path/to/config.yaml [--level leaf]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd


def run(config, level='leaf', threshold=70, apply=False, scope_depth=None, force_pass=False):
    """Suggest manual matches programmatically.

    Args:
        config: A MatcherConfig instance.
        level: 'leaf' or a hierarchy label (e.g., 'province').
        threshold: Minimum fuzzy match score (0-100).
        apply: If True, write suggested matches back to the lookup table as
            match_type='manual'. The Hungarian assignment guarantees a 1-to-1
            pairing per parent group, so each ref is consumed at most once.
        scope_depth: For leaf suggest only. Number of hierarchy levels used to
            form parent groups. Default = full depth (all levels). Set to a
            smaller number to allow matches across the deeper levels (e.g.
            depth=1 with hierarchy=[LGA,WARD] permits cross-WARD matches
            within an LGA).
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        print("ERROR: rapidfuzz is required for match suggestions.")
        print("Install with: pip install rapidfuzz")
        return

    lookups_dir = config.lookups_dir
    labels = config.hierarchy_labels

    if level == 'leaf':
        if force_pass:
            threshold = 0
        _suggest_leaf_matches(config, lookups_dir, labels, fuzz, threshold,
                              apply=apply, scope_depth=scope_depth, force_pass=force_pass)
    elif level in labels:
        _suggest_hierarchy_matches(config, lookups_dir, level, fuzz, threshold, apply=apply)
    else:
        print(f"Unknown level: {level}")
        print(f"Available levels: leaf, {', '.join(labels)}")


def main():
    parser = argparse.ArgumentParser(
        description='Suggest manual matches for unmatched records'
    )
    parser.add_argument('--config', '-c', required=True, help='Path to the YAML config file')
    parser.add_argument('--level', '-l', default='leaf',
        help='Level to suggest matches for: "leaf" or a hierarchy label (e.g., "province")')
    parser.add_argument('--threshold', '-t', type=int, default=70,
        help='Minimum fuzzy match score (0-100) to suggest (default: 70)')
    parser.add_argument('--apply', action='store_true',
        help='Write suggestions back to the lookup as match_type=manual')
    parser.add_argument('--scope-depth', type=int, default=None,
        help='Leaf only: hierarchy depth for parent grouping. Default = all '
             'levels. Use a smaller value to allow matches across deeper '
             'levels (e.g. 1 with [LGA,WARD] = cross-WARD within LGA).')
    parser.add_argument('--force-pass', action='store_true',
        help='Leaf only: force a best-match for every unmatched ref/target pair '
             'within each parent group, ignoring threshold. Marks rows with '
             'forced_pass=x. Use for last-resort assignments to be reviewed manually.')
    args = parser.parse_args()

    from match_bot.core.config import MatcherConfig

    config = MatcherConfig.from_yaml(args.config)
    run(config, level=args.level, threshold=args.threshold,
        apply=args.apply, scope_depth=args.scope_depth,
        force_pass=args.force_pass)


def _score_pair(fuzz, a: str, b: str) -> float:
    """Combined fuzzy score: max of ratio and token_set_ratio.

    `ratio` is sensitive to length; `token_set_ratio` ignores word order and
    duplicates. Taking the max captures both "X" vs "X ward" cases and typo
    cases without one masking the other.
    """
    a, b = a.lower(), b.lower()
    return max(fuzz.ratio(a, b), fuzz.token_set_ratio(a, b))


def _optimal_assignment(targets, refs, score_fn, threshold):
    """Compute globally-optimal 1-to-1 pairing between two lists of items.

    Args:
        targets: List of target items (anything; passed to score_fn).
        refs: List of ref items.
        score_fn: Callable(target, ref) -> float in [0, 100].
        threshold: Minimum score; pairs below this are dropped (item stays
            unmatched). The assignment is computed from the full matrix first,
            then filtered, so high-score pairs aren't blocked by a low-score
            collision.

    Returns:
        List of (target, ref, score) tuples for pairs at/above threshold.
    """
    if not targets or not refs:
        return []

    import numpy as np
    from scipy.optimize import linear_sum_assignment

    n_t, n_r = len(targets), len(refs)
    score_matrix = np.zeros((n_t, n_r), dtype=float)
    for i, t in enumerate(targets):
        for j, r in enumerate(refs):
            score_matrix[i, j] = score_fn(t, r)

    # linear_sum_assignment minimizes cost; convert score → cost
    cost = 100.0 - score_matrix
    row_ind, col_ind = linear_sum_assignment(cost)

    pairs = []
    for i, j in zip(row_ind, col_ind):
        s = score_matrix[i, j]
        if s >= threshold:
            pairs.append((targets[i], refs[j], float(s)))
    return pairs


def _suggest_leaf_matches(config, lookups_dir, labels, fuzz, threshold,
                          apply=False, scope_depth=None, force_pass=False):
    """Suggest matches for unmatched leaf-level records.

    Uses globally-optimal Hungarian assignment per parent hierarchy group, so
    each ref is paired with at most one target and the total score within each
    group is maximized. This avoids the failure mode where a target gets
    matched to a wrong sibling because its correct ref was greedily consumed
    by an earlier-iterated target.
    """
    from collections import defaultdict

    leaf_path = lookups_dir / 'leaf_lookup.csv'
    if not leaf_path.exists():
        print(f"Leaf lookup not found: {leaf_path}")
        print("Run generate_lookups first.")
        return

    df = pd.read_csv(leaf_path)
    unmatched_target = df[df.get('match_type', pd.Series()) == 'no_candidate']
    unmatched_ref = df[df.get('match_type', pd.Series()).isin(['reference_only'])]

    if len(unmatched_target) == 0:
        print("No unmatched target records. Nothing to suggest.")
        return
    if len(unmatched_ref) == 0:
        print("No unmatched reference records to match against.")
        return

    if scope_depth is None or scope_depth > len(labels):
        scope_labels = list(labels)
    else:
        scope_labels = list(labels[:max(0, scope_depth)])

    scope_desc = ', '.join(scope_labels) if scope_labels else 'no parents (global)'
    print(f"Analyzing {len(unmatched_target)} unmatched target records "
          f"against {len(unmatched_ref)} unmatched reference records "
          f"(globally-optimal assignment per group, scoped by {scope_desc})...\n")

    def _parent_key(row, prefix):
        return tuple(
            str(row.get(f'{prefix}_{lbl}', '')).lower().strip()
            for lbl in scope_labels
        )

    targets_by_parent = defaultdict(list)
    for _, t in unmatched_target.iterrows():
        t_name = str(t.get('target_name_standardized') or t.get('target_name_raw') or '').strip()
        if not t_name:
            continue
        targets_by_parent[_parent_key(t, 'target')].append(t)

    refs_by_parent = defaultdict(list)
    for _, r in unmatched_ref.iterrows():
        r_name = str(r.get('ref_name', '')).strip()
        if not r_name:
            continue
        refs_by_parent[_parent_key(r, 'ref')].append(r)

    suggestions = []
    for parent_key, targets in targets_by_parent.items():
        refs = refs_by_parent.get(parent_key, [])
        if not refs:
            continue
        pairs = _optimal_assignment(
            targets, refs,
            score_fn=lambda t, r: _score_pair(
                fuzz,
                str(t.get('target_name_standardized') or t.get('target_name_raw', '')),
                str(r.get('ref_name', '')),
            ),
            threshold=threshold,
        )
        for t, r, s in pairs:
            t_parents = {}
            for label in labels:
                val = t.get(f'target_{label}', '')
                if pd.notna(val) and str(val).strip():
                    t_parents[label] = str(val).lower()
            suggestions.append({
                'target_name': str(t.get('target_name_standardized') or t.get('target_name_raw', '')),
                'target_id': t.get('target_id', ''),
                'ref_name': r.get('ref_name', ''),
                'ref_id': r.get('ref_id', ''),
                'score': s,
                'parents': t_parents,
            })

    if not suggestions:
        print(f"No suggestions found above threshold ({threshold}).")
        return

    suggestions.sort(key=lambda x: -x['score'])
    print(f"Found {len(suggestions)} potential matches:\n")
    for s in suggestions:
        parent_str = ' > '.join(f"{k}={v}" for k, v in s['parents'].items())
        print(f"  [{s['score']:3.0f}] {s['target_name']!r} → {s['ref_name']!r}")
        if parent_str:
            print(f"        ({parent_str})")
        print(f"        target_id={s['target_id']}, ref_id={s['ref_id']}")
        print()

    if apply:
        # Build a ref_id -> ref columns map so the apply can fill ref hierarchy
        # values from the actual ref row (necessary when target and ref parents
        # differ, e.g. cross-ward or cross-LGA modes).
        ref_lookup_map = {}
        for _, r in unmatched_ref.iterrows():
            rid = str(r.get('ref_id', ''))
            if rid:
                ref_lookup_map[rid] = {
                    f'ref_{lbl}': r.get(f'ref_{lbl}', '') for lbl in labels
                }
        _apply_leaf_suggestions(leaf_path, df, suggestions, labels,
                                forced_pass=force_pass, ref_lookup=ref_lookup_map)


def _suggest_hierarchy_matches(config, lookups_dir, level, fuzz, threshold, apply=False):
    """Suggest matches for unmatched hierarchy-level records, scoped by parent.

    Uses the live pipeline target/reference DataFrames so that suggestions are
    constrained to candidates sharing the same parent hierarchy (e.g. an
    unmatched WARD only matches against unmatched WARDs in the same LGA).
    """
    from match_bot.core.matching import MatchingPipeline

    labels = config.hierarchy_labels
    if level not in labels:
        print(f"Unknown level: {level}")
        return
    level_index = labels.index(level)

    pipeline = MatchingPipeline(config)
    pipeline.load_data()
    pipeline.load_lookups()
    pipeline.apply_hierarchy_mappings()

    target = pipeline.target
    ref = pipeline.ref
    h_col = f'h{level_index}'
    parent_cols = [f'h{i}' for i in range(level_index)]
    t_raw_col = f'target_{level}_raw'
    r_raw_col = f'ref_{level}_raw'

    # Distinct (parents..., h_i, raw) tuples on each side
    t_distinct = (target[parent_cols + [h_col, t_raw_col]]
                  .dropna(subset=[h_col])
                  .drop_duplicates(subset=parent_cols + [h_col]))
    r_distinct = (ref[parent_cols + [h_col, r_raw_col]]
                  .dropna(subset=[h_col])
                  .drop_duplicates(subset=parent_cols + [h_col]))

    # A target value is "unmatched" if (parents..., h_i) is absent on ref side
    ref_full_keys = set(
        tuple(str(r[c]).lower() for c in parent_cols + [h_col])
        for _, r in r_distinct.iterrows()
    )
    target_full_keys = set(
        tuple(str(t[c]).lower() for c in parent_cols + [h_col])
        for _, t in t_distinct.iterrows()
    )

    unmatched_target = [
        t for _, t in t_distinct.iterrows()
        if tuple(str(t[c]).lower() for c in parent_cols + [h_col]) not in ref_full_keys
    ]
    unmatched_ref = [
        r for _, r in r_distinct.iterrows()
        if tuple(str(r[c]).lower() for c in parent_cols + [h_col]) not in target_full_keys
    ]

    if not unmatched_target or not unmatched_ref:
        print(f"No unmatched pairs at {level} level.")
        return

    print(f"Analyzing {len(unmatched_target)} unmatched target {level} names "
          f"against {len(unmatched_ref)} unmatched reference names "
          f"(scoped by {', '.join(labels[:level_index]) or 'no parents'})...\n")

    # Group both sides by parent tuple, then run optimal assignment per group.
    from collections import defaultdict
    targets_by_parent = defaultdict(list)
    for t in unmatched_target:
        targets_by_parent[tuple(str(t[c]).lower() for c in parent_cols)].append(t)
    ref_by_parent = defaultdict(list)
    for r in unmatched_ref:
        ref_by_parent[tuple(str(r[c]).lower() for c in parent_cols)].append(r)

    suggestions = []
    for parent_key, ts in targets_by_parent.items():
        rs = ref_by_parent.get(parent_key, [])
        if not rs:
            continue
        pairs = _optimal_assignment(
            ts, rs,
            score_fn=lambda t, r: _score_pair(fuzz, str(t[h_col]), str(r[h_col])),
            threshold=threshold,
        )
        for t, r, score in pairs:
            t_raw = str(t[t_raw_col]) if t_raw_col in t.index else str(t[h_col])
            best_ref_name = str(r[h_col])
            best_score = score
            t_std = str(t[h_col])
            parent_str = ' > '.join(
                f"{labels[i]}={str(t[parent_cols[i]])}" for i in range(level_index)
            )
            suggestions.append({
                'target_name': t_raw,
                'ref_name': best_ref_name,
                'score': best_score,
                'parents': parent_str,
            })

    if not suggestions:
        print(f"No suggestions found above threshold ({threshold}).")
        return

    suggestions.sort(key=lambda x: (-x['score'], x['parents']))
    print(f"Found {len(suggestions)} potential {level} matches:\n")
    for s in suggestions:
        print(f"  [{s['score']:3.0f}] {s['target_name']!r} → {s['ref_name']!r}")
        if s['parents']:
            print(f"        ({s['parents']})")

    if apply:
        _apply_hierarchy_suggestions(lookups_dir / f'{level}_lookup.csv', suggestions)


def _apply_leaf_suggestions(leaf_path, df, suggestions, labels, forced_pass=False,
                            ref_lookup=None):
    """Write leaf-level suggestions back as match_type='manual' rows.

    Args:
        leaf_path: Path to leaf_lookup.csv to write.
        df: Current leaf lookup DataFrame.
        suggestions: List of suggestion dicts from _suggest_leaf_matches.
        labels: Hierarchy labels.
        forced_pass: If True, mark rows with forced_pass='x' (last-resort
            assignments produced by the force-pass mode).
        ref_lookup: Optional dict ref_id -> reference row dict, used to fetch
            ref hierarchy values when the suggestion's parent group differs
            from the ref's actual parents (only happens in cross-parent modes).
    """
    import jellyfish as jf
    from match_bot.core.fuzzy import hamming_distance, levenshtein

    deepest = labels[-1] if labels else None
    cross_col = f'cross_{deepest}' if deepest else None
    object_cols = ['ref_id', 'ref_name', 'match_type', 'mapping_rationale']
    if cross_col:
        if cross_col not in df.columns:
            df[cross_col] = ''
        object_cols.append(cross_col)
    if 'forced_pass' not in df.columns:
        df['forced_pass'] = ''
    object_cols.append('forced_pass')
    for lbl in labels:
        rc = f'ref_{lbl}'
        if rc in df.columns:
            object_cols.append(rc)
    for col in object_cols:
        if col in df.columns:
            df[col] = df[col].astype(object)

    applied = 0
    used_ref_ids = set()
    for s in suggestions:
        rid = str(s['ref_id'])
        tid = str(s['target_id'])
        if rid in used_ref_ids:
            continue
        mask = (df['match_type'] == 'no_candidate') & (df['target_id'].astype(str) == tid)
        if not mask.any():
            continue
        idx = df[mask].index[0]
        df.at[idx, 'ref_id'] = rid
        df.at[idx, 'ref_name'] = s['ref_name']

        # Use the ref's actual parent values from ref_lookup if available
        # (cross-parent suggests can have target_parents != ref_parents).
        ref_row = ref_lookup.get(rid) if ref_lookup else None
        for lbl in labels:
            col = f'ref_{lbl}'
            if col not in df.columns:
                continue
            if ref_row is not None:
                df.at[idx, col] = ref_row.get(f'ref_{lbl}', '')
            else:
                df.at[idx, col] = s['parents'].get(lbl, '')

        df.at[idx, 'match_type'] = 'manual'
        df.at[idx, 'mapping_rationale'] = f"fuzzy suggest score={s['score']:.0f}"

        # cross_ward flag (deepest hierarchy level mismatch)
        if cross_col:
            t_deep = str(df.at[idx, f'target_{deepest}'] or '').strip().lower()
            r_deep = str(df.at[idx, f'ref_{deepest}'] or '').strip().lower()
            df.at[idx, cross_col] = 'x' if t_deep and r_deep and t_deep != r_deep else ''

        if forced_pass:
            df.at[idx, 'forced_pass'] = 'x'

        # Populate metrics on the new match
        t_name = str(df.at[idx, 'target_name_standardized'] or '').strip()
        r_name = str(s['ref_name'] or '').strip().lower()
        if t_name and r_name:
            d = levenshtein(t_name, r_name)
            df.at[idx, 'levenshtein_distance'] = d
            df.at[idx, 'levenshtein_score'] = round(d / max(len(t_name), 1), 3)
            try:
                df.at[idx, 'soundex_distance'] = hamming_distance(
                    jf.soundex(t_name), jf.soundex(r_name)
                )
            except Exception:
                pass

        used_ref_ids.add(rid)
        applied += 1

    drop = (df['match_type'] == 'reference_only') & (df['ref_id'].astype(str).isin(used_ref_ids))
    dropped = drop.sum()
    df = df[~drop].reset_index(drop=True)
    df.to_csv(leaf_path, index=False)
    print(f"\nApplied {applied} suggestions; dropped {dropped} now-redundant reference_only rows.")


def _apply_hierarchy_suggestions(lookup_path, suggestions):
    """Write hierarchy-level suggestions back as match_type='manual' rows."""
    df = pd.read_csv(lookup_path)
    for col in ['reference_name', 'match_type', 'mapping_rationale']:
        if col in df.columns:
            df[col] = df[col].astype(object)
    applied = 0
    used_refs = set()
    for s in suggestions:
        ref_name = s['ref_name']
        target_raw = s['target_name']
        rk = str(ref_name).lower()
        if rk in used_refs:
            continue
        mask = (df['match_type'] == 'no_candidate') & (
            df['target_name_raw'].astype(str).str.lower() == str(target_raw).lower()
        )
        if not mask.any():
            continue
        idx = df[mask].index[0]
        df.at[idx, 'reference_name'] = ref_name
        df.at[idx, 'match_type'] = 'manual'
        df.at[idx, 'mapping_rationale'] = f"fuzzy suggest score={s['score']:.0f}"
        used_refs.add(rk)
        applied += 1
    drop = (df['match_type'] == 'reference_only') & (
        df['reference_name'].astype(str).str.lower().isin(used_refs)
    )
    dropped = drop.sum()
    df = df[~drop].reset_index(drop=True)
    df.to_csv(lookup_path, index=False)
    print(f"\nApplied {applied} suggestions; dropped {dropped} now-redundant reference_only rows.")


if __name__ == '__main__':
    main()
