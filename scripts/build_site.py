"""A bemutatóoldal (GitHub Pages) előállítása a docs/ mappába, angolul és magyarul.

Futtatás: python scripts/build_site.py
Kimenet:  docs/index.html, docs/install.html, docs/hu/index.html, docs/hu/install.html
A telepítési útmutató referenciarészei (mit jelentenek a sorok, hibaelhárítás, környezeti változók, mentés…)
a README.md / README.hu.md „Installation / Telepítés” fejezetéből készülnek, így a kettő nem tér el egymástól.
A megjelenés a docs/site.css, a működés a docs/site.js fájlban van.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import __author__, __url__, __version__  # noqa: E402

DOCS = ROOT / "docs"


def asset_v() -> str:
    """A stíluslap és a szkript címébe kerülő jelölő a tartalmukból, hogy módosítás után a böngésző frisset töltsön."""
    import hashlib
    return hashlib.sha1((DOCS / "site.css").read_bytes() + (DOCS / "site.js").read_bytes()).hexdigest()[:8]

# ------------------------------------------------------------------ apró Markdown → HTML (csak ami a README-ben előfordul)

def inline(s: str) -> str:
    parts = s.split("`")
    out = []
    for i, part in enumerate(parts):
        part = html.escape(part, quote=False)
        if i % 2:
            out.append(f"<code>{part}</code>")
            continue
        part = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", part)
        part = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", part)
        part = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', part)
        out.append(part)
    return "".join(out)


def md(text: str) -> str:
    lines = text.strip("\n").split("\n")
    out: list[str] = []
    i = 0
    para: list[str] = []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j]), quote=False) + "</code></pre>")
            i = j + 1
        elif ln.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in re.split(r"(?<!\\)\|", lines[i].strip().strip("|"))])
                i += 1
            head, body = rows[0], rows[2:]
            t = "<div class=\"tablewrap\"><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table></div>"
            out.append(t)
        elif ln.startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].startswith(">"):
                q.append(lines[i].lstrip("> "))
                i += 1
            out.append('<div class="note">' + md("\n".join(q)) + "</div>")
        elif re.match(r"(- |\d+\. )", ln):
            flush()
            tag = "ul" if ln.startswith("- ") else "ol"
            items = []
            while i < len(lines) and (re.match(r"(- |\d+\. )", lines[i]) or (lines[i].startswith("  ") and lines[i].strip())):
                if re.match(r"(- |\d+\. )", lines[i]):
                    items.append(re.sub(r"^(- |\d+\. )", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
        elif not ln.strip():
            flush()
            i += 1
        else:
            para.append(ln.strip())
            i += 1
    flush()
    return "\n".join(out)


def readme_sections(path: Path) -> list[tuple[str, str]]:
    """A telepítési fejezet ### alfejezetei (cím, HTML) sorrendben."""
    text = path.read_text(encoding="utf-8")
    chapters = re.split(r"^## ", text, flags=re.M)
    install = next(c for c in chapters if "ghcr.io/czdanika/shelfy" in c and "### " in c)
    subs = re.split(r"^### ", install, flags=re.M)[1:]
    return [(s.split("\n", 1)[0].strip(), md(s.split("\n", 1)[1])) for s in subs]


# ------------------------------------------------------------------ közös darabok

ICON = {
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "read": '<path d="M3 5h6a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H3zM21 5h-6a3 3 0 0 0-3 3v12a2 2 0 0 1 2-2h7z"/>',
    "send": '<path d="M22 3L2 11l7 3 3 7z"/><path d="M22 3L9 14"/>',
    "tablet": '<rect x="5" y="2.5" width="14" height="19" rx="2"/><path d="M10 18.5h4"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.5 2.9-5.5 6.5-5.5s6.5 2 6.5 5.5M16 4.8a3.5 3.5 0 0 1 0 6.4M18 14.8c2.1.7 3.5 2.3 3.5 5.2"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>',
    "phone": '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "github": '<path d="M9 19c-4 1.3-4-2-6-2.5M15 22v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12 12 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V22"/>',
    "book": '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11"/>',
}


def icon(name: str, size: int = 22) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICON[name]}</svg>')


def logo(w: int = 34) -> str:
    return (f'<svg class="lg" width="{w}" height="{round(w * .875)}" viewBox="64 96 384 336" aria-hidden="true">'
            '<rect class="lg-shelf" x="80" y="392" width="352" height="26" rx="5"/>'
            '<g class="lg-h lg-h1"><rect class="lg-b lg-b1" x="112" y="120" width="78" height="272" rx="6"/></g>'
            '<g class="lg-h lg-h2"><rect class="lg-b lg-b2" x="204" y="168" width="78" height="224" rx="6"/></g>'
            '<g class="lg-h lg-h3"><g class="lg-b lg-b3"><rect class="lg-tip" x="296" y="112" width="78" height="280" rx="6"/></g></g></svg>')


