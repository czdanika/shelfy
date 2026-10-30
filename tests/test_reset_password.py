"""A jelszó-visszaállító parancs."""
from __future__ import annotations

import pytest

from app import reset_password
from test_admin import login, make_user


@pytest.fixture
def env(admin, cfg, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(cfg.data_dir))
    monkeypatch.setenv("LIBRARY_DIR", str(cfg.library_dir))
    return admin


def test_list_users(env, capsys):
    assert reset_password.main([]) == 0
    assert "admin" in capsys.readouterr().out


def test_reset_and_make_admin(env, monkeypatch, capsys):
    make_user(env, "orsi", can_download=True)
    login(env, "orsi", "olvaso123")
    assert env.get("/me").status_code == 200
    monkeypatch.setenv("NEW_PASSWORD", "ujjelszo123")
    assert reset_password.main(["orsi", "--admin"]) == 0
    # a régi munkamenet érvénytelen lett
    assert env.get("/me", follow_redirects=False).status_code == 303
    login(env, "orsi", "ujjelszo123")
    assert env.get("/admin").status_code == 200


def test_unknown_user_and_short_password(env, monkeypatch):
    assert reset_password.main(["nincs"]) == 1
    monkeypatch.setenv("NEW_PASSWORD", "rovid")
    assert reset_password.main(["admin"]) == 1
