"""
Central matching pipeline for Match-Bot.

The MatchingPipeline class orchestrates data loading, standardization,
lookup application, and multi-step matching (exact -> fuzzy distance ->
fuzzy score) to produce a MatchResult.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

from .config import MatcherConfig
from .data_loader import load_dataset
from .fuzzy import (
    compute_levenshtein_score,
    fuzzy_match_1_to_1,
    validate_number_match,
)
from .lookup import get_hierarchy_mappings, get_manual_matches, load_lookup
from .standardization import apply_name_mappings, normalize_series, remove_accents


# Internal column naming convention:
# _target_h0, _target_h1, ..., _target_name, _target_id
# _ref_h0, _ref_h1, ..., _ref_name, _ref_id
# The matching functions use: h0, h1, ..., name, id, ref_id


@dataclass
class MatchResult:
    """Results from running the matching pipeline."""
    matched: pd.DataFrame
    unmatched_ref: pd.DataFrame
    unmatched_target: pd.DataFrame
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def match_rate(self) -> float:
        """Match rate = matched / (matched + unmatched_reference)."""
        total = self.counts.get('matched', 0) + self.counts.get('unmatched_ref', 0)
        if total == 0:
            return 0.0
        return self.counts.get('matched', 0) / total


class MatchingPipeline:
    """Orchestrates the full matching workflow.

    Usage:
        config = MatcherConfig.from_yaml('config.yaml')
        pipeline = MatchingPipeline(config)
        pipeline.load_data()
        pipeline.load_lookups()
        pipeline.apply_hierarchy_mappings()
        result = pipeline.run()
    """

    def __init__(self, config: MatcherConfig):
        self.config = config
        self.ref: Optional[pd.DataFrame] = None
        self.target: Optional[pd.DataFrame] = None
        self.hierarchy_lookups: Dict[str, pd.DataFrame] = {}
        self.leaf_lookup: Optional[pd.DataFrame] = None
        self._group_columns: List[str] = []

    def load_data(self):
        """Load and standardize both reference and target datasets."""
        cfg = self.config
        labels = cfg.hierarchy_labels

        # Build group column names (h0, h1, ...)
        self._group_columns = [f'h{i}' for i in range(len(labels))]

        # --- Load reference ---
        ref_path = cfg.resolve_file(cfg.reference.file)
        ref_raw = load_dataset(str(ref_path), cfg.reference.format, cfg.reference.layer)
        self.ref = self._normalize_dataset(ref_raw, cfg.reference, prefix='ref')

        # --- Load target ---
        target_path = cfg.resolve_file(cfg.target.file)
        target_raw = load_dataset(str(target_path), cfg.target.format, cfg.target.layer)
        self.target = self._normalize_dataset(target_raw, cfg.target, prefix='target')

        print(f"Loaded {len(self.ref)} reference records, {len(self.target)} target records")

    def _normalize_dataset(self, df: pd.DataFrame, ds_cfg, prefix: str) -> pd.DataFrame:
        """Map original columns to internal schema and apply standardization."""
        cfg = self.config
        labels = cfg.hierarchy_labels
        result = pd.DataFrame()

        # Map hierarchy columns
        for i, level in enumerate(ds_cfg.hierarchy):
            col_name = f'h{i}'
            if level.column in df.columns:
                result[col_name] = df[level.column]
            else:
                raise ValueError(f"Column '{level.column}' not found in {ds_cfg.file}. "
                                 f"Available: {list(df.columns)[:15]}")

        # Map name and id columns
        if ds_cfg.name_column not in df.columns:
            raise ValueError(f"Name column '{ds_cfg.name_column}' not found in {ds_cfg.file}")
        if ds_cfg.id_column not in df.columns:
            raise ValueError(f"ID column '{ds_cfg.id_column}' not found in {ds_cfg.file}")

        result['name'] = df[ds_cfg.name_column]
        result['name_raw'] = df[ds_cfg.name_column].copy()
        result['id'] = df[ds_cfg.id_column].astype(str)

        # Store original hierarchy values (raw) for lookup table output
        for i, level in enumerate(ds_cfg.hierarchy):
            result[f'{prefix}_{level.label}_raw'] = df[level.column]

        # Apply standardization
        std = cfg.standardization
        case = std.case or 'lower'

        columns_to_std = self._group_columns + ['name']
        for col in columns_to_std:
            if col not in result.columns:
                continue
            if std.remove_accents:
                result[col] = result[col].apply(
                    lambda x: remove_accents(str(x)) if pd.notna(x) else x
                )
            result[col] = normalize_series(result[col], case=case, remove_accent=False)

        return result

    def load_lookups(self):
        """Load existing lookup tables from the lookups directory."""
        lookups_dir = self.config.lookups_dir
        labels = self.config.hierarchy_labels

        # Load hierarchy lookups
        for label in labels:
            path = lookups_dir / f'{label}_lookup.csv'
            if path.exists():
                self.hierarchy_lookups[label] = load_lookup(str(path))
                print(f"  Loaded {label} lookup: {len(self.hierarchy_lookups[label])} entries")

        # Load leaf lookup (for manual matches)
        leaf_path = lookups_dir / 'leaf_lookup.csv'
        if leaf_path.exists():
            self.leaf_lookup = load_lookup(str(leaf_path))
            print(f"  Loaded leaf lookup: {len(self.leaf_lookup)} entries")

    def apply_hierarchy_mappings(self):
        """Apply hierarchy lookup mappings to target names."""
        if not self.hierarchy_lookups:
            return

        labels = self.config.hierarchy_labels
        case = self.config.standardization.case or 'lower'

        for i, label in enumerate(labels):
            if label not in self.hierarchy_lookups:
                continue

            mappings = get_hierarchy_mappings(self.hierarchy_lookups[label])
            if not mappings:
                continue

            col = f'h{i}'
            mapped_count = 0
            for idx, row in self.target.iterrows():
                val = row[col]
                if pd.isna(val):
                    continue
                key = str(val).lower()
                if key in mappings:
                    new_val = mappings[key]
                    # Apply case transformation to match standardization
                    if case == 'upper':
                        new_val = new_val.upper()
                    elif case == 'lower':
                        new_val = new_val.lower()
                    elif case == 'title':
                        new_val = new_val.title()
                    self.target.at[idx, col] = new_val
                    mapped_count += 1

            if mapped_count > 0:
                print(f"  Applied {mapped_count} {label} lookup mappings")

        # Report unmatched hierarchy names
        for i, label in enumerate(labels):
            col = f'h{i}'
            target_vals = set(self.target[col].dropna().unique())
            ref_vals = set(self.ref[col].dropna().unique())
            unmatched = target_vals - ref_vals
            if unmatched:
                valid = sorted([n for n in unmatched if pd.notna(n)])
                print(f"  Warning: {len(valid)} {label} names in target not found in reference:")
                for name in valid[:10]:
                    print(f"    - {name}")
                if len(valid) > 10:
                    print(f"    ... and {len(valid) - 10} more")

    def run(self) -> MatchResult:
        """Execute the matching pipeline.

        Steps:
            0. Apply manual matches from existing leaf lookup.
            1. Exact match via merge on [group_columns + name].
            2. Fuzzy match (Levenshtein distance <= threshold).
            3. Fuzzy match (Levenshtein score < threshold).

        Returns:
            MatchResult with matched, unmatched_ref, unmatched_target DataFrames.
        """
        mcfg = self.config.matching
        group_cols = self._group_columns

        # Filter out reference records with NA names
        na_count = self.ref['name'].isna().sum()
        if na_count > 0:
            print(f"Note: {na_count} reference records have NA names and cannot be matched")
            ref = self.ref[self.ref['name'].notna()].copy()
        else:
            ref = self.ref.copy()

        target = self.target.copy()

        matched_ref_ids: Set[str] = set()
        matched_target_ids: Set[str] = set()
        all_matched: List[pd.DataFrame] = []
        match_type_counts: Dict[str, int] = {}

        # ---- Step 0: Manual matches ----
        manual_matches = {}
        if self.leaf_lookup is not None:
            manual_matches = get_manual_matches(self.leaf_lookup)

        if manual_matches:
            print(f"\nStep 0: Applying {len(manual_matches)} manual matches...")
            manual_rows = []
            for target_id, ref_id in manual_matches.items():
                t_row = target[target['id'] == target_id]
                r_row = ref[ref['id'] == ref_id]
                if len(t_row) == 0 or len(r_row) == 0:
                    continue
                if ref_id in matched_ref_ids:
                    continue

                row = self._build_match_row(t_row.iloc[0], r_row.iloc[0], 'manual')
                manual_rows.append(row)
                matched_ref_ids.add(ref_id)
                matched_target_ids.add(target_id)

            if manual_rows:
                manual_df = pd.DataFrame(manual_rows)
                all_matched.append(manual_df)
                match_type_counts['manual'] = len(manual_rows)
                print(f"  Applied {len(manual_rows)} manual matches")

        # Exclude manually matched records from pools
        target_pool = target[~target['id'].isin(matched_target_ids)].copy()
        ref_pool = ref[~ref['id'].isin(matched_ref_ids)].copy()

        # ---- Step 1: Exact matches ----
        print("\nStep 1: Finding exact matches...")
        merge_cols = group_cols + ['name']
        exact = target_pool.merge(
            ref_pool,
            how='inner',
            on=merge_cols,
            suffixes=('_target', '_ref'),
        )

        if not exact.empty:
            # Enforce one-to-one
            exact = exact.drop_duplicates(subset=['id_ref'], keep='first')
            exact = exact.drop_duplicates(subset=['id_target'], keep='first')

            exact_rows = []
            for _, row in exact.iterrows():
                t_data = target_pool[target_pool['id'] == row['id_target']].iloc[0]
                r_data = ref_pool[ref_pool['id'] == row['id_ref']].iloc[0]
                exact_rows.append(self._build_match_row(t_data, r_data, 'exact', levenshtein_distance=0))
                matched_ref_ids.add(row['id_ref'])
                matched_target_ids.add(row['id_target'])

            exact_df = pd.DataFrame(exact_rows)
            all_matched.append(exact_df)
            match_type_counts['exact'] = len(exact_rows)
            print(f"  Found {len(exact_rows)} exact matches")

        target_pool = target[~target['id'].isin(matched_target_ids)].copy()
        ref_pool = ref[~ref['id'].isin(matched_ref_ids)].copy()

        # ---- Step 2: Fuzzy match (distance <= threshold) ----
        if len(target_pool) > 0 and len(ref_pool) > 0:
            threshold = mcfg.levenshtein_distance_threshold
            print(f"\nStep 2: Fuzzy matching (Levenshtein distance <= {threshold})...")

            fuzzy = fuzzy_match_1_to_1(
                ref_pool, target_pool,
                group_columns=group_cols if group_cols else None,
                id_col='id', match_col='name', ref_id_col='id',
            )

            if not fuzzy.empty and 'levenshtein_distance' in fuzzy.columns:
                new_matches = fuzzy[fuzzy['levenshtein_distance'] <= threshold].copy()
                if mcfg.validate_numbers and len(new_matches) > 0:
                    new_matches = validate_number_match(new_matches, source_col='name')

                if len(new_matches) > 0:
                    fuzzy_rows = self._collect_fuzzy_matches(
                        new_matches, target_pool, ref_pool, 'fuzzy_dist'
                    )
                    for row in fuzzy_rows:
                        matched_ref_ids.add(row['_ref_id'])
                        matched_target_ids.add(row['_target_id'])

                    all_matched.append(pd.DataFrame(fuzzy_rows))
                    match_type_counts['fuzzy_dist'] = len(fuzzy_rows)
                    print(f"  Found {len(fuzzy_rows)} fuzzy matches (distance <= {threshold})")

            target_pool = target[~target['id'].isin(matched_target_ids)].copy()
            ref_pool = ref[~ref['id'].isin(matched_ref_ids)].copy()

        # ---- Step 3: Fuzzy match (score < threshold) ----
        if len(target_pool) > 0 and len(ref_pool) > 0 and mcfg.levenshtein_score_threshold:
            score_threshold = mcfg.levenshtein_score_threshold
            print(f"\nStep 3: Fuzzy matching (Levenshtein score < {score_threshold})...")

            fuzzy = fuzzy_match_1_to_1(
                ref_pool, target_pool,
                group_columns=group_cols if group_cols else None,
                id_col='id', match_col='name', ref_id_col='id',
            )

            if not fuzzy.empty and 'levenshtein_distance' in fuzzy.columns:
                fuzzy['lev_score'] = fuzzy['levenshtein_distance'] / fuzzy['name'].str.len()
                new_matches = fuzzy[fuzzy['lev_score'] < score_threshold].copy()

                if len(new_matches) > 0:
                    fuzzy_rows = self._collect_fuzzy_matches(
                        new_matches, target_pool, ref_pool, 'fuzzy_score'
                    )
                    for row in fuzzy_rows:
                        matched_ref_ids.add(row['_ref_id'])
                        matched_target_ids.add(row['_target_id'])

                    all_matched.append(pd.DataFrame(fuzzy_rows))
                    match_type_counts['fuzzy_score'] = len(fuzzy_rows)
                    print(f"  Found {len(fuzzy_rows)} fuzzy matches (score < {score_threshold})")

        # ---- Assemble results ----
        if all_matched:
            matched = pd.concat(all_matched, ignore_index=True)
        else:
            matched = pd.DataFrame()

        unmatched_target = target[~target['id'].isin(matched_target_ids)].copy()
        unmatched_ref = ref[~ref['id'].isin(matched_ref_ids)].copy()

        # Rename internal columns for output
        unmatched_target = self._add_output_columns(unmatched_target, 'target')
        unmatched_ref = self._add_output_columns(unmatched_ref, 'ref')

        total_matched = len(matched) if not matched.empty else 0
        counts = {
            'matched': total_matched,
            'unmatched_ref': len(unmatched_ref),
            'unmatched_target': len(unmatched_target),
        }
        counts.update(match_type_counts)

        return MatchResult(
            matched=matched,
            unmatched_ref=unmatched_ref,
            unmatched_target=unmatched_target,
            counts=counts,
        )

    def _build_match_row(self, t_row: pd.Series, r_row: pd.Series,
                         match_type: str, levenshtein_distance=None) -> dict:
        """Build a standardized match record from target and reference rows."""
        labels = self.config.hierarchy_labels
        row = {}

        for i, label in enumerate(labels):
            col = f'h{i}'
            row[f'_target_{label}'] = t_row.get(col, '')
            row[f'_ref_{label}'] = r_row.get(col, '')

        row['_target_name'] = t_row.get('name', '')
        row['_target_name_raw'] = t_row.get('name_raw', '')
        row['_target_id'] = str(t_row.get('id', ''))
        row['_ref_name'] = r_row.get('name', '')
        row['_ref_id'] = str(r_row.get('id', ''))
        row['_match_type'] = match_type
        row['_levenshtein_distance'] = levenshtein_distance
        row['_mapping_rationale'] = ''

        return row

    def _collect_fuzzy_matches(self, fuzzy_df: pd.DataFrame,
                                target_pool: pd.DataFrame,
                                ref_pool: pd.DataFrame,
                                match_type: str) -> list:
        """Convert fuzzy match results into standardized match rows."""
        rows = []
        for _, frow in fuzzy_df.iterrows():
            target_id = str(frow['id'])
            ref_id = str(frow['ref_id_lev'])
            if pd.isna(ref_id) or ref_id == 'None':
                continue

            t_data = target_pool[target_pool['id'] == target_id]
            r_data = ref_pool[ref_pool['id'] == ref_id]
            if len(t_data) == 0 or len(r_data) == 0:
                continue

            row = self._build_match_row(
                t_data.iloc[0], r_data.iloc[0], match_type,
                levenshtein_distance=frow.get('levenshtein_distance'),
            )
            rows.append(row)
        return rows

    def _add_output_columns(self, df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        """Add prefixed output columns to unmatched records."""
        if df.empty:
            return df

        labels = self.config.hierarchy_labels
        result = df.copy()
        for i, label in enumerate(labels):
            col = f'h{i}'
            result[f'_{prefix}_{label}'] = result.get(col, '')
        result[f'_{prefix}_name'] = result.get('name', '')
        result[f'_{prefix}_name_raw'] = result.get('name_raw', '')
        result[f'_{prefix}_id'] = result.get('id', '')
        return result
