/* Renders every number on the page from data/leaderboard.json.
   To refresh the site, edit that JSON — do not edit index.html.
   tools/update_site.py regenerates the headline, themes and betting blocks. */

/* Exact zero gets no sign — a signed "+0.000" reads as a claim the value is
   positive, and both the consensus reference and a tied theme score are 0. */
const sign = v => (v === 0 ? '' : v > 0 ? '+' : '−');
const fmt = (v, d = 3) => sign(v) + Math.abs(v).toFixed(d);
const cls = v => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');

/* '2026-08-25' -> 'Aug 25, 2026'. Parsed by hand so the label cannot slip a
   day for viewers west of Greenwich. */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}, ${m[1]}` : (iso || '—');
}

/* index.html and main.js are cached independently by Pages (max-age=600), so a
   browser can briefly hold a new page with a stale script, or the reverse. A
   renderer that assumed its element existed threw on null and the catch below
   replaced the leaderboard with a "could not load" message. Look elements up
   through byId instead: a missing one skips its own block and leaves the rest
   of the page intact. */
function byId(id) {
  const el = document.getElementById(id);
  if (!el) console.warn(`LiveMacroEval: #${id} is missing; skipping that block.`);
  return el;
}

const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const link = (name, url) =>
  url ? `<a href="${esc(url)}">${esc(name)}</a>` : esc(name);

function scoreCell(v, d = 3) {
  return `<span class="score ${cls(v)}">${fmt(v, d)}</span>`;
}

/* Full-width diverging bar, zero pinned at the centre. */
function bar(v, max) {
  const w = (Math.abs(v) / max) * 50;
  const side = v >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
  return `<div class="bartrack"><div class="barzero" style="left:50%"></div>
          <div class="bar ${v >= 0 ? 'pos' : 'neg'}" style="${side}"></div></div>`;
}

/* Hairline under a matrix cell, same diverging geometry. */
function cellBar(v, max) {
  const w = (Math.abs(v) / max) * 50;
  const side = v >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
  return `<span class="cellbar"><i class="${v >= 0 ? 'pos' : 'neg'}" style="${side}"></i></span>`;
}

/* One leaderboard row: rank, name, score, interval, bar. No event count
   (user decision 2026-09-03). The consensus reference has no interval. */
function leaderRow(r, rank, isRef, isLead, max) {
  const kindLabel = { llm: 'LLM', human: 'Human', econ: 'Econ' }[r.kind] || '';
  return `<tr class="${isRef ? 'ref' : isLead ? 'lead' : ''}">
    <td class="rank">${isRef ? '—' : rank}</td>
    <td><span class="rowname">${esc(r.name)}</span><span class="kind ${r.kind}">${kindLabel}</span>
        ${r.note ? `<span class="rownote">${esc(r.note)}</span>` : ''}</td>
    <td class="num">${scoreCell(r.score)}</td>
    <td class="num ci">${r.ci ? `[${fmt(r.ci[0])}, ${fmt(r.ci[1])}]` : '—'}</td>
    <td class="barcell">${bar(r.score, max)}</td>
  </tr>`;
}

function panelHeader(label) {
  return `<tr class="grouphdr"><td colspan="5">${esc(label)}</td></tr>`;
}

/* A strip of tab buttons. `views` is [{key, label}]; `pick` receives the key.
   Used by the leaderboard (one tab per target month) and each LiveBetting
   chart (one per betting window). Real buttons with tab roles, so a keyboard
   reaches them and a screen reader hears which one is selected. The strip
   scrolls sideways rather than wrapping, so it stays one line as months
   accumulate; the first tab (the aggregate) is sticky at the left edge. */
function tabStrip(mount, views, active, pick) {
  mount.innerHTML = '';
  mount.setAttribute('role', 'tablist');
  views.forEach((v, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = i === 0 ? 'tab first' : 'tab';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', v.key === active ? 'true' : 'false');
    b.textContent = v.label;
    b.addEventListener('click', () => pick(v.key));
    mount.appendChild(b);
  });
}

