"""SQLite adatbázis: séma és egyszerű segédfüggvények."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

# felhasználónkénti e-mail (SMTP) beállítás; a jelszó titkosítva
USER_MAIL_COLUMNS = ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password", "smtp_from")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'reader',          -- admin | editor | reader
  can_upload INTEGER NOT NULL DEFAULT 0,
  can_edit INTEGER NOT NULL DEFAULT 0,
  can_download INTEGER NOT NULL DEFAULT 1,
  can_delete INTEGER NOT NULL DEFAULT 0,
  kindle_email TEXT,
  language TEXT,
  created_at REAL NOT NULL,
  last_login REAL
);
CREATE TABLE IF NOT EXISTS authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS series (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS publishers (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY,
  uuid TEXT NOT NULL,
  title TEXT NOT NULL,
  sort_title TEXT NOT NULL,
  path TEXT NOT NULL,
  year INTEGER,
  language TEXT,
  isbn TEXT,
  publisher_id INTEGER REFERENCES publishers(id),
  series_id INTEGER REFERENCES series(id),
  series_index REAL,
  rating INTEGER,                                 -- 0..5
  description TEXT,
  has_cover INTEGER NOT NULL DEFAULT 0,
  source TEXT,
  source_url TEXT,
  added_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS book_authors (book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  author_id INTEGER NOT NULL REFERENCES authors(id), position INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (book_id, author_id));
CREATE TABLE IF NOT EXISTS book_tags (book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id), PRIMARY KEY (book_id, tag_id));
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  format TEXT NOT NULL,
  filename TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  UNIQUE (book_id, format)
);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY,
  user_id INTEGER REFERENCES users(id),
  filename TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  format TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',          -- pending | inspecting | review | done | error | rejected
  extracted TEXT,                                  -- JSON: a fájlból kiolvasott adatok
  candidates TEXT,                                 -- JSON: online találatok
  inspection TEXT,                                 -- JSON: vizsgálati jegyzőkönyv (ellenőrzések)
  duplicate_of INTEGER,
  book_id INTEGER,
  message TEXT,
  source TEXT NOT NULL DEFAULT 'web',              -- web | folder
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS read_state (user_id INTEGER NOT NULL, book_id INTEGER NOT NULL, status TEXT NOT NULL,
  updated_at REAL NOT NULL, PRIMARY KEY (user_id, book_id));
CREATE TABLE IF NOT EXISTS reading_pos (user_id INTEGER NOT NULL, book_id INTEGER NOT NULL, cfi TEXT,
  percent REAL, updated_at REAL NOT NULL, PRIMARY KEY (user_id, book_id));
CREATE TABLE IF NOT EXISTS downloads (id INTEGER PRIMARY KEY, user_id INTEGER, book_id INTEGER NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS shelves (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shelf_books (shelf_id INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE, PRIMARY KEY (shelf_id, book_id));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.after_open: list = []  # további sémák (pl. eseménynapló) újranyitáskor
        self._open()

    def _open(self) -> None:
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            for fn in self.after_open:
                fn(self)

    def snapshot(self, dst: Path) -> None:
        """Konzisztens másolat a futó adatbázisról (SQLite online backup)."""
        with self.lock:
            out = sqlite3.connect(str(dst))
            try:
                self.conn.backup(out)
            finally:
                out.close()

    def replace_with(self, src: Path) -> None:
        """A teljes adatbázis cseréje egy másik fájlra (visszaállítás); a kapcsolat újranyílik."""
        with self.lock:
            self.conn.close()
            for suffix in ("-wal", "-shm"):
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            tmp = self.path.with_suffix(".restore")
            tmp.write_bytes(src.read_bytes())
            tmp.replace(self.path)
            self._open()

    def _migrate(self) -> None:
        """Új oszlopok felvétele a meglévő adatbázisokba (verzióváltáskor)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(users)")}
        for c in USER_MAIL_COLUMNS:
            if c not in cols:
                self.conn.execute(f"ALTER TABLE users ADD COLUMN {c} TEXT")

    def q(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def val(self, sql: str, args: tuple | list = ()):
        r = self.one(sql, args)
        return r[0] if r else None

    def run(self, sql: str, args: tuple | list = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    def tx(self):
        return _Tx(self)

    # --- beállítások
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        v = self.val("SELECT value FROM settings WHERE key = ?", (key,))
        return default if v is None else v

    def set_setting(self, key: str, value: str | None) -> None:
        self.run("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))

    # --- név -> azonosító (szerző, sorozat, kiadó, címke)
    def name_id(self, table: str, name: str, sort: str | None = None) -> int:
        assert table in ("authors", "series", "publishers", "tags")
        name = " ".join(name.split())
        row = self.one(f"SELECT id FROM {table} WHERE name = ?", (name,))
        if row:
            return row[0]
        if table == "authors":
            return self.run("INSERT INTO authors(name, sort) VALUES(?, ?)", (name, sort or author_sort(name)))
        return self.run(f"INSERT INTO {table}(name) VALUES(?)", (name,))


class _Tx:
    def __init__(self, db: Database) -> None:
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.lock.release()


HU_PARTICLES = {"de", "von", "van", "der", "la", "le", "du", "di"}


HU_CHARS = set("áéíóöőúüűÁÉÍÓÖŐÚÜŰ")


def author_sort(name: str, language: str | None = None) -> str:
    """Rendezési név. Magyar nevek (Gárdonyi Géza) változatlanok, nyugatiak: 'Stoker, Bram'.

    Magyarnak vesszük, ha a könyv nyelve magyar, vagy a névben magyar ékezet van.
    """
    if "," in name:
        return name
    parts = name.split()
    if len(parts) < 2:
        return name
    if (language or "").lower() in ("hu", "hun", "hungarian", "magyar") or HU_CHARS & set(name):
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def now() -> float:
    return time.time()
