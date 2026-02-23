"""
Match statistics and Markdown report generation for Match-Bot.
"""

from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .config import MatcherConfig


def compute_match_statistics(lookup_path: str) -> Dict:
    """Read a lookup CSV and compute match statistics.

    Args:
        lookup_path: Path to a lookup CSV file.

    Returns:
        Dict with keys: total, matched, unmatched, match_rate,
        and per-match_type counts.
    """
    p = Path(lookup_path)
    if not p.exists():
        return {'total': 0, 'matched': 0, 'unmatched': 0, 'match_rate': 0.0}

    df = pd.read_csv(p)
    total = len(df)

    if 'unmatched' in df.columns:
        unmatched = int((df['unmatched'] == 'x').sum())
        matched = total - unmatched
    elif 'match_type' in df.columns:
        unmatched_types = {'no_candidate', 'reference_only'}
        unmatched = int(df['match_type'].isin(unmatched_types).sum())
        matched = total - unmatched
    else:
        matched = total
        unmatched = 0

    # Count by match_type
    type_counts = {}
    if 'match_type' in df.columns:
        for mt, count in df['match_type'].value_counts().items():
            type_counts[mt] = int(count)

    # Match rate = matched / (matched + unmatched_reference)
    ref_only = type_counts.get('reference_only', 0)
    denominator = matched + ref_only
    match_rate = matched / denominator if denominator > 0 else 0.0

    return {
        'total': total,
        'matched': matched,
        'unmatched': unmatched,
        'match_rate': match_rate,
        'type_counts': type_counts,
    }


def generate_markdown_report(config: MatcherConfig, output_path: Optional[str] = None) -> str:
    """Generate a Markdown report summarizing match results.

    Args:
        config: MatcherConfig for the project.
        output_path: Optional path to write the report. If None, report is returned as string.

    Returns:
        Markdown report string.
    """
    lookups_dir = config.lookups_dir
    labels = config.hierarchy_labels

    lines = [
        f"# {config.project_name} — Match Report",
        "",
        "## Summary",
        "",
        "| Level | Total | Matched | Unmatched | Match Rate |",
        "|-------|------:|--------:|----------:|-----------:|",
    ]

    all_stats = {}

    # Hierarchy levels
    for label in labels:
        path = lookups_dir / f'{label}_lookup.csv'
        stats = compute_match_statistics(str(path))
        all_stats[label] = stats
        rate_pct = f"{stats['match_rate'] * 100:.1f}%"
        lines.append(
            f"| {label.title()} | {stats['total']} | {stats['matched']} | "
            f"{stats['unmatched']} | {rate_pct} |"
        )

    # Leaf level
    leaf_path = lookups_dir / 'leaf_lookup.csv'
    leaf_stats = compute_match_statistics(str(leaf_path))
    all_stats['leaf'] = leaf_stats
    rate_pct = f"{leaf_stats['match_rate'] * 100:.1f}%"
    lines.append(
        f"| **Leaf (name)** | {leaf_stats['total']} | {leaf_stats['matched']} | "
        f"{leaf_stats['unmatched']} | {rate_pct} |"
    )

    # Match type breakdown
    if leaf_stats.get('type_counts'):
        lines.extend([
            "",
            "## Match Type Breakdown (Leaf Level)",
            "",
            "| Match Type | Count |",
            "|------------|------:|",
        ])
        for mt, count in sorted(leaf_stats['type_counts'].items()):
            lines.append(f"| {mt} | {count} |")

    # Unmatched records
    if leaf_path.exists():
        df = pd.read_csv(str(leaf_path))

        # Unmatched target records
        if 'unmatched' in df.columns:
            unmatched_target = df[(df['unmatched'] == 'x') & (df.get('target_id', pd.Series()).fillna('') != '')]
        else:
            unmatched_target = df[df.get('match_type', pd.Series()).isin(['no_candidate'])]

        if len(unmatched_target) > 0:
            lines.extend([
                "",
                "## Unmatched Target Records",
                "",
            ])
            name_col = 'target_name_raw' if 'target_name_raw' in unmatched_target.columns else 'target_name_standardized'
            for _, row in unmatched_target.head(50).iterrows():
                name = row.get(name_col, '?')
                # Include hierarchy context
                hierarchy_parts = []
                for label in labels:
                    val = row.get(f'target_{label}', '')
                    if pd.notna(val) and str(val).strip():
                        hierarchy_parts.append(f"{label}={val}")
                context = ' | '.join(hierarchy_parts)
                lines.append(f"- {name} ({context})")
            if len(unmatched_target) > 50:
                lines.append(f"- ... and {len(unmatched_target) - 50} more")

        # Unmatched reference records
        if 'match_type' in df.columns:
            unmatched_ref = df[df['match_type'] == 'reference_only']
        else:
            unmatched_ref = pd.DataFrame()

        if len(unmatched_ref) > 0:
            lines.extend([
                "",
                "## Unmatched Reference Records",
                "",
            ])
            for _, row in unmatched_ref.head(50).iterrows():
                name = row.get('ref_name', '?')
                hierarchy_parts = []
                for label in labels:
                    val = row.get(f'ref_{label}', '')
                    if pd.notna(val) and str(val).strip():
                        hierarchy_parts.append(f"{label}={val}")
                context = ' | '.join(hierarchy_parts)
                lines.append(f"- {name} ({context})")
            if len(unmatched_ref) > 50:
                lines.append(f"- ... and {len(unmatched_ref) - 50} more")

    report = '\n'.join(lines) + '\n'

    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report, encoding='utf-8')
        print(f"Report written to {output_path}")

    return report