/* Caption for a period panel: the quarter, and for a short or in-progress
   quarter which months it actually holds. */
function periodHeader(p) {
  let t = p.current ? `Current quarter ${p.label}` : `Target quarter ${p.label}`;
  if (p.covers) t += ` — ${p.covers}`;
  return t;
}

/* The leaderboard is tabbed: "All quarters" is the running score, and each
   quarter tab is the same statistic on that quarter's releases only, newest
   first so the quarter in progress is one click away. */
function renderLeaderboard(h) {
  const body = byId('lb-body');
  if (!body) return;
  const views = [{ key: 'all', label: 'All quarters', rows: h.rows, window: h.window,
                   note: h.note }]
    .concat((h.periods || []).slice().reverse().map(p => ({
      key: p.key, label: p.current ? `${p.label} · so far` : p.label, rows: p.rows,
      window: periodHeader(p), note: h.period_note || h.note,
    })));
  let active = 'all';
  const tabs = byId('lb-tabs');

  function draw() {
    const v = views.find(x => x.key === active) || views[0];
    const max = Math.max(...v.rows.map(r => Math.abs(r.score))) || 1;
    const html = [panelHeader(v.window)];
    const llm = v.rows.filter(x => x.kind === 'llm').map(x => x.score);
    const bestH = llm.length ? Math.max(...llm) : NaN;
    let rank = 0;
    for (const r of v.rows) {
      const isRef = r.ci === null && r.score === 0;
      if (!isRef) rank++;
      html.push(leaderRow(r, rank, isRef, r.kind === 'llm' && r.score === bestH, max));
    }
    body.innerHTML = html.join('');
    const note = byId('lb-note');
    if (note) note.textContent = v.note;
  }
  function pick(key) {
    active = key;
    if (tabs) tabStrip(tabs, views, active, pick);
    draw();
  }
  if (tabs && views.length > 1) tabStrip(tabs, views, active, pick);
  draw();
}

/* Tool and agent design: its own table. One row per configuration, each
   naming the arm it is; no CI column, since these rows carry none. The bar
   scale is its own -- the panel is a coverage-matched comparison among its
   three rows, not against the leaderboard. */
function agentRow(r, rank, isRef, isLead, max) {
  const kindLabel = { llm: 'LLM', human: 'Human', econ: 'Econ' }[r.kind] || '';
  const sub = r.model || r.note;
  return `<tr class="${isRef ? 'ref' : isLead ? 'lead' : ''}">
    <td class="rank">${isRef ? '—' : rank}</td>
    <td><span class="rowname">${esc(r.name)}</span><span class="kind ${r.kind}">${kindLabel}</span>
        ${sub ? `<span class="rownote">${esc(sub)}</span>` : ''}</td>
    <td class="num">${scoreCell(r.score)}</td>
    <td class="barcell">${bar(r.score, max)}</td>
  </tr>`;
}

function renderAgentDesign(a) {
  const body = byId('ad-body');
  if (!body || !a) return;
  const max = Math.max(...a.rows.map(r => Math.abs(r.score))) || 1;
  const html = [`<tr class="grouphdr"><td colspan="4">${esc(a.window)}</td></tr>`];
  let rank = 0;
  for (const r of a.rows) {
    const isRef = r.kind === 'human';
    if (!isRef) rank++;
    html.push(agentRow(r, rank, isRef, !!r.best, max));
  }
  body.innerHTML = html.join('');
  const note = byId('ad-note');
  if (note) note.textContent = a.note;
}

/* Models down the side, themes across the top. Tabbed like the leaderboard:
   "All quarters", then each quarter newest first, with the same captions. */
