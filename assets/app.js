// 원시인 형님들 키우기 리뷰 검수함 — 정적 SPA
// 데이터: data/reviews.json (계약: review-web-service/references/data-contract.md)
// 검수 상태는 이 브라우저 localStorage 에만 저장한다. 운영 전환 시 DB로 옮길 지점은 "운영 전환:" 주석으로 표시했다.

import { MODELS, DEFAULT_MODEL, rewriteReply, analyzeReview, ClaudeError } from './claude.js';
import { renderDaily, renderHBars, hideTip } from './charts.js';

const DATA_URL = 'data/reviews.json';
const LS = { state: 'tgr.state.v1', settings: 'tgr.settings.v1', temp: 'tgr.temp.v1', theme: 'tgr.theme', key: 'tgr.apikey' };
const LIMIT = { google_play: 350, app_store: 5970 };
const MIN_NOW = 2;       // 현재 리뷰 1건당 처리 시간(실측, 분)
const MIN_REVIEW = 0.5;  // 검수만 할 때 1건당 시간(가정, 분)

// ---------- 표시 이름 ----------
const L = {
  category: {
    praise: '칭찬·응원', ads: '광고 불만', monetization: '과금·BM', purchase_issue: '결제 오류', bug: '버그·오류',
    account: '계정·데이터', reward_issue: '보상 미지급', balance: '밸런스·난이도', content_request: '콘텐츠 건의',
    performance: '성능·최적화', other: '기타',
  },
  sentiment: { positive: '긍정', neutral: '중립', negative: '부정', mixed: '혼합' },
  priority: { P1: 'P1 즉시 대응', P2: 'P2 48~72시간', P3: 'P3 주간 일괄' },
  route: { cs: '고객센터', dev: '개발', bm: '사업(BM)', planning: '기획', none: '—' },
  flags: {
    refund_request: '환불 요청', legal_threat: '법적 대응 언급', profanity: '욕설', minor_user: '미성년 추정', spam: '스팸',
    competitor_mention: '경쟁작 언급', personal_info: '개인정보 노출', update_related: '업데이트 관련', needs_human: '사람 검수 필요',
  },
  store: { google_play: 'Google Play', app_store: 'App Store' },
  lang: {
    ko: '한국어', en: '영어', pt: '포르투갈어', ru: '러시아어', id: '인도네시아어', es: '스페인어', th: '태국어',
    tr: '튀르키예어', fr: '프랑스어', ja: '일본어', de: '독일어', vi: '베트남어', 'zh-TW': '중국어(번체)',
    'zh-CN': '중국어(간체)', zh: '중국어', it: '이탈리아어', ar: '아랍어', hi: '힌디어', ms: '말레이어', pl: '폴란드어', nl: '네덜란드어',
  },
  proc: { todo: '검수 대기', approved: '승인', held: '보류', posted: '등록 완료', all: '전체' },
};
const HOT_FLAGS = new Set(['legal_threat', 'refund_request', 'personal_info', 'minor_user', 'needs_human']);
const PRIO_RANK = { P1: 0, P2: 1, P3: 2 };
const ROUTES = ['cs', 'dev', 'bm', 'planning'];

const langName = (c) => L.lang[c] || c || '알 수 없음';

// ---------- 유틸 ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const charLen = (s) => [...(s || '')].length; // 코드 포인트 기준(파이프라인 검증과 같은 방식)
const parseTime = (s) => (s ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`) : null);
const fmtDateTime = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const fmtDate = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric' });
const rtf = new Intl.RelativeTimeFormat('ko', { numeric: 'auto' });
function relTime(d) {
  if (!d) return '';
  const diff = (d.getTime() - Date.now()) / 1000;
  const a = Math.abs(diff);
  if (a < 60) return '방금';
  if (a < 3600) return rtf.format(Math.round(diff / 60), 'minute');
  if (a < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
  if (a < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day');
  if (a < 86400 * 365) return rtf.format(Math.round(diff / 86400 / 30), 'month');
  return rtf.format(Math.round(diff / 86400 / 365), 'year');
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const storage = {
  get(k, fb) { try { const v = localStorage.getItem(k); return v == null ? fb : JSON.parse(v); } catch (e) { return fb; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } },
  getRaw(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  setRaw(k, v) { try { localStorage.setItem(k, v); return true; } catch (e) { return false; } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* 저장소 차단 */ } },
};

const ICON = {
  p1: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1 11.5 11H.5Z"/></svg>',
  p2: '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5"/></svg>',
  p3: '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.2"/></svg>',
  todo: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  approved: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="m4.8 8.2 2.2 2.2 4.2-4.6" fill="none" stroke="var(--surface)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  held: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M6.4 5.4v5.2M9.6 5.4v5.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  posted: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m1.5 8.5 3 3 6-7M7 11.5l1 0.2 6-7.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  hand: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm0 3v4.5m0 2.2v.3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  warn: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8 15 14H1Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6.2v3.6m0 2v.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  search: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="m11 11 3.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  box: '<svg viewBox="0 0 18 18" aria-hidden="true"><rect x="2.5" y="2.5" width="13" height="13" rx="3" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
};

// ---------- 상태 ----------
const S = {
  data: null,
  reviews: [],
  byId: new Map(),
  // 운영 전환: 검수 상태(local)·임시 항목(temp)은 DB 테이블(review_status, manual_reviews)로 옮긴다.
  local: storage.get(LS.state, {}),
  temp: storage.get(LS.temp, []),
  settings: { model: DEFAULT_MODEL, tone: 'default', ...storage.get(LS.settings, {}) },
  filters: defaultFilters(),
  visible: [],
  replyReuse: new Map(),
  view: null,
  selectedId: null,
};
function defaultFilters() {
  return { proc: 'todo', pendingOnly: false, store: '', lang: '', rating: '', category: '', priority: '', route: '', q: '' };
}

const saveLocal = () => {
  // 운영 전환: 여기서 API(PATCH /reviews/{id}/status)로 보낸다.
  if (!storage.set(LS.state, S.local)) toast('이 브라우저에 검수 상태를 저장하지 못했습니다(저장소 차단). 새로고침하면 사라집니다.', { error: true });
};
const saveTemp = () => storage.set(LS.temp, S.temp);
const saveSettings = () => storage.set(LS.settings, S.settings);
const apiKey = () => storage.getRaw(LS.key) || '';

const loc = (id) => S.local[id] || {};
function patchLocal(id, patch) {
  const cur = { ...loc(id), ...patch, updated_at: new Date().toISOString() };
  for (const k of Object.keys(cur)) if (cur[k] === undefined) delete cur[k];
  S.local[id] = cur;
  saveLocal();
}
function aiOf(r) {
  if (!r?.ai) return null;
  const o = loc(r.id).override;
  return o ? { ...r.ai, ...o } : r.ai;
}
const langOf = (r) => r.ai?.detected_lang || r.lang;
const procOf = (r) => loc(r.id).status || 'todo';
const replyOf = (r) => loc(r.id).reply ?? r.ai?.reply ?? '';
const limitOf = (r) => LIMIT[r.store] || 350;
const flagsOf = (r) => aiOf(r)?.flags || [];
const needsHuman = (r) => flagsOf(r).includes('needs_human');
const isEdited = (r) => (r.ai ? replyOf(r).trim() !== (r.ai.reply || '').trim() : !!(loc(r.id).reply || '').trim());
function replyKo(r) {
  const l = loc(r.id);
  if (l.reply_ko_for != null) return { text: l.reply_ko, stale: l.reply_ko_for.trim() !== replyOf(r).trim() };
  const ko = r.ai?.reply_ko;
  return { text: ko, stale: !!ko && isEdited(r) };
}
function canApprove(r) {
  const t = replyOf(r).trim();
  if (!t) return { ok: false, why: '답변이 비어 있습니다.' };
  if (r.store === 'google_play' && charLen(t) > LIMIT.google_play) return { ok: false, why: 'Google Play 답변은 350자를 넘을 수 없습니다.' };
  if (charLen(t) > LIMIT.app_store) return { ok: false, why: 'App Store 답변 한도(5,970자)를 넘었습니다.' };
  return { ok: true };
}

// ---------- 토스트 ----------
function toast(msg, { action, error = false, timeout = 6000 } = {}) {
  const host = $('#toasts');
  const t = document.createElement('div');
  t.className = `toast${error ? ' err' : ''}`;
  t.setAttribute('role', error ? 'alert' : 'status');
  t.innerHTML = `<span class="t-msg">${esc(msg)}</span>`;
  let timer;
  const close = () => { clearTimeout(timer); t.remove(); };
  if (action) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = action.label;
    b.addEventListener('click', () => { close(); action.fn(); });
    t.appendChild(b);
  }
  const x = document.createElement('button');
  x.type = 'button';
  x.textContent = '닫기';
  x.addEventListener('click', close);
  t.appendChild(x);
  host.appendChild(t);
  while (host.children.length > 3) host.firstElementChild.remove();
  timer = setTimeout(close, error ? Math.max(timeout, 9000) : timeout);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    ta.remove();
    return ok;
  }
}

function download(name, text, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------- 공통 조각 ----------
function stars(n) {
  const v = Math.max(0, Math.min(5, Number(n) || 0));
  return `<span class="stars" role="img" aria-label="별점 ${v}점">${'★'.repeat(v)}<span class="off">${'★'.repeat(5 - v)}</span></span>`;
}
function prioTag(r) {
  const ai = aiOf(r);
  if (!ai) return '<span class="prio prio-wait">분석 대기</span>';
  const p = ai.priority;
  const icon = p === 'P1' ? ICON.p1 : p === 'P2' ? ICON.p2 : ICON.p3;
  return `<span class="prio prio-${esc(p)}">${icon}${esc(p)}</span>`;
}
function procTag(r) {
  const p = procOf(r);
  return `<span class="proc proc-${p}">${ICON[p]}${L.proc[p]}</span>`;
}
const humanTag = () => `<span class="human">${ICON.hand}사람 검수</span>`;
const dayKey = (r) => (r.created_at || '').slice(0, 10);

// ---------- 데이터 ----------
async function load() {
  let res;
  try {
    res = await fetch(DATA_URL, { cache: 'no-cache' });
  } catch (e) {
    return showLoadError(e);
  }
  if (!res.ok) return showLoadError(null, res.status);
  let data;
  try {
    data = await res.json();
  } catch (e) {
    return showLoadError(e, 'json');
  }
  if (!data || !Array.isArray(data.reviews)) return showLoadError(null, 'shape');
  S.data = data;
  for (const t of S.temp) t.temp = true;
  S.reviews = [...S.temp, ...data.reviews];
  S.byId = new Map(S.reviews.map((r) => [r.id, r]));
  S.replyReuse = new Map();
  for (const r of data.reviews) {
    const t = r.existing_reply?.text;
    if (t) S.replyReuse.set(t, (S.replyReuse.get(t) || 0) + 1);
  }
  // 사용자가 리뷰를 고쳐 다시 분석된 리뷰는 그 전에 저장한 검수 상태(승인·수정 답변)를 버린다.
  // 옛 내용 기준으로 승인한 답변이 새 내용에 그대로 내보내지는 것을 막기 위해서다.
  let reset = 0;
  for (const r of data.reviews) {
    const changed = parseTime(r.content_changed_at);
    const l = S.local[r.id];
    if (changed && l && new Date(l.updated_at || l.approved_at || 0) < changed) {
      delete S.local[r.id];
      reset += 1;
    }
  }
  if (reset) {
    saveLocal();
    setTimeout(() => toast(`작성자가 내용을 고친 리뷰 ${reset}건은 다시 분석되어 검수 상태를 초기화했습니다. "내용 변경됨" 표시를 확인하세요.`, { timeout: 9000 }), 300);
  }
  const c = parseTime(data.meta?.collected_at);
  $('#collected').textContent = c ? `${fmtDateTime.format(c)} 수집 기준` : '';
  window.addEventListener('hashchange', route);
  route();
}

function showLoadError(err, code) {
  const fileProto = location.protocol === 'file:';
  let cause;
  if (fileProto) {
    cause = `<p>브라우저는 <code>file://</code> 로 연 페이지가 데이터 파일을 읽지 못하게 막습니다. 이 폴더에서 로컬 서버를 띄운 뒤 주소로 여세요.</p>
      <pre>python3 -m http.server -d game-review 8765
# 브라우저에서 http://localhost:8765/ 열기</pre>`;
  } else if (code === 404) {
    cause = `<p><code>${esc(DATA_URL)}</code> 파일이 없습니다(404). 파이프라인의 <code>merge_for_site.py</code> 로 데이터를 만든 뒤 다시 배포하세요.</p>`;
  } else if (code === 'json' || code === 'shape') {
    cause = `<p><code>${esc(DATA_URL)}</code> 를 읽었지만 형식이 맞지 않습니다. 파일이 잘렸거나 <code>reviews</code> 배열이 없습니다. <code>merge_for_site.py</code> 를 다시 실행하세요.</p>`;
  } else if (typeof code === 'number') {
    cause = `<p>서버가 <code>${esc(DATA_URL)}</code> 요청에 ${code} 로 응답했습니다. 잠시 뒤 새로고침하세요.</p>`;
  } else {
    cause = `<p>네트워크 오류로 <code>${esc(DATA_URL)}</code> 를 받지 못했습니다(${esc(err?.message || '알 수 없음')}). 연결을 확인하고 새로고침하세요.</p>`;
  }
  $('#view').innerHTML = `<section class="load-error"><h1>리뷰 데이터를 불러오지 못했습니다</h1>${cause}</section>`;
}

