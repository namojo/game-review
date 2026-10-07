// 라이브러리 없이 SVG·HTML로 그리는 작은 차트들.
// 컨테이너의 실제 폭으로 그려서 모바일에서도 글자가 작아지지 않게 한다(창 크기가 바뀌면 다시 그린다).

const NS = 'http://www.w3.org/2000/svg';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const tip = () => document.getElementById('tooltip');
export function showTip(html, x, y) {
  const t = tip();
  if (!t) return;
  t.innerHTML = html;
  t.hidden = false;
  const r = t.getBoundingClientRect();
  const left = Math.min(Math.max(8, x + 14), window.innerWidth - r.width - 8);
  const top = y - r.height - 12 < 8 ? y + 16 : y - r.height - 12;
  t.style.left = `${left}px`;
  t.style.top = `${top}px`;
}
export function hideTip() {
  const t = tip();
  if (t) t.hidden = true;
}

function el(name, attrs = {}, parent) {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

function niceMax(v) {
  if (v <= 5) return 5;
  const step = v <= 20 ? 5 : v <= 50 ? 10 : 20;
  return Math.ceil(v / step) * step;
}

// 막대 위쪽만 4px 둥글게, 바닥은 직각
function columnPath(x, y, w, h, r = 4) {
  if (h <= 0) return '';
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

const dayFmt = new Intl.DateTimeFormat('ko-KR', { month: 'long', day: 'numeric', weekday: 'short', timeZone: 'UTC' });
const shortFmt = (d) => `${d.getUTCMonth() + 1}/${d.getUTCDate()}`;

/**
 * 일별 리뷰 수(세로 막대)와 7일 평균 별점(선)을 같은 x축의 두 차트로 그린다.
 * 서로 단위가 다르므로 축 두 개짜리 한 차트로 합치지 않는다.
 * days: [{ date: 'YYYY-MM-DD', count, ratingSum }]
 */
export function renderDaily(countHost, ratingHost, days, { animate = false } = {}) {
  const W = Math.max(280, Math.floor(countHost.clientWidth || 600));
  const m = { l: 30, r: 34, t: 10 };
  const plotW = W - m.l - m.r;
  const n = days.length;
  const band = plotW / Math.max(1, n);
  const barW = Math.max(1, Math.min(24, band - 2));
  const xAt = (i) => m.l + i * band + (band - barW) / 2;
  const xMid = (i) => m.l + i * band + band / 2;
  const labelStep = Math.max(1, Math.ceil(44 / band));

  // 7일 이동 평균 별점(그날까지 7일 동안의 리뷰 가중 평균)
  const rolling = days.map((_, i) => {
    let c = 0, s = 0;
    for (let j = Math.max(0, i - 6); j <= i; j++) { c += days[j].count; s += days[j].ratingSum; }
    return c ? s / c : null;
  });

  // --- 리뷰 수 ---
  const H1 = 150, axisH = 22;
  const yMax = niceMax(Math.max(1, ...days.map((d) => d.count)));
  const y1 = (v) => m.t + H1 - (v / yMax) * H1;
  const svg1 = el('svg', { class: `chart${animate ? ' grow' : ''}`, width: W, height: m.t + H1 + axisH, viewBox: `0 0 ${W} ${m.t + H1 + axisH}`, role: 'img',
    'aria-label': `일별 리뷰 수 막대 차트. ${n}일, 최대 ${Math.max(...days.map((d) => d.count))}건` });
  for (const v of [0, yMax / 2, yMax]) {
    el('line', { class: v === 0 ? 'axis' : 'grid', x1: m.l, x2: W - m.r, y1: y1(v), y2: y1(v) }, svg1);
    el('text', { x: m.l - 6, y: y1(v) + 4, 'text-anchor': 'end' }, svg1).textContent = v;
  }
  days.forEach((d, i) => {
    const g = el('g', {}, svg1);
    const h = (d.count / yMax) * H1;
    el('path', { class: 'bar', d: columnPath(xAt(i), y1(d.count), barW, h), style: animate ? `animation-delay:${Math.min(i * 6, 360)}ms` : '' }, g);
    const hit = el('rect', { class: 'bar-hit', x: m.l + i * band, y: m.t, width: band, height: H1 }, g);
    const date = new Date(`${d.date}T00:00:00Z`);
    const avg = d.count ? (d.ratingSum / d.count).toFixed(1) : '없음';
    const html = `<b>${esc(dayFmt.format(date))}</b><br>리뷰 ${d.count}건, 평균 별점 ${avg}`;
    hit.addEventListener('pointermove', (e) => { g.classList.add('hover'); showTip(html, e.clientX, e.clientY); });
    hit.addEventListener('pointerleave', () => { g.classList.remove('hover'); hideTip(); });
    if (i % labelStep === 0) {
      el('text', { x: xMid(i), y: m.t + H1 + 16, 'text-anchor': 'middle' }, svg1).textContent = shortFmt(date);
    }
  });
  countHost.replaceChildren(svg1);

  // --- 7일 평균 별점 ---
  const H2 = 96;
  const y2 = (v) => m.t + H2 - ((v - 1) / 4) * H2;
  const svg2 = el('svg', { class: 'chart', width: W, height: m.t + H2 + axisH, viewBox: `0 0 ${W} ${m.t + H2 + axisH}`, role: 'img',
    'aria-label': '7일 평균 별점 선 차트, 1점에서 5점' });
  for (const v of [1, 3, 5]) {
    el('line', { class: v === 1 ? 'axis' : 'grid', x1: m.l, x2: W - m.r, y1: y2(v), y2: y2(v) }, svg2);
    el('text', { x: m.l - 6, y: y2(v) + 4, 'text-anchor': 'end' }, svg2).textContent = v;
  }
  const pts = rolling.map((v, i) => (v == null ? null : [xMid(i), y2(v)]));
  let dPath = '';
  pts.forEach((p, i) => { if (p) dPath += `${dPath && pts[i - 1] ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`; });
  const valid = pts.filter(Boolean);
  if (valid.length) {
    el('path', { class: 'area', d: `${dPath}L${valid[valid.length - 1][0]},${y2(1)}L${valid[0][0]},${y2(1)}Z` }, svg2);
    el('path', { class: 'line', d: dPath }, svg2);
    const lastI = rolling.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0).pop();
    el('circle', { class: 'dot', cx: pts[lastI][0], cy: pts[lastI][1], r: 4 }, svg2);
    const lbl = el('text', { class: 't-strong', x: pts[lastI][0] + 7, y: pts[lastI][1] + 4 }, svg2);
    lbl.textContent = rolling[lastI].toFixed(2);
  }
  days.forEach((d, i) => { if (i % labelStep === 0) el('text', { x: xMid(i), y: m.t + H2 + 16, 'text-anchor': 'middle' }, svg2).textContent = shortFmt(new Date(`${d.date}T00:00:00Z`)); });
  const cross = el('line', { class: 'cross', y1: m.t, y2: m.t + H2, visibility: 'hidden' }, svg2);
  const hoverDot = el('circle', { class: 'dot', r: 4, visibility: 'hidden' }, svg2);
  const hitLayer = el('rect', { x: m.l, y: m.t, width: plotW, height: H2, fill: 'transparent' }, svg2);
  hitLayer.addEventListener('pointermove', (e) => {
    const box = svg2.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.floor((e.clientX - box.left - m.l) / band)));
    if (!pts[i]) return;
    cross.setAttribute('x1', pts[i][0]); cross.setAttribute('x2', pts[i][0]); cross.setAttribute('visibility', 'visible');
    hoverDot.setAttribute('cx', pts[i][0]); hoverDot.setAttribute('cy', pts[i][1]); hoverDot.setAttribute('visibility', 'visible');
    showTip(`<b>${esc(dayFmt.format(new Date(`${days[i].date}T00:00:00Z`)))}</b><br>최근 7일 평균 별점 ${rolling[i].toFixed(2)}`, e.clientX, e.clientY);
  });
  hitLayer.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); hoverDot.setAttribute('visibility', 'hidden'); hideTip(); });
  ratingHost.replaceChildren(svg2);
  return rolling;
}

