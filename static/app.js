/* Leitor de PDF — Mini App do Telegram (JS puro + PDF.js 3.x) */
(function () {
  'use strict';

  var PDFJS_WORKER = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
  var MIN_ZOOM = 0.5;          // relativo a "ajustar à largura"
  var MAX_ZOOM = 5;
  var MAX_RENDERED = 10;       // páginas com canvas em memória ao mesmo tempo
  var MAX_CANVAS_PIXELS = 16777216;

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  var inTelegram = !!(tg && tg.initData);
  var pdfjsLib = window.pdfjsLib;

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    title: $('doc-title'), viewer: $('viewer'), pages: $('pages'), banner: $('banner'),
    state: $('state'), stateSpinner: $('state-spinner'), stateIcon: $('state-icon'),
    stateTitle: $('state-title'), stateText: $('state-text'), stateProgress: $('state-progress'),
    stateProgressBar: $('state-progress-bar'), stateRetry: $('state-retry'), stateLocal: $('state-local'),
    bottombar: $('bottombar'), zoomIn: $('zoom-in'), zoomOut: $('zoom-out'), zoomLabel: $('zoom-label'),
    pager: $('pager'), pageInput: $('page-input'), pageCount: $('page-count'),
    btnOpenLocal: $('btn-open-local'), btnSearch: $('btn-search'), btnDownload: $('btn-download'),
    fileInput: $('file-input'), searchbar: $('searchbar'), searchInput: $('search-input'),
    searchCount: $('search-count'), searchPrev: $('search-prev'), searchNext: $('search-next'),
    searchClose: $('search-close')
  };

  var doc = null;   // documento aberto: {pdf, key, title, remoteId, localFile, pages:[], sizes:[]}
  var zoom = 1;     // fator sobre o "ajustar à largura"
  var fitScale = 1;
  var scale = 1;    // escala efetiva do PDF.js
  var currentPage = 1;
  var nearPages = new Set();
  var observer = null;
  var pumping = false;
  var retryAction = null;
  var search = { query: '', matches: [], index: -1, token: 0, texts: {} };

  // ---------------------------------------------------------------- utils
  var storage = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* sem storage */ } }
  };

  function debounce(fn, ms) {
    var t = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

  function clamp(v, min, max) { return Math.min(max, Math.max(min, v)); }

  function normalize(s) {
    return (s || '').toLocaleLowerCase('pt-BR').normalize('NFD').replace(/[̀-ͯ]/g, '');
  }

  function formatBytes(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
    return (n / 1048576).toFixed(1).replace('.', ',') + ' MB';
  }

  function haptic(type) {
    try { if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(type || 'light'); } catch (e) { /* ignora */ }
  }

  function versionAtLeast(v) {
    try { return !!(tg && tg.isVersionAtLeast && tg.isVersionAtLeast(v)); } catch (e) { return false; }
  }

  // ---------------------------------------------------------------- tema
  var THEME_MAP = {
    bg_color: '--bg', secondary_bg_color: '--bg2', text_color: '--text', hint_color: '--hint',
    link_color: '--link', button_color: '--accent', button_text_color: '--accent-text',
    header_bg_color: '--header-bg', section_separator_color: '--sep'
  };

  function applyTheme() {
    var root = document.documentElement;
    var params = (tg && tg.themeParams) || {};
    var scheme = tg && tg.colorScheme ? tg.colorScheme
      : (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    root.setAttribute('data-scheme', scheme);
    Object.keys(THEME_MAP).forEach(function (key) {
      if (params[key]) root.style.setProperty(THEME_MAP[key], params[key]);
      else root.style.removeProperty(THEME_MAP[key]);
    });
    if (!params.header_bg_color && params.bg_color) root.style.setProperty('--header-bg', params.bg_color);
    try {
      if (tg && versionAtLeast('6.1')) {
        tg.setHeaderColor(params.header_bg_color || params.bg_color || (scheme === 'dark' ? '#1c1f24' : '#ffffff'));
        tg.setBackgroundColor(params.secondary_bg_color || (scheme === 'dark' ? '#111316' : '#eef0f3'));
      }
    } catch (e) { /* versões antigas */ }
  }

  // ---------------------------------------------------------------- estados
  function showState(opts) {
    el.state.hidden = false;
    el.stateSpinner.hidden = !opts.loading;
    el.stateIcon.hidden = !opts.icon;
    el.stateIcon.textContent = opts.icon || '';
    el.stateTitle.textContent = opts.title || '';
    el.stateText.textContent = opts.text || '';
    el.stateProgress.hidden = opts.progress == null;
    if (opts.progress != null) el.stateProgressBar.style.width = Math.round(opts.progress * 100) + '%';
    retryAction = opts.retry || null;
    el.stateRetry.hidden = !opts.retry;
    el.stateLocal.hidden = !opts.local;
  }

  function hideState() { el.state.hidden = true; }

  function setProgress(fraction, text) {
    el.stateProgress.hidden = false;
    el.stateProgressBar.style.width = Math.round(fraction * 100) + '%';
    if (text) el.stateText.textContent = text;
  }

  function AppError(kind, status) { this.kind = kind; this.status = status; }

  var ERRORS = {
    network: { icon: '📡', title: 'Falha de rede', text: 'Não foi possível conectar ao servidor. Verifique sua internet e tente novamente.', retry: true },
    auth: { icon: '🔑', title: 'Sessão inválida', text: 'Não foi possível validar sua sessão do Telegram. Feche o leitor e abra de novo pelo botão do canal.' },
    forbidden: { icon: '🔒', title: 'Sem permissão', text: 'Sua conta não tem permissão para ver este PDF.' },
    notfound: { icon: '🔍', title: 'PDF não encontrado', text: 'Este PDF não existe ou foi removido.' },
    toobig: { icon: '📦', title: 'Arquivo grande demais', text: 'O Telegram não permite que bots baixem arquivos acima de 20 MB.' },
    upstream: { icon: '⚠️', title: 'Erro ao buscar o PDF', text: 'O servidor não conseguiu baixar o arquivo do Telegram. Tente novamente em instantes.', retry: true },
    server: { icon: '⚠️', title: 'Erro no servidor', text: 'Algo deu errado. Tente novamente em instantes.', retry: true },
    invalid: { icon: '📄', title: 'Arquivo inválido', text: 'Não foi possível abrir este arquivo como PDF.' },
    password: { icon: '🔐', title: 'PDF protegido por senha', text: 'PDFs com senha não são suportados pelo leitor. Use o botão de download.' },
    nolib: { icon: '⚠️', title: 'Falha ao carregar o leitor', text: 'Não foi possível carregar o PDF.js. Verifique sua conexão.', retry: true }
  };

  function showError(err, retry) {
    var kind = err && err.kind ? err.kind : 'server';
    if (err && err.name === 'PasswordException') kind = 'password';
    else if (err && (err.name === 'InvalidPDFException' || err.name === 'FormatError')) kind = 'invalid';
    if (!(err instanceof AppError)) console.error(err);
    var info = ERRORS[kind] || ERRORS.server;
    showState({ icon: info.icon, title: info.title, text: info.text, retry: info.retry ? retry : null, local: true });
  }

  function statusKind(status) {
    if (status === 401) return 'auth';
    if (status === 403) return 'forbidden';
    if (status === 404) return 'notfound';
    if (status === 413) return 'toobig';
    if (status === 502) return 'upstream';
    return 'server';
  }

  // ---------------------------------------------------------------- API
  function api(path, options) {
    options = options || {};
    var headers = options.headers || {};
    headers['X-Telegram-Init-Data'] = tg ? tg.initData : '';
    options.headers = headers;
    return fetch(path, options).catch(function () { throw new AppError('network'); }).then(function (res) {
      if (!res.ok) throw new AppError(statusKind(res.status), res.status);
      return res;
    });
  }

  function readWithProgress(res, total, onProgress) {
    if (!res.body || !res.body.getReader || !total) {
      return res.arrayBuffer().then(function (b) { return new Uint8Array(b); });
    }
    var reader = res.body.getReader();
    var chunks = [];
    var received = 0;
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) {
          var out = new Uint8Array(received), pos = 0;
          chunks.forEach(function (c) { out.set(c, pos); pos += c.length; });
          return out;
        }
        chunks.push(r.value);
        received += r.value.length;
        onProgress(Math.min(1, received / total));
        return pump();
      });
    }
    return pump().catch(function (e) { throw e instanceof AppError ? e : new AppError('network'); });
  }

  // ---------------------------------------------------------------- abertura
  function getStartParam() {
    var p = tg && tg.initDataUnsafe ? tg.initDataUnsafe.start_param : null;
    if (!p) {
      try {
        var q = new URLSearchParams(window.location.search);
        p = q.get('tgWebAppStartParam') || q.get('startapp');
      } catch (e) { p = null; }
    }
    return p && /^[A-Za-z0-9_-]{1,64}$/.test(p) ? p : null;
  }

  function loadRemote(id) {
    var retry = function () { loadRemote(id); };
    showState({ loading: true, title: 'Carregando PDF…' });
    var meta = null;
    api('/api/pdf/' + encodeURIComponent(id) + '/meta')
      .then(function (r) { return r.json(); })
      .then(function (m) {
        meta = m;
        setTitle(m.file_name);
        showState({ loading: true, title: m.file_name, text: 'Baixando… ' + formatBytes(m.file_size), progress: 0 });
        return api('/api/pdf/' + encodeURIComponent(id) + '/file');
      })
      .then(function (res) {
        var total = Number(res.headers.get('Content-Length')) || meta.file_size || 0;
        return readWithProgress(res, total, function (f) {
          setProgress(f, 'Baixando… ' + Math.round(f * 100) + '% de ' + formatBytes(total));
        });
      })
      .then(function (data) {
        return openDocument(data, { key: 'pdf:' + id, title: meta.file_name, remoteId: id });
      })
      .catch(function (err) { showError(err, retry); });
  }

  function loadLocal(file) {
    if (!file) return;
    showState({ loading: true, title: file.name, text: 'Lendo arquivo…' });
    var reader = new FileReader();
    reader.onload = function () {
      openDocument(new Uint8Array(reader.result), {
        key: 'local:' + file.name + ':' + file.size, title: file.name, localFile: file
      }).catch(function (err) { showError(err); });
    };
    reader.onerror = function () { showError(new AppError('invalid')); };
    reader.readAsArrayBuffer(file);
  }

  function setTitle(t) {
    el.title.textContent = t || 'Leitor de PDF';
    document.title = t ? t + ' — Leitor de PDF' : 'Leitor de PDF';
  }

  function closeDocument() {
    if (observer) { observer.disconnect(); observer = null; }
    nearPages.clear();
    if (doc) {
      doc.pages.forEach(function (p) { if (p.task) try { p.task.cancel(); } catch (e) { /* ignora */ } });
      try { doc.pdf.destroy(); } catch (e) { /* ignora */ }
    }
    doc = null;
    el.pages.innerHTML = '';
    resetSearch();
  }

  function openDocument(data, info) {
    if (!pdfjsLib) return Promise.reject(new AppError('nolib'));
    closeDocument();
    showState({ loading: true, title: info.title, text: 'Preparando páginas…' });
    return pdfjsLib.getDocument({ data: data, isEvalSupported: false }).promise.then(function (pdf) {
      var proms = [];
      for (var i = 1; i <= pdf.numPages; i++) proms.push(pdf.getPage(i));
      return Promise.all(proms).then(function (pageObjs) {
        var sizes = pageObjs.map(function (pg) {
          var vp = pg.getViewport({ scale: 1 });
          return { w: vp.width, h: vp.height };
        });
        doc = {
          pdf: pdf, key: info.key, title: info.title, remoteId: info.remoteId || null,
          localFile: info.localFile || null, sizes: sizes, pages: []
        };
        setTitle(info.title);
        buildPages();
        hideState();
        el.bottombar.hidden = false;
        el.btnSearch.disabled = false;
        el.btnDownload.disabled = false;
        el.pageCount.textContent = pdf.numPages;

        var savedZoom = parseFloat(storage.get('lpt:zoom:' + doc.key));
        zoom = savedZoom >= MIN_ZOOM && savedZoom <= MAX_ZOOM ? savedZoom : 1;
        computeFitScale();
        layout();
        var saved = parseInt(storage.get('lpt:page:' + doc.key), 10);
        var startPage = saved >= 1 && saved <= pdf.numPages ? saved : 1;
        scrollToPage(startPage);
        setCurrentPage(startPage);
        startObserver();
      });
    });
  }

  // ---------------------------------------------------------------- layout
  function buildPages() {
    var frag = document.createDocumentFragment();
    doc.pages = doc.sizes.map(function (_, idx) {
      var div = document.createElement('div');
      div.className = 'page';
      div.setAttribute('data-page', String(idx + 1));
      frag.appendChild(div);
      return { num: idx + 1, el: div, canvas: null, textLayer: null, renderedScale: 0, failedScale: 0, task: null, token: 0 };
    });
    el.pages.appendChild(frag);
  }

  function computeFitScale() {
    if (!doc) return;
    var maxW = 0;
    doc.sizes.forEach(function (s) { if (s.w > maxW) maxW = s.w; });
    var available = Math.max(100, el.viewer.clientWidth - 16);
    fitScale = available / maxW;
    scale = fitScale * zoom;
  }

  function layout() {
    doc.pages.forEach(function (p, i) {
      var s = doc.sizes[i];
      p.el.style.width = Math.floor(s.w * scale) + 'px';
      p.el.style.height = Math.floor(s.h * scale) + 'px';
      p.el.style.setProperty('--scale-factor', String(scale));
    });
    el.zoomLabel.textContent = Math.round(zoom * 100) + '%';
  }

  function invalidateRenders() {
    doc.pages.forEach(function (p) {
      p.token++;
      if (p.task) { try { p.task.cancel(); } catch (e) { /* ignora */ } p.task = null; }
      p.renderedScale = 0;
      p.failedScale = 0;
      if (p.textLayer) { p.textLayer.remove(); p.textLayer = null; }
      // o canvas antigo fica esticado até a nova renderização (evita "piscar")
    });
  }

  // Mantém o ponto (vx, vy) da área visível no mesmo lugar do conteúdo.
  function setZoom(newZoom, anchor) {
    if (!doc) return;
    newZoom = clamp(newZoom, MIN_ZOOM, MAX_ZOOM);
    var oldScale = scale;
    var v = el.viewer;
    var vx = anchor ? anchor.vx : v.clientWidth / 2;
    var vy = anchor ? anchor.vy : v.clientHeight / 2;
    var cx = anchor && anchor.cx != null ? anchor.cx : v.scrollLeft + vx;
    var cy = anchor && anchor.cy != null ? anchor.cy : v.scrollTop + vy;
    zoom = newZoom;
    scale = fitScale * zoom;
    var r = scale / oldScale;
    layout();
    invalidateRenders();
    v.scrollLeft = cx * r - vx;
    v.scrollTop = cy * r - vy;
    storage.set('lpt:zoom:' + doc.key, String(zoom));
    updateCurrentPage();
    pump();
  }

  function scrollToPage(n) {
    if (!doc) return;
    n = clamp(n, 1, doc.pages.length);
    el.viewer.scrollTop = doc.pages[n - 1].el.offsetTop - 8;
  }

  function updateCurrentPage() {
    if (!doc || !doc.pages.length) return;
    var y = el.viewer.scrollTop + el.viewer.clientHeight * 0.35;
    var lo = 0, hi = doc.pages.length - 1;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (doc.pages[mid].el.offsetTop <= y) lo = mid; else hi = mid - 1;
    }
    setCurrentPage(lo + 1);
  }

  var savePage = debounce(function () {
    if (doc) storage.set('lpt:page:' + doc.key, String(currentPage));
  }, 400);

  function setCurrentPage(n) {
    if (n === currentPage && el.pageInput.value === String(n)) return;
    currentPage = n;
    if (document.activeElement !== el.pageInput) el.pageInput.value = String(n);
    savePage();
  }

  // ---------------------------------------------------------------- renderização
  function startObserver() {
    if (!('IntersectionObserver' in window)) {
      // Navegadores muito antigos: renderiza tudo sob demanda pela rolagem.
      doc.pages.forEach(function (p) { nearPages.add(p.num); });
      pump();
      return;
    }
    observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var n = Number(entry.target.getAttribute('data-page'));
        if (entry.isIntersecting) nearPages.add(n); else nearPages.delete(n);
      });
      pump();
    }, { root: el.viewer, rootMargin: '120% 0px 120% 0px' });
    doc.pages.forEach(function (p) { observer.observe(p.el); });
  }

  function nextPageToRender() {
    var best = null, bestDist = Infinity;
    nearPages.forEach(function (n) {
      var p = doc.pages[n - 1];
      if (!p || p.renderedScale === scale || p.failedScale === scale) return;
      var d = Math.abs(n - currentPage);
      if (d < bestDist) { best = p; bestDist = d; }
    });
    return best;
  }

  function pump() {
    if (pumping || !doc) return;
    pumping = true;
    var current = doc;
    (function loop() {
      if (doc !== current) { pumping = false; return; }
      var p = nextPageToRender();
      if (!p) { pumping = false; releaseFarPages(); return; }
      renderPage(p).then(loop, function (err) {
        console.warn('Falha ao renderizar página', p.num, err);
        p.failedScale = scale;
        loop();
      });
    })();
  }

  function outputScaleFor(vp) {
    var ratio = window.devicePixelRatio || 1;
    var pixels = vp.width * vp.height * ratio * ratio;
    if (pixels > MAX_CANVAS_PIXELS) ratio = Math.sqrt(MAX_CANVAS_PIXELS / (vp.width * vp.height));
    return Math.max(0.5, ratio);
  }

  function renderPage(p) {
    var token = ++p.token;
    var renderScale = scale;
    var current = doc;
    return current.pdf.getPage(p.num).then(function (page) {
      if (token !== p.token) return null;
      var vp = page.getViewport({ scale: renderScale });
      var out = outputScaleFor(vp);
      var canvas = document.createElement('canvas');
      canvas.width = Math.floor(vp.width * out);
      canvas.height = Math.floor(vp.height * out);
      var ctx = canvas.getContext('2d', { alpha: false });
      var task = page.render({
        canvasContext: ctx, viewport: vp, transform: out !== 1 ? [out, 0, 0, out, 0, 0] : null
      });
      p.task = task;
      return task.promise.then(function () {
        p.task = null;
        if (token !== p.token) return null;
        if (p.canvas) p.canvas.remove();
        p.canvas = canvas;
        p.el.insertBefore(canvas, p.el.firstChild);
        p.renderedScale = renderScale;
        return renderTextLayer(p, page, vp, token);
      }, function (err) {
        p.task = null;
        if (err && err.name === 'RenderingCancelledException') return null;
        throw err;
      });
    });
  }

  function getTextContent(num) {
    if (!search.texts[num]) {
      search.texts[num] = doc.pdf.getPage(num).then(function (page) { return page.getTextContent(); });
    }
    return search.texts[num];
  }

  function renderTextLayer(p, page, vp, token) {
    if (!pdfjsLib.renderTextLayer) return null;
    return getTextContent(p.num).then(function (content) {
      if (token !== p.token) return null;
      var layer = document.createElement('div');
      layer.className = 'textLayer';
      var task = pdfjsLib.renderTextLayer({ textContentSource: content, container: layer, viewport: vp, textDivs: [] });
      return task.promise.then(function () {
        if (token !== p.token) return;
        if (p.textLayer) p.textLayer.remove();
        p.textLayer = layer;
        p.el.appendChild(layer);
        applyHighlights(p);
      });
    }).catch(function (e) { console.warn('Camada de texto falhou', e); });
  }

  function releaseFarPages() {
    if (!doc) return;
    var rendered = doc.pages.filter(function (p) { return p.canvas; });
    if (rendered.length <= MAX_RENDERED) return;
    rendered.sort(function (a, b) { return Math.abs(b.num - currentPage) - Math.abs(a.num - currentPage); });
    rendered.slice(0, rendered.length - MAX_RENDERED).forEach(function (p) {
      if (nearPages.has(p.num)) return;
      p.token++;
      p.canvas.width = 0; p.canvas.height = 0; // libera memória no iOS
      p.canvas.remove(); p.canvas = null;
      if (p.textLayer) { p.textLayer.remove(); p.textLayer = null; }
      p.renderedScale = 0;
    });
  }

  // ---------------------------------------------------------------- busca
  function resetSearch() {
    search.token++;
    search.query = '';
    search.matches = [];
    search.index = -1;
    search.texts = {};
    el.searchCount.textContent = '';
  }

  function openSearch() {
    if (!doc) return;
    el.searchbar.hidden = false;
    el.searchInput.focus();
    el.searchInput.select();
  }

  function closeSearch() {
    el.searchbar.hidden = true;
    search.token++;
    search.query = '';
    search.matches = [];
    search.index = -1;
    el.searchCount.textContent = '';
    if (doc) doc.pages.forEach(applyHighlights);
    el.searchInput.blur();
  }

  function countOccurrences(hay, needle) {
    var n = 0, i = hay.indexOf(needle);
    while (i !== -1) { n++; i = hay.indexOf(needle, i + needle.length); }
    return n;
  }

  function runSearch(raw) {
    var q = normalize(raw.trim());
    if (!doc || !q) { closeSearchResults(); return; }
    if (q === search.query && search.matches.length) { gotoMatch(search.index + 1); return; }
    var token = ++search.token;
    search.query = q;
    search.matches = [];
    search.index = -1;
    var total = doc.pages.length;
    var i = 1;
    el.searchCount.textContent = '…';
    (function next() {
      if (token !== search.token) return;
      if (i > total) { finish(); return; }
      var num = i++;
      getTextContent(num).then(function (content) {
        if (token !== search.token) return;
        var text = normalize(content.items.map(function (it) { return it.str + (it.hasEOL ? ' ' : ''); }).join(''));
        var count = countOccurrences(text, q);
        for (var k = 0; k < count; k++) search.matches.push({ page: num, occ: k });
        if (num % 10 === 0) el.searchCount.textContent = Math.round((num / total) * 100) + '%';
        next();
      }, next);
    })();

    function finish() {
      doc.pages.forEach(applyHighlights);
      if (!search.matches.length) { el.searchCount.textContent = '0'; haptic('rigid'); return; }
      var start = 0;
      for (var m = 0; m < search.matches.length; m++) {
        if (search.matches[m].page >= currentPage) { start = m; break; }
      }
      gotoMatch(start);
    }
  }

  function closeSearchResults() {
    search.token++;
    search.query = '';
    search.matches = [];
    search.index = -1;
    el.searchCount.textContent = '';
    if (doc) doc.pages.forEach(applyHighlights);
  }

  function gotoMatch(idx) {
    var n = search.matches.length;
    if (!n) return;
    search.index = ((idx % n) + n) % n;
    var match = search.matches[search.index];
    el.searchCount.textContent = (search.index + 1) + '/' + n;
    var prevPage = currentPage;
    scrollToPage(match.page);
    updateCurrentPage();
    doc.pages.forEach(applyHighlights);
    var p = doc.pages[match.page - 1];
    var focus = function () {
      applyHighlights(p);
      var cur = p.textLayer && p.textLayer.querySelector('.hl.current');
      if (cur) cur.scrollIntoView({ block: 'center', inline: 'center' });
    };
    if (p.textLayer && p.renderedScale === scale) focus();
    else {
      nearPages.add(match.page);
      p.token++;
      p.renderedScale = 0;
      renderPage(p).then(focus, function () { /* ignora */ });
    }
    if (prevPage !== match.page) haptic('light');
  }

  function applyHighlights(p) {
    if (!p.textLayer) return;
    var spans = p.textLayer.querySelectorAll('span');
    var q = search.query;
    var current = search.matches[search.index];
    var hits = [];
    for (var i = 0; i < spans.length; i++) {
      var span = spans[i];
      var hit = !!q && normalize(span.textContent).indexOf(q) !== -1;
      span.classList.toggle('hl', hit);
      span.classList.remove('current');
      if (hit) hits.push(span);
    }
    if (current && current.page === p.num && hits.length) {
      hits[Math.min(current.occ, hits.length - 1)].classList.add('current');
    }
  }

  // ---------------------------------------------------------------- download
  function downloadCurrent() {
    if (!doc) return;
    haptic('light');
    if (doc.localFile) { downloadBlob(doc.localFile, doc.title); return; }
    if (!doc.remoteId) return;
    var fileName = doc.title || 'documento.pdf';
    api('/api/pdf/' + encodeURIComponent(doc.remoteId) + '/download-link', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (tg && versionAtLeast('8.0') && tg.downloadFile) {
          tg.downloadFile({ url: data.url, file_name: data.file_name || fileName });
        } else if (tg && tg.openLink) {
          tg.openLink(data.url);
        } else {
          window.location.href = data.url;
        }
      })
      .catch(function (err) {
        var info = ERRORS[err && err.kind] || ERRORS.server;
        notify(info.title + '. ' + info.text);
      });
  }

  function downloadBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name || 'documento.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
  }

  function notify(text) {
    try { if (tg && versionAtLeast('6.2')) { tg.showAlert(text); return; } } catch (e) { /* cai no banner */ }
    el.banner.textContent = text;
    el.banner.hidden = false;
    setTimeout(function () { el.banner.hidden = true; }, 5000);
  }

  // ---------------------------------------------------------------- gestos
  function setupPinch() {
    var v = el.viewer;
    var pinch = null;

    function dist(t) { return Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY); }

    v.addEventListener('touchstart', function (e) {
      if (!doc || e.touches.length !== 2) return;
      var rect = v.getBoundingClientRect();
      var vx = (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left;
      var vy = (e.touches[0].clientY + e.touches[1].clientY) / 2 - rect.top;
      pinch = {
        start: dist(e.touches), ratio: 1, vx: vx, vy: vy,
        cx: v.scrollLeft + vx, cy: v.scrollTop + vy
      };
      el.pages.style.transformOrigin = pinch.cx + 'px ' + pinch.cy + 'px';
    }, { passive: true });

    v.addEventListener('touchmove', function (e) {
      if (!pinch || e.touches.length !== 2) return;
      e.preventDefault();
      var ratio = dist(e.touches) / pinch.start;
      pinch.ratio = clamp(zoom * ratio, MIN_ZOOM, MAX_ZOOM) / zoom;
      el.pages.style.transform = 'scale(' + pinch.ratio + ')';
    }, { passive: false });

    function end() {
      if (!pinch) return;
      var p = pinch;
      pinch = null;
      el.pages.style.transform = '';
      if (Math.abs(p.ratio - 1) > 0.01) setZoom(zoom * p.ratio, p);
    }
    v.addEventListener('touchend', function (e) { if (e.touches.length < 2) end(); });
    v.addEventListener('touchcancel', end);

    // Ctrl + roda do mouse / pinça do touchpad (Telegram Desktop)
    var wheel = null;
    var commitWheel = debounce(function () {
      if (!wheel) return;
      var w = wheel;
      wheel = null;
      el.pages.style.transform = '';
      setZoom(zoom * w.ratio, w);
    }, 160);
    v.addEventListener('wheel', function (e) {
      if (!doc || !e.ctrlKey) return;
      e.preventDefault();
      if (!wheel) {
        var rect = v.getBoundingClientRect();
        var vx = e.clientX - rect.left, vy = e.clientY - rect.top;
        wheel = { ratio: 1, vx: vx, vy: vy, cx: v.scrollLeft + vx, cy: v.scrollTop + vy };
        el.pages.style.transformOrigin = wheel.cx + 'px ' + wheel.cy + 'px';
      }
      wheel.ratio = clamp(zoom * wheel.ratio * Math.exp(-e.deltaY * 0.0025), MIN_ZOOM, MAX_ZOOM) / zoom;
      el.pages.style.transform = 'scale(' + wheel.ratio + ')';
      commitWheel();
    }, { passive: false });
  }

  // ---------------------------------------------------------------- eventos
  function bindEvents() {
    var onScroll = (function () {
      var queued = false;
      return function () {
        if (queued) return;
        queued = true;
        window.requestAnimationFrame(function () { queued = false; updateCurrentPage(); });
      };
    })();
    el.viewer.addEventListener('scroll', onScroll, { passive: true });

    el.zoomIn.addEventListener('click', function () { setZoom(zoom * 1.25); haptic('light'); });
    el.zoomOut.addEventListener('click', function () { setZoom(zoom / 1.25); haptic('light'); });
    el.zoomLabel.addEventListener('click', function () { setZoom(1); haptic('light'); });

    el.pager.addEventListener('submit', function (e) {
      e.preventDefault();
      var n = parseInt(el.pageInput.value, 10);
      if (doc && n >= 1) {
        n = clamp(n, 1, doc.pages.length);
        scrollToPage(n);
        setCurrentPage(n);
      }
      el.pageInput.value = String(currentPage);
      el.pageInput.blur();
    });
    el.pageInput.addEventListener('focus', function () { el.pageInput.select(); });
    el.pageInput.addEventListener('blur', function () { el.pageInput.value = String(currentPage); });

    var openPicker = function () { el.fileInput.value = ''; el.fileInput.click(); };
    el.btnOpenLocal.addEventListener('click', openPicker);
    el.stateLocal.addEventListener('click', openPicker);
    el.fileInput.addEventListener('change', function () { loadLocal(el.fileInput.files && el.fileInput.files[0]); });
    el.stateRetry.addEventListener('click', function () { if (retryAction) retryAction(); });

    el.btnDownload.addEventListener('click', downloadCurrent);
    el.btnSearch.addEventListener('click', function () {
      if (el.searchbar.hidden) openSearch(); else closeSearch();
    });
    el.searchClose.addEventListener('click', closeSearch);
    el.searchNext.addEventListener('click', function () {
      if (normalize(el.searchInput.value.trim()) !== search.query) runSearch(el.searchInput.value);
      else gotoMatch(search.index + 1);
    });
    el.searchPrev.addEventListener('click', function () { gotoMatch(search.index - 1); });
    el.searchInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (e.shiftKey) gotoMatch(search.index - 1); else runSearch(el.searchInput.value);
      } else if (e.key === 'Escape') {
        closeSearch();
      }
    });
    el.searchInput.addEventListener('input', debounce(function () {
      if (!el.searchInput.value.trim()) closeSearchResults();
    }, 200));

    document.addEventListener('keydown', function (e) {
      if (!doc) return;
      var mod = e.ctrlKey || e.metaKey;
      if (mod && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); openSearch(); }
      else if (mod && (e.key === '=' || e.key === '+')) { e.preventDefault(); setZoom(zoom * 1.25); }
      else if (mod && e.key === '-') { e.preventDefault(); setZoom(zoom / 1.25); }
      else if (mod && e.key === '0') { e.preventDefault(); setZoom(1); }
    });

    var onResize = debounce(function () {
      if (!doc) return;
      var oldScale = scale;
      computeFitScale();
      if (Math.abs(scale - oldScale) / oldScale > 0.01) {
        var page = currentPage;
        layout();
        invalidateRenders();
        scrollToPage(page);
        pump();
      }
    }, 200);
    window.addEventListener('resize', onResize);
    if (tg && tg.onEvent) {
      tg.onEvent('themeChanged', applyTheme);
      tg.onEvent('viewportChanged', function (ev) { if (!ev || ev.isStateStable) onResize(); });
    }
    if (!tg && window.matchMedia) {
      try { window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme); } catch (e) { /* antigo */ }
    }
  }

  // ---------------------------------------------------------------- início
  function init() {
    applyTheme();
    bindEvents();
    setupPinch();

    if (tg) {
      try { tg.ready(); } catch (e) { /* ignora */ }
      try { tg.expand(); } catch (e) { /* ignora */ }
      // Evita que a rolagem do PDF feche o Mini App no celular.
      try { if (versionAtLeast('7.7') && tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (e) { /* ignora */ }
    }

    if (!pdfjsLib) {
      showError(new AppError('nolib'), function () { window.location.reload(); });
      return;
    }
    pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;

    if (!inTelegram) {
      el.banner.textContent = 'Você abriu o leitor fora do Telegram. Para ver os PDFs do canal, use o botão “📖 Abrir no leitor” no Telegram. Aqui você ainda pode abrir um PDF do seu aparelho.';
      el.banner.hidden = false;
      showState({ icon: '📖', title: 'Leitor de PDF', text: 'Abra um PDF do seu aparelho para ler aqui.', local: true });
      return;
    }

    var id = getStartParam();
    if (!id) {
      showState({ icon: '📖', title: 'Nenhum PDF selecionado', text: 'Toque em “📖 Abrir no leitor” embaixo de um PDF do canal, ou abra um PDF do seu aparelho.', local: true });
      return;
    }
    loadRemote(id);
  }

  init();
})();
