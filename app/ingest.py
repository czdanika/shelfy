"""Feltöltés és vizsgálat.

Minden feltöltött (vagy a figyelt mappába tett) fájl egy `uploads` sorba kerül, majd a háttérben
vizsgálaton megy át. A vizsgálat lépései egy jegyzőkönyvbe (inspection) kerülnek, amit a felület
megmutat. Az eredmény: kiolvasott adatok, online találatok, kategória-javaslat, duplikátum-jelzés.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import zipfile
from pathlib import Path

from .categories import suggest
from .db import Database, now
from .library import FORMATS, Library, Meta, safe_name
from .providers import Candidate, Lookup, candidates_json
from .tools import Converter, sha256_file

MAGIC = {
    "epub": lambda b: b[:2] == b"PK",
    "kepub": lambda b: b[:2] == b"PK",
    "cbz": lambda b: b[:2] == b"PK",
    "docx": lambda b: b[:2] == b"PK",
    "odt": lambda b: b[:2] == b"PK",
    "pdf": lambda b: b[:5] == b"%PDF-",
    "mobi": lambda b: b[60:68] in (b"BOOKMOBI", b"TEXtREAd"),
    "azw3": lambda b: b[60:68] == b"BOOKMOBI",
    "azw": lambda b: b[60:68] in (b"BOOKMOBI", b"TEXtREAd"),
    "prc": lambda b: b[60:68] in (b"BOOKMOBI", b"TEXtREAd"),
    "fb2": lambda b: b"<FictionBook" in b[:2048] or b[:5] == b"<?xml",
    "rtf": lambda b: b[:5] == b"{\\rtf",
    "cbr": lambda b: b[:4] == b"Rar!",
    "djvu": lambda b: b[:8] == b"AT&TFORM",
}


def check(key: str, status: str, **params) -> dict:
    """Egy vizsgálati lépés: status = ok | warn | error | info. A szöveg a locales 'insp.<key>.<status>' kulcsa."""
    return {"key": key, "status": status, "params": params}


class Ingest:
    def __init__(self, db: Database, lib: Library, converter: Converter, lookup: Lookup, upload_dir: Path,
                 online: bool = True, default_lang: str = "en", translate=None) -> None:
        self.db, self.lib, self.converter, self.lookup = db, lib, converter, lookup
        self.dir = upload_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.online = online
        self.default_lang = default_lang
        self.translate = translate or (lambda lang, key: key)
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.watcher: asyncio.Task | None = None

    # ------------------------------------------------------------------ indítás
    def start(self, ingest_dir: Path | None = None) -> None:
        loop = asyncio.get_running_loop()
        self.worker = loop.create_task(self._work())
        for r in self.db.q("SELECT id FROM uploads WHERE status IN ('pending', 'inspecting')"):
            self.queue.put_nowait(r["id"])
        if ingest_dir:
            ingest_dir.mkdir(parents=True, exist_ok=True)
            self.watcher = loop.create_task(self._watch(ingest_dir))

    async def stop(self) -> None:
        for t in (self.worker, self.watcher):
            if t:
                t.cancel()

    async def _work(self) -> None:
        while True:
            uid = await self.queue.get()
            try:
                await self.inspect(uid)
            except Exception as e:  # ne álljon le a feldolgozó egy hibás fájl miatt
                self._set(uid, status="error", message=f"{e.__class__.__name__}: {e}"[:500])

    async def _watch(self, folder: Path) -> None:
        """Figyelt mappa (almappákkal együtt): az e-könyv fájlokat, ha 10 mp-ig nem változtak, felveszi és
        áthelyezi a feldolgozásra. Más fájlokat (képek, .opf stb.) nem bánt; a kiürült almappákat törli."""
        sizes: dict[Path, int] = {}
        while True:
            try:
                await asyncio.to_thread(self._scan, folder, sizes)
            except OSError:
                pass
            await asyncio.sleep(10)

    def _scan(self, folder: Path, sizes: dict[Path, int]) -> None:
        for p in sorted(folder.rglob("*")):
            if not p.is_file() or any(part.startswith(".") for part in p.relative_to(folder).parts):
                continue
            if p.suffix.lower().lstrip(".") not in FORMATS:
                continue
            size = p.stat().st_size
            if sizes.get(p) == size and size > 0:
                sizes.pop(p, None)
                self.add_file(p, p.name, user_id=None, source="folder", move=True)
            else:
                sizes[p] = size
        for d in sorted((x for x in folder.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
            try:
                if not any(f.suffix.lower().lstrip(".") in FORMATS for f in d.rglob("*") if f.is_file()):
                    if not any(d.iterdir()):
                        d.rmdir()
            except OSError:
                pass

    # ------------------------------------------------------------------ felvétel
    def add_file(self, src: Path, filename: str, user_id: int | None, source: str = "web", move: bool = False) -> int:
        ext = Path(filename).suffix.lower().lstrip(".")
        ts = now()
        uid = self.db.run("""INSERT INTO uploads(user_id, filename, stored_path, format, size, sha256, status, source, created_at, updated_at)
                             VALUES(?, ?, '', ?, ?, '', 'pending', ?, ?, ?)""",
                          (user_id, filename, ext, src.stat().st_size, source, ts, ts))
        dst = self.dir / f"{uid}_{safe_name(filename, 120)}"
        if move:
            shutil.move(str(src), dst)
        else:
            shutil.copy2(src, dst)
        self.db.run("UPDATE uploads SET stored_path = ? WHERE id = ?", (str(dst), uid))
        self.queue.put_nowait(uid)
        return uid

    def _set(self, uid: int, **kw) -> None:
        kw["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in kw)
        self.db.run(f"UPDATE uploads SET {cols} WHERE id = ?", list(kw.values()) + [uid])

    # ------------------------------------------------------------------ vizsgálat
    async def inspect(self, uid: int, query: dict | None = None) -> None:
        up = self.db.one("SELECT * FROM uploads WHERE id = ?", (uid,))
        if not up:
            return
        self._set(uid, status="inspecting")
        path = Path(up["stored_path"])
        fmt = up["format"]
        checks: list[dict] = []

        # 1. formátum
        if fmt not in FORMATS:
            checks.append(check("format", "error", fmt=fmt.upper() or "?"))
            self._set(uid, status="error", inspection=json.dumps(checks), message="unsupported")
            return
        head = path.read_bytes()[:4096] if path.exists() else b""
        magic_ok = MAGIC.get(fmt, lambda b: True)(head)
        checks.append(check("format", "ok" if magic_ok else "warn", fmt=fmt.upper()))

        # 2. épség
        if fmt in ("epub", "kepub", "cbz", "docx", "odt"):
            try:
                with zipfile.ZipFile(path) as z:
                    bad = z.testzip()
                    names = z.namelist()
                if bad:
                    checks.append(check("integrity", "error", detail=bad))
                elif fmt == "epub" and "META-INF/container.xml" not in names:
                    checks.append(check("integrity", "warn", detail="container.xml"))
                else:
                    checks.append(check("integrity", "ok", files=len(names)))
            except zipfile.BadZipFile:
                checks.append(check("integrity", "error", detail="zip"))
        else:
            checks.append(check("integrity", "ok" if up["size"] > 0 else "error", files=0))

        # 3. ugyanaz a fájl már megvan?
        sha = await asyncio.to_thread(sha256_file, path)
        same = self.lib.find_duplicates(sha, None, None)
        checks.append(check("hash", "warn" if same else "ok", sha=sha[:12]))

        # 4. metaadat a fájlból
        meta, cover = await self.converter.read_meta(path)
        found = [k for k in ("title", "authors", "language", "publisher", "year", "isbn", "series", "description") if meta.get(k)]
        checks.append(check("meta", "ok" if meta.get("title") and meta.get("authors") else "warn",
                            fields=", ".join(found) or "–"))
        if cover:
            cover_file = self.dir / f"{uid}_cover.jpg"
            cover_file.write_bytes(cover)
            from .images import image_size
            sz = image_size(cover)
            checks.append(check("cover", "ok" if sz and sz[1] >= 600 else "warn",
                                size=f"{sz[0]}×{sz[1]}" if sz else "?"))
        else:
            checks.append(check("cover", "warn", size="–"))

        # 5. cím + szerző alapján duplikátum
        dup = same or self.lib.find_duplicates(None, meta.get("title"), meta.get("authors"))
        if dup and not same:
            checks.append(check("duplicate", "warn", n=len(dup)))
        elif not dup:
            checks.append(check("duplicate", "ok", n=0))

        # 6. online keresés
        cands: list[Candidate] = []
        if self.online and self.lookup.providers:
            q = query or {}
            title = q.get("title") or meta.get("title") or Path(up["filename"]).stem
            authors = q.get("authors") if q.get("authors") is not None else meta.get("authors") or []
            cands, status = await self.lookup.search(title, authors, q.get("isbn") or meta.get("isbn"), q.get("only"))
            best = cands[0].score if cands else 0
            checks.append(check("online", "ok" if best >= 0.8 else ("warn" if cands else "info"),
                                n=len(cands), best=f"{round(best * 100)}%",
                                sources=", ".join(f"{k}: {v}" for k, v in status.items())))
        else:
            checks.append(check("online", "info", n=0, best="–", sources="–"))

        # 7. kategória-javaslat (a fájl címkéi + a legjobb találat)
        top = cands[0] if cands and cands[0].score >= 0.6 else None
        cats = suggest((meta.get("tags") or []) + (top.tags if top else []), top.genres if top else [],
                       (top.description if top else None) or meta.get("description"))
        checks.append(check("categories", "ok" if cats else "info", list=", ".join(k for k, _ in cats) or "–"))

        extracted = dict(meta, categories=[k for k, _ in cats])
        self._set(uid, status="review", sha256=sha, extracted=json.dumps(extracted, ensure_ascii=False),
                  candidates=candidates_json(cands), inspection=json.dumps(checks, ensure_ascii=False),
                  duplicate_of=dup[0] if dup else None, message=None)

        if self.db.get_setting("auto_approve") == "1" and top and top.score >= 0.85 and not dup:
            await self.approve(uid, self.proposal(uid, 0), use_candidate_cover=True)

    # ------------------------------------------------------------------ javaslat és jóváhagyás
    def proposal(self, uid: int, cand_index: int | None) -> dict:
        """A fájl adatai + a választott online találat = a szerkeszthető javaslat."""
        up = self.db.one("SELECT extracted, candidates FROM uploads WHERE id = ?", (uid,))
        ex = json.loads(up["extracted"] or "{}")
        cands = json.loads(up["candidates"] or "[]")
        c = cands[cand_index] if cand_index is not None and 0 <= cand_index < len(cands) else None
        p = {
            "title": ex.get("title") or "",
            "authors": ex.get("authors") or [],
            "series": ex.get("series"), "series_index": ex.get("series_index"),
            "publisher": ex.get("publisher"), "year": ex.get("year"), "language": ex.get("language"),
            "isbn": ex.get("isbn"), "description": ex.get("description"), "rating": ex.get("rating"),
            "tags": [], "categories": ex.get("categories") or [], "source": None, "source_url": None, "cover_url": None,
        }
        if c:
            # az online forrás tölti ki a hiányzót; a címet/szerzőt csak akkor írja felül, ha jó az egyezés
            for k in ("year", "publisher", "isbn", "series", "series_index", "rating"):
                if c.get(k) and not p.get(k):
                    p[k] = c[k]
            if c.get("description"):
                p["description"] = c["description"]
            if c.get("score", 0) >= 0.75 or not p["title"]:
                p["title"] = c["title"] or p["title"]
                p["authors"] = c["authors"] or p["authors"]
            if not p.get("language") and c.get("language"):
                p["language"] = c["language"]
            p["tags"] = list(dict.fromkeys((c.get("genres") or []) + (c.get("tags") or [])))[:12]
            p["source"], p["source_url"], p["cover_url"] = c["source"], c["url"], c.get("cover_url")
            cats = suggest(p["tags"] + (ex.get("tags") or []), c.get("genres"), p.get("description"))
            p["categories"] = [k for k, _ in cats]
        return p

    async def approve(self, uid: int, data: dict, use_candidate_cover: bool = False, add_to: int | None = None,
                      lang: str | None = None) -> int:
        up = self.db.one("SELECT * FROM uploads WHERE id = ?", (uid,))
        path = Path(up["stored_path"])
        fmt, sha = up["format"], up["sha256"] or await asyncio.to_thread(sha256_file, path)
        if add_to:  # új formátum egy meglévő könyvhöz
            self.lib.add_file(add_to, path, fmt, sha)
            bid = add_to
        else:
            lang = lang or self.default_lang
            cat_names = [self.translate(lang, f"cat.{k}") for k in data.get("categories") or []]
            meta = Meta.from_dict(dict(data, tags=cat_names + list(data.get("tags") or [])))
            cover = None
            if use_candidate_cover and data.get("cover_url"):
                cover = await self.lookup.fetch_image(data["cover_url"])
            if not cover:
                cf = self.dir / f"{uid}_cover.jpg"
                cover = cf.read_bytes() if cf.exists() else None
            bid = self.lib.add_book(meta, [(path, fmt, sha)], cover)
        self._set(uid, status="done", book_id=bid)
        self.cleanup(uid)
        return bid

    def reject(self, uid: int) -> None:
        self._set(uid, status="rejected")
        self.cleanup(uid)

    def cleanup(self, uid: int) -> None:
        up = self.db.one("SELECT stored_path FROM uploads WHERE id = ?", (uid,))
        for p in (Path(up["stored_path"]), self.dir / f"{uid}_cover.jpg"):
            try:
                p.unlink()
            except OSError:
                pass
