"""Egységtesztek: fordítások teljessége, kategorizálás, Moly-értelmező, rendezés, név-kezelés."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import MOLY_BOOK, MOLY_SEARCH

from app.categories import suggest
from app.db import author_sort
from app.library import Meta, hu_key, sort_title
from app.providers import Candidate, parse_moly_book, parse_moly_search, score

ROOT = Path(__file__).resolve().parent.parent


def test_all_used_keys_translated():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check_locales.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_categories():
    keys = [k for k, _ in suggest(["krimi", "magyar szerző"], [], None)]
    assert keys[0] == "crime"
    keys = [k for k, _ in suggest(["regény", "történelmi regény"], ["ifjúsági"], None)]
    assert "historical" in keys and "literary" not in keys
    assert suggest([], [], None) == []


def test_moly_parsers():
    links = parse_moly_search(MOLY_SEARCH)
    assert links[0] == ("/konyvek/proba-anna-a-kek-varos", ["Próba Anna"], "A kék város")
    c = parse_moly_book(MOLY_BOOK, "https://moly.hu/konyvek/x")
    assert c.title == "A kék város" and c.authors == ["Próba Anna"]
    assert c.genres == ["krimi"] and c.tags == ["magyar szerző"]
    assert c.series == "Városok" and c.series_index == 2.0
    assert c.year == 2019 and c.rating == 4 or c.rating == 5
    assert c.cover_url == "https://img.example/kek.jpg"


def test_score():
    c = Candidate(source="x", url="", title="A kék város", authors=["Próba Anna"])
    assert score(c, "A kék város", ["Próba Anna"]) == 1.0
    assert score(c, "Teljesen más", ["Valaki"]) < 0.5


def test_names_and_sorting():
    assert author_sort("Stephen King", "eng") == "King, Stephen"
    assert author_sort("Gárdonyi Géza", "hun") == "Gárdonyi Géza"
    assert sort_title("A kék város") == "kék város"
    assert sort_title("The Shining") == "Shining"
    words = ["ő", "o", "á", "b", "a", "ö"]
    assert sorted(words, key=hu_key) == ["a", "á", "b", "o", "ö", "ő"]


def test_library_sorting(admin):
    lib = admin.app.state.lib
    for t, a, y in (("Bálna", "Zoltán Ádám", 2001), ("Alma", "Kiss Béla", 1999), ("Cékla", "Ábel Anna", 2020)):
        lib.add_book(Meta(title=t, authors=[a], year=y, language="hun"), [], None)
    titles = lambda s: [b["title"] for b in lib.list_books(sort=s)[0]]
    assert titles("title_az") == ["Alma", "Bálna", "Cékla"]
    assert titles("title_za") == ["Cékla", "Bálna", "Alma"]
    assert titles("year_old") == ["Alma", "Bálna", "Cékla"]
    assert titles("author_az") == ["Cékla", "Alma", "Bálna"]
    assert titles("added_new")[0] == "Cékla"
    html = admin.get("/books?sort=title_za").text
    assert html.index("Cékla") < html.index("Alma")
    # a választott rendezést megjegyzi
    html = admin.get("/books").text
    assert html.index("Cékla") < html.index("Alma")


def test_list_layout(admin):
    lib = admin.app.state.lib
    lib.add_book(Meta(title="Lista könyv", authors=["Szerző Egy"], year=2020, publisher="Kiadó", tags=["krimi"],
                      description="<p>Hosszú <b>leírás</b> a listához.</p>"), [], None)
    html = admin.get("/books?layout=list").text
    assert 'class="brow"' in html and "Hosszú leírás a listához." in html and "Kiadó" in html and "krimi" in html
    assert 'class="brow"' in admin.get("/books").text           # megjegyzi
    assert 'class="brow"' not in admin.get("/books?layout=grid").text


def test_theme_switch(admin):
    html = admin.get("/books").text
    assert "data-theme=" not in html.split(">")[1] and "prefers-color-scheme" in html   # automatikus
    admin.get("/theme/dark", headers={"referer": "http://folio.test/books"})
    assert '<html lang="hu" data-theme="dark">' in admin.get("/books").text
    admin.get("/theme/light")
    assert 'data-theme="light"' in admin.get("/books").text
    admin.get("/theme/auto")
    assert "prefers-color-scheme" in admin.get("/books").text


def test_help_in_all_languages(admin):
    for code, word in (("hu", "Első lépések"), ("en", "Getting started"), ("de", "Erste Schritte"), ("fr", "Premiers pas")):
        admin.get(f"/lang/{code}")
        html = admin.get("/help").text
        assert word in html and 'href="#kindle"' in html, code
    admin.get("/lang/hu")


def test_help_files_have_same_sections():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "locales" / "help"
    ids = {f.stem: re.findall(r'<h2 id="([\w-]+)"', f.read_text()) for f in root.glob("*.html")}
    assert set(ids) >= {"hu", "en", "de", "fr"}
    assert len({tuple(v) for v in ids.values()}) == 1, ids


def test_language_codes_resolve():
    from app.i18n import I18n
    i = I18n()
    assert len(i.languages) >= 14
    assert i.resolve("hu-hu") == "hu" and i.resolve("en-US") == "en" and i.resolve("de_DE") == "de"
    assert i.resolve("zh-cn") == "zh-Hans" and i.resolve("zh-hk") == "zh-Hant" and i.resolve("zh-TW") == "zh-Hant"
    assert i.resolve("xx") is None and i.resolve("") is None
    assert i.from_header("fi;q=0.9, ja-JP;q=0.8, en;q=0.5") == "ja"
    assert i.from_header("zh-HK,zh;q=0.9") == "zh-Hant"


def test_help_pages_for_every_language():
    import re
    from pathlib import Path
    from app.i18n import I18n
    root = Path(__file__).resolve().parent.parent / "locales" / "help"
    ref = re.findall(r'<h2 id="(\w+)"', (root / "en.html").read_text(encoding="utf-8"))
    for code in I18n().languages:
        assert re.findall(r'<h2 id="(\w+)"', (root / f"{code}.html").read_text(encoding="utf-8")) == ref, code


def test_library_providers_parse():
    import json
    from pathlib import Path
    from app.providers import parse_bn_pl, parse_bnf, parse_dnb, parse_ndl, default_providers, name_similarity
    d = Path(__file__).parent / "data"
    dnb = parse_dnb((d / "dnb.xml").read_text(encoding="utf-8"))
    assert any(c.title == "Der Vorleser" and c.authors == ["Bernhard Schlink"] and c.isbn == "9783257073690" for c in dnb)
    bnf = parse_bnf((d / "bnf.xml").read_text(encoding="utf-8"))
    assert any(c.authors == ["Albert Camus"] and c.publisher == "Gallimard" for c in bnf)
    ndl = parse_ndl((d / "ndl.xml").read_text(encoding="utf-8"))
    assert ndl and ndl[0].authors == ["村上春樹"] and ndl[0].cover_url
    bn = parse_bn_pl(json.loads((d / "bn.json").read_text(encoding="utf-8")))
    assert bn[0].title == "Lalka" and bn[0].authors == ["Bolesław Prus"] and bn[0].year == 1994
    assert default_providers("hu")[0] == "moly" and default_providers("de")[0] == "dnb" and default_providers("en") == ["google", "openlibrary"]
    assert name_similarity("Madách Imre", "Imre Madách") == 1.0


def test_matching_series_suffix_and_initials():
    from app.providers import Candidate, name_similarity, score
    assert name_similarity("A. A. Milne", "Alan Alexander Milne") >= 0.95
    assert name_similarity("J. K. Rowling", "Stephen King") < 0.6
    c = Candidate(source="moly", url="", title="A kutya se látta (Hercule Poirot 23.)", authors=["Agatha Christie"])
    assert score(c, "A kutya se látta", ["Agatha Christie"]) >= 0.95


def test_identifiers_and_combined_text():
    from app.providers import Candidate, parse_ident, split_combined, text_score
    assert parse_ident("963-07-1234-5") == ("9789630712347", None)
    assert parse_ident("b07cvsx2hn") == (None, "B07CVSX2HN")
    assert parse_ident("https://www.amazon.com/Martian-Andy-Weir/dp/B072LBZJF1/ref=sr_1_1") == (None, "B072LBZJF1")
    assert parse_ident("https://www.amazon.de/dp/0553418025") == ("9780553418026", None)  # papírkönyv: az ASIN = ISBN-10
    assert parse_ident("") == (None, None)
    assert split_combined("Rejtő Jenő - Piszkos Fred a kapitány") == [("Piszkos Fred a kapitány", "Rejtő Jenő"),
                                                                     ("Rejtő Jenő", "Piszkos Fred a kapitány")]
    assert split_combined("The Martian by Andy Weir") == [("The Martian", "Andy Weir")]
    assert split_combined("Egri csillagok") == []
    good = Candidate(source="openlibrary", url="a", title="Piszkos Fred a kapitány", authors=["Jenő Rejtő"])
    other = Candidate(source="openlibrary", url="b", title="Piszkos Fred és a többiek", authors=["Jenő Rejtő"])
    assert text_score(good, "rejto jeno piszkos fred a kapitany") == 1.0
    assert text_score(other, "rejto jeno piszkos fred a kapitany") < 0.8


def test_lookup_asin_and_free_text_fallback():
    import asyncio
    import json

    import httpx

    from app.providers import Lookup

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        calls.append(q)
        if q.get("q") == "id_amazon:B072LBZJF1":
            docs = [{"key": "/works/OL1W", "title": "The Martian", "author_name": ["Andy Weir"]}]
        elif q.get("q") == "Rejtő Jenő Piszkos Fred a kapitány":  # szabad szavas keresés
            docs = [{"key": "/works/OL2W", "title": "Piszkos Fred a kapitány", "author_name": ["Jenő Rejtő"]},
                    {"key": "/works/OL3W", "title": "Piszkos Fred és a többiek", "author_name": ["Jenő Rejtő"]}]
        else:  # mezőnkénti keresés: az egybeírt címre nincs jó találat
            docs = [{"key": "/works/OL9W", "title": "Valami egészen más", "author_name": ["Nem Ő"]}]
        return httpx.Response(200, text=json.dumps({"docs": docs}))

    async def go():
        lk = Lookup(("openlibrary",), transport=httpx.MockTransport(handler))
        try:
            asin, _ = await lk.search("ismeretlen", [], "B072LBZJF1")
            combined, status = await lk.search("Rejtő Jenő - Piszkos Fred a kapitány", [])
            return asin, combined, status
        finally:
            await lk.close()

    asin, combined, status = asyncio.run(go())
    assert asin[0].title == "The Martian" and asin[0].score >= 0.99
    assert any(c.get("title") == "The Martian" and c.get("author") == "Andy Weir" for c in calls)  # tovább keres a feloldott címmel
    assert combined[0].title == "Piszkos Fred a kapitány" and combined[0].score >= 0.8
    assert status == {"openlibrary": "ok"}
    assert {"title": "Piszkos Fred a kapitány", "author": "Rejtő Jenő"}.items() <= next(
        c for c in calls if c.get("author") == "Rejtő Jenő").items()  # a szétbontott változat is lefutott
