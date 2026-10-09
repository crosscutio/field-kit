"""Flask application for the Community Geocoder GUI.

One page, five stages (Set up → Admin names → Auto match → Link on map →
Review & export). Every request is scoped to a per-browser-session project
directory; all matching work is delegated to ``match_bot.core``.
"""

import json
import os
import shutil
import urllib.error
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory, session

from match_bot import gazetteer as gz
from match_bot import icr_fhir, icr_sdi
from match_bot.core import links, suggest as sg
from match_bot.gui.project import ROW_ID, Project

ADM_LEVELS = ['ADM1', 'ADM2', 'ADM3', 'ADM4']


def create_app():
    app = Flask(__name__)
    # A stable secret key lets a browser session survive a server restart.
    key_path = Path(app.instance_path) / 'secret_key'
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        key_path.write_bytes(os.urandom(32))
    app.secret_key = key_path.read_bytes()
    port = os.environ.get('MATCH_BOT_PORT', '5000')
    app.config['SESSION_COOKIE_NAME'] = f'match_bot_session_{port}'
    upload_base = Path(app.instance_path) / 'uploads'
    upload_base.mkdir(parents=True, exist_ok=True)
    app.config['UPLOAD_BASE'] = upload_base

    def project() -> Project:
        if 'sid' not in session:
            session['sid'] = str(uuid.uuid4())
        return Project(Path(app.config['UPLOAD_BASE']) / session['sid'])

    def body():
        return request.get_json(silent=True) or {}

    def fail(msg, code=400):
        return jsonify({'ok': False, 'error': msg}), code

    def gaz_error(e):
        if isinstance(e, gz.GazetteerError):
            return fail(str(e))
        return fail(f'Network error reaching gazetteer source: {e}. Cached data still works.', 502)

    # ---- page ------------------------------------------------------------
    @app.route('/')
    def index():
        # version the static assets by mtime so a browser never runs a stale script
        static = Path(app.static_folder)
        v = int(max(p.stat().st_mtime for p in static.glob('geocoder.*')))
        return render_template('geocoder.html', asset_v=v)

    @app.after_request
    def _no_stale_assets(resp):
        if request.path.startswith('/static/'):
            resp.headers['Cache-Control'] = 'no-cache'
        return resp

    # ---- project state ---------------------------------------------------
    @app.route('/api/state', methods=['GET', 'POST'])
    def api_state():
        p = project()
        if request.method == 'POST':
            data = body()
            allowed = {'project_name', 'target_name_column', 'target_hierarchy', 'ref_name_column',
                       'ref_id_column', 'ref_lat_column', 'ref_lon_column', 'ref_hierarchy',
                       'boundaries_source', 'country', 'threshold', 'restrict', 'ui', 'ref_source',
                       'target_source'}
            upd = {k: v for k, v in data.items() if k in allowed}
            if 'country' in upd or 'boundaries_source' in upd:
                upd['boundary_levels'] = {}
                upd['ref_tagged'] = None
            if 'target_hierarchy' in upd:
                old = [h.get('label') for h in p.form().get('target_hierarchy', [])]
                new_labels = [h.get('label') for h in upd['target_hierarchy']]
                if old != new_labels:
                    upd['ref_tagged'] = None
                    upd['ref_hierarchy'] = []
            if 'ref_lat_column' in upd or 'ref_lon_column' in upd:
                upd['ref_tagged'] = None
            if upd:
                p.update_form(**upd)
        return jsonify({'ok': True, **p.state()})

    @app.route('/api/reset', methods=['POST'])
    def api_reset():
        p = project()
        shutil.rmtree(p.sd, ignore_errors=True)
        return jsonify({'ok': True, **Project(p.sd).state()})

    # ---- uploads ---------------------------------------------------------
    @app.route('/api/upload', methods=['POST'])
    def api_upload():
        if 'file' not in request.files or not request.files['file'].filename:
            return fail('No file uploaded')
        f = request.files['file']
        role = request.form.get('role', 'target')
        p = project()
        ext = Path(f.filename).suffix.lower()
        if role in ('target', 'ref'):
            if ext != '.csv':
                return fail(f'Expected a .csv file, got "{ext}"')
            try:
                info = p.save_table(role, f.stream, f.filename)
            except Exception as e:
                return fail(f'Could not read CSV: {e}')
            return jsonify({'ok': True, 'info': info, **p.state()})
        if role == 'boundaries':
            label = request.form.get('label', '')
            if not label or ext not in ('.geojson', '.json'):
                return fail('Boundary uploads need a level label and a .geojson file')
            try:
                gj = json.load(f.stream)
                feats = gj.get('features', [])
                props = sorted({k for ft in feats for k in (ft.get('properties') or {})})
            except Exception as e:
                return fail(f'Could not parse GeoJSON: {e}')
            p.boundary_path(label).write_text(json.dumps(gj), encoding='utf-8')
            files = p.form().get('boundary_files', {})
            files[label] = {'filename': f.filename, 'name_property': request.form.get('name_property', ''),
                            'properties': props, 'features': len(feats)}
            p.update_form(boundary_files=files, boundaries_source='upload', ref_tagged=None)
            return jsonify({'ok': True, 'properties': props, 'features': len(feats), **p.state()})
        return fail(f'Unknown role {role!r}')

    @app.route('/api/boundary-property', methods=['POST'])
    def api_boundary_property():
        p, d = project(), body()
        files = p.form().get('boundary_files', {})
        label = d.get('label', '')
        if label in files:
            files[label]['name_property'] = d.get('name_property', '')
            p.update_form(boundary_files=files, ref_tagged=None)
        return jsonify({'ok': True, **p.state()})

    @app.route('/api/boundaries/<label>')
    def api_boundaries(label):
        """Admin polygons for one hierarchy level; each feature carries
        ``_ref`` (its standardized name, which is what tagged places use)."""
        p = project()
        try:
            b = p.boundary_geojson(label)
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return gaz_error(e)
        if not b:
            return fail('No boundaries available', 404)
        st = p.annotate_boundaries(b['geojson'], b['name_property'], label)
        return jsonify({'ok': True, **b, **st})

    @app.route('/api/boundary-levels', methods=['POST'])
    def api_boundary_levels():
        """Set which geoBoundaries ADM level one hierarchy level maps to."""
        p, d = project(), body()
        levels = dict(p.form().get('boundary_levels') or {})
        label, adm = d.get('label', ''), str(d.get('adm', '')).upper()
        if label not in p.levels()[:-1] or adm not in ADM_LEVELS:
            return fail('A level label and an ADM level (ADM1 to ADM4) are required')
        levels[label] = adm
        p.update_form(boundary_levels=levels, ref_tagged=None)
        return jsonify({'ok': True, **p.state()})

    @app.route('/api/boundary-levels/suggest', methods=['POST'])
    def api_boundary_levels_suggest():
        p = project()
        try:
            chosen = p.suggest_boundary_levels()
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return gaz_error(e)
        return jsonify({'ok': True, 'boundary_levels': chosen, **p.state()})

    @app.route('/api/tag-places', methods=['POST'])
    def api_tag_places():
        """Assign admin units to every place from the boundaries."""
        p = project()
        try:
            res = p.tag_places()
        except ValueError as e:
            return fail(str(e))
        except ImportError as e:
            return fail(str(e), 500)
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return gaz_error(e)
        p.history.add('tagged ' + str(res['rows']) + ' places with ' + ', '.join(
            f"{k} ({v['adm']}: {v['inside'] + v['snapped']} in, {v['outside']} out)" for k, v in res['stats'].items()))
        return jsonify({'ok': True, 'tagging': res, **p.state()})

    # ---- pipeline --------------------------------------------------------
    @app.route('/api/lookups', methods=['POST'])
    def api_lookups():
        p = project()
        r = p.ready()
        if not (r['target'] and r['ref']):
            return fail('Upload and map both files first')
        if not r['hierarchy_paired']:
            return fail('Admin levels must be mapped on both files')
        try:
            log = p.run_lookups()
        except Exception as e:
            return fail(f'Matching failed: {e}', 500)
        p.history.add('ran lookups · ' + ', '.join(
            f"{k} {v['linked']}/{v['total']}" for k, v in p.level_stats().items()))
        return jsonify({'ok': True, 'log': log, **p.state()})

    @app.route('/api/run', methods=['POST'])
    def api_run():
        p = project()
        d = body()
        f = p.form()
        threshold = int(d.get('threshold', f.get('threshold', 85)))
        restrict = bool(d.get('restrict', f.get('restrict', True)))
        r = p.ready()
        if not (r['target'] and r['ref']):
            return fail('Upload and map both files first')
        try:
            res = p.run_auto(threshold, restrict)
        except Exception as e:
            return fail(f'Matching failed: {e}', 500)
        st = p.state()
        leaf = st['stats'].get('leaf', {})
        p.history.add(f"ran matching · {leaf.get('linked', 0)} of {st['total']} auto-accepted at {threshold}")
        hist = p.histogram(restrict)
        return jsonify({'ok': True, 'applied': res['applied'], 'histogram': hist['bins'],
                        'log': res['log'], **p.state()})

    @app.route('/api/histogram')
    def api_histogram():
        p = project()
        if not p.has_lookups():
            return jsonify({'ok': True, 'bins': [], 'scores': {}})
        restrict = request.args.get('restrict', '1') != '0'
        return jsonify({'ok': True, **p.histogram(restrict)})

    # ---- admin names stage -----------------------------------------------
    @app.route('/api/level/<label>')
    def api_level(label):
        p = project()
        if label not in p.levels()[:-1]:
            return fail('Unknown level', 404)
        if not p.has_lookups():
            return fail('Run lookups first')
        return jsonify({'ok': True, **p.level_rows(label)})

    @app.route('/api/candidates')
    def api_candidates():
        p = project()
        level = request.args.get('level', 'leaf')
        key = request.args.get('key', '')
        restrict = request.args.get('restrict', '1') != '0'
        top = int(request.args.get('top', 3))
        try:
            cands = p.candidates(level, key, restrict=restrict, top=top)
        except Exception as e:
            return fail(str(e), 500)
        return jsonify({'ok': True, 'candidates': cands})

    def journal(p, text, inverse):
        p.history.add(text, inverse)

    @app.route('/api/link', methods=['POST'])
    def api_link():
        p, d = project(), body()
        level, tk, rk = d.get('level', 'leaf'), str(d.get('target_key', '')), str(d.get('ref_key', ''))
        score = d.get('score')
        has_score = score not in (None, '')
        rationale = f'manual: picked score={float(score):.0f}' if has_score else 'manual: linked'
        res = p.apply_op('link', {'level': level, 'target_key': tk, 'ref_key': rk, 'rationale': rationale})
        if not res.get('ok'):
            return fail(res.get('error', 'Link failed'))
        if level == 'leaf':
            p.clear_pin(tk)
        prev = res.get('previous')
        if prev:
            inverse = {'op': 'link', 'args': {
                'level': level, 'target_key': tk,
                'ref_key': prev.get('ref_id') or prev.get('ref_key'),
                'rationale': prev.get('rationale') or 'manual: linked'}}
        else:
            inverse = {'op': 'unlink', 'args': {'level': level, 'target_key': tk}}
        label = d.get('target_name', tk)
        text = f"{label} → {res.get('ref_name', rk)}"
        if has_score:
            text += f' · {float(score):.0f}'
        journal(p, text + ' · manual', inverse)
        return jsonify({'ok': True, **p.state()})

    @app.route('/api/unlink', methods=['POST'])
    def api_unlink():
        p, d = project(), body()
        level, tk = d.get('level', 'leaf'), str(d.get('target_key', ''))
        res = p.apply_op('unlink', {'level': level, 'target_key': tk})
        if not res.get('ok'):
            return fail(res.get('error', 'Unlink failed'))
        inverse = {'op': 'link', 'args': {'level': level, 'target_key': tk, 'ref_key': res['ref_key'],
                                           'rationale': res.get('rationale') or 'manual: linked'}}
        journal(p, f"unlinked {d.get('target_name', tk)}", inverse)
        return jsonify({'ok': True, **p.state()})

    @app.route('/api/no-equivalent', methods=['POST'])
    def api_no_equivalent():
        p, d = project(), body()
        level, tk = d.get('level', 'leaf'), str(d.get('target_key', ''))
        res = p.apply_op('no_equivalent', {'level': level, 'target_key': tk})
        if not res.get('ok'):
            return fail(res.get('error', 'The change failed'))
        steps = [{'op': 'clear_no_equivalent', 'args': {'level': level, 'target_key': tk}}]
        prev = res.get('previous')
        if prev and (prev.get('ref_id') or prev.get('ref_key')):
            steps.append({'op': 'link', 'args': {
                'level': level, 'target_key': tk,
                'ref_key': prev.get('ref_id') or prev.get('ref_key'),
                'rationale': prev.get('rationale') or 'manual: linked'}})
        journal(p, f"{d.get('target_name', tk)} → no equivalent", {'op': 'multi', 'args': {'steps': steps}})
        return jsonify({'ok': True, **p.state()})

    @app.route('/api/accept-above', methods=['POST'])
    def api_accept_above():
        p, d = project(), body()
        level = d.get('level', 'leaf')
        threshold = int(d.get('threshold', 90))
        restrict = bool(d.get('restrict', True))
        cfg = p.config()
        sugg = sg.suggest(cfg, level, threshold=threshold, scope_depth=None if restrict else 0)
        res = sg.apply(cfg, level, sugg, rationale_prefix='auto: suggest') if sugg else \
            {'applied': 0, 'keys': []}
        if res['applied']:
            journal(p, f"bulk accepted {res['applied']} {level} suggestions above {threshold}",
                    {'op': 'unlink_many', 'args': {'level': level, 'keys': res['keys']}})
        return jsonify({'ok': True, 'applied': res['applied'], **p.state()})

    # ---- link on map stage -----------------------------------------------
    @app.route('/api/unlinked')
    def api_unlinked():
        p = project()
        if not p.has_lookups():
            return jsonify({'ok': True, 'rows': []})
        return jsonify({'ok': True, 'rows': p.unlinked(include_linked=request.args.get('all') == '1')})

    @app.route('/api/places')
    def api_places():
        p = project()
        if 'target' in request.args:
            pts = p.places(target_id=request.args['target'])
        elif 'level' in request.args:
            pts = p.places(scope_label=request.args['level'], values=request.args.getlist('value'))
        else:
            pts = p.places()
        return jsonify({'ok': True, 'points': pts})

    @app.route('/api/pin', methods=['POST'])
    def api_pin():
        p, d = project(), body()
        tid = str(d.get('target_id', ''))
        try:
            lat, lon = float(d['lat']), float(d['lon'])
        except (KeyError, ValueError, TypeError):
            return fail('Latitude and longitude are required')
        cfg = p.config()
        steps = []
        row = links.unlink(cfg, 'leaf', tid)
        if row.get('ok'):
            steps.append({'op': 'link', 'args': {
                'level': 'leaf', 'target_key': tid, 'ref_key': row['ref_key'],
                'rationale': row.get('rationale') or 'manual: linked'}})
        prev = p.set_pin(tid, lat, lon)
        if prev:
            steps.insert(0, {'op': 'pin', 'args': {'target_id': tid, **prev}})
        else:
            steps.insert(0, {'op': 'unpin', 'args': {'target_id': tid}})
        journal(p, f"{d.get('target_name', tid)} → dropped pin · {lat:.4f}, {lon:.4f} · manual · pin",
                {'op': 'multi', 'args': {'steps': steps}})
        return jsonify({'ok': True, **p.state()})

    @app.route('/api/unpin', methods=['POST'])
    def api_unpin():
        p, d = project(), body()
        tid = str(d.get('target_id', ''))
        prev = p.clear_pin(tid)
        if not prev:
            return fail('That community has no pin')
        journal(p, f"removed pin from {d.get('target_name', tid)}",
                {'op': 'pin', 'args': {'target_id': tid, **prev}})
        return jsonify({'ok': True, **p.state()})

    # ---- history ---------------------------------------------------------
    @app.route('/api/history')
    def api_history():
        return jsonify({'ok': True, 'entries': list(reversed(project().history.entries()))})

    @app.route('/api/undo', methods=['POST'])
    def api_undo():
        p = project()
        res = p.undo()
        if not res.get('ok'):
            return fail(res.get('error', 'Undo failed'))
        return jsonify({'ok': True, 'undone': res['undone'], **p.state()})

    # ---- review / export -------------------------------------------------
    @app.route('/api/review')
    def api_review():
        p = project()
        if not p.has_lookups():
            return jsonify({'ok': True, 'rows': []})
        try:
            rows = p.review_rows()
        except Exception as e:
            return fail(str(e), 500)
        return jsonify({'ok': True, 'rows': rows})

    @app.route('/api/download/<path:filepath>')
    def api_download(filepath):
        """Serve result files only. The session folder also holds
        form_state.json, which can carry registry credentials."""
        p = project()
        out = (p.sd / 'output').resolve()
        full = (p.sd / filepath).resolve()
        if out not in full.parents:
            return fail('Only files under output/ can be downloaded', 403)
        if not full.exists():
            return fail('File not found', 404)
        return send_from_directory(str(full.parent), full.name, as_attachment=True)

    # ---- gazetteer builder (named places from OSM + GeoNames) ------------
    @app.route('/api/gazetteer/countries')
    def api_gaz_countries():
        return jsonify({'ok': True, 'countries': gz.list_countries()})

    @app.route('/api/gazetteer/admin1', methods=['POST'])
    def api_gaz_admin1():
        iso3 = str(body().get('iso3', '')).upper()
        if not iso3:
            return fail('A country (ISO3 code) is required')
        cached = (gz.gazetteer_dir() / 'data' / 'boundaries' / f'{iso3}_ADM1.geojson').exists()
        try:
            names = gz.admin1_names(iso3)
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return gaz_error(e)
        return jsonify({'ok': True, 'admin1': names, 'cached': cached})

    @app.route('/api/gazetteer/preview', methods=['POST'])
    def api_gaz_preview():
        d = body()
        iso3 = str(d.get('iso3', '')).upper()
        if not iso3:
            return fail('A country (ISO3 code) is required')
        try:
            counts = gz.count_geonames_in_selection(iso3, d.get('admin1', []))
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return gaz_error(e)
        return jsonify({'ok': True, **counts})

    @app.route('/api/gazetteer/fetch-osm', methods=['POST'])
    def api_gaz_fetch_osm():
        d = body()
        iso3, admin_name = str(d.get('iso3', '')).upper(), d.get('admin1', '')
        if not iso3 or not admin_name:
            return fail('A country (ISO3 code) and an admin-1 area are required')
        try:
            shapes = gz.admin1_shapes(iso3, [admin_name])
            geom = shapes[admin_name]
            cache = gz.overpass_cache_path(iso3, admin_name, geom)
            cached = cache.exists()
            path = gz.fetch_overpass_admin(iso3, admin_name, geom)
            count = len(gz.parse_overpass(path, iso3))
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return gaz_error(e)
        return jsonify({'ok': True, 'count': count, 'cached': cached})

    @app.route('/api/gazetteer/build', methods=['POST'])
    def api_gaz_build():
        d = body()
        iso3 = str(d.get('iso3', '')).upper()
        admin1, skip = d.get('admin1', []), d.get('skip_osm_admins', [])
        if not iso3:
            return fail('A country (ISO3 code) is required')
        p = project()
        log = []
        try:
            gaz_df = gz.assemble_gazetteer(iso3, admin1, skip_osm_admins=skip, log=log.append)
        except (gz.GazetteerError, urllib.error.URLError, TimeoutError, OSError) as e:
            return gaz_error(e)
        gaz_df.to_csv(p.ref_path, index=False)
        p.clear_outputs()
        # Admin units come from the boundaries (tagging step), not from the
        # gazetteer's own admin1/admin2 columns.
        if p.ref_tagged_path.exists():
            p.ref_tagged_path.unlink()
        p.update_form(ref_filename=f'{iso3} gazetteer', ref_source='gazetteer',
                      ref_id_column='point_id', ref_name_column='name',
                      ref_lat_column='latitude', ref_lon_column='longitude',
                      ref_hierarchy=[], ref_tagged=None, country=iso3)
        log.append(f'{len(gaz_df)} named places written')
        return jsonify({'ok': True, 'log': log, 'count': int(len(gaz_df)), **p.state()})

    # ---- ICR SDI: site list from the registry's published GeoParquet -----
    def icr_error(e):
        if isinstance(e, icr_sdi.IcrError):
            return fail(str(e))
        return fail(f'Could not reach the registry: {e}', 502)

    def stored_conn(p, key, url_field, d=None):
        """Connection settings: the request's, falling back to the stored ones.
        An empty secret with an unchanged mode keeps the stored secret."""
        stored = dict(p.form().get(key) or {})
        d = d or {}
        mode = d.get('auth_mode', stored.get('auth_mode', 'none'))
        secret = d.get('auth_secret') or ''
        if not secret and mode == stored.get('auth_mode'):
            secret = stored.get('auth_secret', '')
        url = (d.get('url') or stored.get(url_field) or '').strip()
        return url, mode, secret

    def icr_conn(p, d=None):
        return stored_conn(p, 'icr', 'catalog_url', d)

    def icr_headers(p):
        _, mode, secret = icr_conn(p)
        return icr_sdi.auth_headers(mode, secret)

    def icr_catalog(p):
        """The catalog read at the last check (cached per session), else re-read."""
        url = icr_conn(p)[0]
        cache = p.sd / 'icr_catalog.json'
        if cache.exists():
            cat = json.loads(cache.read_text(encoding='utf-8'))
            if cat.get('_url') == url:
                return cat
        cat = icr_sdi.read_catalog(url, icr_headers(p))
        cache.write_text(json.dumps({**cat, '_url': url}), encoding='utf-8')
        return cat

    def icr_country(p, code):
        cat = icr_catalog(p)
        c = next((c for c in icr_sdi.countries(cat) if c['code'] == code), None)
        if not c:
            raise icr_sdi.IcrError(f'Country {code!r} is not in this catalog')
        return cat, c

    NET = (icr_sdi.IcrError, urllib.error.URLError, TimeoutError, OSError)

    @app.route('/api/icr/catalog', methods=['POST'])
    def api_icr_catalog():
        """Check a catalog address + sign-in; remember them on success."""
        p, d = project(), body()
        url, mode, secret = icr_conn(p, d)
        try:
            cat = icr_sdi.read_catalog(url, icr_sdi.auth_headers(mode, secret))
        except NET as e:
            return icr_error(e)
        (p.sd / 'icr_catalog.json').write_text(json.dumps({**cat, '_url': url}), encoding='utf-8')
        icr = dict(p.form().get('icr') or {})
        if icr.get('catalog_url') != url:
            icr = {}
        icr.update(catalog_url=url, auth_mode=mode, auth_secret=secret)
        p.update_form(icr=icr)
        ctry = [{k: c[k] for k in ('code', 'name', 'admin_units')} | {'types': sorted(c['types'])}
                for c in icr_sdi.countries(cat)]
        return jsonify({'ok': True, 'catalog': {k: cat[k] for k in ('title', 'providers', 'license', 'updated')},
                        'files': len(cat['items']), 'countries': ctry, **p.state()})

    @app.route('/api/icr/options', methods=['POST'])
    def api_icr_options():
        """Types published for a country (counts on record / without
        coordinates) and its admin tree; with ``type``, per-unit site counts."""
        p, d = project(), body()
        code, typ = d.get('country', ''), d.get('type')
        try:
            cat, c = icr_country(p, code)
            conn = icr_sdi.duck(icr_headers(p))
            types = {}
            for t in icr_sdi.SITE_TYPES:
                if t in c['types']:
                    types[t] = {'published': True, **icr_sdi.type_summary(conn, c['types'][t]['href'])}
                else:
                    types[t] = {'published': False}
            units = icr_sdi.admin_units(conn, c['admin_href']) if c['admin_href'] else []
            counts = icr_sdi.site_counts(conn, c['types'][typ]['href']) if typ in c['types'] else {}
        except NET as e:
            return icr_error(e)
        icr = dict(p.form().get('icr') or {})
        icr.update(country=code, **({'type': typ} if typ else {}))
        p.update_form(icr=icr)
        return jsonify({'ok': True, 'country': {'code': c['code'], 'name': c['name']}, 'types': types,
                        'tree': icr_sdi.admin_tree(units, counts), **p.state()})

    @app.route('/api/icr/import/boundaries', methods=['POST'])
    def api_icr_import_boundaries():
        """Step 1 of the import: the country's admin units become the boundaries."""
        p, d = project(), body()
        code = d.get('country', '')
        try:
            cat, c = icr_country(p, code)
            if not c['admin_href']:
                raise icr_sdi.IcrError(f'{c["name"]} has no admin units in this catalog')
            conn = icr_sdi.duck(icr_headers(p))
            units = icr_sdi.admin_units(conn, c['admin_href'])
            gj = {lvl: icr_sdi.boundaries_geojson(units, lvl) for lvl in (1, 2)}
        except NET as e:
            return icr_error(e)
        counts = p.save_icr_boundaries(gj, code)
        log = [f'Read type=admin-unit · {len(units)} admin units',
               f'Boundaries · {counts[1]} at level 1, {counts[2]} at level 2']
        return jsonify({'ok': True, 'log': log, **p.state()})

    @app.route('/api/icr/import/sites', methods=['POST'])
    def api_icr_import_sites():
        """Step 2 of the import: the site rows in scope become target.csv."""
        p, d = project(), body()
        code, typ = d.get('country', ''), d.get('type', '')
        status = 'active' if d.get('status', 'active') == 'active' else 'all'
        unit_ids = d.get('units') or None
        try:
            cat, c = icr_country(p, code)
            if typ not in c['types']:
                raise icr_sdi.IcrError(f'No {typ} file for {c["name"]} in this catalog')
            part = c['types'][typ]
            conn = icr_sdi.duck(icr_headers(p))
            rows = icr_sdi.read_sites(conn, part['href'], status, unit_ids)
        except NET as e:
            return icr_error(e)
        if not rows:
            return fail('No sites match this query')
        p.save_icr_sites(rows, f'{c["name"]} {typ}s · ICR')
        icr = dict(p.form().get('icr') or {})
        icr.update(country=code, type=typ, status=status, units=unit_ids or [],
                   snapshot={'href': part['href'], 'datetime': part['datetime'], 'checksum': part['checksum']})
        p.update_form(icr=icr)
        cov = p.icr_coverage()
        log = [f'Read country={code}/geom_type=point/type={typ} · {len(rows)} rows kept',
               f'{cov["with_coords"]} already have coordinates · {cov["missing"]} need a location',
               'Written to target.csv · source=icr']
        return jsonify({'ok': True, 'log': log, **p.state()})

    # ---- ICR FHIR: named places from campaign form responses -------------
    def fhir_conn(p, d=None):
        url, mode, secret = stored_conn(p, 'fhir', 'base_url', d)
        return url.rstrip('/'), icr_sdi.auth_headers(mode, secret), mode, secret

    def session_json(p, name, value=None):
        path = p.sd / name
        if value is not None:
            path.write_text(json.dumps(value), encoding='utf-8')
            return value
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    @app.route('/api/fhir/connect', methods=['POST'])
    def api_fhir_connect():
        p, d = project(), body()
        try:
            base, headers, mode, secret = fhir_conn(p, d)
            cap = icr_fhir.capability(base, headers)
        except NET as e:
            return icr_error(e)
        fh = dict(p.form().get('fhir') or {})
        if fh.get('base_url') != base:
            fh = {}
        fh.update(base_url=base, auth_mode=mode, auth_secret=secret)
        p.update_form(fhir=fh)
        return jsonify({'ok': True, 'server': cap, **p.state()})

    @app.route('/api/fhir/forms', methods=['POST'])
    def api_fhir_forms():
        """Every Questionnaire on the server, its questions and response count."""
        p = project()
        try:
            base, headers, _, _ = fhir_conn(p)
            forms = icr_fhir.questionnaires(base, headers)
        except NET as e:
            return icr_error(e)
        session_json(p, 'fhir_forms.json', {'base': base, 'forms': forms})
        return jsonify({'ok': True, 'forms': forms})

    @app.route('/api/fhir/scan', methods=['POST'])
    def api_fhir_scan():
        """Subjects, campaigns and statuses of the chosen forms' responses."""
        p, d = project(), body()
        forms = sorted(d.get('forms') or [])
        if not forms:
            return fail('Pick at least one questionnaire')
        cached = session_json(p, 'fhir_scan.json')
        base = fhir_conn(p)[0]
        if cached and cached.get('base') == base and cached.get('forms') == forms:
            return jsonify({'ok': True, **cached['scan']})
        try:
            res = icr_fhir.scan(base, fhir_conn(p)[1], forms)
        except NET as e:
            return icr_error(e)
        session_json(p, 'fhir_scan.json', {'base': base, 'forms': forms, 'scan': res})
        return jsonify({'ok': True, **res})

    @app.route('/api/fhir/pull', methods=['POST'])
    def api_fhir_pull():
        """Pull the selected responses and write them as the named places."""
        p, d = project(), body()
        sel = {k: d.get(k) or [] for k in ('forms', 'subjects', 'campaigns', 'statuses')}
        mapping = {u: m for u, m in (d.get('mapping') or {}).items()
                   if u in sel['forms'] and all((m or {}).get(k) for k in ('name', 'lat', 'lon'))}
        if set(mapping) != set(sel['forms']):
            return fail('Choose the name, latitude and longitude answers for every questionnaire, or remove it')
        cache = session_json(p, 'fhir_forms.json') or {}
        titles = {f['url']: f['title'] for f in cache.get('forms', [])}
        try:
            base, headers, _, _ = fhir_conn(p)
            res = icr_fhir.pull(base, headers, sel, mapping, titles)
        except NET as e:
            return icr_error(e)
        if not res['places']:
            return fail(f"None of the {res['read']} responses had a name and a GPS point in the chosen answers")
        import pandas as pd
        df = pd.DataFrame(res['places'])
        p._write_table('ref', df, f"{len(sel['forms'])} ICR form{'s' if len(sel['forms']) != 1 else ''}")
        p.update_form(ref_source='icr-forms', ref_id_column='point_id', ref_name_column='name',
                      ref_lat_column='latitude', ref_lon_column='longitude', ref_hierarchy=[], ref_tagged=None)
        fh = dict(p.form().get('fhir') or {})
        fh.update(sel, mapping=mapping)
        p.update_form(fhir=fh)
        log = [f"Read {res['read']} responses", f"Skipped {res['skipped']} with no name or GPS answer",
               f"Pooled {res['pooled']} repeat visits → {len(res['places'])} named places",
               'Written to ref.csv · source=icr-forms']
        return jsonify({'ok': True, 'log': log, 'read': res['read'], 'skipped': res['skipped'],
                        'pooled': res['pooled'], 'count': len(res['places']),
                        'preview': res['places'][:12], **p.state()})

    @app.route('/api/icr/records', methods=['POST'])
    def api_icr_records():
        """Choose which imported records become the site list."""
        p = project()
        try:
            p.choose_icr_records(body().get('keep', ''))
        except ValueError as e:
            return fail(str(e))
        return jsonify({'ok': True, **p.state()})

    @app.route('/api/icr/preview')
    def api_icr_preview():
        """Imported registry rows for the preview, filtered by coordinate presence."""
        p = project()
        flt = request.args.get('filter', 'all')
        limit = min(int(request.args.get('limit', 40)), 500)
        import pandas as pd
        df = pd.read_csv(p.target_path, dtype=str).fillna('') if p.target_path.exists() else pd.DataFrame()
        if df.empty or 'icr_latitude' not in df.columns:
            return jsonify({'ok': True, 'rows': [], 'matching': 0})
        has = (df['icr_latitude'] != '') & (df['icr_longitude'] != '')
        sel = df[has] if flt == 'coords' else df[~has] if flt == 'none' else df
        cols = [c for c in p.ICR_TARGET_COLUMNS if c in sel.columns]
        return jsonify({'ok': True, 'matching': int(len(sel)),
                        'rows': sel[cols].head(limit).to_dict(orient='records')})

    return app


def launch_gui():
    """Launch the Community Geocoder web GUI."""
    app = create_app()
    port = int(os.environ.get('MATCH_BOT_PORT', 5000))
    url = f'http://localhost:{port}'
    print(f'Starting Match-Bot GUI at {url}')
    webbrowser.open(url)
    app.run(host='127.0.0.1', port=port, debug=False)


if __name__ == '__main__':
    launch_gui()
