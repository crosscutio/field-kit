"""Per-level match state for the Geocoding tab's level ladder.

Every hierarchy level is the same operation as geocoding: fuzzy match,
review, link the leftovers by hand, descend. Geocoding is that loop at the
leaf level. This module derives a live per-level view of that loop from the
files the pipeline already writes:

- hierarchy links live in ``output/lookups/<label>_lookup.csv`` (the
  crosswalk the pipeline applies on the next run);
- leaf links live in ``output/matched.csv`` / ``unmatched_ref.csv`` plus
  ``manual_geocode.csv`` for map picks.

Nothing here gates work by level progress: any level can be inspected and
linked at any time. Rows whose parent value is still unlinked are merely
*blocked* — they can't be scored or linked until the parent is, which caps
how complete the child level can get.
"""

import re
from pathlib import Path

import pandas as pd

from match_bot.core.standardization import normalize_text, remove_accents

HIER_LOOKUP_COLUMNS = [
    'target_column', 'target_name_raw', 'target_name_standardized',
    'reference_name', 'match_type', 'mapping_rationale',
]

# Pipeline match types folded into the three the drawer shows.
METHOD_DISPLAY = {
    'exact': 'exact',
    'fuzzy_dist': 'fuzzy',
    'fuzzy_score': 'fuzzy',
    'fuzzy_suggest': 'fuzzy',
    'fuzzy': 'fuzzy',
    'manual': 'manual',
}

CANDIDATE_CAP = 12

_NUM_TOKEN = re.compile(r'\b\d+\b')


def _read_csv_str(path):
    if not Path(path).exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str).fillna('')
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _std(value, case, accents):
    """Standardize one value the way the pipeline standardizes columns."""
    if value is None:
        return ''
    s = str(value)
    if accents:
        s = remove_accents(s)
    s = normalize_text(s, case=case or 'lower')
    return '' if s is None or (isinstance(s, float) and pd.isna(s)) else str(s)


def _score(a, b):
    """Fuzzy score in [0, 100] for two names.

    Standalone digit tokens are stripped before scoring: shared trailing
    numbers ("Ward 2" vs "Zone 2") otherwise manufacture high scores between
    unrelated names.
    """
    from rapidfuzz import fuzz

    a = _NUM_TOKEN.sub(' ', str(a).lower()).strip()
    b = _NUM_TOKEN.sub(' ', str(b).lower()).strip()
    if not a or not b:
        return 0
    return int(round(max(fuzz.ratio(a, b), fuzz.token_set_ratio(a, b))))


def _score_from_rationale(rationale):
    m = re.search(r'score\s*=\s*(\d+)', str(rationale or ''))
    return int(m.group(1)) if m else None


def level_specs(form_data):
    """Ordered level list: paired hierarchy levels, then the leaf.

    Levels are paired by position: ref_hierarchy[i] and target_hierarchy[i]
    describe the same level. A level missing its target column can't be
    scored, only displayed.
    """
    ref_h = form_data.get('ref_hierarchy', []) or []
    tgt_h = form_data.get('target_hierarchy', []) or []
    specs = []
    for i, h in enumerate(ref_h):
        tgt = tgt_h[i] if i < len(tgt_h) else {}
        specs.append({
            'label': h.get('label', f'level{i}'),
            'kind': 'hier',
            'ref_col': h.get('column', ''),
            'target_col': tgt.get('column', ''),
        })
    specs.append({
        'label': 'leaf',
        'kind': 'leaf',
        'ref_col': form_data.get('ref_name_column', ''),
        'target_col': form_data.get('target_name_column', ''),
    })
    return specs


def _lookup_path(session_dir, label):
    return session_dir / 'output' / 'lookups' / f'{label}_lookup.csv'


def _read_lookup(session_dir, label):
    df = _read_csv_str(_lookup_path(session_dir, label))
    if df.empty:
        return pd.DataFrame(columns=HIER_LOOKUP_COLUMNS)
    for col in HIER_LOOKUP_COLUMNS:
        if col not in df.columns:
            df[col] = ''
    return df


