"""One geocoding project = one session directory.

Files:
    target.csv                 the community list (needs coordinates)
    ref.csv                    named places (have lat/lon)
    boundaries_<label>.geojson optional user-uploaded admin polygons
    form_state.json            column mapping, rules, ui snapshot
    output/lookups/*.csv       the engine's lookup tables
    output/manual_geocode.csv  dropped pins  (target_id, latitude, longitude)
    output/history.jsonl       action journal
    output/geocoded.csv        export
"""

import contextlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from match_bot.core import export, links, suggest as sg
from match_bot.core.config import MatcherConfig
from match_bot.core.lookup import load_lookup
from match_bot.core.standardization import normalize_text, remove_accents
from match_bot.gui.history import History

ROW_ID = '_row_id'
PIN_COLUMNS = ['target_id', 'latitude', 'longitude']
HIST_BINS = [(50 + 5 * i, 55 + 5 * i) for i in range(10)]   # 50..100 by 5
DEFAULT_FORM = {
    'project_name': 'Untitled project',
    'target_filename': '', 'target_name_column': '', 'target_hierarchy': [],
    'ref_filename': '', 'ref_source': None, 'ref_name_column': '', 'ref_id_column': ROW_ID,
    'ref_lat_column': '', 'ref_lon_column': '', 'ref_hierarchy': [],
    'boundaries_source': 'auto', 'country': '', 'boundary_files': {},
    'threshold': 85, 'restrict': True,
    'ran': False, 'last_run': '',
    'ui': {},
}


def _read_csv_str(path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p, dtype=str).fillna('')
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def fix_mojibake(value) -> str:
    """Repair UTF-8 text that was decoded as Latin-1 ("BouakÃ©" -> "Bouaké"),
    which some geoBoundaries files carry."""
    s = '' if value is None else str(value)
    if 'Ã' in s or 'Â' in s:
        try:
            return s.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def std(value) -> str:
    """Standardize the way the pipeline does (lower, no accents)."""
    if value is None:
        return ''
    s = remove_accents(fix_mojibake(value))
    s = normalize_text(s, case='lower')
    return '' if s is None or (isinstance(s, float) and pd.isna(s)) else str(s)


