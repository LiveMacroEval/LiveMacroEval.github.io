/* Renders every number on the page from data/leaderboard.json.
   To refresh the site, edit that JSON — do not edit index.html.
   tools/update_site.py regenerates the headline, themes and betting blocks. */

/* Exact zero gets no sign — a signed "+0.000" reads as a claim the value is
   positive, and both the consensus reference and a tied theme score are 0. */
const sign = v => (v === 0 ? '' : v > 0 ? '+' : '−');
const fmt = (v, d = 3) => sign(v) + Math.abs(v).toFixed(d);
const pct = v => sign(v) + Math.abs(v).toFixed(1) + '%';
const cls = v => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');

/* '2026-08-25' -> 'Aug 25, 2026'. Parsed by hand so the label cannot slip a
   day for viewers west of Greenwich. */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}, ${m[1]}` : (iso || '—');
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

function renderHeadline(h) {
  const max = Math.max(...h.rows.map(r => Math.abs(r.score))) || 1;
  const best = Math.max(...h.rows.filter(x => x.kind === 'llm').map(x => x.score));
  let rank = 0;
  document.getElementById('lb-body').innerHTML = h.rows.map(r => {
    const isRef = r.ci === null && r.score === 0;
    if (!isRef) rank++;
    const kindLabel = { llm: 'LLM', human: 'Human', econ: 'Econ' }[r.kind] || '';
    return `<tr class="${isRef ? 'ref' : (r.kind === 'llm' && r.score === best) ? 'lead' : ''}">
      <td class="rank">${isRef ? '—' : rank}</td>
      <td><span class="rowname">${esc(r.name)}</span><span class="kind ${r.kind}">${kindLabel}</span>
          ${r.note ? `<span class="rownote">${esc(r.note)}</span>` : ''}</td>
      <td class="num">${scoreCell(r.score)}</td>
      <td class="num ci">${r.ci ? `[${fmt(r.ci[0])}, ${fmt(r.ci[1])}]` : '—'}</td>
      <td class="num ci">${r.events ?? '—'}</td>
      <td class="barcell">${bar(r.score, max)}</td>
    </tr>`;
  }).join('');
  document.getElementById('lb-note').textContent = `${h.window} ${h.note}`;
}

function renderAgentDesign(a) {
  document.getElementById('ad-note').textContent = `${a.window}. ${a.note}`;
  document.getElementById('ad-body').innerHTML = a.rows.map(r => `
    <tr class="${r.best ? 'lead' : ''}">
      <td><span class="rowname">${esc(r.name)}</span>${r.note ? `<span class="rownote">${esc(r.note)}</span>` : ''}</td>
      <td class="num">${scoreCell(r.score)}</td>
    </tr>`).join('');
}

/* Models down the side, themes across the top. */
function renderThemes(t) {
  const max = Math.max(...t.rows.flatMap(r => r.scores.map(Math.abs))) || 1;
  document.getElementById('th-head').innerHTML =
    '<th>Model</th>' + t.columns.map(c => `<th class="num">${esc(c)}</th>`).join('');
  document.getElementById('th-body').innerHTML = t.rows.map(r => `
    <tr>
      <td><span class="rowname">${esc(r.name)}</span><span class="kind ${r.kind}">${
        { llm: 'LLM', human: 'Human', econ: 'Econ' }[r.kind] || ''}</span></td>
      ${r.scores.map(v => `<td class="cell">${scoreCell(v)}${cellBar(v, max)}</td>`).join('')}
    </tr>`).join('');
  document.getElementById('th-note').textContent = `${t.window} ${t.note}`;
}

/* One small table per prediction market. */
function renderBetting(b) {
  document.getElementById('bet-grid').innerHTML = b.markets.map(m => {
    const max = Math.max(...m.rows.map(r => Math.abs(r.ret))) || 1;
    return `<div class="market">
      <h4>${esc(m.label)}</h4>
      <div class="tablewrap"><table class="narrow">
        <thead><tr><th>Arm</th><th class="num">Return</th><th class="barcell"></th></tr></thead>
        <tbody>${m.rows.map(r => `
          <tr><td><span class="rowname">${esc(r.name)}</span><span class="kind ${r.kind}">${
            r.kind === 'human' ? 'Human' : 'LLM'}</span></td>
          <td class="num"><span class="score ${cls(r.ret)}">${pct(r.ret)}</span></td>
          <td class="barcell">${bar(r.ret, max)}</td></tr>`).join('')}
        </tbody>
      </table></div></div>`;
  }).join('');
  document.getElementById('bet-note').textContent = `${b.window} ${b.note}`;
}

function renderIndicators(list) {
  document.getElementById('ind-grid').innerHTML = list.map(t => `
    <div class="card">
      <span class="tag">${t.items.length} indicators</span>
      <h4>${esc(t.theme)}</h4>
      <p class="blurb">${esc(t.blurb)}</p>
      <ul>${t.items.map(i => `<li>${link(i.name, i.url)}</li>`).join('')}</ul>
    </div>`).join('');
}

function renderFed(list) {
  document.getElementById('fed-list').innerHTML = list.map(f =>
    `<li>${link(f.name, f.url)} <span class="ci">— ${esc(f.target)}</span></li>`).join('');
}

fetch('data/leaderboard.json?v=' + Date.now())
  .then(r => r.json())
  .then(d => {
    document.getElementById('last-updated').textContent = fmtDate(d.last_updated);
    document.getElementById('next-update').textContent = fmtDate(d.next_update);
    renderHeadline(d.headline);
    renderAgentDesign(d.agent_design);
    renderThemes(d.themes);
    renderBetting(d.betting);
    renderIndicators(d.indicators);
    renderFed(d.comparators.fed);
  })
  .catch(e => {
    console.error(e);
    document.getElementById('lb-body').innerHTML =
      '<tr><td colspan="6">Could not load data/leaderboard.json. If you opened this file directly with file://, serve it instead: <code>python3 -m http.server</code></td></tr>';
  });
