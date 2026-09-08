"""Single-row link operations on the lookup tables, honouring 1-to-1.

A linked reference place leaves the pool: its ``reference_only`` row is
dropped when linked and restored when unlinked. Every function loads the
level's lookup CSV, mutates it, saves it, and returns a dict with ``ok``
plus whatever the caller needs to undo the operation.

Keys are the same as in :mod:`match_bot.core.suggest`:
    leaf       target_key = target_id, ref_key = ref_id
    hierarchy  target_key = lower(target name), ref_key = reference_name

``mapping_rationale`` encodes who set a link: ``auto: suggest score=NN`` for
bulk-applied suggestions, ``manual: ...`` for a person's choice.
"""

from typing import Dict, List, Optional

import pandas as pd

from .lookup import load_lookup, save_lookup

AUTO_PREFIXES = ('auto:', 'fuzzy suggest')   # second is the legacy CLI rationale
MANUAL_PREFIX = 'manual:'
LINKED_TYPES = {'exact', 'fuzzy_dist', 'fuzzy_score', 'manual'}


def _path(config, level):
    return config.lookups_dir / ('leaf_lookup.csv' if level == 'leaf' else f'{level}_lookup.csv')


def _load(config, level) -> pd.DataFrame:
    df = load_lookup(str(_path(config, level)))
    if df.empty:
        raise FileNotFoundError(f'Lookup for level {level!r} not found; run lookups first')
    for col in df.columns:
        if df[col].dtype != object:
            df[col] = df[col].astype(object)
    return df


def _s(v) -> str:
    return '' if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)


def _norm_id(v) -> str:
    s = _s(v)
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except (ValueError, TypeError):
        pass
    return s


def _metrics(t_name, r_name):
    from .fuzzy import hamming_distance, levenshtein
    t, r = _s(t_name).strip(), _s(r_name).strip().lower()
    if not t or not r:
        return None, None, None
    d = levenshtein(t, r)
    sdx = None
    try:
        import jellyfish as jf
        sdx = hamming_distance(jf.soundex(t), jf.soundex(r))
    except Exception:
        pass
    return d, round(d / max(len(t), 1), 3), sdx


# ---------------------------------------------------------------------------
# Leaf level, DataFrame-internal
# ---------------------------------------------------------------------------

def _leaf_target_idx(df, target_key):
    m = (df['target_id'].map(_norm_id) == _norm_id(target_key)) & (df['match_type'] != 'reference_only')
    return df[m].index[0] if m.any() else None


def _leaf_restore_ref(df, labels, row) -> pd.DataFrame:
    """Append a reference_only row rebuilt from a linked row's ref_* columns."""
    rid = _norm_id(row.get('ref_id'))
    if not rid:
        return df
    exists = (df['match_type'] == 'reference_only') & (df['ref_id'].map(_norm_id) == rid)
    if exists.any():
        return df
    new = {c: '' for c in df.columns}
    for lbl in labels:
        new[f'ref_{lbl}'] = _s(row.get(f'ref_{lbl}', ''))
    new.update({'ref_name': _s(row.get('ref_name', '')), 'ref_id': rid,
                'match_type': 'reference_only', 'unmatched': 'x',
                'levenshtein_distance': None, 'levenshtein_score': None, 'soundex_distance': None})
    return pd.concat([df, pd.DataFrame([new])], ignore_index=True)


def _leaf_unlink_at(df, labels, idx) -> Dict:
    row = df.loc[idx]
    prev = {'ref_id': _norm_id(row.get('ref_id')), 'match_type': _s(row.get('match_type')),
            'rationale': _s(row.get('mapping_rationale'))}
    df = _leaf_restore_ref(df, labels, row)
    for lbl in labels:
        df.at[idx, f'ref_{lbl}'] = ''
    df.at[idx, 'ref_name'] = ''
    df.at[idx, 'ref_id'] = ''
    df.at[idx, 'match_type'] = 'no_candidate'
    df.at[idx, 'mapping_rationale'] = ''
    df.at[idx, 'unmatched'] = 'x'
    for c in ('levenshtein_distance', 'levenshtein_score', 'soundex_distance'):
        if c in df.columns:
            df.at[idx, c] = None
    for c in [c for c in df.columns if c.startswith('cross_')] + (['forced_pass'] if 'forced_pass' in df.columns else []):
        df.at[idx, c] = ''
    return {'df': df, **prev}


