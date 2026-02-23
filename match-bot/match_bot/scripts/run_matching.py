#!/usr/bin/env python3
"""
Run the generic matching pipeline.

Usage:
    python -m match_bot match --config path/to/config.yaml [--verbose]
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description='Generic Matcher — match named places to a GIS dataset'
    )
    parser.add_argument(
        '--config', '-c', required=True,
        help='Path to the YAML config file'
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Print verbose output'
    )

    args = parser.parse_args()

    # Check dependencies
    _check_dependencies()

    from match_bot.core.config import MatcherConfig
    from match_bot.core.matching import MatchingPipeline

    # Load config
    config = MatcherConfig.from_yaml(args.config)
    print(f"Project: {config.project_name}")
    print(f"Hierarchy levels: {config.hierarchy_labels}")

    # Create and run pipeline
    pipeline = MatchingPipeline(config)
    pipeline.load_data()
    pipeline.load_lookups()
    pipeline.apply_hierarchy_mappings()
    result = pipeline.run()

    # Write matched output
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    matched_path = output_dir / 'matched.csv'
    result.matched.to_csv(matched_path, index=False)
    print(f"\nWrote {len(result.matched)} matches to {matched_path}")

    # Print summary
    print(f"\n{'=' * 50}")
    print("MATCHING SUMMARY")
    print(f"{'=' * 50}")
    print(f"  Matched:            {result.counts.get('matched', 0)}")
    print(f"  Unmatched reference: {result.counts.get('unmatched_ref', 0)}")
    print(f"  Unmatched target:    {result.counts.get('unmatched_target', 0)}")
    print(f"  Match rate:          {result.match_rate * 100:.1f}%")

    if args.verbose:
        print("\n  Match type breakdown:")
        for key, val in sorted(result.counts.items()):
            if key not in ('matched', 'unmatched_ref', 'unmatched_target'):
                print(f"    {key}: {val}")


def _check_dependencies():
    """Check that required dependencies are available."""
    missing = []
    try:
        import yaml
    except ImportError:
        missing.append('pyyaml')
    try:
        import jellyfish
    except ImportError:
        missing.append('jellyfish')
    try:
        from scipy.optimize import linear_sum_assignment
    except ImportError:
        missing.append('scipy')

    if missing:
        print("ERROR: Required dependencies are missing:")
        for pkg in missing:
            print(f"  - {pkg}")
        print(f"\nInstall with: pip install {' '.join(missing)}")
        sys.exit(1)


if __name__ == '__main__':
    main()
