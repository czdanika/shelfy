"""Online metaadat-források: Moly.hu, Google Books, Open Library.

Mindegyik ugyanazt a Candidate szerkezetet adja vissza. A lekérdezések udvariasak: azonosítható
User-Agent, egy forrásra egyszerre egy kérés, rövid gyorsítótár.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from urllib.parse import quote_plus, urljoin

import httpx

from . import __version__
from .library import norm

UA = f"Shelfy/{__version__} (self-hosted ebook library)"


@dataclass
class Candidate:
    source: str
    url: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    publisher: str | None = None
    language: str | None = None
    isbn: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    series: str | None = None
    series_index: float | None = None
    rating: int | None = None
    cover_url: str | None = None
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def score(c: Candidate, title: str, authors: list[str]) -> float:
    s = similarity(c.title, title) * 0.65
    if authors and c.authors:
        s += max(similarity(x, y) for x in authors for y in c.authors) * 0.35
    elif not authors:
        s += 0.15
    return round(s, 3)


def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


class Provider:
    name = "base"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.http = client
        self.lock = asyncio.Semaphore(1)

    async def get(self, url: str, **kw) -> httpx.Response:
        async with self.lock:
            return await self.http.get(url, headers={"User-Agent": UA, "Accept-Language": "hu,en;q=0.8"}, **kw)

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        raise NotImplementedError


# ---------------------------------------------------------------- Moly.hu

class Moly(Provider):
    name = "moly"
    BASE = "https://moly.hu"

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        query = isbn or " ".join([title] + authors[:1])
        r = await self.get(f"{self.BASE}/kereses?query={quote_plus(query)}")
        if r.status_code != 200:
            return []
        links = parse_moly_search(r.text)
        # a legjobb 3 előzetes találat részletes adatlapja
        prelim = sorted(links, key=lambda x: -(similarity(x[2], title) + (max((similarity(a, b) for a in authors for b in x[1]), default=0) if authors else 0)))[:3]
        out: list[Candidate] = []
        for path, _a, _t in prelim:
            try:
                br = await self.get(urljoin(self.BASE, path))
            except httpx.HTTPError:
                continue
            if br.status_code == 200:
                c = parse_moly_book(br.text, urljoin(self.BASE, path))
                if c:
                    out.append(c)
        return out


def parse_moly_search(text: str) -> list[tuple[str, list[str], str]]:
    """(útvonal, szerzők, cím) a 'Könyvek' találati blokkból (img alt="Szerző - Szerző2: Cím")."""
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]*href="(/konyvek/[^"/?#]+)"[^>]*>\s*<img[^>]*alt="([^"]+)"', text):
        path, alt = m.group(1), html.unescape(m.group(2))
        if path in seen:
            continue
        seen.add(path)
        if ":" in alt:
            who, ttl = alt.split(":", 1)
            authors = [a.strip() for a in re.split(r"\s+-\s+", who) if a.strip()]
        else:
            authors, ttl = [], alt
        out.append((path, authors, ttl.strip()))
    return out


def parse_moly_book(text: str, url: str) -> Candidate | None:
    m = re.search(r'<h1[^>]*>\s*<span class="item">(.*?)</span>', text, re.S)
    if not m:
        return None
    title = strip_html(m.group(1)).replace("​", "").strip()
    am = re.search(r'<div class="authors">(.*?)</div>', text, re.S)
    authors = [strip_html(x) for x in re.findall(r"<a[^>]*>(.*?)</a>", am.group(1))] if am else []
    desc = None
    dm = re.search(r'<div class="text shrinkable">(.*?)</div>', text, re.S)
    if dm:
        desc = strip_html(dm.group(1))
    else:
        mm = re.search(r'<meta content="([^"]*)" name="description"', text)
        desc = html.unescape(mm.group(1)) if mm else None
    tags, genres = [], []
    tm = re.search(r'<div id="book_tags">(.*?)</div>', text, re.S)
    if tm:
        for cls, name in re.findall(r'<a[^>]*class="(tag[^"]*)"[^>]*>(.*?)</a>', tm.group(1)):
            name = strip_html(name)
            (genres if "genre" in cls else tags).append(name)
    cover = None
    cm = re.search(r'<meta content="([^"]+)" property="og:image"', text)
    if cm:
        cover = cm.group(1)
    year = None
    ym = re.search(r'kiadasok-boritok-szerint">(\d{4})', text)
    if ym:
        year = int(ym.group(1))
    rating = None
    rm = re.search(r'class="like_count"[^>]*>(\d+)%', text)
    if rm:
        rating = max(1, min(5, round(int(rm.group(1)) / 20)))
    series, sidx = None, None
    sm = re.search(r'class="action" href="/sorozatok/[^"]+">([^<]+)</a>\s*(\d+(?:\.\d+)?)\.?', text)
    if sm:
        series, sidx = html.unescape(sm.group(1)).strip(), float(sm.group(2))
    return Candidate(source="moly", url=url, title=title, authors=authors, year=year, description=desc,
                     tags=tags, genres=genres, cover_url=cover, rating=rating, language="hun",
                     series=series, series_index=sidx)


# ---------------------------------------------------------------- Google Books

class GoogleBooks(Provider):
    name = "google"

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        q = f"isbn:{isbn}" if isbn else f"intitle:{title}" + (f" inauthor:{authors[0]}" if authors else "")
        r = await self.get("https://www.googleapis.com/books/v1/volumes", params={"q": q, "maxResults": 5})
        if r.status_code != 200:
            return []
        return parse_google(r.json())


def parse_google(data: dict) -> list[Candidate]:
    out = []
    for it in data.get("items") or []:
        v = it.get("volumeInfo") or {}
        if not v.get("title"):
            continue
        isbn = next((x.get("identifier") for x in v.get("industryIdentifiers") or [] if x.get("type") == "ISBN_13"), None)
        year = None
        if v.get("publishedDate", "")[:4].isdigit():
            year = int(v["publishedDate"][:4])
        imgs = v.get("imageLinks") or {}
        cover = imgs.get("extraLarge") or imgs.get("large") or imgs.get("thumbnail")
        if cover:
            cover = cover.replace("http://", "https://").replace("&edge=curl", "")
        title = v["title"] + (f": {v['subtitle']}" if v.get("subtitle") else "")
        out.append(Candidate(source="google", url=v.get("infoLink") or "", title=title, authors=v.get("authors") or [],
                             year=year, publisher=v.get("publisher"), language=v.get("language"), isbn=isbn,
                             description=strip_html(v.get("description") or "") or None,
                             genres=v.get("categories") or [], cover_url=cover,
                             rating=round(v["averageRating"]) if v.get("averageRating") else None))
    return out


# ---------------------------------------------------------------- Open Library

class OpenLibrary(Provider):
    name = "openlibrary"

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        params = {"limit": 5, "fields": "key,title,author_name,first_publish_year,publisher,isbn,subject,cover_i,language"}
        if isbn:
            params["isbn"] = isbn
        else:
            params["title"] = title
            if authors:
                params["author"] = authors[0]
        r = await self.get("https://openlibrary.org/search.json", params=params)
        if r.status_code != 200:
            return []
        return parse_openlibrary(r.json())


def parse_openlibrary(data: dict) -> list[Candidate]:
    out = []
    for d in data.get("docs") or []:
        if not d.get("title"):
            continue
        cover = f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg" if d.get("cover_i") else None
        out.append(Candidate(source="openlibrary", url=f"https://openlibrary.org{d.get('key', '')}", title=d["title"],
                             authors=d.get("author_name") or [], year=d.get("first_publish_year"),
                             publisher=(d.get("publisher") or [None])[0], isbn=(d.get("isbn") or [None])[0],
                             tags=(d.get("subject") or [])[:15], cover_url=cover,
                             language=(d.get("language") or [None])[0]))
    return out


PROVIDERS = {"moly": Moly, "google": GoogleBooks, "openlibrary": OpenLibrary}


class Lookup:
    def __init__(self, names: tuple[str, ...], transport=None) -> None:
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=True, transport=transport)
        self.providers = [PROVIDERS[n](self.http) for n in names if n in PROVIDERS]
        self.cache: dict[tuple, tuple[float, list[Candidate]]] = {}

    async def close(self) -> None:
        await self.http.aclose()

    async def search(self, title: str, authors: list[str], isbn: str | None = None,
                     only: str | None = None, fresh: bool = False) -> tuple[list[Candidate], dict[str, str]]:
        """Minden forrás párhuzamosan; (pontozott találatok, forrásonkénti állapot). fresh=True: gyorsítótár nélkül."""
        key = (norm(title), tuple(norm(a) for a in authors), isbn, only)
        hit = self.cache.get(key)
        if hit and not fresh and time.time() - hit[0] < 3600:
            return hit[1], {}
        provs = [p for p in self.providers if not only or p.name == only]

        async def one(p: Provider):
            try:
                res = await p.search(title, authors, isbn)
                return p.name, res, "ok" if res else "empty"
            except Exception as e:  # hálózati hiba, változott oldal stb.
                return p.name, [], f"error: {e.__class__.__name__}"

        results = await asyncio.gather(*(one(p) for p in provs))
        cands, status = [], {}
        for name, res, st in results:
            status[name] = st
            for c in res:
                c.score = score(c, title, authors)
                if isbn and c.isbn and c.isbn.replace("-", "") == isbn:
                    c.score = max(c.score, 0.99)
                cands.append(c)
        cands.sort(key=lambda c: -c.score)
        self.cache[key] = (time.time(), cands)
        return cands, status

    async def fetch_image(self, url: str) -> bytes | None:
        try:
            r = await self.http.get(url, headers={"User-Agent": UA})
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 1000:
                return r.content
        except httpx.HTTPError:
            pass
        return None


def candidates_json(cands: list[Candidate]) -> str:
    return json.dumps([c.to_dict() for c in cands], ensure_ascii=False)
