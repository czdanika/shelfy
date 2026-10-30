"""Külső eszközök: ebook-meta / ebook-convert, Kindle-javítás, hash, e-mail."""
from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import smtplib
import ssl
import struct
import tempfile
import zipfile
from email.message import EmailMessage
from pathlib import Path

from .config import Config


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def run(cmd: list[str], timeout: int) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "timeout"
    return proc.returncode, (out or b"").decode(errors="replace")


class Converter:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.lock = asyncio.Semaphore(1)

    @property
    def has_meta(self) -> bool:
        return shutil.which(self.cfg.ebook_meta) is not None

    @property
    def has_convert(self) -> bool:
        return shutil.which(self.cfg.ebook_convert) is not None

    async def read_meta(self, path: Path) -> tuple[dict, bytes | None]:
        """Metaadat + borító kiolvasása a fájlból (ebook-meta --to-opf, --get-cover)."""
        if not self.has_meta:
            return fallback_meta(path), None
        with tempfile.TemporaryDirectory() as td:
            opf = Path(td) / "m.opf"
            cov = Path(td) / "cover.jpg"
            code, out = await run([self.cfg.ebook_meta, str(path), f"--to-opf={opf}", f"--get-cover={cov}"], 120)
            meta = parse_opf(opf.read_text(encoding="utf-8", errors="replace")) if opf.exists() else {}
            if not meta:
                meta = fallback_meta(path)
            cover = cov.read_bytes() if cov.exists() and cov.stat().st_size > 200 else None
            return meta, cover

    async def convert(self, src: Path, dst: Path, opts: list[str]) -> None:
        async with self.lock:
            tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
            code, out = await run([self.cfg.ebook_convert, str(src), str(tmp), *opts], self.cfg.convert_timeout)
            if code != 0 or not tmp.exists():
                raise RuntimeError("Konvertálás sikertelen: " + out[-400:])
            tmp.replace(dst)


# ---------------------------------------------------------------- OPF feldolgozás

OPF_NS = {"dc": "http://purl.org/dc/elements/1.1/", "opf": "http://www.idpf.org/2007/opf"}


def parse_opf(text: str) -> dict:
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    md = root.find("opf:metadata", OPF_NS)
    if md is None:
        md = root.find(".//{http://www.idpf.org/2007/opf}metadata")
    if md is None:
        return {}

    def all_(tag):
        return [(e.text or "").strip() for e in md.findall(f"dc:{tag}", OPF_NS) if (e.text or "").strip()]

    meta: dict = {}
    t = all_("title")
    if t:
        meta["title"] = t[0]
    meta["authors"] = [a for a in all_("creator") if a.lower() not in ("unknown", "ismeretlen")]
    pubs = all_("publisher")
    if pubs:
        meta["publisher"] = pubs[0]
    langs = all_("language")
    if langs and langs[0].lower() not in ("und", "unknown"):
        meta["language"] = langs[0][:3].lower()
    dates = all_("date")
    if dates:
        m = re.match(r"(\d{4})", dates[0])
        if m and int(m.group(1)) > 101:
            meta["year"] = int(m.group(1))
    meta["tags"] = all_("subject")
    desc = all_("description")
    if desc:
        meta["description"] = desc[0]
    for e in md.findall("dc:identifier", OPF_NS):
        scheme = (e.get("{http://www.idpf.org/2007/opf}scheme") or "").lower()
        val = (e.text or "").strip()
        if scheme == "isbn" or re.fullmatch(r"(97[89])?\d{9}[\dXx]", val.replace("-", "")):
            if re.fullmatch(r"(97[89])?\d{9}[\dXx]", val.replace("-", "")):
                meta["isbn"] = val.replace("-", "")
    for e in md.findall("opf:meta", OPF_NS):
        name, content = e.get("name"), e.get("content")
        if name == "calibre:series" and content:
            meta["series"] = content
        elif name == "calibre:series_index" and content:
            try:
                meta["series_index"] = float(content)
            except ValueError:
                pass
        elif name == "calibre:rating" and content:
            try:
                meta["rating"] = round(float(content) / 2)
            except ValueError:
                pass
    return meta


def fallback_meta(path: Path) -> dict:
    """Ha nincs ebook-meta: fájlnévből ("Szerző - Cím.epub"), EPUB-nál az OPF-ből."""
    if path.suffix.lower() == ".epub":
        try:
            with zipfile.ZipFile(path) as z:
                cont = z.read("META-INF/container.xml").decode("utf-8", "replace")
                m = re.search(r'full-path="([^"]+)"', cont)
                if m:
                    meta = parse_opf(z.read(m.group(1)).decode("utf-8", "replace"))
                    if meta.get("title"):
                        return meta
        except Exception:
            pass
    stem = re.sub(r"[_]+", " ", path.stem).strip()
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return {"title": t.strip(), "authors": [a.strip()]}
    return {"title": stem}


# ---------------------------------------------------------------- Kindle

def kindle_personal_doc(path: Path) -> bool:
    """MOBI/AZW3: EBOK -> PDOC és ál-ASIN kikapcsolása, hogy a Kindle a beágyazott borítót mutassa."""
    data = bytearray(path.read_bytes())
    if len(data) < 86 or data[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
        return False
    nrec = struct.unpack_from(">H", data, 76)[0]
    changed = False
    for i in range(nrec):
        off = struct.unpack_from(">I", data, 78 + i * 8)[0]
        if off + 24 > len(data) or data[off + 16:off + 20] != b"MOBI":
            continue
        p = off + 16 + struct.unpack_from(">I", data, off + 20)[0]
        if data[p:p + 4] != b"EXTH":
            continue
        q = p + 12
        for _ in range(struct.unpack_from(">I", data, p + 8)[0]):
            if q + 8 > len(data):
                break
            code, length = struct.unpack_from(">II", data, q)
            if length < 8:
                break
            if code == 501 and data[q + 8:q + 12] == b"EBOK":
                data[q + 8:q + 12] = b"PDOC"
                changed = True
            elif code in (113, 504):
                struct.pack_into(">I", data, q, 65000 + code % 1000)
                changed = True
            q += length
    if changed:
        path.write_bytes(bytes(data))
    return changed


# ---------------------------------------------------------------- e-mail

def send_mail(settings: dict, to: str, subject: str, filename: str, data: bytes, mime: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings["smtp_from"]
    msg["To"] = to
    msg.set_content("Shelfy")
    maintype, _, subtype = (mime or "application/octet-stream").partition("/")
    msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
    ctx = ssl.create_default_context()
    sec = (settings.get("smtp_security") or "starttls").lower()
    port = int(settings.get("smtp_port") or 587)
    server = smtplib.SMTP_SSL(settings["smtp_host"], port, context=ctx, timeout=60) if sec == "ssl" \
        else smtplib.SMTP(settings["smtp_host"], port, timeout=60)
    try:
        if sec == "starttls":
            server.starttls(context=ctx)
        if settings.get("smtp_user"):
            server.login(settings["smtp_user"], settings.get("smtp_password") or "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass
