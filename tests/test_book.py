"""Könyv műveletek: szerkesztés, konvertálás, küldés, polc, olvasási állapot, törlés; tömeges jóváhagyás."""
from __future__ import annotations

import shutil

import pytest

from conftest import make_epub, wait_status


def upload(c, path, name=None):
    with path.open("rb") as f:
        c.post("/upload", files={"files": (name or path.name, f, "application/epub+zip")}, headers={"accept": "application/json"})
    uid = c.app.state.db.val("SELECT MAX(id) FROM uploads")
    wait_status(c, uid)
    return uid


@pytest.fixture
def book(admin, tmp_path):
    uid = upload(admin, make_epub(tmp_path / "kek.epub"))
    r = admin.post("/upload/approve-all", follow_redirects=False)
    assert r.status_code == 303
    return admin.app.state.db.val("SELECT book_id FROM uploads WHERE id = ?", (uid,))


def test_approve_all_uses_good_match(admin, book):
    b = admin.app.state.lib.book(book)
    assert b["title"] == "A kék város" and b["series"] == "Városok"
    assert "Krimi" in {t["name"] for t in b["tags"]}


def test_edit_and_browse(admin, book):
    r = admin.post(f"/book/{book}/edit", data={"title": "Az új cím", "authors": "Próba Anna, Második Szerző",
                                               "tags": "krimi, kedvenc", "publisher": "Kiadó Kft.", "year": "2020",
                                               "rating": "4", "language": "hun"}, follow_redirects=False)
    assert r.status_code == 303
    b = admin.app.state.lib.book(book)
    assert b["title"] == "Az új cím" and [a["name"] for a in b["authors"]] == ["Próba Anna", "Második Szerző"]
    assert "Kiadó Kft." in admin.get("/browse/publishers").text
    assert "Második Szerző" in admin.get("/browse/authors").text
    assert "Az új cím" in admin.get("/books?q=új").text


def test_read_state_and_shelf(admin, book):
    admin.post(f"/book/{book}/read", data={"status": "reading"})
    assert "Az kék" not in admin.get("/books?view=reading").text
    assert "A kék város" in admin.get("/books?view=reading").text
    admin.post(f"/book/{book}/shelf", data={"new_shelf": "Nyaralás"})
    sid = admin.app.state.db.val("SELECT id FROM shelves WHERE name = 'Nyaralás'")
    assert sid and "A kék város" in admin.get(f"/books?shelf={sid}").text


def test_send_without_smtp(admin, book):
    admin.post("/me", data={"display_name": "A", "kindle_email": "a@kindle.com"})
    r = admin.post(f"/book/{book}/send", follow_redirects=False)
    assert "mail_off" in r.headers["location"]


@pytest.mark.skipif(not shutil.which("ebook-convert"), reason="nincs ebook-convert")
def test_convert_to_mobi(admin, book):
    r = admin.post(f"/book/{book}/convert", data={"target": "mobi"}, follow_redirects=False)
    assert "converted" in r.headers["location"]
    assert "mobi" in admin.app.state.lib.book(book)["formats"]
    data = admin.get(f"/book/{book}/download/mobi").content
    assert data[60:68] == b"BOOKMOBI" and b"EBOK" not in data[:20000]  # személyes dokumentum, nem bolti könyv


def test_add_format_to_duplicate(admin, book, tmp_path):
    src = make_epub(tmp_path / "kek2.epub", with_cover=False)
    pdf = tmp_path / "kek.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%\xe2\xe3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF")
    with pdf.open("rb") as f:
        admin.post("/upload", files={"files": ("Próba Anna - A kék város.pdf", f, "application/pdf")}, headers={"accept": "application/json"})
    uid = admin.app.state.db.val("SELECT MAX(id) FROM uploads")
    wait_status(admin, uid)
    up = admin.app.state.db.one("SELECT * FROM uploads WHERE id = ?", (uid,))
    if up["duplicate_of"]:
        admin.post(f"/upload/{uid}/approve", data={"action": "add_format"})
        assert "pdf" in admin.app.state.lib.book(book)["formats"]


def test_research_and_reject(admin, tmp_path):
    uid = upload(admin, make_epub(tmp_path / "x.epub", title="Ismeretlen mű", author="Senki"))
    r = admin.post(f"/upload/{uid}/research", data={"title": "A kék város", "author": "Próba Anna", "only": "moly"}, follow_redirects=False)
    assert r.status_code == 303
    assert "A kék város" in admin.get(f"/upload/{uid}").text
    admin.post(f"/upload/{uid}/reject")
    assert admin.app.state.db.val("SELECT status FROM uploads WHERE id = ?", (uid,)) == "rejected"