def shelves(rows: int = 1, n: int = 44) -> str:
    cols = ["#14B8A6", "#F59E0B", "#F3F4F6", "#9CA3AF", "#2F6690", "#B4532A", "#5B5FA8", "#0F766E"]
    hs = [78, 92, 64, 88, 70, 96, 58, 84, 74, 90, 66, 80, 94, 60]
    out = []
    for r in range(rows):
        lean = ' class="lean"'
        cells = "".join(
            f'<i{lean if (i + r * 2) % 7 == 5 else ""} style="--h:{hs[(i * 5 + r * 3) % 14]}%;--c:{cols[(i * 3 + r * 5 + i // 4) % 8]};'
            f'--w:{14 + (i * 7 + r * 4) % 5 * 4}px;--i:{i}"></i>' for i in range(n))
        out.append(f'<div class="hs-row">{cells}</div>')
    return '<div class="shelves" aria-hidden="true">' + "".join(out) + "</div>"


def shot(src: str, alt: str, eager: bool = False) -> str:
    lazy = "" if eager else ' loading="lazy"'
    return (f'<div class="shot"><div class="bar"><i></i><i></i><i></i></div>'
            f'<img src="{src}" alt="{html.escape(alt)}" width="1360" height="900"{lazy}></div>')


# ------------------------------------------------------------------ szövegek

