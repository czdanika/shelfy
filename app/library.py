"""Könyvtár: tárolás (Szerző/Cím (id) mappaszerkezet), könyvek felvétele, lekérdezése, rendezése."""
from __future__ import annotations

import re
import shutil
import unicodedata
import uuid as uuidlib
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database, author_sort, now

FORMATS = ("epub", "azw3", "mobi", "azw", "pdf", "fb2", "docx", "rtf", "txt", "cbz", "cbr", "kepub", "djvu", "odt", "html", "htm", "lit", "prc")
MIME = {
    "epub": "application/epub+zip", "azw3": "application/vnd.amazon.ebook", "azw": "application/vnd.amazon.ebook",
    "mobi": "application/x-mobipocket-ebook", "prc": "application/x-mobipocket-ebook", "pdf": "application/pdf",
    "fb2": "application/x-fictionbook+xml", "txt": "text/plain", "rtf": "application/rtf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "cbz": "application/vnd.comicbook+zip", "cbr": "application/vnd.comicbook-rar", "djvu": "image/vnd.djvu",
    "odt": "application/vnd.oasis.opendocument.text", "html": "text/html", "htm": "text/html", "kepub": "application/kepub+zip",
}

SORTS = {
    "added_new": "b.added_at DESC",
    "added_old": "b.added_at ASC",
    "title_az": "b.sort_title COLLATE HU ASC",
    "title_za": "b.sort_title COLLATE HU DESC",
    "author_az": "first_author COLLATE HU ASC, b.sort_title COLLATE HU",
    "author_za": "first_author COLLATE HU DESC, b.sort_title COLLATE HU",
    "year_new": "b.year IS NULL, b.year DESC, b.added_at DESC",
    "year_old": "b.year IS NULL, b.year ASC, b.added_at DESC",
    "series": "s.name IS NULL, s.name COLLATE HU, b.series_index, b.sort_title COLLATE HU",
    "rating": "b.rating IS NULL, b.rating DESC, b.added_at DESC",
}
SORT_GROUPS = [  # felület: (címke kulcs, [(rendezés, rövid felirat kulcs, tooltip kulcs)])
    ("sort.added", [("added_new", "sort.newest", "sort.added_new"), ("added_old", "sort.oldest", "sort.added_old")]),
    ("sort.title", [("title_az", "sort.az", "sort.title_az"), ("title_za", "sort.za", "sort.title_za")]),
    ("sort.author", [("author_az", "sort.az", "sort.author_az"), ("author_za", "sort.za", "sort.author_za")]),
    ("sort.year", [("year_new", "sort.newest", "sort.year_new"), ("year_old", "sort.oldest", "sort.year_old")]),
    ("sort.series", [("series", "sort.series_order", "sort.series_full")]),
    ("sort.rating", [("rating", "sort.best", "sort.rating_full")]),
]

_HU_ORDER = {c: i for i, c in enumerate("aábcdeéfghiíjklmnoóöőpqrstuúüűvwxyz")}


def hu_key(s: str) -> tuple:
    """Magyar ábécérend közelítése (á az a után, ö/ő az o után stb.)."""
    out = []
    for ch in (s or "").casefold():
        if ch in _HU_ORDER:
            out.append(_HU_ORDER[ch])
        elif ch.isdigit():
            out.append(-20 + int(ch))
        elif ch.isalpha():
            base = unicodedata.normalize("NFKD", ch)[0]
            out.append(_HU_ORDER.get(base, 100) + 0.5)
        else:
            out.append(-30)
    return tuple(out)


def hu_collate(a: str, b: str) -> int:
    ka, kb = hu_key(a), hu_key(b)
    return (ka > kb) - (ka < kb)


ARTICLES = re.compile(r"^(a|az|egy|the|an|a)\s+", re.I)


def sort_title(title: str) -> str:
    return ARTICLES.sub("", title.strip()) or title


def safe_name(text: str, maxlen: int = 80) -> str:
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", text).strip(" .")
    return t[:maxlen].strip() or "_"


def ascii_name(text: str, maxlen: int = 80) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Za-z0-9 ._,()\-]+", "", t)
    return re.sub(r"\s+", " ", t).strip(" .")[:maxlen] or "konyv"