class Project:
    def __init__(self, session_dir):
        self.sd = Path(session_dir)
        self.sd.mkdir(parents=True, exist_ok=True)
        (self.sd / 'output' / 'lookups').mkdir(parents=True, exist_ok=True)
        self.history = History(self.sd / 'output' / 'history.jsonl')

    # ---- paths -----------------------------------------------------------
    @property
    def target_path(self): return self.sd / 'target.csv'
    @property
    def ref_path(self): return self.sd / 'ref.csv'
    @property
    def lookups_dir(self): return self.sd / 'output' / 'lookups'
    @property
    def pins_path(self): return self.sd / 'output' / 'manual_geocode.csv'

    def boundary_path(self, label):
        return self.sd / f'boundaries_{label}.geojson'

    # ---- form state ------------------------------------------------------
    def form(self) -> Dict:
        p = self.sd / 'form_state.json'
        data = dict(DEFAULT_FORM)
        if p.exists():
            try:
                data.update(json.loads(p.read_text(encoding='utf-8')))
            except (json.JSONDecodeError, OSError):
                pass
        return data

    def update_form(self, **keys) -> Dict:
        data = self.form()
        data.update(keys)
        (self.sd / 'form_state.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data

    # ---- uploads ---------------------------------------------------------
    def save_table(self, role: str, stream, filename: str) -> Dict:
        """Store an uploaded CSV as target.csv / ref.csv with a row-id column."""
        df = pd.read_csv(stream, dtype=str).fillna('')
        df.columns = [str(c) for c in df.columns]
        if ROW_ID not in df.columns:
            df.insert(0, ROW_ID, [str(i + 1) for i in range(len(df))])
        path = self.target_path if role == 'target' else self.ref_path
        df.to_csv(path, index=False)
        self.clear_outputs()
        key = 'target_filename' if role == 'target' else 'ref_filename'
        upd = {key: filename}
        if role == 'ref':
            upd['ref_source'] = 'upload'
            upd['ref_id_column'] = ROW_ID
        self.update_form(**upd)
        return self.table_info(role)

    def table_info(self, role: str) -> Optional[Dict]:
        path = self.target_path if role == 'target' else self.ref_path
        if not path.exists():
            return None
        df = _read_csv_str(path)
        cols = [c for c in df.columns if c != ROW_ID]
        return {'columns': cols, 'rows': int(len(df)),
                'preview': df[cols].head(6).to_dict(orient='records')}

    def clear_outputs(self):
        out = self.sd / 'output'
        for p in out.rglob('*'):
            if p.is_file():
                p.unlink()
        self.lookups_dir.mkdir(parents=True, exist_ok=True)
        self.update_form(ran=False, last_run='')

    # ---- config ----------------------------------------------------------
    def levels(self) -> List[str]:
        return [h['label'] for h in self.form().get('target_hierarchy', []) if h.get('label')] + ['leaf']

    def pipeline(self):
        """Loaded + mapped MatchingPipeline, built once per Project instance."""
        if getattr(self, '_pipeline', None) is None:
            self._pipeline = sg._hier_pipeline(self.config())
        return self._pipeline

    def config(self) -> MatcherConfig:
        f = self.form()
        t_h = [{'column': h['column'], 'label': h['label']} for h in f.get('target_hierarchy', []) if h.get('column')]
        r_h = [{'column': h['column'], 'label': h['label']} for h in f.get('ref_hierarchy', []) if h.get('column')]
        raw = {
            'project_name': f.get('project_name') or 'Untitled project',
            'reference': {'file': 'ref.csv', 'columns': {
                'id': f.get('ref_id_column') or ROW_ID, 'name': f.get('ref_name_column', ''),
                'latitude': f.get('ref_lat_column', ''), 'longitude': f.get('ref_lon_column', ''),
                'hierarchy': r_h}},
            'target': {'file': 'target.csv', 'columns': {
                'id': ROW_ID, 'name': f.get('target_name_column', ''), 'hierarchy': t_h}},
            'standardization': {'case': 'lower', 'remove_accents': True},
            'matching': {'levenshtein_distance_threshold': 1, 'levenshtein_score_threshold': 0.25,
                         'validate_numbers': True},
            'paths': {'lookups_dir': 'output/lookups', 'output_dir': 'output'},
        }
        cfg = MatcherConfig._from_dict(raw)
        cfg._config_dir = self.sd
        cfg.validate()
        return cfg

    def ready(self) -> Dict:
        f = self.form()
        return {
            'target': self.target_path.exists() and bool(f.get('target_name_column')),
            'ref': self.ref_path.exists() and bool(f.get('ref_name_column')) and bool(f.get('ref_lat_column')),
            'hierarchy_paired': len([h for h in f.get('target_hierarchy', []) if h.get('column')]) ==
                                len([h for h in f.get('ref_hierarchy', []) if h.get('column')]),
        }

    # ---- pipeline --------------------------------------------------------
    def run_lookups(self) -> str:
        from match_bot.scripts.generate_lookups import run
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run(self.config())
        return buf.getvalue()

    def has_lookups(self) -> bool:
        return (self.lookups_dir / 'leaf_lookup.csv').exists()

    # ---- stats -----------------------------------------------------------
    def level_stats(self) -> Dict[str, Dict]:
        out = {}
        for label in self.levels():
            path = self.lookups_dir / ('leaf_lookup.csv' if label == 'leaf' else f'{label}_lookup.csv')
            df = _read_csv_str(path)
            if df.empty:
                out[label] = {'total': 0, 'pending': 0, 'linked': 0, 'no_equivalent': 0, 'reference_only': 0}
                continue
            c = Counter(df['match_type'])
            pending = c.get('no_candidate', 0)
            ref_only = c.get('reference_only', 0)
            noeq = c.get('no_equivalent', 0)
            total = len(df) - ref_only
            out[label] = {'total': total, 'pending': pending, 'no_equivalent': noeq,
                          'linked': total - pending - noeq, 'reference_only': ref_only,
                          'manual': sum(1 for _, r in df.iterrows()
                                        if r['match_type'] == 'manual' and not links.is_auto(r['mapping_rationale']))}
        return out

    def state(self) -> Dict:
        f = self.form()
        stats = self.level_stats() if self.has_lookups() else {}
        leaf = stats.get('leaf', {})
        pins = self.pins()
        total = int(len(_read_csv_str(self.target_path))) if self.target_path.exists() else 0
        pinned = int(len(pins))
        return {
            'form': f, 'ready': self.ready(), 'levels': self.levels(), 'stats': stats,
            'has_lookups': self.has_lookups(), 'total': total,
            'geocoded': int(leaf.get('linked', 0)) + pinned, 'pinned': pinned,
            'manual': int(leaf.get('manual', 0)) + pinned,
            'target': self.table_info('target'), 'ref': self.table_info('ref'),
            'boundaries': {lbl: self.boundary_path(lbl).exists() for lbl in self.levels() if lbl != 'leaf'},
            'recent': self.history.recent(2), 'can_undo': self.history.undoable() is not None,
        }

    # ---- admin stage -----------------------------------------------------
    def _target_std_frame(self) -> pd.DataFrame:
        """target.csv with standardized hierarchy + name columns (h0.., name)."""
        f = self.form()
        df = _read_csv_str(self.target_path)
        out = pd.DataFrame({'id': df[ROW_ID]} if ROW_ID in df.columns else {})
        for i, h in enumerate(f.get('target_hierarchy', [])):
            col = h.get('column')
            out[f'h{i}'] = df[col].map(std) if col in df.columns else ''
            out[f'h{i}_raw'] = df[col] if col in df.columns else ''
        nc = f.get('target_name_column')
        out['name'] = df[nc] if nc in df.columns else ''
        return out

    def level_rows(self, label: str) -> Dict:
        """Rows for the Admin names dock at one hierarchy level."""
        labels = self.levels()[:-1]
        i = labels.index(label)
        lk = _read_csv_str(self.lookups_dir / f'{label}_lookup.csv')
        tgt = self._target_std_frame()
        counts = Counter(tgt[f'h{i}']) if f'h{i}' in tgt.columns else Counter()
        # Parent chain for each std value (first seen) for display.
        parents = {}
        for _, r in tgt.iterrows():
            v = r[f'h{i}']
            if v and v not in parents:
                parents[v] = ' › '.join(r[f'h{k}_raw'] for k in range(i))
        best = sg.best_scores(self.config(), label, restrict=True, pipeline=self.pipeline()) if not lk.empty else {}
        rows, ref_only = [], []
        for _, r in lk.iterrows():
            mt = r['match_type']
            if mt == 'reference_only':
                ref_only.append(r['reference_name'])
                continue
            key = std(r['target_name_standardized'] or r['target_name_raw'])
            rows.append({
                'key': key, 'name': r['target_name_raw'] or key,
                'status': 'pending' if mt == 'no_candidate' else ('no_equivalent' if mt == 'no_equivalent' else 'linked'),
                'method': mt, 'ref': r['reference_name'], 'rationale': r['mapping_rationale'],
                'communities': int(counts.get(key, 0)), 'parents': parents.get(key, ''),
                'best': best.get(key, 0.0) if mt == 'no_candidate' else None,
                'score': sg.score_from_rationale(r['mapping_rationale']),
            })
        order = {'pending': 0, 'no_equivalent': 1, 'linked': 2}
        rows.sort(key=lambda x: (order[x['status']], -(x['best'] or 0), x['name'].lower()))
        return {'level': label, 'rows': rows, 'reference_only': sorted(ref_only),
                'pending': sum(1 for x in rows if x['status'] == 'pending'),
                'pending_communities': sum(x['communities'] for x in rows if x['status'] == 'pending'),
                'total_communities': int(sum(counts.values()))}

    def candidates(self, level, key, restrict=True, top=3) -> List[Dict]:
        cands = sg.candidates(self.config(), level, key, restrict=restrict, top=top,
                              pipeline=None if level == 'leaf' else self.pipeline())
        if level == 'leaf' and cands:
            coords = self._ref_coords()
            for c in cands:
                lat, lon, raw = coords.get(links._norm_id(c['ref_id']), ('', '', ''))
                c.update({'lat': lat, 'lon': lon, 'ref_name_raw': raw or c['ref_name']})
        return cands

    def ref_level_values(self, label) -> Dict[str, str]:
        """std value -> raw value for the reference column paired with ``label``."""
        f = self.form()
        labels = self.levels()[:-1]
        if label not in labels:
            return {}
        hier = [h.get('column') for h in f.get('ref_hierarchy', [])]
        col = hier[labels.index(label)] if labels.index(label) < len(hier) else None
        df = _read_csv_str(self.ref_path)
        if not col or col not in df.columns:
            return {}
        out = {}
        for v in df[col].unique():
            if v and std(v) not in out:
                out[std(v)] = v
        return out

    def annotate_boundaries(self, geojson: Dict, name_property: str, label: str) -> Dict:
        """Add ``_ref`` (matched reference std value) to every feature. Exact
        normalized match first, then fuzzy >= 90. Returns match stats."""
        refs = self.ref_level_values(label)
        matched = set()
        for ft in geojson.get('features', []):
            props = ft.setdefault('properties', {})
            name = std(props.get(name_property, ''))
            hit = name if name in refs else ''
            if not hit and name:
                best, bs = '', 0
                for r in refs:
                    sc = sg.score_pair(name, r)
                    if sc > bs:
                        best, bs = r, sc
                if bs >= 90:
                    hit = best
            props['_ref'] = hit
            props['_name'] = fix_mojibake(props.get(name_property, ''))
            if hit:
                matched.add(hit)
        return {'matched': len(matched), 'reference_values': len(refs)}

    # ---- reference points ------------------------------------------------
    def _ref_coords(self) -> Dict[str, tuple]:
        f = self.form()
        df = _read_csv_str(self.ref_path)
        idc, latc, lonc, nc = f.get('ref_id_column') or ROW_ID, f.get('ref_lat_column'), f.get('ref_lon_column'), f.get('ref_name_column')
        if df.empty or idc not in df.columns:
            return {}
        return {links._norm_id(r[idc]): (r.get(latc, ''), r.get(lonc, ''), r.get(nc, '')) for _, r in df.iterrows()}

    def places(self, scope_label=None, values=None, target_id=None, cap=4000) -> List[Dict]:
        """Reference points as [{id,name,lat,lon,admin,linked}].

        scope_label+values: points whose standardized value at that level is
        in ``values``. target_id: points in that community's full parent group.
        """
        f = self.form()
        df = _read_csv_str(self.ref_path)
        if df.empty:
            return []
        idc, latc, lonc, nc = f.get('ref_id_column') or ROW_ID, f.get('ref_lat_column'), f.get('ref_lon_column'), f.get('ref_name_column')
        hier = [h['column'] for h in f.get('ref_hierarchy', []) if h.get('column')]
        labels = self.levels()[:-1]
        for k, col in enumerate(hier):
            df[f'_h{k}'] = df[col].map(std)
        mask = pd.Series(True, index=df.index)
        if scope_label is not None and values is not None:
            k = labels.index(scope_label)
            mask &= df[f'_h{k}'].isin({std(v) for v in values})
        if target_id is not None:
            lk = _read_csv_str(self.lookups_dir / 'leaf_lookup.csv')
            row = lk[lk['target_id'] == str(target_id)]
            if row.empty:
                return []
            row = row.iloc[0]
            for k, lbl in enumerate(labels):
                mask &= df[f'_h{k}'] == std(row.get(f'target_{lbl}', ''))
        sub = df[mask]
        lk = _read_csv_str(self.lookups_dir / 'leaf_lookup.csv')
        linked = set()
        if not lk.empty:
            linked = set(lk[lk['match_type'].isin(['exact', 'fuzzy_dist', 'fuzzy_score', 'manual'])]['ref_id'].map(links._norm_id))
        out = []
        for _, r in sub.head(cap).iterrows():
            try:
                lat, lon = float(r[latc]), float(r[lonc])
            except (ValueError, TypeError, KeyError):
                continue
            rid = links._norm_id(r[idc])
            out.append({'id': rid, 'name': r.get(nc, ''), 'lat': lat, 'lon': lon,
                        'admin': ' › '.join(r[c] for c in hier), 'linked': rid in linked})
        return out

    # ---- link stage ------------------------------------------------------
    def unlinked(self, include_linked=False) -> List[Dict]:
        lk = _read_csv_str(self.lookups_dir / 'leaf_lookup.csv')
        if lk.empty:
            return []
        labels = self.levels()[:-1]
        pins = {links._norm_id(t) for t in self.pins()['target_id']} if self.pins_path.exists() else set()
        pool = Counter()
        for _, r in lk[lk['match_type'] == 'reference_only'].iterrows():
            pool[tuple(std(r.get(f'ref_{l}', '')) for l in labels)] += 1
        out = []
        for _, r in lk[lk['match_type'] != 'reference_only'].iterrows():
            tid = links._norm_id(r['target_id'])
            mt = r['match_type']
            if tid in pins:
                status = 'pin'
            elif mt == 'no_candidate':
                status = 'pending'
            elif mt == 'no_equivalent':
                status = 'no_equivalent'
            else:
                status = 'linked'
            if not include_linked and status != 'pending':
                continue
            out.append({
                'id': tid, 'name': r['target_name_raw'] or r['target_name_standardized'],
                'path': ' › '.join(r.get(f'target_{l}', '') for l in labels),
                'parents': {l: r.get(f'target_{l}', '') for l in labels},
                'status': status, 'method': mt, 'ref_id': r['ref_id'], 'ref_name': r['ref_name'],
                'score': sg.score_from_rationale(r['mapping_rationale']),
                'auto': status == 'linked' and (mt != 'manual' or links.is_auto(r['mapping_rationale'])),
                'pool': pool[tuple(std(r.get(f'target_{l}', '')) for l in labels)],
            })
        # Pending communities with something to choose from come first.
        order = {'pending': 0, 'linked': 1, 'pin': 1, 'no_equivalent': 2}
        out.sort(key=lambda x: (order[x['status']], x['status'] == 'pending' and x['pool'] == 0,
                                x['path'], x['name'].lower()))
        return out

    # ---- pins ------------------------------------------------------------
    def pins(self) -> pd.DataFrame:
        df = _read_csv_str(self.pins_path)
        return df if not df.empty else pd.DataFrame(columns=PIN_COLUMNS)

    def set_pin(self, target_id, lat, lon) -> Optional[Dict]:
        df = self.pins()
        tid = links._norm_id(target_id)
        prev = df[df['target_id'].map(links._norm_id) == tid]
        previous = None if prev.empty else {'lat': prev.iloc[0]['latitude'], 'lon': prev.iloc[0]['longitude']}
        df = df[df['target_id'].map(links._norm_id) != tid]
        df = pd.concat([df, pd.DataFrame([{'target_id': tid, 'latitude': lat, 'longitude': lon}])], ignore_index=True)
        self.pins_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.pins_path, index=False)
        return previous

    def clear_pin(self, target_id) -> Optional[Dict]:
        df = self.pins()
        tid = links._norm_id(target_id)
        prev = df[df['target_id'].map(links._norm_id) == tid]
        if prev.empty:
            return None
        df[df['target_id'].map(links._norm_id) != tid].to_csv(self.pins_path, index=False)
        return {'lat': prev.iloc[0]['latitude'], 'lon': prev.iloc[0]['longitude']}

    # ---- auto-match stage ------------------------------------------------
    def histogram(self, restrict=True) -> Dict:
        scores = sg.best_scores(self.config(), 'leaf', restrict=restrict)
        bins = [0] * len(HIST_BINS)
        for s in scores.values():
            if s <= 0:
                continue
            idx = min(len(HIST_BINS) - 1, max(0, int((s - 50) // 5)))
            bins[idx] += 1
        return {'bins': [{'lo': lo, 'hi': hi, 'n': n} for (lo, hi), n in zip(HIST_BINS, bins)],
                'scores': scores}

    def run_auto(self, threshold: int, restrict: bool) -> Dict:
        cfg = self.config()
        if self.has_lookups():
            links.revert_auto(cfg, 'leaf')
        log = self.run_lookups()
        scope = None if restrict else 0
        sugg = sg.suggest(cfg, 'leaf', threshold=threshold, scope_depth=scope)
        res = sg.apply(cfg, 'leaf', sugg) if sugg else {'applied': 0, 'dropped': 0, 'keys': []}
        from datetime import datetime
        self.update_form(ran=True, last_run=datetime.now().strftime('%H:%M'), threshold=threshold, restrict=restrict)
        return {'applied': res['applied'], 'log': log}

    # ---- review ----------------------------------------------------------
    def review_rows(self) -> List[Dict]:
        cfg = self.config()
        df = export.write_geocoded(cfg, pins=self.pins())
        f = self.form()
        nc = f.get('target_name_column')
        hier = [h['column'] for h in f.get('target_hierarchy', []) if h.get('column')]
        rows = []
        for _, r in df.iterrows():
            rows.append({
                'id': r[ROW_ID], 'name': r.get(nc, ''),
                'path': ' › '.join(str(r.get(c, '')) for c in hier),
                'loc': r['linked_place_name'], 'lat': r['latitude'], 'lon': r['longitude'],
                'score': r['score'], 'by': r['set_by'], 'match_type': r['match_type'],
            })
        return rows

    # ---- undoable operations --------------------------------------------
    def apply_op(self, op: str, args: Dict) -> Dict:
        cfg = self.config()
        if op == 'link':
            return links.link(cfg, args['level'], args['target_key'], args['ref_key'],
                              rationale=args.get('rationale', 'manual: linked'))
        if op == 'unlink':
            return links.unlink(cfg, args['level'], args['target_key'])
        if op == 'no_equivalent':
            return links.set_no_equivalent(cfg, args['level'], args['target_key'])
        if op == 'clear_no_equivalent':
            return links.clear_no_equivalent(cfg, args['level'], args['target_key'])
        if op == 'unlink_many':
            for k in args['keys']:
                links.unlink(cfg, args['level'], k)
            return {'ok': True}
        if op == 'pin':
            self.set_pin(args['target_id'], args['lat'], args['lon'])
            return {'ok': True}
        if op == 'unpin':
            self.clear_pin(args['target_id'])
            return {'ok': True}
        if op == 'multi':
            for step in args['steps']:
                self.apply_op(step['op'], step['args'])
            return {'ok': True}
        return {'ok': False, 'error': f'unknown op {op}'}

    def undo(self) -> Dict:
        entry = self.history.undoable()
        if not entry:
            return {'ok': False, 'error': 'nothing to undo'}
        inv = entry['inverse']
        res = self.apply_op(inv['op'], inv['args'])
        self.history.consume(entry)
        self.history.add('undo · ' + entry['text'])
        return {'ok': bool(res.get('ok', True)), 'undone': entry['text']}
