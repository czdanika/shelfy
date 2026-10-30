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


@pytest.mark.skipif(not shutil.which("ebook-convert"), reason="nincs Calibre")
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
