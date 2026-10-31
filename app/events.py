"""Eseménynapló: ki, mikor, mit csinált (feltöltés, felvétel, küldés, letöltés, szerkesztés, belépés…).

Az események szövege fordítható (locales: "log.<fajta>"); a paraméterek (pl. a könyv címe) a bejegyzéssel
együtt tárolódnak, így a napló akkor is olvasható marad, ha a könyvet vagy a felhasználót később törlik.
"""
from __future__ import annotations

import json
import time

from .db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  at REAL NOT NULL,
  kind TEXT NOT NULL,
  ok INTEGER NOT NULL DEFAULT 1,
  user_id INTEGER,
  user_name TEXT,
  book_id INTEGER,
  params TEXT
);
CREATE INDEX IF NOT EXISTS events_at ON events(at DESC);
CREATE INDEX IF NOT EXISTS events_book ON events(book_id, at DESC);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, at DESC);
"""

# csoportok a szűrőhöz
GROUPS = {
    "books": ("upload", "inspected", "inspect_error", "added", "auto_added", "format_added", "rejected",
              "edited", "metadata_refreshed", "converted", "convert_failed", "deleted", "hidden", "unhidden", "archived", "unarchived"),
    "sending": ("sent", "send_failed", "mail_test", "mail_test_failed"),
    "downloads": ("downloaded",),
    "users": ("login", "login_failed", "user_created", "user_updated", "user_deleted", "password_reset"),
    "system": ("settings_saved", "backup_created", "backup_auto", "backup_restored", "backup_failed"),
}
KINDS = tuple(k for g in GROUPS.values() for k in g)


class Events:
    def __init__(self, db: Database, keep_days: int = 365) -> None:
        self.db = db
        with db.lock:
            db.conn.executescript(SCHEMA)
        db.after_open.append(lambda d: d.conn.executescript(SCHEMA))  # visszaállítás után is legyen napló
        db.run("DELETE FROM events WHERE at < ?", (time.time() - keep_days * 86400,))

    def log(self, kind: str, user=None, book_id: int | None = None, ok: bool = True, user_id: int | None = None,
            **params) -> None:
        """user: User objektum vagy None; user_id: ha csak az azonosító ismert (pl. háttérfolyamatban)."""
        uid = getattr(user, "id", None) or user_id
        name = getattr(user, "display_name", None) or getattr(user, "username", None)
        if uid and not name:
            name = self.db.val("SELECT COALESCE(display_name, username) FROM users WHERE id = ?", (uid,))
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        self.db.run("INSERT INTO events(at, kind, ok, user_id, user_name, book_id, params) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (time.time(), kind, 1 if ok else 0, uid, name, book_id, json.dumps(clean, ensure_ascii=False)))

    def query(self, *, group: str | None = None, kind: str | None = None, user_id: int | None = None,
              book_id: int | None = None, errors: bool = False, since: float | None = None,
              q: str | None = None, page: int = 1, per_page: int = 50) -> tuple[list[dict], int]:
        where, args = [], []
        if kind:
            where.append("kind = ?"); args.append(kind)
        elif group in GROUPS:
            where.append(f"kind IN ({','.join('?' * len(GROUPS[group]))})"); args += list(GROUPS[group])
        if user_id:
            where.append("user_id = ?"); args.append(user_id)
        if book_id:
            where.append("book_id = ?"); args.append(book_id)
        if errors:
            where.append("ok = 0")
        if since:
            where.append("at >= ?"); args.append(since)
        if q:
            where.append("(params LIKE ? OR user_name LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
        w = ("WHERE " + " AND ".join(where)) if where else ""
        total = self.db.val(f"SELECT COUNT(*) FROM events {w}", args)
        rows = self.db.q(f"SELECT * FROM events {w} ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
                         args + [per_page, (page - 1) * per_page])
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"] or "{}")
            out.append(d)
        return out, total
