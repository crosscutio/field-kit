/* Community Geocoder — five-stage front end over the match-bot API. */
(function () {
  'use strict';

  const STAGES = ['Set up', 'Admin names', 'Auto match', 'Link on map', 'Review & export'];
  const SUBSTEPS = ['Your community list', 'Named places', 'Matching rules'];

  const S = {
    stage: 1, sub: 1,
    st: null,                 // last server state
    level: null,              // admin stage: current level label
    lvl: null,                // admin stage: level rows payload
    adminSel: null, adminCands: [], adminCandSel: null, adminQuery: '', adminCandQuery: '',
    hist: null, runLog: '',
    comm: { rows: [], all: false, sel: null, cands: [], candSel: 0, pin: null, query: '', places: [], open: new Set(), revealed: null },
    review: { rows: [], query: '', filter: 'all' },
    logOpen: false, log: [],
    gaz: { countries: [], iso3: '', admin1: [], sel: [], preview: null, log: [], open: false },
    bcountries: [],
    busy: 0,
  };

  // ---------- helpers ----------
  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = n => (n == null || n === '' ? '—' : (Number(n) / 100).toFixed(2));
  const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);
  const normName = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

  function toast(msg, ms) {
    const t = $('#toast');
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, ms || 3200);
  }
  const BUSY_MSGS = [];
  function busy(on, msg) {
    S.busy += on ? 1 : -1;
    if (on) BUSY_MSGS.push(msg || ''); else BUSY_MSGS.shift();
    const box = $('#busy');
    box.hidden = S.busy <= 0;
    const text = BUSY_MSGS.filter(Boolean).slice(-1)[0] || 'working…';
    box.innerHTML = `<div class="busy-box"><div class="busy-dot"></div><div><div class="busy-text">${esc(text)}</div><div class="busy-sub mono">running in the matching engine · please wait</div></div></div>`;
  }
  async function api(path, opts) {
    const o = Object.assign({ headers: {} }, opts || {});
    const msg = o.msg; delete o.msg;
    if (o.json !== undefined) { o.method = o.method || 'POST'; o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(o.json); delete o.json; }
    if (o.form) { o.method = 'POST'; o.body = o.form; delete o.form; }
    busy(true, msg);
    try {
      const r = await fetch(path, o);
      const d = await r.json().catch(() => ({ ok: false, error: 'bad response' }));
      if (!r.ok || d.ok === false) { toast(d.error || ('request failed: ' + r.status), 5000); return null; }
      if (d.form) S.st = d;
      return d;
    } catch (e) {
      toast('network error: ' + e.message, 5000); return null;
    } finally { busy(false); }
  }
  async function saveForm(patch) {
    const d = await api('/api/state', { json: patch });
    return d;
  }

  // ---------- derived state ----------
  const form = () => (S.st && S.st.form) || {};
  const levels = () => (S.st && S.st.levels) || ['leaf'];
  const hierLevels = () => levels().slice(0, -1);
  const stats = () => (S.st && S.st.stats) || {};
  const total = () => (S.st && S.st.total) || 0;
  const geocoded = () => (S.st && S.st.geocoded) || 0;
  const pendingAdmins = () => hierLevels().reduce((n, l) => n + ((stats()[l] || {}).pending || 0), 0);
  const leafPending = () => ((stats().leaf || {}).pending || 0);
  const placesReady = () => S.st && S.st.ready && S.st.ready.ref;
  const fileReady = () => S.st && S.st.ready && S.st.ready.target;
  const paired = () => S.st && S.st.ready && S.st.ready.hierarchy_paired;
  const canReach = n => {
    if (!S.st) return n === 1;
    if (n === 1) return true;
    if (n === 2 || n === 3) return fileReady() && placesReady() && paired();
    return !!form().ran;
  };
  const stageBadge = n => {
    if (n === 2 && S.st && S.st.has_lookups) return pendingAdmins() ? String(pendingAdmins()) : '✓';
    if (n === 3) return form().ran ? '✓' : '';
    if (n === 4 && form().ran) return leafPending() ? String(leafPending()) : '✓';
    return '';
  };
  const pct = () => (total() ? Math.round(geocoded() / total() * 100) : 0) + '%';

  // ---------- rendering ----------
  function render() {
    renderSide();
    const pane = $('#pane');
    const mapwrap = $('#mapwrap');
    const corridor = S.stage === 2 || S.stage === 4;
    // Build the markup before touching the pane: if a stage fails to render, the
    // previous screen stays intact instead of being left floating over the map.
    let html;
    try {
      html = S.stage === 1 ? renderStage1() : S.stage === 2 ? renderStage2() : S.stage === 3 ? renderStage3() : S.stage === 4 ? renderStage4() : renderStage5();
    } catch (e) {
      console.error('render failed at stage ' + S.stage, e);
      toast(`could not draw stage ${S.stage}: ${e.message}`, 8000);
      return;
    }
    pane.className = 'pane ' + (corridor ? 'corridor' : 'flow');
    mapwrap.hidden = !corridor;
    pane.innerHTML = html;
    renderDrawer();
    if (corridor) { positionFlyout(); const dl = $('#pane .dock .panel-list'); if (dl) dl.addEventListener('scroll', positionFlyout); }
    if (corridor) { ensureMap(); setTimeout(() => { MAP.invalidateSize(); if (S.stage === 2) drawAdminMap(); else drawLinkMap(); }, 0); }
  }

  function positionFlyout() {
    // pin the flyout's top to the selected dock row so it reads as growing out of that name
    const fly = $('#pane .flyout'); if (!fly) return;
    const dock = $('#pane .dock'); const pane = $('#pane');
    const sel = dock && (dock.querySelector('.row-item.sel') || null);
    if (sel && fly.dataset.anchor !== positionFlyout._last) { positionFlyout._last = fly.dataset.anchor; sel.scrollIntoView({ block: 'nearest' }); }
    const pr = pane.getBoundingClientRect(); const dr = dock.getBoundingClientRect();
    const list = dock.querySelector('.panel-list'); const lr = list ? list.getBoundingClientRect() : dr;
    let y = sel ? sel.getBoundingClientRect().top - pr.top : dr.top - pr.top;
    // keep the anchor inside the visible part of the list
    y = Math.max(lr.top - pr.top, Math.min(y, lr.bottom - pr.top - 40));
    const maxH = pr.height - 86;
    fly.style.top = y + 'px';
    fly.style.maxHeight = maxH + 'px';
    const fh = fly.getBoundingClientRect().height;
    const recent = $('#pane .recent');
    const bottom = recent ? recent.getBoundingClientRect().top - pr.top - 10 : pr.height - 14;
    if (y + fh > bottom) { const ny = Math.max(72, bottom - fh); fly.style.setProperty('--tab-y', (y - ny + 20) + 'px'); fly.style.top = ny + 'px'; }
    else fly.style.setProperty('--tab-y', '20px');
  }
  function renderSide() {
    const side = $('#side');
    const rail = S.stage === 2 || S.stage === 4;
    side.className = 'side ' + (rail ? 'rail' : 'full');
    if (rail) {
      side.innerHTML = '<div class="logo"></div>' + STAGES.map((l, i) => {
        const n = i + 1;
        return `<div class="rail-stage ${S.stage === n ? 'active' : canReach(n) ? 'ok' : ''}" data-act="stage" data-n="${n}" title="${esc(l)}">${n}</div>`;
      }).join('') + `<div class="rail-bar"><div style="height:${pct()}"></div></div>`;
      return;
    }
    let html = `<div class="brand"><div class="logo"></div><span>Match Bot</span></div>`;
    STAGES.forEach((l, i) => {
      const n = i + 1;
      html += `<div class="stage ${S.stage === n ? 'active' : canReach(n) ? 'ok' : ''}" data-act="stage" data-n="${n}"><span>${n}. ${esc(l)}</span><span class="badge">${stageBadge(n)}</span></div>`;
      if (n === 1 && S.stage === 1) {
        html += '<div class="substeps">' + SUBSTEPS.map((s, j) => `<div class="substep ${S.sub === j + 1 ? 'active' : ''}" data-act="sub" data-n="${j + 1}">${esc(s)}</div>`).join('') + '</div>';
      }
    });
    html += `<div class="side-foot"><div class="mono faint" style="font-size:11px">${geocoded()} / ${total()} geocoded</div><div class="bar"><div style="width:${pct()}"></div></div>
      <div class="actions"><button class="btn sm quiet" data-act="undo" ${S.st && S.st.can_undo ? '' : 'disabled'}>Undo</button><button class="btn sm quiet" data-act="log">History</button><button class="btn sm quiet" data-act="reset" title="Start over">New</button></div></div>`;
    side.innerHTML = html;
  }

  // ----- stage 1 -----
  function renderStage1() {
    const f = form();
    const t = S.st && S.st.target;
    const r = S.st && S.st.ref;
    const summary = t ? `${t.rows} rows · ${t.columns.length} columns` : 'no file yet';
    let body = '';
    if (S.sub === 1) body = renderSub1(f, t);
    else if (S.sub === 2) body = renderSub2(f, r);
    else body = renderSub3(f);
    const canNext = S.sub === 1 ? fileReady() : S.sub === 2 ? (placesReady() && paired()) : (fileReady() && placesReady() && paired());
    const nextLabel = S.sub === 3 ? 'Continue to admin names' : 'Continue';
    return `<div class="topbar"><span class="title" contenteditable="true" data-field="project_name" spellcheck="false">${esc(f.project_name || 'Untitled project')}</span><span class="mono muted" style="font-size:11px">${esc(summary)}</span></div>
      ${body}
      <div class="footer"><button class="btn lg" data-act="back1" ${S.sub === 1 ? 'style="color:#c4c4bd"' : ''}>Back</button><button class="btn lg primary" data-act="next1" ${canNext ? '' : 'disabled'}>${nextLabel}</button></div>`;
  }

  function colSelect(value, columns, attrs) {
    return `<select class="input" ${attrs}><option value="">—</option>${columns.map(c => `<option value="${esc(c)}" ${c === value ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>`;
  }

  function renderSub1(f, t) {
    let html = `<div class="setup"><div class="setup-body"><div class="h1">Upload your community list</div>`;
    if (!t) {
      html += `<div class="drop" data-act="pick-file" data-role="target"><div class="big">Drop a csv here, or click to choose a file</div><div class="mono faint" style="font-size:11px">community names + admin names</div></div>`;
    } else {
      const cols = t.columns;
      const hier = f.target_hierarchy || [];
      html += `<div class="filecard"><div class="flex"><div class="name">${esc(f.target_filename)}</div><div class="mono muted" style="font-size:11px;margin-top:5px">${t.rows} rows · ${cols.length} columns</div></div><button class="btn" data-act="pick-file" data-role="target">Replace</button></div>
        <div class="h1 mt">Which columns hold the names?</div><div class="colrows">
        <div class="colrow first"><span class="lbl">Community name</span>${colSelect(f.target_name_column, cols, 'data-field="target_name_column"')}<span class="right">${t.rows} filled</span></div>`;
      hier.forEach((h, i) => {
        html += `<div class="colrow"><span class="lbl"><span class="mono faint" style="font-size:10px">L${i + 1}</span><input value="${esc(h.label || '')}" placeholder="label" data-hier="target" data-i="${i}" data-k="label"></span>${colSelect(h.column, cols, `data-hier="target" data-i="${i}" data-k="column"`)}<span class="right"><span>admin level ${i + 1}</span><span class="x" data-act="rm-level" data-i="${i}" title="remove level">✕</span></span></div>`;
      });
      html += `<button class="btn addlevel" data-act="add-level">+ add admin level</button></div>`;
      const pcols = [f.target_name_column].concat(hier.map(h => h.column)).filter(Boolean);
      if (pcols.length) {
        const grid = `grid-template-columns:40px ${pcols.map((c, i) => i === 0 ? '1.3fr' : '1fr').join(' ')}`;
        html += `<div class="preview"><div class="hd" style="${grid}"><div class="idx">#</div>${pcols.map(c => `<div>${esc(c)}</div>`).join('')}</div>`;
        t.preview.slice(0, 5).forEach((row, i) => { html += `<div class="r" style="${grid}"><div class="idx">${i + 1}</div>${pcols.map(c => `<div>${esc(row[c])}</div>`).join('')}</div>`; });
        html += '</div>';
      }
    }
    html += `</div><div class="setup-side"><div class="label-caps">Still to set</div><div style="margin-top:14px">
      <div class="row"><span>Named places</span><span class="mono">${esc(placesStatus())}</span></div>
      <div class="row"><span>Matching rules</span><span class="mono">${fmt(f.threshold == null ? 85 : f.threshold)}</span></div></div></div></div>`;
    return html;
  }

  function placesStatus() {
    const f = form(); const r = S.st && S.st.ref;
    if (!r) return 'not set';
    if (!placesReady()) return `${r.rows} rows · columns not set`;
    if (hierLevels().length && !(S.st.ready || {}).tagged) return `${r.rows} places · admin units not assigned`;
    return `${r.rows} places` + (f.ref_source === 'gazetteer' ? ' · OSM + GeoNames' : ' · own file');
  }

  function renderSub2(f, r) {
    const src = f.ref_source;
    let html = `<div class="setup wide"><div class="setup-body"><div class="h1">Where should named places come from?</div>
      <div class="cards"><div class="card ${src === 'gazetteer' || S.gaz.open ? 'on' : ''}" data-act="pick-gaz"><div class="t">Build from OSM + GeoNames</div><div class="note">named places in your regions<br>cities, towns, villages, wards</div></div>
      <div class="card ${src === 'upload' ? 'on' : ''}" data-act="pick-upload"><div class="t">Upload my own geocoded list</div><div class="note">csv with name, admin, lat, lon<br>identify the columns after upload</div></div></div>`;
    if (S.gaz.open || (src === 'gazetteer' && !r)) html += renderGaz(f);
    if (src === 'upload' && !r) {
      html += `<div class="drop maxw" data-act="pick-file" data-role="ref"><div class="big">Drop a csv here, or click to choose a file</div><div class="mono faint" style="font-size:11px">place names + admin names + latitude + longitude</div></div>`;
    }
    if (r) {
      const cols = r.columns;
      const hier = f.ref_hierarchy || [];
      const thier = f.target_hierarchy || [];
      html += `<div class="filecard maxw"><div class="flex"><div class="name">${esc(f.ref_filename)}</div><div class="mono muted" style="font-size:11px;margin-top:5px">${r.rows} rows · ${cols.length} columns${src === 'gazetteer' ? ' · built from OSM + GeoNames' : ''}</div></div>
        ${src === 'gazetteer' ? '<button class="btn" data-act="pick-gaz">Rebuild</button>' : ''}<button class="btn" data-act="pick-file" data-role="ref">Replace with csv</button></div>
        <div class="h1 mt">Which columns hold the places?</div><div class="colrows maxw">
        <div class="colrow first"><span class="lbl">Place name</span>${colSelect(f.ref_name_column, cols, 'data-field="ref_name_column"')}<span class="right">${r.rows} filled</span></div>
        <div class="colrow"><span class="lbl">Latitude</span>${colSelect(f.ref_lat_column, cols, 'data-field="ref_lat_column"')}<span class="right"></span></div>
        <div class="colrow"><span class="lbl">Longitude</span>${colSelect(f.ref_lon_column, cols, 'data-field="ref_lon_column"')}<span class="right"></span></div></div>
        <div class="note maxw" style="margin-top:12px">admin units for each place are assigned from the boundaries below — any admin columns in this file are ignored</div>`;
      const pcols = [f.ref_name_column, f.ref_lat_column, f.ref_lon_column].filter(Boolean);
      if (pcols.length) {
        const grid = `grid-template-columns:${pcols.map((c, i) => i === 0 ? '1.4fr' : '1fr').join(' ')}`;
        html += `<div class="preview maxw"><div class="hd" style="${grid}">${pcols.map(c => `<div>${esc(c)}</div>`).join('')}</div>`;
        r.preview.slice(0, 5).forEach(row => { html += `<div class="r" style="${grid}">${pcols.map((c, i) => `<div ${i ? 'class="muted"' : ''}>${esc(row[c])}</div>`).join('')}</div>`; });
        html += '</div>';
      }
    }
    html += renderBoundaries(f);
    html += '</div></div>';
    return html;
  }

  function renderGaz(f) {
    const g = S.gaz;
    let html = `<div class="gaz"><div class="label-caps">Build named places</div>
      <div class="row"><span style="font-size:13px">Country</span><select class="input" data-gaz="iso3" style="min-width:260px"><option value="">choose…</option>${g.countries.map(c => `<option value="${esc(c.iso3)}" ${c.iso3 === g.iso3 ? 'selected' : ''}>${esc(c.name)} (${esc(c.iso3)})</option>`).join('')}</select>
      <button class="btn" data-act="gaz-admin1" ${g.iso3 ? '' : 'disabled'}>List admin areas</button></div>`;
    if (g.admin1.length) {
      html += `<div class="row"><span class="note">Pick the admin-1 areas your communities fall in</span><button class="btn sm" data-act="gaz-all">all</button><button class="btn sm" data-act="gaz-none">none</button></div>
        <div class="admins">${g.admin1.map(a => `<label><input type="checkbox" data-gaz-admin="${esc(a)}" ${g.sel.includes(a) ? 'checked' : ''}>${esc(a)}</label>`).join('')}</div>
        <div class="row"><button class="btn" data-act="gaz-preview" ${g.sel.length ? '' : 'disabled'}>Preview GeoNames count</button><button class="btn" data-act="gaz-fetch" ${g.sel.length ? '' : 'disabled'}>Fetch OSM for selected</button><button class="btn primary" data-act="gaz-build" ${g.sel.length ? '' : 'disabled'}>Build named places</button></div>`;
      if (g.preview) html += `<div class="note">${g.preview.geonames_count} GeoNames places across ${plural(g.preview.admin_count, 'area', 'areas')} before OSM</div>`;
    }
    if (g.log.length) html += `<div class="log">${g.log.map(l => `<div>${esc(l)}</div>`).join('')}</div>`;
    html += '</div>';
    return html;
  }

  function renderBoundaries(f) {
    const lv = hierLevels();
    if (!lv.length) return `<div class="bounds"><div class="label-caps">Admin boundaries</div><div class="note">no admin levels on the community list — nothing to assign</div></div>`;
    const src = f.boundaries_source || 'auto';
    const files = f.boundary_files || {};
    const bl = f.boundary_levels || {};
    const tagged = f.ref_tagged;
    const ready = S.st.ready || {};
    let html = `<div class="bounds"><div class="label-caps">Admin boundaries · assign each place its ${lv.join(' and ')}</div>
      <div class="note">the tool places every named point inside these polygons and uses the polygon names as its admin units</div>
      <div class="row"><label class="radio"><input type="radio" name="bsrc" value="auto" ${src === 'auto' ? 'checked' : ''} data-field="boundaries_source">geoBoundaries (automatic)</label>
      <label class="radio"><input type="radio" name="bsrc" value="upload" ${src === 'upload' ? 'checked' : ''} data-field="boundaries_source">upload my own GeoJSON</label></div>`;
    if (src === 'auto') {
      html += `<div class="row"><span>Country</span><select class="input" data-field="country" style="min-width:260px"><option value="">choose…</option>${S.gaz.countries.map(c => `<option value="${esc(c.iso3)}" ${c.iso3 === f.country ? 'selected' : ''}>${esc(c.name)} (${esc(c.iso3)})</option>`).join('')}</select>${f.country ? `<button class="btn sm" data-act="suggest-levels">Suggest levels from names</button>` : ''}</div>`;
      lv.forEach((l, i) => {
        const cur = bl[l] || ('ADM' + Math.min(i + 1, 4));
        html += `<div class="lvl"><span class="mono">${esc(l)}</span><span class="row"><span class="note">geoBoundaries level</span><select class="input" data-adm="${esc(l)}">${['ADM1', 'ADM2', 'ADM3', 'ADM4'].map(a => `<option ${a === cur ? 'selected' : ''}>${a}</option>`).join('')}</select></span><span class="note">${tagged && tagged.stats && tagged.stats[l] ? tagStat(tagged.stats[l]) : ''}</span></div>`;
      });
    } else {
      lv.forEach(l => {
        const bf = files[l];
        html += `<div class="lvl"><span class="mono">${esc(l)}</span>${bf ? `<span class="note">${esc(bf.filename)} · ${bf.features} features · <span style="cursor:pointer;text-decoration:underline" data-act="pick-file" data-role="boundaries" data-label="${esc(l)}">replace</span></span><span class="row"><span class="note">name property</span>${colSelect(bf.name_property, bf.properties, `data-bprop="${esc(l)}"`)}</span>` : `<button class="btn" data-act="pick-file" data-role="boundaries" data-label="${esc(l)}">Choose .geojson</button><span></span>`}</div>`;
        if (bf && tagged && tagged.stats && tagged.stats[l]) html += `<div class="note" style="padding-left:132px">${tagStat(tagged.stats[l])}</div>`;
      });
    }
    const canTag = ready.ref && (src === 'upload' ? lv.every(l => files[l] && files[l].name_property) : !!f.country);
    html += `<div class="row" style="margin-top:6px"><button class="btn ${ready.tagged ? '' : 'primary'}" data-act="tag-places" ${canTag ? '' : 'disabled'}>${ready.tagged ? 'Re-assign admin units' : 'Assign admin units from boundaries'}</button>
      <span class="note">${ready.tagged ? `${tagged.rows} places tagged` : (canTag ? 'required before matching' : (ready.ref ? 'choose the boundaries first' : 'set the place columns first'))}</span></div>`;
    html += '</div>';
    return html;
  }
  function tagStat(st) {
    const inside = st.inside + st.snapped;
    return `${st.adm === 'upload' ? 'your file' : st.adm} · ${st.polygons} polygons · ${inside} places inside${st.outside ? ` · ${st.outside} outside all polygons` : ''}${st.invalid ? ` · ${st.invalid} without coordinates` : ''}`;
  }

  function renderSub3(f) {
    const thr = f.threshold == null ? 85 : f.threshold;
    let note = 'suggestions at or above this score are accepted without review';
    if (S.hist && S.hist.scores) {
      const vals = Object.values(S.hist.scores);
      const n = vals.filter(v => v >= thr).length;
      note = `${n} of ${vals.length} still-unmatched communities have a candidate at ${fmt(thr)} or better`;
    }
    return `<div class="setup wide"><div class="setup-body"><div class="h1">Matching rules</div>
      <div class="rules"><div><div class="row"><span>Auto-accept above</span><span class="mono" id="thr-label">${fmt(thr)}</span></div><input type="range" min="50" max="100" step="1" value="${thr}" data-field="threshold" style="margin-top:12px"></div>
      <label class="check"><input type="checkbox" data-field="restrict" ${f.restrict === false ? '' : 'checked'}> Only consider places inside the parent admin unit</label>
      <div class="note">${esc(note)}</div></div>
      <div class="hint">exact and near-exact names (edit distance ≤ 1) always match; the threshold governs the fuzzy suggestions layered on top</div>
      </div></div>`;
  }

  // ----- stage 2 -----
  function renderStage2() {
    const lv = hierLevels();
    if (!S.level) S.level = lv[0];
    const L = S.lvl;
    const rows = L ? L.rows : [];
    const q = S.adminQuery.toLowerCase();
    const shown = rows.filter(r => !q || r.name.toLowerCase().includes(q));
    const cur = currentAdmin();
    const pend = rows.filter(r => r.status === 'pending');
    const idx = lv.indexOf(S.level);
    const nextLabel = idx < lv.length - 1 ? `Continue to ${lv[idx + 1]} →` : 'Continue to matching →';
    const levelTabs = lv.map((l, i) => {
      const st = stats()[l] || {};
      const cls = l === S.level ? 'on' : (st.pending === 0 && S.st.has_lookups ? 'done' : '');
      return `<span class="${cls}" data-act="level" data-l="${esc(l)}">${esc(l)}${S.st.has_lookups && st.pending ? ' · ' + st.pending : (S.st.has_lookups ? ' ✓' : '')}</span>`;
    }).join('');
    const candQ = S.adminCandQuery.toLowerCase();
    const cands = S.adminCands.filter(c => !candQ || c.ref_name.toLowerCase().includes(candQ));
    const picked = S.adminCandSel || (S.adminCands[0] ? S.adminCands[0].ref_key : null);
    const headline = pend.length ? `${plural(pend.length, 'admin name needs', 'admin names need')} a decision` : `all ${S.level} names harmonized`;
    const sub = pend.length ? `they cover ${L ? L.pending_communities : 0} of ${L ? L.total_communities : total()} communities` : `${rows.length - pend.length} of ${rows.length} decided`;
    return `<div class="strip"><span class="title">${esc(cur ? cur.name : headline)}</span><span class="meta">${esc(cur ? `${S.level} · ${plural(cur.communities, 'community', 'communities')}${cur.parents ? ' · ' + cur.parents : ''}` : sub)}</span>
        <input class="input" placeholder="Search admin names" value="${esc(S.adminQuery)}" data-input="adminQuery">
        <div class="right"><span class="mono muted" style="font-size:11px">${pend.length} left · ${rows.length - pend.length} decided</span><button class="btn sm" data-act="undo" ${S.st.can_undo ? '' : 'disabled'}>Undo</button><button class="btn sm" data-act="log">History</button></div></div>
      <div class="dock"><div class="panel-hd"><span class="caps">Admin names</span><span class="mono">${pend.length ? pend.length + ' left' : 'all linked'}</span></div>
        <div class="levels">${levelTabs}</div>
        <div class="panel-list" style="margin-top:8px">${shown.map(r => {
          const sel = cur && cur.key === r.key;
          const meta = r.status === 'linked' ? (r.ref + (r.score != null ? ' · ' + fmt(r.score) : '')) : r.status === 'no_equivalent' ? 'no equivalent' : plural(r.communities, 'community', 'communities') + (r.parents ? ' · ' + r.parents : '');
          const right = r.status === 'pending' ? (r.best ? fmt(r.best) : '—') : '✓';
          return `<div class="row-item ${sel ? 'sel' : ''} ${r.status !== 'pending' ? 'done' : ''}" data-act="admin-sel" data-key="${esc(r.key)}"><div class="main-col"><div class="name">${esc(r.name)}</div><div class="meta">${esc(meta)}</div></div><span class="right">${right}</span></div>`;
        }).join('') || '<div class="cand-empty">nothing at this level</div>'}</div>
        <div class="panel-ft"><button class="btn" data-act="accept-above" data-thr="90">Accept suggestions above 0.90</button><button class="btn ${pend.length ? 'outline' : 'primary'}" data-act="level-next">${nextLabel}</button></div></div>
      ${cur ? `<div class="flyout" data-anchor="${esc(cur.key)}"><div class="insp-hd"><div class="h2">${esc(cur.name)}</div><div class="meta">${esc(cur ? `${S.level} · ${plural(cur.communities, 'community', 'communities')}` : '')}</div></div>
        <div class="insp-sub"><div class="top"><span class="label-caps">All candidates</span><span class="mono faint" style="font-size:10px">${cands.length} of ${S.adminCands.length}</span></div><input class="input" placeholder="Filter candidates" value="${esc(S.adminCandQuery)}" data-input="adminCandQuery"></div>
        <div class="panel-list">${cands.map(c => `<div class="cand ${c.ref_key === picked ? 'on' : ''}" data-act="admin-cand" data-key="${esc(c.ref_key)}"><div class="body"><div class="name">${esc(c.ref_name)}</div><div class="meta">${esc(Object.entries(c.parents || {}).map(([k, v]) => k + '=' + v).join(' › ') || 'top level')}</div></div><span class="score">${fmt(c.score)}</span></div>`).join('') || (cur && cur.status === 'pending' ? '<div class="cand-empty">no unmatched reference names in this parent group — mark “no equivalent” or fix the parent level</div>' : cur ? `<div class="cand-empty">${cur.status === 'linked' ? 'linked to ' + esc(cur.ref) : 'marked no equivalent'}</div>` : '')}</div>
        <div class="panel-ft">${cur && cur.status === 'pending' ? `<button class="btn primary" style="height:36px" data-act="admin-link" ${picked ? '' : 'disabled'}>Link &amp; continue</button><div style="display:flex;gap:8px"><button class="btn quiet" style="flex:1;height:30px" data-act="admin-noeq">No equivalent</button><button class="btn quiet" style="flex:1;height:30px" data-act="admin-skip">Skip</button></div>` : cur ? `<button class="btn" data-act="admin-unlink">Undo this decision</button>` : ''}</div></div>` : ''}`;
  }
  function currentAdmin() {
    const rows = S.lvl ? S.lvl.rows : [];
    const found = rows.find(r => r.key === S.adminSel);
    if (found) return found;
    return rows.find(r => r.status === 'pending') || rows[0] || null;
  }

  // ----- stage 3 -----
  function renderStage3() {
    const f = form();
    const leaf = stats().leaf || {};
    const thr = f.threshold == null ? 85 : f.threshold;
    const matched = geocoded(), left = total() - matched;
    const adminsLinked = hierLevels().map(l => { const s = stats()[l] || {}; return `${l}: ${s.linked || 0} of ${s.total || 0} linked`; }).join(' · ');
    let body;
    if (!f.ran) {
      body = `<div style="max-width:620px"><div class="h0">Ready to match ${total()} communities</div><div class="note" style="font-size:12px;margin-top:12px"><div>${esc(adminsLinked || 'no admin levels')}</div><div>${(S.st.ref || {}).rows || 0} named places · threshold ${fmt(thr)}${f.restrict === false ? ' · unrestricted' : ''}</div></div>
        <button class="btn xl primary" style="margin-top:30px" data-act="run">Run fuzzy matching</button></div>`;
    } else {
      const bins = S.hist ? S.hist.bins : [];
      const maxBin = Math.max(1, ...bins.map(b => b.n));
      const scores = S.hist && S.hist.scores ? Object.values(S.hist.scores) : [];
      const would = scores.filter(s => s >= thr).length;
      body = `<div class="h0">${matched} of ${total()} matched automatically</div><div class="note" style="font-size:12px;margin-top:10px">${left} fell below the threshold and need a person</div>
        <div class="bar-split"><div class="fill" style="width:${pct()}"></div><div class="rest"></div></div><div class="bar-legend"><span>${matched} accepted</span><span>${left} unmatched</span></div>
        <div class="two-col"><div><div class="label-caps">Score distribution · best candidate for each unmatched community</div>
          <div class="hist"><div class="bars">${bins.map(b => `<div class="${b.lo >= thr ? '' : 'below'}" style="height:${Math.max(2, b.n / maxBin * 100)}%" title="${fmt(b.lo)}–${fmt(b.hi)} · ${b.n}"></div>`).join('')}
            <div class="thr-line" style="left:${(thr - 50) / 50 * 100}%"><span>threshold ${fmt(thr)}</span></div></div>
          <div class="ticks">${[50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100].map(v => `<div class="tick ${v % 10 === 0 ? 'major' : ''}" style="left:${(v - 50) / 50 * 100}%"><i></i>${v % 10 === 0 ? `<span>${fmt(v)}</span>` : ''}</div>`).join('')}</div></div>
          ${S.runLog ? `<div class="label-caps" style="margin-top:28px">Engine log</div><div class="pre">${esc(S.runLog)}</div>` : ''}</div>
        <div><div class="label-caps">Adjust and re-run</div><div class="adjust"><div><div class="row"><span>Auto-accept above</span><span class="mono" id="thr-label">${fmt(thr)}</span></div><input type="range" min="50" max="100" step="1" value="${thr}" data-field="threshold" style="margin-top:10px"><div class="note" style="margin-top:8px">${would} of the remaining ${scores.length} have a candidate at ${fmt(thr)} or better</div></div>
          <label class="check" style="font-size:13px"><input type="checkbox" data-field="restrict" ${f.restrict === false ? '' : 'checked'}> Restrict to parent admin unit</label>
          <button class="btn outline" style="height:34px" data-act="run">Re-run matching</button></div>
          <button class="btn primary block" style="height:42px;margin-top:18px" data-act="stage" data-n="4">Link the remaining ${left} →</button></div></div>`;
    }
    return `<div class="topbar"><span class="title">${esc(f.project_name)}</span><span class="mono muted" style="font-size:11px">${f.ran ? 'last run ' + esc(f.last_run) : 'not run yet'}</span><div class="right"><button class="btn sm" data-act="undo" ${S.st.can_undo ? '' : 'disabled'}>Undo</button><button class="btn sm" data-act="log">History</button></div></div><div class="body-pad">${body}</div>`;
  }

  // ----- stage 4 -----
  function pathKeys(row, lv) {
    // group keys for every ancestor of a community row, outermost first
    const out = []; let k = '';
    lv.forEach(l => { k += '\u001f' + ((row.parents || {})[l] || '—'); out.push(k); });
    return out;
  }
  function currentComm() {
    const rows = S.comm.rows;
    return rows.find(r => r.id === S.comm.sel) || rows.find(r => r.status === 'pending') || rows[0] || null;
  }
  function renderStage4() {
    const c = S.comm;
    if (!c.open) { c.open = new Set(); c.revealed = null; }
    const comm = currentComm();
    const q = c.query.toLowerCase();
    const list = c.rows.filter(r => !q || r.name.toLowerCase().includes(q) || (r.ref_name || '').toLowerCase().includes(q));
    const left = leafPending();
    const manual = S.st.manual || 0;
    const cands = c.cands;
    const restrict = form().restrict !== false;
    const noCandsNote = restrict ? 'no candidate inside the linked admin unit — click the map to place a point, or turn off the admin restriction in stage 3' : 'no candidate above 0.35 — click the map to place a point';
    const saveOn = !!c.pin || (cands.length > 0 && comm && comm.status === 'pending');
    const lv = hierLevels();
    const commRow = (r, depth) => {
      const sel = comm && comm.id === r.id;
      const right = r.status === 'linked' ? (r.score != null ? fmt(r.score) : 'auto') : r.status === 'pin' ? 'pin' : r.status === 'no_equivalent' ? '—' : depth ? '' : String(r.pool);
      const meta = r.status === 'linked' ? `→ ${r.ref_name}` : r.status === 'pin' ? 'dropped pin' : r.status === 'no_equivalent' ? 'no equivalent' : (depth ? plural(r.pool, 'place in pool', 'places in pool') : r.path);
      return `<div class="row-item d${depth} ${sel ? 'sel' : ''} ${r.status !== 'pending' ? 'done' : ''}" data-act="comm-sel" data-id="${esc(r.id)}"><div class="main-col"><div class="name">${esc(r.name)}</div><div class="meta">${esc(meta)}</div></div><span class="right">${esc(right)}</span></div>`;
    };
    // keep the branch holding the current community open, but only when the selection moves
    if (comm && c.revealed !== comm.id) { c.revealed = comm.id; pathKeys(comm, lv).forEach(k => c.open.add(k)); }
    const tree = (rows, depth, prefix) => {
      const label = lv[depth];
      const groups = new Map();
      rows.forEach(r => { const g = (r.parents || {})[label] || '—'; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(r); });
      return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([g, rs]) => {
        const key = prefix + '\u001f' + g;
        const open = c.open.has(key);
        const pend = rs.filter(r => r.status === 'pending').length;
        const right = c.all ? `${rs.length - pend}/${rs.length}` : String(pend);
        const kids = !open ? '' : depth + 1 < lv.length ? tree(rs, depth + 1, key) : rs.map(r => commRow(r, depth + 1)).join('');
        return `<div class="row-item grp d${depth} ${open ? 'open' : ''}" data-act="comm-grp" data-key="${esc(key)}"><div class="main-col"><div class="name"><span class="caret">${open ? '▾' : '▸'}</span>${esc(g)}</div><div class="meta">${esc(label)}</div></div><span class="right">${esc(right)}</span></div>${kids}`;
      }).join('');
    };
    const listHtml = list.length ? (q || !lv.length ? list.map(r => commRow(r, 0)).join('') : tree(list, 0, '')) : '<div class="cand-empty">everything is linked</div>';
    return `<div class="strip"><span class="title">${esc(comm ? comm.name : '—')}</span><span class="meta">${esc(comm ? comm.path : '')}</span>
        ${restrict && comm ? `<span class="scope-chip" id="scope-chip">places inside ${esc(comm.path || 'the parent admin unit')}</span>` : '<span class="scope-chip off">all places · restriction off</span>'}
        <input class="input" placeholder="Search communities or places" value="${esc(c.query)}" data-input="commQuery">
        <div class="right"><span class="mono muted" style="font-size:11px">${left} left</span><button class="btn sm" data-act="undo" ${S.st.can_undo ? '' : 'disabled'}>Undo</button><button class="btn sm" data-act="log">History</button></div></div>
      <div class="dock"><div class="panel-hd"><span class="caps">${c.all ? 'All' : 'Unlinked'} · ${c.all ? c.rows.length : left}</span><span class="mono" style="cursor:pointer" data-act="toggle-all">${c.all ? 'hide linked' : 'show all ↗'}</span></div>
        <div class="panel-list">${listHtml}</div>
        <div class="panel-ft"><div class="foot-stats"><span>${geocoded()} linked</span><span>${manual} by hand</span></div><button class="btn ${left ? 'outline' : 'primary'}" data-act="stage" data-n="5">Review &amp; export →</button></div></div>
      ${comm ? `<div class="flyout" data-anchor="${esc(comm.id)}"><div class="insp-hd"><div class="h2">${esc(comm.name)}</div><div class="meta">${esc(comm ? comm.path : '')}</div></div>
        <div class="label-caps" style="padding:12px 14px 6px">${cands.length ? 'Candidates · press 1–' + cands.length : 'Candidates'}${restrict ? ' · within parent admin' : ''}</div>
        <div class="panel-list">${comm && comm.status !== 'pending' ? `<div class="cand-empty">${comm.status === 'linked' ? `linked to ${esc(comm.ref_name)} · ${comm.auto ? 'auto' : 'manual'}` : comm.status === 'pin' ? 'has a dropped pin' : 'marked no equivalent'}</div>` : ''}
          ${cands.map((cd, i) => `<div class="cand ${i === c.candSel && !c.pin ? 'on' : ''}" data-act="cand" data-i="${i}"><div class="key">${i + 1}</div><div class="body"><div class="name">${esc(cd.ref_name_raw || cd.ref_name)}</div><div class="meta">${esc(Object.values(cd.ref_parents || {}).filter(Boolean).join(' › '))}</div></div><span class="score">${fmt(cd.score)}</span></div>`).join('')}
          ${c.pin ? `<div class="cand on"><div class="key pin">✚</div><div class="body"><div class="name">Point on the map</div><div class="meta">${c.pin.lat.toFixed(4)}, ${c.pin.lon.toFixed(4)}</div></div><span class="mono muted" style="font-size:11px;cursor:pointer" data-act="clear-pin">clear</span></div>` : ''}
          ${!cands.length && !c.pin && comm && comm.status === 'pending' ? `<div class="cand-empty">${esc(noCandsNote)}</div>` : ''}</div>
        <div class="panel-ft"><button class="btn primary" style="height:36px" data-act="save" ${saveOn ? '' : 'disabled'}>${c.pin ? 'Save point ↵' : 'Save match ↵'}</button>
          ${comm && comm.status !== 'pending' ? `<button class="btn quiet" style="height:30px" data-act="comm-unlink">${comm.status === 'pin' ? 'Remove pin' : comm.status === 'linked' ? 'Unlink' : 'Clear decision'}</button>` : `<div style="display:flex;gap:8px"><button class="btn quiet" style="flex:1;height:30px" data-act="comm-noeq">No equivalent</button><button class="btn quiet" style="flex:1;height:30px" data-act="skip">Skip</button></div>`}</div></div>` : ''}
      <div class="recent"><div class="top"><span class="label-caps">Recent</span><span class="mono faint" style="font-size:10px;cursor:pointer" data-act="log">full log ↗</span></div><div class="lines">${(S.st.recent && S.st.recent.length ? S.st.recent : [{ text: 'nothing yet' }]).map(e => `<div>${esc(e.text)}</div>`).join('')}</div></div>`;
  }

  // ----- stage 5 -----
  function renderStage5() {
    const R = S.review;
    const q = R.query.toLowerCase();
    const rows = R.rows.filter(r => {
      if (R.filter === 'auto') return r.by === 'auto';
      if (R.filter === 'manual') return r.by.startsWith('manual');
      if (R.filter === 'none') return r.by === 'pending' || r.by === 'no equivalent';
      return true;
    }).filter(r => !q || r.name.toLowerCase().includes(q) || (r.loc || '').toLowerCase().includes(q));
    const counts = { all: R.rows.length, auto: R.rows.filter(r => r.by === 'auto').length, manual: R.rows.filter(r => r.by.startsWith('manual')).length };
    counts.none = counts.all - counts.auto - counts.manual;
    const has = R.rows.filter(r => r.lat !== '' && r.lat != null).length;
    return `<div class="topbar"><input class="input" placeholder="Search all communities" value="${esc(R.query)}" data-input="reviewQuery" style="width:250px"><div class="right"><button class="btn sm" data-act="log">History</button><button class="btn sm primary" data-act="export">Export csv</button></div></div>
      <div class="review-hd"><div><div class="h">${has} of ${R.rows.length} have coordinates</div><div class="note" style="font-size:12px;margin-top:8px">${counts.manual} set by hand · ${counts.none} still pending</div></div>
        <div class="filters">${['all', 'auto', 'manual', 'none'].map(k => `<span class="${R.filter === k ? 'on' : ''}" data-act="filter" data-k="${k}">${k} ${counts[k]}</span>`).join('')}</div></div>
      <div class="table"><div class="hd"><div>Community</div><div>Admin path</div><div>Linked location</div><div>Coordinates</div><div>Score</div><div>Set by</div></div>
        <div class="rows">${rows.map(r => { const has = r.lat !== '' && r.lat != null; return `<div class="r ${has ? '' : 'pending'}"><div>${esc(r.name)}</div><div class="sm">${esc(r.path)}</div><div class="sm" style="${has ? 'color:#111110' : ''}">${esc(r.loc || 'no location yet')}</div><div class="mono ${has ? '' : 'faint'}">${has ? esc(Number(r.lat).toFixed(4) + ', ' + Number(r.lon).toFixed(4)) : '—'}</div><div class="mono" style="font-size:12px">${r.score !== '' && r.score != null ? fmt(r.score) : '—'}</div><div class="mono muted">${esc(r.by)}</div></div>`; }).join('')}</div></div>`;
  }

  function renderDrawer() {
    const d = $('#drawer');
    d.hidden = !S.logOpen;
    if (!S.logOpen) return;
    d.innerHTML = `<div class="sheet"><div class="hd"><span>History</span><button class="btn sm" data-act="log-close">Close</button></div><div class="list">${S.log.length ? S.log.map(e => `<div class="e ${e.undone ? 'undone' : ''}"><span class="t">${esc(e.time)}</span><span class="x">${esc(e.text)}</span></div>`).join('') : '<div class="cand-empty">nothing yet</div>'}</div></div>`;
  }

  // ---------- map ----------
  let MAP = null, LAYERS = { base: null, admin: null, places: null, cands: null, pin: null };
  function ensureMap() {
    if (MAP) return MAP;
    MAP = L.map('map', { zoomControl: false, attributionControl: true }).setView([0, 20], 4);
    LAYERS.base = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, className: 'basemap', attribution: '© OpenStreetMap contributors' }).addTo(MAP);
    L.control.zoom({ position: 'bottomleft' }).addTo(MAP);
    LAYERS.admin = L.layerGroup().addTo(MAP);
    LAYERS.places = L.layerGroup().addTo(MAP);
    LAYERS.cands = L.layerGroup().addTo(MAP);
    LAYERS.pin = L.layerGroup().addTo(MAP);
    MAP.on('zoomend moveend', placeLabels);
    MAP.on('click', e => { if (S.stage === 4 && currentComm() && currentComm().status === 'pending') { S.comm.pin = { lat: e.latlng.lat, lon: e.latlng.lng }; render(); } });
    return MAP;
  }
  // keep fitted areas clear of the dock + flyout on the left, and the Recent panel at the bottom
  const PAD = { paddingTopLeft: [720, 80], paddingBottomRight: [40, 120] };
  function clearLayers() { Object.keys(LAYERS).forEach(k => { if (k !== 'base' && LAYERS[k]) LAYERS[k].clearLayers(); }); DOT_LABELS.length = 0; }

  const BOUNDS_CACHE = {};
  async function loadBoundaries(label) {
    if (BOUNDS_CACHE[label] !== undefined) return BOUNDS_CACHE[label];
    busy(true, `Loading admin boundaries for the ${label} level`);
    try {
      const r = await fetch('/api/boundaries/' + encodeURIComponent(label));
      const d = await r.json().catch(() => null);
      BOUNDS_CACHE[label] = d && d.ok ? d : null;
      if (!BOUNDS_CACHE[label] && d && d.error && r.status !== 404) toast(d.error, 5000);
    } catch (e) { BOUNDS_CACHE[label] = null; }
    finally { busy(false); }
    return BOUNDS_CACHE[label];
  }

  async function drawAdminMap() {
    if (!MAP || S.stage !== 2) return;
    clearLayers();
    const cands = S.adminCands;
    const picked = S.adminCandSel || (cands[0] ? cands[0].ref_key : null);
    const top = cands[0] ? cands[0].score : 1;
    const scoreOf = {};
    cands.forEach(c => { scoreOf[normName(c.ref_name)] = c.score; });
    const b = await loadBoundaries(S.level);
    if (S.stage !== 2) return;
    if (!b || !b.geojson) { $('#maphint').textContent = '[ no admin boundaries available — choose a country or upload GeoJSON in Set up ]'; return; }
    const src = b.adm === 'upload' ? 'your GeoJSON' : 'geoBoundaries ' + b.adm;
    $('#maphint').textContent = `[ ${src} · ${b.matched} of ${b.reference_values} ${S.level} names have a boundary ]`;
    const bounds = [];
    const layer = L.geoJSON(b.geojson, {
      style: f => {
        const ref = normName((f.properties || {})._ref);
        const sc = ref ? scoreOf[ref] : null;
        const on = picked && ref && ref === normName(picked);
        const rel = sc == null ? 0 : (top ? sc / top : 0);
        return { color: '#111110', weight: on ? 2 : (sc == null ? 0.8 : 1), dashArray: (on || sc == null || rel > 0.55) ? null : '4 3',
                 opacity: on ? 1 : (sc == null ? 0.35 : 0.35 + 0.6 * rel), fillColor: '#111110', fillOpacity: on ? 0.16 : (sc == null ? 0 : 0.03 + 0.1 * rel) };
      },
      onEachFeature: (f, lyr) => {
        const props = f.properties || {};
        const ref = normName(props._ref);
        const sc = ref ? scoreOf[ref] : null;
        const raw = props._name || props._ref || '';
        if (sc != null) {
          const on = picked && ref === normName(picked);
          lyr.bindTooltip(`${raw} · ${fmt(sc)}`, { permanent: true, direction: 'center', className: 'poly-label' + (on ? ' on' : '') });
          const c = cands.find(x => normName(x.ref_name) === ref);
          lyr.on('click', () => { S.adminCandSel = c.ref_key; render(); });
          bounds.push(lyr.getBounds());
        } else {
          lyr.bindTooltip(String(raw), { direction: 'center', className: 'poly-label' });
        }
      },
    }).addTo(LAYERS.admin);
    if (bounds.length) {
      const all = bounds.reduce((a, bb) => a.extend(bb), L.latLngBounds(bounds[0]));
      MAP.fitBounds(all, PAD);
    } else {
      try { MAP.fitBounds(layer.getBounds(), PAD); } catch (e) { /* empty layer */ }
    }
  }

  async function drawScopeOutline(comm) {
    // Outline the community's parent admin units (in reference space) so it is
    // clear which area the candidate places were drawn from.
    const lv = hierLevels();
    const drawn = [];
    for (let i = 0; i < lv.length; i++) {
      const val = normName((comm.parents || {})[lv[i]]);
      if (!val) continue;
      const b = await loadBoundaries(lv[i]);
      if (!b || !b.geojson || S.stage !== 4) continue;
      const feats = (b.geojson.features || []).filter(f => normName((f.properties || {})._ref) === val);
      if (!feats.length) continue;
      const deepest = i === lv.length - 1;
      const lyr = L.geoJSON({ type: 'FeatureCollection', features: feats }, {
        style: { color: '#111110', weight: deepest ? 2 : 1, dashArray: deepest ? null : '6 4', opacity: deepest ? 0.9 : 0.5, fillColor: '#111110', fillOpacity: deepest ? 0.04 : 0 },
        interactive: false,
      }).addTo(LAYERS.admin);
      drawn.push({ level: lv[i], name: (feats[0].properties || {})._name || val, bounds: lyr.getBounds(), deepest });
    }
    return drawn;
  }

  async function drawLinkMap() {
    if (!MAP || S.stage !== 4) return;
    clearLayers();
    const c = S.comm;
    const comm = currentComm();
    const cands = c.cands;
    $('#maphint').textContent = c.pin ? `[ point at ${c.pin.lat.toFixed(4)}, ${c.pin.lon.toFixed(4)} ]` : (comm && comm.status === 'pending' ? '[ click a dot to pick it · click anywhere to place a point ]' : '[ pick an unlinked community ]');
    const keep = drawLinkMap._keep;
    drawLinkMap._keep = false;
    let scope = [];
    if (comm && form().restrict !== false) {
      scope = await drawScopeOutline(comm);
      if (S.stage !== 4) return;
      const chip = $('#scope-chip');
      if (chip) chip.textContent = scope.length ? 'places inside ' + scope.map(x => x.name).join(' › ') : 'places inside ' + (comm.path || 'the parent admin unit') + ' (no boundary drawn)';
    }
    const candIds = new Set(cands.map(x => x.ref_id));
    const pts = [];
    // grey (unlinked) dots first, black (linked) dots after so they stay on top
    [...c.places].sort((a, b) => (a.linked ? 1 : 0) - (b.linked ? 1 : 0)).forEach(p => {
      if (candIds.has(p.id)) return;
      const m = L.circleMarker([p.lat, p.lon], { radius: p.linked ? 6 : 4.5, color: '#fff', weight: 1, fillColor: p.linked ? '#111110' : '#c4c4bd', fillOpacity: 1 })
        .bindTooltip(p.name, { permanent: true, direction: 'bottom', offset: [0, 4], className: 'dot-label', interactive: false });
      if (!p.linked && comm && comm.status === 'pending') m.on('click', e => { L.DomEvent.stopPropagation(e); pickFromMap(p); });
      m.addTo(LAYERS.places);
      DOT_LABELS.push({ marker: m, name: p.name, linked: !!p.linked });
      pts.push([p.lat, p.lon]);
    });
    placeLabels();
    cands.forEach((cd, i) => {
      if (cd.lat === '' || cd.lat == null) return;
      const on = i === c.candSel && !c.pin;
      const size = on ? 30 : 24;
      const icon = L.divIcon({ className: '', html: `<div class="cand-marker ${on ? 'on' : ''}" style="width:${size}px;height:${size}px">${i + 1}</div>`, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
      L.marker([Number(cd.lat), Number(cd.lon)], { icon, zIndexOffset: on ? 1000 : 500 }).bindTooltip(`${i + 1} · ${cd.ref_name_raw || cd.ref_name} · ${fmt(cd.score)}`, { direction: 'top', className: 'poly-label' })
        .on('click', e => { L.DomEvent.stopPropagation(e); S.comm.candSel = i; S.comm.pin = null; render(); }).addTo(LAYERS.cands);
      pts.push([Number(cd.lat), Number(cd.lon)]);
    });
    if (c.pin) {
      const icon = L.divIcon({ className: '', html: '<div class="pin-marker" style="width:22px;height:22px"></div>', iconSize: [22, 22], iconAnchor: [11, 11] });
      L.marker([c.pin.lat, c.pin.lon], { icon, interactive: false }).addTo(LAYERS.pin);
    }
    if (!keep) {
      const deepest = scope.find(x => x.deepest) || scope[scope.length - 1];
      let bb = pts.length ? L.latLngBounds(pts) : null;
      if (deepest) bb = bb ? bb.extend(deepest.bounds) : deepest.bounds;
      if (bb) { try { MAP.fitBounds(bb, Object.assign({ maxZoom: 12 }, PAD)); } catch (e) { /* ignore */ } }
    }
  }
  // Show a dot's label only where it will not collide with a label already shown.
  // Linked (black) dots get first claim on the space; the rest fill in as zoom spreads them out.
  const DOT_LABELS = [];
  function placeLabels() {
    if (!MAP || !DOT_LABELS.length) return;
    const size = MAP.getSize();
    const taken = [];
    // numbered candidate markers and the dropped pin own their space first
    [LAYERS.cands, LAYERS.pin].forEach(g => g && g.eachLayer(l => { if (!l.getLatLng) return; const pt = MAP.latLngToContainerPoint(l.getLatLng()); taken.push({ l: pt.x - 16, t: pt.y - 16, r: pt.x + 16, b: pt.y + 16 }); }));
    const order = [...DOT_LABELS].sort((a, b) => (b.linked ? 1 : 0) - (a.linked ? 1 : 0) || a.name.localeCompare(b.name));
    order.forEach(d => {
      const tt = d.marker.getTooltip(); const el = tt && tt.getElement(); if (!el) return;
      const pt = MAP.latLngToContainerPoint(d.marker.getLatLng());
      const w = d.name.length * 6.4 + 6, h = 16;
      const box = { l: pt.x - w / 2, t: pt.y + 8, r: pt.x + w / 2, b: pt.y + 8 + h };
      const onScreen = box.r > 0 && box.l < size.x && box.b > 0 && box.t < size.y;
      const clash = onScreen && taken.some(o => box.l < o.r && box.r > o.l && box.t < o.b && box.b > o.t);
      const show = onScreen && !clash;
      el.classList.toggle('off', !show);
      if (show) taken.push(box);
    });
  }
  function pickFromMap(p) {
    const c = S.comm;
    const comm = currentComm();
    const at = c.cands.findIndex(x => x.ref_id === p.id);
    if (at >= 0) { c.candSel = at; }
    else { c.cands.push({ ref_id: p.id, ref_key: p.id, ref_name: p.name, ref_name_raw: p.name, score: null, lat: String(p.lat), lon: String(p.lon), ref_parents: { admin: p.admin }, fromMap: true }); c.candSel = c.cands.length - 1; }
    c.pin = null;
    drawLinkMap._keep = true;
    render();
  }

  // ---------- data loading per stage ----------
  async function loadState() { const d = await api('/api/state'); return d; }
  async function enterStage(n) {
    if (!canReach(n)) return;
    S.stage = n;
    S.logOpen = false;
    if (n === 2) {
      if (!S.st.has_lookups) { const d = await api('/api/lookups', { json: {}, msg: 'Running exact and near-exact matching at every level' }); if (!d) { S.stage = 1; render(); return; } }
      if (!S.level || !hierLevels().includes(S.level)) S.level = hierLevels()[0];
      if (!hierLevels().length) { toast('no admin levels mapped — skipping to matching'); S.stage = 3; render(); return; }
      await loadLevel();
    } else if (n === 3) {
      if (form().ran) await loadHist();
    } else if (n === 4) {
      await loadComms();
    } else if (n === 5) {
      const d = await api('/api/review', { msg: 'Joining coordinates onto the community list' }); if (d) S.review.rows = d.rows;
    }
    render();
  }
  async function loadLevel() {
    const d = await api('/api/level/' + encodeURIComponent(S.level), { msg: `Scoring unmatched ${S.level} names against the places file` });
    if (!d) return;
    S.lvl = d;
    S.adminCandSel = null; S.adminCandQuery = '';
    await loadAdminCands();
  }
  async function loadAdminCands() {
    const cur = currentAdmin();
    S.adminCands = [];
    if (cur && cur.status === 'pending') {
      const msg = `Looking for fuzzy matches for ${cur.name} at the ${S.level} level`;
      const d = await api(`/api/candidates?level=${encodeURIComponent(S.level)}&key=${encodeURIComponent(cur.key)}&top=0&restrict=1`, { msg });
      if (d) S.adminCands = d.candidates;
      if (!S.adminCands.length) {
        const d2 = await api(`/api/candidates?level=${encodeURIComponent(S.level)}&key=${encodeURIComponent(cur.key)}&top=0&restrict=0`, { msg: msg + ' (whole country)' });
        if (d2 && d2.candidates.length) { S.adminCands = d2.candidates; }
      }
    }
  }
  async function loadHist() {
    const d = await api('/api/histogram?restrict=' + (form().restrict === false ? '0' : '1'), { msg: 'Scoring the best candidate for every unmatched community' });
    if (d) S.hist = d;
  }
  async function loadComms(keepSel) {
    const d = await api('/api/unlinked' + (S.comm.all ? '?all=1' : ''));
    if (!d) return;
    S.comm.rows = d.rows;
    if (!keepSel) S.comm.sel = null;
    await loadCommDetail();
  }
  async function loadCommDetail() {
    const comm = currentComm();
    const c = S.comm;
    c.cands = []; c.candSel = 0; c.pin = null; c.places = [];
    if (!comm) return;
    const restrict = form().restrict === false ? '0' : '1';
    const [cd, pl] = await Promise.all([
      comm.status === 'pending' ? api(`/api/candidates?level=leaf&key=${encodeURIComponent(comm.id)}&top=3&restrict=${restrict}`, { msg: `Ranking candidate places for ${comm.name}` }) : Promise.resolve(null),
      api(`/api/places?target=${encodeURIComponent(comm.id)}`),
    ]);
    if (cd) c.cands = cd.candidates;
    if (pl) c.places = pl.points;
  }
  async function loadGazCountries() {
    if (S.gaz.countries.length) return;
    const d = await api('/api/gazetteer/countries');
    if (d) S.gaz.countries = d.countries;
  }

  // ---------- actions ----------
  const actions = {
    stage: el => enterStage(Number(el.dataset.n)),
    sub: el => { S.sub = Number(el.dataset.n); render(); },
    back1: () => { if (S.sub > 1) { S.sub -= 1; render(); } },
    next1: async () => {
      if (S.sub === 1 && fileReady()) { S.sub = 2; await loadGazCountries(); render(); return; }
      if (S.sub === 2 && placesReady() && paired()) { S.sub = 3; if (S.st.has_lookups) await loadHist(); render(); return; }
      if (S.sub === 3) { const d = await api('/api/lookups', { json: {}, msg: 'Running exact and near-exact matching at every level' }); if (d) { S.level = hierLevels()[0]; await enterStage(2); } }
    },
    'pick-file': el => {
      const role = el.dataset.role;
      const inp = $('#file-' + role);
      inp.dataset.label = el.dataset.label || '';
      inp.value = ''; inp.click();
    },
    'add-level': async () => { const h = (form().target_hierarchy || []).slice(); h.push({ column: '', label: h.length === 0 ? 'region' : h.length === 1 ? 'district' : 'level' + (h.length + 1) }); await saveForm({ target_hierarchy: h }); render(); },
    'rm-level': async el => { const i = Number(el.dataset.i); const h = (form().target_hierarchy || []).slice(); h.splice(i, 1); await saveForm({ target_hierarchy: h }); render(); },
    'pick-gaz': async () => { S.gaz.open = true; await loadGazCountries(); await saveForm({ ref_source: 'gazetteer' }); render(); },
    'pick-upload': async () => { S.gaz.open = false; await saveForm({ ref_source: 'upload' }); render(); if (!S.st.ref) actions['pick-file']({ dataset: { role: 'ref' } }); },
    'gaz-admin1': async () => { const d = await api('/api/gazetteer/admin1', { json: { iso3: S.gaz.iso3 } }); if (d) { S.gaz.admin1 = d.admin1; S.gaz.sel = []; S.gaz.preview = null; S.gaz.log = [d.cached ? 'boundaries from cache' : 'boundaries downloaded']; } render(); },
    'gaz-all': () => { S.gaz.sel = S.gaz.admin1.slice(); render(); },
    'gaz-none': () => { S.gaz.sel = []; render(); },
    'gaz-preview': async () => { const d = await api('/api/gazetteer/preview', { json: { iso3: S.gaz.iso3, admin1: S.gaz.sel } }); if (d) S.gaz.preview = d; render(); },
    'gaz-fetch': async () => {
      for (const a of S.gaz.sel) {
        S.gaz.log.push('fetching OSM for ' + a + '…'); render();
        const d = await api('/api/gazetteer/fetch-osm', { json: { iso3: S.gaz.iso3, admin1: a }, msg: `Fetching OpenStreetMap places for ${a}` });
        S.gaz.log[S.gaz.log.length - 1] = d ? `${a}: ${d.count} OSM places${d.cached ? ' (cached)' : ''}` : `${a}: failed`;
        render();
      }
    },
    'tag-places': async () => {
      const lv = hierLevels();
      const d = await api('/api/tag-places', { json: {}, msg: `Placing every named point inside the ${lv.join(' and ')} boundaries` });
      if (d) { const st = d.tagging.stats; toast(`${d.tagging.rows} places tagged: ` + Object.keys(st).map(k => `${k} ${st[k].inside + st[k].snapped} in / ${st[k].outside} out`).join(', '), 6000); Object.keys(BOUNDS_CACHE).forEach(k => delete BOUNDS_CACHE[k]); }
      render();
    },
    'suggest-levels': async () => {
      const d = await api('/api/boundary-levels/suggest', { json: {}, msg: 'Comparing geoBoundaries ADM1–ADM3 names with your admin names' });
      if (d) toast('suggested: ' + Object.entries(d.boundary_levels).map(([k, v]) => `${k} → ${v}`).join(', '), 5000);
      render();
    },
    'gaz-build': async () => {
      const d = await api('/api/gazetteer/build', { json: { iso3: S.gaz.iso3, admin1: S.gaz.sel }, msg: 'Assembling named places from GeoNames and OSM' });
      if (d) { S.gaz.log = d.log; S.gaz.open = false; toast(`${d.count} named places built`); }
      render();
    },
    'level': async el => { const l = el.dataset.l; if (l === S.level) return; S.level = l; S.adminSel = null; await loadLevel(); render(); },
    'level-next': async () => {
      const lv = hierLevels(); const i = lv.indexOf(S.level);
      const nextName = i < lv.length - 1 ? `the ${lv[i + 1]} level` : 'the community level';
      const d = await api('/api/lookups', { json: {}, msg: `Re-running exact matching so ${nextName} reflects the harmonized ${S.level} names` }); if (!d) return;
      if (i < lv.length - 1) { S.level = lv[i + 1]; S.adminSel = null; await loadLevel(); render(); }
      else await enterStage(3);
    },
    'admin-sel': async el => { S.adminSel = el.dataset.key; S.adminCandSel = null; S.adminCandQuery = ''; await loadAdminCands(); render(); },
    'admin-cand': el => { S.adminCandSel = el.dataset.key; render(); },
    'admin-link': async () => {
      const cur = currentAdmin(); if (!cur) return;
      const picked = S.adminCandSel || (S.adminCands[0] && S.adminCands[0].ref_key);
      const cand = S.adminCands.find(c => c.ref_key === picked); if (!cand) return;
      const d = await api('/api/link', { json: { level: S.level, target_key: cur.key, ref_key: cand.ref_key, score: cand.score, target_name: cur.name }, msg: `Linking ${cur.name} → ${cand.ref_name}` });
      if (d) { S.adminSel = null; await loadLevel(); render(); }
    },
    'admin-noeq': async () => { const cur = currentAdmin(); if (!cur) return; const d = await api('/api/no-equivalent', { json: { level: S.level, target_key: cur.key, target_name: cur.name } }); if (d) { S.adminSel = null; await loadLevel(); render(); } },
    'admin-unlink': async () => {
      const cur = currentAdmin(); if (!cur) return;
      const d = cur.status === 'linked' ? await api('/api/unlink', { json: { level: S.level, target_key: cur.key, target_name: cur.name } }) : await undoDecision(S.level, cur.key);
      if (d) { await loadLevel(); render(); }
    },
    'admin-skip': () => { const rows = S.lvl.rows.filter(r => r.status === 'pending'); const cur = currentAdmin(); const i = rows.findIndex(r => r.key === (cur && cur.key)); const nxt = rows[i + 1] || rows[0]; S.adminSel = nxt ? nxt.key : null; S.adminCandSel = null; loadAdminCands().then(render); },
    'accept-above': async el => {
      const lvl = S.stage === 2 ? S.level : 'leaf';
      const d = await api('/api/accept-above', { json: { level: lvl, threshold: Number(el.dataset.thr) }, msg: `Applying fuzzy suggestions above ${fmt(el.dataset.thr)} at the ${lvl} level` });
      if (d) { toast(d.applied ? `accepted ${d.applied} suggestions` : 'nothing at or above that score'); if (S.stage === 2) await loadLevel(); render(); }
    },
    run: async () => {
      const f = form();
      const d = await api('/api/run', { json: { threshold: f.threshold == null ? 85 : f.threshold, restrict: f.restrict !== false }, msg: `Running fuzzy matching at the community level, accepting above ${fmt(f.threshold == null ? 85 : f.threshold)}` });
      if (d) { S.hist = { bins: d.histogram, scores: null }; S.runLog = d.log; await loadHist(); }
      render();
    },
    'toggle-all': async () => { S.comm.all = !S.comm.all; await loadComms(true); render(); },
    'comm-grp': el => { const k = el.dataset.key; if (S.comm.open.has(k)) S.comm.open.delete(k); else S.comm.open.add(k); redrawList('.dock .panel-list', () => renderStage4()); positionFlyout(); },
    'comm-sel': async el => { S.comm.sel = el.dataset.id; await loadCommDetail(); render(); },
    cand: el => { S.comm.candSel = Number(el.dataset.i); S.comm.pin = null; drawLinkMap._keep = true; render(); },
    'clear-pin': () => { S.comm.pin = null; render(); },
    save: () => doSave(),
    skip: () => doSkip(),
    'comm-noeq': async () => { const comm = currentComm(); if (!comm) return; const d = await api('/api/no-equivalent', { json: { level: 'leaf', target_key: comm.id, target_name: comm.name } }); if (d) { await loadComms(); render(); } },
    'comm-unlink': async () => {
      const comm = currentComm(); if (!comm) return;
      let d = null;
      if (comm.status === 'pin') d = await api('/api/unpin', { json: { target_id: comm.id, target_name: comm.name } });
      else if (comm.status === 'linked') d = await api('/api/unlink', { json: { level: 'leaf', target_key: comm.id, target_name: comm.name } });
      else d = await undoDecision('leaf', comm.id);
      if (d) { S.comm.sel = comm.id; await loadComms(true); render(); }
    },
    filter: el => { S.review.filter = el.dataset.k; render(); },
    export: () => { window.location.href = '/api/download/output/geocoded.csv'; },
    undo: async () => { const d = await api('/api/undo', { json: {} }); if (d) { toast('undid · ' + d.undone); await refreshStage(); } },
    log: async () => { const d = await api('/api/history'); if (d) { S.log = d.entries; S.logOpen = true; renderDrawer(); } },
    'log-close': () => { S.logOpen = false; renderDrawer(); },
    reset: async () => { if (!confirm('Start a new project? Uploaded files, links and history for this session are deleted.')) return; const d = await api('/api/reset', { json: {} }); if (d) { S.stage = 1; S.sub = 1; S.level = null; S.lvl = null; S.hist = null; S.comm = { rows: [], all: false, sel: null, cands: [], candSel: 0, pin: null, query: '', places: [], open: new Set(), revealed: null }; S.review.rows = []; S.gaz.open = false; render(); } },
  };
  async function undoDecision(level, key) {
    // "no equivalent" has no direct clear endpoint; go through undo if it was the last action, else re-link path via unlink is not applicable.
    const d = await api('/api/history');
    const last = d && d.entries.find(e => e.inverse);
    if (last && last.text.includes('no equivalent')) return api('/api/undo', { json: {} });
    toast('use Undo/History to clear an older "no equivalent" decision', 4000);
    return null;
  }
  async function refreshStage() {
    if (S.stage === 2) await loadLevel();
    else if (S.stage === 3) await loadHist();
    else if (S.stage === 4) await loadComms(true);
    else if (S.stage === 5) { const d = await api('/api/review'); if (d) S.review.rows = d.rows; }
    render();
  }
  async function doSave() {
    const comm = currentComm(); if (!comm || comm.status !== 'pending') return;
    const c = S.comm;
    let d = null;
    if (c.pin) d = await api('/api/pin', { json: { target_id: comm.id, lat: c.pin.lat, lon: c.pin.lon, target_name: comm.name } });
    else if (c.cands[c.candSel]) { const cd = c.cands[c.candSel]; d = await api('/api/link', { json: { level: 'leaf', target_key: comm.id, ref_key: cd.ref_id, score: cd.score, target_name: comm.name } }); }
    if (!d) return;
    // advance to the next unlinked after this one
    const pend = c.rows.filter(r => r.status === 'pending');
    const i = pend.findIndex(r => r.id === comm.id);
    const nxt = pend[i + 1] || pend.find(r => r.id !== comm.id) || null;
    S.comm.sel = nxt ? nxt.id : null;
    await loadComms(true); render();
  }
  function doSkip() {
    const c = S.comm; const comm = currentComm();
    const pend = c.rows.filter(r => r.status === 'pending');
    const i = pend.findIndex(r => comm && r.id === comm.id);
    const nxt = pend[i + 1] || pend[0];
    S.comm.sel = nxt ? nxt.id : null;
    loadCommDetail().then(render);
  }

  // ---------- events ----------
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const fn = actions[el.dataset.act];
    if (fn) { e.preventDefault(); fn(el); }
  });
  document.addEventListener('change', async e => {
    const t = e.target;
    if (t.dataset.field) {
      let v = t.type === 'checkbox' ? t.checked : t.value;
      if (t.dataset.field === 'threshold') v = Number(v);
      await saveForm({ [t.dataset.field]: v });
      if (t.dataset.field === 'boundaries_source' || t.dataset.field === 'country') { Object.keys(BOUNDS_CACHE).forEach(k => delete BOUNDS_CACHE[k]); }
      if (t.dataset.field === 'country' && v && hierLevels().length) { await actions['suggest-levels'](); return; }
      if (t.dataset.field === 'restrict' && S.stage === 3 && form().ran) await loadHist();
      render();
    } else if (t.dataset.hier) {
      const which = t.dataset.hier === 'target' ? 'target_hierarchy' : 'ref_hierarchy';
      const h = (form()[which] || []).map(x => Object.assign({}, x));
      const i = Number(t.dataset.i);
      while (h.length <= i) h.push({ column: '', label: '' });
      h[i][t.dataset.k] = t.value;
      await saveForm({ [which]: h }); render();
    } else if (t.dataset.gaz === 'iso3') { S.gaz.iso3 = t.value; S.gaz.admin1 = []; S.gaz.sel = []; render(); }
    else if (t.dataset.gazAdmin !== undefined) { const a = t.dataset.gazAdmin; if (t.checked) { if (!S.gaz.sel.includes(a)) S.gaz.sel.push(a); } else S.gaz.sel = S.gaz.sel.filter(x => x !== a); render(); }
    else if (t.dataset.adm) { await api('/api/boundary-levels', { json: { label: t.dataset.adm, adm: t.value } }); Object.keys(BOUNDS_CACHE).forEach(k => delete BOUNDS_CACHE[k]); render(); }
    else if (t.dataset.bprop) { await api('/api/boundary-property', { json: { label: t.dataset.bprop, name_property: t.value } }); Object.keys(BOUNDS_CACHE).forEach(k => delete BOUNDS_CACHE[k]); render(); }
  });
  document.addEventListener('input', e => {
    const t = e.target;
    if (t.dataset.field === 'threshold') { const l = $('#thr-label'); if (l) l.textContent = fmt(t.value); return; }
    if (t.dataset.input === 'adminQuery') { S.adminQuery = t.value; redrawList('.dock .panel-list', () => renderStage2()); }
    else if (t.dataset.input === 'adminCandQuery') { S.adminCandQuery = t.value; redrawList('.flyout .panel-list', () => renderStage2()); }
    else if (t.dataset.input === 'commQuery') { S.comm.query = t.value; redrawList('.dock .panel-list', () => renderStage4()); positionFlyout(); }
    else if (t.dataset.input === 'reviewQuery') { S.review.query = t.value; redrawList('.table .rows', () => renderStage5()); }
  });
  document.addEventListener('focusout', async e => {
    const t = e.target;
    if (t.dataset && t.dataset.field === 'project_name') { const v = t.textContent.trim() || 'Untitled project'; if (v !== form().project_name) await saveForm({ project_name: v }); }
  });
  function redrawList(sel, renderFn) {
    // re-render only the list portion so the search box keeps focus
    const tmp = document.createElement('div'); tmp.innerHTML = renderFn();
    const fresh = tmp.querySelector(sel); const cur = $('#pane ' + sel);
    if (fresh && cur) cur.innerHTML = fresh.innerHTML;
  }
  ['target', 'ref', 'boundaries'].forEach(role => {
    $('#file-' + role).addEventListener('change', async e => {
      const f = e.target.files[0]; if (!f) return;
      const fd = new FormData(); fd.append('file', f); fd.append('role', role);
      if (role === 'boundaries') fd.append('label', e.target.dataset.label || '');
      const d = await api('/api/upload', { form: fd });
      if (d) {
        if (role === 'target' && !form().target_name_column) autoMapTarget();
        if (role === 'ref') { autoMapRef(); S.gaz.open = false; }
        toast(`${f.name} uploaded`);
        Object.keys(BOUNDS_CACHE).forEach(k => delete BOUNDS_CACHE[k]);
      }
      render();
    });
  });
  // drag & drop onto drop zones
  document.addEventListener('dragover', e => { const z = e.target.closest('.drop'); if (z) { e.preventDefault(); z.classList.add('over'); } });
  document.addEventListener('dragleave', e => { const z = e.target.closest('.drop'); if (z) z.classList.remove('over'); });
  document.addEventListener('drop', e => {
    const z = e.target.closest('.drop'); if (!z) return;
    e.preventDefault(); z.classList.remove('over');
    const f = e.dataTransfer.files[0]; if (!f) return;
    const inp = $('#file-' + z.dataset.role); inp.dataset.label = z.dataset.label || '';
    const dt = new DataTransfer(); dt.items.add(f); inp.files = dt.files; inp.dispatchEvent(new Event('change'));
  });
  function guess(cols, pats) { for (const p of pats) { const hit = cols.find(c => p.test(c)); if (hit) return hit; } return ''; }
  async function autoMapTarget() {
    const cols = (S.st.target || {}).columns || [];
    const name = guess(cols, [/^(community|village|name|facility|settlement|locality)/i, /name/i]);
    const a1 = guess(cols, [/admin_?1|adm1|region|province|state/i]);
    const a2 = guess(cols, [/admin_?2|adm2|district|iu$|lga|county|department/i]);
    const h = []; if (a1) h.push({ column: a1, label: 'region' }); if (a2) h.push({ column: a2, label: 'district' });
    await saveForm({ target_name_column: name, target_hierarchy: h });
  }
  async function autoMapRef() {
    const cols = (S.st.ref || {}).columns || [];
    const f = form();
    const patch = {};
    if (!f.ref_name_column) patch.ref_name_column = guess(cols, [/^name$/i, /place|name/i]);
    if (!f.ref_lat_column) patch.ref_lat_column = guess(cols, [/^lat/i, /latitude|^y$/i]);
    if (!f.ref_lon_column) patch.ref_lon_column = guess(cols, [/^lon|^lng/i, /longitude|^x$/i]);
    await saveForm(patch);
  }

  // keyboard (stage 4)
  window.addEventListener('keydown', e => {
    if (S.stage !== 4 || S.logOpen) return;
    if (e.target && (e.target.tagName === 'INPUT' || e.target.isContentEditable)) return;
    const c = S.comm;
    if (e.key >= '1' && e.key <= '9') { const i = Number(e.key) - 1; if (i < c.cands.length) { c.candSel = i; c.pin = null; drawLinkMap._keep = true; render(); } }
    else if (e.key === 'Enter') doSave();
    else if (e.key.toLowerCase() === 's') doSkip();
    else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); actions.undo(); }
  });

  // ---------- boot ----------
  (async function boot() {
    await loadState();
    if (!S.st) { $('#pane').innerHTML = '<div class="cand-empty">could not reach the server</div>'; return; }
    const ui = form().ui || {};
    if (S.st.target && S.st.ref) await loadGazCountries();
    if (form().ran && canReach(4)) { S.stage = 4; await loadComms(); }
    else if (S.st.has_lookups && canReach(2)) { S.stage = 2; S.level = hierLevels()[0]; if (hierLevels().length) await loadLevel(); else S.stage = 3; }
    else if (S.st.target) { S.stage = 1; S.sub = S.st.ref ? 2 : 1; }
    render();
  })();
})();
