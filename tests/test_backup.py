"""Mentés és visszaállítás: adatbázis és teljes mentés, biztonsági mentés, SMTP-jelszó újratitkosítás, ellenőrzés."""
from __future__ import annotations

import io
import json
import zipfile

from app import backup as bk
from app.library import Meta
from app.tools import sha256_file
from conftest import make_epub


def add_book(c, tmp_path, title):
    p = make_epub(tmp_path / f"{title}.epub", title=title)
    return c.app.state.lib.add_book(Meta(title=title, authors=["Próba Anna"]), [(p, "epub", sha256_file(p))], None)


def test_backup_download_db_and_full(admin, tmp_path):
    add_book(admin, tmp_path, "Első")
    r = admin.get("/admin/backup/download?full=0")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert {"manifest.json", "shelfy.db", "secret.key"} <= set(names) and not any(n.startswith("library/") for n in names)
    m = json.loads(z.read("manifest.json"))
    assert m["app"] == "shelfy" and m["books"] == 1 and m["full"] is False
    r = admin.get("/admin/backup/download?full=1")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert any(n.startswith("library/") and n.endswith(".epub") for n in z.namelist())
    assert "Mentés" in admin.get("/admin/backup").text


def test_restore_full_backup_into_empty_instance(admin, tmp_path, cfg):
    bid = add_book(admin, tmp_path, "Megmaradó")
    admin.post("/admin/settings", data={"smtp_host": "smtp.example", "smtp_password": "titok123", "smtp_from": "a@example.com"})
    full = admin.get("/admin/backup/download?full=1").content
    # egy másik "szerver": új adat- és könyvtár mappa, más titkos kulccsal
    from fastapi.testclient import TestClient
    import httpx
    from app.config import Config
    from app.main import create_app
    from conftest import fake_handler
    cfg2 = Config(data_dir=tmp_path / "d2", library_dir=tmp_path / "l2", ingest_dir=None, secret_key="masik-kulcs-0123456789",
                  providers=("moly",), default_language="hu")
    with TestClient(create_app(cfg2, transport=httpx.MockTransport(fake_handler)), base_url="http://folio.test") as c2:
        c2.post("/setup", data={"username": "uj", "password": "jelszo123", "password2": "jelszo123"})
        r = c2.post("/admin/backup/restore", files={"file": ("m.zip", full, "application/zip")},
                    data={"confirm": "1", "library": "1"}, follow_redirects=False)
        assert "restored=1" in r.headers["location"] and "missing=0" in r.headers["location"]
        b = c2.app.state.lib.book(bid)
        assert b["title"] == "Megmaradó" and (cfg2.library_dir / b["path"]).is_dir()
        # az SMTP-jelszó az új kulccsal is visszafejthető
        from app.auth import Sessions
        assert Sessions(cfg2.secret_key).unseal(c2.app.state.db.get_setting("smtp_password")) == "titok123"
        # biztonsági mentés készült a visszaállítás előtt
        assert any(p.name.startswith("before-restore-") for p in (cfg2.data_dir / "backups").iterdir())
        # a mentésben "admin" van, "uj" nincs → ki kell lépni, az admin be tud lépni
        assert c2.post("/login", data={"username": "admin", "password": "jelszo123"}, follow_redirects=False).status_code == 303


def test_db_only_restore_reports_missing_files(admin, tmp_path, cfg):
    add_book(admin, tmp_path, "Hiányzó")
    dbonly = admin.get("/admin/backup/download?full=0").content
    import shutil
    shutil.rmtree(cfg.library_dir)
    cfg.library_dir.mkdir()
    r = admin.post("/admin/backup/restore", files={"file": ("m.zip", dbonly, "application/zip")}, data={"confirm": "1"},
                   follow_redirects=False)
    assert "missing=1" in r.headers["location"]
    html = admin.get("/admin/backup?check=1").text
    assert "Hiányzó" in html


def test_restore_rejects_bad_files(admin):
    r = admin.post("/admin/backup/restore", files={"file": ("x.zip", b"nem zip", "application/zip")}, data={"confirm": "1"},
                   follow_redirects=False)
    assert "restore_notzip" in r.headers["location"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("valami.txt", "x")
    r = admin.post("/admin/backup/restore", files={"file": ("x.zip", buf.getvalue(), "application/zip")}, data={"confirm": "1"},
                   follow_redirects=False)
    assert "restore_notshelfy" in r.headers["location"]
    r = admin.post("/admin/backup/restore", files={"file": ("x.zip", buf.getvalue(), "application/zip")}, follow_redirects=False)
    assert "restore_confirm" in r.headers["location"]


def test_auto_backup_and_keep(admin, cfg):
    db, lib = admin.app.state.db, admin.app.state.lib
    bk.auto_backup(db, cfg.data_dir, cfg.library_dir, cfg.secret_key, keep=2)   # (indításkor már készülhetett)
    assert list((cfg.data_dir / "backups").glob("auto-db-*.zip"))
    assert bk.auto_backup(db, cfg.data_dir, cfg.library_dir, cfg.secret_key, keep=2) is None  # naponta egy
    for i in range(3):
        (cfg.data_dir / "backups" / f"auto-db-2020010{i}-0000.zip").write_bytes(b"x")
    bk.auto_backup(db, cfg.data_dir, cfg.library_dir, cfg.secret_key, keep=2)
    assert len(list((cfg.data_dir / "backups").glob("auto-db-*.zip"))) <= 2


def test_about_page(admin):
    html = admin.get("/about").text
    assert "Shelfy" in html and "Czibolya Dániel" in html and "github.com/czdanika/shelfy" in html