def _write_lookup(session_dir, label, df):
    path = _lookup_path(session_dir, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _lookup_links(session_dir, label):
    """Mapping of the level's crosswalk: lower(target_std) -> entry dict."""
    links = {}
    for _, row in _read_lookup(session_dir, label).iterrows():
        tgt = str(row['target_name_standardized']).strip()
        ref = str(row['reference_name']).strip()
        if tgt and ref:
            links[tgt.lower()] = {
                'target': str(row['target_name_raw']).strip() or tgt,
                'target_std': tgt,
                'ref': ref,
                'method': METHOD_DISPLAY.get(str(row['match_type']), 'manual'),
                'score': _score_from_rationale(row['mapping_rationale']),
            }
    return links


class LevelState:
    """Computes per-level rows from the session's uploaded files + outputs."""

    def __init__(self, session_dir, form_data):
        self.sd = Path(session_dir)
        self.form = form_data
        self.case = form_data.get('case', 'lower')
        self.accents = bool(form_data.get('remove_accents', True))
        self.specs = level_specs(form_data)
        self.hier_specs = [s for s in self.specs if s['kind'] == 'hier']
        self._load_frames()
        self._links = {
            s['label']: _lookup_links(self.sd, s['label'])
            for s in self.hier_specs
        }

    # ---- data loading -------------------------------------------------

    def _load_frames(self):
        ref = _read_csv_str(self.sd / 'ref.csv')
        tgt = _read_csv_str(self.sd / 'target.csv')
        self.ref_raw, self.tgt_raw = ref, tgt

        def std_col(df, col):
            if df.empty or not col or col not in df.columns:
                return pd.Series([], dtype=str)
            return df[col].map(lambda v: _std(v, self.case, self.accents))

        # std hierarchy columns keyed by level index
        self.ref_h = [std_col(ref, s['ref_col']) for s in self.hier_specs]
        self.tgt_h = [std_col(tgt, s['target_col']) for s in self.hier_specs]

    def _tgt_effective(self, i):
        """Target values at level i in ref space (crosswalk applied)."""
        label = self.hier_specs[i]['label']
        links = self._links.get(label, {})
        col = self.tgt_h[i]
        if col.empty or not links:
            return col
        return col.map(lambda v: _std(links[v.lower()]['ref'], self.case,
                                      self.accents) if v.lower() in links else v)

    # ---- hierarchy levels ---------------------------------------------

    def hier_rows(self, i):
        """One row per distinct reference value at hierarchy level i."""
        spec = self.hier_specs[i]
        ref_col = self.ref_h[i]
        if ref_col.empty:
            return []

        # distinct ref values with their parent chains (std space)
        parents = [self.ref_h[j] for j in range(i)]
        chains = {}
        raw_display = {}
        for idx, val in ref_col.items():
            if not val:
                continue
            chain = tuple(p.iloc[idx] if idx < len(p) else '' for p in parents)
            chains.setdefault((chain, val), 0)
            chains[(chain, val)] += 1
            raw_display.setdefault((chain, val), str(
                self.ref_raw[spec['ref_col']].iloc[idx]))

        # target values (raw std + effective) with effective parent chains
        tgt_col = self.tgt_h[i]
        tgt_eff = self._tgt_effective(i)
        tgt_parents = [self._tgt_effective(j) for j in range(i)]
        tgt_by_chain = {}
        for idx, val in tgt_col.items():
            if not val:
                continue
            chain = tuple(p.iloc[idx] if idx < len(p) else '' for p in tgt_parents)
            entry = tgt_by_chain.setdefault(chain, {'std': set(), 'eff': set(),
                                                    'display': {}, 'count': {}})
            entry['std'].add(val)
            entry['eff'].add(tgt_eff.iloc[idx])
            entry['display'].setdefault(val, str(
                self.tgt_raw[spec['target_col']].iloc[idx]))
            entry['count'][val] = entry['count'].get(val, 0) + 1

        links = self._links.get(spec['label'], {})
        resolved_parent = self._resolved_sets(i)

        rows = []
        for (chain, val), n_children in sorted(chains.items()):
            display = raw_display[(chain, val)]
            blocked = any(
                chain[j] and chain[j] not in resolved_parent[j]
                for j in range(i)
            )
            pool = tgt_by_chain.get(chain, {'std': set(), 'eff': set(),
                                            'display': {}, 'count': {}})
            row = {
                'key': val,
                'name': display,
                'level': spec['label'],
                'children': n_children,
                'blocked': blocked,
                'target': None, 'method': None, 'score': None,
                'guess': None, 'guess_score': None,
            }
            link = next((l for l in links.values()
                         if _std(l['ref'], self.case, self.accents) == val), None)
            if link is not None:
                row['target'] = link['target']
                row['method'] = link['method']
                row['score'] = link['score']
            elif val in pool['std']:
                # same name on both sides, no crosswalk needed
                row['target'] = pool['display'].get(val, display)
                row['method'] = 'exact'
                row['score'] = 100
            elif not blocked:
                guess, score = self._best_candidate(val, pool, links)
                row['guess'], row['guess_score'] = guess, score
            rows.append(row)
        return rows

    def _resolved_sets(self, upto):
        """For each level < upto: the set of resolved (linked) ref std values."""
        sets = []
        for j in range(upto):
            resolved = set()
            for r in self.hier_rows(j):
                if r['method']:
                    resolved.add(r['key'])
            sets.append(resolved)
        return sets

    def _best_candidate(self, val, pool, links):
        linked_targets = {l['target_std'].lower() for l in links.values()}
        best, best_score = None, None
        for cand in pool['std']:
            if cand.lower() in linked_targets:
                continue
            s = _score(val, cand)
            if best_score is None or s > best_score:
                best, best_score = pool['display'].get(cand, cand), s
        return best, best_score

    def hier_candidates(self, i, ref_key):
        """Scored candidate list for the name-to-name picker."""
        spec = self.hier_specs[i]
        chain = None
        # rebuild the parent chain for this ref value
        parents = [self.ref_h[j] for j in range(i)]
        for idx, val in self.ref_h[i].items():
            if val == ref_key:
                chain = tuple(p.iloc[idx] if idx < len(p) else '' for p in parents)
                break
        if chain is None:
            return []

        tgt_col = self.tgt_h[i]
        tgt_parents = [self._tgt_effective(j) for j in range(i)]
        links = self._links.get(spec['label'], {})
        linked_targets = {l['target_std'].lower() for l in links.values()}

        seen = {}
        for idx, val in tgt_col.items():
            if not val:
                continue
            t_chain = tuple(p.iloc[idx] if idx < len(p) else '' for p in tgt_parents)
            if t_chain != chain:
                continue
            if val not in seen:
                display = str(self.tgt_raw[spec['target_col']].iloc[idx])
                source = ''
                if 'source' in self.tgt_raw.columns:
                    source = str(self.tgt_raw['source'].iloc[idx])
                seen[val] = {'display': display, 'count': 0, 'source': source}
            seen[val]['count'] += 1

        cands = []
        for val, info in seen.items():
            meta_bits = []
            if info['source']:
                meta_bits.append({'geonames': 'GeoNames', 'osm': 'OSM',
                                  'user': 'your CSV'}.get(info['source'],
                                                          info['source']))
            meta_bits.append(spec['target_col'])
            meta_bits.append(f"{info['count']} place"
                             + ('s' if info['count'] != 1 else ''))
            cands.append({
                'name': info['display'],
                'std': val,
                'meta': ' · '.join(meta_bits),
                'score': _score(ref_key, val),
                'linked': val.lower() in linked_targets,
            })
        cands.sort(key=lambda c: -c['score'])
        return cands[:CANDIDATE_CAP]

    # ---- hierarchy link operations ------------------------------------

    def link_hier(self, label, ref_value, target_std, target_raw='',
                  method='manual', score=None):
        """Upsert one crosswalk entry. Relinking drops that value's leaf links."""
        ref_std = _std(ref_value, self.case, self.accents)
        df = _read_lookup(self.sd, label)

        dropped = 0
        existing = df[df['reference_name'].map(
            lambda v: _std(v, self.case, self.accents)) == ref_std]
        if not existing.empty and not (
                existing['target_name_standardized'].str.lower() ==
                target_std.lower()).any():
            # the parent is being re-pointed: children linked under the old
            # meaning are no longer trustworthy — drop them (user decision).
            dropped = self.drop_children(label, ref_std)
        df = df[df['reference_name'].map(
            lambda v: _std(v, self.case, self.accents)) != ref_std]
        df = df[df['target_name_standardized'].str.lower() != target_std.lower()]

        rationale = f'fuzzy suggest score={score}' if (
            method == 'fuzzy' and score is not None) else ''
        df = pd.concat([df, pd.DataFrame([{
            'target_column': f'_target_{label}',
            'target_name_raw': target_raw or target_std,
            'target_name_standardized': target_std,
            'reference_name': ref_std,
            'match_type': method,
            'mapping_rationale': rationale,
        }])], ignore_index=True)
        _write_lookup(self.sd, label, df)
        self._links[label] = _lookup_links(self.sd, label)
        return dropped

    def unlink_hier(self, label, ref_value):
        """Remove a crosswalk entry and drop leaf links made under it."""
        ref_std = _std(ref_value, self.case, self.accents)
        df = _read_lookup(self.sd, label)
        df = df[df['reference_name'].map(
            lambda v: _std(v, self.case, self.accents)) != ref_std]
        _write_lookup(self.sd, label, df)
        self._links[label] = _lookup_links(self.sd, label)
        return self.drop_children(label, ref_std)

    def drop_children(self, label, ref_std):
        """Return matched leaf rows under this parent value to the pools.

        Only leaf links are dropped; with 3+ hierarchy levels, intermediate
        lookup entries are not scoped by parent and are left in place.
        Manual geocodes for the dropped refs are removed too — the parent
        they were placed under no longer means the same place.
        """
        out = self.sd / 'output'
        matched = _read_csv_str(out / 'matched.csv')
        col = f'_ref_{label}'
        if matched.empty or col not in matched.columns:
            return 0
        mask = matched[col].map(
            lambda v: _std(v, self.case, self.accents)) == ref_std
        if not mask.any():
            return 0

        un_ref = _read_csv_str(out / 'unmatched_ref.csv')
        un_tgt = _read_csv_str(out / 'unmatched_target.csv')
        dropped_refs = set()
        for _, row in matched[mask].iterrows():
            dropped_refs.add(str(row.get('_ref_id', '')))
            if not un_ref.empty or list(un_ref.columns):
                un_ref = pd.concat([un_ref, pd.DataFrame(
                    [{c: row.get(c, '') for c in un_ref.columns}])],
                    ignore_index=True)
            if str(row.get('_target_id', '')) and (
                    not un_tgt.empty or list(un_tgt.columns)):
                un_tgt = pd.concat([un_tgt, pd.DataFrame(
                    [{c: row.get(c, '') for c in un_tgt.columns}])],
                    ignore_index=True)
        matched = matched[~mask]
        matched.to_csv(out / 'matched.csv', index=False)
        if list(un_ref.columns):
            un_ref.to_csv(out / 'unmatched_ref.csv', index=False)
        if list(un_tgt.columns):
            un_tgt.to_csv(out / 'unmatched_target.csv', index=False)

        manual_path = out / 'manual_geocode.csv'
        manual = _read_csv_str(manual_path)
        if not manual.empty and 'ref_id' in manual.columns:
            manual = manual[~manual['ref_id'].astype(str).isin(dropped_refs)]
            manual.to_csv(manual_path, index=False)
        return int(mask.sum())

    # ---- leaf level ----------------------------------------------------

    def leaf_ran(self):
        return (self.sd / 'output' / 'matched.csv').exists()

    def leaf_rows(self):
        out = self.sd / 'output'
        matched = _read_csv_str(out / 'matched.csv')
        un_ref = _read_csv_str(out / 'unmatched_ref.csv')
        labels = [s['label'] for s in self.hier_specs]
        resolved = self._resolved_sets(len(self.hier_specs))

        parent_lbl = labels[-1] if labels else None

        rows = []
        for _, m in matched.iterrows():
            name = str(m.get('_ref_name_raw', '') or m.get('_ref_name', ''))
            target = str(m.get('_target_name_raw', '') or m.get('_target_name', ''))
            method = METHOD_DISPLAY.get(str(m.get('_match_type', '')), 'fuzzy')
            score = _score_from_rationale(m.get('_mapping_rationale', ''))
            if score is None and method == 'fuzzy':
                score = _score(name, target)
            rows.append({
                'ref_id': str(m.get('_ref_id', '')),
                'name': name, 'level': 'leaf', 'target': target,
                'method': method, 'score': score,
                'blocked': False, 'guess': None, 'guess_score': None,
                'parent': str(m.get(f'_ref_{parent_lbl}', '') or '') if parent_lbl else '',
            })

        if not un_ref.empty and '_ref_id' in un_ref.columns:
            unmatched_pool = self._leaf_target_pool()
            for _, r in un_ref.iterrows():
                chain = tuple(
                    _std(r.get(f'_ref_{lbl}', ''), self.case, self.accents)
                    for lbl in labels)
                j_block = next(
                    (j for j in range(len(labels))
                     if chain[j] and chain[j] not in resolved[j]), None)
                blocked = j_block is not None
                name = str(r.get('_ref_name_raw', '') or r.get('_ref_name', ''))
                row = {
                    'ref_id': str(r.get('_ref_id', '')),
                    'name': name, 'level': 'leaf', 'target': None,
                    'method': None, 'score': None,
                    'blocked': blocked, 'guess': None, 'guess_score': None,
                    'blocked_on': labels[j_block] if blocked else None,
                    'blocked_parent': str(r.get(f'_ref_{labels[j_block]}', '') or '') if blocked else None,
                    'parent': str(r.get(f'_ref_{parent_lbl}', '') or '') if parent_lbl else '',
                }
                if not blocked:
                    best, best_score, _tid, _lat, _lon = self._best_leaf_candidate(
                        name, chain, unmatched_pool)
                    row['guess'], row['guess_score'] = best, best_score
                rows.append(row)
        return rows

    def _leaf_target_pool(self):
        """Unmatched targets with effective parent chains and coordinates."""
        un_tgt = _read_csv_str(self.sd / 'output' / 'unmatched_target.csv')
        labels = [s['label'] for s in self.hier_specs]
        pool = []
        if un_tgt.empty or '_target_id' not in un_tgt.columns:
            return pool
        for _, t in un_tgt.iterrows():
            chain = []
            for j, lbl in enumerate(labels):
                v = _std(t.get(f'_target_{lbl}', ''), self.case, self.accents)
                links = self._links.get(lbl, {})
                if v.lower() in links:
                    v = _std(links[v.lower()]['ref'], self.case, self.accents)
                chain.append(v)
            pool.append({
                'target_id': str(t.get('_target_id', '')),
                'name': str(t.get('_target_name_raw', '')
                            or t.get('_target_name', '')),
                'chain': tuple(chain),
            })
        return pool

    def _best_leaf_candidate(self, name, chain, pool):
        best = (None, None, None, None, None)
        for cand in pool:
            if cand['chain'] != chain:
                continue
            s = _score(name, cand['name'])
            if best[1] is None or s > best[1]:
                best = (cand['name'], s, cand['target_id'], None, None)
        return best

    def leaf_candidates(self, ref_id):
        """Scored candidates for one unmatched reference record."""
        un_ref = _read_csv_str(self.sd / 'output' / 'unmatched_ref.csv')
        if un_ref.empty or '_ref_id' not in un_ref.columns:
            return []
        hit = un_ref[un_ref['_ref_id'].astype(str) == str(ref_id)]
        if hit.empty:
            return []
        r = hit.iloc[0]
        labels = [s['label'] for s in self.hier_specs]
        chain = tuple(
            _std(r.get(f'_ref_{lbl}', ''), self.case, self.accents)
            for lbl in labels)
        name = str(r.get('_ref_name_raw', '') or r.get('_ref_name', ''))

        coords, _ = self._target_coords()
        cands = []
        for cand in self._leaf_target_pool():
            if cand['chain'] != chain:
                continue
            lat, lon = coords.get(cand['target_id'], ('', ''))
            meta_bits = [b for b in cand['chain'] if b]
            # Coordinates distinguish same-name points (near-duplicate
            # gazetteer entries are common); the picker shows meta verbatim.
            if lat and lon:
                try:
                    meta_bits.append(f'{float(lat):.4f}, {float(lon):.4f}')
                except (TypeError, ValueError):
                    pass
            cands.append({
                'name': cand['name'],
                'target_id': cand['target_id'],
                'meta': ' · '.join(meta_bits) if meta_bits else 'no hierarchy',
                'score': _score(name, cand['name']),
                'latitude': lat, 'longitude': lon,
            })
        cands.sort(key=lambda c: -c['score'])
        return cands[:CANDIDATE_CAP]

    def _target_coords(self):
        tgt = self.tgt_raw
        id_col = self.form.get('target_id_column', '')
        lat_col = self.form.get('geo_lat_column', '')
        lon_col = self.form.get('geo_lon_column', '')
        coords = {}
        if tgt.empty or not all(c in tgt.columns for c in (id_col, lat_col, lon_col)):
            return coords, 0
        for _, row in tgt.iterrows():
            coords[str(row[id_col])] = (str(row[lat_col]), str(row[lon_col]))
        return coords, 0

    # ---- assembled state ----------------------------------------------

    def state(self):
        levels = []
        for i, spec in enumerate(self.hier_specs):
            rows = self.hier_rows(i)
            open_rows = [r for r in rows if not r['method'] and not r['blocked']]
            blocked_rows = [r for r in rows if r['blocked']]
            levels.append({
                **spec,
                'rows': rows,
                'total': len(rows),
                'open': len(open_rows),
                'blocked': len(blocked_rows),
                'ran': True,
            })
        leaf_rows = self.leaf_rows() if self.leaf_ran() else []
        open_rows = [r for r in leaf_rows if not r['method'] and not r['blocked']]
        blocked_rows = [r for r in leaf_rows if r['blocked']]
        levels.append({
            **self.specs[-1],
            'rows': leaf_rows,
            'total': len(leaf_rows),
            'open': len(open_rows),
            'blocked': len(blocked_rows),
            'ran': self.leaf_ran(),
        })
        return {'levels': levels}

    # ---- rerun ---------------------------------------------------------

    def rerun_hier(self, label, threshold):
        """Auto-link unresolved rows scoring at/above threshold. Non-destructive:
        rows already linked (by hand or before) are never touched."""
        i = next((j for j, s in enumerate(self.hier_specs)
                  if s['label'] == label), None)
        if i is None:
            return {'linked': 0, 'open': 0}
        linked = 0
        for row in self.hier_rows(i):
            if row['method'] or row['blocked']:
                continue
            if row['guess'] is not None and (row['guess_score'] or 0) >= threshold:
                self.link_hier(label, row['key'],
                               _std(row['guess'], self.case, self.accents),
                               target_raw=row['guess'],
                               method='fuzzy', score=row['guess_score'])
                linked += 1
        remaining = [r for r in self.hier_rows(i)
                     if not r['method'] and not r['blocked']]
        return {'linked': linked, 'open': len(remaining)}

    def rerun_leaf(self, threshold):
        """Auto-link unmatched leaf refs whose best candidate clears threshold."""
        out = self.sd / 'output'
        matched = _read_csv_str(out / 'matched.csv')
        un_ref = _read_csv_str(out / 'unmatched_ref.csv')
        un_tgt = _read_csv_str(out / 'unmatched_target.csv')
        if matched.empty and un_ref.empty:
            return {'linked': 0, 'open': 0}

        linked = 0
        for row in self.leaf_rows():
            if row['method'] or row['blocked']:
                continue
            cands = self.leaf_candidates(row['ref_id'])
            if not cands or cands[0]['score'] < threshold:
                continue
            cand = cands[0]
            ref_mask = un_ref['_ref_id'].astype(str) == row['ref_id']
            tgt_mask = un_tgt['_target_id'].astype(str) == cand['target_id']
            if not ref_mask.any() or not tgt_mask.any():
                continue
            r_data, t_data = un_ref[ref_mask].iloc[0], un_tgt[tgt_mask].iloc[0]
            cols = list(matched.columns) or [
                c for c in list(r_data.index) + list(t_data.index)
                if str(c).startswith('_')] + ['_match_type', '_mapping_rationale']
            new_row = {c: '' for c in cols}
            for c in cols:
                if c in r_data.index and str(c).startswith('_ref'):
                    new_row[c] = r_data[c]
                if c in t_data.index and str(c).startswith('_target'):
                    new_row[c] = t_data[c]
            new_row['_match_type'] = 'fuzzy_suggest'
            new_row['_mapping_rationale'] = f"fuzzy suggest score={cand['score']}"
            matched = pd.concat([matched, pd.DataFrame([new_row])],
                                ignore_index=True)
            un_ref = un_ref[~ref_mask]
            un_tgt = un_tgt[~tgt_mask]
            linked += 1

        if linked:
            matched.to_csv(out / 'matched.csv', index=False)
            un_ref.to_csv(out / 'unmatched_ref.csv', index=False)
            un_tgt.to_csv(out / 'unmatched_target.csv', index=False)
        remaining = [r for r in self.leaf_rows()
                     if not r['method'] and not r['blocked']]
        return {'linked': linked, 'open': len(remaining)}
