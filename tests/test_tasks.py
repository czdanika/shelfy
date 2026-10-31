"""Háttérfeladatok: létrehozás, haladás, hiba, a Feladatok oldal és a karbantartó feladatok indítása."""
from __future__ import annotations

import time

import pytest

from app.tasks import Tasks


def test_task_lifecycle(admin):
    t: Tasks = admin.app.state.tasks
    tid = t.create("convert", user_id=None, subject="Valami")
    assert t.active() >= 1
    t.start(tid)
    t.progress(tid, 40)
    row = [r for r in t.list() if r["id"] == tid][0]
    assert row["status"] == "running" and row["progress"] == 40
    t.finish(tid, True, "backup_done", size=1024)
    row = [r for r in t.list() if r["id"] == tid][0]
    assert row["status"] == "done" and row["progress"] == 100 and row["message"]["key"] == "backup_done"
    with pytest.raises(RuntimeError):
        with t.track("convert", subject="Hibás"):
            raise RuntimeError("bumm")
    assert t.list()[0]["status"] == "error"


def test_tasks_page_and_run(admin):
    r = admin.post("/tasks/run/check", follow_redirects=False)
    assert r.status_code == 303 and "/tasks" in r.headers["location"]
    for _ in range(50):
        if not admin.app.state.tasks.active():
            break
        time.sleep(0.05)
    page = admin.get("/tasks").text
    assert "Háttérfeladatok" in page and "Könyvtár ellenőrzése" in page
    assert admin.get("/tasks.json").json()["active"] == 0


def test_tasks_reader_sees_own_only(admin):
    from test_admin import login, make_user
    admin.app.state.tasks.create("backup", user_id=1, subject="admin-mentés")
    make_user(admin, can_download=True)
    login(admin, "olvaso", "olvaso123")
    assert admin.post("/tasks/run/check", follow_redirects=False).status_code == 403
    assert "admin-mentés" not in admin.get("/tasks").text
