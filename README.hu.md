<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="branding/png/shelfy-logo-dark-1600.png">
    <img alt="Shelfy" src="branding/png/shelfy-logo-light-1600.png" width="360">
  </picture>
</p>


*[English](README.md) · Magyar*

Saját, otthoni e-könyvtár: feltöltés, automatikus vizsgálat és adatgyűjtés, kereshető és szerkeszthető katalógus, konvertálás, küldés Kindle-re és egy egyszerű Kindle-nézet a régi Kindle-ökhöz is. Több felhasználóval, a családnak.

A felület **14 nyelven** érhető el – angol, magyar, német, francia, olasz, spanyol, portugál, lengyel, orosz, török, japán, koreai, egyszerűsített és hagyományos kínai –, mindegyikhez beépített használati útmutatóval; a nyelv a felső sávban egy legördülő listából választható. Minden felirat a `locales/*.json` fájlokban van, és a felületről is fordítható. Világos és sötét téma is van.

![Könyvtár](docs/hu/library.png)

## Mit tud

- **Feltöltés bármilyen formátumban** (EPUB, MOBI, AZW3, PDF, FB2, CBZ, DOCX, TXT…): böngészőből behúzva, vagy egy figyelt mappába téve.
- **Vizsgálat feltöltés után**, jegyzőkönyvvel:
  - a fájl valóban az-e, aminek a kiterjesztése mondja
  - ép-e
  - megvan-e már ugyanez a fájl vagy ugyanez a könyv
  - milyen adatok vannak a fájlban
  - milyen a borító
- **Információk letöltése**: Moly.hu, Google Books, Open Library és nemzeti könyvtárak (német DNB, francia BnF, lengyel BN, japán NDL); a forrásokat és a sorrendjüket a Beállításokban választod ki. A leírás, a sorozat, a kiadás éve, a címkék és a borító egyetlen kattintással átvehető. A találatok közül te választasz, és az adatok jóváhagyás előtt szerkeszthetők.
- **Automatikus kategorizálás** a címkékből és a leírásból (krimi, fantasy, sci-fi, történelmi regény…).
- **Böngészés és rendezés**: szerzők, sorozatok, címkék, kiadók, nyelvek, formátumok és értékelés szerint, valamint a saját polcaid és olvasási állapot.
- **Rács és lista nézet**: csúszkával állítható, hány borító férjen egy sorba, és választható, hány könyv legyen egy oldalon. Listában a borító mellett az adatok és a leírás eleje is látszik.
- **Online adatfrissítés** a már felvett könyveknél: adatonként összeveti a jelenlegit és az online értéket, és csak azt veszi át, amit kijelölsz.
- **Konvertálás** az e-könyv formátumok között. Letöltéskor a hiányzó Kindle-formátumot (MOBI/AZW3/EPUB) magától elkészíti.
- **Küldés Kindle-re e-mailben**, minden felhasználónak a saját Kindle-címére.
- **Kindle-nézet** (`/kindle`): JavaScript nélküli, e-ink-barát felület. Kindle böngészőből magától ez nyílik meg, a régi Kindle MOBI-t kap.
- **Felhasználók és szerepkörök**: adminisztrátor, szerkesztő, olvasó, egyenként állítható jogokkal (feltöltés, szerkesztés, letöltés, törlés).
- **Vendég-böngészés** (kapcsoló a Beállításokban): bejelentkezés nélkül is végig lehet nézni és keresni a könyvtárban, de letölteni, küldeni vagy módosítani nem lehet.
- **E-mail felhasználónként**: közös SMTP-fiók, és ha kell, mindenkinek saját (beépített Gmail-útmutatóval). Az SMTP-jelszavak titkosítva tárolódnak.
- **Napló**: feltöltés, felvétel, küldés (a sikertelen is, okkal), letöltés, belépés és beállításváltozás időbélyeggel, szűrhetően. Minden könyvnek saját előzményei vannak, mindenki látja a saját tevékenységét.
- **Mentés és visszaállítás**: adatbázis-mentés (kicsi, másodpercek alatt) vagy teljes mentés az összes könyvfájllal, napi automatikus mentés a szerveren, visszaállítás az admin felületről vagy parancssorból. Minden visszaállítás előtt biztonsági mentés készül, a könyvtár-ellenőrzés pedig kilistázza, mely könyveknél hiányzik fájl.
- **Háttérfeladatok**: a vizsgálatok, konvertálások, küldések, mentések és visszaállítások a *Feladatok* oldalon követhetők állapottal, haladással, futásidővel és eredménnyel. Az adminisztrátor innen indíthat könyvtár-ellenőrzést és duplikátumkeresést.
- **Névjegy oldal** verziószámmal, készítővel, forráskód-linkkel és rendszerinformációval.
- **Világos és sötét téma** (automatikus, a rendszert követi, vagy kézzel választható).
- **Beépített súgó** minden nyelven, a megfelelő oldalakról linkelve.
- **Telefonra telepíthető** (webalkalmazás-leíró és ikonok).