L = {
    "en": dict(
        dir="", other=("hu/", "Magyar"), html_lang="en", manual="MANUAL.md",
        nav=("Features", "Screens", "Install", "Manual"),
        title="Shelfy – your family’s own ebook library at home",
        desc="Shelfy is a self-hosted ebook library for your home and family: upload, automatic checks and metadata, a browser reader, Send to Kindle. Runs in one Docker container.",
        h1="Your family’s own <em>ebook library</em>, at home.",
        lead="Upload your books and Shelfy checks the files, fills in the details and the cover from the internet, and puts them in a searchable catalogue – to read in the browser or send to a Kindle.",
        cta=("Install in 5 minutes", "View on GitHub"), sub="One Docker container · runs on a NAS, a Raspberry Pi or any Linux box · your books stay at home",
        facts=(("14", "interface languages"), ("14+", "ebook formats"), ("1", "Docker container"), ("amd64 · arm64", "NAS, Raspberry Pi, PC")),
        f_kicker="What it does", f_h2="Everything a home library needs", f_p="From dropping in a file to reading it on the sofa.",
        feats=(
            ("upload", "Upload and check", "Drop in books in any format. Shelfy checks that each file is intact, whether you already have it, and what it can read from it."),
            ("search", "Details and cover from the internet", "Moly.hu, Google Books, Open Library, Hardcover and national libraries. You decide what to take over."),
            ("read", "Read in the browser", "A built-in reader for EPUB and PDF with text settings, bookmarks and highlights. Pick up where you left off."),
            ("send", "Send to Kindle", "By e-mail or straight to your Amazon account, up to 200 MB. A missing Kindle format is created for you."),
            ("tablet", "Kindle view", "An e-ink friendly page with no JavaScript: browse and download right from the Kindle’s own browser."),
            ("users", "For the whole family", "Users, roles and permissions, personal shelves and reading status, optional guest browsing."),
            ("layers", "A tidy library", "Authors, series, tags, publishers and languages; grid and list views; automatic categories."),
            ("shield", "Backup and restore", "Daily automatic backups, one-click restore, and an easy move to another machine."),
            ("globe", "14 languages", "The interface and the built-in help, with an in-app translation editor."),
            ("phone", "On your phone too", "A responsive layout that installs as an app on iOS and Android."),
        ),
        s_kicker="A look inside", s_h2="Made to be pleasant to use", s_p="Animated, quick, and equally at home in light and dark.",
        shows=(
            ("home.png", "Home", "Pick up where you left off", "The home page opens with the book you are reading, its progress and a continue button.",
             ("Your other unfinished books next to it", "Recently added and popular books in scrollable rows")),
            ("library.png", "Library", "A library that is nice to look at", "Covers open like a book when you point at them, with quick buttons for download, Send to Kindle and edit.",
             ("Grid and list views, adjustable cover size", "Sort by date, title, author, series or rating")),
            ("book.png", "Book page", "Everything about a book in one place", "Details, formats, reading status, shelves and the description, on a background tinted by the cover.",
             ("Read, download, convert or send in one click", "Refresh the details online, field by field")),
            ("dark.png", "Themes", "Light, dark, or follow the system", "Each user picks their own theme and language.", ()),
        ),
        q_kicker="Installation", q_h2="Up and running in three steps", q_p="No database to set up, no extra services. One container and one folder.",
        q_steps=(("Paste the compose file", "into Portainer, your NAS’s container app, or a terminal."),
                 ("Open the address", "http://<nas-ip>:18200 and create the administrator account."),
                 ("Upload your books", "or copy them into the watched folder.")),
        q_btn="Full install guide",
        foot=("Install guide", "Manual", "GitHub", "Report a problem"),
        c=dict(port="NAS port : container port (change only the left one)", data="database, settings, backups and the book files – back this up",
               ingest="watched folder: ebooks copied here are imported", lib="keep the book files in a separate share",
               tz="time zone for the timestamps", lang="default interface language", ingestenv="turns the watched folder on",
               puid="owner of the folders on the NAS (id <username>)"),
        copy="Copy", copied="Copied",
        g_title="Install Shelfy – step-by-step guide", g_h1="Install Shelfy",
        g_lead="Shelfy runs as a single Docker container. Pick your system, set a few values, and copy the file this page generates for you.",
        toc=("Quick start", "Reference"),
        st=("Pick your system", "Set it up", "Copy and start", "Open Shelfy", "First settings"),
        need="You need a device that can run Docker containers: a NAS, a Raspberry Pi, or any Linux, Windows or macOS computer. The image is published for <code>amd64</code> and <code>arm64</code> and includes the conversion tools.",
        plats=dict(
            terramaster=("TerraMaster", "/Volume1/DockerAppsData/shelfy", "/Volume1/Books/Shelfy",
                         "Install <strong>Docker Manager</strong> (or <strong>Portainer</strong>) from the App Center. Create a new project / stack from a compose file and paste the file below. In Portainer: <em>Stacks → Add stack → Web editor</em>."),
            synology=("Synology", "/volume1/docker/shelfy", "/volume1/books/Shelfy",
                      "Open <strong>Container Manager</strong> (DSM 7.2 or later) → <em>Project → Create</em>. Choose the data folder as the path, select <em>Create docker-compose.yml</em>, and paste the file below."),
            qnap=("QNAP", "/share/Container/shelfy", "/share/Books/Shelfy",
                  "Open <strong>Container Station</strong> → <em>Applications → Create</em>, give it the name <code>shelfy</code>, and paste the file below into the YAML editor."),
            unraid=("Unraid", "/mnt/user/appdata/shelfy", "/mnt/user/books/Shelfy",
                    "Install the <strong>Docker Compose Manager</strong> plugin from Community Applications, add a new stack and paste the file below. Without the plugin, use the <em>docker run</em> command in a terminal."),
            linux=("Raspberry Pi / Linux", "/home/pi/shelfy", "/home/pi/books",
                   "Install Docker (<code>curl -fsSL https://get.docker.com | sh</code>), save the file below as <code>docker-compose.yml</code> in an empty folder, and run <code>docker compose up -d</code> there."),
        ),
        fl=dict(data=("Data folder", "Everything Shelfy stores goes here. This is the one folder to back up."),
                port=("Port", "Shelfy will answer on this port of your device."),
                lang=("Default language", "Each user can still choose their own."),
                tz=("Time zone", "For the timestamps in the log."),
                ingest="Watched folder: import ebooks copied into it",
                lib=("Book files in a separate share (optional)", "Leave empty to keep the books inside the data folder."),
                puid=("PUID / PGID (optional)", "Only if you see “permission denied”: the owner of the folders (find it with <code>id &lt;username&gt;</code>).")),
        out=("Compose file", "docker run"),
        s3a="Create the folders first, so they belong to your own user:",
        s3b="Then paste the file where your system asks for it (see step 1) and start it.",
        s4a="When the container is running, open this address in a browser on your home network and create the administrator account:",
        s4b="Replace <code>&lt;nas-ip&gt;</code> with the address of your device.",
        s5=("<strong>Settings</strong> (left menu): default language, the online sources and their order, and the e-mail (SMTP) account for Send to Kindle. The page includes a step-by-step Gmail guide.",
            "<strong>Administration → Users</strong>: add the family members.",
            "Upload a few books, or copy them into the watched folder.",
            "On <strong>My account</strong>, enter your Kindle e-mail address, or connect your Amazon account to send without e-mail."),
        remote_h="Reaching Shelfy from outside",
        remote=("Shelfy speaks plain HTTP on its port. To reach it from outside your home, put it behind a reverse proxy with HTTPS (Nginx Proxy Manager, Caddy, Traefik) or a tunnel such as Cloudflare Tunnel or Tailscale.",
                "Behind Cloudflare, a single upload cannot be larger than the limit of your plan (100 MB on the free plan). Upload bigger files on the home network, or copy them into the watched folder.",
                "Old Kindle browsers cannot open many modern HTTPS sites. At home, use the local address on the Kindle: <code>http://&lt;nas-ip&gt;:<span data-port>18200</span></code>."),
        ref_ids=("lines", "paths", "variants", "data", "update", "trouble", "env", "password", "backup"),
        more="The <a href=\"{manual}\">user manual</a> describes everything Shelfy can do once it is running.",
    ),
    "hu": dict(
        dir="hu/", other=("../", "English"), html_lang="hu", manual="MANUAL.hu.md",
        nav=("Funkciók", "Képek", "Telepítés", "Kézikönyv"),
        title="Shelfy – a család saját, otthoni e-könyvtára",
        desc="A Shelfy saját, otthoni e-könyvtár a családnak: feltöltés, automatikus ellenőrzés és adatgyűjtés, olvasó a böngészőben, küldés Kindle-re. Egyetlen Docker-konténerben fut.",
        h1="A család saját <em>e-könyvtára</em>, otthon.",
        lead="Töltsd fel a könyveidet: a Shelfy ellenőrzi a fájlokat, az internetről kiegészíti az adataikat és a borítót, és kereshető katalógusba rendezi őket – olvashatod a böngészőben, vagy elküldheted a Kindle-re.",
        cta=("Telepítés 5 perc alatt", "Megnézem a GitHubon"), sub="Egyetlen Docker-konténer · fut NAS-on, Raspberry Pi-n vagy bármilyen Linuxon · a könyveid otthon maradnak",
        facts=(("14", "nyelvű felület"), ("14+", "e-könyv-formátum"), ("1", "Docker-konténer"), ("amd64 · arm64", "NAS, Raspberry Pi, PC")),
        f_kicker="Mit tud", f_h2="Minden, ami egy otthoni könyvtárhoz kell", f_p="A fájl behúzásától addig, hogy a kanapén olvasod.",
        feats=(
            ("upload", "Feltöltés és ellenőrzés", "Húzd be a könyveket bármilyen formátumban. A Shelfy megnézi, ép-e a fájl, megvan-e már, és mit tud kiolvasni belőle."),
            ("search", "Adatok és borító az internetről", "Moly.hu, Google Books, Open Library, Hardcover és nemzeti könyvtárak. Te döntöd el, mit veszel át."),
            ("read", "Olvasás a böngészőben", "Beépített olvasó EPUB-hoz és PDF-hez, szövegbeállításokkal, könyvjelzőkkel és kiemelésekkel. Ott folytatod, ahol abbahagytad."),
            ("send", "Küldés Kindle-re", "E-mailben vagy közvetlenül az Amazon-fiókodba, 200 MB-ig. A hiányzó Kindle-formátumot elkészíti."),
            ("tablet", "Kindle-nézet", "E-ink-barát oldal JavaScript nélkül: a Kindle saját böngészőjéből is böngészhetsz és letölthetsz."),
            ("users", "Az egész családnak", "Felhasználók, szerepkörök és jogosultságok, saját polcok és olvasási állapot, választható vendégböngészés."),
            ("layers", "Rendezett könyvtár", "Szerzők, sorozatok, címkék, kiadók és nyelvek; rács és lista nézet; automatikus kategóriák."),
            ("shield", "Mentés és visszaállítás", "Napi automatikus mentés, visszaállítás egy kattintással, könnyű költözés másik gépre."),
            ("globe", "14 nyelven", "A felület és a beépített súgó is, a programban szerkeszthető fordításokkal."),
            ("phone", "Telefonon is", "Reszponzív felület, amely iOS-en és Androidon appként telepíthető."),
        ),
        s_kicker="Bepillantás", s_h2="Úgy készült, hogy jó legyen használni", s_p="Mozgalmas, gyors, világosban és sötétben is otthonos.",
        shows=(
            ("home.png", "Kezdőlap", "Ott folytatod, ahol abbahagytad", "A kezdőlap az éppen olvasott könyveddel nyit: haladásjelző és folytatás gomb.",
             ("Mellette a többi félbehagyott könyved", "Lapozható sorokban a nemrég hozzáadott és a népszerű könyvek")),
            ("library.png", "Könyvtár", "Könyvtár, amire jó ránézni", "A borítók könyvként kinyílnak, ha föléjük viszed az egeret; gyorsgombok a letöltéshez, a Kindle-re küldéshez és a szerkesztéshez.",
             ("Rács és lista nézet, állítható borítóméret", "Rendezés dátum, cím, szerző, sorozat vagy értékelés szerint")),
            ("book.png", "A könyv oldala", "Minden egy helyen a könyvről", "Adatok, formátumok, olvasási állapot, polcok és a leírás, a borító színeivel árnyalt háttéren.",
             ("Olvasás, letöltés, konvertálás vagy küldés egy kattintással", "Az adatok online frissítése, mezőnként")),
            ("dark.png", "Témák", "Világos, sötét, vagy a rendszer szerint", "Mindenki maga választ témát és nyelvet.", ()),
        ),
        q_kicker="Telepítés", q_h2="Három lépés, és működik", q_p="Nem kell adatbázist beállítani, nincs külön szolgáltatás. Egy konténer és egy mappa.",
        q_steps=(("Illeszd be a compose fájlt", "a Portainerbe, a NAS konténerkezelőjébe vagy egy terminálba."),
                 ("Nyisd meg a címet:", "http://<nas-ip>:18200, és hozd létre az adminisztrátori fiókot."),
                 ("Töltsd fel a könyveidet,", "vagy másold be őket a figyelt mappába.")),
        q_btn="Teljes telepítési útmutató",
        foot=("Telepítési útmutató", "Kézikönyv", "GitHub", "Hibabejelentés"),
        c=dict(port="a NAS portja : a konténer portja (csak a bal oldalit írd át)", data="adatbázis, beállítások, mentések és a könyvfájlok – ezt mentsd",
               ingest="figyelt mappa: az ide másolt e-könyveket felveszi", lib="a könyvfájlok külön megosztásban",
               tz="időzóna az időbélyegekhez", lang="a felület alapnyelve", ingestenv="bekapcsolja a figyelt mappát",
               puid="a mappák tulajdonosa a NAS-on (id <felhasználónév>)"),
        copy="Másolás", copied="Kimásolva",
        g_title="A Shelfy telepítése – útmutató lépésről lépésre", g_h1="A Shelfy telepítése",
        g_lead="A Shelfy egyetlen Docker-konténerként fut. Válaszd ki a rendszeredet, állíts be néhány értéket, és másold ki a fájlt, amit ez az oldal elkészít neked.",
        toc=("Gyors indulás", "Részletek"),
        st=("Válaszd ki a rendszeredet", "Állítsd be", "Másold ki és indítsd el", "Nyisd meg a Shelfyt", "Első beállítások"),
        need="Olyan eszköz kell, amelyen Docker-konténer futtatható: NAS, Raspberry Pi, vagy bármilyen linuxos, windowsos vagy macOS-es gép. A kép <code>amd64</code> és <code>arm64</code> változatban készül, a konvertáló eszközökkel együtt.",
        plats=dict(
            terramaster=("TerraMaster", "/Volume1/DockerAppsData/shelfy", "/Volume1/Konyvek/Shelfy",
                         "Telepítsd az App Centerből a <strong>Docker Managert</strong> (vagy a <strong>Portainert</strong>). Hozz létre új projektet / stacket compose fájlból, és illeszd be a lenti fájlt. Portainerben: <em>Stacks → Add stack → Web editor</em>."),
            synology=("Synology", "/volume1/docker/shelfy", "/volume1/konyvek/Shelfy",
                      "Nyisd meg a <strong>Container Managert</strong> (DSM 7.2 vagy újabb) → <em>Projekt → Létrehozás</em>. Útvonalnak az adatmappát add meg, válaszd a <em>docker-compose.yml létrehozása</em> lehetőséget, és illeszd be a lenti fájlt."),
            qnap=("QNAP", "/share/Container/shelfy", "/share/Konyvek/Shelfy",
                  "Nyisd meg a <strong>Container Stationt</strong> → <em>Applications → Create</em>, add neki a <code>shelfy</code> nevet, és illeszd be a lenti fájlt a YAML-szerkesztőbe."),
            unraid=("Unraid", "/mnt/user/appdata/shelfy", "/mnt/user/konyvek/Shelfy",
                    "Telepítsd a Community Applicationsből a <strong>Docker Compose Manager</strong> bővítményt, adj hozzá új stacket, és illeszd be a lenti fájlt. Bővítmény nélkül használd a <em>docker run</em> parancsot terminálban."),
            linux=("Raspberry Pi / Linux", "/home/pi/shelfy", "/home/pi/konyvek",
                   "Telepítsd a Dockert (<code>curl -fsSL https://get.docker.com | sh</code>), mentsd a lenti fájlt <code>docker-compose.yml</code> néven egy üres mappába, és ott futtasd: <code>docker compose up -d</code>."),
        ),
        fl=dict(data=("Adatmappa", "Ide kerül minden, amit a Shelfy tárol. Ez az egyetlen mappa, amit menteni kell."),
                port=("Port", "A Shelfy az eszközöd ezen a portján lesz elérhető."),
                lang=("Alapnyelv", "Mindenki választhat saját nyelvet."),
                tz=("Időzóna", "A napló időbélyegeihez."),
                ingest="Figyelt mappa: az ide másolt e-könyveket magától felveszi",
                lib=("A könyvfájlok külön megosztásban (nem kötelező)", "Hagyd üresen, ha a könyvek maradhatnak az adatmappában."),
                puid=("PUID / PGID (nem kötelező)", "Csak ha „permission denied” hibát látsz: a mappák tulajdonosa (SSH-n: <code>id &lt;felhasználónév&gt;</code>).")),
        out=("Compose fájl", "docker run"),
        s3a="Előbb hozd létre a mappákat, hogy a saját felhasználódé legyenek:",
        s3b="Utána illeszd be a fájlt oda, ahová a rendszered kéri (lásd az 1. lépést), és indítsd el.",
        s4a="Ha a konténer fut, nyisd meg ezt a címet egy böngészőben az otthoni hálózaton, és hozd létre az adminisztrátori fiókot:",
        s4b="A <code>&lt;nas-ip&gt;</code> helyére az eszközöd címét írd.",
        s5=("<strong>Beállítások</strong> (bal oldali menü): alapnyelv, az online források és sorrendjük, valamint az e-mail (SMTP) fiók a Kindle-re küldéshez. Az oldalon lépésről lépésre Gmail-útmutató is van.",
            "<strong>Adminisztráció → Felhasználók</strong>: vedd fel a családtagokat.",
            "Tölts fel néhány könyvet, vagy másold be őket a figyelt mappába.",
            "A <strong>Saját fiók</strong> oldalon add meg a Kindle e-mail címedet, vagy kapcsold össze az Amazon-fiókodat, hogy e-mail nélkül küldhess."),
        remote_h="Elérés kívülről",
        remote=("A Shelfy sima HTTP-n válaszol a portján. Ha otthonon kívülről is el szeretnéd érni, tedd HTTPS-es fordított proxy (Nginx Proxy Manager, Caddy, Traefik) vagy alagút mögé, például Cloudflare Tunnel vagy Tailscale.",
                "Cloudflare mögött egy feltöltés nem lehet nagyobb a csomagod korlátjánál (az ingyenes csomagon 100 MB). A nagyobb fájlokat az otthoni hálózatról töltsd fel, vagy másold a figyelt mappába.",
                "A régi Kindle-böngészők sok modern HTTPS-oldalt nem tudnak megnyitni. Otthon a Kindle-ön a helyi címet használd: <code>http://&lt;nas-ip&gt;:<span data-port>18200</span></code>."),
        ref_ids=("lines", "paths", "variants", "data", "update", "trouble", "env", "password", "backup"),
        more="A <a href=\"{manual}\">kézikönyv</a> leírja, mit tud a Shelfy, ha már fut.",
    ),
}

