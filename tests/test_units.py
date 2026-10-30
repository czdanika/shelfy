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
