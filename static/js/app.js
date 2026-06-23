'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  loaded: false,
  info: null,
  filters: null,
  infoInitialized: false,
};

// ── Sidebar toggle ────────────────────────────────────────────────────────────
document.getElementById('sidebarToggle').addEventListener('click', () => {
  document.getElementById('sidebar').classList.toggle('collapsed');
});
document.getElementById('sidebarClose').addEventListener('click', () => {
  document.getElementById('sidebar').classList.add('collapsed');
});

// ── Data-source radio → show/hide inputs ─────────────────────────────────────
document.querySelectorAll('input[name="source"]').forEach(r => {
  r.addEventListener('change', updateSourceInputs);
});

function updateSourceInputs() {
  const src = document.querySelector('input[name="source"]:checked').value;
  document.getElementById('gsheetInputs').classList.toggle('d-none', src !== 'gsheet');
  document.getElementById('urlInputs').classList.toggle('d-none', src !== 'url');
  document.getElementById('uploadInputs').classList.toggle('d-none', src !== 'upload');
}

// ── Load data ─────────────────────────────────────────────────────────────────
document.getElementById('loadDataBtn').addEventListener('click', loadData);
document.getElementById('clearCacheBtn').addEventListener('click', async () => {
  await fetch('/api/clear', { method: 'POST' });
  state.loaded = false;
  state.infoInitialized = false;
  loadData();
});

async function loadData() {
  const src = document.querySelector('input[name="source"]:checked').value;
  const statusEl = document.getElementById('loadStatus');
  const btn = document.getElementById('loadDataBtn');

  btn.disabled = true;
  statusEl.innerHTML = '<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Loading…</span>';

  try {
    let resp;
    if (src === 'gsheet') {
      resp = await fetch('/api/load/gsheet', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
    } else if (src === 'url') {
      const url = document.getElementById('csvUrl').value.trim();
      if (!url) {
        statusEl.innerHTML = '<span class="text-warning">Paste a CSV URL first.</span>';
        btn.disabled = false;
        return;
      }
      resp = await fetch('/api/load/url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
    } else {
      const file = document.getElementById('uploadFile').files[0];
      if (!file) {
        statusEl.innerHTML = '<span class="text-warning">Select a file first.</span>';
        btn.disabled = false;
        return;
      }
      const form = new FormData();
      form.append('file', file);
      resp = await fetch('/api/load/upload', { method: 'POST', body: form });
    }

    const data = await resp.json();
    if (!data.ok) {
      statusEl.innerHTML = `<span class="text-danger">${esc(data.error)}</span>`;
    } else {
      statusEl.innerHTML = `<span class="text-success"><i class="bi bi-check-circle-fill me-1"></i>${data.rows.toLocaleString()} rows loaded</span>`;
      state.loaded = true;
      state.infoInitialized = false;

      ['ic50TypeSection', 'viewSection', 'sortSection', 'filtersSection', 'applySection']
        .forEach(id => document.getElementById(id).classList.remove('d-none'));
      document.getElementById('clearCacheBtn').classList.remove('d-none');

      await renderView();
    }
  } catch (e) {
    statusEl.innerHTML = `<span class="text-danger">Error: ${esc(e.message)}</span>`;
  }
  btn.disabled = false;
}

// ── Metric change → dilution visibility + threshold preset labels ─────────────
document.querySelectorAll('input[name="metric"]').forEach(r => {
  r.addEventListener('change', () => {
    const isPct = document.getElementById('metricPctNeut').checked;
    document.getElementById('dilutionGroup').classList.toggle('d-none', !isPct);
    rebuildThresholdPresets(isPct);
  });
});

function rebuildThresholdPresets(isPct) {
  const labels = isPct ? ['≥50%', '≥75%', 'Custom'] : ['≥3.0', '≥3.5', 'Custom'];
  const vals   = isPct ? [50, 75, 'custom'] : [3.0, 3.5, 'custom'];
  const defaultThr = isPct ? 50 : 3.0;

  const container = document.getElementById('threshPresets');
  container.innerHTML = labels.map((lbl, i) =>
    `<button type="button" class="btn btn-outline-secondary btn-xs thr-preset${i === 0 ? ' active' : ''}"
             data-val="${vals[i]}">${lbl}</button>`
  ).join('');
  document.getElementById('customThreshold').value = defaultThr;
  wireThresholdPresets();
}

function wireThresholdPresets() {
  document.getElementById('threshPresets').querySelectorAll('.thr-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      document.getElementById('threshPresets').querySelectorAll('.thr-preset')
        .forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const custom = document.getElementById('customThreshold');
      if (btn.dataset.val === 'custom') {
        custom.classList.remove('d-none');
      } else {
        custom.classList.add('d-none');
        custom.value = btn.dataset.val;
      }
    });
  });
}
wireThresholdPresets();

