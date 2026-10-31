"""Shelfy – saját e-könyvtár: webes felület, feltöltés vizsgálattal, admin."""
from __future__ import annotations

import asyncio
import mimetypes
import hashlib
import hmac
import shutil
import json
import math
import re
import tempfile
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __author__, __url__, __version__
from . import backup as bk
from .categories import CATEGORIES
from .auth import PERMS, ROLE_DEFAULTS, ROLES, Sessions, Throttle, User, hash_password, verify_password
from .config import Config, load_config
from .db import Database, now
from .events import GROUPS, KINDS, Events
from .tasks import Tasks
from .i18n import REFERENCE, I18n
from .images import thumbnail
from .ingest import Ingest
from .library import MIME, SORT_GROUPS, SORTS, Library, Meta, ascii_name
from .providers import PROVIDER_LANGS, PROVIDERS, Lookup, default_providers
from .tools import Converter, kindle_personal_doc, send_mail, sha256_file

BASE = Path(__file__).resolve().parent.parent
COOKIE = "shelfy"
YEAR = 60 * 60 * 24 * 365
mimetypes.add_type("application/manifest+json", ".webmanifest")
PER_PAGE = 60
PER_PAGE_CHOICES = (30, 60, 120, 240)
ZOOM_MIN, ZOOM_MAX, ZOOM_DEFAULT = 90, 260, 160  # könyvkártya szélessége px-ben
# Kindle 1/2/DX és Kindle Keyboard ("Kindle/3.0" a "+" nélkül): csak MOBI-t tölt le
OLD_KINDLE_UA = re.compile(r"Kindle/(?:[12]\.\d|3\.0(?!\+))")
KINDLE_UA = re.compile(r"Kindle|Silk/")
# bejelentkezés nélkül böngészhető oldalak (ha az admin bekapcsolta)
GUEST_PATHS = re.compile(r"/|/books|/browse/[a-z]+|/book/\d+|/book/\d+/cover|/help|/about")
PUBLIC = ("/theme/", "/login", "/setup", "/static/", "/lang", "/health")
SMTP_KEYS = ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password", "smtp_from")
FACETS = {"authors": "author", "series": "series", "tags": "tag", "publishers": "publisher",
          "languages": "lang", "formats": "fmt", "ratings": "rating"}


