#!/usr/bin/env python3
"""
Match-Bot CLI.

Usage:
    python -m match_bot match --config config.yaml
    python -m match_bot lookups --config config.yaml
    python -m match_bot suggest --config config.yaml [--threshold 70]
"""

import sys


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__.strip())
        sys.exit(0)
    if sys.argv[1].startswith('-'):
        print(f"Unknown option: {sys.argv[1]}")
        print(__doc__.strip())
        sys.exit(1)

    subcommand = sys.argv.pop(1)

    try:
        if subcommand == 'match':
            from match_bot.scripts.run_matching import main as run_main
            run_main()
        elif subcommand == 'lookups':
            from match_bot.scripts.generate_lookups import main as lookups_main
            lookups_main()
        elif subcommand == 'suggest':
            from match_bot.scripts.suggest_matches import main as suggest_main
            suggest_main()
        else:
            print(f"Unknown subcommand: {subcommand}")
            print("Available: match, lookups, suggest")
            sys.exit(1)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