// ── Encoding → threshold group ────────────────────────────────────────────────
document.querySelectorAll('input[name="encoding"]').forEach(r => {
  r.addEventListener('change', () => {
    const isThr = document.getElementById('encThreshold').checked;
    document.getElementById('thresholdGroup').classList.toggle('d-none', !isThr);
  });
});

// ── Layout → subgroup + curve visibility ─────────────────────────────────────
document.querySelectorAll('input[name="layout"]').forEach(r => {
  r.addEventListener('change', () => {
    const isSingle = document.getElementById('layoutSingle').checked;
    document.getElementById('subgroupGroup').classList.toggle('d-none', !isSingle);
    if (!isSingle) document.getElementById('curveSection').classList.add('d-none');
  });
});

// ── Apply button ──────────────────────────────────────────────────────────────
document.getElementById('applyBtn').addEventListener('click', renderView);

// ── Render ────────────────────────────────────────────────────────────────────
async function renderView() {
  if (!state.loaded) return;

  showLoading(true);
  clearError();

  const filters = gatherFilters();
  state.filters = filters;

  try {
    const resp = await fetch('/api/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(filters),
    });
    const data = await resp.json();

    if (!data.ok) { showError(data.error); showLoading(false); return; }

    updateDiagnostics(data.info);
    updateSidebarFromInfo(data.info, filters);
    renderCharts(data.results, filters);

    const isSingle = filters.view_mode === 'single';
    document.getElementById('curveSection').classList.toggle('d-none', !isSingle);
    if (isSingle && data.results.length > 0) {
      const r = data.results[0];
      updateCurveDropdowns(r.constructs, r.psvs);
      await loadCurve();
    }

    document.getElementById('welcomeState').classList.add('d-none');
    document.getElementById('diagnosticsPanel').classList.remove('d-none');

  } catch (e) {
    showError(e.message);
  }
  showLoading(false);
}

function gatherFilters() {
  const metric = document.querySelector('input[name="metric"]:checked').value;
  const dilSel = document.getElementById('dilutionSelect');
  const dilRaw = dilSel.value;
  const dilution = (metric === 'pct_neut') ? (dilRaw ? parseFloat(dilRaw) : 30.0) : null;

  const mode = document.querySelector('input[name="encoding"]:checked').value;
  const activePreset = document.querySelector('.thr-preset.active');
  const threshold = activePreset ? parseFloat(document.getElementById('customThreshold').value) : 50;
  const ge = document.getElementById('thrGe').checked;

  const view_mode = document.querySelector('input[name="layout"]:checked').value;
  const bucket = document.getElementById('bucketSelect').value || null;
  const subgroup = document.getElementById('subgroupSelect').value || 'All constructs';
  const sort_by = document.querySelector('input[name="sortby"]:checked').value;
  const sort_descending = document.querySelector('input[name="sortorder"]:checked').value === 'desc';
  const corrected_ic50 = document.getElementById('ic50Corrected').checked;
  const show_values = document.getElementById('showValues').checked;
  const use_geno = document.getElementById('useGeno').checked;

  const experiments = getChecked('expChecks');
  const groups      = getChecked('grpChecks');
  const psvs        = getChecked('psvChecks');

  return {
    metric, dilution, mode, threshold, ge, view_mode, bucket, subgroup,
    sort_by, sort_descending, corrected_ic50, show_values, use_geno,
    experiments: experiments.length ? experiments : null,
    groups:      groups.length ? groups : null,
    psvs:        psvs.length  ? psvs   : null,
  };
}

function getChecked(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return [];
  return Array.from(el.querySelectorAll('input[type="checkbox"]:checked')).map(c => c.value);
}

