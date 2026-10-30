"""Közös tesztkellékek: ideiglenes adatmappa, minta EPUB, hálózat nélküli (hamis) metaadat-források."""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.config import Config

# Kézzel írt, szintetikus Moly-szerű oldalak (nem valódi tartalom).
MOLY_SEARCH = """<html><body><h2>Könyvek</h2>
<a href="/konyvek/proba-anna-a-kek-varos"><img alt="Próba Anna: A kék város" src="x.jpg"></a>
<a href="/konyvek/mas-szerzo-masik-konyv"><img alt="Más Szerző: Másik könyv" src="y.jpg"></a>
</body></html>"""
MOLY_BOOK = """<html><head><meta content="https://img.example/kek.jpg" property="og:image">
<meta content="Rövid leírás." name="description"></head><body>
<h1 class="book"><span class="item">A kék város</span></h1>
<div class="authors"><a href="/alkotok/proba-anna">Próba Anna</a></div>
<a class="action" href="/sorozatok/varosok">Városok</a> 2.
<div class="text shrinkable"><p>Egy nyomozó a kék városban egy rejtélyes bűnügy után kutat.</p></div>
<div id="book_tags"><a class="tag genre" href="/c/krimi">krimi</a><a class="tag" href="/c/magyar">magyar szerző</a></div>
<a href="/kiadasok-boritok-szerint">2019</a>
<span class="like_count">90%</span>
</body></html>"""


def cover_bytes(w=600, h=900, color=(30, 90, 120)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def fake_handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "moly.hu" and path == "/kereses":
        return httpx.Response(200, text=MOLY_SEARCH)
    if host == "moly.hu" and path == "/konyvek/proba-anna-a-kek-varos":
        return httpx.Response(200, text=MOLY_BOOK)
    if host == "moly.hu":
        return httpx.Response(404)
    if host == "img.example":
        return httpx.Response(200, content=cover_bytes(color=(200, 40, 40)), headers={"content-type": "image/jpeg"})
    return httpx.Response(404)


def make_epub(path: Path, title="A kék város", author="Próba Anna", lang="hu", with_cover=True) -> Path:
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
  <dc:title>{title}</dc:title><dc:creator opf:role="aut">{author}</dc:creator>
  <dc:language>{lang}</dc:language><dc:identifier id="id">urn:uuid:{abs(hash(title))}</dc:identifier>
  {'<meta name="cover" content="cover"/>' if with_cover else ''}
 </metadata>
 <manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
  <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  {'<item id="cover" href="cover.jpg" media-type="image/jpeg"/>' if with_cover else ''}</manifest>
 <spine toc="ncx"><itemref idref="c1"/></spine>
</package>"""
    ncx = """<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/>
<docTitle><text>t</text></docTitle><navMap><navPoint id="n1" playOrder="1"><navLabel><text>1</text></navLabel>
<content src="c1.xhtml"/></navPoint></navMap></ncx>"""
    body = f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head><body><h1>{title}</h1><p>Szöveg.</p></body></html>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("content.opf", opf)
        z.writestr("toc.ncx", ncx)
        z.writestr("c1.xhtml", body)
        if with_cover:
            z.writestr("cover.jpg", cover_bytes())
    return path


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_dir=tmp_path / "data", library_dir=tmp_path / "library", ingest_dir=None,
                  secret_key="test-secret-key-0123456789", default_language="hu", providers=("moly",), online_lookup=True)


@pytest.fixture
def client(cfg):
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app(cfg, transport=httpx.MockTransport(fake_handler))
    with TestClient(app, base_url="http://shelfy.test") as c:
        yield c


@pytest.fixture
def admin(client):
    r = client.post("/setup", data={"username": "admin", "password": "jelszo123", "password2": "jelszo123"})
    assert r.status_code in (200, 303)
    return client


def wait_status(client, uid, statuses=("review", "done", "error"), timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st = {i["id"]: i["status"] for i in client.get("/upload/status.json").json()["items"]}
        db = client.app.state.db
        s = db.val("SELECT status FROM uploads WHERE id = ?", (uid,))
        if s in statuses:
            return s
        time.sleep(0.3)
    raise AssertionError(f"timeout, status={s}")
