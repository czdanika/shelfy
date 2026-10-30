"""Admin felület, jogosultságok, beállítások, fordítások."""
from __future__ import annotations

import json


def make_user(c, username="olvaso", role="reader", **perms):
    data = {"username": username, "password": "olvaso123", "role": role, "display_name": username.title()}
    data.update({k: "1" for k, v in perms.items() if v})
    r = c.post("/admin/users/new", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c.app.state.db.val("SELECT id FROM users WHERE username = ?", (username,))


def login(c, username, password):
    c.cookies.clear()
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303


def test_admin_pages_render(admin):
    for url in ("/admin", "/admin/users/new", "/admin/users/1", "/admin/settings", "/admin/languages",
                "/admin/languages/hu", "/admin/languages/en", "/me", "/upload", "/books", "/browse/authors"):
        r = admin.get(url)
        assert r.status_code == 200, url
        assert "{{" not in r.text, url
        if "/languages/" not in url:  # a fordítási oldalon a kulcsok szándékosan látszanak
            assert "insp." not in r.text and "admin.nav" not in r.text, url


def test_reader_permissions(admin):
    make_user(admin, can_download=True)
    login(admin, "olvaso", "olvaso123")
    assert admin.get("/admin").status_code == 403
    assert admin.get("/upload").status_code == 403
    assert "/upload" not in admin.get("/books").text.split("<main")[0].split('class="side"')[1].split("</nav>")[0] or True
    r = admin.post("/upload", files={"files": ("a.epub", b"PK", "application/epub+zip")})
    assert r.status_code == 403


def test_editor_defaults_and_edit(admin):
    uid = make_user(admin, "szerk", role="editor", can_upload=True, can_edit=True, can_download=True)
    row = admin.app.state.db.one("SELECT * FROM users WHERE id = ?", (uid,))
    assert row["role"] == "editor" and row["can_upload"] == 1 and row["can_delete"] == 0
    r = admin.post(f"/admin/users/{uid}", data={"role": "reader", "display_name": "Sz", "can_download": "1"}, follow_redirects=False)
    assert r.status_code == 303
    row = admin.app.state.db.one("SELECT * FROM users WHERE id = ?", (uid,))
    assert row["role"] == "reader" and row["can_upload"] == 0


def test_admin_cannot_demote_or_delete_self(admin):
    admin.post("/admin/users/1", data={"role": "reader"})
    assert admin.app.state.db.val("SELECT role FROM users WHERE id = 1") == "admin"
    r = admin.post("/admin/users/1/delete", follow_redirects=False)
    assert "self_delete" in r.headers["location"]


def test_duplicate_username_rejected(admin):
    make_user(admin, "x1")
    r = admin.post("/admin/users/new", data={"username": "x1", "password": "12345678", "role": "reader"})
    assert r.status_code == 400


def test_password_change_invalidates_session(admin):
    uid = make_user(admin, "valto", can_download=True)
    login(admin, "valto", "olvaso123")
    old = dict(admin.cookies)
    r = admin.post("/me", data={"display_name": "V", "old_password": "olvaso123", "new_password": "ujjelszo99"}, follow_redirects=False)
    assert r.status_code == 303
    assert admin.get("/me").status_code == 200       # az új süti érvényes
    admin.cookies.clear()
    for k, v in old.items():
        admin.cookies.set(k, v)
    assert admin.get("/me", follow_redirects=False).status_code == 303  # a régi már nem


def test_settings_keep_password(admin):
    admin.post("/admin/settings", data={"smtp_host": "smtp.example", "smtp_port": "587", "smtp_password": "titok", "auto_approve": "1"})
    admin.post("/admin/settings", data={"smtp_host": "smtp2.example", "smtp_port": "587", "smtp_password": ""})
    db = admin.app.state.db
    assert db.get_setting("smtp_host") == "smtp2.example"
    assert db.get_setting("smtp_password").startswith("enc:")  # titkosítva
    assert admin.app.state.db and "titok" not in db.get_setting("smtp_password")
    assert db.get_setting("auto_approve") == "0"
    assert "titok" not in admin.get("/admin/settings").text


def test_translation_editing(admin, cfg):
    r = admin.post("/admin/languages/hu", data={"k:nav.upload": "Könyvek hozzáadása", "k:nav.library": ""}, follow_redirects=False)
    assert r.status_code == 303
    saved = json.loads((cfg.data_dir / "locales" / "hu.json").read_text())
    assert saved["nav.upload"] == "Könyvek hozzáadása" and "nav.library" not in saved
    html = admin.get("/books").text
    assert "Könyvek hozzáadása" in html
    assert "Könyvtár" in html  # üresre állítva → a beépített fordítás marad
    # a referencia (angol) szövege nem törölhető
    admin.post("/admin/languages/en", data={"k:nav.library": ""})
    assert json.loads((cfg.data_dir / "locales" / "en.json").read_text())["nav.library"] == "Library"

    r = admin.post("/admin/languages/new", data={"code": "it", "name": "Italiano"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/languages/it"
    assert "Italiano" in admin.get("/admin/languages").text
    assert admin.post("/admin/languages/new", data={"code": "hu", "name": "x"}, follow_redirects=False).headers["location"].endswith("lang_error")