// ── Update sidebar from server info ──────────────────────────────────────────
function updateSidebarFromInfo(info, filters) {
  // Dilution
  const dilSel = document.getElementById('dilutionSelect');
  const dils = (info.all_dilutions && info.all_dilutions.length) ? info.all_dilutions : [30];
  dilSel.innerHTML = dils.map(d =>
    `<option value="${d}">1:${Math.round(d)}</option>`
  ).join('');
  // Select the value the server actually used, or fall back to first option
  const dilTarget = filters.dilution ?? dils[0];
  const dilOpt = [...dilSel.options].find(o => parseFloat(o.value) === dilTarget);
  if (dilOpt) dilSel.value = dilOpt.value;

  // Dose window
  const bucketSel = document.getElementById('bucketSelect');
  const buckets = [...(info.buckets_present || []), 'All (pooled)'];
  bucketSel.innerHTML = buckets.map(b =>
    `<option value="${b === 'All (pooled)' ? '' : b}">${b}</option>`
  ).join('');
  if (filters.bucket) bucketSel.value = filters.bucket;

  // Construct subgroup
  const subSel = document.getElementById('subgroupSelect');
  subSel.innerHTML = ['All constructs', ...(info.subgroups_present || [])].map(s =>
    `<option value="${esc(s)}">${esc(s)}</option>`
  ).join('');
  if (filters.subgroup) subSel.value = filters.subgroup;

  // Experiment / Group / PSV checkboxes — only build once per data load
  if (!state.infoInitialized) {
    state.infoInitialized = true;
    buildCheckboxGroup('expChecks', info.experiments);
    buildCheckboxGroup('grpChecks', info.groups);
    buildCheckboxGroup('psvChecks', info.psvs);
  }
}

function buildCheckboxGroup(containerId, items) {
  const el = document.getElementById(containerId);
  el.innerHTML = (items || []).map(item =>
    `<div class="form-check form-check-sm">
      <input class="form-check-input" type="checkbox" value="${esc(item)}"
             id="chk_${containerId}_${esc(item)}" checked>
      <label class="form-check-label" for="chk_${containerId}_${esc(item)}">${esc(item)}</label>
    </div>`
  ).join('');
}

// ── Render charts ─────────────────────────────────────────────────────────────
function renderCharts(results, filters) {
  const area = document.getElementById('chartsArea');

  if (!results.length) {
    area.innerHTML = '<div class="alert alert-warning">No data to display — check your filters.</div>';
    return;
  }

  if (filters.view_mode === 'single') {
    const r = results[0];
    area.innerHTML = `
      <div class="chart-card">
        <div class="chart-card-body">
          <div id="chart_main"></div>
          <p class="legend-caption">${legendText(filters.metric, filters.mode)}</p>
          <button class="btn btn-outline-secondary btn-sm mt-2" onclick="downloadCsv()">
            <i class="bi bi-download me-1"></i>Download this view (CSV)
          </button>
        </div>
      </div>`;
    mountPlotly('chart_main', r.fig, filters);
  } else {
    area.innerHTML = results.map((r, i) => `
      <div class="chart-card">
        <div class="chart-card-header" data-bs-toggle="collapse" data-bs-target="#chartBody_${i}">
          <i class="bi bi-chevron-down small"></i>
          <span>${esc(r.title)}</span>
        </div>
        <div class="collapse show" id="chartBody_${i}">
          <div class="chart-card-body">
            <div id="chart_${i}"></div>
            <p class="legend-caption">${legendText(filters.metric, filters.mode)}</p>
          </div>
        </div>
      </div>`
    ).join('');
    results.forEach((r, i) => mountPlotly(`chart_${i}`, r.fig, filters));
  }
}

function mountPlotly(divId, fig, filters) {
  const div = document.getElementById(divId);
  if (!div) return;

  // Keep the server-computed width so columns are properly spaced.
  // The chart-card has overflow-x:auto so it scrolls if wider than the panel.
  const layout = Object.assign({}, fig.layout, { autosize: false });

  Plotly.react(div, fig.data, layout, { responsive: false, displaylogo: false });

  if (filters.view_mode === 'single') {
    div.on('plotly_click', d => {
      if (!d.points || !d.points[0]) return;
      selectCurve(d.points[0].y, d.points[0].x);
    });
  }
}

function legendText(metric, mode) {
  if (mode === 'threshold')
    return '🟩 ≥ threshold (hit) &nbsp;·&nbsp; ⬜ tested, below threshold &nbsp;·&nbsp; ✕ not tested &nbsp;·&nbsp; ⬛ No Neutralization';
  if (metric === 'log10_ic50')
    return 'Color = log₁₀(IC50): red (low) → green (high potency) &nbsp;·&nbsp; ⬛ No Neutralization &nbsp;·&nbsp; ✕ not tested';
  return 'Color = % neutralization: white → green &nbsp;·&nbsp; ✕ not tested';
}

