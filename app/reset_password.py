"""Jelszó visszaállítása parancssorból (pl. ha az admin elfelejtette a sajátját).

    docker exec -it shelfy python -m app.reset_password            # felhasználók listája
    docker exec -it shelfy python -m app.reset_password daniel     # új jelszó beállítása
    docker exec -it shelfy python -m app.reset_password daniel --admin   # és adminná tétel

Csak a szerveren futtatható, a webes felületen nem nyit új utat. A jelszó cseréje után
a felhasználó minden eszközről kijelentkezik (a munkamenet a jelszóhoz kötött).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from .auth import ROLE_DEFAULTS, hash_password
from .config import load_config
from .db import Database


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.reset_password",
                                 description="Reset a Shelfy user's password / Felhasználó jelszavának visszaállítása")
    ap.add_argument("username", nargs="?", help="felhasználónév (nélküle: lista)")
    ap.add_argument("--admin", action="store_true", help="a felhasználó adminisztrátor is legyen")
    args = ap.parse_args(argv)

    cfg = load_config()
    db = Database(cfg.data_dir / "shelfy.db")
    if not args.username:
        rows = db.q("SELECT username, display_name, role FROM users ORDER BY role = 'admin' DESC, username")
        if not rows:
            print("Nincs még felhasználó – nyisd meg a webes felületet az első admin létrehozásához.")
            return 0
        for r in rows:
            print(f"{r['username']:<20} {r['role']:<8} {r['display_name'] or ''}")
        return 0

    row = db.one("SELECT id, username FROM users WHERE username = ?", (args.username,))
    if not row:
        print(f"Nincs ilyen felhasználó: {args.username}", file=sys.stderr)
        return 1

    pw = os.environ.get("NEW_PASSWORD")  # nem interaktív használathoz
    if not pw:
        if not sys.stdin.isatty():
            print("Interaktív futtatás kell (docker exec -it …), vagy add meg a NEW_PASSWORD változót.", file=sys.stderr)
            return 1
        pw = getpass.getpass("Új jelszó (legalább 8 karakter): ")
        if pw != getpass.getpass("Új jelszó még egyszer: "):
            print("A két jelszó nem egyezik.", file=sys.stderr)
            return 1
    if len(pw) < 8:
        print("A jelszó legalább 8 karakter legyen.", file=sys.stderr)
        return 1

    db.run("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(pw), row["id"]))
    if args.admin:
        flags = ROLE_DEFAULTS["admin"]
        db.run("UPDATE users SET role = 'admin', can_upload = ?, can_edit = ?, can_download = ?, can_delete = ? WHERE id = ?",
               (flags["can_upload"], flags["can_edit"], flags["can_download"], flags["can_delete"], row["id"]))
    from .events import Events
    Events(db).log("password_reset", user_id=row["id"], name=row["username"], admin="1" if args.admin else None)
    print(f"Kész: {row['username']} jelszava megváltozott" + (" és adminisztrátor lett." if args.admin else ".")
          + " Minden eszközön újra be kell jelentkeznie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
