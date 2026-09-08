"""Flask web application for Match-Bot GUI."""

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import uuid
import webbrowser
from pathlib import Path

import pandas as pd
from flask import (
    Flask,
    flash,
    jsonify,
    render_template,
    request,
    send_from_directory,
    session,
)

from match_bot import gazetteer as gz
from match_bot.core.data_loader import get_columns
from match_bot.gui.builder import (
    build_config,
    form_data_to_yaml,
    yaml_to_form_data,
)
from match_bot.gui.levels import HIER_LOOKUP_COLUMNS, LevelState

# Ungrouped matching builds a k×k float64 matrix; beyond this many points we
# require a hierarchy level so matching is grouped (see gazetteer README).
GEO_OOM_POINT_LIMIT = 5000

# Cap map payloads so huge gazetteer selections don't lock up Leaflet.
GEO_MAP_FEATURE_CAP = 15000


def _predissolve_geojson(session_dir, form_data):
    """Pre-generate dissolved GeoJSON files for each hierarchy level + leaf.

    Skips if dissolved files already exist (geometry doesn't change between runs).
    """
    import geopandas as gpd

    geojson_path = session_dir / 'target.geojson'
    if not geojson_path.exists():
        return

    output_dir = session_dir / 'output'
    if (output_dir / 'dissolved_leaf.geojson').exists():
        return

    gdf = gpd.read_file(str(geojson_path))
    output_dir = session_dir / 'output'
    output_dir.mkdir(parents=True, exist_ok=True)

    hierarchy = form_data.get('target_hierarchy', [])
    name_col = form_data.get('target_name_column', '')

    # Dissolve for each hierarchy level
    for h in hierarchy:
        col = h.get('column', '')
        label = h.get('label', '')
        if col and col in gdf.columns:
            dissolved = gdf.dissolve(by=[col], as_index=False)
            dissolved.to_file(
                str(output_dir / f'dissolved_{label}.geojson'), driver='GeoJSON',
            )

    # Dissolve for leaf level (hierarchy columns + name column)
    hier_cols = [h['column'] for h in hierarchy if h.get('column') in gdf.columns]
    leaf_cols = hier_cols + ([name_col] if name_col and name_col in gdf.columns else [])
    if leaf_cols:
        dissolved = gdf.dissolve(by=leaf_cols, as_index=False)
        dissolved.to_file(
            str(output_dir / 'dissolved_leaf.geojson'), driver='GeoJSON',
        )


# ---------------------------------------------------------------------------
# Geocoding-mode helpers
#
# The Geocoding tab matches a reference CSV against a CSV of points. The
# matching pipeline itself is unchanged (it ignores coordinate columns);
# these helpers re-join coordinates onto its outputs and keep manual
# map-picked matches alive across re-runs.
# ---------------------------------------------------------------------------

MANUAL_GEOCODE_COLUMNS = ['ref_id', 'target_id', 'latitude', 'longitude']


def _read_csv_str(path):
    """Read a CSV with every value as a string ('' for missing).

    String dtype keeps IDs byte-stable (no float round-trips like '7' -> '7.0')
    so cross-file ID comparisons are exact.
    """
    if not Path(path).exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str).fillna('')
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_manual_geocodes(session_dir):
    df = _read_csv_str(session_dir / 'output' / 'manual_geocode.csv')
    if df.empty:
        return pd.DataFrame(columns=MANUAL_GEOCODE_COLUMNS)
    return df


def _write_manual_geocodes(session_dir, df):
    out_dir = session_dir / 'output'
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / 'manual_geocode.csv', index=False)


def _point_coords(session_dir, id_col, lat_col, lon_col):
    """Build {target_id: (lat, lon)} from target.csv, plus a skipped count.

    Rows with unparseable or out-of-range coordinates are skipped (they stay
    matchable by name, just can't be placed on the map).
    """
    target_df = _read_csv_str(session_dir / 'target.csv')
    coords = {}
    skipped = 0
    if target_df.empty or not all(c in target_df.columns for c in (id_col, lat_col, lon_col)):
        return coords, skipped
    lat = pd.to_numeric(target_df[lat_col], errors='coerce')
    lon = pd.to_numeric(target_df[lon_col], errors='coerce')
    valid = lat.notna() & lon.notna() & lat.abs().le(90) & lon.abs().le(180)
    skipped = int((~valid).sum())
    for tid, la, lo in zip(target_df.loc[valid, id_col], lat[valid], lon[valid]):
        coords[str(tid)] = (float(la), float(lo))
    return coords, skipped


def _ref_raw_names(session_dir, ref_id_col, ref_name_col):
    """Map reference ID -> original (raw) name from ref.csv.

    matched.csv only carries standardized names, so display names are joined
    back from the uploaded file.
    """
    ref_df = _read_csv_str(session_dir / 'ref.csv')
    if ref_df.empty or ref_id_col not in ref_df.columns or ref_name_col not in ref_df.columns:
        return {}
    return dict(zip(ref_df[ref_id_col].astype(str), ref_df[ref_name_col].astype(str)))


def _geocode_rows(session_dir, form_data):
    """Resolve coordinates for every matched.csv row.

    Returns a list of dicts: {ref_id, ref_name, target_id, point_name,
    latitude, longitude, match_type}. Coordinates come from the points CSV
    for point matches, or from manual_geocode.csv for map-click matches
    (and as a fallback when a point vanished from a re-uploaded CSV).
    """
    matched_df = _read_csv_str(session_dir / 'output' / 'matched.csv')
    if matched_df.empty or '_ref_id' not in matched_df.columns:
        return []
    coords, _ = _point_coords(
        session_dir,
        form_data.get('target_id_column', ''),
        form_data.get('geo_lat_column', ''),
        form_data.get('geo_lon_column', ''),
    )
    manual_df = _read_manual_geocodes(session_dir)
    manual_coords = {
        str(r['ref_id']): (r['latitude'], r['longitude'])
        for _, r in manual_df.iterrows()
        if str(r.get('latitude', '')) and str(r.get('longitude', ''))
    }
    raw_names = _ref_raw_names(
        session_dir,
        form_data.get('ref_id_column', ''),
        form_data.get('ref_name_column', ''),
    )

    rows = []
    for _, m in matched_df.iterrows():
        ref_id = str(m.get('_ref_id', ''))
        target_id = str(m.get('_target_id', ''))
        lat = lon = ''
        if target_id and target_id in coords:
            lat, lon = coords[target_id]
        elif ref_id in manual_coords:
            lat, lon = manual_coords[ref_id]
        rows.append({
            'ref_id': ref_id,
            'ref_name': raw_names.get(
                ref_id, str(m.get('_ref_name_raw', '') or m.get('_ref_name', ''))),
            'target_id': target_id,
            'point_name': str(m.get('_target_name_raw', '') or m.get('_target_name', '')),
            'latitude': lat,
            'longitude': lon,
            'match_type': str(m.get('_match_type', '')),
        })
    return rows


def _write_geocoded_csv(session_dir, form_data):
    """Write output/geocoded.csv: the original reference CSV plus coordinates.

    Columns appended: matched_point_id, matched_point_name, latitude,
    longitude, match_type. Unmatched reference rows are kept with blanks so
    the download always covers the full dataset.
    """
    ref_id_col = form_data.get('ref_id_column', '')
    ref_df = _read_csv_str(session_dir / 'ref.csv')
    if ref_df.empty or ref_id_col not in ref_df.columns:
        return

    by_ref = {r['ref_id']: r for r in _geocode_rows(session_dir, form_data)}
    out = ref_df.copy()
    keys = out[ref_id_col].astype(str)
    out['matched_point_id'] = [by_ref.get(k, {}).get('target_id', '') for k in keys]
    out['matched_point_name'] = [by_ref.get(k, {}).get('point_name', '') for k in keys]
    out['latitude'] = [by_ref.get(k, {}).get('latitude', '') for k in keys]
    out['longitude'] = [by_ref.get(k, {}).get('longitude', '') for k in keys]
    out['match_type'] = [by_ref.get(k, {}).get('match_type', '') for k in keys]

    out_dir = session_dir / 'output'
    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_dir / 'geocoded.csv', index=False)