// ── Diagnostics ───────────────────────────────────────────────────────────────
function updateDiagnostics(info) {
  document.getElementById('dRawRows').textContent       = fmt(info.n_raw);
  document.getElementById('dAfterFilter').textContent   = fmt(info.n_after_filter);
  document.getElementById('dUnknownBucket').textContent = fmt(info.n_unknown_bucket);
  document.getElementById('dUncategorized').textContent = fmt(info.n_uncategorized);

  const warn = document.getElementById('diagWarnings');
  warn.innerHTML = '';
  if (info.n_unknown_bucket)
    warn.innerHTML += `<div class="alert alert-warning small py-1 px-2 mb-1">${esc(info.n_unknown_bucket + ' rows have no Prime/Boost1/Boost2 mapping (excluded). Add their keys to BUCKET_MAP in hcv_data.py.')}</div>`;
  if (info.n_uncategorized)
    warn.innerHTML += `<div class="alert alert-warning small py-1 px-2 mb-1">${esc(info.n_uncategorized + " rows fell into 'Uncategorized'. Extend SUBGROUP_RULES in hcv_data.py.")}</div>`;

  const colMap = info.columns || {};
  const colList = typeof colMap === 'object' && !Array.isArray(colMap)
    ? Object.entries(colMap).filter(([,v]) => v).map(([k,v]) => `${k}=${v}`)
    : colMap;
  document.getElementById('diagColumns').textContent =
    'Detected columns: ' + (Array.isArray(colList) ? colList : []).join(', ');
}

// ── Curve ─────────────────────────────────────────────────────────────────────
function updateCurveDropdowns(constructs, psvs) {
  const cc = document.getElementById('curveConstruct');
  const pp = document.getElementById('curvePsv');
  const prevC = cc.value, prevP = pp.value;

  cc.innerHTML = constructs.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  pp.innerHTML = psvs.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join('');

  if ([...cc.options].some(o => o.value === prevC)) cc.value = prevC;
  if ([...pp.options].some(o => o.value === prevP)) pp.value = prevP;
}

document.getElementById('curveConstruct').addEventListener('change', loadCurve);
document.getElementById('curvePsv').addEventListener('change', loadCurve);

function selectCurve(construct, psv) {
  const cc = document.getElementById('curveConstruct');
  const pp = document.getElementById('curvePsv');
  if ([...cc.options].some(o => o.value === construct)) cc.value = construct;
  if ([...pp.options].some(o => o.value === psv)) pp.value = psv;
  loadCurve();
}

async function loadCurve() {
  if (!state.filters) return;
  const construct = document.getElementById('curveConstruct').value;
  const psv = document.getElementById('curvePsv').value;
  if (!construct || !psv) return;

  const f = state.filters;
  try {
    const resp = await fetch('/api/curve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        construct, psv,
        bucket: f.bucket,
        metric: f.metric,
        threshold: f.threshold,
        corrected_ic50: f.corrected_ic50,
        experiments: f.experiments,
        groups: f.groups,
        psvs: f.psvs,
      }),
    });
    const data = await resp.json();
    if (data.ok) {
      const layout = Object.assign({}, data.fig.layout, { autosize: true });
      delete layout.width;
      Plotly.react('curvePlot', data.fig.data, layout, { responsive: true, displaylogo: false });
    }
  } catch (e) {
    console.warn('Curve load error:', e);
  }
}

// ── Download ──────────────────────────────────────────────────────────────────
async function downloadCsv() {
  if (!state.filters) return;
  const resp = await fetch('/api/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.filters),
  });
  if (!resp.ok) { alert('Download failed'); return; }
  const blob = await resp.blob();
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url;
  a.download = 'hcv_view.csv';
  a.click();
  URL.revokeObjectURL(url);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function showLoading(on) {
  document.getElementById('loadingSpinner').classList.toggle('d-none', !on);
  document.getElementById('chartsArea').classList.toggle('d-none', on);
  if (on) document.getElementById('curveSection').classList.add('d-none');
}

function showError(msg) {
  const el = document.getElementById('errorMsg');
  el.textContent = msg;
  el.classList.remove('d-none');
}

function clearError() {
  document.getElementById('errorMsg').classList.add('d-none');
}

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmt(n) {
  return n == null ? '—' : Number(n).toLocaleString();
}
