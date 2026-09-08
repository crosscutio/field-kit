"""Structured fuzzy suggestions over the lookup tables.

Everything here returns data; ``scripts/suggest_matches`` wraps it for the
CLI. Scores are rapidfuzz 0-100 (max of ``ratio`` and ``token_set_ratio``)
with standalone digit tokens stripped first, because shared trailing numbers
("Ward 2" vs "Zone 2") otherwise manufacture high scores between unrelated
names.

Keys:
    leaf level      target_key = target_id, ref_key = ref_id
    hierarchy level target_key = lower(target_name_raw), ref_key = reference_name
"""

import contextlib
import io
import re
from collections import defaultdict
from typing import Callable, Dict, List, Optional

import pandas as pd

from .lookup import load_lookup, norm_id

_NUM_TOKEN = re.compile(r'\b\d+\b')
_SCORE_RE = re.compile(r'score\s*=\s*(\d+)')

AUTO_SUGGEST_PREFIX = 'auto: suggest'


def _fuzz():
    try:
        from rapidfuzz import fuzz
    except ImportError as e:
        raise ImportError('rapidfuzz is required for suggestions: pip install rapidfuzz') from e
    return fuzz


def _clean(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ''
    return ' '.join(_NUM_TOKEN.sub(' ', str(s).lower()).split())


def score_pair(a, b) -> float:
    """Fuzzy score in [0, 100] between two names."""
    fuzz = _fuzz()
    a, b = _clean(a), _clean(b)
    if not a or not b:
        return 0.0
    return float(max(fuzz.ratio(a, b), fuzz.token_set_ratio(a, b)))


def score_from_rationale(rationale) -> Optional[int]:
    m = _SCORE_RE.search(str(rationale or ''))
    return int(m.group(1)) if m else None


def optimal_assignment(targets, refs, score_fn: Callable, threshold: float):
    """Globally-optimal 1-to-1 pairing; pairs below ``threshold`` are dropped
    after assignment so a low-score collision can't block a high-score pair."""
    if not targets or not refs:
        return []
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    matrix = np.zeros((len(targets), len(refs)), dtype=float)
    for i, t in enumerate(targets):
        for j, r in enumerate(refs):
            matrix[i, j] = score_fn(t, r)
    rows, cols = linear_sum_assignment(100.0 - matrix)
    return [(targets[i], refs[j], float(matrix[i, j]))
            for i, j in zip(rows, cols) if matrix[i, j] >= threshold]


# ---------------------------------------------------------------------------
# Level views: unmatched targets / refs with parent keys
# ---------------------------------------------------------------------------

def _leaf_frame(config) -> pd.DataFrame:
    return load_lookup(str(config.lookups_dir / 'leaf_lookup.csv'))


def _leaf_name(row) -> str:
    v = row.get('target_name_standardized')
    if pd.isna(v) or not str(v).strip():
        v = row.get('target_name_raw', '')
    return '' if pd.isna(v) else str(v).strip()


def _parent_key(row, prefix, labels):
    return tuple(str(row.get(f'{prefix}_{lbl}', '') if pd.notna(row.get(f'{prefix}_{lbl}', '')) else '')
                 .lower().strip() for lbl in labels)


def _leaf_pools(config, scope_labels):
    """(targets_by_parent, refs_by_parent, df) for no_candidate / reference_only rows."""
    df = _leaf_frame(config)
    if df.empty or 'match_type' not in df.columns:
        return {}, {}, df
    tby, rby = defaultdict(list), defaultdict(list)
    for _, t in df[df['match_type'] == 'no_candidate'].iterrows():
        if _leaf_name(t):
            tby[_parent_key(t, 'target', scope_labels)].append(t)
    for _, r in df[df['match_type'] == 'reference_only'].iterrows():
        if str(r.get('ref_name', '')).strip() and pd.notna(r.get('ref_name')):
            rby[_parent_key(r, 'ref', scope_labels)].append(r)
    return tby, rby, df


def _hier_pipeline(config):
    from .matching import MatchingPipeline
    p = MatchingPipeline(config)
    with contextlib.redirect_stdout(io.StringIO()):
        p.load_data()
        p.load_lookups()
        p.apply_hierarchy_mappings()
    return p


def _hier_pools(config, level, pipeline=None):
    """Distinct unmatched (parents..., value) tuples per side, grouped by parent.

    Values already decided in the level's lookup (manual / no_equivalent) are
    excluded from the target side.
    """
    labels = config.hierarchy_labels
    i = labels.index(level)
    p = pipeline or _hier_pipeline(config)
    h = f'h{i}'
    parents = [f'h{k}' for k in range(i)]
    t_raw, r_raw = f'target_{level}_raw', f'ref_{level}_raw'

    def distinct(df, raw):
        cols = parents + [h] + ([raw] if raw in df.columns else [])
        return df[cols].dropna(subset=[h]).drop_duplicates(subset=parents + [h])

    t_d, r_d = distinct(p.target, t_raw), distinct(p.ref, r_raw)
    key = lambda row: tuple(str(row[c]).lower() for c in parents + [h])
    r_keys = {key(r) for _, r in r_d.iterrows()}
    t_keys = {key(t) for _, t in t_d.iterrows()}

    decided = set()
    lk = load_lookup(str(config.lookups_dir / f'{level}_lookup.csv'))
    if not lk.empty and 'match_type' in lk.columns:
        dec = lk[lk['match_type'].isin(['manual', 'no_equivalent'])]
        decided = {str(v).lower() for v in dec['target_name_standardized'].dropna()}

    tby, rby = defaultdict(list), defaultdict(list)
    for _, t in t_d.iterrows():
        if key(t) not in r_keys and str(t[h]).lower() not in decided:
            tby[tuple(str(t[c]).lower() for c in parents)].append(t)
    for _, r in r_d.iterrows():
        if key(r) not in t_keys:
            rby[tuple(str(r[c]).lower() for c in parents)].append(r)
    return tby, rby, p


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest(config, level='leaf', threshold=70, scope_depth=None, pipeline=None) -> List[Dict]:
    """Ranked 1-to-1 suggestions for a level, highest score first.

    Each dict: target_key, target_name, ref_key, ref_name, score, parents,
    plus target_id / ref_id at the leaf level.
    """
    labels = config.hierarchy_labels
    if level == 'leaf':
        scope = list(labels) if scope_depth is None or scope_depth > len(labels) \
            else list(labels[:max(0, scope_depth)])
        tby, rby, _ = _leaf_pools(config, scope)
        out = []
        for pk, targets in tby.items():
            refs = rby.get(pk, [])
            for t, r, s in optimal_assignment(
                    targets, refs, lambda t, r: score_pair(_leaf_name(t), r.get('ref_name', '')),
                    threshold):
                out.append(_leaf_suggestion(t, r, s, labels))
        out.sort(key=lambda x: -x['score'])
        return out

    if level not in labels:
        raise ValueError(f'Unknown level {level!r}; expected leaf or one of {labels}')
    i = labels.index(level)
    tby, rby, _ = _hier_pools(config, level, pipeline)
    h, t_raw = f'h{i}', f'target_{level}_raw'
    out = []
    for pk, ts in tby.items():
        rs = rby.get(pk, [])
        for t, r, s in optimal_assignment(ts, rs, lambda t, r: score_pair(t[h], r[h]), threshold):
            raw = str(t[t_raw]) if t_raw in t.index else str(t[h])
            out.append({
                'target_key': str(t[h]).lower(),
                'target_name': raw,
                'ref_key': str(r[h]),
                'ref_name': str(r[h]),
                'score': s,
                'parents': {labels[k]: str(t[f'h{k}']) for k in range(i)},
            })
    out.sort(key=lambda x: (-x['score'], x['target_name']))
    return out


def _leaf_suggestion(t, r, score, labels):
    parents = {}
    for lbl in labels:
        v = t.get(f'target_{lbl}', '')
        if pd.notna(v) and str(v).strip():
            parents[lbl] = str(v).lower()
    rid = norm_id(r.get('ref_id', ''))
    tid = norm_id(t.get('target_id', ''))
    return {
        'target_key': tid, 'target_id': tid,
        'target_name': _leaf_name(t),
        'ref_key': rid, 'ref_id': rid, 'ref_name': str(r.get('ref_name', '')),
        'score': score, 'parents': parents,
        'ref_parents': {lbl: ('' if pd.isna(r.get(f'ref_{lbl}', '')) else str(r.get(f'ref_{lbl}', '')))
                        for lbl in labels},
    }


def candidates(config, level, target_key, restrict=True, top=3, pipeline=None) -> List[Dict]:
    """Ranked reference candidates for one unmatched target.

    ``restrict`` limits the pool to reference_only rows in the target's parent
    group; otherwise the whole unmatched pool is scored.
    """
    labels = config.hierarchy_labels
    if level == 'leaf':
        tby, rby, df = _leaf_pools(config, labels)
        rows = df[df['target_id'].map(norm_id) == norm_id(target_key)] if not df.empty else df
        if rows.empty:
            return []
        t = rows.iloc[0]
        name = _leaf_name(t)
        pool = rby.get(_parent_key(t, 'target', labels), []) if restrict \
            else [r for lst in rby.values() for r in lst]
        scored = [_leaf_suggestion(t, r, score_pair(name, r.get('ref_name', '')), labels) for r in pool]
    else:
        i = labels.index(level)
        h = f'h{i}'
        tby, rby, _ = _hier_pools(config, level, pipeline)
        found = None
        for pk, ts in tby.items():
            for t in ts:
                if str(t[h]).lower() == str(target_key).lower():
                    found = (pk, t)
                    break
            if found:
                break
        if not found:
            return []
        pk, t = found
        pool = rby.get(pk, []) if restrict else [r for lst in rby.values() for r in lst]
        scored = [{
            'target_key': str(t[h]).lower(), 'ref_key': str(r[h]), 'ref_name': str(r[h]),
            'score': score_pair(t[h], r[h]),
            'parents': {labels[k]: str(r[f'h{k}']) for k in range(i)},
        } for r in pool]
    scored.sort(key=lambda x: (-x['score'], x['ref_name']))
    return scored[:top] if top else scored


def best_scores(config, level='leaf', restrict=True, pipeline=None) -> Dict[str, float]:
    """target_key -> best candidate score (0 when the pool is empty). Used
    for the histogram / threshold preview."""
    labels = config.hierarchy_labels
    out = {}
    if level == 'leaf':
        tby, rby, _ = _leaf_pools(config, labels)
        all_refs = [r for lst in rby.values() for r in lst]
        for pk, ts in tby.items():
            pool = rby.get(pk, []) if restrict else all_refs
            for t in ts:
                name = _leaf_name(t)
                out[norm_id(t.get('target_id', ''))] = max(
                    (score_pair(name, r.get('ref_name', '')) for r in pool), default=0.0)
        return out
    i = labels.index(level)
    h = f'h{i}'
    tby, rby, _ = _hier_pools(config, level, pipeline)
    all_refs = [r for lst in rby.values() for r in lst]
    for pk, ts in tby.items():
        pool = rby.get(pk, []) if restrict else all_refs
        for t in ts:
            out[str(t[h]).lower()] = max((score_pair(t[h], r[h]) for r in pool), default=0.0)
    return out


def apply(config, level, suggestions, rationale_prefix=AUTO_SUGGEST_PREFIX, force_pass=False) -> Dict:
    """Write suggestions into the level's lookup as match_type='manual'.

    Returns {'applied': n, 'dropped': n_reference_only_rows_removed, 'keys': [...]}.
    """
    from . import links
    if level == 'leaf':
        return links.apply_leaf(config, suggestions, rationale_prefix, force_pass)
    return links.apply_hierarchy(config, level, suggestions, rationale_prefix)
