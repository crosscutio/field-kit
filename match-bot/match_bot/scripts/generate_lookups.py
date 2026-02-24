#!/usr/bin/env python3
"""
Generate lookup tables from match results.

Usage:
    python -m match_bot lookups --config path/to/config.yaml
"""

import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(
        description='Generate hierarchy and leaf lookup tables from match results'
    )
    parser.add_argument(
        '--config', '-c', required=True,
        help='Path to the YAML config file'
    )
    args = parser.parse_args()

    from match_bot.core.config import MatcherConfig
    from match_bot.core.lookup import (
        generate_hierarchy_lookup,
        generate_leaf_lookup,
        load_lookup,
        preserve_manual_matches,
        save_lookup,
    )
    from match_bot.core.matching import MatchingPipeline

    config = MatcherConfig.from_yaml(args.config)
    print(f"Project: {config.project_name}")

    # Run the pipeline to get match results
    pipeline = MatchingPipeline(config)
    pipeline.load_data()
    pipeline.load_lookups()
    pipeline.apply_hierarchy_mappings()
    result = pipeline.run()

    lookups_dir = config.lookups_dir
    lookups_dir.mkdir(parents=True, exist_ok=True)
    labels = config.hierarchy_labels

    # Generate hierarchy lookups
    for i, label in enumerate(labels):
        target_col = f'_target_{label}'
        ref_col = f'_ref_{label}'

        # Check that columns exist in matched data
        if result.matched.empty:
            print(f"  No matches — skipping {label} lookup")
            continue

        if target_col not in result.matched.columns:
            print(f"  Column {target_col} not found — skipping {label} lookup")
            continue

        lookup = generate_hierarchy_lookup(
            matched=result.matched,
            unmatched_target=result.unmatched_target,
            unmatched_ref=result.unmatched_ref,
            level_label=label,
            target_col=target_col,
            ref_col=ref_col,
        )

        # Preserve manual matches from existing lookup
        existing_path = lookups_dir / f'{label}_lookup.csv'
        if existing_path.exists():
            existing = load_lookup(str(existing_path))
            lookup = preserve_manual_matches(lookup, existing)

        out_path = lookups_dir / f'{label}_lookup.csv'
        save_lookup(lookup, str(out_path))
        print(f"  Wrote {len(lookup)} entries to {out_path}")

    # Generate leaf lookup
    leaf_lookup = generate_leaf_lookup(
        matched=result.matched,
        unmatched_target=result.unmatched_target,
        unmatched_ref=result.unmatched_ref,
        config=config,
    )

    # Preserve manual matches
    existing_leaf_path = lookups_dir / 'leaf_lookup.csv'
    if existing_leaf_path.exists():
        existing_leaf = load_lookup(str(existing_leaf_path))
        leaf_lookup = preserve_manual_matches(leaf_lookup, existing_leaf)

    save_lookup(leaf_lookup, str(existing_leaf_path))
    print(f"  Wrote {len(leaf_lookup)} entries to {existing_leaf_path}")

    # Print summary
    if 'unmatched' in leaf_lookup.columns:
        matched_count = len(leaf_lookup[leaf_lookup['unmatched'] != 'x'])
    else:
        matched_count = len(leaf_lookup)
    print(f"\nLookup Summary:")
    print(f"  Total entries: {len(leaf_lookup)}")
    print(f"  Matched:       {matched_count}")
    print(f"  Unmatched:     {len(leaf_lookup) - matched_count}")
    if 'match_type' in leaf_lookup.columns:
        print(f"  Match types:")
        for mt, count in leaf_lookup['match_type'].value_counts().items():
            print(f"    {mt}: {count}")


if __name__ == '__main__':
    main()
