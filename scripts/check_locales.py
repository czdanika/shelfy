"""Ellenőrzi, hogy a sablonokban és a kódban használt összes fordítási kulcs megvan-e a locales fájlokban.

Futtatás: python scripts/check_locales.py   (hiba esetén 1-es kilépési kóddal áll le)
A dinamikusan összerakott kulcsokat (pl. 'view.' ~ key) a DYNAMIC lista sorolja fel.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.auth import PERMS, ROLES  # noqa: E402
from app.categories import CATEGORIES  # noqa: E402
from app.i18n import _flatten  # noqa: E402
from app.library import SORT_GROUPS, SORTS  # noqa: E402

STATIC = [
    (r"""\bt\(\s*'([a-z_]+\.[a-z_.0-9]*[a-z0-9])'""", "templates/**/*.html"),
    (r'''\bt\(\s*"([a-z_]+\.[a-z_.0-9]*[a-z0-9])"''', "templates/**/*.html"),
    (r'''tr\(request, "([a-z_]+\.[a-z_.0-9]*[a-z0-9])"''', "app/*.py"),
]


def dynamic() -> set[str]:
    keys: set[str] = set()
    keys |= {f"view.{k}" for k in ("all", "new", "hot", "top", "reading", "read", "unread", "discover", "incomplete", "archived")}
    keys |= {f"browse.{k}" for k in ("authors", "series", "tags", "publishers", "languages", "formats", "ratings")}
    keys |= {f"state.{k}" for k in ("unread", "reading", "read")}
    keys |= {f"role.{r}" for r in ROLES} | {f"perm.{p}" for p in PERMS}
    keys |= {f"cat.{k}" for k in CATEGORIES}
    keys |= {f"source.{s}" for s in ("moly", "google", "openlibrary")}
    keys |= {f"sort.{s}" for s in SORTS}
    for label, opts in SORT_GROUPS:
        keys.add(label)
        for _, short, title in opts:
            keys |= {short, title}
    keys |= {f"upload.src_{s}" for s in ("web", "folder")}
    keys |= {f"upload.status_{s}" for s in ("pending", "inspecting", "review", "done", "error", "rejected")}
    keys |= {f"admin.nav_{s}" for s in ("users", "settings", "languages", "log")}
    keys |= {f"admin.sec_{s}" for s in ("starttls", "ssl", "none")}
    keys |= {f"book.dl_{f}" for f in ("mobi", "azw3", "epub")}
    from app.events import GROUPS, KINDS
    keys |= {f"log.{k}" for k in KINDS} | {f"log.group_{g}" for g in GROUPS}
    keys |= {f"log.period_{p}" for p in ("1", "7", "30", "365", "all")}
    keys |= {f"mail.src_{s}" for s in ("own", "global", "none")}
    keys |= {f"insp.src_{s}" for s in ("ok", "empty", "error")}
    keys |= {f"book.{f}" for f in ("title", "authors", "language", "publisher", "year", "isbn", "series", "description",
                                    "series_index", "rating", "tags")}
    # vizsgálati lépések: a kódból olvassuk ki, milyen kulcs/állapot párok fordulhatnak elő
    src = (ROOT / "app/ingest.py").read_text(encoding="utf-8")
    for key, rest in re.findall(r'check\("([a-z]+)",\s*(.+)', src):
        keys.add(f"insp.{key}")
        for st in re.findall(r'"(ok|warn|error|info)"', rest):
            keys.add(f"insp.{key}_{st}")
    # flash üzenetek
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    keys |= {f"msg.{m}" for m in re.findall(r'flash\([^\n]*?,\s*"([a-z_]+)"\)', main)}
    return keys


def used() -> set[str]:
    keys = dynamic()
    for pattern, glob in STATIC:
        for f in ROOT.glob(glob):
            keys |= set(re.findall(pattern, f.read_text(encoding="utf-8")))
    return keys


def main() -> int:
    need = used()
    bad = 0
    for f in sorted((ROOT / "locales").glob("*.json")):
        cat = _flatten({k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items() if k != "_meta"})
        missing = sorted(need - set(cat))
        if missing:
            bad += 1
            print(f"{f.name}: {len(missing)} hiányzó kulcs")
            for k in missing:
                print("  ", k)
        else:
            print(f"{f.name}: OK ({len(cat)} kulcs, {len(need)} használt)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