@dataclass
class Meta:
    title: str
    authors: list[str] = field(default_factory=list)
    series: str | None = None
    series_index: float | None = None
    publisher: str | None = None
    year: int | None = None
    language: str | None = None
    isbn: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    rating: int | None = None
    source: str | None = None
    source_url: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Meta":
        def num(x, t):
            try:
                return t(x) if x not in (None, "") else None
            except (TypeError, ValueError):
                return None
        return cls(
            title=(d.get("title") or "").strip() or "?",
            authors=[a.strip() for a in d.get("authors") or [] if a and a.strip()],
            series=(d.get("series") or "").strip() or None,
            series_index=num(d.get("series_index"), float),
            publisher=(d.get("publisher") or "").strip() or None,
            year=num(d.get("year"), int),
            language=(d.get("language") or "").strip() or None,
            isbn=(d.get("isbn") or "").strip() or None,
            tags=list(dict.fromkeys(t.strip() for t in d.get("tags") or [] if t and t.strip())),
            description=(d.get("description") or "").strip() or None,
            rating=num(d.get("rating"), int),
            source=d.get("source"), source_url=d.get("source_url"),
        )


class Library:
    def __init__(self, db: Database, root: Path) -> None:
        self.db = db
        self.root = root
        db.conn.create_collation("HU", hu_collate)
        db.after_open.append(lambda d: d.conn.create_collation("HU", hu_collate))  # visszaállítás utáni újranyitáskor is

    # ------------------------------------------------------------------ tárolás
    def book_dir(self, book_id: int) -> Path:
        return self.root / self.db.val("SELECT path FROM books WHERE id = ?", (book_id,))

    def cover_path(self, book_id: int) -> Path:
        return self.book_dir(book_id) / "cover.jpg"

    def file_path(self, book_id: int, fmt: str) -> Path | None:
        r = self.db.one("SELECT filename FROM files WHERE book_id = ? AND format = ?", (book_id, fmt))
        return self.book_dir(book_id) / r["filename"] if r else None

    def _rel_dir(self, meta: Meta, book_id: int) -> str:
        author = safe_name(meta.authors[0] if meta.authors else "Ismeretlen szerző")
        return f"{author}/{safe_name(meta.title, 60)} ({book_id})"

    def _base_filename(self, meta: Meta) -> str:
        who = meta.authors[0] if meta.authors else ""
        return safe_name(f"{meta.title} - {who}" if who else meta.title, 100)

    # ------------------------------------------------------------------ írás
    def add_book(self, meta: Meta, files: list[tuple[Path, str, str]], cover: bytes | None = None) -> int:
        """files: (forrásfájl, formátum, sha256). A forrásfájlok a könyv mappájába másolódnak."""
        ts = now()
        with self.db.tx():
            bid = self.db.run(
                "INSERT INTO books(uuid, title, sort_title, path, added_at, updated_at) VALUES(?, ?, ?, '', ?, ?)",
                (str(uuidlib.uuid4()), meta.title, sort_title(meta.title), ts, ts))
            rel = self._rel_dir(meta, bid)
            self.db.run("UPDATE books SET path = ? WHERE id = ?", (rel, bid))
            self._write_meta(bid, meta)
        d = self.root / rel
        d.mkdir(parents=True, exist_ok=True)
        for src, fmt, sha in files:
            self.add_file(bid, src, fmt, sha, meta)
        if cover:
            self.set_cover(bid, cover)
        return bid

    def add_file(self, book_id: int, src: Path, fmt: str, sha: str, meta: Meta | None = None) -> None:
        meta = meta or self.meta(book_id)
        name = f"{self._base_filename(meta)}.{fmt}"
        dst = self.book_dir(book_id) / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        self.db.run("INSERT INTO files(book_id, format, filename, size, sha256) VALUES(?, ?, ?, ?, ?) "
                    "ON CONFLICT(book_id, format) DO UPDATE SET filename = excluded.filename, size = excluded.size, "
                    "sha256 = excluded.sha256", (book_id, fmt, name, dst.stat().st_size, sha))

    def set_cover(self, book_id: int, data: bytes) -> None:
        from .images import to_jpeg
        p = self.cover_path(book_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(to_jpeg(data))
        self.db.run("UPDATE books SET has_cover = 1, updated_at = ? WHERE id = ?", (now(), book_id))

    def _write_meta(self, bid: int, m: Meta) -> None:
        db = self.db
        pub = db.name_id("publishers", m.publisher) if m.publisher else None
        ser = db.name_id("series", m.series) if m.series else None
        db.run("""UPDATE books SET title = ?, sort_title = ?, year = ?, language = ?, isbn = ?, publisher_id = ?,
                  series_id = ?, series_index = ?, rating = ?, description = ?, source = COALESCE(?, source),
                  source_url = COALESCE(?, source_url), updated_at = ? WHERE id = ?""",
               (m.title, sort_title(m.title), m.year, m.language, m.isbn, pub, ser,
                m.series_index if ser else None, m.rating, m.description, m.source, m.source_url, now(), bid))
        db.run("DELETE FROM book_authors WHERE book_id = ?", (bid,))
        for i, a in enumerate(m.authors or ["Ismeretlen szerző"]):
            aid = db.name_id("authors", a, author_sort(a, m.language))
            db.run("INSERT OR IGNORE INTO book_authors(book_id, author_id, position) VALUES(?, ?, ?)", (bid, aid, i))
        db.run("DELETE FROM book_tags WHERE book_id = ?", (bid,))
        for t in m.tags:
            db.run("INSERT OR IGNORE INTO book_tags(book_id, tag_id) VALUES(?, ?)", (bid, db.name_id("tags", t)))

    def update_meta(self, bid: int, m: Meta) -> None:
        with self.db.tx():
            self._write_meta(bid, m)
        self._cleanup_orphans()

    def delete_book(self, bid: int) -> None:
        d = self.book_dir(bid)
        self.db.run("DELETE FROM books WHERE id = ?", (bid,))
        if d.exists() and self.root in d.parents:
            shutil.rmtree(d, ignore_errors=True)
            try:
                d.parent.rmdir()
            except OSError:
                pass
        self._cleanup_orphans()

    def _cleanup_orphans(self) -> None:
        db = self.db
        db.run("DELETE FROM authors WHERE id NOT IN (SELECT author_id FROM book_authors)")
        db.run("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM book_tags)")
        db.run("DELETE FROM series WHERE id NOT IN (SELECT series_id FROM books WHERE series_id IS NOT NULL)")
        db.run("DELETE FROM publishers WHERE id NOT IN (SELECT publisher_id FROM books WHERE publisher_id IS NOT NULL)")

    # ------------------------------------------------------------------ olvasás
    def meta(self, bid: int) -> Meta:
        b = self.book(bid)
        return Meta(title=b["title"], authors=[a["name"] for a in b["authors"]], series=b["series"],
                    series_index=b["series_index"], publisher=b["publisher"], year=b["year"], language=b["language"],
                    isbn=b["isbn"], tags=[t["name"] for t in b["tags"]], description=b["description"], rating=b["rating"],
                    source=b.get("source"), source_url=b.get("source_url"))

    def book(self, bid: int) -> dict | None:
        r = self.db.one("""SELECT b.*, s.name AS series, p.name AS publisher FROM books b
                           LEFT JOIN series s ON s.id = b.series_id LEFT JOIN publishers p ON p.id = b.publisher_id
                           WHERE b.id = ?""", (bid,))
        if not r:
            return None
        d = dict(r)
        d["authors"] = [dict(x) for x in self.db.q(
            "SELECT a.id, a.name FROM book_authors ba JOIN authors a ON a.id = ba.author_id WHERE ba.book_id = ? ORDER BY ba.position", (bid,))]
        d["tags"] = [dict(x) for x in self.db.q(
            "SELECT t.id, t.name FROM book_tags bt JOIN tags t ON t.id = bt.tag_id WHERE bt.book_id = ? ORDER BY t.name COLLATE HU", (bid,))]
        d["files"] = [dict(x) for x in self.db.q("SELECT format, filename, size, sha256 FROM files WHERE book_id = ? ORDER BY format", (bid,))]
        d["formats"] = [f["format"] for f in d["files"]]
        return d

    def list_books(self, *, sort: str = "added_new", q: str | None = None, author: int | None = None,
                   series: int | None = None, tag: int | None = None, publisher: int | None = None,
                   language: str | None = None, fmt: str | None = None, rating: int | None = None,
                   view: str | None = None, user_id: int | None = None, shelf: int | None = None,
                   page: int = 1, per_page: int = 60) -> tuple[list[dict], int]:
        where, args = ["b.archived = 0"], []
        if view == "archived":
            where = ["b.archived = 1"]
        if q:
            like = f"%{q.strip()}%"
            where.append("""(b.title LIKE ? OR b.isbn = ? OR s.name LIKE ? OR p.name LIKE ?
                OR EXISTS (SELECT 1 FROM book_authors ba JOIN authors a ON a.id = ba.author_id WHERE ba.book_id = b.id AND a.name LIKE ?)
                OR EXISTS (SELECT 1 FROM book_tags bt JOIN tags t ON t.id = bt.tag_id WHERE bt.book_id = b.id AND t.name LIKE ?))""")
            args += [like, q.strip(), like, like, like, like]
        if author:
            where.append("EXISTS (SELECT 1 FROM book_authors WHERE book_id = b.id AND author_id = ?)"); args.append(author)
        if tag:
            where.append("EXISTS (SELECT 1 FROM book_tags WHERE book_id = b.id AND tag_id = ?)"); args.append(tag)
        if series:
            where.append("b.series_id = ?"); args.append(series)
        if publisher:
            where.append("b.publisher_id = ?"); args.append(publisher)
        if language:
            where.append("b.language = ?"); args.append(language)
        if fmt:
            where.append("EXISTS (SELECT 1 FROM files WHERE book_id = b.id AND format = ?)"); args.append(fmt)
        if rating is not None:
            where.append("b.rating = ?"); args.append(rating)
        if shelf:
            where.append("EXISTS (SELECT 1 FROM shelf_books WHERE book_id = b.id AND shelf_id = ?)"); args.append(shelf)
        order = SORTS.get(sort, SORTS["added_new"])
        if view == "hot":
            order = "(SELECT COUNT(*) FROM downloads d WHERE d.book_id = b.id) DESC, b.added_at DESC"
            where.append("EXISTS (SELECT 1 FROM downloads d WHERE d.book_id = b.id)")
        elif view == "top":
            where.append("b.rating >= 4")
        elif view in ("read", "reading") and user_id:
            where.append("EXISTS (SELECT 1 FROM read_state r WHERE r.book_id = b.id AND r.user_id = ? AND r.status = ?)")
            args += [user_id, view]
        elif view == "unread" and user_id:
            where.append("NOT EXISTS (SELECT 1 FROM read_state r WHERE r.book_id = b.id AND r.user_id = ? AND r.status = 'read')")
            args.append(user_id)
        elif view == "discover":
            order = "RANDOM()"
        elif view == "incomplete":
            where.append("(b.has_cover = 0 OR b.description IS NULL OR NOT EXISTS (SELECT 1 FROM book_tags WHERE book_id = b.id))")
        base = f"""FROM books b LEFT JOIN series s ON s.id = b.series_id LEFT JOIN publishers p ON p.id = b.publisher_id
                   WHERE {' AND '.join(where)}"""
        total = self.db.val(f"SELECT COUNT(*) {base}", args)
        rows = self.db.q(f"""SELECT b.id, b.title, b.year, b.rating, b.has_cover, b.added_at, b.series_index, b.language,
                   s.name AS series, s.id AS series_id, p.name AS publisher, p.id AS publisher_id,
                   substr(b.description, 1, 1200) AS description,
                   (SELECT a.sort FROM book_authors ba JOIN authors a ON a.id = ba.author_id WHERE ba.book_id = b.id
                     ORDER BY ba.position LIMIT 1) AS first_author
                   {base} ORDER BY {order} LIMIT ? OFFSET ?""", args + [per_page, (max(page, 1) - 1) * per_page])
        books = [dict(r) for r in rows]
        if books:
            ids = [b["id"] for b in books]
            marks = ",".join("?" * len(ids))
            auth: dict[int, list] = {}
            for r in self.db.q(f"""SELECT ba.book_id, a.id, a.name FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                                   WHERE ba.book_id IN ({marks}) ORDER BY ba.position""", ids):
                auth.setdefault(r["book_id"], []).append({"id": r["id"], "name": r["name"]})
            fmts: dict[int, list] = {}
            for r in self.db.q(f"SELECT book_id, format FROM files WHERE book_id IN ({marks}) ORDER BY format", ids):
                fmts.setdefault(r["book_id"], []).append(r["format"])
            tags: dict[int, list] = {}
            for r in self.db.q(f"""SELECT bt.book_id, t.id, t.name FROM book_tags bt JOIN tags t ON t.id = bt.tag_id
                                   WHERE bt.book_id IN ({marks}) ORDER BY t.name""", ids):
                tags.setdefault(r["book_id"], []).append({"id": r["id"], "name": r["name"]})
            for b in books:
                b["authors"] = auth.get(b["id"], [])
                b["formats"] = fmts.get(b["id"], [])
                b["tags"] = tags.get(b["id"], [])
        return books, total

    def facet(self, kind: str) -> list[dict]:
        """Böngészési listák darabszámmal."""
        if kind == "authors":
            sql = """SELECT a.id, a.name, COUNT(*) AS n FROM authors a JOIN book_authors ba ON ba.author_id = a.id
                     JOIN books b ON b.id = ba.book_id AND b.archived = 0 GROUP BY a.id ORDER BY a.sort COLLATE HU"""
        elif kind == "series":
            sql = """SELECT s.id, s.name, COUNT(*) AS n FROM series s JOIN books b ON b.series_id = s.id AND b.archived = 0
                     GROUP BY s.id ORDER BY s.name COLLATE HU"""
        elif kind == "tags":
            sql = """SELECT t.id, t.name, COUNT(*) AS n FROM tags t JOIN book_tags bt ON bt.tag_id = t.id
                     JOIN books b ON b.id = bt.book_id AND b.archived = 0 GROUP BY t.id ORDER BY t.name COLLATE HU"""
        elif kind == "publishers":
            sql = """SELECT p.id, p.name, COUNT(*) AS n FROM publishers p JOIN books b ON b.publisher_id = p.id AND b.archived = 0
                     GROUP BY p.id ORDER BY p.name COLLATE HU"""
        elif kind == "languages":
            sql = """SELECT language AS id, language AS name, COUNT(*) AS n FROM books WHERE archived = 0 AND language IS NOT NULL
                     GROUP BY language ORDER BY n DESC"""
        elif kind == "formats":
            sql = """SELECT f.format AS id, UPPER(f.format) AS name, COUNT(DISTINCT f.book_id) AS n FROM files f
                     JOIN books b ON b.id = f.book_id AND b.archived = 0 GROUP BY f.format ORDER BY n DESC"""
        elif kind == "ratings":
            sql = """SELECT rating AS id, rating AS name, COUNT(*) AS n FROM books WHERE archived = 0 AND rating IS NOT NULL
                     GROUP BY rating ORDER BY rating DESC"""
        else:
            return []
        return [dict(r) for r in self.db.q(sql)]

    def counts(self) -> dict:
        return {
            "books": self.db.val("SELECT COUNT(*) FROM books WHERE archived = 0"),
            "size": self.db.val("SELECT COALESCE(SUM(size), 0) FROM files") or 0,
        }

    def stats(self) -> dict:
        """Részletes statisztika az admin oldalra."""
        q = self.db.val
        return {
            "books": q("SELECT COUNT(*) FROM books WHERE archived = 0"),
            "archived": q("SELECT COUNT(*) FROM books WHERE archived = 1"),
            "files": q("SELECT COUNT(*) FROM files"),
            "size": q("SELECT COALESCE(SUM(size), 0) FROM files") or 0,
            "authors": q("SELECT COUNT(DISTINCT author_id) FROM book_authors"),
            "series": q("SELECT COUNT(DISTINCT series_id) FROM books WHERE series_id IS NOT NULL"),
            "no_cover": q("SELECT COUNT(*) FROM books WHERE has_cover = 0 AND archived = 0"),
            "formats": [dict(r) for r in self.db.q("SELECT format, COUNT(*) AS n, SUM(size) AS size FROM files GROUP BY format ORDER BY n DESC")],
            "duplicates": len(self.duplicate_groups()),
        }

    def duplicate_groups(self) -> list[list[int]]:
        """Azonos cím + első szerző (kis- és nagybetű, ékezet nélkül) = valószínű duplikátum."""
        groups: dict[str, list[int]] = {}
        for r in self.db.q("""SELECT b.id, b.title, (SELECT a.name FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                              WHERE ba.book_id = b.id ORDER BY ba.position LIMIT 1) AS author FROM books b WHERE b.archived = 0"""):
            groups.setdefault(norm(r["title"]) + "|" + norm(r["author"] or ""), []).append(r["id"])
        return [ids for ids in groups.values() if len(ids) > 1]

    def find_duplicates(self, sha: str | None, title: str | None, authors: list[str] | None) -> list[int]:
        ids: list[int] = []
        if sha:
            ids += [r[0] for r in self.db.q("SELECT DISTINCT book_id FROM files WHERE sha256 = ?", (sha,))]
        if title:
            nt = norm(title)
            na = {norm(a) for a in authors or []}
            for r in self.db.q("""SELECT b.id, b.title FROM books b"""):
                if norm(r["title"]) == nt and r["id"] not in ids:
                    names = {norm(x[0]) for x in self.db.q(
                        "SELECT a.name FROM book_authors ba JOIN authors a ON a.id = ba.author_id WHERE ba.book_id = ?", (r["id"],))}
                    if not na or names & na:
                        ids.append(r["id"])
        return ids


def norm(s: str) -> str:
    """Összehasonlításhoz: kisbetű, ékezet nélkül, csak betűk és számok – bármilyen írásrendszerben
    (cirill, kínai, japán, koreai is megmarad, különben ezek a címek üresre normálódnának)."""
    t = unicodedata.normalize("NFKD", s or "")
    t = unicodedata.normalize("NFKC", "".join(c for c in t if not unicodedata.combining(c))).casefold()
    return re.sub(r"[\W_]+", " ", t).strip()
