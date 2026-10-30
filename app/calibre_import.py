"""Meglévő Calibre könyvtár átvétele (metadata.db + fájlok + borítók).

A Calibre adatbázisát csak olvassa (read-only), a fájlokat átmásolja a Shelfy könyvtárába.
Már átvett könyvet (azonos Calibre uuid vagy azonos fájl) nem vesz fel újra, így többször is futtatható.
Parancssorból: python -m app.calibre_import /calibre-konyvtar
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database
from .library import FORMATS, Library, Meta
from .tools import sha256_file


@dataclass
class Progress:
    running: bool = False
    total: int = 0
    done: int = 0
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    finished_at: float | None = None


def _year(pubdate: str | None) -> int | None:
    # a Calibre az ismeretlen dátumot 0101-01-01-ként tárolja
    if pubdate and pubdate[:4].isdigit() and int(pubdate[:4]) > 1000:
        return int(pubdate[:4])
    return None


def _ts(stamp: str | None) -> float | None:
    try:
        return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if stamp else None
    except ValueError:
        return None


def read_calibre(root: Path) -> list[dict]:
    """A Calibre metadata.db tartalma könyvenként egy szótárban."""
    con = sqlite3.connect(f"file:{root / 'metadata.db'}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row

    def multi(sql: str) -> dict[int, list]:
        out: dict[int, list] = {}
        for r in con.execute(sql):
            out.setdefault(r[0], []).append(r[1])
        return out

    authors = multi("SELECT l.book, a.name FROM books_authors_link l JOIN authors a ON a.id = l.author ORDER BY l.id")
    tags = multi("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id = l.tag")
    series = {k: v[0] for k, v in multi("SELECT l.book, s.name FROM books_series_link l JOIN series s ON s.id = l.series").items()}
    pubs = {k: v[0] for k, v in multi("SELECT l.book, p.name FROM books_publishers_link l JOIN publishers p ON p.id = l.publisher").items()}
    ratings = {k: v[0] for k, v in multi("SELECT l.book, r.rating FROM books_ratings_link l JOIN ratings r ON r.id = l.rating").items()}
    langs = {k: v[0] for k, v in multi("SELECT l.book, g.lang_code FROM books_languages_link l JOIN languages g ON g.id = l.lang_code ORDER BY l.item_order").items()}
    comments = {r[0]: r[1] for r in con.execute("SELECT book, text FROM comments")}
    idents = multi("SELECT book, type || ':' || val FROM identifiers")
    files = multi("SELECT book, format || '|' || name FROM data")
    books = []
    for r in con.execute("SELECT id, title, uuid, path, has_cover, series_index, pubdate, timestamp FROM books ORDER BY id"):
        ids = dict(x.split(":", 1) for x in idents.get(r["id"], []))
        books.append({
            "calibre_id": r["id"], "uuid": r["uuid"], "path": r["path"], "has_cover": bool(r["has_cover"]),
            "added_at": _ts(r["timestamp"]),
            "meta": {
                "title": r["title"], "authors": authors.get(r["id"], []), "tags": tags.get(r["id"], []),
                "series": series.get(r["id"]), "series_index": r["series_index"] if series.get(r["id"]) else None,
                "publisher": pubs.get(r["id"]), "year": _year(r["pubdate"]), "language": langs.get(r["id"]),
                "isbn": ids.get("isbn"), "description": comments.get(r["id"]),
                "rating": round(ratings[r["id"]] / 2) if ratings.get(r["id"]) else None,
                "source": "calibre",
            },
            "files": [tuple(x.split("|", 1)) for x in files.get(r["id"], [])],
        })
    con.close()
    return books


def import_library(root: Path, db: Database, lib: Library, progress: Progress | None = None) -> Progress:
    p = progress or Progress()
    p.running, p.done, p.added, p.skipped, p.errors, p.finished_at = True, 0, 0, 0, [], None
    try:
        books = read_calibre(root)
        p.total = len(books)
        for b in books:
            try:
                if b["uuid"] and db.val("SELECT 1 FROM books WHERE uuid = ?", (b["uuid"],)):
                    p.skipped += 1
                    continue
                folder = root / b["path"]
                files = []
                for fmt, name in b["files"]:
                    f = folder / f"{name}.{fmt.lower()}"
                    if f.exists() and fmt.lower() in FORMATS:
                        files.append((f, fmt.lower(), sha256_file(f)))
                if files and lib.find_duplicates(files[0][2], None, None):
                    p.skipped += 1
                    continue
                cover_file = folder / "cover.jpg"
                cover = cover_file.read_bytes() if b["has_cover"] and cover_file.exists() else None
                bid = lib.add_book(Meta.from_dict(b["meta"]), files, cover)
                db.run("UPDATE books SET uuid = COALESCE(?, uuid), added_at = COALESCE(?, added_at) WHERE id = ?",
                       (b["uuid"], b["added_at"], bid))
                p.added += 1
            except Exception as e:  # egy hibás könyv ne állítsa le az egészet
                p.errors.append(f"{b['meta'].get('title')}: {e.__class__.__name__}: {e}"[:300])
            finally:
                p.done += 1
    except Exception as e:
        p.errors.append(f"{e.__class__.__name__}: {e}"[:300])
    finally:
        p.running = False
        import time
        p.finished_at = time.time()
    return p


async def import_async(root: Path, db: Database, lib: Library, progress: Progress) -> Progress:
    return await asyncio.to_thread(import_library, root, db, lib, progress)


if __name__ == "__main__":
    import sys

    from .config import load_config
    cfg = load_config()
    database = Database(cfg.data_dir / "shelfy.db")
    res = import_library(Path(sys.argv[1]), database, Library(database, cfg.library_dir))
    print(f"összesen {res.total}, felvéve {res.added}, kihagyva {res.skipped}, hiba {len(res.errors)}")
    for err in res.errors:
        print("  ", err)
