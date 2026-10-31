"""Fordítások: minden felirat a locales/<nyelv>.json fájlokban van, kulcs → szöveg formában.

A kulcsok pontokkal tagolhatók ("nav.library"); a JSON lehet lapos vagy egymásba ágyazott.
Hiányzó fordításnál a referencia nyelvre (magyar), végül magára a kulcsra esik vissza.
Változók: {name} formában, pl. t("upload.count", n=3).
"""
from __future__ import annotations

import json
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
REFERENCE = "en"  # referencia nyelv: ebből fordítunk, és erre esik vissza a hiányzó fordítás

# Más rendszerek nyelvkódjai (böngésző, TerraMaster TOS: en-us, zh-cn, zh-hk…) → a Shelfy kódjai
ALIASES = {
    "zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-sg": "zh-Hans", "zh-hans": "zh-Hans",
    "zh-hk": "zh-Hant", "zh-tw": "zh-Hant", "zh-mo": "zh-Hant", "zh-hant": "zh-Hant",
    "pt-br": "pt", "nb": "no", "nn": "no",
}


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = str(v)
    return out


class I18n:
    """A beépített fordítások (locales/) mellett a felületen szerkesztett változatok egy külön mappába
    (DATA_DIR/locales) kerülnek, így képfrissítéskor sem vesznek el; betöltéskor ezek felülírják a beépítettet."""

    def __init__(self, directory: Path = LOCALES_DIR, override_dir: Path | None = None) -> None:
        self.dir = directory
        self.override_dir = override_dir
        self.catalogs: dict[str, dict[str, str]] = {}
        self.names: dict[str, str] = {}
        self.reload()

    def _load(self, folder: Path) -> None:
        for f in sorted(folder.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            meta = data.pop("_meta", {})
            self.catalogs.setdefault(f.stem, {}).update({k: v for k, v in _flatten(data).items() if v})
            if meta.get("name"):
                self.names[f.stem] = meta["name"]
            self.names.setdefault(f.stem, f.stem)

    def reload(self) -> None:
        self.catalogs.clear()
        self.names.clear()
        self._load(self.dir)
        if self.override_dir and self.override_dir.is_dir():
            self._load(self.override_dir)

    def save(self, code: str, values: dict[str, str], name: str | None = None) -> None:
        """Szerkesztett fordítás mentése (lapos kulcs → szöveg JSON) a felülíró mappába."""
        if not self.override_dir:
            raise RuntimeError("no override dir")
        self.override_dir.mkdir(parents=True, exist_ok=True)
        cat = dict(self.catalogs.get(code, {}))
        for k, v in values.items():
            if v:
                cat[k] = v
            else:
                cat.pop(k, None)
        out = {"_meta": {"name": name or self.names.get(code, code)}}
        out.update(dict(sorted(cat.items())))
        tmp = self.override_dir / f".{code}.json.tmp"
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.override_dir / f"{code}.json")
        self.reload()

    @property
    def languages(self) -> list[str]:
        return sorted(self.catalogs, key=lambda c: (c != REFERENCE, c))

    def has(self, lang: str | None) -> bool:
        return bool(lang) and lang in self.catalogs

    def resolve(self, code: str | None) -> str | None:
        """Nyelvkód illesztése a meglévő fordításokhoz: pontos egyezés, ismert álnév (zh-cn, zh-hk…),
        végül az alapnyelv (en-us → en, hu-hu → hu). Ha nincs ilyen fordítás: None."""
        if not code:
            return None
        code = code.strip().replace("_", "-")
        if code in self.catalogs:
            return code
        low = code.lower()
        for c in self.catalogs:
            if c.lower() == low:
                return c
        for cand in (ALIASES.get(low), ALIASES.get(low.split("-")[0]), low.split("-")[0]):
            if cand and cand in self.catalogs:
                return cand
        return None

    def from_header(self, header: str | None) -> str | None:
        """Accept-Language fejléc → a legjobb elérhető nyelv."""
        langs = []
        for i, part in enumerate((header or "").split(",")):
            tag, _, q = part.strip().partition(";q=")
            try:
                weight = float(q) if q else 1.0
            except ValueError:
                weight = 0.0
            if tag and tag != "*":
                langs.append((-weight, i, tag))
        for _, _, tag in sorted(langs):
            if (c := self.resolve(tag)):
                return c
        return None

    def t(self, lang: str, key: str, **kw) -> str:
        text = self.catalogs.get(lang, {}).get(key)
        if text is None:
            text = self.catalogs.get(REFERENCE, {}).get(key, key)
        if kw:
            try:
                text = text.format(**kw)
            except (KeyError, IndexError, ValueError):
                pass
        return text

    def completeness(self, lang: str) -> tuple[int, int]:
        ref = self.catalogs.get(REFERENCE, {})
        cat = self.catalogs.get(lang, {})
        done = sum(1 for k in ref if cat.get(k))
        return done, len(ref)

    def missing(self, lang: str) -> list[str]:
        ref = self.catalogs.get(REFERENCE, {})
        cat = self.catalogs.get(lang, {})
        return [k for k in ref if not cat.get(k)]
