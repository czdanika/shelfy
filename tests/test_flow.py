"""Végigmegy a fő folyamaton: első indítás → feltöltés → vizsgálat → jóváhagyás → könyv oldal."""
from __future__ import annotations

from conftest import make_epub, wait_status


def test_setup_redirect(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert "Első indítás" in client.get("/setup").text


def test_login_required_and_wrong_password(admin):
    admin.cookies.clear()
    r = admin.get("/books", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    r = admin.post("/login", data={"username": "admin", "password": "rossz"})
    assert r.status_code == 401 and "Hibás" in r.text
    r = admin.post("/login", data={"username": "admin", "password": "jelszo123", "next": "/books"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/books"


def test_csrf_blocked(admin):
    r = admin.post("/me", data={"display_name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_upload_inspect_approve(admin, tmp_path):
    epub = make_epub(tmp_path / "kek.epub")
    with epub.open("rb") as f:
        r = admin.post("/upload", files={"files": ("Próba Anna - A kék város.epub", f, "application/epub+zip")},
                       headers={"accept": "application/json"})
    assert r.json() == {"ok": True, "count": 1}
    uid = admin.app.state.db.val("SELECT MAX(id) FROM uploads")
    assert wait_status(admin, uid) == "review"

    page = admin.get(f"/upload/{uid}").text
    assert "Vizsgálati jegyzőkönyv" in page
    assert "a fájl tartalma megfelel" in page          # formátum ellenőrzés
    assert "Moly.hu" in page and "A kék város" in page  # online találat
    assert 'value="crime" checked' in page              # kategória-javaslat a műfajból

    r = admin.post(f"/upload/{uid}/approve", data={
        "title": "A kék város", "authors": "Próba Anna", "series": "Városok", "series_index": "2",
        "year": "2019", "language": "hun", "tags": "magyar szerző", "description": "Leírás",
        "categories": ["crime"], "cover_choice": "online", "cover_url": "https://img.example/kek.jpg",
        "source": "moly", "source_url": "https://moly.hu/konyvek/proba-anna-a-kek-varos"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/book/")
    bid = int(r.headers["location"].split("/")[2].split("?")[0])
    b = admin.app.state.lib.book(bid)
    assert b["title"] == "A kék város" and b["series"] == "Városok"
    assert {t["name"] for t in b["tags"]} == {"Krimi", "magyar szerző"}
    assert b["formats"] == ["epub"] and b["has_cover"]
    # a borító az online (piros) kép lett
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(admin.get(f"/book/{bid}/cover").content)).convert("RGB")
    assert img.getpixel((10, 10))[0] > 150

    html = admin.get(f"/book/{bid}").text
    assert "A kék város" in html and "Városok" in html
    assert admin.get(f"/book/{bid}/download/epub").status_code == 200

    # ugyanaz a fájl még egyszer → duplikátum jelzés
    with epub.open("rb") as f:
        admin.post("/upload", files={"files": ("kek.epub", f, "application/epub+zip")}, headers={"accept": "application/json"})
    uid2 = admin.app.state.db.val("SELECT MAX(id) FROM uploads")
    wait_status(admin, uid2)
    page = admin.get(f"/upload/{uid2}").text
    assert "Lehet, hogy már megvan" in page and "Pontosan ez a fájl" in page


def test_bad_file_reports_error(admin, tmp_path):
    p = tmp_path / "rossz.epub"
    p.write_bytes(b"ez nem zip")
    with p.open("rb") as f:
        admin.post("/upload", files={"files": ("rossz.epub", f, "application/epub+zip")}, headers={"accept": "application/json"})
    uid = admin.app.state.db.val("SELECT MAX(id) FROM uploads")
    wait_status(admin, uid)
    page = admin.get(f"/upload/{uid}").text
    assert "A fájl sérült" in page


def test_unsupported_format(admin, tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"MZ....")
    with p.open("rb") as f:
        admin.post("/upload", files={"files": ("x.exe", f, "application/octet-stream")}, headers={"accept": "application/json"})
    uid = admin.app.state.db.val("SELECT MAX(id) FROM uploads")
    assert wait_status(admin, uid) == "error"
    assert "nem kezeli" in admin.get(f"/upload/{uid}").text


def test_two_uploads_review_one_after_another(admin, tmp_path):
    ids = []
    for name, title in (("a.epub", "A kék város"), ("b.epub", "Másik könyv")):
        with make_epub(tmp_path / name, title=title, author="Próba Anna").open("rb") as f:
            admin.post("/upload", files={"files": (name, f, "application/epub+zip")}, headers={"accept": "application/json"})
        ids.append(admin.app.state.db.val("SELECT MAX(id) FROM uploads"))
    for uid in ids:
        wait_status(admin, uid)
    first, second = ids
    page = admin.get(f"/upload/{first}").text
    assert "Átnézésre vár (2)" in page and f'href="/upload/{second}"' in page
    assert "Felvétel és a következő (1 vár)" in page
    # jóváhagyás után a második átnézése jön, nem tűnik el
    r = admin.post(f"/upload/{first}/approve", data={"title": "A kék város", "authors": "Próba Anna", "next": "next"},
                   follow_redirects=False)
    assert r.headers["location"] == f"/upload/{second}?msg=approved"
    page = admin.get(r.headers["location"]).text
    assert "Felvéve a könyvtárba" in page and "Felvétel a könyvtárba" in page
    # az utolsó után a könyv oldala
    r = admin.post(f"/upload/{second}/approve", data={"title": "Másik könyv", "authors": "Próba Anna"}, follow_redirects=False)
    assert r.headers["location"].startswith("/book/")


def test_book_page_points_to_waiting_uploads(admin, tmp_path):
    for name in ("a.epub", "b.epub"):
        with make_epub(tmp_path / name, title=name).open("rb") as f:
            admin.post("/upload", files={"files": (name, f, "application/epub+zip")}, headers={"accept": "application/json"})
    last = admin.app.state.db.val("SELECT MAX(id) FROM uploads")
    wait_status(admin, last); wait_status(admin, last - 1)
    r = admin.post(f"/upload/{last}/approve", data={"title": "x", "next": "book"}, follow_redirects=False)
    html = admin.get(r.headers["location"]).text
    assert "1 feltöltés vár még átnézésre" in html
