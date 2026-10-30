# Shelfy

*[English](README.md) · Magyar*

Saját, otthoni e-könyvtár: feltöltés, automatikus vizsgálat és adatgyűjtés, kereshető és szerkeszthető katalógus, konvertálás, küldés Kindle-re és egy egyszerű Kindle-nézet a régi Kindle-ökhöz is. Több felhasználóval, a családnak.

A felület magyar és angol. Minden felirat a `locales/*.json` fájlokban van, és a felületről is fordítható.

![Könyvtár](docs/hu/library.png)

## Mit tud

- **Feltöltés bármilyen formátumban** (EPUB, MOBI, AZW3, PDF, FB2, CBZ, DOCX, TXT…): böngészőből behúzva, vagy egy figyelt mappába téve.
- **Vizsgálat feltöltés után**, jegyzőkönyvvel:
  - a fájl valóban az-e, aminek a kiterjesztése mondja
  - ép-e
  - megvan-e már ugyanez a fájl vagy ugyanez a könyv
  - milyen adatok vannak a fájlban
  - milyen a borító
- **Információk letöltése**: Moly.hu, Google Books és Open Library. A leírás, a sorozat, a kiadás éve, a címkék és a borító egyetlen kattintással átvehető. A találatok közül te választasz, és az adatok jóváhagyás előtt szerkeszthetők.
- **Automatikus kategorizálás** a címkékből és a leírásból (krimi, fantasy, sci-fi, történelmi regény…).
- **Böngészés és rendezés**: szerzők, sorozatok, címkék, kiadók, nyelvek, formátumok és értékelés szerint, valamint a saját polcaid.
- **Konvertálás** az e-könyv formátumok között. Letöltéskor a hiányzó Kindle-formátumot (MOBI/AZW3/EPUB) magától elkészíti.
- **Küldés Kindle-re e-mailben**, minden felhasználónak a saját Kindle-címére.
- **Kindle-nézet** (`/kindle`): JavaScript nélküli, e-ink-barát felület. Kindle böngészőből magától ez nyílik meg, a régi Kindle MOBI-t kap.
- **Felhasználók és szerepkörök**: adminisztrátor, szerkesztő, olvasó, egyenként állítható jogokkal (feltöltés, szerkesztés, letöltés, törlés).

| Feltöltés | Vizsgálat eredménye |
|---|---|
| ![](docs/hu/upload.png) | ![](docs/hu/review.png) |

| Kindle lista | Kindle adatlap |
|---|---|
| ![](docs/hu/kindle_list.png) | ![](docs/hu/kindle_book.png) |

## Telepítés (Portainer / Docker Compose)

Lásd: [`docker-compose.example.yml`](docker-compose.example.yml).

```yaml
services:
  shelfy:
    image: ghcr.io/czdanika/shelfy:latest
    restart: unless-stopped
    ports: ["18200:8080"]
    volumes:
      - shelfy-data:/data
    environment:
volumes:
  shelfy-data:
```

Az első megnyitáskor létrehozod az admin fiókot. Utána:

1. **Adminisztráció → Beállítások**: SMTP a Kindle-küldéshez. A feladó címét az Amazon-fiókban fel kell venni a jóváhagyott feladók közé.
2. **Adminisztráció → Felhasználók**: a családtagok fiókjai.

> A régi Kindle böngészője a Cloudflare-en át nem mindig tölt be (TLS). Otthon a helyi címmel nyisd meg: `http://<nas-ip>:18200`.

### Környezeti változók

| Változó | Alapérték | Leírás |
|---|---|---|
| `DATA_DIR` | `/data` | adatbázis, gyorsítótár, titkosító kulcs |
| `LIBRARY_DIR` | `/data/library` | a könyvek fájljai (Szerző/Cím (id)/…) |
| `INGEST_DIR` | – | figyelt mappa almappákkal együtt: az ide másolt e-könyveket magától felveszi (és kiveszi innen) |
| `DEFAULT_LANGUAGE` | `hu` | a felület alapnyelve |
| `ONLINE_LOOKUP` | `true` | online adatkeresés be/ki |
| `METADATA_PROVIDERS` | `moly,google,openlibrary` | adatforrások sorrendben |
| `ADMIN_USER`, `ADMIN_PASSWORD` | – | első admin fiók (ha nincs még felhasználó) |
| `SECRET_KEY` | automatikus | a munkamenet-sütik titkosító kulcsa |
| `CONVERT_TIMEOUT` | `300` | konvertálás időkorlátja másodpercben |

### Elfelejtett jelszó

Egy felhasználó jelszavát az admin az *Adminisztráció → Felhasználók* oldalon állíthatja vissza. Ha az admin a sajátját felejtette el, a szerveren futtasd ezt (a webes felületről nem érhető el):

```bash
docker exec -it shelfy python -m app.reset_password            # felhasználók listája
docker exec -it shelfy python -m app.reset_password daniel     # új jelszó beállítása
docker exec -it shelfy python -m app.reset_password daniel --admin   # …és adminná tétel
```

Utána a felhasználó minden eszközről kijelentkezik.

## Fordítás

A szövegek a `locales/hu.json` (referencia) és a `locales/en.json` fájlban vannak. Új nyelvet az **Adminisztráció → Nyelvek** oldalon lehet felvenni és fordítani. A felületen végzett módosítások a `DATA_DIR/locales` mappába kerülnek, így frissítéskor sem vesznek el.

Ellenőrzés, hogy minden használt kulcs le van-e fordítva:

```bash
python scripts/check_locales.py
```

## Fejlesztés

```bash
pip install -r requirements.txt pytest
pytest -q                       # a konvertálási tesztek ebook-convert nélkül kimaradnak
DATA_DIR=./data uvicorn --factory app.main:create_app --reload
```

A Moly.hu adatait az oldal nyilvános HTML-jéből olvassa, azonosítható User-Agenttel és visszafogott tempóban.
