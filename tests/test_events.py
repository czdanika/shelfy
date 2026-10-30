"""Eseménynapló: feltöltés, felvétel, letöltés, küldés, belépés nyoma és megjelenítése."""
from __future__ import annotations

from conftest import make_epub, wait_status
from test_admin import login, make_user


def kinds(c):
    return [r["kind"] for r in c.app.state.db.q("SELECT kind FROM events ORDER BY id")]


def test_activity_is_logged_and_shown(admin, tmp_path, monkeypatch):
    with make_epub(tmp_path / "kek.epub").open("rb") as f:
        admin.post("/upload", files={"files": ("kek.epub", f, "application/epub+zip")}, headers={"accept": "application/json"})
    uid = admin.app.state.db.val("SELECT MAX(id) FROM uploads")
    wait_status(admin, uid)
    admin.post(f"/upload/{uid}/approve", data={"title": "A kék város", "authors": "Próba Anna", "next": "book"})
    bid = admin.app.state.db.val("SELECT book_id FROM uploads WHERE id = ?", (uid,))
    admin.get(f"/book/{bid}/download/epub")
    admin.post(f"/book/{bid}/send")  # nincs SMTP → sikertelen küldés naplózva
    k = kinds(admin)
    assert k[:2] == ["user_created", "upload"] and "inspected" in k and "added" in k
    assert "downloaded" in k and "send_failed" in k

    html = admin.get("/admin/log").text
    assert "Felvéve a könyvtárba: A kék város (EPUB)" in html
    assert "Letöltve: A kék város (EPUB)" in html
    assert "Küldés sikertelen: A kék város" in html and "nincs beállítva" in html
    assert 'class="bad"' in html
    # szűrők
    assert "Letöltve" not in admin.get("/admin/log?group=sending").text
    assert "Felvéve" not in admin.get("/admin/log?errors=1").text
    assert "Letöltve" in admin.get("/admin/log?q=kék").text
    # könyv előzményei és saját tevékenység
    assert "Előzmények" in admin.get(f"/book/{bid}").text
    assert "Tevékenységeim" in admin.get("/me").text


def test_failed_login_logged_and_log_admin_only(admin):
    make_user(admin, "orsi", can_download=True)
    admin.cookies.clear()
    admin.post("/login", data={"username": "orsi", "password": "rossz"})
    login(admin, "orsi", "olvaso123")
    assert admin.get("/admin/log").status_code == 403
    login(admin, "admin", "jelszo123")
    html = admin.get("/admin/log?group=users").text
    assert "Sikertelen belépés: orsi" in html and "Új felhasználó: orsi" in html
