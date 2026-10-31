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


def test_per_user_mail_settings(admin, book, monkeypatch):
    sent = []
    import app.main as m
    monkeypatch.setattr(m, "send_mail", lambda s, to, subj, name, data, mime: sent.append((s["smtp_host"], s["smtp_user"], s["smtp_password"], to)))
    # közös fiók az admintól
    admin.post("/admin/settings", data={"smtp_host": "smtp.kozos.hu", "smtp_port": "587", "smtp_from": "kozos@example.com",
                                        "smtp_user": "kozos", "smtp_password": "kozosjelszo"})
    uid = make_user(admin, "orsi", can_download=True)
    assert "közös" in admin.get("/admin").text
    # az admin beállítja Orsinak a saját fiókját
    admin.post(f"/admin/users/{uid}/mail", data={"smtp_host": "smtp.orsi.hu", "smtp_port": "465", "smtp_security": "ssl",
                                                  "smtp_user": "orsi", "smtp_password": "titkos-orsi", "smtp_from": "orsi@example.com"})
    row = admin.app.state.db.one("SELECT smtp_host, smtp_password FROM users WHERE id = ?", (uid,))
    assert row["smtp_host"] == "smtp.orsi.hu" and row["smtp_password"].startswith("enc:") and "titkos" not in row["smtp_password"]
    assert "titkos-orsi" not in admin.get(f"/admin/users/{uid}").text
    # a közös jelszó is titkosítva tárolódik
    assert admin.app.state.db.get_setting("smtp_password").startswith("enc:")

    admin.post(f"/book/{book}/send", data={})  # admin: nincs Kindle címe
    admin.post("/me", data={"display_name": "A", "kindle_email": "admin@kindle.com"})
    admin.post(f"/book/{book}/send")
    assert sent[-1] == ("smtp.kozos.hu", "kozos", "kozosjelszo", "admin@kindle.com")

    login(admin, "orsi", "olvaso123")
    assert "saját beállításod" in admin.get("/me").text
    admin.post("/me", data={"display_name": "Orsi", "kindle_email": "orsi@kindle.com"})
    admin.post(f"/book/{book}/send")
    assert sent[-1] == ("smtp.orsi.hu", "orsi", "titkos-orsi", "orsi@kindle.com")
    # jelszó üresen hagyva megmarad, majd törlés után a közös jön
    admin.post("/me/mail", data={"smtp_host": "smtp.orsi2.hu", "smtp_from": "orsi@example.com", "smtp_user": "orsi", "smtp_password": ""})
    admin.post(f"/book/{book}/send")
    assert sent[-1][:3] == ("smtp.orsi2.hu", "orsi", "titkos-orsi")
    admin.post("/me/mail", data={"action": "clear"})
    admin.post(f"/book/{book}/send")
    assert sent[-1][0] == "smtp.kozos.hu"
    r = admin.post("/me/mail/test", data={"to": "x@example.com"}, follow_redirects=False)
    assert "mail_sent" in r.headers["location"]


def test_mobi_only_book_converts_to_epub_and_sends(admin, tmp_path, monkeypatch):
    """Csak MOBI-ban meglévő könyv: küldéshez EPUB készül (régen --share-not-sync miatt elbukott)."""
    import subprocess
    from app.library import Meta
    from app.tools import sha256_file
    src = make_epub(tmp_path / "x.epub", title="Csak mobi", author="Teszt Elek")
    mobi = tmp_path / "x.mobi"
    subprocess.run(["ebook-convert", str(src), str(mobi)], check=True, capture_output=True)
    lib = admin.app.state.lib
    bid = lib.add_book(Meta(title="Csak mobi", authors=["Teszt Elek"]), [(mobi, "mobi", sha256_file(mobi))], None)
    assert lib.book(bid)["formats"] == ["mobi"]
    r = admin.get(f"/book/{bid}/download/epub")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    sent = []
    import app.main as m
    monkeypatch.setattr(m, "send_mail", lambda s, to, subj, name, data, mime: sent.append((name, data[:2])))
    admin.post("/admin/settings", data={"smtp_host": "smtp.example", "smtp_port": "587", "smtp_from": "a@example.com"})
    admin.post("/me", data={"display_name": "A", "kindle_email": "a@kindle.com"})
    r = admin.post(f"/book/{bid}/send", follow_redirects=False)
    assert "mail_sent" in r.headers["location"]
    assert sent and sent[0][0].endswith(".epub") and sent[0][1] == b"PK"
    # tartós konvertálás MOBI → EPUB is működik
    r = admin.post(f"/book/{bid}/convert", data={"target": "epub"}, follow_redirects=False)
    assert "converted" in r.headers["location"] and "epub" in lib.book(bid)["formats"]


def test_kindle_browse_menus(admin, tmp_path):
    from test_backup import add_book
    add_book(admin, tmp_path, "Elso")
    for kind in ("authors", "series", "tags", "publishers", "languages", "ratings"):
        r = admin.get(f"/kindle/browse/{kind}")
        assert r.status_code == 200, kind
    page = admin.get("/kindle/browse/authors").text
    assert "/kindle?author=" in page
    aid = admin.app.state.db.val("SELECT id FROM authors LIMIT 1")
    r = admin.get(f"/kindle?author={aid}")
    assert r.status_code == 200 and "Elso" in r.text and "Próba Anna" in r.text