function renderThemes(t) {
  const body = byId('th-body');
  if (!body) return;
  const head = byId('th-head');
  if (head) head.innerHTML =
    '<th>Model</th>' + t.columns.map(c => `<th class="num">${esc(c)}</th>`).join('');
  const views = [{ key: 'all', label: 'All quarters', rows: t.rows, window: t.window,
                   note: t.note }]
    .concat((t.periods || []).slice().reverse().map(p => ({
      key: p.key, label: p.current ? `${p.label} · so far` : p.label, rows: p.rows,
      window: periodHeader(p), note: t.period_note || t.note,
    })));
  let active = 'all';
  const tabs = byId('th-tabs');

  function draw() {
    const v = views.find(x => x.key === active) || views[0];
    const max = Math.max(...v.rows.flatMap(r => r.scores.map(Math.abs))) || 1;
    const html = [`<tr class="grouphdr"><td colspan="${t.columns.length + 1}">${esc(v.window)}</td></tr>`];
    for (const r of v.rows) {
      html.push(`<tr>
        <td><span class="rowname">${esc(r.name)}</span><span class="kind ${r.kind}">${
          { llm: 'LLM', human: 'Human', econ: 'Econ' }[r.kind] || ''}</span></td>
        ${r.scores.map(x => `<td class="cell">${scoreCell(x)}${cellBar(x, max)}</td>`).join('')}
      </tr>`);
    }
    body.innerHTML = html.join('');
    const tn = byId('th-note');
    if (tn) tn.textContent = v.note;
  }
  function pick(key) {
    active = key;
    if (tabs) tabStrip(tabs, views, active, pick);
    draw();
  }
  if (tabs && views.length > 1) tabStrip(tabs, views, active, pick);
  draw();
}

function renderIndicators(list) {
  if (!byId('ind-grid')) return;
  byId('ind-grid').innerHTML = list.map(t => `
    <div class="card">
      <span class="tag">${t.items.length} indicators</span>
      <h4>${esc(t.theme)}</h4>
      <p class="blurb">${esc(t.blurb)}</p>
      <ul>${t.items.map(i => `<li>${link(i.name, i.url)}</li>`).join('')}</ul>
    </div>`).join('');
}

function renderFed(list) {
  if (!byId('fed-list')) return;
  byId('fed-list').innerHTML = list.map(f =>
    `<li>${link(f.name, f.url)} <span class="ci">— ${esc(f.target)}</span></li>`).join('');
}

fetch('data/leaderboard.json?v=' + Date.now())
  .then(r => r.json())
  .then(d => {
    const lu = byId('last-updated'), nu = byId('next-update');
    if (lu) lu.textContent = fmtDate(d.last_updated);
    if (nu) nu.textContent = fmtDate(d.next_update);
    renderLeaderboard(d.headline);
    renderAgentDesign(d.agent_design);
    renderThemes(d.themes);
    renderIndicators(d.indicators);
    renderFed(d.comparators.fed);
  })
  .catch(e => {
    console.error(e);
    const body = byId('lb-body');
    if (!body) return;
    const msg = location.protocol === 'file:'
      ? 'This page reads its numbers with fetch(), which a browser blocks on file:// URLs. Serve the folder instead: <code>python3 -m http.server</code> in docs/, then open http://localhost:8000.'
      : `Could not load data/leaderboard.json (${esc(e.message || e)}). A hard reload usually fixes it — the page and the script are cached separately.`;
    body.innerHTML = `<tr><td colspan="5">${msg}</td></tr>`;
  });

/* ---------------------------------------------------------------- charts --
   The line figures are drawn here from data/series.json rather than shipped as
   PNGs, so they pick up the page's own type and palette and stay legible in
   both themes. Hand-rolled SVG on purpose: a charting library would be a
   ~200KB dependency for five static curves, and every candidate wants its own
   colour system, which is the thing we are trying to avoid.

   Series colours are the paper's own model palette, so a model reads the same
   on the site as in the figures. Baselines (Fed nowcasts, consensus, ARIMA)
   are dashed, matching the paper convention. */
