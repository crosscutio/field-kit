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


def main():
    parser = argparse.ArgumentParser(
        description='Suggest manual matches for unmatched records'
    )
    parser.add_argument(
        '--config', '-c', required=True,
        help='Path to the YAML config file'
    )
    parser.add_argument(
        '--level', '-l', default='leaf',
        help='Level to suggest matches for: "leaf" or a hierarchy label (e.g., "province")'
    )
    parser.add_argument(
        '--threshold', '-t', type=int, default=70,
        help='Minimum fuzzy match score (0-100) to suggest (default: 70)'
    )
    args = parser.parse_args()

    try:
        from rapidfuzz import fuzz
    except ImportError:
        print("ERROR: rapidfuzz is required for match suggestions.")
        print("Install with: pip install rapidfuzz")
        sys.exit(1)

    from match_bot.core.config import MatcherConfig
    from match_bot.core.lookup import load_lookup

    config = MatcherConfig.from_yaml(args.config)
    lookups_dir = config.lookups_dir
    labels = config.hierarchy_labels

    if args.level == 'leaf':
        _suggest_leaf_matches(config, lookups_dir, labels, fuzz, args.threshold)
    elif args.level in labels:
        _suggest_hierarchy_matches(config, lookups_dir, args.level, fuzz, args.threshold)
    else:
        print(f"Unknown level: {args.level}")
        print(f"Available levels: leaf, {', '.join(labels)}")
        sys.exit(1)


def _suggest_leaf_matches(config, lookups_dir, labels, fuzz, threshold):
    """Suggest matches for unmatched leaf-level records."""
    leaf_path = lookups_dir / 'leaf_lookup.csv'
    if not leaf_path.exists():
        print(f"Leaf lookup not found: {leaf_path}")
        print("Run generate_lookups first.")
        return

    df = pd.read_csv(leaf_path)

    # Separate unmatched target (no_candidate) and reference (reference_only)
    unmatched_target = df[df.get('match_type', pd.Series()) == 'no_candidate']
    unmatched_ref = df[df.get('match_type', pd.Series()).isin(['reference_only'])]

    if len(unmatched_target) == 0:
        print("No unmatched target records. Nothing to suggest.")
        return

    if len(unmatched_ref) == 0:
        print("No unmatched reference records to match against.")
        return

    print(f"Analyzing {len(unmatched_target)} unmatched target records "
          f"against {len(unmatched_ref)} unmatched reference records...\n")

    # Group by parent hierarchy for more relevant suggestions
    # Use the first hierarchy level as parent grouping
    suggestions = []

    for _, t_row in unmatched_target.iterrows():
        t_name = str(t_row.get('target_name_standardized', t_row.get('target_name_raw', '')))
        if not t_name.strip():
            continue

        # Get parent hierarchy for context
        t_parents = {}
        for label in labels:
            val = t_row.get(f'target_{label}', '')
            if pd.notna(val) and str(val).strip():
                t_parents[label] = str(val).lower()

        best_score = 0
        best_ref = None

        for _, r_row in unmatched_ref.iterrows():
            r_name = str(r_row.get('ref_name', ''))
            if not r_name.strip():
                continue

            # Check if parent hierarchy matches (if available)
            parent_match = True
            for label in labels:
                t_val = t_parents.get(label, '')
                r_val = str(r_row.get(f'ref_{label}', '')).lower()
                if t_val and r_val and t_val != r_val:
                    parent_match = False
                    break

            if not parent_match:
                continue

            score = fuzz.ratio(t_name.lower(), r_name.lower())
            if score > best_score:
                best_score = score
                best_ref = r_row

        if best_score >= threshold and best_ref is not None:
            suggestions.append({
                'target_name': t_name,
                'target_id': t_row.get('target_id', ''),
                'ref_name': best_ref.get('ref_name', ''),
                'ref_id': best_ref.get('ref_id', ''),
                'score': best_score,
                'parents': t_parents,
            })

    if not suggestions:
        print(f"No suggestions found above threshold ({threshold}).")
        return

    # Sort by score descending
    suggestions.sort(key=lambda x: x['score'], reverse=True)

    print(f"Found {len(suggestions)} potential matches:\n")
    for s in suggestions:
        parent_str = ' > '.join(f"{k}={v}" for k, v in s['parents'].items())
        print(f"  [{s['score']:3d}] {s['target_name']!r} → {s['ref_name']!r}")
        if parent_str:
            print(f"        ({parent_str})")
        print(f"        target_id={s['target_id']}, ref_id={s['ref_id']}")
        print()


def _suggest_hierarchy_matches(config, lookups_dir, level, fuzz, threshold):
    """Suggest matches for unmatched hierarchy-level records."""
    path = lookups_dir / f'{level}_lookup.csv'
    if not path.exists():
        print(f"Lookup not found: {path}")
        return

    df = pd.read_csv(path)

    unmatched_target = df[
        (df.get('match_type', pd.Series()) == 'no_candidate') &
        (df.get('target_name_raw', pd.Series()).fillna('') != '')
    ]
    unmatched_ref = df[
        (df.get('match_type', pd.Series()).isin(['reference_only'])) &
        (df.get('reference_name', pd.Series()).fillna('') != '')
    ]

    if len(unmatched_target) == 0 or len(unmatched_ref) == 0:
        print(f"No unmatched pairs at {level} level.")
        return

    print(f"Analyzing {len(unmatched_target)} unmatched target {level} names "
          f"against {len(unmatched_ref)} unmatched reference names...\n")

    suggestions = []
    for _, t_row in unmatched_target.iterrows():
        t_name = str(t_row.get('target_name_raw', ''))
        best_score = 0
        best_ref_name = None

        for _, r_row in unmatched_ref.iterrows():
            r_name = str(r_row.get('reference_name', ''))
            score = fuzz.ratio(t_name.lower(), r_name.lower())
            if score > best_score:
                best_score = score
                best_ref_name = r_name

        if best_score >= threshold and best_ref_name:
            suggestions.append({
                'target_name': t_name,
                'ref_name': best_ref_name,
                'score': best_score,
            })

    if not suggestions:
        print(f"No suggestions found above threshold ({threshold}).")
        return

    suggestions.sort(key=lambda x: x['score'], reverse=True)
    print(f"Found {len(suggestions)} potential {level} matches:\n")
    for s in suggestions:
        print(f"  [{s['score']:3d}] {s['target_name']!r} → {s['ref_name']!r}")


if __name__ == '__main__':
    main()
