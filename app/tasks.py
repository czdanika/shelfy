"""Háttérfeladatok: mi fut, mi várakozik, mi készült el, mi hibázott – haladással és futásidővel.

Minden hosszabb művelet (vizsgálat, konvertálás, küldés, mentés, visszaállítás, ellenőrzés, duplikátumkeresés)
feladatként jelenik meg a Feladatok oldalon. A szövegek fordíthatók: "tasks.kind_<fajta>", az üzenetek "tasks.msg_<kulcs>".
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager

from .db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | error
  progress INTEGER NOT NULL DEFAULT 0,      -- 0..100
  user_id INTEGER,
  user_name TEXT,
  subject TEXT,                             -- pl. fájlnév vagy könyvcím
  message TEXT,                             -- JSON: {"key": "...", ...paraméterek}
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL
);
CREATE INDEX IF NOT EXISTS tasks_created ON tasks(created_at DESC);
"""

KINDS = ("inspect", "approve_all", "convert", "send", "backup", "restore", "check", "duplicates")
STATUSES = ("queued", "running", "done", "error")


class Tasks:
    def __init__(self, db: Database, keep_days: int = 14) -> None:
        self.db = db
        with db.lock:
            db.conn.executescript(SCHEMA)
        db.after_open.append(lambda d: d.conn.executescript(SCHEMA))
        db.run("DELETE FROM tasks WHERE created_at < ?", (time.time() - keep_days * 86400,))
        # újraindításkor félbemaradt feladatok
        db.run("UPDATE tasks SET status = 'error', finished_at = ?, message = ? WHERE status IN ('running', 'queued')",
               (time.time(), json.dumps({"key": "interrupted"})))

    def create(self, kind: str, user=None, user_id: int | None = None, subject: str | None = None,
               status: str = "queued") -> int:
        uid = getattr(user, "id", None) or user_id
        name = getattr(user, "display_name", None) or getattr(user, "username", None)
        if uid and not name:
            name = self.db.val("SELECT COALESCE(display_name, username) FROM users WHERE id = ?", (uid,))
        now = time.time()
        return self.db.run("INSERT INTO tasks(kind, status, user_id, user_name, subject, created_at, started_at) "
                           "VALUES(?, ?, ?, ?, ?, ?, ?)",
                           (kind, status, uid, name, subject, now, now if status == "running" else None))

    def start(self, tid: int) -> None:
        self.db.run("UPDATE tasks SET status = 'running', started_at = COALESCE(started_at, ?) WHERE id = ?",
                    (time.time(), tid))

    def progress(self, tid: int, pct: float, key: str | None = None, **params) -> None:
        msg = json.dumps(dict(params, key=key), ensure_ascii=False) if key else None
        self.db.run("UPDATE tasks SET progress = ?, message = COALESCE(?, message) WHERE id = ?",
                    (max(0, min(100, int(pct))), msg, tid))

    def finish(self, tid: int, ok: bool = True, key: str | None = None, **params) -> None:
        msg = json.dumps(dict(params, key=key), ensure_ascii=False) if key else None
        self.db.run("UPDATE tasks SET status = ?, progress = CASE WHEN ? THEN 100 ELSE progress END, "
                    "message = COALESCE(?, message), finished_at = ?, started_at = COALESCE(started_at, ?) WHERE id = ?",
                    ("done" if ok else "error", 1 if ok else 0, msg, time.time(), time.time(), tid))

    @contextmanager
    def track(self, kind: str, user=None, subject: str | None = None, user_id: int | None = None):
        """with tasks.track("convert", user, "Könyv") as t: ... – hiba esetén 'error' állapotba kerül."""
        tid = self.create(kind, user, user_id=user_id, subject=subject, status="running")
        state = {"done": False}

        class Handle:
            id = tid

            def progress(_, pct, key=None, **params):
                self.progress(tid, pct, key, **params)

            def finish(_, ok=True, key=None, **params):
                state["done"] = True
                self.finish(tid, ok, key, **params)

        try:
            yield Handle()
        except Exception as e:
            self.finish(tid, False, "exception", error=f"{e.__class__.__name__}: {e}"[:200])
            raise
        else:
            if not state["done"]:
                self.finish(tid, True)

    def list(self, user_id: int | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM tasks" + (" WHERE user_id = ?" if user_id else "") + " ORDER BY id DESC LIMIT ?"
        rows = self.db.q(sql, ((user_id, limit) if user_id else (limit,)))
        out = []
        now = time.time()
        for r in rows:
            d = dict(r)
            d["message"] = json.loads(d["message"]) if d["message"] else {}
            start, end = d["started_at"], d["finished_at"] or (now if d["status"] == "running" else None)
            d["duration"] = (end - start) if start and end else None
            out.append(d)
        return out

    def active(self) -> int:
        return self.db.val("SELECT COUNT(*) FROM tasks WHERE status IN ('queued', 'running')") or 0