const SERIES_COLORS = {
  'GPT-5': 'var(--accent)',
  'Claude-4.5-Sonnet': 'var(--warm)',
  'Qwen3-235B': 'var(--s-purple)',
  'Qwen3-80B': 'var(--s-slate)',
  'Claude Code multi-agent': 'var(--s-gold)',
  'GPT-5 (reasoned)': 'var(--s-teal-lt)',
  // the pipeline draws this arm in a pale apricot that has no contrast on
  // white, and purple and slate are Qwen's, which shares these charts
  'Claude Code agent': 'var(--s-rose)',
};
const BASELINE_COLORS = ['var(--s-blue)', 'var(--s-olive)', 'var(--s-brown)', 'var(--s-grey)'];

const SVG_NS = 'http://www.w3.org/2000/svg';
function el(name, attrs, text) {
  const n = document.createElementNS(SVG_NS, name);
  for (const k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) n.setAttribute(k, attrs[k]);
  if (text !== undefined) n.textContent = text;
  return n;
}

/* "Nice" axis bounds: round the data range out to a readable step so the tick
   labels are numbers a person would choose, not 1.0473. */
function niceScale(lo, hi, want) {
  if (!(hi > lo)) { hi = lo + 1; }
  const raw = (hi - lo) / Math.max(want, 2);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 1e6; v += step) ticks.push(+v.toFixed(10));
  return { lo: start, hi: end, ticks };
}

/* Push overlapping end-labels apart so a crowded right edge stays readable.
   The paper figures label lines directly rather than using a legend box; this
   keeps that, which is why the collision pass is needed at all. Two passes:
   down to open the gaps, then back up from `yMax` so a stack of series that
   all end at the floor (three arms at -100% in a month view) climbs above the
   axis instead of sliding into the tick labels. */
function declutter(items, minGap, yMin, yMax) {
  items.sort((a, b) => a.y - b.y);
  for (let i = 1; i < items.length; i++) {
    if (items[i].y - items[i - 1].y < minGap) items[i].y = items[i - 1].y + minGap;
  }
  if (items.length && yMax !== undefined && items[items.length - 1].y > yMax) {
    items[items.length - 1].y = yMax;
    for (let i = items.length - 2; i >= 0; i--) {
      if (items[i + 1].y - items[i].y < minGap) items[i].y = items[i + 1].y - minGap;
    }
  }
  if (yMin !== undefined) items.forEach(l => { l.y = Math.max(l.y, yMin); });
  return items;
}

