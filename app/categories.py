"""Automatikus kategorizálás: a fájl és az online források címkéiből egységes kategóriák.

A kategóriák neve fordítható (locales: "cat.<kulcs>"); a könyvhöz a fordított név kerül címkeként
a beállított alapnyelven, így a kategóriák a címkék között böngészhetők.
"""
from __future__ import annotations

import re
import unicodedata

# kulcs -> kulcsszavak (ékezet és kisbetű nélkül, szórészletként is illeszkednek)
CATEGORIES: dict[str, list[str]] = {
    "crime": ["krimi", "thriller", "detektiv", "nyomozas", "bunugy", "crime", "mystery", "detective", "suspense", "noir"],
    "fantasy": ["fantasy", "fantasztikus", "magia", "sarkany", "tunder", "urban fantasy", "epic fantasy", "varazs"],
    "scifi": ["sci-fi", "scifi", "science fiction", "sci fi", "urutazas", "disztopia", "dystopia", "cyberpunk", "space opera", "litrpg"],
    "romance": ["romantikus", "romance", "szerelmi", "love story", "szerelem", "chick lit"],
    "horror": ["horror", "rem", "kisertet", "vampir", "gothic", "gotikus", "zombi"],
    "historical": ["tortenelmi regeny", "historical fiction", "tortenelmi", "kozepkor"],
    "young_adult": ["ifjusagi", "young adult", "ya ", "tinedzser", "new adult"],
    "children": ["gyerek", "gyermek", "mese", "children", "picture book", "kepeskonyv"],
    "classic": ["klasszikus", "classic", "kotelezo olvasmany", "vilagirodalom"],
    "literary": ["szepirodalom", "literary fiction", "kortars", "magyar irodalom", "regeny", "novel", "fiction"],
    "biography": ["eletrajz", "memoar", "biography", "memoir", "autobiography", "onletrajz"],
    "nonfiction": ["ismeretterjeszto", "nonfiction", "non-fiction", "tudomany", "science", "popular science"],
    "history": ["tortenelem", "history", "haboru", "military history"],
    "selfhelp": ["onsegito", "self-help", "pszichologia", "psychology", "szemelyisegfejlesztes", "motivacio"],
    "lifestyle": ["eletmod", "egeszseg", "health", "fitness", "dieta", "lifestyle"],
    "cooking": ["szakacskonyv", "gasztronomia", "cookbook", "cooking", "recept", "food"],
    "business": ["uzlet", "business", "karrier", "penzugy", "marketing", "management", "economics"],
    "tech": ["szamitastechnika", "programozas", "computer", "programming", "informatika", "software"],
    "religion": ["vallas", "religion", "spiritualitas", "spirituality", "biblia", "ezoterika"],
    "comics": ["kepregeny", "comic", "manga", "graphic novel"],
    "poetry": ["vers", "kolteszet", "poetry", "poems"],
    "drama": ["drama", "szinmu", "play"],
    "humor": ["humor", "humoros", "comedy", "szatira"],
    "adventure": ["kaland", "adventure", "western"],
    "travel": ["utazas", "utikonyv", "travel"],
}
# Ha konkrétabb műfaj is van, az általános "szépirodalom" ne kerüljön be.
GENERIC = {"literary"}


def _norm(s: str) -> str:
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return " " + re.sub(r"[^a-z0-9\- ]+", " ", t) + " "


def suggest(tags: list[str], genres: list[str] | None = None, description: str | None = None,
            limit: int = 3) -> list[tuple[str, float]]:
    """(kategória kulcs, súly) listája, csökkenő súllyal."""
    scores: dict[str, float] = {}
    sources = [(g, 3.0) for g in genres or []] + [(t, 2.0) for t in tags or []]
    if description:
        sources.append((description[:1500], 0.5))
    for text, weight in sources:
        n = _norm(text)
        for key, words in CATEGORIES.items():
            if any(w in n for w in words):
                scores[key] = scores.get(key, 0) + weight
    # regénynél a "történelem" (ismeretterjesztő) címke félrevezető, ha történelmi regény
    if "historical" in scores:
        scores.pop("history", None)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1:
        ranked = [kv for kv in ranked if kv[0] not in GENERIC] or ranked
    return [(k, round(v, 1)) for k, v in ranked[:limit] if v >= 1.0]