LANGS = [("en", "English"), ("hu", "Magyar"), ("de", "Deutsch"), ("fr", "Français"), ("it", "Italiano"), ("es", "Español"), ("pt", "Português"),
         ("pl", "Polski"), ("ru", "Русский"), ("tr", "Türkçe"), ("ja", "日本語"), ("ko", "한국어"), ("zh-Hans", "简体中文"), ("zh-Hant", "繁體中文")]


# ------------------------------------------------------------------ oldalak

def page(lang: str, c: dict, name: str, title: str, body: str, active: str, data: dict | None = None) -> str:
    up = "../" if c["dir"] else ""
    gh = __url__
    other = c["other"][0] + (name if name != "index.html" else "")
    nav = c["nav"]
    script = f"<script>window.SHELFY = {json.dumps(data or {'copy': c['copy'], 'copied': c['copied']}, ensure_ascii=False)};</script>"
    return f"""<!doctype html>
<html lang="{c['html_lang']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(c['desc'])}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(c['desc'])}">
<meta property="og:image" content="https://czdanika.github.io/shelfy/{c['dir']}home.png">
<meta name="theme-color" content="#111827">
<link rel="icon" href="{up}favicon.svg" type="image/svg+xml">
<link rel="alternate" hreflang="{'hu' if lang == 'en' else 'en'}" href="{other}">
<link rel="stylesheet" href="{up}site.css?v={asset_v()}">
</head>
<body>
<header class="nav"><div class="wrap">
  <a class="brand" href="./">{logo(34)}<span>Shelfy</span></a>
  <a class="l{' on' if active == 'features' else ''}" href="./#features">{nav[0]}</a>
  <a class="l" href="./#screens">{nav[1]}</a>
  <a class="l{' on' if active == 'install' else ''}" href="install.html">{nav[2]}</a>
  <a class="l" href="{gh}/blob/main/{c['manual']}">{nav[3]}</a>
  <a class="l" href="{gh}" aria-label="GitHub">{icon('github', 20)}</a>
  <a class="lang" href="{other}" lang="{'hu' if lang == 'en' else 'en'}">{c['other'][1]}</a>
</div></header>
{body}
<footer><div class="wrap">
  <span>© {__author__} · Shelfy {__version__}</span>
  <nav><a href="install.html">{c['foot'][0]}</a><a href="{gh}/blob/main/{c['manual']}">{c['foot'][1]}</a><a href="{gh}">{c['foot'][2]}</a><a href="{gh}/issues">{c['foot'][3]}</a></nav>
</div></footer>
{script}
<script src="{up}site.js?v={asset_v()}"></script>
</body>
</html>
"""