function lineChart(mount, spec) {
  const W = 760, H = spec.height || 300;
  const M = { t: 14, r: spec.labelWidth || 132, b: 54, l: 46 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;

  const xs = spec.series.map(s => [s.x0, s.x0 + s.values.length - 1]);
  const xlo = Math.min(...xs.map(p => p[0])), xhi = Math.max(...xs.map(p => p[1]));
  const flat = spec.series.flatMap(s => s.values).filter(v => v !== null && isFinite(v));
  if (!flat.length) return;
  // a chart may pin a reference level into the range: break-even for the
  // betting curves, so a month in which every arm lost still shows how far
  // below zero it sits rather than zooming into the losses
  const lo = spec.yInclude === undefined ? Math.min(...flat) : Math.min(...flat, spec.yInclude);
  const hi = spec.yInclude === undefined ? Math.max(...flat) : Math.max(...flat, spec.yInclude);
  const y = niceScale(lo, hi, 5);
  const X = v => M.l + (xhi === xlo ? 0 : (v - xlo) / (xhi - xlo)) * iw;
  const Y = v => M.t + ih - (v - y.lo) / (y.hi - y.lo) * ih;

  const svg = el('svg', {
    viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img',
    'aria-label': spec.alt || spec.title || 'chart',
  });

  // horizontal gridlines + y labels. Decimals come from the tick STEP, not a
  // fixed width: a 0.05 step formatted to 1dp prints 0.3, 0.3, 0.4, 0.4.
  const tstep = y.ticks.length > 1 ? Math.abs(y.ticks[1] - y.ticks[0]) : 1;
  const dp = Math.max(0, Math.min(4, Math.ceil(-Math.log10(tstep) + 1e-9)));
  for (const t of y.ticks) {
    svg.appendChild(el('line', { x1: M.l, x2: M.l + iw, y1: Y(t), y2: Y(t), class: 'grid' }));
    svg.appendChild(el('text', { x: M.l - 8, y: Y(t) + 4, class: 'ylab' }, spec.fmt(t, dp)));
  }
  // the zero line carries meaning on both chart families: break-even for the
  // betting curves, and the consensus reference for the scores.
  if (y.lo <= 0 && y.hi >= 0) {
    svg.appendChild(el('line', { x1: M.l, x2: M.l + iw, y1: Y(0), y2: Y(0), class: 'zero' }));
  }
  // x axis
  svg.appendChild(el('line', { x1: M.l, x2: M.l + iw, y1: M.t + ih, y2: M.t + ih, class: 'axis' }));
  // x ticks on a round step, not five evenly-spaced fractions: an 8-day window
  // split into fifths prints +0, +2, +3, +5, +6, which reads as a broken axis.
  const xt = niceScale(xlo, xhi, 4).ticks.filter(v => v >= xlo - 1e-9 && v <= xhi + 1e-9);
  const xticks = xt.length >= 2 ? xt : [xlo, xhi];
  xticks.forEach((v, i) => {
    svg.appendChild(el('text', {
      x: X(v), y: M.t + ih + 20, class: 'xlab',
      'text-anchor': i === 0 ? 'start'
        : (i === xticks.length - 1 && v >= xhi - (xhi - xlo) * 0.02) ? 'end' : 'middle',
    }, spec.xfmt(v)));
  });
  if (spec.xLabel) {
    svg.appendChild(el('text', {
      x: M.l + iw / 2, y: H - 8, class: 'axlab',
    }, spec.xLabel));
  }

  if (spec.marker !== undefined && spec.marker >= xlo && spec.marker <= xhi) {
    svg.appendChild(el('line', {
      x1: X(spec.marker), x2: X(spec.marker), y1: M.t, y2: M.t + ih, class: 'marker',
    }));
    svg.appendChild(el('text', {
      x: X(spec.marker) + 5, y: M.t + 11, class: 'markerlab',
    }, spec.markerLabel || ''));
  }

  const labels = [];
  spec.series.forEach(s => {
    // a null breaks the path rather than drawing a straight line across a gap
    let d = '', pen = false, lastY = null;
    s.values.forEach((v, i) => {
      if (v === null || !isFinite(v)) { pen = false; return; }
      const px = X(s.x0 + i), py = Y(v);
      d += (pen ? 'L' : 'M') + px.toFixed(1) + ' ' + py.toFixed(1) + ' ';
      pen = true; lastY = py;
    });
    if (!d) return;
    svg.appendChild(el('path', {
      d, fill: 'none', stroke: s.color, 'stroke-width': s.dash ? 2 : 2.4,
      'stroke-dasharray': s.dash ? '6 4' : null,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }));
    if (lastY !== null) labels.push({ y: lastY, name: s.name, color: s.color });
  });

  declutter(labels, 15, M.t + 6, M.t + ih + 4).forEach(l => {
    svg.appendChild(el('text', {
      x: M.l + iw + 8, y: l.y + 4,
      class: 'endlab', fill: l.color,
    }, l.name));
  });

  /* ---- hover readout ---------------------------------------------------
     A transparent capture rect over the plot area, a vertical guide, a dot on
     every series that has a value at that x, and an HTML tooltip. The numbers
     shown are the plotted points themselves -- the same rounded, downsampled
     values already in series.json -- so this reveals nothing the chart does
     not already draw. Pointer events cover mouse, pen and touch alike. */
  const guide = el('line', { class: 'hoverline', y1: M.t, y2: M.t + ih, opacity: 0 });
  svg.appendChild(guide);
  const dots = spec.series.map(() => {
    const c = el('circle', { r: 4, class: 'hoverdot', opacity: 0 });
    svg.appendChild(c);
    return c;
  });
  const hit = el('rect', {
    x: M.l, y: M.t, width: iw, height: ih, fill: 'transparent',
    style: 'cursor:crosshair',
  });
  svg.appendChild(hit);

  mount.innerHTML = '';
  mount.className = 'chartbox';
  mount.appendChild(svg);

  /* The readout is a fixed strip UNDER the chart, the same width as it, with
     the series flowing left-to-right. An overlay box tall enough for eight
     stacked rows covered the plot it was describing; laid out horizontally the
     same information is three short lines and never moves. It also has a
     resting state -- the final value of every series -- so the panel is useful
     before anyone hovers and the page does not reflow when they do. */
  const read = document.createElement('div');
  read.className = 'readout';
  mount.appendChild(read);

  const lastIdx = s => {
    for (let i = s.values.length - 1; i >= 0; i--) {
      if (s.values[i] !== null && isFinite(s.values[i])) return i;
    }
    return -1;
  };
  function paint(xv, resting) {
    const rows = [];
    spec.series.forEach((s, k) => {
      const i = resting ? lastIdx(s) : xv - s.x0;
      const v = s.values[i];
      if (i < 0 || i >= s.values.length || v === null || !isFinite(v)) {
        dots[k].setAttribute('opacity', 0);
        return;
      }
      if (resting) {
        dots[k].setAttribute('opacity', 0);
      } else {
        dots[k].setAttribute('cx', X(xv));
        dots[k].setAttribute('cy', Y(v));
        dots[k].setAttribute('fill', s.color);
        dots[k].setAttribute('opacity', 1);
      }
      rows.push({ name: s.name, color: s.color, v });
    });
    if (!rows.length) return false;
    rows.sort((a, b) => b.v - a.v);
    const head = resting ? 'Final' : (spec.xtipfmt || spec.xfmt)(xv);
    read.innerHTML = `<span class="rx">${esc(head)}</span>` + rows.map(r =>
      `<span class="ri"><i style="background:${r.color}"></i>`
      + `<span class="rn">${esc(r.name)}</span>`
      + `<b>${esc(spec.tipfmt(r.v))}</b></span>`).join('');
    return true;
  }

  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
  function rest() {
    guide.setAttribute('opacity', 0);
    dots.forEach(d => d.setAttribute('opacity', 0));
    paint(0, true);
  }
  function move(ev) {
    const ctm = svg.getScreenCTM();
    if (!ctm) return;
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    const loc = pt.matrixTransform(ctm.inverse());
    // nearest whole step, so the readout snaps to real points not interpolations
    const xv = Math.round(clamp(xlo + (loc.x - M.l) / iw * (xhi - xlo), xlo, xhi));
    if (!paint(xv, false)) { rest(); return; }
    guide.setAttribute('x1', X(xv));
    guide.setAttribute('x2', X(xv));
    guide.setAttribute('opacity', 1);
  }
  rest();
  hit.addEventListener('pointermove', move);
  hit.addEventListener('pointerdown', move);
  hit.addEventListener('pointerleave', rest);
}

const pct = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v) + '%';

