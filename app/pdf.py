"""Képekből álló (szkennelt) és túl nagy PDF-ek: oldalak kirajzolása (poppler), kisebb PDF, képes CBZ."""
from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from .config import Config
from .tools import run

# (hosszabbik oldal képpontban, JPEG-minőség) – fentről lefelé az első, ami belefér a keretbe
LADDER = ((2048, 75), (2048, 62), (1800, 62), (1800, 50), (1800, 42), (1500, 45), (1500, 36), (1280, 36), (1280, 28))
SAMPLES = 8          # ennyi oldalból becsüli a teljes méretet
PAGE_DPI = 150       # a kisebb PDF oldalmérete ebből számolódik (csak a megjelenített méretet adja)


def available(cfg: Config) -> bool:
    return all(shutil.which(t) for t in (cfg.pdftoppm, cfg.pdfinfo, cfg.pdftotext))


async def page_count(cfg: Config, src: Path) -> int:
    code, out = await run([cfg.pdfinfo, str(src)], 120)
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    if code != 0 or not m:
        raise RuntimeError("PDF nem olvasható: " + out[-200:])
    return int(m.group(1))


async def is_scanned(cfg: Config, src: Path) -> bool:
    """Nincs (érdemi) szövegréteg: az oldalak képek, a szöveges konvertálásnak nincs miből dolgoznia."""
    pages = 30
    proc_out = await run([cfg.pdftotext, "-l", str(pages), str(src), "-"], 120)
    if proc_out[0] != 0:
        return False
    letters = sum(c.isalnum() for c in proc_out[1])
    return letters < 20 * min(pages, await page_count(cfg, src))


async def render(cfg: Config, src: Path, outdir: Path, long_side: int, quality: int, timeout: int,
                 first: int | None = None, last: int | None = None, gray: bool = False) -> list[Path]:
    """Oldalak JPEG-be (p-001.jpg…); a nevek a sorrendet adják."""
    cmd = [cfg.pdftoppm, "-jpeg", "-jpegopt", f"quality={quality},optimize=y", "-scale-to", str(long_side)]
    if gray:
        cmd.append("-gray")
    if first:
        cmd += ["-f", str(first), "-l", str(last or first)]
    code, out = await run(cmd + [str(src), str(outdir / "p")], timeout)
    files = sorted(outdir.glob("p-*.jpg"))
    if code != 0 or not files:
        raise RuntimeError("PDF-oldalak kirajzolása sikertelen: " + out[-300:])
    return files


def write_pdf(pages: list[Path], dst: Path, title: str = "") -> None:
    """A JPEG-ek újratömörítés nélkül kerülnek a PDF-be (oldalanként egy kép)."""
    n = len(pages)
    offsets: dict[int, int] = {}
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
    with open(tmp, "wb") as f:
        def obj(num: int, body: str, stream: bytes | None = None) -> None:
            offsets[num] = f.tell()
            f.write(f"{num} 0 obj\n{body}\n".encode("latin-1"))
            if stream is not None:
                f.write(b"stream\n" + stream + b"\nendstream\n")
            f.write(b"endobj\n")

        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        obj(1, "<< /Type /Catalog /Pages 2 0 R >>")
        obj(2, f"<< /Type /Pages /Count {n} /Kids [{' '.join(f'{4 + 3 * i} 0 R' for i in range(n))}] >>")
        obj(3, f"<< /Title <{('\ufeff' + title).encode('utf-16-be').hex().upper()}> /Producer (Shelfy) >>")
        for i, p in enumerate(pages):
            with Image.open(p) as im:
                w, h, mode = im.width, im.height, im.mode
            pw, ph = w * 72 / PAGE_DPI, h * 72 / PAGE_DPI
            data = p.read_bytes()
            content = f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im Do Q".encode()
            obj(4 + 3 * i, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
                           f"/Resources << /XObject << /Im {6 + 3 * i} 0 R >> >> /Contents {5 + 3 * i} 0 R >>")
            obj(5 + 3 * i, f"<< /Length {len(content)} >>", content)
            obj(6 + 3 * i, f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /BitsPerComponent 8 "
                           f"/ColorSpace /{'DeviceGray' if mode == 'L' else 'DeviceRGB'} /Filter /DCTDecode "
                           f"/Length {len(data)} >>", data)
        xref = f.tell()
        total = 4 + 3 * n
        f.write(f"xref\n0 {total}\n".encode() + b"0000000000 65535 f \n")
        for num in range(1, total):
            f.write(f"{offsets[num]:010d} 00000 n \n".encode())
        f.write(f"trailer\n<< /Size {total} /Root 1 0 R /Info 3 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    tmp.replace(dst)


async def shrink_pdf(cfg: Config, src: Path, dst: Path, max_bytes: int, title: str = "") -> bool:
    """A PDF oldalait képként újrakódolja úgy, hogy a fájl max_bytes alá kerüljön. False: így sem fér bele.

    A méretet néhány mintaoldalból becsüli, így a teljes kirajzolás jellemzően egyszer fut le.
    A szövegréteg (kijelölés, keresés) elvész – ezért csak akkor érdemes hívni, ha a fájl egyébként nem küldhető el."""
    n = await page_count(cfg, src)
    sample = sorted({1 + round(i * (n - 1) / max(1, SAMPLES - 1)) for i in range(min(SAMPLES, n))})
    timeout = cfg.convert_timeout * 4
    for long_side, quality in LADDER:
        with tempfile.TemporaryDirectory(dir=dst.parent) as td:
            sizes = []
            for pg in sample:
                f = (await render(cfg, src, Path(td), long_side, quality, 120, first=pg))[0]
                sizes.append(f.stat().st_size)
                f.unlink()
            if sum(sizes) / len(sizes) * n > max_bytes * 0.93:
                continue
            pages = await render(cfg, src, Path(td), long_side, quality, timeout)
            await asyncio.to_thread(write_pdf, pages, dst, title)
        if dst.stat().st_size <= max_bytes:
            return True
        dst.unlink(missing_ok=True)
    return False


async def images_cbz(cfg: Config, src: Path, dst: Path, long_side: int, quality: int, gray: bool = False) -> None:
    """Szkennelt PDF → CBZ (oldalanként egy kép): ebből a konvertáló képes AZW3/MOBI/EPUB könyvet készít."""
    with tempfile.TemporaryDirectory(dir=dst.parent) as td:
        pages = await render(cfg, src, Path(td), long_side, quality, cfg.convert_timeout * 4, gray=gray)

        def pack() -> None:
            with zipfile.ZipFile(dst, "w", zipfile.ZIP_STORED) as z:
                for i, p in enumerate(pages, 1):
                    z.write(p, f"{i:05d}.jpg")
        await asyncio.to_thread(pack)