| Feltöltés | Vizsgálat eredménye |
|---|---|
| ![](docs/hu/upload.png) | ![](docs/hu/review.png) |

| Lista nézet | Sötét téma |
|---|---|
| ![](docs/hu/list.png) | ![](docs/hu/dark.png) |

| Napló | Súgó |
|---|---|
| ![](docs/hu/log.png) | ![](docs/hu/help.png) |

| Háttérfeladatok | |
|---|---|
| ![](docs/tasks.png) | |

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

1. **Beállítások** (bal oldali menü): SMTP a Kindle-küldéshez. A feladó címét az Amazon-fiókban fel kell venni a jóváhagyott feladók közé.
2. **Adminisztráció → Felhasználók**: a családtagok fiókjai.

> A régi Kindle böngészője a Cloudflare-en át nem mindig tölt be (TLS). Otthon a helyi címmel nyisd meg: `http://<nas-ip>:18200`.

### Környezeti változók

| Változó | Alapérték | Leírás |
|---|---|---|
| `DATA_DIR` | `/data` | adatbázis, gyorsítótár, titkosító kulcs |
| `LIBRARY_DIR` | `/data/library` | a könyvek fájljai (Szerző/Cím (id)/…) |
| `INGEST_DIR` | – | figyelt mappa almappákkal együtt: az ide másolt e-könyveket magától felveszi (és kiveszi innen) |
| `DEFAULT_LANGUAGE` | `en` | a felület alapnyelve: `en`, `hu`, `de`, `fr`, `it`, `es`, `pt`, `pl`, `ru`, `tr`, `ja`, `ko`, `zh-Hans`, `zh-Hant` (régiós kód is jó, pl. `hu-hu`, `zh-cn`, `zh-hk`); a kategória-címkék is ezen a nyelven kerülnek a könyvekhez. Bejelentkezés előtt a böngésző nyelvét használja, ha elérhető. |
| `ONLINE_LOOKUP` | `true` | az online adatkeresés kezdőértéke (a Beállításokban átkapcsolható) |
| `METADATA_PROVIDERS` | nyelv szerint | az adatforrások kezdő listája fontossági sorrendben: `moly`, `google`, `openlibrary`, `dnb`, `bnf`, `bn_pl`, `ndl`. Ha nincs megadva, az alapnyelv dönt (pl. `hu` → a Moly.hu az első). A forrásokat, a sorrendjüket és egy opcionális Google Books API-kulcsot a Beállításokban lehet kezelni. |
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

### Mentés, visszaállítás, költözés

Az adatbázis kicsi, a könyvfájlok a `LIBRARY_DIR` mappában vannak. Az *Adminisztráció → Mentés* oldalon:

- letölthetsz **adatbázis-mentést** (adatok, felhasználók, polcok, beállítások) vagy **teljes mentést**, ami a könyvfájlokat is tartalmazza;
- bekapcsolhatod a **napi automatikus adatbázis-mentést** a `DATA_DIR/backups` mappába (megadod, hányat őrizzen meg);
- **visszaállíthatsz** feltöltött vagy a szerveren tárolt mentésből. Előtte a jelenlegi állapotról biztonsági mentés készül.

Nagy könyvtárnál a könyvmappát a NAS saját mentőeszközeivel érdemes menteni, Shelfyvel csak az adatbázist. Költözéskor: másold át a könyvmappát, indítsd el az új Shelfyt, állítsd vissza az adatbázis-mentést, majd futtasd a *Könyvtár ellenőrzése* feladatot. Parancssorból:

```bash
docker exec shelfy python -m app.backup create                          # csak adatbázis, a DATA_DIR/backups mappába
docker exec shelfy python -m app.backup create --full -o /data/full.zip # könyvfájlokkal együtt
docker exec shelfy python -m app.backup check                           # hiányzó fájlú könyvek

# parancssori visszaállítás: előbb állítsd le a Shelfyt, aztán egy egyszeri konténerrel
docker compose stop shelfy
docker compose run --rm shelfy python -m app.backup restore /data/backups/<fájl>.zip
docker compose start shelfy
```

## Fordítás

A szövegek a `locales/<kód>.json` fájlokban vannak: angol (`en.json`, ez a referencia – a hiányzó fordítások helyén angol jelenik meg), valamint magyar, német, francia, olasz, spanyol, portugál, lengyel, orosz, török, japán, koreai, egyszerűsített és hagyományos kínai (`hu.json`, `de.json`, … `zh-Hans.json`, `zh-Hant.json`). A használati útmutató nyelvenként a `locales/help/<kód>.html` fájlban van; saját változat a `DATA_DIR/locales/help/` mappába tehető. Új nyelvet az **Adminisztráció → Nyelvek** oldalon lehet felvenni és fordítani. A felületen végzett módosítások a `DATA_DIR/locales` mappába kerülnek, így frissítéskor sem vesznek el.

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