function renderBettingCharts(b) {
  const mount = byId('bet-charts');
  if (!mount || !b || !b.markets) return;
  mount.innerHTML = '';

  b.markets.forEach(m => {
    const fig = document.createElement('figure');
    fig.className = 'chartfig';
    // each chart carries its own strip: the markets resolve on different
    // calendars (GDP is quarterly), so their windows do not line up
    const strip = document.createElement('div');
    strip.className = 'tabs';
    fig.appendChild(strip);
    const box = document.createElement('div');
    fig.appendChild(box);
    const cap = document.createElement('figcaption');
    fig.appendChild(cap);
    mount.appendChild(fig);

    // A series keeps its colour across tabs: baselines are numbered by their
    // order in the cumulative view, and the window views reuse the map, so
    // the NY Fed is the same blue in Q2 as in the whole run.
    const colors = {};
    let bi = 0;
    m.series.forEach(s => {
      colors[s.name] = SERIES_COLORS[s.name] || (s.kind === 'human'
        ? BASELINE_COLORS[bi++ % BASELINE_COLORS.length] : 'var(--s-grey)');
    });
    const colorFor = s => colors[s.name] || SERIES_COLORS[s.name]
      || (s.kind === 'human' ? BASELINE_COLORS[0] : 'var(--s-grey)');

    const views = [{ key: 'all', label: 'All months', series: m.series,
                     xLabel: 'Days since nowcasting start',
                     tail: 'cumulative LiveBetting return, every window end to end.' }]
      .concat((m.months || []).slice().reverse().map(mo => ({
        key: mo.key, label: mo.label, series: mo.series,
        xLabel: `Days since the ${mo.label} window opened`,
        tail: `LiveBetting return on the ${mo.label} bets alone.`,
      })));
    let active = 'all';

    function draw() {
      const v = views.find(x => x.key === active) || views[0];
      cap.innerHTML = `<b>${esc(m.label)}</b> — ${esc(v.tail)}`;
      lineChart(box, {
        series: v.series.map(s => ({
          name: s.name, x0: s.start, values: s.values,
          dash: s.kind === 'human', color: colorFor(s),
        })),
        height: 300, yInclude: 0,
        alt: `LiveBetting return on ${m.label}, by model and baseline`,
        xLabel: v.xLabel,
        fmt: v => pct(v), xfmt: v => '+' + Math.round(v),
        xtipfmt: v => 'Day +' + Math.round(v),
        tipfmt: v => pct(v),
      });
    }
    function pick(key) {
      active = key;
      tabStrip(strip, views, active, pick);
      draw();
    }
    if (views.length > 1) tabStrip(strip, views, active, pick);
    else strip.remove();
    draw();
  });
}

