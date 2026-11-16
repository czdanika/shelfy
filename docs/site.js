// Shelfy bemutatóoldal: megjelenés görgetéskor, másolás gombok, tartalomjegyzék, beállítófájl-készítő.
(function () {
  var T = window.SHELFY || {};

  // ---- megjelenés görgetéskor
  var rv = document.querySelectorAll('.rv');
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, {rootMargin: '0px 0px -8% 0px'});
    rv.forEach(function (el) { io.observe(el); });
  } else rv.forEach(function (el) { el.classList.add('in'); });

  // ---- másolás gomb minden kódblokkra
  function addCopy(pre) {
    var box = document.createElement('div'); box.className = 'codebox';
    pre.parentNode.insertBefore(box, pre); box.appendChild(pre);
    var b = document.createElement('button'); b.type = 'button'; b.className = 'copy'; b.textContent = T.copy || 'Copy';
    b.addEventListener('click', function () {
      var text = pre.textContent;
      function done() { b.textContent = T.copied || 'Copied'; b.classList.add('ok'); setTimeout(function () { b.textContent = T.copy || 'Copy'; b.classList.remove('ok'); }, 1600); }
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback); else fallback();
      function fallback() {
        var ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); done(); } catch (e) {} ta.remove();
      }
    });
    box.appendChild(b);
  }
  document.querySelectorAll('pre').forEach(addCopy);

  // ---- tartalomjegyzék: az éppen olvasott rész kiemelése
  var links = [].slice.call(document.querySelectorAll('.toc a'));
  if (links.length && 'IntersectionObserver' in window) {
    var map = {};
    links.forEach(function (a) { map[a.getAttribute('href').slice(1)] = a; });
    var spy = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        links.forEach(function (a) { a.classList.remove('on'); });
        var a = map[e.target.id]; if (a) a.classList.add('on');
      });
    }, {rootMargin: '-20% 0px -70% 0px'});
    Object.keys(map).forEach(function (id) { var el = document.getElementById(id); if (el) spy.observe(el); });
  }

  // ---- beállítófájl-készítő (telepítési útmutató)
  var gen = document.getElementById('gen');
  if (!gen) return;
  var $ = function (id) { return document.getElementById(id); };
  var P = T.platforms, cur = 'terramaster', touched = {};
  var f = {data: $('g-data'), port: $('g-port'), ingest: $('g-ingest'), lib: $('g-lib'), lang: $('g-lang'), tz: $('g-tz'), puid: $('g-puid'), pgid: $('g-pgid')};
  try { f.tz.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Budapest'; } catch (e) { f.tz.value = 'Europe/Budapest'; }
  f.lang.value = T.lang || 'en';
  ['data', 'lib'].forEach(function (k) { f[k].addEventListener('input', function () { touched[k] = true; }); });

  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
  function pad(s, n) { while (s.length < n) s += ' '; return s; }
  function line(code, note) {   // a megjegyzés oszlopba igazítva; a hossz a látható szövegből számít, nem a jelölésből
    if (!note) return code;
    var len = code.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').length;
    return code + pad('', Math.max(2, 50 - len)) + '<span class="c"># ' + esc(note) + '</span>';
  }

  function render() {
    var data = f.data.value.trim() || P[cur].data, port = String(parseInt(f.port.value, 10) || 18200);
    var ingest = f.ingest.checked, lib = f.lib.value.trim(), puid = f.puid.value.trim(), pgid = f.pgid.value.trim();
    var c = T.c, y = [];
    y.push('<span class="k">services</span>:', '  <span class="k">shelfy</span>:',
           '    <span class="k">image</span>: ghcr.io/czdanika/shelfy:latest', '    <span class="k">container_name</span>: shelfy',
           '    <span class="k">restart</span>: unless-stopped', '    <span class="k">ports</span>:',
           line('      - <span class="s">"' + esc(port) + ':8080"</span>', c.port),
           '    <span class="k">volumes</span>:', line('      - ' + esc(data) + ':/data', c.data));
    if (ingest) y.push(line('      - ' + esc(data) + '-ingest:/ingest', c.ingest));
    if (lib) y.push(line('      - ' + esc(lib) + ':/data/library', c.lib));
    y.push('    <span class="k">environment</span>:', line('      - TZ=' + esc(f.tz.value.trim() || 'Europe/Budapest'), c.tz),
           line('      - DEFAULT_LANGUAGE=' + esc(f.lang.value), c.lang));
    if (ingest) y.push(line('      - INGEST_DIR=/ingest', c.ingestenv));
    if (puid) y.push(line('      - PUID=' + esc(puid), c.puid));
    if (pgid) y.push('      - PGID=' + esc(pgid));
    $('g-compose').innerHTML = y.join('\n');

    var r = ['docker run -d --name shelfy --restart unless-stopped \\', '  -p ' + port + ':8080 \\', '  -v ' + data + ':/data \\'];
    if (ingest) r.push('  -v ' + data + '-ingest:/ingest -e INGEST_DIR=/ingest \\');
    if (lib) r.push('  -v ' + lib + ':/data/library \\');
    r.push('  -e TZ=' + (f.tz.value.trim() || 'Europe/Budapest') + ' -e DEFAULT_LANGUAGE=' + f.lang.value + ' \\');
    if (puid || pgid) r.push('  ' + (puid ? '-e PUID=' + puid + ' ' : '') + (pgid ? '-e PGID=' + pgid + ' ' : '') + '\\');
    r.push('  ghcr.io/czdanika/shelfy:latest');
    $('g-run').textContent = r.join('\n');

    document.querySelectorAll('[data-port]').forEach(function (el) { el.textContent = port; });
    document.querySelectorAll('[data-folder]').forEach(function (el) { el.textContent = data; });
    document.querySelectorAll('[data-folder-ingest]').forEach(function (el) { el.textContent = data + '-ingest'; el.closest('[data-if-ingest]') && (el.closest('[data-if-ingest]').hidden = !ingest); });
    document.querySelectorAll('[data-if-ingest]').forEach(function (el) { el.hidden = !ingest; });
  }

  function pick(k) {
    cur = k;
    document.querySelectorAll('.tab[data-plat]').forEach(function (b) { b.classList.toggle('on', b.dataset.plat === k); b.setAttribute('aria-selected', String(b.dataset.plat === k)); });
    document.querySelectorAll('.plat').forEach(function (p) { p.classList.toggle('on', p.dataset.plat === k); });
    if (!touched.data) f.data.value = P[k].data;
    f.lib.placeholder = P[k].lib;
    render();
  }
  document.querySelectorAll('.tab[data-plat]').forEach(function (b) { b.addEventListener('click', function () { pick(b.dataset.plat); }); });
  document.querySelectorAll('.tab[data-out]').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.tab[data-out]').forEach(function (x) { x.classList.toggle('on', x === b); });
      document.querySelectorAll('[data-outbox]').forEach(function (x) { x.hidden = x.dataset.outbox !== b.dataset.out; });
    });
  });
  gen.addEventListener('input', render);
  pick(cur);
})();
