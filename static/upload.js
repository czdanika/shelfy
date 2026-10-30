// Feltöltés: húzd-és-ejtsd, haladásjelzés, és a vizsgálat állapotának frissítése.
(function () {
  var form = document.getElementById('upform');
  var input = document.getElementById('files');
  var progress = document.getElementById('progress');
  if (!form) return;

  function send(files) {
    if (!files || !files.length) return;
    var data = new FormData();
    for (var i = 0; i < files.length; i++) data.append('files', files[i]);
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.upload.onprogress = function (e) {
      if (e.lengthComputable) progress.textContent = Math.round(e.loaded * 100 / e.total) + '%';
    };
    xhr.onload = function () { location.reload(); };
    xhr.onerror = function () { progress.textContent = '!'; };
    xhr.send(data);
  }

  input.addEventListener('change', function () { send(input.files); });
  form.addEventListener('click', function (e) { if (e.target === form) input.click(); });
  ['dragenter', 'dragover'].forEach(function (ev) {
    form.addEventListener(ev, function (e) { e.preventDefault(); form.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    form.addEventListener(ev, function (e) { e.preventDefault(); form.classList.remove('over'); });
  });
  form.addEventListener('drop', function (e) { send(e.dataTransfer.files); });

  // amíg van folyamatban lévő vizsgálat, 3 mp-enként ellenőrzi és frissít
  function busy() {
    return document.querySelector('.qitem[data-status="pending"], .qitem[data-status="inspecting"]');
  }
  if (busy()) {
    var timer = setInterval(function () {
      fetch('/upload/status.json').then(function (r) { return r.json(); }).then(function (d) {
        var changed = d.items.some(function (it) {
          var el = document.querySelector('.qitem[data-id="' + it.id + '"]');
          return !el || el.getAttribute('data-status') !== it.status;
        });
        if (changed) { clearInterval(timer); location.reload(); }
      });
    }, 3000);
  }
})();