def landing(lang: str, c: dict) -> str:
    gh = __url__
    facts = "".join(f'<div class="fact rv" style="--d:{i}"><b>{a}</b><span>{b}</span></div>' for i, (a, b) in enumerate(c["facts"]))
    feats = "".join(f'<article class="feat rv" style="--d:{i % 3}"><div class="ic">{icon(ic)}</div><h3>{t}</h3><p>{p}</p></article>'
                    for i, (ic, t, p) in enumerate(c["feats"]))
    shows = ""
    for i, (img, kick, h, p, pts) in enumerate(c["shows"]):
        ul = "<ul>" + "".join(f"<li>{x}</li>" for x in pts) + "</ul>" if pts else ""
        shows += (f'<div class="show{" flip" if i % 2 else ""} rv">{shot(img, h)}'
                  f'<div><div class="kicker">{kick}</div><h3>{h}</h3><p>{p}</p>{ul}</div></div>')
    steps = "".join(f"<li><b>{html.escape(a)}</b><span>{html.escape(b)}</span></li>" for a, b in c["q_steps"])
    compose = ('<span class="k">services</span>:\n  <span class="k">shelfy</span>:\n    <span class="k">image</span>: ghcr.io/czdanika/shelfy:latest\n'
               '    <span class="k">restart</span>: unless-stopped\n    <span class="k">ports</span>: [<span class="s">"18200:8080"</span>]\n'
               '    <span class="k">volumes</span>:\n      - /volume1/docker/shelfy:/data\n    <span class="k">environment</span>:\n'
               f'      - TZ=Europe/Budapest\n      - DEFAULT_LANGUAGE={lang}')
    body = f"""<main>
<section class="hero">
  <div class="glow" aria-hidden="true"></div>
  <div class="wrap">
    <div>
      <h1>{c['h1']}</h1>
      <p class="lead">{c['lead']}</p>
      <div class="cta"><a class="btn primary" href="install.html">{icon('download', 20)} {c['cta'][0]}</a><a class="btn ghost" href="{gh}">{icon('github', 20)} {c['cta'][1]}</a></div>
      <p class="small">{c['sub']}</p>
    </div>
    <div class="stage">{shot('home.png', c['shows'][0][2], eager=True)}<img class="phone" src="mobile.png" alt="" width="780" height="1600"></div>
  </div>
  {shelves()}
</section>
<div class="wrap"><div class="facts">{facts}</div></div>
<section id="features"><div class="wrap">
  <div class="sec-head rv"><div class="kicker">{c['f_kicker']}</div><h2>{c['f_h2']}</h2><p>{c['f_p']}</p></div>
  <div class="grid">{feats}</div>
</div></section>
<section id="screens" style="padding-top:0"><div class="wrap">
  <div class="sec-head rv"><div class="kicker">{c['s_kicker']}</div><h2>{c['s_h2']}</h2><p>{c['s_p']}</p></div>
  {shows}
</div></section>
<section class="band" id="install"><div class="glow" aria-hidden="true"></div><div class="wrap">
  <div class="sec-head rv"><div class="kicker">{c['q_kicker']}</div><h2>{c['q_h2']}</h2><p>{c['q_p']}</p></div>
  <div class="quick">
    <div class="rv"><ol class="steps">{steps}</ol><p style="margin:28px 0 0"><a class="btn primary" href="install.html">{icon('book', 20)} {c['q_btn']}</a></p></div>
    <div class="rv" style="--d:1"><pre>{compose}</pre></div>
  </div>
</div></section>
</main>"""
    return page(lang, c, "index.html", c["title"], body, "features")