def _merge_manual_geocodes(manual_df, matched_df, un_ref_df, un_tgt_df,
                           ref_ids, tgt_ids):
    """Re-impose stored manual geocodes onto fresh pipeline outputs.

    Manual decisions win over fuzzy results on both sides: any matched row
    sharing a manual row's reference or point is evicted and its other half
    returns to the unmatched pool. Pure function: takes and returns
    DataFrames (matched, unmatched_ref, unmatched_target, manual, log lines).
    """
    logs = []
    if manual_df.empty:
        return matched_df, un_ref_df, un_tgt_df, manual_df, logs

    matched_df = matched_df.copy()
    un_ref_df = un_ref_df.copy()
    un_tgt_df = un_tgt_df.copy()

    def project(row, columns):
        return {col: row.get(col, '') for col in columns}

    kept_manual = []
    for _, mrow in manual_df.iterrows():
        ref_id = str(mrow.get('ref_id', ''))
        target_id = str(mrow.get('target_id', ''))
        lat = str(mrow.get('latitude', ''))
        lon = str(mrow.get('longitude', ''))

        if ref_id not in ref_ids:
            logs.append(f'Manual geocode for reference "{ref_id}" dropped '
                        '(record no longer in the reference CSV).')
            continue
        if target_id and target_id not in tgt_ids:
            logs.append(f'Manual geocode for reference "{ref_id}": point '
                        f'"{target_id}" no longer in the points CSV — kept as '
                        'a coordinate-only match.')
            target_id = ''

        # Evict fuzzy/previous rows that conflict with this manual decision
        ref_conflict = matched_df['_ref_id'].astype(str) == ref_id if '_ref_id' in matched_df.columns else pd.Series(False, index=matched_df.index)
        tgt_conflict = (matched_df['_target_id'].astype(str) == target_id) if (target_id and '_target_id' in matched_df.columns) else pd.Series(False, index=matched_df.index)
        evicted = matched_df[ref_conflict | tgt_conflict]

        ref_source = None
        tgt_source = None
        for _, ev in evicted.iterrows():
            ev_ref = str(ev.get('_ref_id', ''))
            ev_tgt = str(ev.get('_target_id', ''))
            if ev_ref == ref_id:
                ref_source = ev
                # Its previous point (if different) returns to the pool
                if ev_tgt and ev_tgt != target_id and '_target_id' in un_tgt_df.columns:
                    un_tgt_df = pd.concat(
                        [un_tgt_df, pd.DataFrame([project(ev, un_tgt_df.columns)])],
                        ignore_index=True)
            else:
                # Point stolen from another ref: that ref becomes unmatched
                if '_ref_id' in un_ref_df.columns:
                    un_ref_df = pd.concat(
                        [un_ref_df, pd.DataFrame([project(ev, un_ref_df.columns)])],
                        ignore_index=True)
            if ev_tgt == target_id:
                tgt_source = ev
        matched_df = matched_df[~(ref_conflict | tgt_conflict)]

        # Locate ref-side data: unmatched pool first, else the evicted row
        if not un_ref_df.empty and '_ref_id' in un_ref_df.columns:
            hit = un_ref_df[un_ref_df['_ref_id'].astype(str) == ref_id]
            if not hit.empty:
                ref_source = hit.iloc[0]
                un_ref_df = un_ref_df[un_ref_df['_ref_id'].astype(str) != ref_id]
        if target_id and not un_tgt_df.empty and '_target_id' in un_tgt_df.columns:
            hit = un_tgt_df[un_tgt_df['_target_id'].astype(str) == target_id]
            if not hit.empty:
                tgt_source = hit.iloc[0]
                un_tgt_df = un_tgt_df[un_tgt_df['_target_id'].astype(str) != target_id]

        if ref_source is None:
            logs.append(f'Manual geocode for reference "{ref_id}" skipped '
                        '(record not found in match outputs).')
            continue

        new_row = {col: '' for col in matched_df.columns}
        for col in matched_df.columns:
            if col in ref_source.index and str(col).startswith('_ref'):
                new_row[col] = ref_source[col]
            if tgt_source is not None and col in tgt_source.index and str(col).startswith('_target'):
                new_row[col] = tgt_source[col]
        new_row['_match_type'] = 'manual'
        if '_levenshtein_distance' in new_row:
            new_row['_levenshtein_distance'] = ''
        if '_mapping_rationale' in new_row:
            new_row['_mapping_rationale'] = (
                'manual geocode (map point)' if target_id
                else f'manual geocode (coordinates {lat}, {lon})')
        matched_df = pd.concat([matched_df, pd.DataFrame([new_row])], ignore_index=True)

        kept_manual.append({'ref_id': ref_id, 'target_id': target_id,
                            'latitude': lat, 'longitude': lon})

    manual_out = pd.DataFrame(kept_manual, columns=MANUAL_GEOCODE_COLUMNS)
    return matched_df, un_ref_df, un_tgt_df, manual_out, logs


def _apply_manual_geocodes(session_dir, form_data):
    """Load state, run the manual merge, and write everything back.

    Returns log lines describing what was re-applied/dropped.
    """
    manual_df = _read_manual_geocodes(session_dir)
    if manual_df.empty:
        return []

    out_dir = session_dir / 'output'
    matched_df = _read_csv_str(out_dir / 'matched.csv')
    un_ref_df = _read_csv_str(out_dir / 'unmatched_ref.csv')
    un_tgt_df = _read_csv_str(out_dir / 'unmatched_target.csv')
    if matched_df.empty and un_ref_df.empty:
        return []

    ref_df = _read_csv_str(session_dir / 'ref.csv')
    tgt_df = _read_csv_str(session_dir / 'target.csv')
    ref_id_col = form_data.get('ref_id_column', '')
    tgt_id_col = form_data.get('target_id_column', '')
    ref_ids = set(ref_df[ref_id_col].astype(str)) if ref_id_col in ref_df.columns else set()
    tgt_ids = set(tgt_df[tgt_id_col].astype(str)) if tgt_id_col in tgt_df.columns else set()

    matched_df, un_ref_df, un_tgt_df, manual_out, logs = _merge_manual_geocodes(
        manual_df, matched_df, un_ref_df, un_tgt_df, ref_ids, tgt_ids)

    matched_df.to_csv(out_dir / 'matched.csv', index=False)
    un_ref_df.to_csv(out_dir / 'unmatched_ref.csv', index=False)
    un_tgt_df.to_csv(out_dir / 'unmatched_target.csv', index=False)
    _write_manual_geocodes(session_dir, manual_out)
    applied = len(manual_out)
    if applied:
        logs.append(f'Re-applied {applied} manual geocode(s).')
    return logs