// ---------- 라우팅 ----------
function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [path, query = ''] = h.split('?');
  const parts = path.split('/').filter(Boolean);
  return { view: parts[0] || 'dashboard', id: parts[1] ? decodeURIComponent(parts[1]) : null, query: new URLSearchParams(query) };
}

function route() {
  hideTip();
  const { view, id, query } = parseHash();
  const known = ['dashboard', 'inbox', 'guide', 'settings'];
  const v = known.includes(view) ? view : 'dashboard';
  $$('.tabs a').forEach((a) => (a.dataset.tab === v ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  const main = $('#view');
  if (v === 'inbox') {
    const fresh = S.view !== 'inbox';
    const hasQuery = [...query.keys()].length > 0;
    if (hasQuery) {
      S.filters = { ...defaultFilters(), proc: 'all' };
      for (const k of Object.keys(S.filters)) if (query.has(k)) S.filters[k] = k === 'pendingOnly' ? query.get(k) === '1' : query.get(k);
      history.replaceState(null, '', id ? `#/inbox/${encodeURIComponent(id)}` : '#/inbox');
    }
    if (fresh || hasQuery) renderInbox(main);
    selectReview(id && S.byId.has(id) ? id : null, { fromRoute: true });
    if (fresh) document.title = '검수 인박스 | 리뷰 검수함';
  } else {
    S.selectedId = null;
    if (v === 'dashboard') renderDashboard(main);
    if (v === 'guide') renderGuide(main);
    if (v === 'settings') renderSettings(main);
    document.title = `${{ dashboard: '대시보드', guide: '운영 가이드', settings: '설정' }[v]} | 리뷰 검수함`;
    window.scrollTo(0, 0);
  }
  S.view = v;
}

function go(hash, { replace = false } = {}) {
  if (replace) {
    history.replaceState(null, '', hash);
    route();
  } else if (location.hash === hash) {
    route();
  } else {
    location.hash = hash;
  }
}

// ======================================================================
// 대시보드
// ======================================================================
function aggregates() {
  const rs = S.data.reviews;
  const withAi = rs.filter((r) => r.ai);
  const count = (pred) => withAi.filter(pred).length;
  const days = [];
  const keys = rs.map(dayKey).filter(Boolean).sort();
  if (keys.length) {
    const byDay = new Map();
    for (const r of rs) {
      const k = dayKey(r);
      const d = byDay.get(k) || { count: 0, ratingSum: 0 };
      d.count += 1;
      d.ratingSum += r.rating;
      byDay.set(k, d);
    }
    for (let t = new Date(`${keys[0]}T00:00:00Z`); t <= new Date(`${keys[keys.length - 1]}T00:00:00Z`); t.setUTCDate(t.getUTCDate() + 1)) {
      const k = t.toISOString().slice(0, 10);
      days.push({ date: k, ...(byDay.get(k) || { count: 0, ratingSum: 0 }) });
    }
  }
  const cat = new Map();
  for (const r of withAi) {
    const c = aiOf(r).category;
    const v = cat.get(c) || { n: 0, sum: 0 };
    v.n += 1;
    v.sum += r.rating;
    cat.set(c, v);
  }
  const lang = new Map();
  for (const r of rs) lang.set(langOf(r), (lang.get(langOf(r)) || 0) + 1);
  return {
    total: rs.length,
    withAi: withAi.length,
    pending: rs.filter((r) => r.status === 'pending').length,
    p1: count((r) => aiOf(r).priority === 'P1'),
    human: count((r) => needsHuman(r)),
    urgent: count((r) => aiOf(r).priority === 'P1' || needsHuman(r)),
    cs: count((r) => aiOf(r).needs_cs),
    avg: rs.length ? rs.reduce((a, r) => a + r.rating, 0) / rs.length : 0,
    days,
    cat: [...cat.entries()].map(([k, v]) => ({ key: k, n: v.n, avg: v.sum / v.n })).sort((a, b) => b.n - a.n),
    lang: [...lang.entries()].sort((a, b) => b[1] - a[1]),
    gp: rs.filter((r) => r.store === 'google_play').length,
    as: rs.filter((r) => r.store === 'app_store').length,
  };
}

function renderDashboard(main) {
  const a = aggregates();
  const meta = S.data.meta || {};
  const windowDays = meta.window_days || a.days.length || 1;
  const perDay = (a.total / windowDays).toFixed(1);
  const first = a.days[0]?.date, last = a.days[a.days.length - 1]?.date;
  const period = first ? `${fmtDate.format(new Date(`${first}T12:00:00Z`))}~${fmtDate.format(new Date(`${last}T12:00:00Z`))}` : '';
  const savedMin = a.total * (MIN_NOW - MIN_REVIEW);
  const hasAi = a.withAi > 0;
  const waitCell = '<span class="k-wait">분석 대기</span>';
  const clusters = hasAi ? ROUTES.map(routeCluster).filter(Boolean) : [];

  const lead = hasAi
    ? `<h1>리뷰 ${a.total.toLocaleString('ko-KR')}건 가운데 사람이 먼저 볼 리뷰는 ${a.urgent}건입니다</h1>
       <p>${esc(period)}, ${windowDays}일 동안 하루 평균 ${perDay}건이 들어왔습니다. 결제·계정·진행 불가(P1) ${a.p1}건을 인박스 맨 위에 두었고, AI가 확신하지 못한 ${a.human}건에는 사람 검수 표시를 붙였습니다.${clusters.map((c) => ` ${L.route[c.route]} 쪽은 ${langName(c.lang)} P1 ${c.count}건이 몰려 있습니다(예: ${c.example}).`).join('')}</p>`
    : `<h1>리뷰 ${a.total.toLocaleString('ko-KR')}건을 모았고, AI 분류를 기다리고 있습니다</h1>
       <p>${esc(period)}, ${windowDays}일 동안 하루 평균 ${perDay}건이 들어왔습니다. 분류·번역·답변 초안이 들어오면 우선순위와 부서별 목록이 이 화면에 채워집니다.</p>`;

  main.innerHTML = `
  <div class="page">
    <section class="dash-lead">
      ${lead}
      <div class="cta">
        <a class="btn btn-primary" href="#/inbox">검수 인박스 열기</a>
        ${hasAi ? '<a class="btn" href="#/inbox?priority=P1">P1만 보기</a>' : ''}
        <a class="btn" href="#/inbox?pendingOnly=1">스토어 미답변 ${a.pending}건 보기</a>
      </div>
    </section>

    <section class="tablet" aria-label="핵심 지표">
      <div class="kpi"><div class="k-label">전체 리뷰</div><div class="k-value">${a.total.toLocaleString('ko-KR')}<small>건</small></div><div class="k-foot">AI 분석 ${a.withAi}건${a.total - a.withAi ? `, 대기 ${a.total - a.withAi}건` : ''}</div></div>
      <div class="kpi"><div class="k-label">스토어 미답변</div><div class="k-value">${a.pending}<small>건</small></div><div class="k-foot">스토어에 아직 답변이 없음</div></div>
      <div class="kpi"><div class="k-label">P1 즉시 대응</div><div class="k-value">${hasAi ? `${a.p1}<small>건</small>` : waitCell}</div><div class="k-foot">결제·계정·진행 불가, 24시간 안</div></div>
      <div class="kpi"><div class="k-label">고객센터 이관</div><div class="k-value">${hasAi ? `${a.cs}<small>건</small>` : waitCell}</div><div class="k-foot">계정·결제 내역 조회가 필요</div></div>
      <div class="kpi"><div class="k-label">평균 별점</div><div class="k-value">${a.avg.toFixed(2)}</div><div class="k-foot">수집한 리뷰 기준(스토어 평점과 다름)</div></div>
      <div class="kpi kpi-saving"><div class="k-label">예상 절감 시간</div><div class="k-value">${(savedMin / 60).toFixed(1)}<small>시간</small></div>
        <div class="k-foot">${a.total}건 × (${MIN_NOW}분 − 검수 ${MIN_REVIEW}분) = ${savedMin.toLocaleString('ko-KR')}분. 1건당 ${MIN_NOW}분은 운영팀 실측, 검수 ${MIN_REVIEW}분은 가정값입니다.</div></div>
    </section>

    <div class="dash-grid">
      <section class="panel span-7" aria-labelledby="h-daily">
        <div class="panel-head"><h3 id="h-daily">일별 리뷰 수와 별점 추이</h3><span class="sub">UTC 날짜 기준</span></div>
        <p class="chart-label">일별 리뷰 수</p>
        <div id="c-daily"></div>
        <p class="chart-label">최근 7일 평균 별점</p>
        <div id="c-rating"></div>
        <details class="table-toggle"><summary>표로 보기</summary><div class="table-scroll" id="t-daily"></div></details>
      </section>
      <section class="panel span-5" aria-labelledby="h-cat">
        <div class="panel-head"><h3 id="h-cat">유형별 리뷰 수</h3><span class="sub">유형마다 평균 별점</span></div>
        <div id="c-cat">${hasAi ? '' : '<p class="empty">AI 분류가 끝나면 유형 분포와 유형별 평균 별점이 나타납니다.</p>'}</div>
      </section>
      <section class="panel span-5" aria-labelledby="h-lang">
        <div class="panel-head"><h3 id="h-lang">언어별 리뷰 수</h3><span class="sub">${hasAi ? 'AI가 판별한 실제 언어' : '수집 언어 기준'}</span></div>
        <div id="c-lang"></div>
        <div class="store-split">
          <div><b>${a.gp}</b>Google Play <span class="muted">${a.total ? Math.round((a.gp / a.total) * 100) : 0}%</span></div>
          <div><b>${a.as}</b>App Store <span class="muted">${a.total ? Math.round((a.as / a.total) * 100) : 0}%</span></div>
        </div>
      </section>
      <section class="panel span-7" aria-labelledby="h-reuse">
        <div class="panel-head"><h3 id="h-reuse">지금 쓰는 답변 문구</h3><span class="sub">스토어에 이미 달린 답변 기준</span></div>
        ${reuseBlock()}
      </section>
    </div>

    <section aria-labelledby="h-routes" style="margin-bottom:32px">
      <h2 class="section-title" id="h-routes">부서별 전달 목록</h2>
      <p class="section-note">답변과 별개로 각 부서가 알아야 할 리뷰입니다. 우선순위와 공감 수가 높은 순서로 묶었습니다. 주간 회의에 그대로 붙여 넣을 수 있게 복사 버튼을 두었습니다.</p>
      ${hasAi ? `<div class="routes">${ROUTES.map(routeColumn).join('')}</div>` : '<p class="empty">AI 분류가 끝나면 고객센터·개발·사업(BM)·기획 별로 전달할 리뷰가 모입니다.</p>'}
    </section>

    <section aria-labelledby="h-compare">
      <h2 class="section-title" id="h-compare">현재 템플릿 답변과 AI 맞춤 답변</h2>
      <p class="section-note">같은 부정 리뷰에 지금 달린 답변(답변이 없으면 그 상태)과 AI 초안을 나란히 놓았습니다. 리뷰 내용을 짚는지 보세요.</p>
      ${compareExamples()}
    </section>
  </div>`;

  const draw = (animate) => {
    const host = $('#c-daily');
    if (!host) return;
    renderDaily(host, $('#c-rating'), a.days, { animate });
  };
  draw(true);
  $('#t-daily').innerHTML = `<table class="data-table"><thead><tr><th>날짜</th><th class="num">리뷰 수</th><th class="num">그날 평균 별점</th></tr></thead><tbody>${
    a.days.map((d) => `<tr><td>${d.date}</td><td class="num">${d.count}</td><td class="num">${d.count ? (d.ratingSum / d.count).toFixed(2) : '—'}</td></tr>`).join('')}</tbody></table>`;
  if (hasAi) {
    renderHBars($('#c-cat'), a.cat.map((c) => ({
      name: L.category[c.key] || c.key,
      value: c.n,
      extra: `★${c.avg.toFixed(1)}`,
      tip: `<b>${esc(L.category[c.key] || c.key)}</b><br>${c.n}건(${((c.n / a.withAi) * 100).toFixed(1)}%), 평균 별점 ${c.avg.toFixed(2)}`,
    })));
  }
  const langRows = a.lang.slice(0, 10);
  const rest = a.lang.slice(10).reduce((s, [, n]) => s + n, 0);
  if (rest) langRows.push(['기타', rest]);
  renderHBars($('#c-lang'), langRows.map(([k, n]) => ({ name: k === '기타' ? '그 밖의 언어' : langName(k), value: n, tip: `<b>${esc(langName(k))}</b><br>${n}건(${((n / a.total) * 100).toFixed(1)}%)` })));

  $$('[data-copy-route]').forEach((b) => b.addEventListener('click', async () => {
    const ok = await copyText(routeText(b.dataset.copyRoute));
    toast(ok ? `${L.route[b.dataset.copyRoute]} 전달 목록을 복사했습니다.` : '복사하지 못했습니다. 브라우저 권한을 확인하세요.', { error: !ok });
  }));

  const onResize = debounce(() => { if (S.view === 'dashboard') draw(false); }, 150);
  window.removeEventListener('resize', S._dashResize || (() => {}));
  S._dashResize = onResize;
  window.addEventListener('resize', onResize);
}

function reuseBlock() {
  const total = [...S.replyReuse.values()].reduce((s, n) => s + n, 0);
  if (!total) return '<p class="empty">스토어에 달린 답변이 없습니다.</p>';
  const top = [...S.replyReuse.entries()].sort((x, y) => y[1] - x[1]);
  const top5 = top.slice(0, 5).reduce((s, [, n]) => s + n, 0);
  const langs = new Set(S.data.reviews.filter((r) => r.existing_reply).map((r) => r.lang)).size;
  const rows = top.slice(0, 5).map(([t, n]) => `<tr><td>${esc(t.split('\n').slice(1).join(' ').slice(0, 70) || t.slice(0, 70))}…</td><td class="num">${n}회</td></tr>`).join('');
  return `
    <p style="margin:0 0 12px;font-size:16px">답변 <b>${total}</b>건이 서로 다른 문구 <b>${S.replyReuse.size}</b>종으로 쓰였습니다(${langs}개 언어). 가장 많이 쓴 5종이 전체의 ${Math.round((top5 / total) * 100)}%입니다.</p>
    <table class="data-table"><thead><tr><th>가장 많이 반복된 문구(인사 다음 줄부터)</th><th class="num">사용</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function routeItems(route) {
  return S.data.reviews
    .filter((r) => aiOf(r)?.route_to === route)
    .sort((x, y) => (PRIO_RANK[aiOf(x).priority] - PRIO_RANK[aiOf(y).priority]) || ((y.thumbs_up || 0) - (x.thumbs_up || 0)) || (parseTime(y.created_at) - parseTime(x.created_at)));
}
// 한 부서의 P1이 한 언어에 몰리면(5건 이상, 그 부서 P1의 60% 이상, 전체 리뷰 비중의 3배 이상) 지역 서버·빌드 문제일 가능성이 크다.
// 주 언어(한국어)는 원래 많으므로 비중 배수로 거른다. 요약에서 자주 나온 말과 대표 요약을 같이 보여준다.
function routeCluster(route) {
  const p1 = routeItems(route).filter((r) => aiOf(r).priority === 'P1');
  if (p1.length < 5) return null;
  const byLang = new Map();
  for (const r of p1) byLang.set(langOf(r), [...(byLang.get(langOf(r)) || []), r]);
  const [lang, rs] = [...byLang.entries()].sort((x, y) => y[1].length - x[1].length)[0];
  const langShare = S.data.reviews.filter((r) => langOf(r) === lang).length / Math.max(1, S.data.reviews.length);
  if (rs.length < 5 || rs.length / p1.length < 0.6 || rs.length / p1.length < langShare * 3) return null;
  const words = new Map();
  for (const r of rs) {
    const ws = (aiOf(r).summary_ko || '').split(/[\s·,()"'“”/]+/).map((x) => x.replace(/(으로|에서|로|후|시)$/, '')).filter((x) => x.length >= 2);
    for (const w of new Set(ws)) words.set(w, (words.get(w) || 0) + 1);
  }
  const top = [...words.entries()].filter(([, n]) => n >= 3).sort((x, y) => y[1] - x[1]).slice(0, 4).map(([w]) => w);
  // 대표 요약: 다른 요약과 겹치는 두 단어 묶음(예: "버전 확인")이 많은 것, 동점이면 겹치는 단어가 많은 것
  const toks = (r) => (aiOf(r).summary_ko || '').split(/[\s·,()"'“”/]+/).filter(Boolean);
  const pairs = new Map();
  for (const r of rs) {
    const t = toks(r);
    for (const pr of new Set(t.slice(1).map((w, i) => `${t[i]} ${w}`))) pairs.set(pr, (pairs.get(pr) || 0) + 1);
  }
  const score = (r) => {
    const t = toks(r);
    const pairScore = t.slice(1).reduce((a, w, i) => a + ((pairs.get(`${t[i]} ${w}`) || 0) > 1 ? pairs.get(`${t[i]} ${w}`) : 0), 0);
    const wordScore = t.reduce((a, w) => a + (words.get(w.replace(/(으로|에서|로|후|시)$/, '')) || 0), 0);
    return pairScore * 100 + wordScore;
  };
  const example = [...rs].sort((x, y) => score(y) - score(x))[0];
  const dates = rs.map((r) => parseTime(r.created_at)).sort((x, y) => x - y);
  const versions = [...new Set(rs.map((r) => r.app_version).filter(Boolean))].sort();
  return { route, lang, count: rs.length, total: p1.length, top, example: aiOf(example).summary_ko, from: dates[0], to: dates[dates.length - 1], versions };
}
function clusterNote(c) {
  return `<div class="cluster" role="note">
    <p class="cl-head">${ICON.warn}<b>${esc(langName(c.lang))} P1 ${c.count}건이 몰렸습니다</b></p>
    <p>${esc(L.route[c.route])} P1 ${c.total}건 중 ${c.count}건, ${esc(fmtDate.format(c.from))}~${esc(fmtDate.format(c.to))}. 대표 증상: “${esc(c.example)}”.${c.top.length ? ` 자주 나온 말: ${esc(c.top.join(', '))}.` : ''}${c.versions.length ? ` 버전 ${esc(c.versions.join(', '))}.` : ''} 지역 서버나 해당 국가 빌드를 먼저 확인하세요.</p>
    <a class="btn btn-sm" href="#/inbox?route=${c.route}&lang=${encodeURIComponent(c.lang)}&priority=P1">${esc(langName(c.lang))} P1 ${c.count}건 보기</a>
  </div>`;
}

function routeColumn(route) {
  const items = routeItems(route);
  const shown = items.slice(0, 6);
  const cluster = routeCluster(route);
  return `<section class="route-col" aria-labelledby="rt-${route}">
    <header><h3 id="rt-${route}">${L.route[route]}</h3><span class="count">${items.length}건</span></header>
    ${cluster ? clusterNote(cluster) : ''}
    ${shown.length ? `<ol>${shown.map((r) => `<li><a href="#/inbox/${encodeURIComponent(r.id)}">${prioTag(r)}<span>${esc(aiOf(r).summary_ko || r.text.slice(0, 40))}</span>
      <span class="r-meta">${L.category[aiOf(r).category] || ''}, ★${r.rating}, ${esc(langName(langOf(r)))}${r.thumbs_up ? `, 공감 ${r.thumbs_up}` : ''}</span></a></li>`).join('')}</ol>` : '<p class="muted" style="margin:0;font-size:14px">전달할 리뷰가 없습니다.</p>'}
    <div class="actions" style="margin-top:10px">
      ${items.length > shown.length ? `<a class="btn btn-sm" href="#/inbox?route=${route}">${items.length}건 모두 보기</a>` : ''}
      ${items.length ? `<button class="btn btn-sm btn-ghost" type="button" data-copy-route="${route}">회의용으로 복사</button>` : ''}
    </div>
  </section>`;
}
function routeText(route) {
  const items = routeItems(route);
  const lines = items.map((r) => {
    const ai = aiOf(r);
    return `- [${ai.priority}] ${ai.summary_ko} (${L.category[ai.category] || ai.category}, ★${r.rating}, ${langName(langOf(r))}, ${fmtDate.format(parseTime(r.created_at))})`;
  });
  return `[${L.route[route]}] 리뷰 ${items.length}건 — 원시인 형님들 키우기 리뷰 검수함\n${lines.join('\n')}`;
}

// 컨설팅 근거로 쓸 대표 사례(결제 P1 미답변, 광고 3개씩, 러시아 접속 불가). 데이터에 없으면 자동 선택으로 채운다.
const FEATURED = ['gp-ko-5237482aa9', 'gp-ko-206cefc715', 'gp-ru-36f3661138'];
function pickCompareExamples(n = 3) {
  const picked = FEATURED.map((id) => S.byId.get(id)).filter((r) => r && r.ai?.reply);
  for (const r of autoCompareExamples(n)) {
    if (picked.length >= n) break;
    if (!picked.includes(r) && !picked.some((p) => p.ai.category === r.ai.category && langOf(p) === langOf(r))) picked.push(r);
  }
  return picked.slice(0, n);
}
function autoCompareExamples(n = 3) {
  // 조치가 필요한 유형의 부정 리뷰 가운데, 같은 문구가 많이 반복된 답변을 우선한다. 유형과 언어가 겹치지 않게 고른다.
  const ACTION = new Set(['ads', 'purchase_issue', 'bug', 'account', 'reward_issue', 'monetization', 'balance', 'performance']);
  const cand = S.data.reviews.filter((r) => r.ai && r.existing_reply && r.rating <= 2 && r.ai.reply && r.ai.reply_strategy === 'personalized');
  const reuse = (r) => S.replyReuse.get(r.existing_reply.text) || 0;
  cand.sort((x, y) => (ACTION.has(y.ai.category) - ACTION.has(x.ai.category)) || (reuse(y) - reuse(x)) || ((y.thumbs_up || 0) - (x.thumbs_up || 0)));
  const out = [];
  for (const strict of [true, false]) {
    for (const r of cand) {
      if (out.length >= n) break;
      if (out.includes(r)) continue;
      if (strict && out.some((o) => o.ai.category === r.ai.category || langOf(o) === langOf(r))) continue;
      out.push(r);
    }
  }
  return out;
}
function compareBox(r, { withAiKo = true } = {}) {
  const reuse = S.replyReuse.get(r.existing_reply?.text) || 0;
  const ko = replyKo(r);
  return `<div class="compare">
    ${r.existing_reply
    ? `<div class="old"><h4>현재 운영 답변(템플릿)${reuse > 1 ? `<span class="reuse">같은 문구 ${reuse}회 사용</span>` : ''}</h4><p class="txt">${esc(r.existing_reply.text)}</p></div>`
    : `<div class="old"><h4>현재 운영 답변 <span class="reuse">아직 없음</span></h4><p class="txt muted">스토어에 답변이 달리지 않았습니다(${esc(relTime(parseTime(r.created_at)))} 작성).${aiOf(r)?.priority === 'P1' ? ' P1은 24시간 안 답변이 목표입니다.' : ''}</p></div>`}
    <div class="new"><h4>AI 초안${r.ai?.reply_strategy === 'template' ? ' (템플릿 변형)' : ' (맞춤 답변)'}</h4><p class="txt">${esc(replyOf(r))}</p>
      ${withAiKo && ko.text ? `<p class="ko">${esc(ko.text)}</p>` : ''}</div>
  </div>`;
}
function compareExamples() {
  const ex = pickCompareExamples();
  if (!ex.length) return '<p class="empty">AI 초안이 준비되면 같은 리뷰에 지금 달린 답변과 AI 초안을 나란히 보여줍니다.</p>';
  return `<div class="compare-list">${ex.map((r) => {
    const ai = aiOf(r);
    return `<article class="compare-card">
      <div class="cc-review">
        <div class="cc-meta">${stars(r.rating)}${prioTag(r)}<span class="chip chip-cat">${esc(L.category[ai.category] || ai.category)}</span><span class="chip chip-lang">${esc(langName(langOf(r)))}</span>
          <a class="btn btn-sm btn-ghost" href="#/inbox/${encodeURIComponent(r.id)}">인박스에서 열기</a></div>
        <blockquote>${esc(r.title ? `${r.title} — ${r.text}` : r.text)}</blockquote>
        ${ai.translation_ko ? `<p class="cc-tr">${esc(ai.translation_ko)}</p>` : ''}
      </div>
      ${compareBox(r)}
    </article>`;
  }).join('')}</div>`;
}

// ======================================================================
// 검수 인박스
// ======================================================================
const rowEls = new Map();

function matches(r, f, { ignoreProc = false } = {}) {
  const ai = aiOf(r);
  if (!ignoreProc && f.proc !== 'all' && procOf(r) !== f.proc) return false;
  if (f.pendingOnly && r.status !== 'pending') return false;
  if (f.store && r.store !== f.store) return false;
  if (f.lang && langOf(r) !== f.lang) return false;
  if (f.rating && String(r.rating) !== f.rating) return false;
  if (f.category && ai?.category !== f.category) return false;
  if (f.priority && (f.priority === 'wait' ? ai : ai?.priority !== f.priority)) return false;
  if (f.route && ai?.route_to !== f.route) return false;
  if (f.q) {
    const hay = `${r.title || ''}\n${r.text}\n${ai?.translation_ko || ''}\n${ai?.summary_ko || ''}`.toLowerCase();
    if (!f.q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w))) return false;
  }
  return true;
}
function sortReviews(list) {
  return list.sort((x, y) => {
    const ax = aiOf(x), ay = aiOf(y);
    if (!!ax !== !!ay) return ax ? -1 : 1;
    if (x.temp !== y.temp) return x.temp ? -1 : 1;
    if (ax && ay) {
      // P1 → P2 → P3, 같은 등급 안에서 사람 검수 먼저. 검수 표시를 등급보다 앞에 두면 의미 없는 P3가 결제 오류 P1보다 위에 온다.
      const p = PRIO_RANK[ax.priority] - PRIO_RANK[ay.priority];
      if (p) return p;
      const hx = needsHuman(x), hy = needsHuman(y);
      if (hx !== hy) return hx ? -1 : 1;
    }
    return parseTime(y.created_at) - parseTime(x.created_at);
  });
}

function rowEl(r) {
  let a = rowEls.get(r.id);
  if (!a) {
    a = document.createElement('a');
    a.className = 'row';
    a.href = `#/inbox/${encodeURIComponent(r.id)}`;
    a.dataset.id = r.id;
    rowEls.set(r.id, a);
    a.innerHTML = rowInner(r);
  }
  return a;
}
function rowInner(r) {
  const ai = aiOf(r);
  const summary = ai?.summary_ko;
  const edited = isEdited(r);
  return `<div class="r-top">${stars(r.rating)}${prioTag(r)}${needsHuman(r) ? humanTag() : ''}
      ${ai ? `<span class="chip chip-cat">${esc(L.category[ai.category] || ai.category)}</span>` : ''}
      <span class="chip chip-lang">${esc(langName(langOf(r)))}</span>${r.store === 'app_store' ? '<span class="chip chip-lang">App Store</span>' : ''}
      ${r.temp ? '<span class="chip chip-temp">붙여넣은 리뷰</span>' : ''}${r.content_changed_at ? '<span class="chip chip-temp">내용 변경됨</span>' : ''}${edited ? '<span class="chip chip-edit">수정됨</span>' : ''}</div>
    <div class="r-side"><span>${esc(relTime(parseTime(r.created_at)))}</span>${procTag(r)}</div>
    <div class="r-text${summary ? '' : ' raw'}">${esc(summary || (r.title ? `${r.title} — ${r.text}` : r.text))}</div>`;
}
function updateRow(id) {
  const r = S.byId.get(id);
  const a = rowEls.get(id);
  if (r && a) {
    a.innerHTML = rowInner(r);
    a.classList.toggle('is-done', procOf(r) !== 'todo');
  }
}

function optionList(entries, current, allLabel) {
  return `<option value="">${esc(allLabel)}</option>${entries.map(([v, label]) => `<option value="${esc(v)}"${v === current ? ' selected' : ''}>${esc(label)}</option>`).join('')}`;
}

function renderInbox(main) {
  rowEls.clear();
  const f = S.filters;
  const langs = new Map();
  for (const r of S.reviews) langs.set(langOf(r), (langs.get(langOf(r)) || 0) + 1);
  const langEntries = [...langs.entries()].sort((x, y) => y[1] - x[1]).map(([k, n]) => [k, `${langName(k)} ${n}`]);
  const hasKey = !!apiKey();

  main.innerHTML = `
  <section class="inbox" data-mode="list">
    <div class="inbox-bar">
      <label class="search"><span class="sr-only">리뷰 검색</span>${ICON.search}
        <input id="q" class="input" type="search" placeholder="원문·번역·요약에서 찾기" value="${esc(f.q)}" autocomplete="off"><kbd>/</kbd></label>
      <button id="bulk" class="btn" type="button"></button>
      <span class="spacer"></span>
      <button id="paste-toggle" class="btn btn-ghost" type="button" aria-expanded="false" aria-controls="paste">새 리뷰 붙여넣기</button>
      <button id="exp-json" class="btn" type="button">승인 답변 JSON</button>
      <button id="exp-csv" class="btn" type="button">CSV</button>
    </div>
    <div id="paste" class="paste" hidden>
      <form class="paste-inner" id="paste-form">
        <label class="field"><span>리뷰 원문(스토어 밖의 커뮤니티 글도 됩니다)</span><textarea class="textarea" name="text" required placeholder="리뷰를 붙여넣으세요"></textarea></label>
        <label class="field"><span>스토어</span><select class="select" name="store"><option value="google_play">Google Play(350자 한도)</option><option value="app_store">App Store</option></select></label>
        <label class="field"><span>별점</span><select class="select" name="rating">${[5, 4, 3, 2, 1].map((n) => `<option value="${n}"${n === 3 ? ' selected' : ''}>${n}점</option>`).join('')}</select></label>
        <button class="btn btn-primary" type="submit"${hasKey ? '' : ' disabled'}>분석하고 추가</button>
        <p class="paste-note">${hasKey ? 'Claude가 분류·번역·초안을 만들어 인박스 맨 위에 임시 항목으로 넣습니다. 스토어 리뷰 ID가 없어 내보내기 파일에는 빈 ID로 들어갑니다.' : '이 기능은 Claude API 키가 필요합니다. <a href="#/settings">설정</a>에서 키를 넣으세요.'}</p>
      </form>
    </div>
    <div class="inbox-panes">
      <div class="list-pane">
        <div class="filters">
          <div class="seg" role="group" aria-label="처리 상태" id="seg"></div>
          <div class="filter-grid">
            <label><span class="sr-only">스토어</span><select class="select" data-f="store">${optionList(Object.entries(L.store), f.store, '모든 스토어')}</select></label>
            <label><span class="sr-only">언어</span><select class="select" data-f="lang">${optionList(langEntries, f.lang, '모든 언어')}</select></label>
            <label><span class="sr-only">별점</span><select class="select" data-f="rating">${optionList([5, 4, 3, 2, 1].map((n) => [String(n), `별점 ${n}점`]), f.rating, '모든 별점')}</select></label>
            <label><span class="sr-only">유형</span><select class="select" data-f="category">${optionList(Object.entries(L.category), f.category, '모든 유형')}</select></label>
            <label><span class="sr-only">우선순위</span><select class="select" data-f="priority">${optionList([...Object.entries(L.priority), ['wait', '분석 대기']], f.priority, '모든 우선순위')}</select></label>
            <label><span class="sr-only">담당 부서</span><select class="select" data-f="route">${optionList(ROUTES.map((k) => [k, `담당 ${L.route[k]}`]).concat([['none', '담당 없음']]), f.route, '모든 담당')}</select></label>
          </div>
          <div class="filter-foot">
            <label class="check"><input type="checkbox" id="pending-only"${f.pendingOnly ? ' checked' : ''}>스토어 미답변만</label>
            <span><span id="count"></span> <button class="btn btn-sm btn-ghost" type="button" id="reset-f">필터 초기화</button></span>
          </div>
        </div>
        <div class="list" id="list" aria-label="리뷰 목록"></div>
      </div>
      <div class="detail-pane" id="detail-pane"></div>
    </div>
  </section>`;

  $('#q').addEventListener('input', debounce((e) => { S.filters.q = e.target.value.trim(); applyFilters(); }, 120));
  $$('[data-f]').forEach((s) => s.addEventListener('change', () => { S.filters[s.dataset.f] = s.value; applyFilters(); }));
  $('#pending-only').addEventListener('change', (e) => { S.filters.pendingOnly = e.target.checked; applyFilters(); });
  $('#reset-f').addEventListener('click', () => { S.filters = defaultFilters(); renderInbox(main); selectReview(S.selectedId); });
  $('#bulk').addEventListener('click', bulkApprove);
  $('#exp-json').addEventListener('click', () => exportApproved('json'));
  $('#exp-csv').addEventListener('click', () => exportApproved('csv'));
  $('#paste-toggle').addEventListener('click', (e) => {
    const p = $('#paste');
    p.hidden = !p.hidden;
    e.currentTarget.setAttribute('aria-expanded', String(!p.hidden));
    if (!p.hidden) $('textarea', p).focus();
  });
  $('#paste-form').addEventListener('submit', onPasteSubmit);
  $('#list').addEventListener('click', (e) => {
    const a = e.target.closest('a.row');
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    go(`#/inbox/${encodeURIComponent(a.dataset.id)}`);
  });
  applyFilters();
}

function renderSeg() {
  const seg = $('#seg');
  if (!seg) return;
  const counts = { todo: 0, approved: 0, held: 0, posted: 0, all: 0 };
  for (const r of S.reviews) {
    if (!matches(r, S.filters, { ignoreProc: true })) continue;
    counts[procOf(r)] += 1;
    counts.all += 1;
  }
  seg.innerHTML = ['todo', 'approved', 'held', 'posted', 'all'].map((k) =>
    `<button type="button" data-proc="${k}" aria-pressed="${S.filters.proc === k}">${L.proc[k]}<span class="n">${counts[k]}</span></button>`).join('');
  $$('button', seg).forEach((b) => b.addEventListener('click', () => { S.filters.proc = b.dataset.proc; applyFilters(); }));
}

function bulkCandidates() {
  return S.reviews.filter((r) => {
    const ai = aiOf(r);
    return ai && ai.priority === 'P3' && r.ai.reply_strategy === 'template' && !needsHuman(r) && procOf(r) === 'todo' && canApprove(r).ok;
  });
}
function refreshBar() {
  const n = bulkCandidates().length;
  const b = $('#bulk');
  if (b) {
    b.textContent = `P3 템플릿 답변 일괄 승인 ${n}건`;
    b.disabled = n === 0;
    b.title = n ? '우선순위 P3, 템플릿 답변, 사람 검수 표시가 없는 검수 대기 리뷰를 한 번에 승인합니다.' : '조건에 맞는 검수 대기 리뷰가 없습니다.';
  }
  const approved = exportRows().length;
  for (const id of ['exp-json', 'exp-csv']) {
    const e = $(`#${id}`);
    if (!e) continue;
    e.disabled = approved === 0;
    e.title = approved ? `승인한 답변 ${approved}건을 내려받습니다.` : '승인한 답변이 없습니다.';
  }
  const j = $('#exp-json');
  if (j) j.textContent = `승인 답변 ${approved}건 JSON`;
}

function applyFilters() {
  const list = $('#list');
  if (!list) return;
  S.visible = sortReviews(S.reviews.filter((r) => matches(r, S.filters)));
  const frag = document.createDocumentFragment();
  for (const r of S.visible) {
    const a = rowEl(r);
    a.classList.toggle('is-done', procOf(r) !== 'todo');
    a.setAttribute('aria-current', String(r.id === S.selectedId));
    frag.appendChild(a);
  }
  list.replaceChildren(frag);
  if (!S.visible.length) {
    list.innerHTML = `<p class="list-empty">${S.filters.proc === 'todo' && !S.filters.q ? '검수할 리뷰가 없습니다. 처리 상태를 "전체"로 바꾸면 처리한 리뷰도 볼 수 있습니다.' : '조건에 맞는 리뷰가 없습니다. 필터를 줄여 보세요.'}</p>`;
  }
  $('#count').textContent = `${S.visible.length}건`;
  renderSeg();
  refreshBar();
}

function selectReview(id, { fromRoute = false, focusRow = false } = {}) {
  const prev = S.selectedId;
  S.selectedId = id;
  if (prev && rowEls.get(prev)) rowEls.get(prev).setAttribute('aria-current', 'false');
  const inbox = $('.inbox');
  if (!inbox) return;
  inbox.dataset.mode = id ? 'detail' : 'list';
  const pane = $('#detail-pane');
  if (!id) {
    pane.innerHTML = emptyDetail();
    return;
  }
  const a = rowEls.get(id);
  if (a) {
    a.setAttribute('aria-current', 'true');
    if (fromRoute || focusRow) a.scrollIntoView({ block: 'nearest' });
  }
  renderDetail(pane, S.byId.get(id));
  pane.scrollTop = 0;
  if (window.matchMedia('(max-width: 900px)').matches) window.scrollTo(0, 0);
}

function emptyDetail() {
  const todo = S.reviews.filter((r) => procOf(r) === 'todo').length;
  return `<div class="detail-empty">
    <h2>왼쪽에서 리뷰를 고르세요</h2>
    <p>검수 대기 ${todo}건이 남았습니다. P1부터 위에 있고, 같은 등급에서는 사람 검수 표시가 먼저 옵니다.</p>
    <div class="keys"><kbd>J</kbd><span>다음 리뷰</span><kbd>K</kbd><span>이전 리뷰</span><kbd>A</kbd><span>승인</span><kbd>H</kbd><span>보류</span><kbd>C</kbd><span>답변 복사</span><kbd>/</kbd><span>검색</span></div>
  </div>`;
}

function selectOptions(map, current) {
  return Object.entries(map).map(([v, l]) => `<option value="${esc(v)}"${v === current ? ' selected' : ''}>${esc(l)}</option>`).join('');
}

function renderDetail(pane, r) {
  if (!r) {
    pane.innerHTML = '<div class="detail-empty"><h2>리뷰를 찾지 못했습니다</h2><p>데이터가 갱신되며 빠진 리뷰일 수 있습니다. 목록에서 다시 고르세요.</p></div>';
    return;
  }
  const ai = aiOf(r);
  const l = loc(r.id);
  const lang = langOf(r);
  const created = parseTime(r.created_at);
  const templates = S.data.templates?.[lang] || [];
  const hasKey = !!apiKey();
  const idx = S.visible.findIndex((x) => x.id === r.id);
  const override = l.override && Object.keys(l.override).length;

  pane.innerHTML = `
  <article class="detail" aria-labelledby="d-title">
    <a class="btn btn-sm back" href="#/inbox">목록으로</a>
    <div class="d-head">
      ${prioTag(r)}${needsHuman(r) ? humanTag() : ''}${procTag(r)}${r.temp ? '<span class="chip chip-temp">붙여넣은 리뷰</span>' : ''}${r.content_changed_at ? `<span class="chip chip-temp" title="작성자가 리뷰를 고쳐 다시 분석했습니다">내용 변경됨 ${esc(fmtDate.format(parseTime(r.content_changed_at)))}</span>` : ''}
      <span class="grow"></span>
      <span class="d-nav">
        <button class="btn btn-sm" type="button" data-act="prev"${idx <= 0 ? ' disabled' : ''} title="이전 리뷰 (K)">이전</button>
        <button class="btn btn-sm" type="button" data-act="next"${idx < 0 || idx >= S.visible.length - 1 ? ' disabled' : ''} title="다음 리뷰 (J)">다음</button>
      </span>
    </div>
    <h2 class="d-summary" id="d-title">${esc(ai?.summary_ko || '분석 대기 중인 리뷰')}</h2>

    <section class="review-box" aria-label="리뷰 원문">
      ${r.title ? `<p class="rv-title">${esc(r.title)}</p>` : ''}
      <p class="rv-text" lang="${esc(lang)}">${esc(r.text)}</p>
      <dl class="meta">
        <div><dt>별점</dt><dd>${stars(r.rating)}</dd></div>
        <div><dt>스토어</dt><dd>${esc(L.store[r.store] || r.store)}</dd></div>
        <div><dt>언어</dt><dd>${esc(langName(lang))}${r.country ? `, ${esc(r.country.toUpperCase())}` : ''}</dd></div>
        <div><dt>버전</dt><dd>${esc(r.app_version || '알 수 없음')}</dd></div>
        <div><dt>작성</dt><dd>${created ? `${esc(fmtDateTime.format(created))} (${esc(relTime(created))})` : '알 수 없음'}</dd></div>
        <div><dt>도움돼요</dt><dd>${r.thumbs_up || 0}</dd></div>
        <div><dt>스토어 답변</dt><dd>${r.status === 'replied' ? '있음' : '없음'}</dd></div>
      </dl>
      ${ai?.translation_ko ? `<details open><summary>한국어 번역</summary><p class="rv-tr">${esc(ai.translation_ko)}</p></details>` : ''}
    </section>

    ${ai ? `
    <section class="d-section" aria-labelledby="h-cls">
      <h3 id="h-cls">분류 ${override ? '<span class="chip chip-edit">수동 변경</span> <button class="btn btn-sm btn-ghost" type="button" data-act="reset-cls">AI 분류로 되돌리기</button>' : '<span class="muted" style="font-weight:500">틀렸으면 바꾸세요. 이 브라우저에 저장됩니다.</span>'}</h3>
      <div class="cls">
        <label class="field"><span>유형</span><select class="select" data-cls="category">${selectOptions(L.category, ai.category)}</select></label>
        <label class="field"><span>감정</span><select class="select" data-cls="sentiment">${selectOptions(L.sentiment, ai.sentiment)}</select></label>
        <label class="field"><span>우선순위</span><select class="select" data-cls="priority">${selectOptions(L.priority, ai.priority)}</select></label>
        <label class="field"><span>담당 부서</span><select class="select" data-cls="route_to">${selectOptions({ ...L.route, none: '없음' }, ai.route_to)}</select></label>
      </div>
      <div class="cls-foot">
        ${ai.needs_cs ? '<span class="chip chip-flag hot">고객센터 확인 필요</span>' : ''}
        ${(ai.flags || []).map((fl) => `<span class="chip chip-flag${HOT_FLAGS.has(fl) ? ' hot' : ''}">${esc(L.flags[fl] || fl)}</span>`).join('')}
        <span class="conf">AI 확신도 ${Math.round((ai.confidence ?? 0) * 100)}%, ${r.ai.reply_strategy === 'template' ? '템플릿 답변' : '맞춤 답변'}</span>
      </div>
    </section>` : `
    <section class="d-section"><p class="empty">이 리뷰는 아직 AI 분석 전입니다. 분류·번역·초안이 들어오면 여기서 검수합니다.${hasKey ? ' 지금 바로 초안이 필요하면 아래에서 Claude로 쓰세요.' : ''}</p></section>`}

    <section class="d-section" aria-labelledby="h-reply">
      <h3 id="h-reply">답변 초안 <span class="muted" style="font-weight:500">${esc(langName(lang))}로 작성</span>${isEdited(r) ? ' <span class="chip chip-edit">수정됨</span>' : ''}</h3>
      <div class="editor-wrap">
        <label class="sr-only" for="reply">답변 초안</label>
        <textarea id="reply" class="textarea" lang="${esc(lang)}" spellcheck="false">${esc(replyOf(r))}</textarea>
        <div class="counter-row"><span id="over-msg"></span><span class="counter" id="counter"></span></div>
        <div class="reply-ko" id="reply-ko"></div>
      </div>
      <div class="actions">
        <button class="btn btn-primary" type="button" data-act="approve" id="approve-btn">승인 <kbd>A</kbd></button>
        <button class="btn" type="button" data-act="hold">보류 <kbd>H</kbd></button>
        <span class="tpl-menu">
          <button class="btn" type="button" data-act="tpl" aria-expanded="false"${templates.length ? '' : ' disabled title="이 언어의 템플릿이 아직 없습니다"'}>템플릿 삽입</button>
          <div class="tpl-pop" id="tpl-pop" hidden role="menu">${templates.map((t, i) => `<button type="button" role="menuitem" data-tpl="${i}">${esc(t)}</button>`).join('')}</div>
        </span>
        <button class="btn" type="button" data-act="copy">복사 <kbd>C</kbd></button>
        <button class="btn" type="button" data-act="rewrite"${hasKey ? '' : ' disabled title="설정에서 Claude API 키를 넣으면 쓸 수 있습니다"'}>Claude로 다시 쓰기</button>
        <span class="sep"></span>
        ${procOf(r) === 'approved' ? '<button class="btn" type="button" data-act="posted">등록 완료 표시</button>' : ''}
        ${procOf(r) !== 'todo' ? '<button class="btn btn-ghost" type="button" data-act="undo-status">검수 대기로 되돌리기</button>' : ''}
        ${r.temp ? '<button class="btn btn-ghost btn-danger" type="button" data-act="del-temp">임시 항목 삭제</button>' : ''}
      </div>
      ${hasKey ? '' : '<p class="muted" style="font-size:13px;margin:8px 0 0">Claude로 다시 쓰기는 <a href="#/settings">설정</a>에서 API 키를 넣으면 켜집니다.</p>'}
      ${l.approved_at && procOf(r) !== 'todo' ? `<p class="muted" style="font-size:13px;margin:8px 0 0">${procOf(r) === 'posted' && l.posted_at ? `등록 완료 표시 ${esc(fmtDateTime.format(new Date(l.posted_at)))}, ` : ''}승인 ${esc(fmtDateTime.format(new Date(l.approved_at)))}</p>` : ''}
    </section>

    ${r.existing_reply ? `
    <section class="d-section" aria-labelledby="h-cmp">
      <h3 id="h-cmp">지금 스토어에 달린 답변과 비교 <span class="muted" style="font-weight:500">${r.existing_reply.at ? esc(fmtDateTime.format(parseTime(r.existing_reply.at))) : ''}</span></h3>
      <div id="cmp">${compareBox(r, { withAiKo: false })}</div>
    </section>` : ''}

    <p class="shortcut-hint"><kbd>J</kbd><kbd>K</kbd> 이동, <kbd>A</kbd> 승인, <kbd>H</kbd> 보류, <kbd>C</kbd> 복사, <kbd>/</kbd> 검색. 입력창 안에서는 단축키가 꺼지고 <kbd>Esc</kbd>로 빠져나옵니다.</p>
  </article>`;

  const ta = $('#reply', pane);
  const sync = () => {
    updateCounter(r);
    updateReplyKo(r);
    const cmp = $('#cmp', pane);
    if (cmp) cmp.innerHTML = compareBox(r, { withAiKo: false });
  };
  const persist = debounce(() => { updateRow(r.id); refreshBar(); }, 250);
  ta.addEventListener('input', () => {
    const v = ta.value;
    // 승인·등록 완료 뒤에 고치면 사람이 승인하지 않은 문구가 내보내지므로 검수 대기로 되돌린다
    const wasDone = ['approved', 'posted'].includes(procOf(r));
    // AI 초안과 같아지면 수정 기록을 지운다(번역도 원래 것으로 돌아간다)
    if (r.ai && v === r.ai.reply) patchLocal(r.id, { reply: undefined, reply_ko: undefined, reply_ko_for: undefined });
    else patchLocal(r.id, { reply: v });
    if (wasDone) {
      const was = procOf(r);
      patchLocal(r.id, { status: undefined, approved_at: undefined, edited: undefined });
      const tag = pane.querySelector('.d-head .proc');
      if (tag) tag.outerHTML = procTag(r);
      pane.querySelectorAll('[data-act=posted], [data-act=undo-status]').forEach((b) => b.remove());
      updateRow(r.id);
      refreshBar();
      renderSeg();
      toast(was === 'posted'
        ? '등록 완료한 답변을 고쳐 검수 대기로 되돌렸습니다. 다시 승인하고 스토어에 다시 등록하세요.'
        : '승인한 답변을 고쳐 검수 대기로 되돌렸습니다. 다시 승인해야 내보내기에 들어갑니다.', { timeout: 7000 });
    }
    sync();
    persist();
  });
  sync();

  pane.querySelectorAll('[data-cls]').forEach((s) => s.addEventListener('change', () => {
    const o = { ...(loc(r.id).override || {}) };
    if (s.value === r.ai[s.dataset.cls]) delete o[s.dataset.cls]; else o[s.dataset.cls] = s.value;
    patchLocal(r.id, { override: Object.keys(o).length ? o : undefined });
    updateRow(r.id);
    renderDetail(pane, r);
    toast(`${{ category: '유형', sentiment: '감정', priority: '우선순위', route_to: '담당 부서' }[s.dataset.cls]}을 바꿨습니다. 이 브라우저에 저장됩니다.`);
  }));

  pane.querySelector('.detail').addEventListener('click', (e) => {
    const b = e.target.closest('[data-act], [data-tpl]');
    if (!b || b.disabled) return;
    if (b.dataset.tpl != null) return insertTemplate(r, templates[Number(b.dataset.tpl)]);
    const act = b.dataset.act;
    if (act === 'approve') approve(r);
    else if (act === 'hold') hold(r);
    else if (act === 'copy') copyReply(r);
    else if (act === 'prev') step(-1);
    else if (act === 'next') step(1);
    else if (act === 'rewrite') rewrite(r, b);
    else if (act === 'posted') setStatus(r, 'posted', '등록 완료로 표시했습니다.');
    else if (act === 'undo-status') setStatus(r, undefined, '검수 대기로 되돌렸습니다.');
    else if (act === 'reset-cls') { patchLocal(r.id, { override: undefined }); updateRow(r.id); renderDetail(pane, r); }
    else if (act === 'del-temp') deleteTemp(r);
    else if (act === 'tpl') {
      const pop = $('#tpl-pop', pane);
      pop.hidden = !pop.hidden;
      b.setAttribute('aria-expanded', String(!pop.hidden));
      if (!pop.hidden) pop.querySelector('button')?.focus();
    }
  });
}

function updateCounter(r) {
  const n = charLen(replyOf(r).trim());
  const lim = limitOf(r);
  const gp = r.store === 'google_play';
  const c = $('#counter');
  const msg = $('#over-msg');
  const btn = $('#approve-btn');
  if (!c) return;
  const over = n > lim;
  c.textContent = gp ? `${n} / 350자` : `${n}자 (App Store 한도 5,970자, 권장 350자 안)`;
  c.className = `counter${over ? ' over' : gp && n > 300 ? ' warn' : ''}`;
  msg.innerHTML = over ? `<span class="over-msg">${ICON.warn}${gp ? `${n - lim}자 초과. Google Play는 350자를 넘는 답변을 거부합니다.` : '한도를 넘었습니다.'}</span>` : '';
  const ok = canApprove(r);
  btn.disabled = !ok.ok;
  btn.title = ok.ok ? '이 답변을 승인합니다 (A)' : ok.why;
}
function updateReplyKo(r) {
  const host = $('#reply-ko');
  if (!host) return;
  const ko = replyKo(r);
  if (!ko.text) { host.hidden = true; return; }
  host.hidden = false;
  host.innerHTML = `<span class="field-label">답변 한국어 번역</span>${ko.stale ? ` <span class="stale">${ICON.warn}번역은 원문 수정 전 기준입니다</span>` : ''}<p>${esc(ko.text)}</p>`;
  const svg = host.querySelector('.stale svg');
  if (svg) { svg.setAttribute('width', '13'); svg.setAttribute('height', '13'); }
}

function step(dir) {
  if (!S.visible.length) return;
  const i = S.visible.findIndex((x) => x.id === S.selectedId);
  const next = i < 0 ? S.visible[0] : S.visible[Math.max(0, Math.min(S.visible.length - 1, i + dir))];
  if (next && next.id !== S.selectedId) {
    history.replaceState(null, '', `#/inbox/${encodeURIComponent(next.id)}`);
    selectReview(next.id, { focusRow: true });
  }
}

// 처리 후 다음 리뷰로 넘어간다. 필터 때문에 현재 리뷰가 목록에서 빠지면 그 자리의 다음 리뷰를 고른다.
function afterProcess(r) {
  const before = S.visible.map((x) => x.id);
  const i = before.indexOf(r.id);
  applyFilters();
  const stillThere = S.visible.some((x) => x.id === r.id);
  let nextId = null;
  if (stillThere) {
    const j = S.visible.findIndex((x) => x.id === r.id);
    nextId = S.visible[j + 1]?.id || r.id;
  } else {
    for (const id of before.slice(i + 1)) if (S.visible.some((x) => x.id === id)) { nextId = id; break; }
    if (!nextId) nextId = S.visible[S.visible.length - 1]?.id || null;
  }
  history.replaceState(null, '', nextId ? `#/inbox/${encodeURIComponent(nextId)}` : '#/inbox');
  selectReview(nextId, { focusRow: true });
}

function approve(r) {
  const ok = canApprove(r);
  if (!ok.ok) return toast(ok.why, { error: true });
  const prev = { ...loc(r.id) };
  patchLocal(r.id, { status: 'approved', approved_at: new Date().toISOString(), edited: isEdited(r) });
  updateRow(r.id);
  toast('승인했습니다.', { action: { label: '실행 취소', fn: () => restore([[r.id, prev]]) }, timeout: 4000 });
  afterProcess(r);
}
function hold(r) {
  const prev = { ...loc(r.id) };
  patchLocal(r.id, { status: 'held' });
  updateRow(r.id);
  toast('보류했습니다. 처리 상태 "보류"에서 다시 볼 수 있습니다.', { action: { label: '실행 취소', fn: () => restore([[r.id, prev]]) }, timeout: 4000 });
  afterProcess(r);
}
function setStatus(r, status, msg) {
  const prev = { ...loc(r.id) };
  patchLocal(r.id, { status, posted_at: status === 'posted' ? new Date().toISOString() : prev.posted_at });
  updateRow(r.id);
  applyFilters();
  selectReview(r.id);
  toast(msg, { action: { label: '실행 취소', fn: () => restore([[r.id, prev]]) }, timeout: 4000 });
}
function restore(pairs) {
  for (const [id, prev] of pairs) {
    if (prev && Object.keys(prev).length) S.local[id] = prev; else delete S.local[id];
    updateRow(id);
  }
  saveLocal();
  applyFilters();
  const id = pairs.length === 1 ? pairs[0][0] : S.selectedId;
  if (S.view === 'inbox') {
    history.replaceState(null, '', id ? `#/inbox/${encodeURIComponent(id)}` : '#/inbox');
    selectReview(id, { focusRow: true });
  }
  toast('되돌렸습니다.', { timeout: 2500 });
}
async function copyReply(r) {
  const ok = await copyText(replyOf(r));
  toast(ok ? '답변을 복사했습니다.' : '복사하지 못했습니다. 브라우저 권한을 확인하세요.', { error: !ok, timeout: 2500 });
}
function insertTemplate(r, text) {
  if (!text) return;
  const prev = { ...loc(r.id) };
  patchLocal(r.id, { reply: text, reply_ko: null, reply_ko_for: text });
  updateRow(r.id);
  renderDetail($('#detail-pane'), r);
  toast('템플릿을 넣었습니다. 번역은 제공되지 않습니다.', { action: { label: '되돌리기', fn: () => restore([[r.id, prev]]) } });
}

async function rewrite(r, btn) {
  const key = apiKey();
  if (!key) return toast('설정에서 Claude API 키를 먼저 넣으세요.', { error: true });
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = '다시 쓰는 중…';
  const prev = { ...loc(r.id) };
  try {
    const out = await rewriteReply({
      apiKey: key, model: S.settings.model, tone: S.settings.tone, review: r, ai: aiOf(r),
      currentReply: replyOf(r), templates: S.data.templates?.[langOf(r)] || [],
    });
    const wasDone = ['approved', 'posted'].includes(procOf(r));
    patchLocal(r.id, { reply: out.reply, reply_ko: out.reply_ko, reply_ko_for: out.reply, ...(wasDone ? { status: undefined, approved_at: undefined, edited: undefined } : {}) });
    updateRow(r.id);
    if (S.selectedId === r.id) renderDetail($('#detail-pane'), r);
    const used = MODELS.find((m) => m.id === out.model)?.label || out.model || S.settings.model;
    const fellBack = out.model && !out.model.startsWith(S.settings.model);
    toast(`${fellBack ? `${used} 모델이 대신 답했습니다(${S.settings.model} 거절). ` : `Claude가 답변을 다시 썼습니다(${used}). `}${wasDone ? '승인을 풀었으니 다시 승인하세요.' : ''}`, { action: { label: '되돌리기', fn: () => restore([[r.id, prev]]) } });
  } catch (e) {
    toast(e instanceof ClaudeError ? e.message : `다시 쓰지 못했습니다: ${e.message}`, { error: true });
    if (btn.isConnected) { btn.disabled = false; btn.textContent = label; }
  }
}

function bulkApprove() {
  const cands = bulkCandidates();
  if (!cands.length) return;
  const now = new Date().toISOString();
  const prev = cands.map((r) => [r.id, { ...loc(r.id) }]);
  for (const r of cands) {
    S.local[r.id] = { ...loc(r.id), status: 'approved', approved_at: now, edited: isEdited(r), updated_at: now };
    updateRow(r.id);
  }
  saveLocal();
  applyFilters();
  if (S.selectedId && !S.visible.some((x) => x.id === S.selectedId)) {
    history.replaceState(null, '', '#/inbox');
    selectReview(null);
  } else if (S.selectedId) {
    selectReview(S.selectedId);
  }
  toast(`P3 템플릿 답변 ${cands.length}건을 승인했습니다.`, { action: { label: '실행 취소', fn: () => restore(prev) }, timeout: 8000 });
}

function exportRows() {
  // 한도 초과·빈 답변은 승인 상태여도 내보내지 않는다(이전 버전에서 저장된 상태 대비)
  return S.reviews.filter((r) => procOf(r) === 'approved' && canApprove(r).ok).map((r) => ({
    store: r.store,
    source_review_id: r.source_review_id || '',
    lang: langOf(r),
    rating: r.rating,
    reply: replyOf(r).trim(),
    approved_at: loc(r.id).approved_at || '',
    edited: isEdited(r),
  }));
}
function exportApproved(kind) {
  const rows = exportRows();
  const skipped = S.reviews.filter((r) => procOf(r) === 'approved').length - rows.length;
  if (!rows.length) return toast(skipped ? `승인한 답변 ${skipped}건이 모두 글자 수 한도를 넘거나 비어 있어 내보내지 않았습니다.` : '승인한 답변이 없습니다.', { error: true });
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '');
  if (kind === 'json') {
    download(`approved_replies_${stamp}.json`, JSON.stringify(rows, null, 1), 'application/json');
  } else {
    const cols = ['store', 'source_review_id', 'lang', 'rating', 'reply', 'approved_at', 'edited'];
    const cell = (v) => { const s = String(v); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const csv = `﻿${cols.join(',')}\r\n${rows.map((o) => cols.map((c) => cell(o[c])).join(',')).join('\r\n')}\r\n`;
    download(`approved_replies_${stamp}.csv`, csv, 'text/csv;charset=utf-8');
  }
  toast(`승인 답변 ${rows.length}건을 ${kind.toUpperCase()}로 내려받았습니다.${skipped ? ` 한도를 넘거나 빈 답변 ${skipped}건은 뺐습니다.` : ''} pipeline/post_replies.py 로 등록합니다.`, { error: skipped > 0 });
}

async function onPasteSubmit(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const key = apiKey();
  if (!key) return toast('설정에서 Claude API 키를 먼저 넣으세요.', { error: true });
  const text = form.text.value.trim();
  if (!text) return toast('리뷰 원문을 넣으세요.', { error: true });
  const btn = form.querySelector('button[type=submit]');
  btn.disabled = true;
  btn.textContent = '분석 중…';
  const now = new Date();
  const review = {
    id: `tmp-${now.getTime().toString(36)}`, source_review_id: '', store: form.store.value, lang: 'und', country: '',
    rating: Number(form.rating.value), title: '', text, author: '', created_at: now.toISOString().slice(0, 19),
    app_version: null, thumbs_up: 0, existing_reply: null, status: 'pending', ai: null, temp: true,
  };
  try {
    review.ai = await analyzeReview({ apiKey: key, model: S.settings.model, tone: S.settings.tone, review, templates: [] });
    review.lang = review.ai.detected_lang;
    S.temp.unshift(review);
    saveTemp();
    S.reviews.unshift(review);
    S.byId.set(review.id, review);
    form.reset();
    $('#paste').hidden = true;
    $('#paste-toggle').setAttribute('aria-expanded', 'false');
    S.filters.proc = 'todo';
    applyFilters();
    go(`#/inbox/${encodeURIComponent(review.id)}`);
    toast(review.ai.served_by && !review.ai.served_by.startsWith(S.settings.model)
      ? `붙여넣은 리뷰를 분석해 맨 위에 넣었습니다(${review.ai.served_by} 모델이 대신 답함).`
      : '붙여넣은 리뷰를 분석해 맨 위에 넣었습니다.');
    delete review.ai.served_by;
    saveTemp();
  } catch (err) {
    toast(err instanceof ClaudeError ? err.message : `분석하지 못했습니다: ${err.message}`, { error: true });
  } finally {
    btn.disabled = false;
    btn.textContent = '분석하고 추가';
  }
}
function deleteTemp(r) {
  S.temp = S.temp.filter((t) => t.id !== r.id);
  saveTemp();
  S.reviews = S.reviews.filter((x) => x.id !== r.id);
  S.byId.delete(r.id);
  delete S.local[r.id];
  saveLocal();
  rowEls.delete(r.id);
  afterProcess(r);
  toast('임시 항목을 삭제했습니다.');
}

// 키보드: J·K 이동, A 승인, H 보류, C 복사, / 검색
document.addEventListener('keydown', (e) => {
  if (S.view !== 'inbox' || e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  const textEntry = t.closest?.('textarea, [contenteditable="true"]') || (t.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'file'].includes(t.type));
  if (textEntry) {
    if (e.key === 'Escape') t.blur();
    return;
  }
  // select 는 글자 입력으로 항목을 고르므로 / 와 Esc 만 받는다
  if (t.tagName === 'SELECT') {
    if (e.key === '/') { e.preventDefault(); $('#q')?.focus(); }
    if (e.key === 'Escape') t.blur();
    return;
  }
  if (e.key === 'Escape') {
    const pop = $('#tpl-pop');
    if (pop && !pop.hidden) { pop.hidden = true; return; }
  }
  const k = e.key.toLowerCase();
  const r = S.selectedId ? S.byId.get(S.selectedId) : null;
  if (k === '/') { e.preventDefault(); $('#q')?.focus(); return; }
  if (k === 'j') { e.preventDefault(); step(1); return; }
  if (k === 'k') { e.preventDefault(); step(-1); return; }
  if (!r) return;
  if (k === 'a') { e.preventDefault(); approve(r); }
  else if (k === 'h') { e.preventDefault(); hold(r); }
  else if (k === 'c') { e.preventDefault(); copyReply(r); }
});
document.addEventListener('click', (e) => {
  const pop = $('#tpl-pop');
  if (pop && !pop.hidden && !e.target.closest('.tpl-menu')) pop.hidden = true;
});

// ======================================================================
// 운영 가이드
// ======================================================================
function renderGuide(main) {
  const total = S.data.reviews.length;
  const windowDays = S.data.meta?.window_days || 60;
  const perDay = total / windowDays;
  main.innerHTML = `
  <div class="page guide">
    <h1>운영 가이드</h1>
    <p class="lede">지금은 담당자가 리뷰를 하나씩 읽고, 번역하고, 별점에 맞는 고정 문구를 붙여 넣습니다. 자동화 뒤에는 수집부터 초안까지 기계가 하고, 사람은 검수와 승인만 합니다.</p>

    <div class="flow-wrap">
      <section class="flow" aria-labelledby="h-asis">
        <h3 id="h-asis">지금 방식 <span class="sub">리뷰 1건에 약 ${MIN_NOW}분, 다른 업무에 밀리면 며칠 치를 몰아서</span></h3>
        <ol class="steps asis">
          <li><b>스토어 콘솔 열기</b><p>Google Play Console과 App Store Connect를 따로 돌며 새 리뷰를 찾습니다.</p><span class="who who-human">사람</span></li>
          <li><b>읽고 번역</b><p>13개 언어 리뷰를 번역기로 옮겨 내용을 파악합니다.</p><span class="who who-human">사람</span></li>
          <li><b>고정 문구 고르기</b><p>별점에 맞춰 미리 써 둔 4~5종 가운데 하나를 붙입니다. 광고 불만에도 같은 사과문이 나갑니다.</p><span class="who who-pain">문제</span></li>
          <li><b>등록</b><p>콘솔에서 한 건씩 등록합니다. 부서 공유는 따로 하지 않습니다.</p><span class="who who-human">사람</span></li>
        </ol>
      </section>
      <section class="flow" aria-labelledby="h-tobe">
        <h3 id="h-tobe">자동화 뒤 <span class="sub">사람은 5단계만, 1건에 약 ${MIN_REVIEW}분</span></h3>
        <ol class="steps">
          <li><b>수집</b><p>매일 09:00 공식 API로 두 스토어의 새 리뷰를 가져옵니다.</p><span class="who who-auto">자동</span></li>
          <li><b>유형 분류</b><p>11개 유형, 우선순위, 담당 부서를 매깁니다(Claude Haiku 4.5).</p><span class="who who-auto">자동</span></li>
          <li><b>번역</b><p>한국어가 아닌 리뷰와 답변을 한국어로 옮깁니다.</p><span class="who who-auto">자동</span></li>
          <li><b>답변 초안</b><p>짧은 칭찬은 템플릿 3종을 돌려 쓰고, 나머지는 리뷰를 짚는 맞춤 답변을 씁니다(Claude Sonnet 5.5).</p><span class="who who-auto">자동</span></li>
          <li class="human-step"><b>검수·승인</b><p>이 화면에서 P1부터 읽고 고치고 승인합니다. 부서별 목록을 회의에 넘깁니다.</p><span class="who who-human">사람</span></li>
          <li><b>등록</b><p>승인한 답변만 스크립트가 API로 등록합니다. 사람 승인 없이 올라가지 않습니다.</p><span class="who who-auto">자동</span></li>
        </ol>
      </section>
    </div>

    <h2>시간으로 보면</h2>
    <div class="compare-time">
      <div class="panel"><span class="muted">지금 하루 처리 시간</span><b>${(perDay * MIN_NOW).toFixed(0)}분</b><p>하루 평균 ${perDay.toFixed(1)}건 × ${MIN_NOW}분. 몰아서 처리하면 Play API가 1주 이전 리뷰를 주지 않아 놓치는 리뷰가 생깁니다.</p></div>
      <div class="panel"><span class="muted">자동화 뒤 하루 검수 시간</span><b>${Math.max(1, Math.round(perDay * MIN_REVIEW))}분</b><p>하루 평균 ${perDay.toFixed(1)}건 × 검수 ${MIN_REVIEW}분(가정). P3 템플릿 답변은 일괄 승인합니다.</p></div>
      <div class="panel"><span class="muted">이번 데이터 기준 절감</span><b>${((total * (MIN_NOW - MIN_REVIEW)) / 60).toFixed(1)}시간</b><p>${windowDays}일 동안 ${total}건 × (${MIN_NOW}분 − ${MIN_REVIEW}분). 검수 시간은 실제 운영에서 다시 재야 합니다.</p></div>
    </div>

    <h2>운영 전환 체크리스트</h2>
    <ul class="checklist">
      ${[
        ['Google Play 서비스 계정', 'Google Cloud에서 서비스 계정을 만들고 Play Console [사용자 및 권한]에서 이 앱의 "리뷰 답변" 권한을 줍니다.'],
        ['App Store Connect API 키', '고객 지원 권한 이상의 .p8 키를 발급하고 Key ID·Issuer ID를 기록합니다.'],
        ['Claude API 키', '운영용 키를 GitHub Actions Secrets(ANTHROPIC_API_KEY)에 넣습니다. 저장소에 넣지 않습니다.'],
        ['매일 실행 스케줄', '.github/workflows/refresh-reviews.yml 의 schedule 주석을 풀면 매일 09:00(KST)에 수집·초안이 돕니다.'],
        ['P1 알림', '결제·계정 신규 건이 생기면 사내 메신저 웹훅으로 알립니다(파이프라인 마지막 단계에 추가).'],
        ['검수 상태 저장소', '지금은 이 브라우저에만 저장됩니다. 두 명 이상이 쓰면 DB(예: Supabase, Firestore)로 옮깁니다.'],
        ['등록 스크립트 시험', 'pipeline/post_replies.py 를 --dry-run 으로 먼저 돌려 대상과 글자 수를 확인한 뒤 --execute 로 등록합니다.'],
        ['답변 지침 관리', '말투를 바꾸려면 pipeline/prompts/reply-guidelines.md 를 고치고 초안을 다시 만듭니다.'],
      ].map(([b, s]) => `<li>${ICON.box}<div><b>${esc(b)}</b><span>${esc(s)}</span></div></li>`).join('')}
    </ul>

    <h2>데이터 출처와 한계</h2>
    <ul class="limits">
      <li>이 화면은 공개 데이터로 만든 PoC입니다. Google Play는 google-play-scraper, App Store는 iTunes 고객 리뷰 RSS로 ${windowDays}일 치를 모았습니다.</li>
      <li>App Store RSS는 국가별 최근 리뷰만 주고 개발자 답변이 없습니다. 그래서 App Store 리뷰는 모두 "스토어 답변 없음"으로 보입니다.</li>
      <li>Google Play 리뷰는 13개 언어로 나눠 모았습니다. 같은 리뷰가 여러 언어 목록에 걸리면 한 번만 남겼습니다.</li>
      <li>작성자 이름은 수집 단계에서 해시 별칭으로 바꾸고 화면에 보여주지 않습니다.</li>
      <li>평균 별점은 수집한 리뷰의 평균입니다. 스토어에 표시되는 누적 평점(Play 4.47)과 다릅니다.</li>
      <li>이 사이트는 실제 스토어에 답변을 등록하지 않습니다. 승인한 답변은 파일로 내려받아 등록 스크립트에 넘깁니다.</li>
    </ul>
  </div>`;
}

// ======================================================================
// 설정
// ======================================================================
function renderSettings(main) {
  const key = apiKey();
  const nState = Object.keys(S.local).length;
  const counts = { approved: 0, held: 0, posted: 0, edited: 0 };
  for (const [id, v] of Object.entries(S.local)) {
    if (v.status) counts[v.status] = (counts[v.status] || 0) + 1;
    const r = S.byId.get(id);
    if (r && isEdited(r)) counts.edited += 1;
  }
  const theme = storage.getRaw(LS.theme) || 'system';
  main.innerHTML = `
  <div class="page settings">
    <h1>설정</h1>

    <section class="panel" aria-labelledby="h-key">
      <h2 id="h-key">Claude API 키</h2>
      <p>답변 다시 쓰기와 새 리뷰 붙여넣기에 씁니다. 키는 이 브라우저의 localStorage 에만 저장되고 서버로 보내지 않습니다(요청은 브라우저에서 api.anthropic.com 으로 바로 갑니다).</p>
      <form class="form-row" id="key-form" autocomplete="off">
        <label class="field"><span>API 키</span><input class="input" type="password" name="key" placeholder="${key ? '저장된 키가 있습니다. 바꾸려면 새 키를 넣으세요' : 'sk-ant-로 시작하는 키'}" autocomplete="off" spellcheck="false"></label>
        <button class="btn btn-primary" type="submit">키 저장</button>
        <button class="btn btn-danger" type="button" id="key-del"${key ? '' : ' disabled'}>키 삭제</button>
      </form>
      <p class="key-state ${key ? 'on' : 'off'}">${key ? `저장됨, 끝자리 ${esc(key.slice(-4))}` : '저장된 키가 없습니다'}</p>
      <p class="risk">${ICON.warn}<span>브라우저에서 바로 호출하려면 <code>anthropic-dangerous-direct-browser-access: true</code> 헤더가 필요합니다. 같은 브라우저의 확장 프로그램이나 스크립트가 키를 읽을 수 있으니 사용 한도를 걸어 둔 개인 시험용 키를 쓰고, 다 쓰면 삭제하세요.</span></p>
      <div class="form-row" style="margin-top:16px">
        <label class="field"><span>모델</span><select class="select" id="model">${MODELS.map((m) => `<option value="${m.id}"${m.id === S.settings.model ? ' selected' : ''}>${esc(m.label)} — ${esc(m.note)}</option>`).join('')}</select></label>
      </div>
    </section>

    <section class="panel" aria-labelledby="h-tone">
      <h2 id="h-tone">답변 톤</h2>
      <p>Claude로 다시 쓸 때 프롬프트에 붙는 요청입니다. 서명(GM Mr. Bow)과 350자 한도는 바뀌지 않습니다.</p>
      <div class="radios" role="radiogroup" aria-label="답변 톤">
        ${[['default', '기본'], ['shorter', '더 짧게'], ['polite', '더 공손하게']].map(([v, l]) => `<label><input type="radio" name="tone" value="${v}"${S.settings.tone === v ? ' checked' : ''}>${l}</label>`).join('')}
      </div>
    </section>

    <section class="panel" aria-labelledby="h-theme">
      <h2 id="h-theme">화면 테마</h2>
      <p>상단 바의 버튼으로도 바꿀 수 있습니다.</p>
      <div class="radios" role="radiogroup" aria-label="화면 테마">
        ${[['system', '시스템 설정 따르기'], ['light', '밝게'], ['dark', '어둡게']].map(([v, l]) => `<label><input type="radio" name="theme" value="${v}"${theme === v ? ' checked' : ''}>${l}</label>`).join('')}
      </div>
    </section>

    <section class="panel" aria-labelledby="h-state">
      <h2 id="h-state">검수 상태</h2>
      <p>승인·보류·수정한 답변은 이 브라우저에만 있습니다. 다른 컴퓨터로 옮기거나 지우기 전에 백업하세요.</p>
      <div class="stat-line"><span>기록 ${nState}건</span><span>승인 ${counts.approved}</span><span>보류 ${counts.held}</span><span>등록 완료 ${counts.posted}</span><span>답변 수정 ${counts.edited}</span><span>붙여넣은 리뷰 ${S.temp.length}</span></div>
      <div class="form-row">
        <button class="btn" type="button" id="backup">상태 백업 내려받기</button>
        <label class="btn" for="restore-file">백업 파일 가져오기</label>
        <input type="file" id="restore-file" accept="application/json,.json" class="sr-only">
        <button class="btn btn-danger" type="button" id="reset-state"${nState || S.temp.length ? '' : ' disabled'}>검수 상태 초기화</button>
      </div>
    </section>
  </div>`;

  $('#key-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const v = e.currentTarget.key.value.trim();
    if (!v) return toast('키를 넣으세요.', { error: true });
    if (!/^sk-ant-/.test(v)) toast('보통 sk-ant- 로 시작합니다. 그래도 저장했습니다.', { error: true });
    if (!storage.setRaw(LS.key, v)) return toast('이 브라우저가 저장소를 막아 키를 저장하지 못했습니다.', { error: true });
    renderSettings(main);
    toast('API 키를 이 브라우저에 저장했습니다.');
  });
  $('#key-del').addEventListener('click', () => {
    storage.del(LS.key);
    renderSettings(main);
    toast('API 키를 삭제했습니다.');
  });
  $('#model').addEventListener('change', (e) => { S.settings.model = e.target.value; saveSettings(); toast('모델을 바꿨습니다.', { timeout: 2000 }); });
  $$('input[name=tone]').forEach((i) => i.addEventListener('change', () => { S.settings.tone = i.value; saveSettings(); }));
  $$('input[name=theme]').forEach((i) => i.addEventListener('change', () => setTheme(i.value)));
  $('#backup').addEventListener('click', () => {
    const payload = { kind: 'tgr-review-state', version: 1, exported_at: new Date().toISOString(), state: S.local, temp: S.temp, settings: S.settings };
    download(`review_state_${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(payload, null, 1), 'application/json');
    toast('검수 상태를 내려받았습니다. API 키는 백업에 넣지 않았습니다.');
  });
  $('#restore-file').addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const p = JSON.parse(await file.text());
      if (p.kind !== 'tgr-review-state' || typeof p.state !== 'object') throw new Error('검수 상태 백업 파일이 아닙니다.');
      const prev = { local: S.local, temp: S.temp };
      S.local = p.state || {};
      S.temp = Array.isArray(p.temp) ? p.temp.map((t) => ({ ...t, temp: true })) : [];
      saveLocal();
      saveTemp();
      rebuildReviews();
      renderSettings(main);
      toast(`백업을 가져왔습니다(기록 ${Object.keys(S.local).length}건).`, { action: { label: '실행 취소', fn: () => { S.local = prev.local; S.temp = prev.temp; saveLocal(); saveTemp(); rebuildReviews(); renderSettings(main); } } });
    } catch (err) {
      toast(`가져오지 못했습니다: ${err.message}`, { error: true });
    }
  });
  $('#reset-state').addEventListener('click', () => {
    const prev = { local: S.local, temp: S.temp };
    S.local = {};
    S.temp = [];
    saveLocal();
    saveTemp();
    rebuildReviews();
    renderSettings(main);
    toast('검수 상태를 초기화했습니다.', { action: { label: '실행 취소', fn: () => { S.local = prev.local; S.temp = prev.temp; saveLocal(); saveTemp(); rebuildReviews(); renderSettings(main); } }, timeout: 10000 });
  });
}
function rebuildReviews() {
  S.reviews = [...S.temp, ...S.data.reviews];
  S.byId = new Map(S.reviews.map((r) => [r.id, r]));
  rowEls.clear();
}

// ---------- 테마 ----------
function effectiveTheme() {
  const t = document.documentElement.dataset.theme;
  if (t === 'light' || t === 'dark') return t;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function syncThemeButton() {
  const b = $('#theme-toggle');
  const dark = effectiveTheme() === 'dark';
  b.setAttribute('aria-label', dark ? '밝은 화면으로 전환' : '어두운 화면으로 전환');
  b.title = b.getAttribute('aria-label');
}
function setTheme(v) {
  if (v === 'light' || v === 'dark') {
    document.documentElement.dataset.theme = v;
    storage.setRaw(LS.theme, v);
  } else {
    delete document.documentElement.dataset.theme;
    storage.del(LS.theme);
  }
  syncThemeButton();
}
$('#theme-toggle').addEventListener('click', () => setTheme(effectiveTheme() === 'dark' ? 'light' : 'dark'));
window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', syncThemeButton);
syncThemeButton();

load();