def guide(lang: str, c: dict) -> str:
    ref = readme_sections(ROOT / ("README.md" if lang == "en" else "README.hu.md"))[1:]   # az első (gyors indulás) helyett a lépések állnak
    assert len(ref) == len(c["ref_ids"]), (lang, len(ref))
    st = c["st"]
    tabs = "".join(f'<button class="tab" type="button" role="tab" data-plat="{k}">{v[0]}</button>' for k, v in c["plats"].items())
    plats = "".join(f'<div class="plat note" data-plat="{k}"><p>{v[3]}</p></div>' for k, v in c["plats"].items())
    fl = c["fl"]
    langopts = "".join(f'<option value="{k}">{html.escape(n)} ({k})</option>' for k, n in LANGS)

    def field(key, inner, wide=False):
        lab, hint = fl[key]
        return f'<div class="field{" wide" if wide else ""}"><label for="g-{key}">{lab}</label>{inner}<small>{hint}</small></div>'

    form = (field("data", '<input id="g-data" type="text" spellcheck="false" autocomplete="off">', True)
            + field("port", '<input id="g-port" type="number" min="1" max="65535" value="18200">')
            + field("lang", f'<select id="g-lang">{langopts}</select>')
            + field("tz", '<input id="g-tz" type="text" spellcheck="false" autocomplete="off">')
            + f'<div class="field wide"><label class="check"><input id="g-ingest" type="checkbox" checked> {fl["ingest"]}</label></div>'
            + field("lib", '<input id="g-lib" type="text" spellcheck="false" autocomplete="off">', True)
            + f'<div class="field wide"><label for="g-puid">{fl["puid"][0]}</label><div style="display:flex;gap:10px">'
              '<input id="g-puid" type="text" inputmode="numeric" placeholder="1026" style="max-width:140px" autocomplete="off">'
              '<input id="g-pgid" type="text" inputmode="numeric" placeholder="100" style="max-width:140px" autocomplete="off" aria-label="PGID"></div>'
              f'<small>{fl["puid"][1]}</small></div>')
    quick = f"""
<section id="s1"><h2><span class="num">1</span>{st[0]}</h2>
  <p>{c['need']}</p>
  <div class="tabs" role="tablist">{tabs}</div>{plats}
</section>
<section id="s2"><h2><span class="num">2</span>{st[1]}</h2>
  <div class="panel"><div class="form">{form}</div></div>
</section>
<section id="s3"><h2><span class="num">3</span>{st[2]}</h2>
  <p>{c['s3a']}</p>
  <ul><li><code data-folder></code></li><li data-if-ingest><code data-folder-ingest></code></li></ul>
  <p>{c['s3b']}</p>
  <div class="tabs"><button class="tab on" type="button" data-out="compose">{c['out'][0]}</button><button class="tab" type="button" data-out="run">{c['out'][1]}</button></div>
  <div data-outbox="compose"><pre id="g-compose"></pre></div>
  <div data-outbox="run" hidden><pre id="g-run"></pre></div>
</section>
<section id="s4"><h2><span class="num">4</span>{st[3]}</h2>
  <p>{c['s4a']}</p>
  <p><span class="addr">http://&lt;nas-ip&gt;:<span data-port>18200</span></span></p>
  <p>{c['s4b']}</p>
</section>
<section id="s5"><h2><span class="num">5</span>{st[4]}</h2>
  <ol>{''.join(f'<li>{x}</li>' for x in c['s5'])}</ol>
  <p>{c['more'].format(manual=__url__ + '/blob/main/' + c['manual'])}</p>
</section>
<section id="remote"><h2>{c['remote_h']}</h2>
  <p>{c['remote'][0]}</p>
  <div class="note warn"><p>{c['remote'][1]}</p></div>
  <div class="note"><p>{c['remote'][2]}</p></div>
</section>"""
    refs = "".join(f'<section id="{i}"><h2>{html.escape(t)}</h2>{h}</section>' for i, (t, h) in zip(c["ref_ids"], ref))
    toc = (f'<b>{c["toc"][0]}</b>' + "".join(f'<a href="#s{n + 1}">{n + 1}. {t}</a>' for n, t in enumerate(st))
           + f'<b>{c["toc"][1]}</b><a href="#remote">{c["remote_h"]}</a>' + "".join(f'<a href="#{i}">{html.escape(t)}</a>' for i, (t, _) in zip(c["ref_ids"], ref)))
    body = f"""<main>
<div class="pagehead"><div class="glow" aria-hidden="true"></div><div class="wrap"><h1>{c['g_h1']}</h1><p>{c['g_lead']}</p></div></div>
<div class="wrap guide" id="gen">
  <nav class="toc" aria-label="{c['toc'][1]}">{toc}</nav>
  <div class="doc">{quick}{refs}</div>
</div>
</main>"""
    data = {"copy": c["copy"], "copied": c["copied"], "lang": lang, "c": c["c"],
            "platforms": {k: {"data": v[1], "lib": v[2]} for k, v in c["plats"].items()}}
    return page(lang, c, "install.html", c["g_title"], body, "install", data)


def main() -> None:
    (DOCS / "favicon.svg").write_text((ROOT / "static" / "icon.svg").read_text(encoding="utf-8"), encoding="utf-8")
    for lang, c in L.items():
        d = DOCS / c["dir"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(landing(lang, c), encoding="utf-8")
        (d / "install.html").write_text(guide(lang, c), encoding="utf-8")
        print("wrote", (d / "index.html").relative_to(ROOT), (d / "install.html").relative_to(ROOT))


if __name__ == "__main__":
    main()
