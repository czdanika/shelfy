"""Calibre könyvtár átvétele: valódi, calibredb-vel készített könyvtárból."""
from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from conftest import make_epub

pytestmark = pytest.mark.skipif(not shutil.which("calibredb"), reason="nincs Calibre")


@pytest.fixture
def calibre_lib(tmp_path):
    root = tmp_path / "Calibre Library"
    root.mkdir()
    for i, (t, a) in enumerate((("Első könyv", "Szerző Egy"), ("Második könyv", "Stephen King"))):
        e = make_epub(tmp_path / f"b{i}.epub", title=t, author=a)
        subprocess.run(["calibredb", "add", "--library-path", str(root), str(e)], check=True, capture_output=True)
    subprocess.run(["calibredb", "set_metadata", "--library-path", str(root), "1",
                    "--field", "series:Próbasorozat", "--field", "series_index:3", "--field", "tags:krimi,kedvenc",
                    "--field", "comments:<p>Saját gyűjtött leírás.</p>", "--field", "rating:4",
                    "--field", "publisher:Saját Kiadó", "--field", "pubdate:2015-05-01"], check=True, capture_output=True)
    return root


def test_import_calibre(admin, calibre_lib):
    r = admin.post("/admin/import", data={"path": str(calibre_lib)}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(100):
        if "Kész." in admin.get("/admin/import").text:
            break
        time.sleep(0.2)
    lib = admin.app.state.lib
    books, total = lib.list_books(sort="title_az")
    assert total == 2
    b = lib.book([x for x in books if x["title"] == "Első könyv"][0]["id"])
    assert b["series"] == "Próbasorozat" and b["series_index"] == 3
    assert {t["name"] for t in b["tags"]} == {"krimi", "kedvenc"}
    assert "Saját gyűjtött leírás" in b["description"]
    assert b["rating"] == 4 and b["publisher"] == "Saját Kiadó" and b["year"] == 2015
    assert b["formats"] == ["epub"] and b["has_cover"]
    # másodszorra semmit nem vesz fel újra
    admin.post("/admin/import", data={"path": str(calibre_lib)})
    for _ in range(100):
        if "Kész." in admin.get("/admin/import").text and not admin.app.state.import_task.done() is False:
            break
        time.sleep(0.2)
    time.sleep(0.5)
    assert lib.list_books()[1] == 2
    assert "2 kihagyva" in admin.get("/admin/import").text


def test_import_bad_path(admin, tmp_path):
    r = admin.post("/admin/import", data={"path": str(tmp_path)}, follow_redirects=False)
    assert "import_nodb" in r.headers["location"]
