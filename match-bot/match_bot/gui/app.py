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
        """Return unmatched reference and target records as JSON."""
        sd = _get_session_dir()
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