def _read_gaz_state(session_dir):
    path = session_dir / 'gaz_state.json'
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_gaz_state(session_dir, state):
    with open(session_dir / 'gaz_state.json', 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def _read_form_state(session_dir):
    path = session_dir / 'form_state.json'
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _update_form_state(session_dir, **keys):
    """Merge top-level keys into form_state.json.

    Merge (not overwrite) because two writers touch the file: the client
    snapshot posts 'form'/'ui', while the upload endpoint records 'files'.
    """
    state = _read_form_state(session_dir) or {}
    state.update(keys)
    with open(session_dir / 'form_state.json', 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def _clear_match_outputs(session_dir):
    """Remove match outputs whose target ids reference a replaced points pool.

    manual_geocode.csv is deliberately kept — the manual-merge step re-applies
    it after the next run (vanished points degrade to coordinate matches).
    """
    out_dir = session_dir / 'output'
    for name in ('matched.csv', 'unmatched_ref.csv', 'unmatched_target.csv',
                 'geocoded.csv'):
        p = out_dir / name
        if p.exists():
            p.unlink()


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = os.urandom(24)
    # Cookies are domain-scoped, not port-scoped: two GUI instances on different
    # ports would otherwise overwrite each other's session cookie. Naming the
    # cookie per-port lets parallel instances coexist in one browser.
    port = os.environ.get('MATCH_BOT_PORT', '5000')
    app.config['SESSION_COOKIE_NAME'] = f'match_bot_session_{port}'

    upload_base = Path(app.instance_path) / 'uploads'
    upload_base.mkdir(parents=True, exist_ok=True)
    app.config['UPLOAD_BASE'] = upload_base

    def _request_mode():
        """Return 'geo' when the request targets the Geocoding workflow.

        Checked in query args, form fields, and the JSON body so every
        endpoint style (GET, multipart upload, JSON POST) can carry it.
        """
        if request.args.get('mode') == 'geo':
            return 'geo'
        if request.form.get('mode') == 'geo':
            return 'geo'
        body = request.get_json(silent=True)
        if isinstance(body, dict) and body.get('mode') == 'geo':
            return 'geo'
        return None

    def _get_session_dir(mode=None):
        """Get or create the upload directory for the current session.

        The Geocoding tab ('geo' mode) works in its own subdirectory so the
        two workflows never clobber each other's uploads or outputs.
        """
        if 'sid' not in session:
            session['sid'] = str(uuid.uuid4())
        d = upload_base / session['sid']
        if mode == 'geo':
            d = d / 'geocoding'
        d.mkdir(parents=True, exist_ok=True)
        return d

    @app.route('/')
    def index():
        return render_template('index.html', active_tab='match')

    @app.route('/geocoding')
    def geocoding():
        return render_template('geocoding.html', active_tab='geocoding')

    @app.route('/api/reset', methods=['POST'])
    def api_reset():
        """Explicitly start a new project for one tab.

        Replaces the old page-load lookup purge: without this, a different
        dataset loaded in the same sticky session would inherit the previous
        dataset's lookup rewrites (load_lookups applies them unconditionally).
        """
        mode = _request_mode()
        sd = _get_session_dir(mode)
        if mode == 'geo':
            shutil.rmtree(sd, ignore_errors=True)
            sd.mkdir(parents=True, exist_ok=True)
        else:
            # The geocoding tab's dir is nested inside this one — keep it.
            for child in sd.iterdir():
                if child.name == 'geocoding':
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
        prefix = 'geo_' if mode else ''
        for key in (f'{prefix}ref_ext', f'{prefix}target_ext'):
            session.pop(key, None)
        return jsonify({'ok': True})

    @app.route('/api/columns', methods=['POST'])
    def api_columns():
        """Upload a dataset file and return its column headers."""
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'No file selected'}), 400

        prefix = request.form.get('prefix', 'file')
        ext = Path(file.filename).suffix.lower()
        mode = _request_mode()

        # Validate allowed extensions per prefix (geocoding is CSV-only)
        if mode == 'geo':
            allowed = {'.csv'}
        else:
            allowed = {'.csv', '.geojson'} if prefix == 'target' else {'.csv'}
        if ext not in allowed:
            return jsonify({'error': f'Unsupported file type "{ext}" for {prefix}. Allowed: {", ".join(sorted(allowed))}'}), 400

        # Save with a predictable name, preserving original extension
        filename = f'{prefix}{ext}'
        sd = _get_session_dir(mode)
        save_path = sd / filename
        needs_reblend = False
        if mode == 'geo' and prefix == 'target' and _read_gaz_state(sd) is not None:
            # A gazetteer pool exists: keep the generated target.csv and stash
            # the upload for re-blending on the next gazetteer build.
            save_path = sd / 'user_points.csv'
            needs_reblend = True
            state = _read_gaz_state(sd)
            state['user_columns'] = None  # remap on next build
            _write_gaz_state(sd, state)
        file.save(str(save_path))
        if not needs_reblend:
            session[f'{"geo_" if mode else ""}{prefix}_ext'] = ext
        if mode != 'geo' and prefix == 'target':
            # A new target invalidates pre-dissolved layers, which are
            # otherwise served as-is (see _predissolve_geojson early return).
            for p in (sd / 'output').glob('dissolved_*.geojson'):
                p.unlink()

        try:
            cols = get_columns(str(save_path))
        except Exception as e:
            return jsonify({'error': f'Failed to read file: {e}'}), 400

        # Record the original filename so a reloaded page can restore the
        # file chips (the browser-side name is otherwise lost on navigation).
        files = (_read_form_state(sd) or {}).get('files') or {}
        files['user_points' if needs_reblend else prefix] = {
            'name': file.filename, 'ext': ext,
        }
        _update_form_state(sd, files=files)

        return jsonify({
            'columns': cols,
            'filename': file.filename,
            'needs_reblend': needs_reblend,
        })

    @app.route('/api/state', methods=['GET', 'POST'])
    def api_state():
        """Session persistence for page reloads and tab switches.

        POST stores the client's form snapshot verbatim (keys 'form'/'ui');
        GET returns everything a freshly loaded page needs to restore itself.
        Concurrent browser tabs are last-writer-wins, which is acceptable for
        a single-user local tool.
        """
        mode = _request_mode()
        sd = _get_session_dir(mode)

        if request.method == 'POST':
            body = request.get_json(silent=True) or {}
            updates = {k: body[k] for k in ('form', 'ui') if k in body}
            if updates:
                _update_form_state(sd, **updates)
            return jsonify({'ok': True})

        state = _read_form_state(sd) or {}
        files_meta = state.get('files') or {}

        def _file_info(prefix):
            meta = files_meta.get(prefix) or {}
            if prefix == 'user_points':
                path = sd / 'user_points.csv'
            else:
                ext = meta.get('ext') or session.get(
                    f'{"geo_" if mode else ""}{prefix}_ext') or '.csv'
                path = sd / f'{prefix}{ext}'
                if not path.exists() and prefix == 'target':
                    for cand in ('.csv', '.geojson'):
                        if (sd / f'target{cand}').exists():
                            path = sd / f'target{cand}'
                            break
            if not path.exists():
                return None
            info = {'name': meta.get('name') or path.name, 'ext': path.suffix}
            try:
                info['columns'] = get_columns(str(path))
            except Exception:
                info['columns'] = None
            return info

        files = {p: _file_info(p) for p in ('ref', 'target', 'user_points')}
        out = sd / 'output'
        lookups_dir = out / 'lookups'
        lookups = sorted(
            p.stem[: -len('_lookup')]
            for p in lookups_dir.glob('*_lookup.csv')
            if p.stem != 'leaf_lookup'
        ) if lookups_dir.exists() else []
        return jsonify({
            'exists': bool(state) or any(files.values()),
            'form': state.get('form'),
            'ui': state.get('ui'),
            'files': files,
            'gaz': _read_gaz_state(sd),
            'outputs': {
                'matched': (out / 'matched.csv').exists(),
                'geocoded': (out / 'geocoded.csv').exists(),
                'lookups': lookups,
            },
        })

    @app.route('/api/run/<action>', methods=['POST'])
    def api_run(action):
        """Run a pipeline action (match, lookups, suggest)."""
        if action not in ('match', 'lookups', 'suggest'):
            return jsonify({'error': f'Unknown action: {action}'}), 400

        data = request.get_json()
        if not data:
            return jsonify({'error': 'No form data provided'}), 400

        # Set file paths to the uploaded files in the session directory
        mode = _request_mode()
        target_ext = session.get(f'{"geo_" if mode else ""}target_ext', '.csv')
        data['ref_file'] = 'ref.csv'
        data['target_file'] = f'target{target_ext}'
        data['lookups_dir'] = 'output/lookups'
        data['output_dir'] = 'output'

        # Verify uploaded files exist
        sd = _get_session_dir(mode)
        if not (sd / 'ref.csv').exists():
            return jsonify({'error': 'Reference CSV not uploaded. Please upload a reference file first.'}), 400
        if not (sd / data['target_file']).exists():
            return jsonify({'error': 'Target file not uploaded. Please upload a target file first.'}), 400

        try:
            config = build_config(data, sd)
        except (ValueError, KeyError) as e:
            return jsonify({'error': f'Configuration error: {e}'}), 400

        # Validate that all configured columns exist in the uploaded files
        for label, file_name, ds_cfg in [
            ('Reference', 'ref.csv', config.reference),
            ('Target', data['target_file'], config.target),
        ]:
            csv_cols = get_columns(str(sd / file_name))
            for col_name, col_role in [
                (ds_cfg.id_column, 'ID'),
                (ds_cfg.name_column, 'Name'),
            ]:
                if col_name and col_name not in csv_cols:
                    return jsonify({'error': f'{label} {col_role} column "{col_name}" not found in {file_name}. Available columns: {csv_cols}'}), 400
            for level in ds_cfg.hierarchy:
                if level.column not in csv_cols:
                    return jsonify({'error': f'{label} hierarchy column "{level.column}" not found in {file_name}. Available columns: {csv_cols}'}), 400

        # OOM guard: ungrouped matching builds a k×k matrix — refuse huge
        # ungrouped point pools instead of exhausting memory.
        if action == 'match' and mode == 'geo' and not data.get('target_hierarchy'):
            try:
                n_points = len(_read_csv_str(sd / data['target_file']))
            except Exception:
                n_points = 0
            if n_points > GEO_OOM_POINT_LIMIT:
                return jsonify({'error': (
                    f'{n_points:,} points with no hierarchy grouping would build '
                    f'a {n_points:,}×{n_points:,} matching matrix. Add an admin1 '
                    'hierarchy level (the gazetteer provides one) or narrow the '
                    'selection.')}), 400

        # Capture stdout
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            downloads = []
            if action == 'match':
                from match_bot.scripts.run_matching import run
                result = run(config, verbose=True)
                matched_path = config.output_dir / 'matched.csv'
                if matched_path.exists():
                    downloads.append({
                        'name': 'matched.csv',
                        'url': f'/api/download/output/matched.csv',
                    })
                # Save unmatched DataFrames for the /api/unmatched endpoint
                if result is not None:
                    if result.unmatched_ref is not None:
                        result.unmatched_ref.to_csv(config.output_dir / 'unmatched_ref.csv', index=False)
                    if result.unmatched_target is not None:
                        result.unmatched_target.to_csv(config.output_dir / 'unmatched_target.csv', index=False)
            elif action == 'lookups':
                from match_bot.scripts.generate_lookups import run
                run(config)
                lookups_dir = config.lookups_dir
                if lookups_dir.exists():
                    for f in sorted(lookups_dir.iterdir()):
                        if f.suffix == '.csv':
                            downloads.append({
                                'name': f.name,
                                'url': f'/api/download/output/lookups/{f.name}',
                            })
            elif action == 'suggest':
                from match_bot.scripts.suggest_matches import run
                level = data.get('suggest_level', 'leaf')
                threshold = int(data.get('suggest_threshold', 70))
                run(config, level=level, threshold=threshold)
        except Exception as e:
            sys.stdout = old_stdout
            return jsonify({'error': str(e)}), 500
        finally:
            sys.stdout = old_stdout

        # Pre-dissolve GeoJSON at each hierarchy level for fast map switching
        if action == 'match' and mode != 'geo':
            if target_ext == '.geojson':
                try:
                    _predissolve_geojson(sd, data)
                except Exception:
                    pass  # Map will fall back to raw GeoJSON

        extra_lines = []
        if action == 'match' and mode == 'geo':
            # Re-impose stored manual geocodes (manual wins over fuzzy) and
            # rebuild the coordinate-joined output.
            try:
                extra_lines.extend(_apply_manual_geocodes(sd, data))
                _write_geocoded_csv(sd, data)
                if (sd / 'output' / 'geocoded.csv').exists():
                    downloads.append({
                        'name': 'geocoded.csv',
                        'url': '/api/download/output/geocoded.csv?mode=geo',
                    })
            except Exception as e:
                extra_lines.append(
                    f'WARNING: failed to apply manual geocodes: {e}. '
                    'Stored manual matches in output/manual_geocode.csv were not lost.')

        output_text = buffer.getvalue()
        if extra_lines:
            output_text = output_text.rstrip('\n') + '\n' + '\n'.join(extra_lines) + '\n'
        return jsonify({
            'output': output_text,
            'downloads': downloads,
        })

    @app.route('/api/download/<path:filepath>')
    def api_download(filepath):
        """Serve an output file for download."""
        full_path = _get_session_dir(_request_mode()) / filepath
        if not full_path.exists():
            return jsonify({'error': 'File not found'}), 404
        return send_from_directory(str(full_path.parent), full_path.name, as_attachment=True)

    @app.route('/api/load-config', methods=['POST'])
    def api_load_config():
        """Upload a YAML config and return form data."""
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'No file selected'}), 400

        try:
            yaml_str = file.read().decode('utf-8')
            form_data = yaml_to_form_data(yaml_str)
        except Exception as e:
            return jsonify({'error': f'Failed to parse YAML: {e}'}), 400

        # Restore saved crosswalk links into the session's lookup files so a
        # second pass over the same dataset starts with parents settled.
        restored = 0
        crosswalk = form_data.get('crosswalk') or {}
        if crosswalk:
            sd = _get_session_dir(_request_mode())
            lookups_dir = sd / 'output' / 'lookups'
            lookups_dir.mkdir(parents=True, exist_ok=True)
            for label, entries in crosswalk.items():
                rows = []
                for e in entries or []:
                    tgt = str(e.get('target', '')).strip()
                    ref = str(e.get('ref', '')).strip()
                    if not tgt or not ref:
                        continue
                    rows.append({
                        'target_column': f'_target_{label}',
                        'target_name_raw': str(e.get('target_raw', '') or tgt),
                        'target_name_standardized': tgt,
                        'reference_name': ref,
                        'match_type': str(e.get('method', 'manual')),
                        'mapping_rationale': str(e.get('rationale', '')),
                    })
                if rows:
                    pd.DataFrame(rows, columns=HIER_LOOKUP_COLUMNS).to_csv(
                        lookups_dir / f'{label}_lookup.csv', index=False)
                    restored += len(rows)

        return jsonify({'form_data': form_data, 'crosswalk_restored': restored})

    @app.route('/api/save-config', methods=['POST'])
    def api_save_config():
        """Generate a YAML config file from form data and return it for download."""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No form data provided'}), 400

        # Embed the session's hand-made crosswalk links so the config carries
        # the parent-level reconciliation, not just the settings. Labels come
        # from the lookup files on disk, not the client payload — a page
        # reloaded before restore sends empty ref_hierarchy.
        mode = _request_mode()
        sd = _get_session_dir(mode)
        lookups_dir = sd / 'output' / 'lookups'
        if lookups_dir.exists():
            crosswalk = {}
            for path in sorted(lookups_dir.glob('*_lookup.csv')):
                label = path.stem[: -len('_lookup')]
                if label == 'leaf':  # different schema, not a crosswalk
                    continue
                try:
                    df = pd.read_csv(path, dtype=str).fillna('')
                except Exception:
                    continue
                entries = []
                for _, row in df.iterrows():
                    tgt = str(row.get('target_name_standardized', '')).strip()
                    ref = str(row.get('reference_name', '')).strip()
                    if tgt and ref:
                        entries.append({
                            'target': tgt,
                            'target_raw': str(row.get('target_name_raw', '')),
                            'ref': ref,
                            'method': str(row.get('match_type', 'manual')),
                            'rationale': str(row.get('mapping_rationale', '')),
                        })
                if entries:
                    crosswalk[label] = entries
            if crosswalk:
                data['crosswalk'] = crosswalk

        # A reloaded page reports no gazetteer selection (gazActive is
        # client-side); fall back to the persisted build record.
        if mode == 'geo' and not data.get('gaz_country'):
            gaz_state = _read_gaz_state(sd)
            if gaz_state:
                data['gaz_country'] = gaz_state.get('iso3', '')
                data['gaz_admin1'] = gaz_state.get('admin1', [])

        yaml_str = form_data_to_yaml(data)
        return (
            yaml_str,
            200,
            {
                'Content-Type': 'application/x-yaml',
                'Content-Disposition': 'attachment; filename="config.yaml"',
            },
        )

    @app.route('/api/geojson')
    def api_geojson():
        """Serve the uploaded target GeoJSON file for Leaflet rendering."""
        sd = _get_session_dir(_request_mode())
        geojson_path = sd / 'target.geojson'
        if not geojson_path.exists():
            return jsonify({'error': 'No GeoJSON uploaded'}), 404
        return send_from_directory(str(sd), 'target.geojson', mimetype='application/geo+json')

    @app.route('/api/dissolved/<level>')
    def api_dissolved(level):
        """Serve a pre-dissolved GeoJSON layer for the given level."""
        sd = _get_session_dir(_request_mode())
        filename = f'dissolved_{level}.geojson'
        path = sd / 'output' / filename
        if not path.exists():
            return jsonify({'error': f'No dissolved layer for level "{level}"'}), 404
        return send_from_directory(str(sd / 'output'), filename, mimetype='application/geo+json')

    @app.route('/api/geojson-view', methods=['POST'])
    def api_geojson_view():
        """Serve dissolved GeoJSON with match status for map display.

        Expects JSON body: {level, dissolve: [col, ...], id_col (for leaf)}
        """
        try:
            import geopandas as gpd
        except ImportError:
            return jsonify({'error': 'geopandas required for map views'}), 500

        data = request.get_json() or {}
        level = data.get('level', 'leaf')
        dissolve_cols = data.get('dissolve', [])
        id_col = data.get('id_col', '')
        hierarchy = data.get('hierarchy', [])  # [{column, label}, ...]

        sd = _get_session_dir(_request_mode())
        geojson_path = sd / 'target.geojson'
        if not geojson_path.exists():
            return jsonify({'error': 'No GeoJSON uploaded'}), 404

        gdf = gpd.read_file(str(geojson_path))

        # Dissolve to the requested level
        if dissolve_cols:
            valid_cols = [c for c in dissolve_cols if c in gdf.columns]
            if valid_cols:
                gdf = gdf.dissolve(by=valid_cols, as_index=False)

        # Determine matched features
        matched_path = sd / 'output' / 'matched.csv'
        matched_df = pd.read_csv(matched_path).fillna('') if matched_path.exists() else pd.DataFrame()

        if level == 'leaf':
            # Match by target ID
            matched_ids = set()
            if not matched_df.empty and '_target_id' in matched_df.columns:
                matched_ids = set(matched_df['_target_id'].astype(str).unique())
            if id_col and id_col in gdf.columns:
                gdf['_matched'] = gdf[id_col].astype(str).isin(matched_ids)
            else:
                gdf['_matched'] = False
        else:
            # Match by hierarchy name (case-insensitive)
            matched_names = set()
            target_col = f'_target_{level}'
            if not matched_df.empty and target_col in matched_df.columns:
                matched_names = set(matched_df[target_col].astype(str).str.lower().unique())

            # Also consider hierarchy lookup mappings
            lookup_path = sd / 'output' / 'lookups' / f'{level}_lookup.csv'
            if lookup_path.exists():
                lk = pd.read_csv(lookup_path).fillna('')
                mapped = lk[
                    (lk['reference_name'].astype(str).str.strip() != '')
                    & (lk['target_name_standardized'].astype(str).str.strip() != '')
                ]
                matched_names.update(
                    mapped['target_name_standardized'].astype(str).str.lower().unique()
                )

            # Find the GeoJSON column for this level
            geojson_col = ''
            for h in hierarchy:
                if h.get('label') == level:
                    geojson_col = h.get('column', '')
                    break
            if geojson_col and geojson_col in gdf.columns:
                gdf['_matched'] = gdf[geojson_col].astype(str).str.lower().isin(matched_names)
            else:
                gdf['_matched'] = False

        # Convert bool to int for JSON serialization consistency
        gdf['_matched'] = gdf['_matched'].astype(bool)

        return gdf.to_json(), 200, {'Content-Type': 'application/geo+json'}

    @app.route('/api/matched-set', methods=['POST'])
    def api_matched_set():
        """Return the set of matched values and the GeoJSON property to check.

        Lightweight endpoint for re-coloring an already-loaded dissolved map
        without re-running the dissolve.
        """
        data = request.get_json() or {}
        level = data.get('level', 'leaf')
        id_col = data.get('id_col', '')
        hierarchy = data.get('hierarchy', [])

        sd = _get_session_dir(_request_mode())
        matched_path = sd / 'output' / 'matched.csv'
        matched_df = pd.read_csv(matched_path).fillna('') if matched_path.exists() else pd.DataFrame()

        if level == 'leaf':
            matched_vals = []
            if not matched_df.empty and '_target_id' in matched_df.columns:
                matched_vals = matched_df['_target_id'].astype(str).unique().tolist()
            return jsonify({'matched': matched_vals, 'match_prop': id_col})
        else:
            matched_names = set()
            target_col = f'_target_{level}'
            if not matched_df.empty and target_col in matched_df.columns:
                matched_names = set(matched_df[target_col].astype(str).str.lower().unique())

            lookup_path = sd / 'output' / 'lookups' / f'{level}_lookup.csv'
            if lookup_path.exists():
                lk = pd.read_csv(lookup_path).fillna('')
                mapped = lk[
                    (lk['reference_name'].astype(str).str.strip() != '')
                    & (lk['target_name_standardized'].astype(str).str.strip() != '')
                ]
                matched_names.update(
                    mapped['target_name_standardized'].astype(str).str.lower().unique()
                )

            geojson_col = ''
            for h in hierarchy:
                if h.get('label') == level:
                    geojson_col = h.get('column', '')
                    break

            return jsonify({'matched': sorted(matched_names), 'match_prop': geojson_col})

    @app.route('/api/matched-ids')
    def api_matched_ids():
        """Return the set of matched target IDs from matched.csv."""
        sd = _get_session_dir(_request_mode())
        matched_path = sd / 'output' / 'matched.csv'
        if not matched_path.exists():
            return jsonify({'ids': []})
        df = pd.read_csv(matched_path)
        if '_target_id' not in df.columns:
            return jsonify({'ids': []})
        ids = df['_target_id'].dropna().unique().tolist()
        return jsonify({'ids': ids})

    @app.route('/api/unmatched')
    def api_unmatched():
        """Return unmatched reference and target records as JSON.

        Query params:
            level: 'leaf' (default) for leaf-level records, or a hierarchy
                   label (e.g. 'IU') for hierarchy-level unmatched names.
        """
        level = request.args.get('level', 'leaf')
        sd = _get_session_dir(_request_mode())

        if level == 'leaf':
            result = {}
            for key in ('unmatched_ref', 'unmatched_target'):
                path = sd / 'output' / f'{key}.csv'
                if path.exists():
                    df = pd.read_csv(path).fillna('')
                    result[key] = {
                        'columns': list(df.columns),
                        'rows': df.values.tolist(),
                    }
                else:
                    result[key] = {'columns': [], 'rows': []}
            return jsonify(result)

        # Hierarchy-level view: derive unmatched names at the given level
        matched_path = sd / 'output' / 'matched.csv'
        unmatched_ref_path = sd / 'output' / 'unmatched_ref.csv'
        unmatched_target_path = sd / 'output' / 'unmatched_target.csv'
        lookup_path = sd / 'output' / 'lookups' / f'{level}_lookup.csv'

        matched_df = pd.read_csv(matched_path).fillna('') if matched_path.exists() else pd.DataFrame()
        unmatched_ref_df = pd.read_csv(unmatched_ref_path).fillna('') if unmatched_ref_path.exists() else pd.DataFrame()
        unmatched_target_df = pd.read_csv(unmatched_target_path).fillna('') if unmatched_target_path.exists() else pd.DataFrame()

        ref_col = f'_ref_{level}'
        target_col = f'_target_{level}'

        # Names that are already matched (appear in matched.csv)
        matched_ref_names = set()
        matched_target_names = set()
        if not matched_df.empty:
            if ref_col in matched_df.columns:
                matched_ref_names = set(matched_df[ref_col].dropna().astype(str).unique())
            if target_col in matched_df.columns:
                matched_target_names = set(matched_df[target_col].dropna().astype(str).unique())

        # Also treat hierarchy lookup mappings as matched
        if lookup_path.exists():
            lk = pd.read_csv(lookup_path).fillna('')
            mapped = lk[
                (lk['reference_name'].astype(str).str.strip() != '')
                & (lk['target_name_standardized'].astype(str).str.strip() != '')
            ]
            matched_target_names.update(mapped['target_name_standardized'].astype(str).unique())
            matched_ref_names.update(mapped['reference_name'].astype(str).unique())

        # Unique unmatched names at this level
        unmatched_ref_names = []
        if not unmatched_ref_df.empty and ref_col in unmatched_ref_df.columns:
            all_ref = set(unmatched_ref_df[ref_col].dropna().astype(str).unique())
            unmatched_ref_names = sorted(all_ref - matched_ref_names)

        unmatched_target_names = []
        if not unmatched_target_df.empty and target_col in unmatched_target_df.columns:
            all_target = set(unmatched_target_df[target_col].dropna().astype(str).unique())
            unmatched_target_names = sorted(all_target - matched_target_names)

        return jsonify({
            'unmatched_ref': {
                'columns': ['_ref_name_raw'],
                'rows': [[n] for n in unmatched_ref_names],
            },
            'unmatched_target': {
                'columns': ['_target_name_raw'],
                'rows': [[n] for n in unmatched_target_names],
            },
        })

    @app.route('/api/manual-match', methods=['POST'])
    def api_manual_match():
        """Save a user-picked manual match.

        For leaf level: moves records between matched/unmatched CSVs.
        For hierarchy levels: creates a lookup mapping so the next
        'Run Match' uses the corrected hierarchy name.
        """
        data = request.get_json()
        if not data or 'ref_id' not in data or 'target_id' not in data:
            return jsonify({'error': 'ref_id and target_id are required'}), 400

        level = data.get('level', 'leaf')
        sd = _get_session_dir(_request_mode())

        if level != 'leaf':
            # Hierarchy-level match: save a name mapping in the lookup file
            ref_name = str(data['ref_id'])
            target_name = str(data['target_id'])

            lookups_dir = sd / 'output' / 'lookups'
            lookups_dir.mkdir(parents=True, exist_ok=True)
            lookup_path = lookups_dir / f'{level}_lookup.csv'

            if lookup_path.exists():
                lookup_df = pd.read_csv(lookup_path).fillna('')
            else:
                lookup_df = pd.DataFrame(columns=[
                    'target_column', 'target_name_raw', 'target_name_standardized',
                    'reference_name', 'match_type', 'mapping_rationale',
                ])

            # Update existing entry or append new one
            mask = lookup_df['target_name_standardized'].astype(str) == target_name
            if mask.any():
                idx = lookup_df[mask].index[0]
                lookup_df.at[idx, 'reference_name'] = ref_name
                lookup_df.at[idx, 'match_type'] = 'manual'
            else:
                new_entry = {
                    'target_column': f'_target_{level}',
                    'target_name_raw': target_name,
                    'target_name_standardized': target_name,
                    'reference_name': ref_name,
                    'match_type': 'manual',
                    'mapping_rationale': '',
                }
                lookup_df = pd.concat([lookup_df, pd.DataFrame([new_entry])], ignore_index=True)

            lookup_df.to_csv(lookup_path, index=False)
            return jsonify({'ok': True})

        # Leaf-level match: move records between CSVs
        ref_id = str(data['ref_id'])
        target_id = str(data['target_id'])

        matched_path = sd / 'output' / 'matched.csv'
        unmatched_ref_path = sd / 'output' / 'unmatched_ref.csv'
        unmatched_target_path = sd / 'output' / 'unmatched_target.csv'

        for p in (matched_path, unmatched_ref_path, unmatched_target_path):
            if not p.exists():
                return jsonify({'error': f'{p.name} not found — run matching first'}), 400

        matched_df = pd.read_csv(matched_path).fillna('')
        unmatched_ref_df = pd.read_csv(unmatched_ref_path).fillna('')
        unmatched_target_df = pd.read_csv(unmatched_target_path).fillna('')

        # Cast ID columns to string for safe comparison
        unmatched_ref_df['_ref_id'] = unmatched_ref_df['_ref_id'].astype(str)
        unmatched_target_df['_target_id'] = unmatched_target_df['_target_id'].astype(str)

        ref_row = unmatched_ref_df[unmatched_ref_df['_ref_id'] == ref_id]
        target_row = unmatched_target_df[unmatched_target_df['_target_id'] == target_id]

        if ref_row.empty:
            return jsonify({'error': f'Reference ID {ref_id} not found in unmatched records'}), 404
        if target_row.empty:
            return jsonify({'error': f'Target ID {target_id} not found in unmatched records'}), 404

        # Build a new matched row using existing matched.csv columns as template
        new_row = {col: '' for col in matched_df.columns}
        ref_data = ref_row.iloc[0]
        target_data = target_row.iloc[0]

        for col in matched_df.columns:
            if col in ref_data.index:
                new_row[col] = ref_data[col]
            if col in target_data.index:
                new_row[col] = target_data[col]

        new_row['_match_type'] = 'manual'
        new_row['_levenshtein_distance'] = ''
        new_row['_mapping_rationale'] = ''

        # Append to matched, remove from unmatched, save all three
        matched_df = pd.concat([matched_df, pd.DataFrame([new_row])], ignore_index=True)
        unmatched_ref_df = unmatched_ref_df[unmatched_ref_df['_ref_id'] != ref_id]
        unmatched_target_df = unmatched_target_df[unmatched_target_df['_target_id'] != target_id]

        matched_df.to_csv(matched_path, index=False)
        unmatched_ref_df.to_csv(unmatched_ref_path, index=False)
        unmatched_target_df.to_csv(unmatched_target_path, index=False)

        return jsonify({'ok': True})

    @app.route('/api/geo/points')
    def api_geo_points():
        """Serve the points CSV as GeoJSON for the Geocoding map.

        Query params: id_col, name_col, lat_col, lon_col. Each feature gets
        _matched (point appears in matched.csv) and _manual flags. Synthetic
        features are appended for coordinate-only manual matches so they show
        on the map too.
        """
        sd = _get_session_dir('geo')
        id_col = request.args.get('id_col', '')
        name_col = request.args.get('name_col', '')
        lat_col = request.args.get('lat_col', '')
        lon_col = request.args.get('lon_col', '')

        target_df = _read_csv_str(sd / 'target.csv')
        if target_df.empty or id_col not in target_df.columns:
            return jsonify({'type': 'FeatureCollection', 'features': [], 'skipped': 0})

        coords, skipped = _point_coords(sd, id_col, lat_col, lon_col)

        matched_df = _read_csv_str(sd / 'output' / 'matched.csv')
        matched_ids = set()
        manual_ids = set()
        if '_target_id' in matched_df.columns:
            matched_ids = {v for v in matched_df['_target_id'].astype(str) if v}
            if '_match_type' in matched_df.columns:
                manual_ids = {
                    str(v) for v, t in zip(matched_df['_target_id'],
                                           matched_df['_match_type'])
                    if str(v) and str(t) == 'manual'}

        has_source = 'source' in target_df.columns
        capped = 0
        features = []
        for _, row in target_df.iterrows():
            tid = str(row[id_col])
            if tid not in coords:
                continue
            if len(features) >= GEO_MAP_FEATURE_CAP:
                capped += 1
                continue
            lat, lon = coords[tid]
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {
                    'id': tid,
                    'name': str(row.get(name_col, '')) if name_col else tid,
                    '_matched': tid in matched_ids,
                    '_manual': tid in manual_ids,
                    '_source': str(row['source']) if has_source else '',
                },
            })

        # Coordinate-only manual matches (no CSV point behind them)
        manual_df = _read_manual_geocodes(sd)
        ref_names = {}
        if '_ref_id' in matched_df.columns:
            for _, m in matched_df.iterrows():
                ref_names[str(m['_ref_id'])] = str(
                    m.get('_ref_name_raw', '') or m.get('_ref_name', ''))
        ref_names.update(_ref_raw_names(
            sd,
            request.args.get('ref_id_col', ''),
            request.args.get('ref_name_col', ''),
        ))
        for _, m in manual_df.iterrows():
            if str(m.get('target_id', '')):
                continue
            try:
                lat, lon = float(m['latitude']), float(m['longitude'])
            except (ValueError, TypeError):
                continue
            rid = str(m['ref_id'])
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {
                    'id': f'ref:{rid}',
                    'name': ref_names.get(rid, rid),
                    '_matched': True,
                    '_manual': True,
                },
            })

        return jsonify({'type': 'FeatureCollection', 'features': features,
                        'skipped': skipped, 'capped': capped})

    @app.route('/api/geo/geocoded')
    def api_geo_geocoded():
        """Return the table of successfully geocoded reference records.

        Query params: id_col, lat_col, lon_col (points CSV columns, used to
        resolve coordinates for point matches).
        """
        sd = _get_session_dir('geo')
        form_data = {
            'target_id_column': request.args.get('id_col', ''),
            'geo_lat_column': request.args.get('lat_col', ''),
            'geo_lon_column': request.args.get('lon_col', ''),
            'ref_id_column': request.args.get('ref_id_col', ''),
            'ref_name_column': request.args.get('ref_name_col', ''),
        }
        rows = [r for r in _geocode_rows(sd, form_data)
                if str(r['latitude']) != '' and str(r['longitude']) != '']
        rows.sort(key=lambda r: r['ref_name'].lower())
        return jsonify({
            'columns': ['ref_id', 'ref_name', 'latitude', 'longitude',
                        'point_name', 'match_type', 'target_id'],
            'rows': [[r['ref_id'], r['ref_name'], r['latitude'], r['longitude'],
                      r['point_name'], r['match_type'], r['target_id']] for r in rows],
        })

    @app.route('/api/geo/manual-match', methods=['POST'])
    def api_geo_manual_match():
        """Save a map-picked manual geocode.

        Body: full form data plus ref_id, latitude, longitude, and target_id
        ('' for a raw map-click coordinate match). Persists to
        manual_geocode.csv, then re-applies all manual geocodes so the three
        match CSVs and geocoded.csv stay consistent.
        """
        data = request.get_json()
        if not data or not str(data.get('ref_id', '')):
            return jsonify({'error': 'ref_id is required'}), 400
        if data.get('latitude') in (None, '') or data.get('longitude') in (None, ''):
            return jsonify({'error': 'latitude and longitude are required'}), 400

        sd = _get_session_dir('geo')
        ref_id = str(data['ref_id'])
        target_id = str(data.get('target_id', '') or '')

        un_ref_df = _read_csv_str(sd / 'output' / 'unmatched_ref.csv')
        if '_ref_id' not in un_ref_df.columns or \
                not (un_ref_df['_ref_id'].astype(str) == ref_id).any():
            return jsonify({'error': f'Reference ID {ref_id} not found in unmatched records'}), 404

        if target_id:
            matched_df = _read_csv_str(sd / 'output' / 'matched.csv')
            if '_target_id' in matched_df.columns and \
                    (matched_df['_target_id'].astype(str) == target_id).any():
                return jsonify({'error': f'Point {target_id} is already matched'}), 409
            un_tgt_df = _read_csv_str(sd / 'output' / 'unmatched_target.csv')
            if '_target_id' not in un_tgt_df.columns or \
                    not (un_tgt_df['_target_id'].astype(str) == target_id).any():
                return jsonify({'error': f'Point {target_id} not found in unmatched points'}), 404

        manual_df = _read_manual_geocodes(sd)
        manual_df = manual_df[manual_df['ref_id'].astype(str) != ref_id]
        manual_df = pd.concat([manual_df, pd.DataFrame([{
            'ref_id': ref_id,
            'target_id': target_id,
            'latitude': str(data['latitude']),
            'longitude': str(data['longitude']),
        }])], ignore_index=True)
        _write_manual_geocodes(sd, manual_df)

        logs = _apply_manual_geocodes(sd, data)
        _write_geocoded_csv(sd, data)
        return jsonify({'ok': True, 'log': logs})

    @app.route('/api/geo/unmatch', methods=['POST'])
    def api_geo_unmatch():
        """Undo a geocode: return the reference record (and its point, if any)
        to the unmatched pools.

        Body: full form data plus ref_id. Note: unmatching a fuzzy result is
        allowed, but a later Run Match may re-match it.
        """
        data = request.get_json()
        if not data or not str(data.get('ref_id', '')):
            return jsonify({'error': 'ref_id is required'}), 400

        sd = _get_session_dir('geo')
        ref_id = str(data['ref_id'])
        out_dir = sd / 'output'

        matched_df = _read_csv_str(out_dir / 'matched.csv')
        if '_ref_id' not in matched_df.columns:
            return jsonify({'error': 'No matches found — run matching first'}), 400
        mask = matched_df['_ref_id'].astype(str) == ref_id
        if not mask.any():
            return jsonify({'error': f'Reference ID {ref_id} is not matched'}), 404

        un_ref_df = _read_csv_str(out_dir / 'unmatched_ref.csv')
        un_tgt_df = _read_csv_str(out_dir / 'unmatched_target.csv')

        row = matched_df[mask].iloc[0]
        if '_ref_id' in un_ref_df.columns:
            un_ref_df = pd.concat([un_ref_df, pd.DataFrame(
                [{col: row.get(col, '') for col in un_ref_df.columns}])],
                ignore_index=True)
        if str(row.get('_target_id', '')) and '_target_id' in un_tgt_df.columns:
            un_tgt_df = pd.concat([un_tgt_df, pd.DataFrame(
                [{col: row.get(col, '') for col in un_tgt_df.columns}])],
                ignore_index=True)

        matched_df = matched_df[~mask]
        matched_df.to_csv(out_dir / 'matched.csv', index=False)
        un_ref_df.to_csv(out_dir / 'unmatched_ref.csv', index=False)
        un_tgt_df.to_csv(out_dir / 'unmatched_target.csv', index=False)

        manual_df = _read_manual_geocodes(sd)
        if not manual_df.empty:
            _write_manual_geocodes(
                sd, manual_df[manual_df['ref_id'].astype(str) != ref_id])

        _write_geocoded_csv(sd, data)
        return jsonify({'ok': True})

    # ------------------------------------------------------------------
    # Level ladder (Geocoding tab): per-level match state, candidates,
    # link/unlink, and per-level rerun. Hierarchy links are crosswalk
    # entries in output/lookups/<label>_lookup.csv; leaf links go through
    # the existing manual-geocode / unmatch endpoints.
    # ------------------------------------------------------------------

    @app.route('/api/geo/levels', methods=['POST'])
    def api_geo_levels():
        """Full ladder state: one entry per level with its rows and counts."""
        data = request.get_json() or {}
        sd = _get_session_dir('geo')
        try:
            return jsonify(LevelState(sd, data).state())
        except Exception as e:
            return jsonify({'error': f'Failed to compute level state: {e}'}), 500

    @app.route('/api/geo/level/candidates', methods=['POST'])
    def api_geo_level_candidates():
        """Scored candidates for one unresolved row (the picker's list)."""
        data = request.get_json() or {}
        level = str(data.get('level', ''))
        sd = _get_session_dir('geo')
        try:
            state = LevelState(sd, data)
            if level == 'leaf':
                cands = state.leaf_candidates(str(data.get('ref_id', '')))
            else:
                idx = next((i for i, s in enumerate(state.hier_specs)
                            if s['label'] == level), None)
                if idx is None:
                    return jsonify({'error': f'Unknown level: {level}'}), 400
                cands = state.hier_candidates(idx, str(data.get('ref_key', '')))
            return jsonify({'candidates': cands})
        except Exception as e:
            return jsonify({'error': f'Failed to score candidates: {e}'}), 500

    @app.route('/api/geo/level/link', methods=['POST'])
    def api_geo_level_link():
        """Link one hierarchy-level reference value to a gazetteer name.

        Saved as a crosswalk entry, applied on the next Run match. Re-pointing
        an already-linked value drops the leaf links made under it (they were
        made under a parent that no longer means the same place).
        """
        data = request.get_json() or {}
        level = str(data.get('level', ''))
        ref_key = str(data.get('ref_key', ''))
        target = str(data.get('target', ''))
        if not level or level == 'leaf' or not ref_key or not target:
            return jsonify({'error': 'level (non-leaf), ref_key and target '
                                     'are required'}), 400
        sd = _get_session_dir('geo')
        try:
            state = LevelState(sd, data)
            dropped = state.link_hier(
                level, ref_key,
                str(data.get('target_std', '') or target),
                target_raw=target)
            if dropped:
                _write_geocoded_csv(sd, data)
            return jsonify({'ok': True, 'dropped_children': dropped})
        except Exception as e:
            return jsonify({'error': f'Failed to link: {e}'}), 500

    @app.route('/api/geo/level/unlink', methods=['POST'])
    def api_geo_level_unlink():
        """Remove a hierarchy-level link and drop leaf links made under it."""
        data = request.get_json() or {}
        level = str(data.get('level', ''))
        ref_key = str(data.get('ref_key', ''))
        if not level or level == 'leaf' or not ref_key:
            return jsonify({'error': 'level (non-leaf) and ref_key are required'}), 400
        sd = _get_session_dir('geo')
        try:
            state = LevelState(sd, data)
            dropped = state.unlink_hier(level, ref_key)
            if dropped:
                _write_geocoded_csv(sd, data)
            return jsonify({'ok': True, 'dropped_children': dropped})
        except Exception as e:
            return jsonify({'error': f'Failed to unlink: {e}'}), 500

    @app.route('/api/geo/level/rerun', methods=['POST'])
    def api_geo_level_rerun():
        """Re-score one level: auto-link unresolved rows at/above threshold.

        Non-destructive — rows already linked (by hand or by an earlier pass)
        are never touched.
        """
        data = request.get_json() or {}
        level = str(data.get('level', ''))
        try:
            threshold = int(data.get('threshold', 70))
        except (TypeError, ValueError):
            return jsonify({'error': 'threshold must be an integer'}), 400
        sd = _get_session_dir('geo')
        try:
            state = LevelState(sd, data)
            if level == 'leaf':
                result = state.rerun_leaf(threshold)
                _write_geocoded_csv(sd, data)
            else:
                result = state.rerun_hier(level, threshold)
            return jsonify({'ok': True, **result})
        except Exception as e:
            return jsonify({'error': f'Failed to rerun {level}: {e}'}), 500

    # ------------------------------------------------------------------
    # Gazetteer (Geocoding tab): build a scoped points pool from
    # GeoNames + OSM downloads, blended with any user-uploaded points.
    # ------------------------------------------------------------------

    def _gaz_error(e):
        if isinstance(e, gz.GazetteerError):
            return jsonify({'error': str(e)}), 400
        return jsonify({'error': f'Network error reaching gazetteer source: {e}. '
                                 'Check your connection — cached data still works.'}), 502

    @app.route('/api/geo/gazetteer/countries')
    def api_gaz_countries():
        return jsonify({'countries': gz.list_countries()})

    @app.route('/api/geo/gazetteer/admin1', methods=['POST'])
    def api_gaz_admin1():
        data = request.get_json() or {}
        iso3 = str(data.get('iso3', '')).upper()
        if not iso3:
            return jsonify({'error': 'iso3 is required'}), 400
        cached = (gz.gazetteer_dir() / 'data' / 'boundaries' / f'{iso3}_ADM1.geojson').exists()
        try:
            names = gz.admin1_names(iso3)
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return _gaz_error(e)
        return jsonify({'admin1': names, 'cached': cached})

    @app.route('/api/geo/gazetteer/preview', methods=['POST'])
    def api_gaz_preview():
        data = request.get_json() or {}
        iso3 = str(data.get('iso3', '')).upper()
        admin1 = data.get('admin1', [])
        if not iso3:
            return jsonify({'error': 'iso3 is required'}), 400
        try:
            counts = gz.count_geonames_in_selection(iso3, admin1)
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return _gaz_error(e)
        warning = None
        if counts['geonames_count'] > GEO_OOM_POINT_LIMIT:
            warning = (f'Large selection ({counts["geonames_count"]:,} GeoNames '
                       'places before OSM) — matching will require a hierarchy '
                       'level, and the map may be slow.')
        return jsonify({**counts, 'warning': warning})

    @app.route('/api/geo/gazetteer/fetch-osm', methods=['POST'])
    def api_gaz_fetch_osm():
        data = request.get_json() or {}
        iso3 = str(data.get('iso3', '')).upper()
        admin_name = data.get('admin1', '')
        if not iso3 or not admin_name:
            return jsonify({'error': 'iso3 and admin1 are required'}), 400
        try:
            shapes = gz.admin1_shapes(iso3, [admin_name])
            geom = shapes[admin_name]
            cache = gz.overpass_cache_path(iso3, admin_name, geom)
            cached = cache.exists()
            path = gz.fetch_overpass_admin(iso3, admin_name, geom)
            count = len(gz.parse_overpass(path, iso3))
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return _gaz_error(e)
        return jsonify({'count': count, 'cached': cached})

    @app.route('/api/geo/gazetteer/build', methods=['POST'])
    def api_gaz_build():
        data = request.get_json() or {}
        iso3 = str(data.get('iso3', '')).upper()
        admin1 = data.get('admin1', [])
        skip_osm = data.get('skip_osm_admins', [])
        if not iso3:
            return jsonify({'error': 'iso3 is required'}), 400

        sd = _get_session_dir('geo')
        log_lines = []
        try:
            gaz_df = gz.assemble_gazetteer(iso3, admin1, skip_osm_admins=skip_osm,
                                           log=log_lines.append)
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return _gaz_error(e)
        gaz_df.to_csv(sd / 'gazetteer_points.csv', index=False)

        # First build with an existing plain upload: preserve it as the user
        # points file and remember its column mapping.
        state = _read_gaz_state(sd)
        user_columns = (state or {}).get('user_columns')
        if state is None and (sd / 'target.csv').exists():
            (sd / 'target.csv').rename(sd / 'user_points.csv')
        if (sd / 'user_points.csv').exists() and user_columns is None:
            user_columns = {
                'id': data.get('target_id_column', ''),
                'name': data.get('target_name_column', ''),
                'lat': data.get('geo_lat_column', ''),
                'lon': data.get('geo_lon_column', ''),
                'hierarchy': [[h.get('column', ''), h.get('label', '')]
                              for h in data.get('target_hierarchy', [])],
            }

        user_df = None
        if (sd / 'user_points.csv').exists() and user_columns and user_columns.get('id'):
            raw = _read_csv_str(sd / 'user_points.csv')
            if not raw.empty and user_columns['id'] in raw.columns:
                user_df = gz.normalize_user_points(
                    raw, user_columns['id'], user_columns.get('name', ''),
                    user_columns.get('lat', ''), user_columns.get('lon', ''),
                    hier_cols=[tuple(h) for h in user_columns.get('hierarchy', [])])

        combined, stats = gz.blend_points(user_df, gaz_df)
        combined.to_csv(sd / 'target.csv', index=False)
        session['geo_target_ext'] = '.csv'
        _write_gaz_state(sd, {
            'iso3': iso3,
            'admin1': admin1,
            'skip_osm_admins': skip_osm,
            'user_columns': user_columns,
            'combined': user_df is not None,
            'total': int(len(combined)),
        })
        _clear_match_outputs(sd)

        log_lines.append(
            f'Points pool: {stats["geonames"]} GeoNames + {stats["osm"]} OSM'
            + (f' + {stats["user"]} user' if stats['user'] else '')
            + (f' − {stats["dropped_duplicates"]} duplicates'
               if stats['dropped_duplicates'] else '')
            + f' = {len(combined)} points')
        admin_vals = sorted(v for v in combined['admin1'].unique() if v)
        if admin_vals:
            log_lines.append(
                'Target admin1 values: ' + ', '.join(admin_vals)
                + ' — your reference hierarchy column must use the same names '
                  'for grouped matching.')

        ref_hier = data.get('ref_hierarchy', [])
        suggested_label = ref_hier[0]['label'] if ref_hier else 'admin1'
        oom_warning = None
        if len(combined) > GEO_OOM_POINT_LIMIT:
            oom_warning = (
                f'{len(combined):,} points — Run Match will require a '
                'hierarchy level (grouped matching) at this size.')

        return jsonify({
            'log': log_lines,
            'counts': {**stats, 'total': int(len(combined))},
            'columns': gz.CANONICAL_COLUMNS,
            'preset': {
                'target_id_column': 'point_id',
                'target_name_column': 'name',
                'geo_lat_column': 'latitude',
                'geo_lon_column': 'longitude',
                'suggested_hierarchy': [
                    {'column': 'admin1', 'label': suggested_label}],
            },
            'oom_warning': oom_warning,
        })

    @app.route('/api/geo/gazetteer/clear', methods=['POST'])
    def api_gaz_clear():
        sd = _get_session_dir('geo')
        for name in ('gazetteer_points.csv', 'gaz_state.json', 'target.csv'):
            p = sd / name
            if p.exists():
                p.unlink()
        restored_columns = None
        if (sd / 'user_points.csv').exists():
            (sd / 'user_points.csv').rename(sd / 'target.csv')
            try:
                restored_columns = get_columns(str(sd / 'target.csv'))
            except Exception:
                restored_columns = None
        _clear_match_outputs(sd)
        if restored_columns is not None:
            return jsonify({'restored': True, 'columns': restored_columns})
        return jsonify({'restored': False, 'empty': True})

    @app.route('/api/chat', methods=['POST'])
    def api_chat():
        """Send a message to Claude via the Claude Code CLI."""
        data = request.get_json()
        if not data or not data.get('message', '').strip():
            return jsonify({'error': 'Empty message'}), 400

        message = data['message'].strip()

        # Check that the claude CLI is available
        if not shutil.which('claude'):
            return jsonify({
                'error': 'Claude Code CLI not found. '
                         'Install from https://claude.ai/code'
            }), 500

        # Build context from current match state and unmatched records
        level = data.get('level', 'leaf')
        context_parts = []
        sd = _get_session_dir(_request_mode())
        matched_path = sd / 'output' / 'matched.csv'
        unmatched_ref_path = sd / 'output' / 'unmatched_ref.csv'
        unmatched_target_path = sd / 'output' / 'unmatched_target.csv'

        matched_df = pd.read_csv(matched_path).fillna('') if matched_path.exists() else pd.DataFrame()
        unmatched_ref_df = pd.read_csv(unmatched_ref_path).fillna('') if unmatched_ref_path.exists() else pd.DataFrame()
        unmatched_target_df = pd.read_csv(unmatched_target_path).fillna('') if unmatched_target_path.exists() else pd.DataFrame()

        if not matched_df.empty:
            context_parts.append(
                f'Current match state: {len(matched_df)} matched, '
                f'{len(unmatched_ref_df)} unmatched reference, '
                f'{len(unmatched_target_df)} unmatched target.'
            )

        # Include actual unmatched records at the current viewing level
        if level != 'leaf' and not unmatched_ref_df.empty:
            ref_col = f'_ref_{level}'
            target_col = f'_target_{level}'
            matched_ref_names = set(matched_df[ref_col].astype(str).unique()) if ref_col in matched_df.columns else set()
            matched_target_names = set(matched_df[target_col].astype(str).unique()) if target_col in matched_df.columns else set()

            # Check lookup for additional mappings
            lookup_path = sd / 'output' / 'lookups' / f'{level}_lookup.csv'
            if lookup_path.exists():
                lk = pd.read_csv(lookup_path).fillna('')
                mapped = lk[(lk['reference_name'].astype(str).str.strip() != '') & (lk['target_name_standardized'].astype(str).str.strip() != '')]
                matched_target_names.update(mapped['target_name_standardized'].astype(str).unique())
                matched_ref_names.update(mapped['reference_name'].astype(str).unique())

            uref_names = sorted(set(unmatched_ref_df[ref_col].dropna().astype(str).unique()) - matched_ref_names) if ref_col in unmatched_ref_df.columns else []
            utgt_names = sorted(set(unmatched_target_df[target_col].dropna().astype(str).unique()) - matched_target_names) if target_col in unmatched_target_df.columns else []

            context_parts.append(f'The user is viewing the {level} hierarchy level.')
            if uref_names:
                context_parts.append(f'Unmatched reference {level} names: {", ".join(uref_names)}')
            if utgt_names:
                context_parts.append(f'Unmatched target {level} names: {", ".join(utgt_names)}')
        elif level == 'leaf' and not unmatched_ref_df.empty:
            # Include a sample of leaf-level unmatched records
            ref_names = unmatched_ref_df['_ref_name_raw'].dropna().unique()[:30].tolist() if '_ref_name_raw' in unmatched_ref_df.columns else []
            tgt_names = unmatched_target_df['_target_name_raw'].dropna().unique()[:30].tolist() if '_target_name_raw' in unmatched_target_df.columns else []
            context_parts.append('The user is viewing the leaf (name) level.')
            if ref_names:
                context_parts.append(f'Unmatched reference names: {", ".join(str(n) for n in ref_names)}')
            if tgt_names:
                context_parts.append(f'Unmatched target names: {", ".join(str(n) for n in tgt_names)}')

        preamble = (
            'You are an assistant embedded in the Match-Bot GUI, a tool for '
            'reconciling place-name datasets. Keep answers concise — the user '
            'sees them in a small chat window. You give advice only; you '
            'cannot execute actions. The user controls the GUI with buttons '
            '(Run Match, Pick New Match, level dropdown, etc.). '
            'IMPORTANT: Only reference data provided below — never invent or '
            'guess record names. When suggesting a match based on external '
            'knowledge (e.g. a place being renamed, merged, or reclassified), '
            'use web search to find a supporting source and include the URL. '
            'Do not claim a renaming or administrative change without '
            'providing a link.'
        )
        if context_parts:
            preamble += '\n\n' + '\n'.join(context_parts)

        prompt = f'{preamble}\n\nUser: {message}'

        try:
            result = subprocess.run(
                ['claude', '-p', prompt,
                 '--allowedTools', 'WebSearch', 'WebFetch'],
                capture_output=True, text=True, timeout=120,
                cwd=str(Path(__file__).resolve().parent.parent.parent),
            )
            response = result.stdout.strip()
            if not response and result.stderr:
                return jsonify({'error': result.stderr.strip()}), 500
            return jsonify({'response': response or '(no response)'})
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Claude took too long to respond (120s timeout).'}), 504
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    return app


def launch_gui():
    """Launch the Match-Bot web GUI."""
    app = create_app()
    port = int(os.environ.get('MATCH_BOT_PORT', 5000))
    url = f'http://localhost:{port}'
    print(f'Starting Match-Bot GUI at {url}')
    webbrowser.open(url)
    app.run(host='127.0.0.1', port=port, debug=False)


if __name__ == '__main__':
    launch_gui()