def _leaf_link_in(df, labels, target_key, ref_key, rationale) -> Dict:
    idx = _leaf_target_idx(df, target_key)
    if idx is None:
        return {'ok': False, 'error': f'Community {target_key!r} not found', 'df': df}
    rmask = (df['match_type'] == 'reference_only') & (df['ref_id'].map(_norm_id) == _norm_id(ref_key))
    if not rmask.any():
        return {'ok': False, 'error': f'Place {ref_key!r} is not available (already linked or unknown)', 'df': df}
    undo = None
    if _norm_id(df.at[idx, 'ref_id']):
        u = _leaf_unlink_at(df, labels, idx)
        df, undo = u['df'], {k: u[k] for k in ('ref_id', 'match_type', 'rationale')}
        rmask = (df['match_type'] == 'reference_only') & (df['ref_id'].map(_norm_id) == _norm_id(ref_key))
    ridx = df[rmask].index[0]
    r = df.loc[ridx]
    for lbl in labels:
        df.at[idx, f'ref_{lbl}'] = _s(r.get(f'ref_{lbl}', ''))
    df.at[idx, 'ref_name'] = _s(r.get('ref_name', ''))
    df.at[idx, 'ref_id'] = _norm_id(r.get('ref_id'))
    df.at[idx, 'match_type'] = 'manual'
    df.at[idx, 'mapping_rationale'] = rationale
    df.at[idx, 'unmatched'] = ''
    d, sc, sdx = _metrics(df.at[idx, 'target_name_standardized'], df.at[idx, 'ref_name'])
    if 'levenshtein_distance' in df.columns:
        df.at[idx, 'levenshtein_distance'] = d
        df.at[idx, 'levenshtein_score'] = sc
    if 'soundex_distance' in df.columns:
        df.at[idx, 'soundex_distance'] = sdx
    deepest = labels[-1] if labels else None
    if deepest and f'cross_{deepest}' in df.columns:
        t, rr = _s(df.at[idx, f'target_{deepest}']).lower(), _s(df.at[idx, f'ref_{deepest}']).lower()
        df.at[idx, f'cross_{deepest}'] = 'x' if t and rr and t != rr else ''
    df = df.drop(index=ridx).reset_index(drop=True)
    return {'ok': True, 'df': df, 'target_key': _norm_id(target_key), 'ref_key': _norm_id(ref_key),
            'ref_name': _s(r.get('ref_name', '')), 'previous': undo}


# ---------------------------------------------------------------------------
# Hierarchy level, DataFrame-internal
# ---------------------------------------------------------------------------

def _hier_target_idx(df, target_key):
    k = _s(target_key).lower().strip()
    std = df['target_name_standardized'].map(lambda v: _s(v).lower().strip())
    raw = df['target_name_raw'].map(lambda v: _s(v).lower().strip())
    m = ((std == k) | (raw == k)) & (df['match_type'] != 'reference_only')
    return df[m].index[0] if m.any() else None


def _hier_restore_ref(df, ref_name) -> pd.DataFrame:
    if not _s(ref_name):
        return df
    exists = (df['match_type'] == 'reference_only') & \
             (df['reference_name'].map(lambda v: _s(v).lower()) == _s(ref_name).lower())
    if exists.any():
        return df
    new = {c: '' for c in df.columns}
    new.update({'reference_name': ref_name, 'match_type': 'reference_only'})
    return pd.concat([df, pd.DataFrame([new])], ignore_index=True)


def _hier_unlink_at(df, idx) -> Dict:
    row = df.loc[idx]
    prev = {'ref_key': _s(row.get('reference_name')), 'match_type': _s(row.get('match_type')),
            'rationale': _s(row.get('mapping_rationale'))}
    df = _hier_restore_ref(df, prev['ref_key'])
    df.at[idx, 'reference_name'] = ''
    df.at[idx, 'match_type'] = 'no_candidate'
    df.at[idx, 'mapping_rationale'] = ''
    return {'df': df, **prev}


def _hier_link_in(df, target_key, ref_key, rationale) -> Dict:
    idx = _hier_target_idx(df, target_key)
    if idx is None:
        return {'ok': False, 'error': f'Admin name {target_key!r} not found', 'df': df}
    rmask = (df['match_type'] == 'reference_only') & \
            (df['reference_name'].map(lambda v: _s(v).lower()) == _s(ref_key).lower())
    if not rmask.any():
        return {'ok': False, 'error': f'Reference admin {ref_key!r} is not available', 'df': df}
    undo = None
    if _s(df.at[idx, 'reference_name']):
        u = _hier_unlink_at(df, idx)
        df, undo = u['df'], {k: u[k] for k in ('ref_key', 'match_type', 'rationale')}
        rmask = (df['match_type'] == 'reference_only') & \
                (df['reference_name'].map(lambda v: _s(v).lower()) == _s(ref_key).lower())
    ridx = df[rmask].index[0]
    ref_name = _s(df.at[ridx, 'reference_name'])
    df.at[idx, 'reference_name'] = ref_name
    df.at[idx, 'match_type'] = 'manual'
    df.at[idx, 'mapping_rationale'] = rationale
    df = df.drop(index=ridx).reset_index(drop=True)
    return {'ok': True, 'df': df, 'target_key': _s(target_key).lower(), 'ref_key': ref_name,
            'ref_name': ref_name, 'previous': undo}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def link(config, level, target_key, ref_key, rationale='manual: linked') -> Dict:
    df = _load(config, level)
    res = (_leaf_link_in(df, config.hierarchy_labels, target_key, ref_key, rationale)
           if level == 'leaf' else _hier_link_in(df, target_key, ref_key, rationale))
    df = res.pop('df')
    if res['ok']:
        save_lookup(df, str(_path(config, level)))
    return res


