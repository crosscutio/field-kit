#!/usr/bin/env python3
"""
Suggest manual matches for unmatched records.

Thin CLI wrapper around :mod:`match_bot.core.suggest`: prints suggestions and,
with --apply, writes them back to the lookup table.

Usage:
    python -m match_bot suggest --config path/to/config.yaml [--level leaf]
"""

import argparse


def run(config, level='leaf', threshold=70, apply=False, scope_depth=None, force_pass=False):
    """Suggest manual matches programmatically (prints; returns the suggestions).

    Args:
        config: A MatcherConfig instance.
        level: 'leaf' or a hierarchy label (e.g., 'province').
        threshold: Minimum fuzzy match score (0-100).
        apply: If True, write suggested matches back to the lookup table as
            match_type='manual'. Assignment is 1-to-1 per parent group.
        scope_depth: For leaf suggest only. Number of hierarchy levels used to
            form parent groups. Default = full depth.
        force_pass: Leaf only: threshold 0, rows tagged forced_pass='x'.
    """
    try:
        from match_bot.core import suggest as sg
        sg._fuzz()
    except ImportError:
        print("ERROR: rapidfuzz is required for match suggestions.")
        print("Install with: pip install rapidfuzz")
        return []

    labels = config.hierarchy_labels
    if level != 'leaf' and level not in labels:
        print(f"Unknown level: {level}")
        print(f"Available levels: leaf, {', '.join(labels)}")
        return []

    if level == 'leaf':
        if not (config.lookups_dir / 'leaf_lookup.csv').exists():
            print(f"Leaf lookup not found: {config.lookups_dir / 'leaf_lookup.csv'}")
            print("Run generate_lookups first.")
            return []
        if force_pass:
            threshold = 0
        scope = labels if scope_depth is None or scope_depth > len(labels) else labels[:max(0, scope_depth)]
        tby, rby, _ = sg._leaf_pools(config, list(scope))
        n_t, n_r = sum(len(v) for v in tby.values()), sum(len(v) for v in rby.values())
        if n_t == 0:
            print("No unmatched target records. Nothing to suggest.")
            return []
        if n_r == 0:
            print("No unmatched reference records to match against.")
            return []
        scope_desc = ', '.join(scope) if scope else 'no parents (global)'
        print(f"Analyzing {n_t} unmatched target records against {n_r} unmatched reference "
              f"records (globally-optimal assignment per group, scoped by {scope_desc})...\n")
    else:
        i = labels.index(level)
        tby, rby, _ = sg._hier_pools(config, level)
        n_t, n_r = sum(len(v) for v in tby.values()), sum(len(v) for v in rby.values())
        if not n_t or not n_r:
            print(f"No unmatched pairs at {level} level.")
            return []
        print(f"Analyzing {n_t} unmatched target {level} names against {n_r} unmatched "
              f"reference names (scoped by {', '.join(labels[:i]) or 'no parents'})...\n")

    suggestions = sg.suggest(config, level=level, threshold=threshold, scope_depth=scope_depth)
    if not suggestions:
        print(f"No suggestions found above threshold ({threshold}).")
        return []

    what = 'matches' if level == 'leaf' else f'{level} matches'
    print(f"Found {len(suggestions)} potential {what}:\n")
    for s in suggestions:
        print(f"  [{s['score']:3.0f}] {s['target_name']!r} → {s['ref_name']!r}")
        parent_str = ' > '.join(f"{k}={v}" for k, v in s['parents'].items())
        if parent_str:
            print(f"        ({parent_str})")
        if level == 'leaf':
            print(f"        target_id={s['target_id']}, ref_id={s['ref_id']}")
            print()

    if apply:
        res = sg.apply(config, level, suggestions, rationale_prefix='fuzzy suggest', force_pass=force_pass)
        print(f"\nApplied {res['applied']} suggestions; dropped {res['dropped']} now-redundant reference_only rows.")
    return suggestions


def main():
    parser = argparse.ArgumentParser(description='Suggest manual matches for unmatched records')
    parser.add_argument('--config', '-c', required=True, help='Path to the YAML config file')
    parser.add_argument('--level', '-l', default='leaf',
        help='Level to suggest matches for: "leaf" or a hierarchy label (e.g., "province")')
    parser.add_argument('--threshold', '-t', type=int, default=70,
        help='Minimum fuzzy match score (0-100) to suggest (default: 70)')
    parser.add_argument('--apply', action='store_true',
        help='Write suggestions back to the lookup as match_type=manual')
    parser.add_argument('--scope-depth', type=int, default=None,
        help='Leaf only: hierarchy depth for parent grouping. Default = all levels.')
    parser.add_argument('--force-pass', action='store_true',
        help='Leaf only: force a best-match for every unmatched pair within each '
             'parent group, ignoring threshold. Marks rows with forced_pass=x.')
    args = parser.parse_args()

    from match_bot.core.config import MatcherConfig
    config = MatcherConfig.from_yaml(args.config)
    run(config, level=args.level, threshold=args.threshold, apply=args.apply,
        scope_depth=args.scope_depth, force_pass=args.force_pass)


if __name__ == '__main__':
    main()
