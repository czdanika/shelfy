"""Mentés és visszaállítás.

A Shelfy két részből áll:
  * az adatbázis (shelfy.db): könyvadatok, felhasználók, polcok, olvasási állapot, napló, beállítások – kicsi;
  * a könyvtár mappa (LIBRARY_DIR): a könyvfájlok és borítók Szerző/Cím (id)/ szerkezetben – ez a nagy rész.

A mentés egy ZIP fájl:
  manifest.json          – mi van benne (verzió, dátum, könyvek száma, teljes-e)
  shelfy.db              – az adatbázis konzisztens pillanatképe
  secret.key             – a titkosítás kulcsa (az SMTP-jelszavak visszafejtéséhez visszaállításkor)
  locales/…              – a felületen szerkesztett fordítások
  library/…              – csak teljes mentésnél: az összes könyvfájl és borító

Parancssorból (nagy könyvtárakhoz):
  python -m app.backup create [--full] [-o mentes.zip]
  python -m app.backup restore mentes.zip      (a Shelfy leállítása után)
  python -m app.backup check                   (hiányzó könyvfájlok keresése)
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__

DB_NAME = "shelfy.db"
STORED = {".epub", ".mobi", ".azw", ".azw3", ".pdf", ".cbz", ".cbr", ".jpg", ".jpeg", ".png", ".docx", ".kepub", ".djvu", ".zip"}


class _Sink:
    """Csak hozzáfűzhető kimenet a ZIP-író számára; a darabokat folyamatosan ki lehet venni (streameléshez)."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.pos = 0

    def write(self, b) -> int:
        self.parts.append(bytes(b))
        self.pos += len(b)
        return len(b)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = b"".join(self.parts)
        self.parts.clear()
        return out


def library_files(library_dir: Path):
    for p in sorted(library_dir.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            yield p


def library_size(library_dir: Path) -> int:
    return sum(p.stat().st_size for p in library_files(library_dir))


def _manifest(db_file: Path, full: bool, library_dir: Path) -> dict:
    con = sqlite3.connect(str(db_file))
    try:
        books = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        con.close()
    return {"app": "shelfy", "version": __version__, "created": dt.datetime.now().isoformat(timespec="seconds"),
            "full": full, "books": books, "files": files, "users": users,
            "library_bytes": library_size(library_dir) if full else None}


def stream_backup(db, data_dir: Path, library_dir: Path, secret: str, full: bool):
    """Generátor: a mentés ZIP bájtjai darabonként (teljes mentésnél sem kell ideiglenes fájl)."""
    with tempfile.TemporaryDirectory(dir=data_dir) as tmp:
        snap = Path(tmp) / DB_NAME
        db.snapshot(snap)
        sink = _Sink()
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr("manifest.json", json.dumps(_manifest(snap, full, library_dir), ensure_ascii=False, indent=1))
            z.write(snap, DB_NAME)
            z.writestr("secret.key", secret)
            yield sink.take()
            loc = data_dir / "locales"
            if loc.is_dir():
                for p in sorted(loc.rglob("*")):
                    if p.is_file():
                        z.write(p, "locales/" + p.relative_to(loc).as_posix())
            yield sink.take()
            if full:
                for p in library_files(library_dir):
                    comp = zipfile.ZIP_STORED if p.suffix.lower() in STORED else zipfile.ZIP_DEFLATED
                    z.write(p, "library/" + p.relative_to(library_dir).as_posix(), compress_type=comp)
                    yield sink.take()
        yield sink.take()


def write_backup(db, data_dir: Path, library_dir: Path, secret: str, full: bool, dst: Path) -> Path:
    tmp = dst.with_suffix(dst.suffix + ".part")
    with tmp.open("wb") as f:
        for chunk in stream_backup(db, data_dir, library_dir, secret, full):
            f.write(chunk)
    tmp.replace(dst)
    return dst


def backup_name(full: bool, prefix: str = "shelfy") -> str:
    return f"{prefix}-{'full' if full else 'db'}-{dt.datetime.now():%Y%m%d-%H%M}.zip"


# ---------------------------------------------------------------- automatikus napi mentés

def auto_backup(db, data_dir: Path, library_dir: Path, secret: str, keep: int = 14) -> Path | None:
    """Naponta egy adatbázis-mentés a DATA_DIR/backups mappába; a legrégebbieket törli."""
    folder = data_dir / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    today = f"auto-db-{dt.date.today():%Y%m%d}"
    dst = None
    if not any(p.name.startswith(today) for p in folder.glob("auto-db-*.zip")):
        dst = write_backup(db, data_dir, library_dir, secret, False, folder / f"{today}-{dt.datetime.now():%H%M}.zip")
    autos = sorted(folder.glob("auto-db-*.zip"))
    for old in autos[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return dst


def list_backups(data_dir: Path) -> list[dict]:
    folder = data_dir / "backups"
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "at": st.st_mtime})
    return out


# ---------------------------------------------------------------- visszaállítás

class RestoreError(Exception):
    """Hibakód a felületnek (locales: msg.restore_<kód>)."""


def inspect_backup(path: Path) -> dict:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise RestoreError("notzip")
    with z:
        names = set(z.namelist())
        if "manifest.json" not in names or DB_NAME not in names:
            raise RestoreError("notshelfy")
        m = json.loads(z.read("manifest.json"))
        if m.get("app") != "shelfy":
            raise RestoreError("notshelfy")
        m["has_library"] = any(n.startswith("library/") for n in names)
        return m