def create_app(cfg: Config | None = None, transport=None) -> FastAPI:
    cfg = cfg or load_config()
    for d in (cfg.data_dir, cfg.library_dir):
        d.mkdir(parents=True, exist_ok=True)
    db = Database(cfg.data_dir / "shelfy.db")
    lib = Library(db, cfg.library_dir)
    i18n = I18n(override_dir=cfg.data_dir / "locales")
    cfg.default_language = i18n.resolve(cfg.default_language) or REFERENCE  # pl. hu-hu, zh-cn is jó
    sessions = Sessions(cfg.secret_key)
    throttle = Throttle()
    converter = Converter(cfg)
    lookup = Lookup((), transport=transport)
    events = Events(db)
    tasks = Tasks(db)
    ingest = Ingest(db, lib, converter, lookup, cfg.data_dir / "uploads", events=events, tasks=tasks,
                    online=cfg.online_lookup, default_lang=cfg.default_language, translate=i18n.t)

    # ---- a felületről állítható beállítások (a környezeti változók csak a kezdőértéket adják)
    env_lang = cfg.default_language

    def provider_list() -> list[dict]:
        """Források fontossági sorrendben: [{'name', 'on', 'langs'}]."""
        saved = db.get_setting("providers")
        items = []
        try:
            items = [x for x in json.loads(saved) if x.get("name") in PROVIDERS] if saved else []
        except ValueError:
            items = []
        if not items:
            start = list(cfg.providers) if cfg.providers else default_providers(cfg.default_language)
            items = [{"name": n, "on": True} for n in start if n in PROVIDERS]
        names = {x["name"] for x in items}
        items += [{"name": n, "on": False} for n in PROVIDERS if n not in names]
        return [dict(x, langs=PROVIDER_LANGS.get(x["name"], ())) for x in items]

    def apply_settings() -> None:
        cfg.default_language = i18n.resolve(db.get_setting("default_language")) or i18n.resolve(env_lang) or REFERENCE
        ol = db.get_setting("online_lookup")
        cfg.online_lookup = (ol == "1") if ol in ("0", "1") else cfg.online_lookup
        lookup.configure([x["name"] for x in provider_list() if x["on"]],
                         google_key=sessions.unseal(db.get_setting("google_api_key")))
        ingest.online = cfg.online_lookup
        ingest.default_lang = cfg.default_language
        ingest.watch_enabled = lambda: db.get_setting("watch_folder") != "0"

    apply_settings()
    covers_dir = cfg.data_dir / "cache" / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    # első admin környezeti változóból
    if cfg.admin_user and cfg.admin_password and not db.val("SELECT COUNT(*) FROM users"):
        db.run("""INSERT INTO users(username, display_name, password_hash, role, can_upload, can_edit, can_download,
                  can_delete, created_at) VALUES(?, ?, ?, 'admin', 1, 1, 1, 1, ?)""",
               (cfg.admin_user, cfg.admin_user, hash_password(cfg.admin_password), now()))

    async def auto_backups():
        """Naponta egy adatbázis-mentés a DATA_DIR/backups mappába (kikapcsolható a beállításokban)."""
        while True:
            try:
                if db.get_setting("auto_backup", "1") == "1":
                    keep = int(db.get_setting("backup_keep") or 14)
                    dst = await asyncio.to_thread(bk.auto_backup, db, cfg.data_dir, cfg.library_dir, cfg.secret_key, keep)
                    if dst:
                        events.log("backup_auto", name=dst.name)
            except Exception as e:  # a mentés hibája ne állítsa le az appot
                events.log("backup_failed", ok=False, error=f"{e.__class__.__name__}: {e}"[:200])
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(_app):
        apply_settings()
        ingest.start(cfg.ingest_dir)
        backup_task = asyncio.create_task(auto_backups())
        yield
        backup_task.cancel()
        await ingest.stop()
        await lookup.close()

    app = FastAPI(title="Shelfy", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
    tpl = Jinja2Templates(directory=str(BASE / "templates"))
    import datetime as _dt
    from markupsafe import Markup
    from .richtext import plaintext, richtext
    tpl.env.filters["richtext"] = lambda v: Markup(richtext(v))
    tpl.env.filters["plain"] = lambda v: " ".join(plaintext(v).split())
    def filesize(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".0 ", " ")
            n /= 1024
    tpl.env.filters["filesize"] = filesize
    tpl.env.filters["num"] = lambda x: (int(x) if isinstance(x, float) and x.is_integer() else x) if x not in (None, "") else ""
    tpl.env.filters["datetime"] = lambda ts: _dt.datetime.fromtimestamp(ts).strftime("%Y.%m.%d %H:%M") if ts else "–"
    app.state.db, app.state.lib, app.state.ingest = db, lib, ingest
    app.state.tasks = tasks

    # ------------------------------------------------------------------ munkamenet, nyelv
    def session(request: Request) -> dict:
        return sessions.load(request.cookies.get(COOKIE))

    def current_user(request: Request) -> User | None:
        s = session(request)
        if not s.get("uid"):
            return None
        r = db.one("SELECT * FROM users WHERE id = ?", (s["uid"],))
        if not r:
            return None
        u = User.from_row(r)
        return u if s.get("fp") == u.fingerprint else None

    def lang_of(request: Request, user: User | None = None) -> str:
        for cand in (request.cookies.get("shelfy_lang"), user.language if user else None):
            if (c := i18n.resolve(cand)):
                return c
        if not user and (c := i18n.from_header(request.headers.get("accept-language"))):
            return c  # bejelentkezés előtt a böngésző nyelve
        return i18n.resolve(cfg.default_language) or REFERENCE

    def save(request: Request, resp: Response, data: dict) -> Response:
        resp.set_cookie(COOKIE, sessions.dump(data), max_age=YEAR, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
        return resp

    def settings_dict() -> dict:
        d = {k: db.get_setting(k) for k in SMTP_KEYS + ("auto_approve", "kindle_subject", "guest_browse")}
        d["smtp_password"] = sessions.unseal(d["smtp_password"])
        return d

    def user_mail(uid: int) -> dict:
        """A felhasználó saját SMTP beállítása (a jelszó visszafejtve)."""
        r = db.one("SELECT " + ", ".join(SMTP_KEYS) + " FROM users WHERE id = ?", (uid,))
        d = {k: r[k] for k in SMTP_KEYS} if r else {k: None for k in SMTP_KEYS}
        d["smtp_password"] = sessions.unseal(d["smtp_password"])
        return d

    def mail_settings(user: User) -> tuple[dict | None, str]:
        """A küldéshez használt beállítás: a felhasználó sajátja, különben a közös. (beállítás, 'own'|'global'|'none')"""
        own = user_mail(user.id)
        if own["smtp_host"] and own["smtp_from"]:
            return dict(own, kindle_subject=db.get_setting("kindle_subject")), "own"
        g = settings_dict()
        if g["smtp_host"] and g["smtp_from"]:
            return g, "global"
        return None, "none"

    def save_mail(form, target: str, uid: int | None = None) -> None:
        """SMTP mezők mentése a közös beállításba (target='global') vagy egy felhasználóhoz."""
        vals = {k: (form.get(k) or "").strip() or None for k in SMTP_KEYS}
        keep_pw = not form.get("smtp_password")  # üresen hagyva = marad a régi
        vals["smtp_password"] = sessions.seal(form.get("smtp_password"))
        if target == "global":
            for k, v in vals.items():
                if k == "smtp_password" and keep_pw:
                    continue
                db.set_setting(k, v)
            return
        keys = [k for k in SMTP_KEYS if not (k == "smtp_password" and keep_pw)]
        db.run(f"UPDATE users SET {', '.join(k + ' = ?' for k in keys)} WHERE id = ?", [vals[k] for k in keys] + [uid])

    def clear_mail(uid: int) -> None:
        db.run(f"UPDATE users SET {', '.join(k + ' = NULL' for k in SMTP_KEYS)} WHERE id = ?", (uid,))

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        # CSRF: POST csak a saját oldalunkról jöhet
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and urlsplit(origin).netloc != request.url.netloc:
                return Response("CSRF", status_code=403)
        if path.startswith(PUBLIC):
            return await call_next(request)
        if not db.val("SELECT COUNT(*) FROM users"):
            return RedirectResponse("/setup", status_code=303)
        # Kindle böngészőből a főoldal helyett az egyszerű Kindle-nézet (a teljes felület választható)
        if path == "/" and request.method == "GET" and KINDLE_UA.search(request.headers.get("user-agent", "")) \
                and request.cookies.get("shelfy_ui") != "full":
            return RedirectResponse("/kindle", status_code=303)
        user = current_user(request)
        if not user and request.method == "GET" and GUEST_PATHS.fullmatch(path) \
                and db.get_setting("guest_browse") == "1":
            user = User.guest()  # vendég: csak böngészés, letöltés és minden módosítás nélkül
        if not user:
            nxt = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse("/login?" + urlencode({"next": nxt if request.method == "GET" else "/"}), status_code=303)
        if path.startswith("/admin") and not user.is_admin:
            return Response("403", status_code=403)
        request.state.user = user
        return await call_next(request)

    def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        user = getattr(request.state, "user", None)
        lang = lang_of(request, user)
        def langname(code):
            k = f"langname.{(code or '')[:3]}"
            v = i18n.t(lang, k)
            return code if v == k else v

        base = {
            "langname": langname,
            "t": partial(i18n.t, lang), "lang": lang, "languages": [(c, i18n.names[c]) for c in i18n.languages],
            "user": user, "version": __version__, "path": request.url.path,
            "query": dict(request.query_params), "qs": qs, "now": now(),
            "event_text": partial(event_text, lang),
            "sources": [p.name for p in lookup.providers],
            "theme": request.cookies.get("shelfy_theme") if request.cookies.get("shelfy_theme") in ("light", "dark") else "auto",
        }
        if user and user.is_guest:
            base["nav_counts"] = lib.counts()
        elif user:
            base["shelves"] = [dict(r) for r in db.q("SELECT id, name FROM shelves WHERE user_id = ? ORDER BY name", (user.id,))]
            base["nav_counts"] = lib.counts()
            base["pending"] = db.val("SELECT COUNT(*) FROM uploads WHERE status IN ('pending','inspecting','review')")
            base["active_tasks"] = tasks.active()
        base.update(ctx)
        resp = tpl.TemplateResponse(request, name, base, status_code=status)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    class _Safe(dict):
        def __missing__(self, k):
            return "–"

    def event_text(lang: str, e: dict) -> str:
        p = dict(e["params"])
        if p.get("reason"):
            p["reason"] = i18n.t(lang, "msg." + p["reason"])
        if p.get("source") in ("moly", "google", "openlibrary"):
            p["source"] = i18n.t(lang, "source." + p["source"])
        if p.get("source") in ("web", "folder"):
            p["source"] = i18n.t(lang, "upload.src_" + p["source"])
        tmpl = i18n.t(lang, "log." + e["kind"])
        try:
            return tmpl.format_map(_Safe(p))
        except (ValueError, IndexError):
            return tmpl

    def qs(current: dict, **changes) -> str:
        d = {k: v for k, v in current.items() if v not in (None, "")}
        for k, v in changes.items():
            if v in (None, ""):
                d.pop(k, None)
            else:
                d[k] = v
        if "page" not in changes:
            d.pop("page", None)
        return "?" + urlencode(d) if d else "?"

    def tr(request: Request, key: str, **kw) -> str:
        return i18n.t(lang_of(request, getattr(request.state, "user", None)), key, **kw)

    def flash(url: str, msg: str) -> RedirectResponse:
        url, _, frag = url.partition("#")
        sep = "&" if "?" in url else "?"
        return RedirectResponse(f"{url}{sep}msg={msg}" + (f"#{frag}" if frag else ""), status_code=303)

    # ------------------------------------------------------------------ belépés, első indítás
    @app.get("/setup", response_class=HTMLResponse)
    async def setup_form(request: Request):
        if db.val("SELECT COUNT(*) FROM users"):
            return RedirectResponse("/", status_code=303)
        return render(request, "setup.html", error=None)

    @app.post("/setup")
    async def setup(request: Request, username: str = Form(...), password: str = Form(...), password2: str = Form(...)):
        if db.val("SELECT COUNT(*) FROM users"):
            return RedirectResponse("/", status_code=303)
        if len(password) < 8 or password != password2 or not username.strip():
            return render(request, "setup.html", 400, error=tr(request, "setup.error"))
        uid = db.run("""INSERT INTO users(username, display_name, password_hash, role, can_upload, can_edit, can_download,
                        can_delete, created_at) VALUES(?, ?, ?, 'admin', 1, 1, 1, 1, ?)""",
                     (username.strip(), username.strip(), hash_password(password), now()))
        u = User.from_row(db.one("SELECT * FROM users WHERE id = ?", (uid,)))
        events.log("user_created", u, name=u.username, role="admin")
        return save(request, RedirectResponse("/", status_code=303), {"uid": u.id, "fp": u.fingerprint})

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/"):
        return render(request, "login.html", next=next, error=None)

    @app.post("/login")
    async def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
        if not next.startswith("/") or next.startswith("//"):
            next = "/"
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
        if throttle.blocked(ip):
            return render(request, "login.html", 429, next=next, error=tr(request, "login.blocked"))
        r = db.one("SELECT * FROM users WHERE username = ?", (username.strip(),))
        if not r or not verify_password(password, r["password_hash"]):
            throttle.fail(ip)
            events.log("login_failed", ok=False, user_id=r["id"] if r else None, name=username.strip()[:60], ip=ip)
            await asyncio.sleep(0.5)
            return render(request, "login.html", 401, next=next, error=tr(request, "login.error"))
        throttle.reset(ip)
        u = User.from_row(r)
        db.run("UPDATE users SET last_login = ? WHERE id = ?", (now(), u.id))
        events.log("login", u, ip=ip)
        return save(request, RedirectResponse(next, status_code=303), {"uid": u.id, "fp": u.fingerprint})

    @app.get("/logout")
    async def logout(request: Request):
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    HELP_DIR = BASE / "locales" / "help"

    @app.get("/help", response_class=HTMLResponse)
    async def help_page(request: Request):
        """Használati útmutató; a szöveg nyelvenként a locales/help/<nyelv>.html fájlban (felülírható DATA_DIR/locales/help)."""
        lang = lang_of(request, request.state.user)
        html = None
        for d in (cfg.data_dir / "locales" / "help", HELP_DIR):
            for code in (lang, REFERENCE):
                f = d / f"{code}.html"
                if f.is_file():
                    html = f.read_text(encoding="utf-8")
                    break
            if html:
                break
        toc = re.findall(r'<h2 id="([\w-]+)">(.*?)</h2>', html or "")
        from markupsafe import Markup
        return render(request, "help.html", content=Markup(html or ""), toc=toc)

    @app.get("/theme/{mode}")
    async def set_theme(request: Request, mode: str):
        back = request.headers.get("referer") or "/"
        if urlsplit(back).netloc not in ("", request.url.netloc):
            back = "/"
        resp = RedirectResponse(back, status_code=303)
        if mode in ("light", "dark"):
            resp.set_cookie("shelfy_theme", mode, max_age=YEAR, samesite="lax")
        else:
            resp.delete_cookie("shelfy_theme")
        return resp

    @app.get("/lang")
    @app.get("/lang/{code}")
    async def set_lang(request: Request, code: str = ""):
        back = request.headers.get("referer") or "/"
        if urlsplit(back).netloc not in ("", request.url.netloc):
            back = "/"
        resp = RedirectResponse(back, status_code=303)
        if (code := i18n.resolve(code)):
            resp.set_cookie("shelfy_lang", code, max_age=YEAR, samesite="lax")
        return resp

    @app.get("/health")
    async def health():
        return {"ok": True, "version": __version__, "books": lib.counts()["books"], "convert": converter.has_convert}

    # ------------------------------------------------------------------ könyvtár
    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return await books(request)

    @app.get("/books", response_class=HTMLResponse)
    async def books(request: Request):
        p = request.query_params
        user: User = request.state.user
        sort = p.get("sort") if p.get("sort") in SORTS else request.cookies.get("shelfy_sort", "added_new")
        if sort not in SORTS:
            sort = "added_new"

        def num(k):
            v = p.get(k)
            return int(v) if v and v.lstrip("-").isdigit() else None

        page = max(1, num("page") or 1)
        view = p.get("view") or "all"
        per_page = num("pp") if num("pp") in PER_PAGE_CHOICES else None
        if per_page is None:
            c = request.cookies.get("shelfy_pp", "")
            per_page = int(c) if c.isdigit() and int(c) in PER_PAGE_CHOICES else PER_PAGE
        layout = p.get("layout") if p.get("layout") in ("grid", "list") else request.cookies.get("shelfy_layout", "grid")
        if layout not in ("grid", "list"):
            layout = "grid"
        zc = request.cookies.get("shelfy_zoom", "")
        zoom = min(ZOOM_MAX, max(ZOOM_MIN, int(zc))) if zc.isdigit() else ZOOM_DEFAULT
        rows, total = lib.list_books(sort=sort, q=p.get("q"), author=num("author"), series=num("series"), tag=num("tag"),
                                     publisher=num("publisher"), language=p.get("lang"), fmt=p.get("fmt"),
                                     rating=num("rating"), view=view, user_id=user.id, shelf=num("shelf"),
                                     page=page, per_page=per_page)
        heading = tr(request, f"view.{view}") if view != "all" else tr(request, "view.all")
        for key, table in (("author", "authors"), ("series", "series"), ("tag", "tags"), ("publisher", "publishers")):
            if num(key):
                heading = db.val(f"SELECT name FROM {table} WHERE id = ?", (num(key),)) or heading
        if num("shelf"):
            heading = db.val("SELECT name FROM shelves WHERE id = ? AND user_id = ?", (num("shelf"), user.id)) or heading
        if p.get("q"):
            heading = tr(request, "search.results", q=p.get("q"))
        if p.get("lang"):
            heading = tr(request, "browse.languages") + ": " + p.get("lang")
        if p.get("fmt"):
            heading = p.get("fmt").upper()
        resp = render(request, "library.html", books=rows, total=total, page=page,
                      pages=max(1, math.ceil(total / per_page)), sort=sort, sort_groups=SORT_GROUPS,
                      heading=heading, view=view, msg=p.get("msg"), zoom=zoom, zoom_min=ZOOM_MIN, zoom_max=ZOOM_MAX,
                      per_page=per_page, per_page_choices=PER_PAGE_CHOICES, layout=layout)
        if p.get("layout") in ("grid", "list"):
            resp.set_cookie("shelfy_layout", layout, max_age=YEAR, samesite="lax")
        if num("pp") in PER_PAGE_CHOICES:
            resp.set_cookie("shelfy_pp", str(per_page), max_age=YEAR, samesite="lax")
        if p.get("sort") in SORTS:
            resp.set_cookie("shelfy_sort", sort, max_age=YEAR, samesite="lax")
        return resp

    @app.get("/browse/{kind}", response_class=HTMLResponse)
    async def browse(request: Request, kind: str):
        if kind not in FACETS:
            return Response(status_code=404)
        items = lib.facet(kind)
        return render(request, "browse.html", kind=kind, items=items, param=FACETS[kind])

    @app.get("/book/{bid}", response_class=HTMLResponse)
    async def book(request: Request, bid: int):
        b = lib.book(bid)
        if not b:
            return Response(status_code=404)
        user: User = request.state.user
        state = db.val("SELECT status FROM read_state WHERE user_id = ? AND book_id = ?", (user.id, bid))
        more = []
        if b["authors"]:
            more, _ = lib.list_books(author=b["authors"][0]["id"], per_page=12)
            more = [m for m in more if m["id"] != bid][:10]
        return render(request, "book.html", b=b, read_state=state, more=more, msg=request.query_params.get("msg"),
                      can_mail=not user.is_guest and mail_settings(request.state.user)[0] is not None,
                      can_convert=converter.has_convert,
                      history=events.query(book_id=bid, per_page=15)[0] if request.state.user.can("can_edit") else [])

    @app.get("/book/{bid}/cover")
    async def cover(bid: int, w: int = 0, g: int = 0):
        p = lib.cover_path(bid) if db.val("SELECT has_cover FROM books WHERE id = ?", (bid,)) else None
        if not p or not p.exists():
            return Response(status_code=404)
        headers = {"Cache-Control": "public, max-age=86400"}
        if not w:
            return FileResponse(p, media_type="image/jpeg", headers=headers)
        w = max(60, min(w, 900))
        cached = covers_dir / f"{bid}_{w}{'g' if g else ''}_{int(p.stat().st_mtime)}.jpg"
        if not cached.exists():  # g=1: szürkeárnyalatos (e-ink kijelzőre kisebb és élesebb)
            cached.write_bytes(await asyncio.to_thread(thumbnail, p.read_bytes(), w, bool(g)))
        return FileResponse(cached, media_type="image/jpeg", headers=headers)

    def disposition(name: str) -> dict:
        return {"Content-Disposition": f'attachment; filename="{name}"'}

    # ------------------------------------------------------------------ formátumok, Kindle
    KINDLE_FORMATS = ("mobi", "azw3", "epub")
    SOURCE_ORDER = ("epub", "azw3", "mobi", "docx", "fb2", "odt", "rtf", "txt", "pdf")
    convert_locks: dict[str, asyncio.Lock] = {}

    def device_of(request: Request) -> str:
        """old = régi Kindle (csak MOBI), new = újabb Kindle (AZW3). Süti, különben a böngésző azonosítója alapján."""
        d = request.cookies.get("shelfy_device")
        if d in ("old", "new"):
            return d
        return "old" if OLD_KINDLE_UA.search(request.headers.get("user-agent", "")) else "new"

    def convert_opts(b: dict, target: str) -> list[str]:
        opts = ["--title", b["title"]]
        if target in ("mobi", "azw3"):  # csak Kindle-kimenetnél létező kapcsoló: nincs ál-ASIN → a saját borító látszik
            opts.insert(0, "--share-not-sync")
        if b["authors"]:
            opts += ["--authors", " & ".join(a["name"] for a in b["authors"])]
        if b.get("series"):
            opts += ["--series", b["series"]]
            if b.get("series_index"):
                opts += ["--series-index", str(b["series_index"])]
        if lib.cover_path(b["id"]).exists():
            opts += ["--cover", str(lib.cover_path(b["id"]))]
        if target == "mobi":  # régi Kindle: klasszikus MOBI (KF8 nélkül), 600×800-as profil
            opts += ["--output-profile", "kindle", "--mobi-file-type", "old"]
        elif target == "azw3":
            opts += ["--output-profile", "kindle_pw3"]
        return opts

    async def ensure_format(bid: int, fmt: str) -> Path | None:
        """A könyv fájlja a kért formátumban: ha nincs meg, konvertál és gyorsítótárba teszi (data/cache/convert)."""
        p = lib.file_path(bid, fmt)
        if p and p.exists():
            return p
        if fmt not in KINDLE_FORMATS or not converter.has_convert:
            return None
        b = lib.book(bid)
        src_fmt = next((f for f in SOURCE_ORDER if f in b["formats"] and f != fmt), None)
        if not src_fmt:
            return None
        out = cfg.data_dir / "cache" / "convert" / f"{bid}_{int(b['updated_at'] or 0)}.{fmt}"
        lock = convert_locks.setdefault(out.name, asyncio.Lock())
        async with lock:
            if not out.exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                for old in out.parent.glob(f"{bid}_*.{fmt}"):  # elavult (régebbi adatokkal készült) példány
                    old.unlink(missing_ok=True)
                await converter.convert(lib.file_path(bid, src_fmt), out, convert_opts(b, fmt))
                if fmt in ("mobi", "azw3"):
                    await asyncio.to_thread(kindle_personal_doc, out)
        return out

    def file_name(b: dict) -> str:
        return ascii_name(f"{b['authors'][0]['name']} - {b['title']}" if b["authors"] else b["title"])

    @app.get("/book/{bid}/download/{fmt}")
    async def download(request: Request, bid: int, fmt: str):
        user: User = request.state.user
        b = lib.book(bid)
        if not user.can("can_download"):
            return Response(status_code=403)
        if not b:
            return Response(status_code=404)
        try:
            if fmt in KINDLE_FORMATS and not (lib.file_path(bid, fmt) and lib.file_path(bid, fmt).exists()):
                with tasks.track("convert", user, f"{b['title']} → {fmt.upper()}"):
                    p = await ensure_format(bid, fmt)
            else:
                p = await ensure_format(bid, fmt)
        except RuntimeError as e:
            events.log("convert_failed", user, bid, ok=False, title=b["title"], fmt=fmt.upper(), error=str(e)[-200:])
            return flash(f"/book/{bid}", "convert_error")
        if not p:
            return Response(status_code=404)
        db.run("INSERT INTO downloads(user_id, book_id, at) VALUES(?, ?, ?)", (user.id, bid, now()))
        events.log("downloaded", user, bid, title=b["title"], fmt=fmt.upper())
        name = f"{file_name(b)}.{fmt}"
        if fmt in ("azw3", "mobi", "azw") and p.parent != cfg.data_dir / "cache" / "convert":
            tmp = Path(tempfile.mkstemp(suffix="." + fmt, dir=cfg.data_dir)[1])
            tmp.write_bytes(p.read_bytes())
            await asyncio.to_thread(kindle_personal_doc, tmp)
            from starlette.background import BackgroundTask
            return FileResponse(tmp, media_type=MIME.get(fmt, "application/octet-stream"), headers=disposition(name),
                                background=BackgroundTask(tmp.unlink, missing_ok=True))
        return FileResponse(p, media_type=MIME.get(fmt, "application/octet-stream"), headers=disposition(name))

    @app.post("/book/{bid}/convert")
    async def convert(request: Request, bid: int, target: str = Form(...)):
        """Tartós konvertálás: az új formátum a könyv fájljai közé kerül (szerkesztői jog)."""
        user: User = request.state.user
        if not user.can("can_edit") or target not in ("epub", "azw3", "mobi", "pdf") or not converter.has_convert:
            return Response(status_code=403)
        b = lib.book(bid)
        src_fmt = next((f for f in SOURCE_ORDER if f in b["formats"] and f != target), None)
        if not src_fmt:
            return flash(f"/book/{bid}", "convert_nosrc")
        dst = Path(tempfile.mkstemp(suffix="." + target, dir=cfg.data_dir)[1])
        tid = tasks.create("convert", user, subject=f"{b['title']} → {target.upper()}", status="running")
        try:
            await converter.convert(lib.file_path(bid, src_fmt), dst, convert_opts(b, target))
            tasks.finish(tid, True)
            lib.add_file(bid, dst, target, await asyncio.to_thread(sha256_file, dst))
            events.log("converted", user, bid, title=b["title"], fmt=target.upper())
        except RuntimeError as e:
            events.log("convert_failed", user, bid, ok=False, title=b["title"], fmt=target.upper(), error=str(e)[-200:])
            tasks.finish(tid, False, "exception", error=str(e)[-200:])
            return flash(f"/book/{bid}", "convert_error")
        finally:
            dst.unlink(missing_ok=True)
        return flash(f"/book/{bid}", "converted")

    async def send_to_kindle(user: User, bid: int) -> str:
        """Küldés + naplózás; az eredmény egy üzenetkulcs."""
        info: dict = {}
        b = lib.book(bid)
        tid = tasks.create("send", user, subject=b["title"] if b else None, status="running")
        res = await _send_to_kindle(user, bid, info)
        tasks.finish(tid, res == "mail_sent", "sent" if res == "mail_sent" else "failed",
                     to=user.kindle_email or "–", reason=res, error=info.get("error"))
        events.log("sent" if res == "mail_sent" else "send_failed", user, bid, ok=res == "mail_sent",
                   title=b["title"] if b else None, to=user.kindle_email, fmt=info.get("fmt"),
                   via=info.get("via"), reason=None if res == "mail_sent" else res, error=info.get("error"))
        return res

    async def _send_to_kindle(user: User, bid: int, info: dict) -> str:
        s, _src = mail_settings(user)
        info["via"] = s["smtp_from"] if s else None
        if not s:
            return "mail_off"
        if not user.kindle_email:
            return "mail_noaddr"
        b = lib.book(bid)
        # az Amazon EPUB-ot, PDF-et, DOCX-et és TXT-t fogad; MOBI/AZW3-ból EPUB készül
        fmt = next((f for f in ("epub", "pdf", "docx", "txt") if f in b["formats"]), None)
        try:
            p = lib.file_path(bid, fmt) if fmt else await ensure_format(bid, "epub")
        except RuntimeError as e:
            info["error"] = str(e)[-200:]
            return "convert_error"
        if not p:
            return "mail_nofmt"
        fmt = p.suffix.lstrip(".")
        info["fmt"] = fmt.upper()
        if p.stat().st_size > 49 * 1024 * 1024:
            return "mail_toobig"
        try:
            await asyncio.to_thread(send_mail, s, user.kindle_email, s.get("kindle_subject") or "Shelfy",
                                    f"{file_name(b)}.{fmt}", p.read_bytes(), MIME[fmt])
        except Exception as e:
            info["error"] = f"{e.__class__.__name__}: {e}"[:200]
            return "mail_error"
        db.run("INSERT INTO downloads(user_id, book_id, at) VALUES(?, ?, ?)", (user.id, bid, now()))
        return "mail_sent"

    @app.post("/book/{bid}/send")
    async def send(request: Request, bid: int):
        user: User = request.state.user
        if not user.can("can_download"):
            return Response(status_code=403)
        back = "/kindle/book/" if (request.headers.get("referer") or "").find("/kindle/") >= 0 else "/book/"
        return flash(f"{back}{bid}", await send_to_kindle(user, bid))

    @app.post("/book/{bid}/read")
    async def mark_read(request: Request, bid: int, status: str = Form(...)):
        user: User = request.state.user
        if status in ("read", "reading"):
            db.run("INSERT INTO read_state(user_id, book_id, status, updated_at) VALUES(?, ?, ?, ?) "
                   "ON CONFLICT(user_id, book_id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                   (user.id, bid, status, now()))
        else:
            db.run("DELETE FROM read_state WHERE user_id = ? AND book_id = ?", (user.id, bid))
        return RedirectResponse(f"/book/{bid}", status_code=303)

    @app.post("/book/{bid}/shelf")
    async def to_shelf(request: Request, bid: int, shelf: str = Form(""), new_shelf: str = Form("")):
        user: User = request.state.user
        sid = int(shelf) if shelf.isdigit() else None
        if new_shelf.strip():
            sid = db.run("INSERT INTO shelves(user_id, name) VALUES(?, ?)", (user.id, new_shelf.strip()))
        if sid and db.val("SELECT 1 FROM shelves WHERE id = ? AND user_id = ?", (sid, user.id)):
            db.run("INSERT OR IGNORE INTO shelf_books(shelf_id, book_id) VALUES(?, ?)", (sid, bid))
        return flash(f"/book/{bid}", "shelved")

    @app.get("/book/{bid}/edit", response_class=HTMLResponse)
    async def edit_form(request: Request, bid: int):
        if not request.state.user.can("can_edit"):
            return Response(status_code=403)
        b = lib.book(bid)
        return render(request, "edit.html", b=b, m=dict(lib.meta(bid).__dict__))

    @app.post("/book/{bid}/edit")
    async def edit(request: Request, bid: int):
        user: User = request.state.user
        if not user.can("can_edit"):
            return Response(status_code=403)
        form = await request.form()
        meta = meta_from_form(form)
        lib.update_meta(bid, meta)
        events.log("edited", user, bid, title=meta.title)
        cov = form.get("cover")
        if cov is not None and getattr(cov, "filename", ""):
            data = await cov.read()
            if data:
                lib.set_cover(bid, data)
        return flash(f"/book/{bid}", "saved")

    @app.post("/book/{bid}/delete")
    async def delete(request: Request, bid: int):
        if not request.state.user.can("can_delete"):
            return Response(status_code=403)
        b = lib.book(bid)
        lib.delete_book(bid)
        if b:
            events.log("deleted", request.state.user, bid, title=b["title"],
                       authors=", ".join(a["name"] for a in b["authors"]))
        return flash("/books", "deleted")

    # ------------------------------------------------------------------ online adatfrissítés meglévő könyvhöz
    def url_sig(url: str) -> str:
        return hmac.new(cfg.secret_key.encode(), url.encode(), hashlib.sha256).hexdigest()[:24]

    tpl.env.globals["cover_proxy"] = lambda bid, url: f"/book/{bid}/lookup/cover?" + urlencode({"url": url, "sig": url_sig(url)})

    LOOKUP_FIELDS = ("title", "authors", "series", "series_index", "publisher", "year", "language", "isbn",
                     "rating", "description", "tags")

    async def book_lookup(b: dict, p, fresh: bool):
        title = (p.get("title") or b["title"]).strip()
        author = (p.get("author") if p.get("author") is not None else (b["authors"][0]["name"] if b["authors"] else "")).strip()
        isbn = (p.get("isbn") or "").strip() or None
        only = p.get("only") or None
        cands, status = await lookup.search(title, [author] if author else [], isbn, only, fresh=fresh)
        return {"title": title, "author": author, "isbn": isbn or "", "only": only or ""}, cands, status

    @app.get("/book/{bid}/lookup", response_class=HTMLResponse)
    async def book_lookup_page(request: Request, bid: int):
        if not request.state.user.can("can_edit"):
            return Response(status_code=403)
        b = lib.book(bid)
        if not b:
            return Response(status_code=404)
        p = request.query_params
        searched = "go" in p
        q, cands, status = await book_lookup(b, p, fresh=p.get("go") == "1") if searched else (
            {"title": b["title"], "author": b["authors"][0]["name"] if b["authors"] else "", "isbn": b["isbn"] or "", "only": ""}, [], {})
        c = int(p["c"]) if p.get("c", "").lstrip("-").isdigit() else (0 if cands else -1)
        cand = cands[c].to_dict() if 0 <= c < len(cands) else None
        cur = lib.meta(bid).__dict__
        rows, cats = [], []
        if cand:
            for f in LOOKUP_FIELDS:
                new_v = cand.get(f) if f != "tags" else list(dict.fromkeys((cand.get("genres") or []) + (cand.get("tags") or [])))[:12]
                if new_v in (None, "", []):
                    continue
                old_v = cur.get(f)
                same = (old_v == new_v) or (f == "tags" and set(new_v) <= set(old_v or []))
                rows.append({"key": f, "old": old_v, "new": new_v, "same": same,
                             "checked": not same and (old_v in (None, "", []) or f == "description" and len(str(new_v)) > len(str(old_v or "")) + 40)})
            from .categories import suggest
            have = {x.lower() for x in cur.get("tags") or []}
            cats = [{"key": k, "name": i18n.t(cfg.default_language, f"cat.{k}"), "has": i18n.t(cfg.default_language, f"cat.{k}").lower() in have}
                    for k, _ in suggest((cand.get("tags") or []) + (cand.get("genres") or []), cand.get("genres"), cand.get("description"))]
        sources = ", ".join(f"{i18n.t(lang_of(request, request.state.user), 'source.' + k)}: {i18n.t(lang_of(request, request.state.user), 'insp.src_' + (v if v in ('ok', 'empty') else 'error'))}" for k, v in status.items())
        return render(request, "lookup.html", b=b, q=q, cands=[x.to_dict() for x in cands], chosen=c, cand=cand,
                      rows=rows, cats=cats, searched=searched, sources=sources)

    @app.get("/book/{bid}/lookup/cover")
    async def book_lookup_cover(request: Request, bid: int, url: str, sig: str = ""):
        """Online borító előnézete a szerveren át; csak a Shelfy által aláírt (találatból származó) cím."""
        if not request.state.user.can("can_edit") or not hmac.compare_digest(sig, url_sig(url)):
            return Response(status_code=404)
        cache = covers_dir / f"cand_{hashlib.sha1(url.encode()).hexdigest()}.jpg"
        if not cache.exists():
            data = await lookup.fetch_image(url)
            if not data:
                return Response(status_code=404)
            cache.write_bytes(thumbnail(data, 400))
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.post("/book/{bid}/lookup")
    async def book_lookup_apply(request: Request, bid: int):
        if not request.state.user.can("can_edit"):
            return Response(status_code=403)
        b = lib.book(bid)
        form = await request.form()
        _q, cands, _st = await book_lookup(b, form, fresh=False)  # ugyanaz a keresés, gyorsítótárból
        c = int(form.get("c") or -1)
        if not (0 <= c < len(cands)):
            return flash(f"/book/{bid}/lookup", "lookup_gone")
        cand = cands[c].to_dict()
        m = lib.meta(bid)
        take = set(form.getlist("take"))
        for f in LOOKUP_FIELDS:
            if f not in take:
                continue
            if f == "tags":
                new_tags = list(dict.fromkeys((cand.get("genres") or []) + (cand.get("tags") or [])))[:12]
                m.tags = list(dict.fromkeys(m.tags + new_tags))
            elif cand.get(f) not in (None, "", []):
                setattr(m, f, cand[f])
        cat_names = [i18n.t(cfg.default_language, f"cat.{k}") for k in form.getlist("categories")]
        m.tags = list(dict.fromkeys(m.tags + cat_names))
        m.source, m.source_url = cand["source"], cand["url"]
        lib.update_meta(bid, m)
        events.log("metadata_refreshed", request.state.user, bid, title=m.title, source=cand["source"],
                   fields=", ".join(sorted(take)) or "–")
        if "cover" in take and cand.get("cover_url"):
            data = await lookup.fetch_image(cand["cover_url"])
            if data:
                lib.set_cover(bid, data)
        return flash(f"/book/{bid}", "lookup_applied")

    def meta_from_form(form) -> Meta:
        split = lambda s: [x.strip() for x in (s or "").replace(";", ",").split(",") if x.strip()]
        return Meta.from_dict({
            "title": form.get("title"), "authors": split(form.get("authors")), "series": form.get("series"),
            "series_index": form.get("series_index"), "publisher": form.get("publisher"), "year": form.get("year"),
            "language": form.get("language"), "isbn": form.get("isbn"), "tags": split(form.get("tags")),
            "description": form.get("description"), "rating": form.get("rating"),
            "source": form.get("source") or None, "source_url": form.get("source_url") or None,
        })

    # ------------------------------------------------------------------ feltöltés
    @app.get("/upload", response_class=HTMLResponse)
    async def upload_page(request: Request):
        user: User = request.state.user
        if not user.can("can_upload"):
            return Response(status_code=403)
        items = [dict(r) for r in db.q("""SELECT id, filename, format, size, status, duplicate_of, message, source, updated_at
                                          FROM uploads WHERE status IN ('pending','inspecting','review','error')
                                          ORDER BY id DESC LIMIT 200""")]
        done = [dict(r) for r in db.q("""SELECT u.id, u.filename, u.status, u.book_id, b.title FROM uploads u
                                         LEFT JOIN books b ON b.id = u.book_id WHERE u.status IN ('done','rejected')
                                         ORDER BY u.updated_at DESC LIMIT 15""")]
        for it in items:
            it["extracted"] = json.loads(db.val("SELECT extracted FROM uploads WHERE id = ?", (it["id"],)) or "{}")
            it["inspection"] = json.loads(db.val("SELECT inspection FROM uploads WHERE id = ?", (it["id"],)) or "[]")
            cands = json.loads(db.val("SELECT candidates FROM uploads WHERE id = ?", (it["id"],)) or "[]")
            it["best"] = cands[0] if cands else None
        return render(request, "upload.html", items=items, done=done, msg=request.query_params.get("msg"),
                      ingest_dir=str(cfg.ingest_dir) if cfg.ingest_dir else None,
                      auto_approve=db.get_setting("auto_approve") == "1", online=cfg.online_lookup)

    @app.post("/upload")
    async def upload(request: Request, files: list[UploadFile] = File(...)):
        user: User = request.state.user
        if not user.can("can_upload"):
            return Response(status_code=403)
        n = 0
        for f in files:
            if not f.filename:
                continue
            with tempfile.NamedTemporaryFile(delete=False, dir=cfg.data_dir) as tmp:
                while chunk := await f.read(1 << 20):
                    tmp.write(chunk)
            ingest.add_file(Path(tmp.name), Path(f.filename).name, user.id, move=True)
            n += 1
        if request.headers.get("accept", "").startswith("application/json"):
            return JSONResponse({"ok": True, "count": n})
        return flash("/upload", "uploaded")

    @app.get("/upload/status.json")
    async def upload_status(request: Request):
        rows = db.q("SELECT id, status FROM uploads WHERE status IN ('pending','inspecting','review','error') ORDER BY id DESC LIMIT 200")
        return {"items": [dict(r) for r in rows]}

    def get_upload(uid: int):
        r = db.one("SELECT * FROM uploads WHERE id = ?", (uid,))
        if not r:
            return None
        d = dict(r)
        for k in ("extracted", "candidates", "inspection"):
            d[k] = json.loads(d[k] or ("{}" if k == "extracted" else "[]"))
        return d

    @app.get("/upload/{uid}", response_class=HTMLResponse)
    async def review(request: Request, uid: int, c: int | None = None):
        if not request.state.user.can("can_upload"):
            return Response(status_code=403)
        up = get_upload(uid)
        if not up:
            return Response(status_code=404)
        if c is None:
            c = 0 if up["candidates"] and up["candidates"][0]["score"] >= 0.6 else -1
        proposal = ingest.proposal(uid, c if c >= 0 else None) if up["status"] == "review" else None
        dup = lib.book(up["duplicate_of"]) if up["duplicate_of"] else None
        has_cover = (cfg.data_dir / "uploads" / f"{uid}_cover.jpg").exists()
        lang = lang_of(request, request.state.user)
        for chk in up["inspection"]:  # a jegyzőkönyv belső kulcsai helyett lefordított nevek
            prm = chk["params"]
            if chk["key"] == "categories" and up["extracted"].get("categories"):
                prm["list"] = ", ".join(i18n.t(lang, f"cat.{k}") for k in up["extracted"]["categories"])
            elif chk["key"] == "meta" and prm.get("fields") not in (None, "–"):
                prm["fields"] = ", ".join(i18n.t(lang, f"book.{f.strip()}") for f in prm["fields"].split(","))
            elif chk["key"] == "online":
                parts = []
                for item in filter(None, (prm.get("sources") or "").split(", ")):
                    name, _, st = item.partition(": ")
                    if name in ("–", "ki"):
                        continue
                    st_key = st if st in ("ok", "empty") else "error"
                    parts.append(f"{i18n.t(lang, 'source.' + name)}: {i18n.t(lang, 'insp.src_' + st_key)}")
                prm["sources"] = f" ({', '.join(parts)})" if parts else ""
        others = waiting()
        return render(request, "review.html", up=up, chosen=c, p=proposal, dup=dup, has_cover=has_cover,
                      categories=list(CATEGORIES), queue=others, msg=request.query_params.get("msg"),
                      more=sum(1 for w in others if w["id"] != uid and w["status"] != "done"))

    @app.get("/upload/{uid}/cand/{idx}/cover")
    async def candidate_cover(request: Request, uid: int, idx: int):
        """Az online találat borítója a szerveren keresztül (gyorsítótárral), így a böngésző nem kéri le közvetlenül."""
        up = get_upload(uid)
        if not up or not (0 <= idx < len(up["candidates"])) or not up["candidates"][idx].get("cover_url"):
            return Response(status_code=404)
        url = up["candidates"][idx]["cover_url"]
        cache = covers_dir / f"cand_{hashlib.sha1(url.encode()).hexdigest()}.jpg"
        if not cache.exists():
            data = await lookup.fetch_image(url)
            if not data:
                return Response(status_code=404)
            cache.write_bytes(thumbnail(data, 400))
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.get("/upload/{uid}/cover")
    async def upload_cover(uid: int):
        p = cfg.data_dir / "uploads" / f"{uid}_cover.jpg"
        return FileResponse(p, media_type="image/jpeg") if p.exists() else Response(status_code=404)

    def waiting(exclude: int | None = None) -> list[dict]:
        """Az átnézésre váró (és a még vizsgált) feltöltések, a legrégebbi elöl."""
        rows = db.q("""SELECT id, filename, status, extracted FROM uploads
                       WHERE status IN ('pending','inspecting','review') ORDER BY id""")
        out = []
        for r in rows:
            if r["id"] == exclude:
                continue
            ex = json.loads(r["extracted"] or "{}")
            out.append({"id": r["id"], "status": r["status"], "title": ex.get("title") or r["filename"]})
        return out

    def after_review(uid: int, msg: str, bid: int | None = None, mode: str | None = None) -> RedirectResponse:
        """Jóváhagyás/elvetés után: a következő átnézendő könyv, a sor, vagy a felvett könyv oldala."""
        rest = waiting(uid)
        nxt = next((w for w in rest if w["status"] == "review"), None)
        if mode == "book" and bid:
            return flash(f"/book/{bid}", msg)
        if mode == "queue":
            return flash("/upload", msg)
        if nxt:
            return flash(f"/upload/{nxt['id']}", msg)
        if rest:  # a többi még vizsgálat alatt
            return flash("/upload", msg)
        return flash(f"/book/{bid}" if bid else "/upload", msg)

    @app.post("/upload/{uid}/approve")
    async def approve(request: Request, uid: int):
        user: User = request.state.user
        if not user.can("can_upload"):
            return Response(status_code=403)
        form = await request.form()
        up = get_upload(uid)
        if not up or up["status"] != "review":
            return RedirectResponse("/upload", status_code=303)
        if form.get("action") == "add_format" and up["duplicate_of"]:
            bid = await ingest.approve(uid, {}, add_to=up["duplicate_of"], user=user)
            return flash(f"/book/{bid}", "format_added")
        meta = meta_from_form(form)
        data = {**meta.__dict__, "categories": form.getlist("categories"),
                "cover_url": form.get("cover_url") if form.get("cover_choice") == "online" else None}
        bid = await ingest.approve(uid, data, use_candidate_cover=form.get("cover_choice") == "online",
                                   lang=lang_of(request, user), user=user)
        return after_review(uid, "approved", bid, form.get("next"))

    @app.post("/upload/approve-all")
    async def approve_all(request: Request):
        user: User = request.state.user
        if not user.can("can_upload"):
            return Response(status_code=403)
        n = 0
        rows = db.q("SELECT id, candidates, duplicate_of FROM uploads WHERE status = 'review'")
        tid = tasks.create("approve_all", user, subject=str(len(rows)), status="running")
        for i, r in enumerate(rows):
            tasks.progress(tid, 100 * i / max(1, len(rows)))
            cands = json.loads(r["candidates"] or "[]")
            if r["duplicate_of"]:
                continue
            c = 0 if cands and cands[0]["score"] >= 0.75 else None
            await ingest.approve(r["id"], ingest.proposal(r["id"], c), use_candidate_cover=c is not None,
                                 lang=lang_of(request, user), user=user)
            n += 1
        tasks.finish(tid, True, "approved_n", n=n)
        return flash("/upload", "approved")

    @app.post("/upload/{uid}/reject")
    async def reject(request: Request, uid: int):
        if not request.state.user.can("can_upload"):
            return Response(status_code=403)
        ingest.reject(uid, user=request.state.user)
        return after_review(uid, "rejected", None, "next")

    @app.post("/upload/{uid}/research")
    async def research(request: Request, uid: int, title: str = Form(""), author: str = Form(""),
                       isbn: str = Form(""), only: str = Form("")):
        if not request.state.user.can("can_upload"):
            return Response(status_code=403)
        await ingest.inspect(uid, {"title": title.strip() or None, "authors": [author.strip()] if author.strip() else None,
                                   "isbn": isbn.strip() or None, "only": only or None})
        return RedirectResponse(f"/upload/{uid}", status_code=303)

    # ------------------------------------------------------------------ saját fiók
    @app.get("/me", response_class=HTMLResponse)
    async def me(request: Request):
        return render(request, "me.html", msg=request.query_params.get("msg"), error=None,
                      m=user_mail(request.state.user.id), mail_src=mail_settings(request.state.user)[1],
                      my_events=events.query(user_id=request.state.user.id, per_page=20)[0])

    @app.post("/me")
    async def me_save(request: Request, display_name: str = Form(""), kindle_email: str = Form(""), language: str = Form(""),
                      old_password: str = Form(""), new_password: str = Form("")):
        user: User = request.state.user
        db.run("UPDATE users SET display_name = ?, kindle_email = ?, language = ? WHERE id = ?",
               (display_name.strip() or user.username, kindle_email.strip() or None,
                language if i18n.has(language) else None, user.id))
        resp = flash("/me", "saved")
        if new_password:
            if len(new_password) < 8 or not verify_password(old_password, user.password_hash):
                return render(request, "me.html", 400, msg=None, error=tr(request, "me.password_error"),
                              m=user_mail(user.id), mail_src=mail_settings(user)[1])
            db.run("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user.id))
            u = User.from_row(db.one("SELECT * FROM users WHERE id = ?", (user.id,)))
            save(request, resp, {"uid": u.id, "fp": u.fingerprint})
        if i18n.has(language):
            resp.set_cookie("shelfy_lang", language, max_age=YEAR, samesite="lax")
        return resp

    @app.post("/me/mail")
    async def me_mail(request: Request):
        form = await request.form()
        uid = request.state.user.id
        if form.get("action") == "clear":
            clear_mail(uid)
            return flash("/me", "mail_cleared")
        save_mail(form, "user", uid)
        return flash("/me", "saved")

    @app.post("/me/mail/test")
    async def me_mail_test(request: Request, to: str = Form(...)):
        s, _src = mail_settings(request.state.user)
        if not s:
            return flash("/me", "mail_off")
        try:
            await asyncio.to_thread(send_mail, s, to.strip(), "Shelfy teszt", "shelfy.txt", b"Shelfy OK", "text/plain")
        except Exception as e:
            events.log("mail_test_failed", request.state.user, ok=False, to=to.strip(), error=f"{e.__class__.__name__}: {e}"[:200])
            return flash("/me", "mail_error")
        events.log("mail_test", request.state.user, to=to.strip())
        return flash("/me", "mail_sent")

    # ------------------------------------------------------------------ admin
    @app.get("/admin", response_class=HTMLResponse)
    async def admin_users(request: Request):
        users = [dict(r) for r in db.q("SELECT * FROM users ORDER BY role = 'admin' DESC, username COLLATE NOCASE")]
        return render(request, "admin_users.html", users=users, msg=request.query_params.get("msg"), section="users")

    @app.get("/admin/users/{uid}", response_class=HTMLResponse)
    async def admin_user_form(request: Request, uid: str):
        u = None if uid == "new" else db.one("SELECT * FROM users WHERE id = ?", (int(uid),))
        return render(request, "admin_user.html", u=dict(u) if u else None, roles=ROLES, perms=PERMS,
                      role_defaults=ROLE_DEFAULTS, error=None, section="users",
                      m=user_mail(u["id"]) if u else None, msg=request.query_params.get("msg"))

    @app.post("/admin/users/{uid}")
    async def admin_user_save(request: Request, uid: str):
        form = await request.form()
        username = (form.get("username") or "").strip()
        role = form.get("role") if form.get("role") in ROLES else "reader"
        flags = {p: 1 if form.get(p) else 0 for p in PERMS}
        if role == "admin":
            flags = dict(ROLE_DEFAULTS["admin"])
        pw = form.get("password") or ""
        common = (form.get("display_name") or username, role, flags["can_upload"], flags["can_edit"],
                  flags["can_download"], flags["can_delete"], (form.get("kindle_email") or "").strip() or None,
                  form.get("language") if i18n.has(form.get("language")) else None)
        if uid == "new":
            if not username or len(pw) < 8 or db.val("SELECT 1 FROM users WHERE username = ?", (username,)):
                return render(request, "admin_user.html", 400, u=None, roles=ROLES, perms=PERMS,
                              role_defaults=ROLE_DEFAULTS, error=tr(request, "admin.user_error"), section="users")
            db.run("""INSERT INTO users(username, display_name, role, can_upload, can_edit, can_download, can_delete,
                      kindle_email, language, password_hash, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (username,) + common + (hash_password(pw), now()))
            events.log("user_created", request.state.user, name=username, role=role)
        else:
            target = int(uid)
            me_ = request.state.user
            if target == me_.id and role != "admin":  # magát nem fokozhatja le az admin
                role = "admin"
                common = (common[0], "admin", 1, 1, 1, 1) + common[6:]
            db.run("""UPDATE users SET display_name = ?, role = ?, can_upload = ?, can_edit = ?, can_download = ?,
                      can_delete = ?, kindle_email = ?, language = ? WHERE id = ?""", common + (target,))
            if pw:
                if len(pw) < 8:
                    return flash(f"/admin/users/{uid}", "short_password")
                db.run("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(pw), target))
            events.log("user_updated", request.state.user,
                       name=db.val("SELECT username FROM users WHERE id = ?", (target,)), role=role,
                       password="1" if pw else None)
        return flash("/admin", "saved")

    @app.post("/admin/users/{uid}/mail")
    async def admin_user_mail(request: Request, uid: int):
        form = await request.form()
        if form.get("action") == "clear":
            clear_mail(uid)
            return flash(f"/admin/users/{uid}", "mail_cleared")
        save_mail(form, "user", uid)
        return flash(f"/admin/users/{uid}", "saved")

    @app.post("/admin/users/{uid}/delete")
    async def admin_user_delete(request: Request, uid: int):
        if uid == request.state.user.id:
            return flash("/admin", "self_delete")
        name = db.val("SELECT username FROM users WHERE id = ?", (uid,))
        db.run("DELETE FROM users WHERE id = ?", (uid,))
        events.log("user_deleted", request.state.user, name=name)
        return flash("/admin", "deleted")

    LOG_PER_PAGE = 50

    @app.get("/admin/log", response_class=HTMLResponse)
    async def admin_log(request: Request):
        p = request.query_params
        page = max(1, int(p["page"])) if p.get("page", "").isdigit() else 1
        period = p.get("period") or "30"
        since = None if period == "all" else now() - int(period) * 86400 if period.isdigit() else None
        uid = int(p["user"]) if p.get("user", "").isdigit() else None
        rows, total = events.query(group=p.get("group") or None, user_id=uid, errors=p.get("errors") == "1",
                                   since=since, q=(p.get("q") or "").strip() or None, page=page, per_page=LOG_PER_PAGE)
        existing = {r[0] for r in db.q("SELECT id FROM books")} if rows else set()
        users = [dict(r) for r in db.q("SELECT id, COALESCE(display_name, username) AS name FROM users ORDER BY name")]
        return render(request, "admin_log.html", rows=rows, total=total, page=page,
                      pages=max(1, math.ceil(total / LOG_PER_PAGE)), groups=list(GROUPS), users=users,
                      existing=existing, period=period, section="log")

    # ------------------------------------------------------------------ névjegy
    @app.get("/about", response_class=HTMLResponse)
    async def about(request: Request):
        import platform
        import sys
        return render(request, "about.html", author=__author__, url=__url__,
                      python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                      platform=f"{platform.system()} {platform.machine()}",
                      tools={"ebook-meta": converter.has_meta, "ebook-convert": converter.has_convert},
                      counts=lib.counts(), languages_n=len(i18n.languages))

    # ------------------------------------------------------------------ háttérfeladatok
    def task_text(lang: str, t: dict) -> str:
        m = dict(t.get("message") or {})
        key = m.pop("key", None)
        if not key:
            return ""
        if m.get("reason"):
            m["reason"] = i18n.t(lang, "msg." + m["reason"])
        if "size" in m:
            m["size"] = tpl.env.filters["filesize"](m["size"])
        try:
            return i18n.t(lang, "tasks.msg_" + key).format_map(_Safe(m))
        except (ValueError, IndexError):
            return key

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks_page(request: Request):
        user: User = request.state.user
        rows = tasks.list(None if user.is_admin else user.id)
        lang = lang_of(request, user)
        for r in rows:
            r["text"] = task_text(lang, r)
        return render(request, "tasks.html", rows=rows, busy=any(r["status"] in ("queued", "running") for r in rows),
                      msg=request.query_params.get("msg"))

    @app.get("/tasks.json")
    async def tasks_json(request: Request):
        user: User = request.state.user
        rows = tasks.list(None if user.is_admin else user.id, limit=50)
        return {"active": tasks.active(), "items": [{"id": r["id"], "status": r["status"], "progress": r["progress"]} for r in rows]}

    @app.post("/tasks/run/{kind}")
    async def tasks_run(request: Request, kind: str):
        user: User = request.state.user
        if not user.is_admin or kind not in ("check", "duplicates"):
            return Response(status_code=403)
        tid = tasks.create(kind, user, status="running")

        async def job():
            try:
                if kind == "check":
                    missing = await asyncio.to_thread(bk.check_library, db, cfg.library_dir)
                    tasks.finish(tid, not missing, "check_done", n=len(missing))
                else:
                    groups = await asyncio.to_thread(lib.duplicate_groups)
                    tasks.finish(tid, True, "duplicates_done", n=len(groups))
            except Exception as e:
                tasks.finish(tid, False, "exception", error=f"{e.__class__.__name__}: {e}"[:200])
        app.state.bg = asyncio.create_task(job())
        return flash("/tasks", "task_started")

    # ------------------------------------------------------------------ mentés és visszaállítás
    def backup_dir() -> Path:
        d = cfg.data_dir / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @app.get("/admin/backup", response_class=HTMLResponse)
    async def admin_backup(request: Request):
        p = request.query_params
        lib_size = await asyncio.to_thread(bk.library_size, cfg.library_dir)
        missing = await asyncio.to_thread(bk.check_library, db, cfg.library_dir) if p.get("check") else None
        result = None
        if p.get("restored"):
            result = {k: p.get(k) for k in ("books", "files", "missing", "safety")}
        return render(request, "admin_backup.html", section="backup", backups=bk.list_backups(cfg.data_dir),
                      lib_size=lib_size, db_size=(cfg.data_dir / bk.DB_NAME).stat().st_size,
                      auto=db.get_setting("auto_backup", "1") == "1", keep=int(db.get_setting("backup_keep") or 14),
                      missing=missing, result=result, msg=p.get("msg"),
                      library_dir=str(cfg.library_dir), data_dir=str(cfg.data_dir))

    @app.get("/admin/backup/download")
    async def admin_backup_download(request: Request, full: int = 0):
        name = bk.backup_name(bool(full))
        events.log("backup_created", request.state.user, name=name, full="1" if full else None)
        return StreamingResponse(bk.stream_backup(db, cfg.data_dir, cfg.library_dir, cfg.secret_key, bool(full)),
                                 media_type="application/zip", headers=disposition(name))

    @app.get("/admin/backup/file/{name}")
    async def admin_backup_file(request: Request, name: str):
        p = backup_dir() / Path(name).name
        if not p.is_file() or p.suffix != ".zip":
            return Response(status_code=404)
        return FileResponse(p, media_type="application/zip", headers=disposition(p.name))

    @app.post("/admin/backup/file/{name}/delete")
    async def admin_backup_delete(request: Request, name: str):
        (backup_dir() / Path(name).name).unlink(missing_ok=True)
        return flash("/admin/backup", "deleted")

    @app.post("/admin/backup/settings")
    async def admin_backup_settings(request: Request, keep: int = Form(14), auto: str = Form("")):
        db.set_setting("auto_backup", "1" if auto else "0")
        db.set_setting("backup_keep", str(max(1, min(365, keep))))
        return flash("/admin/backup", "saved")

    @app.post("/admin/backup/now")
    async def admin_backup_now(request: Request, full: str = Form("")):
        name = bk.backup_name(bool(full))
        user = request.state.user
        tid = tasks.create("backup", user, subject=name, status="running")

        async def job():
            try:
                await asyncio.to_thread(bk.write_backup, db, cfg.data_dir, cfg.library_dir, cfg.secret_key, bool(full),
                                        backup_dir() / name)
                tasks.finish(tid, True, "backup_done", size=(backup_dir() / name).stat().st_size)
                events.log("backup_created", user, name=name, full="1" if full else None)
            except Exception as e:
                tasks.finish(tid, False, "exception", error=f"{e.__class__.__name__}: {e}"[:200])
        app.state.bg = asyncio.create_task(job())
        return flash("/tasks", "task_started")

    @app.post("/admin/backup/restore")
    async def admin_backup_restore(request: Request, file: UploadFile = File(None), name: str = Form(""),
                                   library: str = Form(""), confirm: str = Form("")):
        user: User = request.state.user
        if not confirm:
            return flash("/admin/backup", "restore_confirm")
        tmp = None
        if name:
            src = backup_dir() / Path(name).name
        else:
            if not file or not file.filename:
                return flash("/admin/backup", "restore_nofile")
            tmp = Path(tempfile.mkstemp(suffix=".zip", dir=cfg.data_dir)[1])
            with tmp.open("wb") as f:
                while chunk := await file.read(1 << 20):
                    f.write(chunk)
            src = tmp
        try:
            res = await asyncio.to_thread(bk.restore_backup, src, db, cfg.data_dir, cfg.library_dir, sessions,
                                          cfg.secret_key, bool(library))
        except bk.RestoreError as e:
            return flash("/admin/backup", f"restore_{e}")
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
        i18n.reload()
        apply_settings()
        tasks.finish(tasks.create("restore", user, subject=name or (file.filename if file else ""), status="running"),
                     True, "restored", books=res["books"], files=res["restored_files"], missing=res["missing"])
        events.log("backup_restored", user, name=name or (file.filename if file else ""), books=res["books"],
                   files=res["restored_files"], missing=res["missing"])
        resp = RedirectResponse("/admin/backup?" + urlencode({"restored": 1, "books": res["books"], "files": res["restored_files"],
                                                               "missing": res["missing"], "safety": res["safety"]}),
                                status_code=303)
        u = db.one("SELECT * FROM users WHERE id = ?", (user.id,))
        if u:  # ha a mentésben is ugyanez a felhasználó van, bent marad
            nu = User.from_row(u)
            save(request, resp, {"uid": nu.id, "fp": nu.fingerprint})
        return resp

    @app.get("/admin/settings", response_class=HTMLResponse)
    async def admin_settings(request: Request):
        def dir_size(d: Path) -> int:
            return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0
        disk = shutil.disk_usage(cfg.library_dir)
        st = lib.stats()
        st.update(cache=dir_size(cfg.data_dir / "cache"), disk_free=disk.free, disk_total=disk.total)
        return render(request, "admin_settings.html", s=settings_dict(), converter=converter, cfg=cfg, stats=st,
                      providers=provider_list(), google_key=bool(db.get_setting("google_api_key")),
                      watch=db.get_setting("watch_folder") != "0",
                      msg=request.query_params.get("msg"), section="settings")

    @app.post("/admin/settings")
    async def admin_settings_save(request: Request):
        form = await request.form()
        save_mail(form, "global")
        db.set_setting("kindle_subject", (form.get("kindle_subject") or "").strip() or None)
        events.log("settings_saved", request.state.user)
        return flash("/admin/settings#mail", "saved")

    @app.post("/admin/settings/general")
    async def admin_settings_general(request: Request):
        form = await request.form()
        lang = i18n.resolve(form.get("default_language"))
        db.set_setting("default_language", lang)
        db.set_setting("online_lookup", "1" if form.get("online_lookup") else "0")
        db.set_setting("watch_folder", "1" if form.get("watch_folder") else "0")
        db.set_setting("auto_approve", "1" if form.get("auto_approve") else "0")
        db.set_setting("guest_browse", "1" if form.get("guest_browse") else "0")
        apply_settings()
        events.log("settings_saved", request.state.user)
        return flash("/admin/settings#general", "saved")

    @app.post("/admin/settings/sources")
    async def admin_settings_sources(request: Request):
        form = await request.form()
        order = [n for n in form.getlist("order") if n in PROVIDERS]
        order += [n for n in PROVIDERS if n not in order]
        on = set(form.getlist("on"))
        move = (form.get("move") or "").partition(":")
        if move[0] in order:  # ▲ / ▼ gomb: egy hellyel feljebb vagy lejjebb
            i = order.index(move[0])
            j = i - 1 if move[2] == "up" else i + 1
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
        if form.get("preset"):  # az alapnyelvhez illő kezdő beállítás
            start = default_providers(cfg.default_language)
            order = start + [n for n in PROVIDERS if n not in start]
            on = set(start)
        db.set_setting("providers", json.dumps([{"name": n, "on": n in on} for n in order]))
        key = (form.get("google_api_key") or "").strip()
        if form.get("google_key_clear"):
            db.set_setting("google_api_key", None)
        elif key:
            db.set_setting("google_api_key", sessions.seal(key))
        apply_settings()
        events.log("settings_saved", request.state.user)
        return flash("/admin/settings#sources", "saved")

    @app.post("/admin/settings/test-mail")
    async def admin_test_mail(request: Request, to: str = Form(...)):
        s = settings_dict()
        try:
            await asyncio.to_thread(send_mail, s, to, "Shelfy teszt", "shelfy.txt", b"Shelfy OK", "text/plain")
        except Exception as e:
            events.log("mail_test_failed", request.state.user, ok=False, to=to, error=f"{e.__class__.__name__}: {e}"[:200])
            return flash("/admin/settings", "mail_error")
        events.log("mail_test", request.state.user, to=to)
        return flash("/admin/settings", "mail_sent")

    @app.get("/admin/languages", response_class=HTMLResponse)
    async def admin_languages(request: Request):
        langs = []
        for code in i18n.languages:
            done, total = i18n.completeness(code)
            langs.append({"code": code, "name": i18n.names[code], "done": done, "total": total,
                          "pct": round(done * 100 / total) if total else 0})
        return render(request, "admin_languages.html", langs=langs, section="languages")

    @app.get("/admin/languages/{code}", response_class=HTMLResponse)
    async def admin_language(request: Request, code: str):
        if not i18n.has(code):
            return Response(status_code=404)
        ref = i18n.catalogs[REFERENCE]
        rows = [{"key": k, "ref": v, "value": i18n.catalogs[code].get(k, "")} for k, v in ref.items()]
        return render(request, "admin_language.html", code=code, lang_name=i18n.names[code], rows=rows, is_ref=code == REFERENCE, ref_name=i18n.names[REFERENCE], section="languages")


    @app.post("/admin/languages/new")
    async def admin_language_new(request: Request, code: str = Form(...), name: str = Form(...)):
        code = code.strip().lower()
        if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,4})?", code) or i18n.has(code):
            return flash("/admin/languages", "lang_error")
        i18n.save(code, {}, name.strip() or code)
        return RedirectResponse(f"/admin/languages/{code}", status_code=303)

    @app.post("/admin/languages/{code}")
    async def admin_language_save(request: Request, code: str):
        if not i18n.has(code):
            return Response(status_code=404)
        form = await request.form()
        values = {k[2:]: v.strip() for k, v in form.items() if k.startswith("k:") and isinstance(v, str)}
        if code == REFERENCE:  # a referencia nyelvből kulcs nem tűnhet el
            values = {k: v or i18n.catalogs[REFERENCE][k] for k, v in values.items()}
        i18n.save(code, values)
        return flash(f"/admin/languages/{code}", "saved")

    # ------------------------------------------------------------------ Kindle-nézet
    # E-ink barát, JavaScript nélküli felület a Kindle böngészőjéhez (a Polc utódja).
    KPER = 20

    def kindle_ctx(request: Request) -> dict:
        dev = device_of(request)
        return {"device": dev, "kfmt": "mobi" if dev == "old" else "azw3",
                "can_mail": mail_settings(request.state.user)[0] is not None,
                "can_convert": converter.has_convert, "msg": request.query_params.get("msg")}

    @app.get("/kindle", response_class=HTMLResponse)
    async def kindle_home(request: Request, q: str = "", page: int = 1, view: str = "all", sort: str = "added_new"):
        user: User = request.state.user
        page = max(1, page)
        if sort not in SORTS:
            sort = "added_new"
        if view not in ("all", "new", "reading", "unread", "read"):
            view = "all"
        rows, total = lib.list_books(sort=sort, q=q or None, view=view, user_id=user.id, page=page, per_page=KPER)
        return render(request, "kindle/list.html", books=rows, total=total, page=page,
                      pages=max(1, math.ceil(total / KPER)), q=q, view=view, sort=sort, **kindle_ctx(request))

    @app.get("/kindle/book/{bid}", response_class=HTMLResponse)
    async def kindle_book(request: Request, bid: int):
        b = lib.book(bid)
        if not b:
            return Response(status_code=404)
        return render(request, "kindle/book.html", b=b, **kindle_ctx(request))

    @app.get("/kindle/settings", response_class=HTMLResponse)
    async def kindle_settings(request: Request):
        return render(request, "kindle/settings.html", **kindle_ctx(request))

    @app.post("/kindle/settings")
    async def kindle_settings_save(request: Request, device: str = Form("new"), kindle_email: str = Form(""),
                                   ui: str = Form("kindle")):
        user: User = request.state.user
        db.run("UPDATE users SET kindle_email = ? WHERE id = ?", (kindle_email.strip() or None, user.id))
        resp = flash("/kindle" if ui == "kindle" else "/books", "saved")
        resp.set_cookie("shelfy_device", "old" if device == "old" else "new", max_age=YEAR, samesite="lax")
        resp.set_cookie("shelfy_ui", "full" if ui == "full" else "kindle", max_age=YEAR, samesite="lax")
        return resp

    return app