function renderCaseCharts(c) {
  const mount = byId('case-charts');
  if (!mount || !c || !c.panels) return;
  mount.innerHTML = '';
  c.panels.forEach(p => {
    const fig = document.createElement('figure');
    fig.className = 'chartfig';
    const box = document.createElement('div');
    fig.appendChild(box);
    const cap = document.createElement('figcaption');
    cap.innerHTML = `<b>${esc(p.label)}</b> — ${esc(p.unit)}. `
      + 'The dashed line marks 10:30 ET, April 8, 2026.';
    fig.appendChild(cap);
    mount.appendChild(fig);

    const t0 = new Date(p.start + ':00');
    const hoursTo = iso => (new Date(iso + ':00') - t0) / 3.6e6;
    lineChart(box, {
      series: [{ name: 'GPT-5', x0: 0, values: p.values, color: 'var(--accent)' }],
      height: 262, labelWidth: 66,
      alt: `GPT-5 nowcasts for ${p.label}`,
      xLabel: 'Nowcast time',
      marker: hoursTo(p.event) / p.step_hours,
      markerLabel: 'Apr 8',
      fmt: (v, dp) => v.toFixed(dp),
      tipfmt: v => v.toFixed(2) + '%',
      xfmt: v => {
        const d = new Date(t0.getTime() + v * p.step_hours * 3.6e6);
        return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
      },
    });
  });
}

fetch('data/series.json?v=' + Date.now())
  .then(r => r.ok ? r.json() : Promise.reject(new Error(r.status)))
  .then(s => { renderBettingCharts(s.betting); renderCaseCharts(s.case_study); })
  .catch(e => console.warn('LiveMacroEval: charts unavailable —', e.message || e));
