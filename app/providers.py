"""Online metaadat-források: Moly.hu, Google Books, Open Library és nemzeti könyvtárak (DNB, BnF, BN, NDL).

Mindegyik ugyanazt a Candidate szerkezetet adja vissza. A lekérdezések udvariasak: azonosítható
User-Agent, egy forrásra egyszerre egy kérés, rövid gyorsítótár.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
import xml.etree.ElementTree as ET
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


def name_similarity(a: str, b: str) -> float:
    """Nevek: a sorrend nem számít ('Madách Imre' = 'Imre Madách')."""
    srt = lambda x: " ".join(sorted(norm(x).split()))
    return max(similarity(a, b), similarity(srt(a), srt(b)))


def score(c: Candidate, title: str, authors: list[str]) -> float:
    s = similarity(c.title, title) * 0.65
    if authors and c.authors:
        s += max(name_similarity(x, y) for x in authors for y in c.authors) * 0.35
    elif not authors:
        s += 0.15
    return round(s, 3)


def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


class Provider:
    name = "base"
    min_interval = 0.5  # másodperc két kérés között – nagy importnál se terheljük a forrást

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.http = client
        self.lock = asyncio.Semaphore(1)
        self.last = 0.0

    async def get(self, url: str, **kw) -> httpx.Response:
        async with self.lock:
            wait = self.last + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self.http.get(url, headers={"User-Agent": UA, "Accept-Language": "hu,en;q=0.8"}, **kw)
            finally:
                self.last = time.monotonic()

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        raise NotImplementedError


# ---------------------------------------------------------------- Moly.hu

class Moly(Provider):
    name = "moly"
    min_interval = 1.5  # a Moly.hu nyilvános oldalait olvassuk: kíméletes tempó
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
    api_key: str | None = None  # opcionális saját Google API-kulcs (a kulcs nélküli kvóta gyakran elfogy)

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        q = f"isbn:{isbn}" if isbn else f"intitle:{title}" + (f" inauthor:{authors[0]}" if authors else "")
        params = {"q": q, "maxResults": 5}
        if self.api_key:
            params["key"] = self.api_key
        r = await self.get("https://www.googleapis.com/books/v1/volumes", params=params)
        r.raise_for_status()  # 429: elfogyott a kulcs nélküli közös kvóta → a felületen látszik
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


# ---------------------------------------------------------------- nemzeti könyvtárak (MARC / Dublin Core)

def _name_order(name: str) -> str:
    """'Schlink, Bernhard' → 'Bernhard Schlink'; kelet-ázsiai nevek: '村上, 春樹' → '村上春樹'."""
    name = re.sub(r"\s*\(?\d{3,4}-?\d{0,4}\)?\.?$", "", (name or "").strip()).strip(" ,.")
    if "," not in name:
        return name
    last, first = (x.strip() for x in name.split(",", 1))
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", last):
        return last + first
    return f"{first} {last}".strip()


def _clean_title(t: str) -> str:
    t = re.sub(r"[\x88\x89\x98\x9c]", "", t or "")  # MARC rendezési jelek
    return re.sub(r"\s*[/:;=.,]\s*$", "", t).strip()


def _year(s) -> int | None:
    m = re.search(r"(1[5-9]\d\d|20\d\d)", str(s or ""))
    return int(m.group(1)) if m else None


def _isbn(s) -> str | None:
    v = re.sub(r"[^0-9Xx]", "", (str(s or "").split() or [""])[0])
    return v if len(v) in (10, 13) else None


def _marc_fields(rec, ns: str) -> dict[str, list[dict[str, list[str]]]]:
    """MARC XML rekord → {tag: [{kód: [értékek]}]}"""
    out: dict = {}
    for df in rec.iter(f"{ns}datafield"):
        subs: dict = {}
        for sf in df.iter(f"{ns}subfield"):
            subs.setdefault(sf.get("code"), []).append((sf.text or "").strip())
        out.setdefault(df.get("tag"), []).append(subs)
    for cf in rec.iter(f"{ns}controlfield"):
        out.setdefault(cf.get("tag"), []).append({"": [(cf.text or "").strip()]})
    return out


def _first(f: dict, tag: str, code: str) -> str | None:
    for d in f.get(tag, []):
        if d.get(code) and d[code][0]:
            return d[code][0]
    return None


class DNB(Provider):
    """Deutsche Nationalbibliothek – SRU, MARC21 (német nyelvű könyvek)."""
    name = "dnb"

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        q = f"num={isbn}" if isbn else f'tit="{title}"' + (f' and per="{authors[0]}"' if authors else "")
        r = await self.get("https://services.dnb.de/sru/dnb", params={
            "version": "1.1", "operation": "searchRetrieve", "query": q, "recordSchema": "MARC21-xml", "maximumRecords": 6})
        return parse_dnb(r.text) if r.status_code == 200 else []


def parse_dnb(text: str) -> list[Candidate]:
    ns = "{http://www.loc.gov/MARC21/slim}"
    out = []
    for rec in ET.fromstring(text).iter(f"{ns}record"):
        f = _marc_fields(rec, ns)
        title = _clean_title(_first(f, "245", "a") or "")
        if not title:
            continue
        idn = _first(f, "001", "")
        isbn = next((i for d in f.get("020", []) for i in [_isbn((d.get("a") or [""])[0])] if i and len(i) == 13), None) \
            or next((i for d in f.get("020", []) for i in [_isbn((d.get("a") or [""])[0])] if i), None)
        authors = [_name_order(d["a"][0]) for t in ("100", "700") for d in f.get(t, []) if d.get("a")
                   and (t == "100" or "aut" in d.get("4", []))]
        pub = _first(f, "264", "b") or _first(f, "260", "b")
        tags = list(dict.fromkeys(d["a"][0] for d in f.get("650", []) if d.get("a")))[:12]
        series, idx = _first(f, "490", "a"), _first(f, "490", "v")
        out.append(Candidate(source="dnb", url=f"https://d-nb.info/{idn}" if idn else "", title=title, authors=authors,
                             year=_year(_first(f, "264", "c") or _first(f, "260", "c")), publisher=pub,
                             language=_first(f, "041", "a"), isbn=isbn, tags=tags,
                             series=series, series_index=float(idx) if idx and re.fullmatch(r"\d+(\.\d+)?", idx) else None,
                             cover_url=f"https://portal.dnb.de/opac/mvb/cover?isbn={isbn}" if isbn else None))
    return out


class BnF(Provider):
    """Bibliothèque nationale de France – SRU, UNIMARC (francia nyelvű könyvek)."""
    name = "bnf"

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        q = f'bib.isbn all "{isbn}"' if isbn else f'bib.title all "{title}"' + (f' and bib.author all "{authors[0]}"' if authors else "")
        r = await self.get("https://catalogue.bnf.fr/api/SRU", params={
            "version": "1.2", "operation": "searchRetrieve", "query": q + ' and bib.doctype any "a"',
            "recordSchema": "unimarcXchange", "maximumRecords": 6})
        return parse_bnf(r.text) if r.status_code == 200 else []


def parse_bnf(text: str) -> list[Candidate]:
    out = []
    for rec in ET.fromstring(text).iter():
        if not rec.tag.endswith("}record") or "zing" in rec.tag:
            continue
        ns = rec.tag[:-len("record")]
        f = _marc_fields(rec, ns)
        title = _clean_title(_first(f, "200", "a") or "")
        if not title:
            continue
        authors = []
        for t in ("700", "701"):
            for d in f.get(t, []):
                if d.get("a"):
                    authors.append(f"{(d.get('b') or [''])[0]} {d['a'][0]}".strip())
        if not authors and _first(f, "200", "f"):
            authors = [a.strip() for a in re.split(r"[;,]| et ", _first(f, "200", "f")) if a.strip()][:3]
        ark = rec.get("id") or ""
        out.append(Candidate(source="bnf", url=f"https://catalogue.bnf.fr/{ark}" if ark else "", title=title, authors=authors,
                             year=_year(_first(f, "214", "d") or _first(f, "210", "d")),
                             publisher=_first(f, "214", "c") or _first(f, "210", "c"), language=_first(f, "101", "a"),
                             isbn=_isbn(_first(f, "010", "a")), description=_first(f, "330", "a"),
                             tags=list(dict.fromkeys(d["a"][0] for d in f.get("606", []) if d.get("a")))[:12],
                             series=_first(f, "225", "a") if not (_first(f, "225", "a") or "").isdigit() else None))
    return out


class BNPolska(Provider):
    """Biblioteka Narodowa (Lengyelország) – data.bn.org.pl JSON (lengyel nyelvű könyvek)."""
    name = "bn_pl"

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        params = {"limit": 6, "kind": "książka"}
        if isbn:
            params["isbnIssn"] = isbn
        else:
            params["title"] = title
            if authors:
                params["author"] = authors[0]
        r = await self.get("https://data.bn.org.pl/api/institutions/bibs.json", params=params)
        return parse_bn_pl(r.json()) if r.status_code == 200 else []


def parse_bn_pl(data: dict) -> list[Candidate]:
    out = []
    for b in data.get("bibs") or []:
        f: dict = {}
        for fld in (b.get("marc") or {}).get("fields") or []:
            for tag, v in fld.items():
                if isinstance(v, dict):
                    subs: dict = {}
                    for sf in v.get("subfields") or []:
                        for k, x in sf.items():
                            subs.setdefault(k, []).append(x)
                    f.setdefault(tag, []).append(subs)
        title = _clean_title(_first(f, "245", "a") or b.get("title") or "")
        if not title:
            continue
        authors = [_name_order(d["a"][0]) for d in f.get("100", []) if d.get("a")]
        pub = (_first(f, "264", "b") or _first(f, "260", "b") or "").strip(" ,:;") or None
        tags = list(dict.fromkeys(d["a"][0].strip(" .") for t in ("650", "655") for d in f.get(t, []) if d.get("a")))[:12]
        out.append(Candidate(source="bn_pl", url=f"https://katalogi.bn.org.pl/discovery/search?query=any,contains,{b.get('isbnIssn') or quote_plus(title)}&vid=48OMNIS_NLOP:48OMNIS_NLOP",
                             title=title, authors=authors, year=_year(b.get("publicationYear")), publisher=pub,
                             language="pol" if b.get("language") == "polski" else None, isbn=_isbn(b.get("isbnIssn")),
                             tags=tags, genres=[b["genre"]] if b.get("genre") else []))
    return out


class NDL(Provider):
    """National Diet Library (Japán) – OpenSearch (japán nyelvű könyvek)."""
    name = "ndl"

    async def search(self, title: str, authors: list[str], isbn: str | None = None) -> list[Candidate]:
        params = {"cnt": 6, "mediatype": "books"}
        if isbn:
            params["isbn"] = isbn
        else:
            params["title"] = title
            if authors:
                params["creator"] = authors[0]
        r = await self.get("https://ndlsearch.ndl.go.jp/api/opensearch", params=params)
        return parse_ndl(r.text) if r.status_code == 200 else []


def parse_ndl(text: str) -> list[Candidate]:
    dc, dcndl = "{http://purl.org/dc/elements/1.1/}", "{http://ndl.go.jp/dcndl/terms/}"
    xsi = "{http://www.w3.org/2001/XMLSchema-instance}type"
    out = []
    for it in ET.fromstring(text).iter("item"):
        title = (it.findtext(f"{dc}title") or it.findtext("title") or "").strip()
        if not title:
            continue
        isbn = None
        for idf in it.findall(f"{dc}identifier"):
            if (idf.get(xsi) or "").startswith("dcndl:ISBN"):
                isbn = _isbn(idf.text) or isbn
        series = it.findtext(f"{dcndl}seriesTitle")
        idx = None
        if series and ";" in series:
            series, _, num = series.partition(";")
            idx = float(num.strip()) if re.fullmatch(r"\d+", num.strip()) else None
        out.append(Candidate(source="ndl", url=it.findtext("link") or "", title=title,
                             authors=[_name_order(c.text) for c in it.findall(f"{dc}creator") if c.text][:3],
                             year=_year(it.findtext(f"{dc}date") or it.findtext("{http://purl.org/dc/terms/}issued")),
                             publisher=it.findtext(f"{dc}publisher"), isbn=isbn, series=(series or "").strip() or None,
                             series_index=idx,
                             cover_url=f"https://ndlsearch.ndl.go.jp/thumbnail/{isbn}.jpg" if isbn else None))
    return out


PROVIDERS = {"moly": Moly, "google": GoogleBooks, "openlibrary": OpenLibrary,
             "dnb": DNB, "bnf": BnF, "bn_pl": BNPolska, "ndl": NDL}
# melyik forrás milyen nyelvű könyvekhez jó (a felületen és az alapértelmezett választásnál)
PROVIDER_LANGS = {"moly": ("hu",), "google": (), "openlibrary": (), "dnb": ("de",), "bnf": ("fr",), "bn_pl": ("pl",), "ndl": ("ja",)}


def default_providers(lang: str) -> list[str]:
    """Az alapnyelvhez illő kezdő forráslista: a nyelv saját forrása elöl, utána a nemzetközi források."""
    base = (lang or "en").split("-")[0].lower()
    own = [n for n, ls in PROVIDER_LANGS.items() if base in ls]
    return own + ["google", "openlibrary"]


PRIORITY_STEP = 0.03  # sorrendbeli helyenként ennyi előnyt kap egy forrás a rangsorolásnál (a kijelzett % nem változik)


class Lookup:
    def __init__(self, names: tuple[str, ...], transport=None) -> None:
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=True, transport=transport)
        self.cache: dict[tuple, tuple[float, list[Candidate]]] = {}
        self.throttle = transport is None  # tesztben (saját transport) nincs várakozás
        self.configure(names)

    def configure(self, names, google_key: str | None = None) -> None:
        """A bekapcsolt források, fontossági sorrendben (az első a legfontosabb)."""
        self.providers = [PROVIDERS[n](self.http) for n in names if n in PROVIDERS]
        for p in self.providers:
            if not self.throttle:
                p.min_interval = 0
            if isinstance(p, GoogleBooks):
                p.api_key = google_key or None
        self.cache.clear()

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
            except httpx.HTTPStatusError as e:
                return p.name, [], f"error: HTTP {e.response.status_code}"
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
        rank = {p.name: i for i, p in enumerate(self.providers)}
        n = len(self.providers)
        # a pontszám dönt; közel egyforma találatoknál a fontosabb forrás kerül előre
        cands.sort(key=lambda c: -(c.score + PRIORITY_STEP * (n - 1 - rank.get(c.source, n - 1))))
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
