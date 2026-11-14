"""Szkennelt és túl nagy PDF-ek: tömörítés 50 MB alá, oldalképes Kindle-könyv, Kindle-böngészős letöltés."""
from __future__ import annotations

import asyncio
import os
import shutil

import pytest
from PIL import Image

from app import pdf
from app.library import Meta
from app.tools import sha256_file
from test_kindle import NEW_UA

pytestmark = pytest.mark.skipif(not all(shutil.which(t) for t in ("pdftoppm", "pdfinfo", "pdftotext")), reason="nincs poppler-utils")


def scanned_pdf(path, pages=6, size=(1200, 1600), quality=95):
    """Szövegréteg nélküli PDF: minden oldal egy (zajos, ezért rosszul tömörödő) kép."""
    jpg = path.with_suffix(".jpg")
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(jpg, "JPEG", quality=quality)
    pdf.write_pdf([jpg] * pages, path, "Szkennelt")
    return path


def text_pdf(path):
    text = "Ez egy rendes szoveges oldal, amelyet a konvertalo fel tud dolgozni."
    stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, o)
    xref = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref
    path.write_bytes(out)
    return path


def test_scanned_detection(cfg, tmp_path):
    assert asyncio.run(pdf.is_scanned(cfg, scanned_pdf(tmp_path / "s.pdf", pages=2, size=(300, 400))))
    assert not asyncio.run(pdf.is_scanned(cfg, text_pdf(tmp_path / "t.pdf")))


def test_shrink_pdf_fits_limit(cfg, tmp_path):
    src = scanned_pdf(tmp_path / "s.pdf")
    limit = src.stat().st_size // 4
    dst = tmp_path / "small.pdf"
    assert asyncio.run(pdf.shrink_pdf(cfg, src, dst, limit, "Árvíztűrő"))
    assert dst.stat().st_size <= limit
    assert asyncio.run(pdf.page_count(cfg, dst)) == 6
    assert not list(tmp_path.glob("*.tmp*"))
    # ami sehogy sem fér bele, arra nemet mond, és nem hagy maga után fájlt
    assert not asyncio.run(pdf.shrink_pdf(cfg, src, tmp_path / "x.pdf", 20_000))
    assert not (tmp_path / "x.pdf").exists()


def add_pdf(admin, path, title="Szkennelt"):
    return admin.app.state.lib.add_book(Meta(title=title, authors=["Teszt Elek"]), [(path, "pdf", sha256_file(path))], None)


@pytest.mark.skipif(not shutil.which("ebook-convert"), reason="nincs ebook-convert")
def test_scanned_pdf_downloads_as_kindle_book(admin, cfg, tmp_path):
    bid = add_pdf(admin, scanned_pdf(tmp_path / "s.pdf", pages=3, size=(600, 800)))
    r = admin.get(f"/book/{bid}/download/azw3")
    assert r.status_code == 200 and r.content[60:68] == b"BOOKMOBI"
    assert len(r.content) > 300_000  # benne vannak az oldalképek
    r = admin.get(f"/book/{bid}/download/mobi")
    assert r.status_code == 200 and r.content[60:68] == b"BOOKMOBI"
    assert not list((cfg.data_dir / "cache" / "convert").glob("*.cbz"))


def test_kindle_browser_gets_no_pdf_link(admin, tmp_path):
    bid = add_pdf(admin, scanned_pdf(tmp_path / "s.pdf", pages=1, size=(300, 400)))
    html = admin.get(f"/kindle/book/{bid}", headers={"user-agent": NEW_UA}).text
    assert f"/book/{bid}/download/pdf" not in html
    assert f"/book/{bid}/download/pdf" in admin.get(f"/kindle/book/{bid}").text  # más eszközön a Kindle-nézetben is letölthető


def test_big_pdf_is_shrunk_for_sending(admin, tmp_path, monkeypatch):
    src = scanned_pdf(tmp_path / "s.pdf", pages=6, size=(2400, 3200), quality=100)
    assert src.stat().st_size > 50 * 1024 * 1024
    bid = add_pdf(admin, src, "Nagy")
    sent = []
    import app.main as m
    monkeypatch.setattr(m, "send_mail", lambda s, to, subj, name, data, mime: sent.append((name, len(data), data[:5])))
    admin.post("/admin/settings", data={"smtp_host": "smtp.example", "smtp_port": "587", "smtp_from": "a@example.com"})
    admin.post("/me", data={"display_name": "A", "kindle_email": "a@kindle.com"})
    r = admin.post(f"/book/{bid}/send", follow_redirects=False)
    assert "mail_sent" in r.headers["location"]
    assert sent[0][0].endswith(".pdf") and sent[0][1] < 49 * 1024 * 1024 and sent[0][2] == b"%PDF-"
    assert admin.app.state.lib.file_path(bid, "pdf").stat().st_size > 50 * 1024 * 1024  # az eredeti érintetlen
    admin.post(f"/book/{bid}/send")  # másodszorra a gyorsítótárból megy
    assert len(sent) == 2 and sent[1][1] == sent[0][1]