/**
 * 가로 막대 목록(HTML). rows: [{ name, value, extra?, tip? }]
 * 한 계열이므로 한 색만 쓰고 값은 막대 끝에 적는다.
 */
export function renderHBars(host, rows, { unit = '건', extraHead = '', valueHead = '리뷰 수', nameHead = '' } = {}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  const head = nameHead
    ? `<div class="hbars-head"><span>${esc(nameHead)}</span><span>${esc(valueHead)}</span><span>${esc(extraHead)}</span></div>`
    : '';
  host.innerHTML = `<div class="hbars">${head}${rows.map((r, i) => `
    <div class="hb-row" data-i="${i}">
      <span class="hb-name">${esc(r.name)}</span>
      <span class="hb-track"><span class="hb-bar" style="width:${((r.value / max) * 100).toFixed(1)}%"></span></span>
      <span class="hb-val"><b>${r.value.toLocaleString('ko-KR')}</b>${esc(unit)}${r.extra ? ` <span class="muted">${esc(r.extra)}</span>` : ''}</span>
    </div>`).join('')}</div>`;
  host.querySelectorAll('.hb-row').forEach((rowEl) => {
    const r = rows[Number(rowEl.dataset.i)];
    if (!r.tip) return;
    rowEl.querySelectorAll('span').forEach((s) => {
      s.addEventListener('pointermove', (e) => showTip(r.tip, e.clientX, e.clientY));
      s.addEventListener('pointerleave', hideTip);
    });
  });
}
