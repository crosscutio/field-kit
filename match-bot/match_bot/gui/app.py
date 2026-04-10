"""Flask web application for Match-Bot GUI."""

import io
import os
import sys
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

from match_bot.core.data_loader import get_columns
from match_bot.gui.builder import (
    build_config,
    form_data_to_yaml,
    yaml_to_form_data,
)


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


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = os.urandom(24)

    upload_base = Path(app.instance_path) / 'uploads'
    upload_base.mkdir(parents=True, exist_ok=True)
    app.config['UPLOAD_BASE'] = upload_base

    def _get_session_dir():
        """Get or create the upload directory for the current session."""
        if 'sid' not in session:
            session['sid'] = str(uuid.uuid4())
        d = upload_base / session['sid']
        d.mkdir(parents=True, exist_ok=True)
        return d

    @app.route('/')
    def index():
        # Clear stale outputs from any previous session so manual hierarchy
        # mappings don't bleed into a fresh workflow.
        if 'sid' in session:
            sd = upload_base / session['sid']
            lookups_dir = sd / 'output' / 'lookups'
            if lookups_dir.exists():
                for f in lookups_dir.iterdir():
                    if f.is_file():
                        f.unlink()
        return render_template('index.html')

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

        # Validate allowed extensions per prefix
        allowed = {'.csv', '.geojson'} if prefix == 'target' else {'.csv'}
        if ext not in allowed:
            return jsonify({'error': f'Unsupported file type "{ext}" for {prefix}. Allowed: {", ".join(sorted(allowed))}'}), 400

        # Save with a predictable name, preserving original extension
        filename = f'{prefix}{ext}'
        save_path = _get_session_dir() / filename
        file.save(str(save_path))
        session[f'{prefix}_ext'] = ext

        try:
            cols = get_columns(str(save_path))
        except Exception as e:
            return jsonify({'error': f'Failed to read file: {e}'}), 400

        return jsonify({
            'columns': cols,
            'filename': file.filename,
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
        target_ext = session.get('target_ext', '.csv')
        data['ref_file'] = 'ref.csv'
        data['target_file'] = f'target{target_ext}'
        data['lookups_dir'] = 'output/lookups'
        data['output_dir'] = 'output'

        # Verify uploaded files exist
        sd = _get_session_dir()
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
        if action == 'match':
            target_ext = session.get('target_ext', '.csv')
            if target_ext == '.geojson':
                try:
                    _predissolve_geojson(sd, data)
                except Exception:
                    pass  # Map will fall back to raw GeoJSON

        output_text = buffer.getvalue()
        return jsonify({
            'output': output_text,
            'downloads': downloads,
        })

    @app.route('/api/download/<path:filepath>')
    def api_download(filepath):
        """Serve an output file for download."""
        full_path = _get_session_dir() / filepath
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

        return jsonify({'form_data': form_data})

    @app.route('/api/save-config', methods=['POST'])
    def api_save_config():
        """Generate a YAML config file from form data and return it for download."""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No form data provided'}), 400

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
        sd = _get_session_dir()
        geojson_path = sd / 'target.geojson'
        if not geojson_path.exists():
            return jsonify({'error': 'No GeoJSON uploaded'}), 404
        return send_from_directory(str(sd), 'target.geojson', mimetype='application/geo+json')

    @app.route('/api/dissolved/<level>')
    def api_dissolved(level):
        """Serve a pre-dissolved GeoJSON layer for the given level."""
        sd = _get_session_dir()
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

        sd = _get_session_dir()
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

        sd = _get_session_dir()
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
        sd = _get_session_dir()
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
        sd = _get_session_dir()

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
        sd = _get_session_dir()

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
