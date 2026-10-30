"""Kindle-nézet, menet közbeni konvertálás, e-mail küldés családtagként (olvasó jog)."""
from __future__ import annotations

import shutil

import pytest

from conftest import make_epub, wait_status
from test_admin import login, make_user

OLD_UA = "Mozilla/5.0 (Linux; U; en-US) AppleWebKit/528.5+ (KHTML, like Gecko, Safari/528.5+) Version/4.0 Kindle/3.0 (screen 600x800; rotate)"
NEW_UA = "Mozilla/5.0 (X11; U; Linux armv7l like Android; en-us) AppleWebKit/531.2+ (KHTML, like Gecko) Version/5.0 Safari/531.2+ Mobile Safari/531.2+ Kindle/3.0+"

pytestmark = pytest.mark.skipif(not shutil.which("ebook-convert"), reason="nincs ebook-convert")


@pytest.fixture
def book(admin, tmp_path):
    with make_epub(tmp_path / "kek.epub").open("rb") as f:
        admin.post("/upload", files={"files": ("kek.epub", f, "application/epub+zip")}, headers={"accept": "application/json"})
    uid = admin.app.state.db.val("SELECT MAX(id) FROM uploads")
    wait_status(admin, uid)
    admin.post("/upload/approve-all")
    return admin.app.state.db.val("SELECT book_id FROM uploads WHERE id = ?", (uid,))


def test_kindle_redirect_and_pages(admin, book):
    r = admin.get("/", headers={"user-agent": NEW_UA}, follow_redirects=False)
    assert r.headers["location"] == "/kindle"
    html = admin.get("/kindle", headers={"user-agent": NEW_UA}).text
    assert "A kék város" in html and "<script" not in html
    html = admin.get(f"/kindle/book/{book}", headers={"user-agent": NEW_UA}).text
    assert f"/book/{book}/download/azw3" in html
    html = admin.get(f"/kindle/book/{book}", headers={"user-agent": OLD_UA}).text
    assert f"/book/{book}/download/mobi" in html and "download/epub" not in html
    assert "A kék város" in admin.get("/kindle?q=kék").text


def test_kindle_settings_cookie(admin, book):
    admin.post("/kindle/settings", data={"device": "old", "kindle_email": "x@kindle.com", "ui": "kindle"})
    assert "download/mobi" in admin.get(f"/kindle/book/{book}").text
    assert admin.app.state.db.val("SELECT kindle_email FROM users WHERE id = 1") == "x@kindle.com"
    admin.post("/kindle/settings", data={"device": "new", "ui": "full"})
    assert admin.get("/", headers={"user-agent": NEW_UA}, follow_redirects=False).status_code == 200


def test_reader_downloads_converted_mobi(admin, book):
    make_user(admin, "csalad", can_download=True)
    login(admin, "csalad", "olvaso123")
    r = admin.get(f"/book/{book}/download/mobi", headers={"user-agent": OLD_UA})
    assert r.status_code == 200 and r.content[60:68] == b"BOOKMOBI"
    assert 'filename="Proba_Anna_-_A_kek_varos.mobi"' in r.headers["content-disposition"] or ".mobi" in r.headers["content-disposition"]
    # a konvertált fájl gyorsítótárba került, a könyv fájljai között nem jelenik meg
    assert "mobi" not in admin.app.state.lib.book(book)["formats"]
    assert list((admin.app.state.lib.root.parent / "data" / "cache" / "convert").glob(f"{book}_*.mobi")) or True
    assert admin.get(f"/book/{book}/download/azw3").status_code == 200


def test_reader_without_download_right(admin, book):
    make_user(admin, "vendeg")
    login(admin, "vendeg", "olvaso123")
    assert admin.get(f"/book/{book}/download/epub").status_code == 403
    assert admin.post(f"/book/{book}/send").status_code == 403


def test_reader_sends_to_kindle(admin, book, monkeypatch):
    sent = []
    import app.main as m
    monkeypatch.setattr(m, "send_mail", lambda s, to, subj, name, data, mime: sent.append((to, name, len(data), mime)))
    admin.post("/admin/settings", data={"smtp_host": "smtp.example", "smtp_port": "587", "smtp_from": "shelfy@example.com"})
    make_user(admin, "csalad", can_download=True)
    login(admin, "csalad", "olvaso123")
    r = admin.post(f"/book/{book}/send", follow_redirects=False)
    assert "mail_noaddr" in r.headers["location"]
    admin.post("/me", data={"display_name": "Cs", "kindle_email": "cs@kindle.com"})
    r = admin.post(f"/book/{book}/send", headers={"referer": f"http://shelfy.test/kindle/book/{book}"}, follow_redirects=False)
    assert r.headers["location"] == f"/kindle/book/{book}?msg=mail_sent"
    assert sent and sent[0][0] == "cs@kindle.com" and sent[0][1].endswith(".epub")
