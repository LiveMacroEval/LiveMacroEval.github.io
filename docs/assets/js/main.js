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

/* One row, used by both panels of the leaderboard. Agent-design rows have no
   CI or event count, so those cells fall back to an em dash. */
function leaderRow(r, rank, isRef, isLead, max) {
  const kindLabel = { llm: 'LLM', human: 'Human', econ: 'Econ' }[r.kind] || '';
  return `<tr class="${isRef ? 'ref' : isLead ? 'lead' : ''}">
    <td class="rank">${isRef ? '—' : rank}</td>
    <td><span class="rowname">${esc(r.name)}</span><span class="kind ${r.kind}">${kindLabel}</span>
        ${r.note ? `<span class="rownote">${esc(r.note)}</span>` : ''}</td>
    <td class="num">${scoreCell(r.score)}</td>
    <td class="num ci">${r.ci ? `[${fmt(r.ci[0])}, ${fmt(r.ci[1])}]` : '—'}</td>
    <td class="num ci">${r.events ?? '—'}</td>
    <td class="barcell">${bar(r.score, max)}</td>
  </tr>`;
}

function panelHeader(label) {
  return `<tr class="grouphdr"><td colspan="6">${esc(label)}</td></tr>`;
}

/* The headline evaluation and the agent-design experiment render as two
   panels of a single table. They share the metric and the bar scale, but not
   the window or the consensus source -- hence the panel captions. */
function renderLeaderboard(h, a) {
  const body = byId('lb-body');
  if (!body) return;
  const all = [...h.rows.map(r => r.score), ...a.rows.map(r => r.score)];
  const max = Math.max(...all.map(Math.abs)) || 1;
  const html = [];

  html.push(panelHeader(h.window));
  const bestH = Math.max(...h.rows.filter(x => x.kind === 'llm').map(x => x.score));
  let rank = 0;
  for (const r of h.rows) {
    const isRef = r.ci === null && r.score === 0;
    if (!isRef) rank++;
    html.push(leaderRow(r, rank, isRef, r.kind === 'llm' && r.score === bestH, max));
  }

  html.push(panelHeader(`${a.title} — ${a.window}`));
  rank = 0;
  for (const r of a.rows) {
    const isRef = r.kind === 'human';
    if (!isRef) rank++;
    html.push(leaderRow(r, rank, isRef, !!r.best, max));
  }

  body.innerHTML = html.join('');
  const note = byId('lb-note');
  if (note) note.textContent = `${h.note} ${a.note}`;
}

/* Models down the side, themes across the top. */
function renderThemes(t) {
  if (!byId('th-body')) return;
  const max = Math.max(...t.rows.flatMap(r => r.scores.map(Math.abs))) || 1;
  const head = byId('th-head');
  if (head) head.innerHTML =
    '<th>Model</th>' + t.columns.map(c => `<th class="num">${esc(c)}</th>`).join('');
  byId('th-body').innerHTML = t.rows.map(r => `
    <tr>
      <td><span class="rowname">${esc(r.name)}</span><span class="kind ${r.kind}">${
        { llm: 'LLM', human: 'Human', econ: 'Econ' }[r.kind] || ''}</span></td>
      ${r.scores.map(v => `<td class="cell">${scoreCell(v)}${cellBar(v, max)}</td>`).join('')}
    </tr>`).join('');
  const tn = byId('th-note');
  if (tn) tn.textContent = `${t.window} ${t.note}`;
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
    renderLeaderboard(d.headline, d.agent_design);
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
    body.innerHTML = `<tr><td colspan="6">${msg}</td></tr>`;
  });
