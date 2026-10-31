"""Duplikátumok keresése és egyesítése."""
from __future__ import annotations

import time

from conftest import make_epub

from app.library import Meta
from app.ingest import sha256_file


def mk(c, tmp_path, title, authors, fmt="epub", isbn=None, desc=None, name=None):
    p = make_epub(tmp_path / f"{name or title}.{fmt}", title=title)
    if fmt != "epub":
        p.write_bytes(p.read_bytes() + fmt.encode())  # más tartalom, más hash
    return c.app.state.lib.add_book(Meta(title=title, authors=authors, isbn=isbn, description=desc),
                                    [(p, fmt, sha256_file(p))], None)


def scan(c):
    c.post("/tasks/run/duplicates", data={"back": "dups"})
    for _ in range(50):
        if not c.app.state.db.val("SELECT 1 FROM tasks WHERE kind = 'duplicates' AND status = 'running'"):
            break
        time.sleep(0.1)


def test_formats_of_one_book_are_not_duplicates(admin, tmp_path):
    lib = admin.app.state.lib
    a = mk(admin, tmp_path, "Az arany ember", ["Jókai Mór"], "mobi")
    p = make_epub(tmp_path / "x.azw3", title="Az arany ember"); p.write_bytes(p.read_bytes() + b"z")
    lib.add_file(a, p, "azw3", sha256_file(p))
    assert lib.duplicate_groups() == []


def test_find_and_merge(admin, tmp_path):
    lib, db = admin.app.state.lib, admin.app.state.db
    a = mk(admin, tmp_path, "Az arany ember", ["Jókai Mór"], "mobi", name="a")
    b = mk(admin, tmp_path, "Az Arany Ember (Jókai összes)", ["Mór Jókai"], "azw3", desc="Leírás", name="b")
    c = mk(admin, tmp_path, "Egy másik", ["Valaki"], isbn="963-07-1234-X", name="c")
    d = mk(admin, tmp_path, "Another title", ["Más"], isbn="9789630712347", name="d")
    mk(admin, tmp_path, "Az arany ember", ["Más Szerző"], name="e")  # azonos cím, más szerző: nem duplikátum
    db.run("INSERT INTO read_state(user_id, book_id, status, updated_at) VALUES(1, ?, 'read', 0)", (b,))
    scan(admin)
    page = admin.get("/admin/duplicates").text
    assert "Az Arany Ember (Jókai összes)" in page and "Another title" in page
    groups = {tuple(g["ids"]): g["reasons"] for g in lib.duplicate_groups()}
    assert groups == {(a, b): ["title"], (c, d): ["isbn"]}
    r = admin.post("/admin/duplicates/merge", data={"ids": [str(a), str(b)], "keep": str(a)}, follow_redirects=False)
    assert r.status_code == 303 and "dup_merged" in r.headers["location"]
    k = lib.book(a)
    assert lib.book(b) is None and sorted(k["formats"]) == ["azw3", "mobi"] and k["description"] == "Leírás"
    assert db.val("SELECT status FROM read_state WHERE book_id = ?", (a,)) == "read"
    assert lib.file_path(a, "azw3").exists()
    # nem duplikátum: többé nem jön elő
    admin.post("/admin/duplicates/ignore", data={"ids": [str(c), str(d)]})
    assert lib.duplicate_groups() == []
    assert "Another title" not in admin.get("/admin/duplicates").text


def test_merge_all(admin, tmp_path):
    lib = admin.app.state.lib
    for i in range(3):
        mk(admin, tmp_path, "Ugyanaz", ["Próba Anna"], name=f"u{i}", fmt=["epub", "mobi", "pdf"][i])
    scan(admin)
    admin.post("/admin/duplicates/merge-all")
    for _ in range(50):
        if not admin.app.state.db.val("SELECT 1 FROM tasks WHERE kind = 'merge' AND status = 'running'"):
            break
        time.sleep(0.1)
    rows, total = lib.list_books()
    assert total == 1 and sorted(rows[0]["formats"]) == ["epub", "mobi", "pdf"]
    assert "Ugyanaz" not in admin.get("/admin/duplicates").text.split("dupgroups")[1]
