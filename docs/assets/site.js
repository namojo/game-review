// 산출물 문서 사이트: 테마, 모바일 목록, 코드 복사, 언어 필터·규격 탭. 스크립트가 없어도 문서는 모두 읽힌다.
(function () {
  var root = document.documentElement;
  root.classList.add('js');
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  // 테마
  var themeBtn = document.querySelector('.top .theme');
  function effective() {
    var t = root.dataset.theme;
    if (t === 'light' || t === 'dark') return t;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  if (themeBtn) themeBtn.addEventListener('click', function () {
    var next = effective() === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    store('tgdocs.theme', next);
  });

  // 모바일 문서 목록
  var toggle = document.querySelector('.nav-toggle');
  var side = document.getElementById('side');
  if (toggle && side) {
    toggle.addEventListener('click', function () {
      var open = side.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? '문서 목록 닫기' : '문서 목록 열기');
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && side.classList.contains('open')) toggle.click(); });
    document.addEventListener('click', function (e) {
      if (side.classList.contains('open') && !side.contains(e.target) && !toggle.contains(e.target)) toggle.click();
    });
  }
  var cur = document.querySelector('.side a[aria-current="page"]');
  if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'center' });

  // 좁은 화면에서는 목차를 접어 둔다
  if (window.matchMedia('(max-width: 1180px)').matches) {
    var toc = document.querySelector('.toc details');
    if (toc) toc.removeAttribute('open');
  }

  // 코드 블록 복사
  document.querySelectorAll('.doc pre').forEach(function (pre) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'copy-btn';
    b.textContent = '복사';
    b.addEventListener('click', function () {
      var text = (pre.querySelector('code') || pre).innerText;
      var done = function (ok) { b.textContent = ok ? '복사함' : '복사 안 됨'; setTimeout(function () { b.textContent = '복사'; }, 1500); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
      else done(false);
    });
    pre.appendChild(b);
  });

  // 언어 필터(다국어 문구 표·배너 갤러리)
  var state = { lang: '', fmt: 'landscape' };
  function apply() {
    document.querySelectorAll('.doc [data-lang]').forEach(function (el) {
      if (el.tagName === 'BUTTON') return;
      var okLang = !state.lang || el.dataset.lang === state.lang;
      var okFmt = !el.dataset.fmt || el.dataset.fmt === state.fmt;
      el.hidden = !(okLang && okFmt);
    });
    document.querySelectorAll('.lang-filter button').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.lang === state.lang)); });
    document.querySelectorAll('.tabs button[data-fmt]').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.fmt === state.fmt)); });
    document.querySelectorAll('.gallery.banners').forEach(function (g) { g.dataset.fmt = state.fmt; });
  }
  document.querySelectorAll('.lang-filter button').forEach(function (b) {
    b.addEventListener('click', function () { state.lang = b.dataset.lang; apply(); });
  });
  document.querySelectorAll('.tabs button[data-fmt]').forEach(function (b) {
    b.addEventListener('click', function () { state.fmt = b.dataset.fmt; apply(); });
  });
  if (document.querySelector('.tabs button[data-fmt]') || document.querySelector('.lang-filter')) apply();
})();