def unlink(config, level, target_key) -> Dict:
    df = _load(config, level)
    if level == 'leaf':
        idx = _leaf_target_idx(df, target_key)
        if idx is None or not _norm_id(df.at[idx, 'ref_id']):
            return {'ok': False, 'error': f'{target_key!r} is not linked'}
        u = _leaf_unlink_at(df, config.hierarchy_labels, idx)
    else:
        idx = _hier_target_idx(df, target_key)
        if idx is None or not _s(df.at[idx, 'reference_name']):
            return {'ok': False, 'error': f'{target_key!r} is not linked'}
        u = _hier_unlink_at(df, idx)
    df = u.pop('df')
    save_lookup(df, str(_path(config, level)))
    out = {'ok': True, 'target_key': target_key, **u}
    if level == 'leaf':
        out['ref_key'] = u['ref_id']
    return out


def set_no_equivalent(config, level, target_key) -> Dict:
    df = _load(config, level)
    idx = _leaf_target_idx(df, target_key) if level == 'leaf' else _hier_target_idx(df, target_key)
    if idx is None:
        return {'ok': False, 'error': f'{target_key!r} not found'}
    previous = None
    linked = _norm_id(df.at[idx, 'ref_id']) if level == 'leaf' else _s(df.at[idx, 'reference_name'])
    if linked:
        u = _leaf_unlink_at(df, config.hierarchy_labels, idx) if level == 'leaf' else _hier_unlink_at(df, idx)
        df = u.pop('df')
        previous = u
    df.at[idx, 'match_type'] = 'no_equivalent'
    df.at[idx, 'mapping_rationale'] = 'manual: no equivalent'
    if 'unmatched' in df.columns:
        df.at[idx, 'unmatched'] = 'x'
    save_lookup(df, str(_path(config, level)))
    return {'ok': True, 'target_key': target_key, 'previous': previous}


def clear_no_equivalent(config, level, target_key) -> Dict:
    df = _load(config, level)
    idx = _leaf_target_idx(df, target_key) if level == 'leaf' else _hier_target_idx(df, target_key)
    if idx is None or df.at[idx, 'match_type'] != 'no_equivalent':
        return {'ok': False, 'error': f'{target_key!r} is not marked no-equivalent'}
    df.at[idx, 'match_type'] = 'no_candidate'
    df.at[idx, 'mapping_rationale'] = ''
    save_lookup(df, str(_path(config, level)))
    return {'ok': True, 'target_key': target_key}


def is_auto(rationale) -> bool:
    r = _s(rationale).strip().lower()
    return any(r.startswith(p) for p in AUTO_PREFIXES)


def revert_auto(config, level='leaf') -> List[str]:
    """Unlink every row that was linked by a bulk suggestion pass. Returns
    the target keys reverted."""
    df = _load(config, level)
    labels = config.hierarchy_labels
    mask = (df['match_type'] == 'manual') & df['mapping_rationale'].map(is_auto)
    keys = []
    for idx in list(df[mask].index):
        if level == 'leaf':
            keys.append(_norm_id(df.at[idx, 'target_id']))
            df = _leaf_unlink_at(df, labels, idx)['df']
        else:
            keys.append(_s(df.at[idx, 'target_name_standardized']).lower())
            df = _hier_unlink_at(df, idx)['df']
    if keys:
        save_lookup(df, str(_path(config, level)))
    return keys


def apply_leaf(config, suggestions, rationale_prefix, force_pass=False) -> Dict:
    df = _load(config, 'leaf')
    labels = config.hierarchy_labels
    before = int((df['match_type'] == 'reference_only').sum())
    applied, keys = 0, []
    for s in suggestions:
        res = _leaf_link_in(df, labels, s['target_key'], s['ref_key'],
                            f"{rationale_prefix} score={s['score']:.0f}")
        df = res.pop('df')
        if res['ok']:
            applied += 1
            keys.append(res['target_key'])
            if force_pass and 'forced_pass' in df.columns:
                df.at[_leaf_target_idx(df, s['target_key']), 'forced_pass'] = 'x'
    if applied:
        save_lookup(df, str(_path(config, 'leaf')))
    after = int((df['match_type'] == 'reference_only').sum())
    return {'applied': applied, 'dropped': before - after, 'keys': keys}


def apply_hierarchy(config, level, suggestions, rationale_prefix) -> Dict:
    df = _load(config, level)
    before = int((df['match_type'] == 'reference_only').sum())
    applied, keys = 0, []
    for s in suggestions:
        res = _hier_link_in(df, s['target_key'], s['ref_key'], f"{rationale_prefix} score={s['score']:.0f}")
        df = res.pop('df')
        if res['ok']:
            applied += 1
            keys.append(res['target_key'])
    if applied:
        save_lookup(df, str(_path(config, level)))
    after = int((df['match_type'] == 'reference_only').sum())
    return {'applied': applied, 'dropped': before - after, 'keys': keys}