def test_delete(admin, book):
    d = admin.app.state.lib.book_dir(book)
    r = admin.post(f"/book/{book}/delete", follow_redirects=False)
    assert r.status_code == 303 and admin.app.state.lib.book(book) is None and not d.exists()


def test_online_lookup_for_existing_book(admin, book):
    # a könyv adatainak egy részét kitöröljük, majd online frissítjük
    m = admin.app.state.lib.meta(book)
    m.series, m.series_index, m.year, m.description = None, None, None, "régi"
    admin.app.state.lib.update_meta(book, m)
    html = admin.get(f"/book/{book}/lookup?go=1").text
    assert "Összehasonlítás" in html and "Moly.hu" in html
    assert 'name="take" value="series" aria-label="Sorozat" checked' in html
    assert 'value="description"' in html and "/lookup/cover?url=" in html
    r = admin.post(f"/book/{book}/lookup", data={"c": "0", "title": "A kék város", "author": "Próba Anna", "isbn": "", "only": "",
                                                  "take": ["series", "series_index", "year", "description", "cover"],
                                                  "categories": ["crime"]}, follow_redirects=False)
    assert "lookup_applied" in r.headers["location"]
    b = admin.app.state.lib.book(book)
    assert b["series"] == "Városok" and b["series_index"] == 2 and b["year"] == 2019
    assert "rejtélyes" in b["description"] and "Krimi" in {t["name"] for t in b["tags"]}


def test_lookup_cover_proxy_requires_signature(admin, book):
    assert admin.get(f"/book/{book}/lookup/cover?url=https://img.example/kek.jpg&sig=bad").status_code == 404


def test_reader_epub_and_position(admin, tmp_path):
    from test_backup import add_book
    bid = add_book(admin, tmp_path, "Olvasnivaló")
    page = admin.get(f"/book/{bid}").text
    assert f"/book/{bid}/reader" in page
    r = admin.get(f"/book/{bid}/reader")
    assert r.status_code == 200 and "epub.min.js" in r.text
    r = admin.get(f"/book/{bid}/reader/book.epub")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert admin.post(f"/book/{bid}/reader/pos", json={"cfi": "epubcfi(/6/4!/4/2/1:0)", "percent": 0.42}).json()["ok"]
    assert "42%" in admin.get(f"/book/{bid}").text
    db = admin.app.state.db
    assert db.val("SELECT status FROM read_state WHERE book_id = ?", (bid,)) == "reading"
    admin.post(f"/book/{bid}/reader/pos", json={"cfi": "x", "percent": 1})
    assert db.val("SELECT status FROM read_state WHERE book_id = ?", (bid,)) == "read"


def test_reader_notes_api(admin, tmp_path):
    from test_backup import add_book
    bid = add_book(admin, tmp_path, "Jegyzetes")
    n = admin.post(f"/book/{bid}/reader/notes", json={"kind": "highlight", "cfi": "epubcfi(/6/4!/4/2,/1:0,/1:5)", "text": "Szöveg",
                                                      "color": "green", "percent": 0.1}).json()
    assert n["color"] == "green" and n["kind"] == "highlight"
    b = admin.post(f"/book/{bid}/reader/notes", json={"kind": "bookmark", "cfi": "epubcfi(/6/4!/4/2/1:0)", "percent": 0.2}).json()
    u = admin.post(f"/book/{bid}/reader/notes/{n['id']}", json={"note": "fontos", "color": "pink"}).json()
    assert u["note"] == "fontos" and u["color"] == "pink"
    assert len(admin.get(f"/book/{bid}/reader/notes").json()) == 2
    admin.post(f"/book/{bid}/reader/notes/{b['id']}", json={"delete": 1})
    assert [x["kind"] for x in admin.get(f"/book/{bid}/reader/notes").json()] == ["highlight"]


def test_cover_url_changes_after_new_cover(admin, book):
    import re as _re
    from conftest import cover_bytes
    url = lambda: _re.search(r'/book/\d+/cover\?v=(\d+)&w=520', admin.get(f"/book/{book}").text)
    before = url()
    import time as _t; _t.sleep(1.1)
    admin.app.state.lib.set_cover(book, cover_bytes(color=(10, 200, 10)))
    after = url()
    assert after and (not before or before.group(1) != after.group(1))