def _safe_target(root: Path, name: str) -> Path | None:
    """ZIP-ből kibontandó út a célmappán belül (zip-slip ellen)."""
    rel = PurePosixPath(name)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    target = (root / Path(*rel.parts)).resolve()
    return target if str(target).startswith(str(root.resolve())) else None


def restore_backup(path: Path, db, data_dir: Path, library_dir: Path, sessions, secret: str,
                   restore_library: bool = True) -> dict:
    """Visszaállítás: előtte biztonsági mentés a mostani adatbázisról, majd csere.
    Az SMTP-jelszavakat a mentés kulcsával visszafejti és a mostani kulccsal újra titkosítja."""
    info = inspect_backup(path)
    (data_dir / "backups").mkdir(parents=True, exist_ok=True)
    safety = write_backup(db, data_dir, library_dir, secret, False,
                          data_dir / "backups" / f"before-restore-{dt.datetime.now():%Y%m%d-%H%M%S}.zip")
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory(dir=data_dir) as tmp:
        newdb = Path(tmp) / DB_NAME
        newdb.write_bytes(z.read(DB_NAME))
        con = sqlite3.connect(str(newdb))
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreError("corrupt")
            old_secret = z.read("secret.key").decode().strip() if "secret.key" in z.namelist() else None
            if old_secret and old_secret != secret:
                from .auth import Sessions
                old = Sessions(old_secret)
                rows = con.execute("SELECT key, value FROM settings WHERE key = 'smtp_password'").fetchall()
                rows_u = con.execute("SELECT id, smtp_password FROM users WHERE smtp_password IS NOT NULL").fetchall() \
                    if "smtp_password" in [c[1] for c in con.execute("PRAGMA table_info(users)")] else []
                for k, v in rows:
                    con.execute("UPDATE settings SET value = ? WHERE key = ?", (sessions.seal(old.unseal(v)), k))
                for uid, v in rows_u:
                    con.execute("UPDATE users SET smtp_password = ? WHERE id = ?", (sessions.seal(old.unseal(v)), uid))
                con.commit()
        finally:
            con.close()
        # fordítások
        for n in z.namelist():
            if n.startswith("locales/") and not n.endswith("/"):
                t = _safe_target(data_dir / "locales", n[len("locales/"):])
                if t:
                    t.parent.mkdir(parents=True, exist_ok=True)
                    t.write_bytes(z.read(n))
        # könyvfájlok
        restored_files = 0
        if restore_library and info["has_library"]:
            library_dir.mkdir(parents=True, exist_ok=True)
            for zi in z.infolist():
                if not zi.filename.startswith("library/") or zi.is_dir():
                    continue
                t = _safe_target(library_dir, zi.filename[len("library/"):])
                if not t:
                    continue
                t.parent.mkdir(parents=True, exist_ok=True)
                with z.open(zi) as src, t.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                restored_files += 1
        db.replace_with(newdb)
    info.update(safety=safety.name, restored_files=restored_files, missing=len(check_library(db, library_dir)))
    return info


def check_library(db, library_dir: Path) -> list[dict]:
    """Azok a könyvfájlok, amelyek az adatbázisban szerepelnek, de a könyvtár mappában nincsenek meg."""
    missing = []
    for r in db.q("SELECT b.id, b.title, b.path, f.format, f.filename FROM files f JOIN books b ON b.id = f.book_id"):
        if not (library_dir / r["path"] / r["filename"]).is_file():
            missing.append({"id": r["id"], "title": r["title"], "format": r["format"], "file": f"{r['path']}/{r['filename']}"})
    return missing


# ---------------------------------------------------------------- parancssor

def main(argv: list[str] | None = None) -> int:
    import argparse

    from .auth import Sessions
    from .config import load_config
    from .db import Database

    ap = argparse.ArgumentParser(prog="python -m app.backup", description="Shelfy backup / restore")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="mentés készítése")
    c.add_argument("--full", action="store_true", help="a könyvfájlokkal együtt")
    c.add_argument("-o", "--output", help="a ZIP fájl helye (alapból DATA_DIR/backups)")
    r = sub.add_parser("restore", help="visszaállítás egy mentésből (a Shelfy leállítása után)")
    r.add_argument("file")
    r.add_argument("--no-library", action="store_true", help="a könyvfájlokat ne bontsa ki, csak az adatbázist")
    sub.add_parser("check", help="hiányzó könyvfájlok keresése")
    args = ap.parse_args(argv)

    cfg = load_config()
    db = Database(cfg.data_dir / DB_NAME)
    if args.cmd == "create":
        dst = Path(args.output) if args.output else cfg.data_dir / "backups" / backup_name(args.full)
        dst.parent.mkdir(parents=True, exist_ok=True)
        t = time.time()
        write_backup(db, cfg.data_dir, cfg.library_dir, cfg.secret_key, args.full, dst)
        print(f"Kész / done: {dst} ({dst.stat().st_size / 1048576:.1f} MB, {time.time() - t:.0f} s)")
    elif args.cmd == "restore":
        try:
            res = restore_backup(Path(args.file), db, cfg.data_dir, cfg.library_dir, Sessions(cfg.secret_key),
                                 cfg.secret_key, restore_library=not args.no_library)
        except RestoreError as e:
            print(f"Hiba / error: {e}")
            return 1
        print(f"Visszaállítva / restored: {res['books']} könyv, {res['restored_files']} fájl kibontva; "
              f"hiányzó fájl / missing files: {res['missing']}; biztonsági mentés / safety backup: {res['safety']}")
    else:
        missing = check_library(db, cfg.library_dir)
        for m in missing:
            print(f"#{m['id']} {m['title']} ({m['format']}): {m['file']}")
        print(f"{len(missing)} hiányzó fájl / missing files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
