"""Amazon Send to Kindle (saját protokoll): összekapcsolás a profilban, küldés, e-mail tartalék, 200 MB-os határ."""
from __future__ import annotations

import json
import re

import pytest

import app.main as m
from app.library import Meta
from app.tools import sha256_file
from conftest import make_epub
from test_pdf import scanned_pdf

CLIENT = json.dumps({"version": 1, "device_info": {"name": "Teszt Elek", "adp_token": "t", "device_private_key": "k"}})
REDIRECT = "https://www.amazon.com/gp/sendtokindle?openid.assoc_handle=amzn_device_na&openid.oa2.authorization_code=ABC123&"


def add_book(admin, tmp_path, fmt="epub", path=None):
    path = path or make_epub(tmp_path / "kek.epub")
    return admin.app.state.lib.add_book(Meta(title="A kék város", authors=["Próba Anna"]), [(path, fmt, sha256_file(path))], None)


@pytest.fixture
def linked(admin, monkeypatch):
    """Összekapcsolt Amazon-fiók; a hálózati hívások helyett felvevő."""
    calls = {"connect": [], "send": []}
    monkeypatch.setattr(m.amazon, "connect", lambda verifier, url, serial: calls["connect"].append((verifier, url, serial)) or CLIENT)
    monkeypatch.setattr(m.amazon, "send", lambda client, path, title, author, fmt:
                        calls["send"].append((client, path.stat().st_size, title, author, fmt)) or 1)
    monkeypatch.setattr(m.amazon, "disconnect", lambda client: calls.setdefault("disconnect", []).append(client))
    html = admin.get("/me").text
    assert "https://www.amazon.com/ap/signin?" in html and "Amazon Send to Kindle" in html
    verifier = re.search(r'name="verifier" value="([^"]+)"', html).group(1)
    assert verifier.startswith("enc:")  # az ellenőrző kód titkosítva utazik az űrlapban
    r = admin.post("/me/amazon", data={"url": REDIRECT, "verifier": verifier}, follow_redirects=False)
    assert r.headers["location"] == "/me?msg=saved"
    assert calls["connect"][0][1] == REDIRECT and not calls["connect"][0][0].startswith("enc:")
    assert re.fullmatch(r"[A-Z0-9]{32}", calls["connect"][0][2])  # saját eszközsorszám, nem a könyvtár közös sorszáma
    return calls


def test_connect_is_stored_sealed_and_shown(admin, linked):
    stored = admin.app.state.db.val("SELECT stk_client FROM users WHERE id = 1")
    assert stored.startswith("enc:") and "adp_token" not in stored
    html = admin.get("/me").text
    assert "Teszt Elek" in html and "ap/signin" not in html and 'value="disconnect"' in html


def test_connect_needs_the_redirect_address(admin, monkeypatch):
    monkeypatch.setattr(m.amazon, "connect", lambda v, u, s: pytest.fail("nem szabad az Amazonhoz fordulni"))
    verifier = re.search(r'name="verifier" value="([^"]+)"', admin.get("/me").text).group(1)
    r = admin.post("/me/amazon", data={"url": "https://www.amazon.com/", "verifier": verifier}, follow_redirects=False)
    assert "stk_connect_error" in r.headers["location"]
    r = admin.post("/me/amazon", data={"url": REDIRECT, "verifier": ""}, follow_redirects=False)
    assert "stk_connect_error" in r.headers["location"]
    assert admin.app.state.db.val("SELECT stk_client FROM users WHERE id = 1") is None


def test_sends_through_amazon_without_mail_settings(admin, linked, tmp_path):
    bid = add_book(admin, tmp_path)
    assert f'action="/book/{bid}/send"' in admin.get(f"/book/{bid}").text  # SMTP nélkül is van küldés gomb
    r = admin.post(f"/book/{bid}/send", follow_redirects=False)
    assert r.headers["location"] == f"/book/{bid}?msg=stk_sent"
    client, _size, title, author, fmt = linked["send"][0]
    assert json.loads(client)["device_info"]["name"] == "Teszt Elek"
    assert (title, author, fmt) == ("A kék város", "Próba Anna", "epub")
    ev = admin.app.state.db.one("SELECT kind, params FROM events ORDER BY id DESC LIMIT 1")
    assert ev["kind"] == "sent" and "Amazon" in ev["params"]


def test_amazon_takes_pdf_over_the_mail_limit_unchanged(admin, linked, tmp_path):
    src = scanned_pdf(tmp_path / "s.pdf", pages=6, size=(2400, 3200), quality=100)
    assert src.stat().st_size > 50 * 1024 * 1024
    bid = add_book(admin, tmp_path, "pdf", src)
    r = admin.post(f"/book/{bid}/send", follow_redirects=False)
    assert "stk_sent" in r.headers["location"]
    assert linked["send"][0][1] == src.stat().st_size and linked["send"][0][4] == "pdf"  # 200 MB alatt nem tömörít


def mail_on(admin, monkeypatch):
    sent = []
    monkeypatch.setattr(m, "send_mail", lambda s, to, subj, name, data, mime: sent.append((to, name)))
    admin.post("/admin/settings", data={"smtp_host": "smtp.example", "smtp_port": "587", "smtp_from": "a@example.com"})
    admin.post("/me", data={"display_name": "A", "kindle_email": "a@kindle.com"})
    return sent


def test_both_methods_give_a_dropdown_and_stay_separate(admin, linked, tmp_path, monkeypatch):
    sent = mail_on(admin, monkeypatch)
    bid = add_book(admin, tmp_path)
    html = admin.get(f"/book/{bid}").text
    assert 'name="via" value="amazon"' in html and 'name="via" value="mail"' in html  # legördülő: mindkét mód
    r = admin.post(f"/book/{bid}/send", data={"via": "mail"}, follow_redirects=False)
    assert "mail_sent" in r.headers["location"] and len(sent) == 1 and not linked["send"]
    r = admin.post(f"/book/{bid}/send", data={"via": "amazon"}, follow_redirects=False)
    assert "stk_sent" in r.headers["location"] and len(sent) == 1 and len(linked["send"]) == 1

    def boom(*a):
        raise ValueError("HTTP Error 401")
    monkeypatch.setattr(m.amazon, "send", boom)
    r = admin.post(f"/book/{bid}/send", data={"via": "amazon"}, follow_redirects=False)
    assert "stk_error" in r.headers["location"] and len(sent) == 1  # nincs csendes átváltás e-mailre
    ev = admin.app.state.db.one("SELECT kind, params FROM events ORDER BY id DESC LIMIT 1")
    assert ev["kind"] == "send_failed" and "HTTP Error 401" in ev["params"]


def test_profile_choice_picks_one_method(admin, linked, tmp_path, monkeypatch):
    sent = mail_on(admin, monkeypatch)
    bid = add_book(admin, tmp_path)
    admin.post("/me", data={"display_name": "A", "kindle_email": "a@kindle.com", "kindle_via": "mail"})
    html = admin.get(f"/book/{bid}").text
    assert 'name="via" value="mail"' in html and 'value="amazon"' not in html
    assert 'value="mail" selected' in admin.get("/me").text
    # a kikapcsolt mód kérésre sem megy: a beállított marad
    r = admin.post(f"/book/{bid}/send", data={"via": "amazon"}, follow_redirects=False)
    assert "mail_sent" in r.headers["location"] and len(sent) == 1 and not linked["send"]
    admin.post("/me", data={"display_name": "A", "kindle_email": "a@kindle.com", "kindle_via": "amazon"})
    assert 'value="mail"' not in admin.get(f"/book/{bid}").text
    r = admin.post(f"/book/{bid}/send", follow_redirects=False)
    assert "stk_sent" in r.headers["location"] and len(linked["send"]) == 1
    # a választott mód nincs (már) beállítva: a másik marad használatban
    admin.post("/me/amazon", data={"action": "disconnect"})
    r = admin.post(f"/book/{bid}/send", follow_redirects=False)
    assert "mail_sent" in r.headers["location"] and len(sent) == 2


def test_disconnect(admin, linked, tmp_path):
    r = admin.post("/me/amazon", data={"action": "disconnect"}, follow_redirects=False)
    assert r.headers["location"] == "/me?msg=saved" and len(linked["disconnect"]) == 1
    assert admin.app.state.db.val("SELECT stk_client FROM users WHERE id = 1") is None
    bid = add_book(admin, tmp_path)
    assert "mail_off" in admin.post(f"/book/{bid}/send", follow_redirects=False).headers["location"]


def test_list_view_offers_each_method(admin, linked, tmp_path, monkeypatch):
    bid = add_book(admin, tmp_path)
    # csak Amazon, Kindle e-mail cím nélkül: a listában is van küldés ikon, és megmondja, mivel küld
    html = admin.get("/books").text
    assert html.count(f'action="/book/{bid}/send"') == 1 and 'name="via" value="amazon"' in html
    assert "Küldés Kindle-re: Amazon Send to Kindle" in html
    mail_on(admin, monkeypatch)
    for view in ("/books?layout=grid", "/books?layout=list"):
        html = admin.get(view).text
        assert 'name="via" value="amazon"' in html and 'name="via" value="mail"' in html
        assert "Küldés Kindle-re: E-mail" in html


def test_device_serial_is_own_and_stable():
    a, b = m.amazon.device_serial("titok|stk|1"), m.amazon.device_serial("titok|stk|2")
    assert re.fullmatch(r"[A-Z0-9]{32}", a) and a != b and a == m.amazon.device_serial("titok|stk|1")
    assert a != "ZYSQ37GQ5JQDAIKDZ3WYH6I74MJCVEGG" and m.amazon.device_serial("masik|stk|1") != a


def test_register_sends_the_given_serial(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return (b"<response><adp_token>T</adp_token><device_private_key>K</device_private_key><name>Teszt Elek</name>"
                                b"<device_type>A</device_type><given_name>Elek</given_name><account_pool>Amazon</account_pool>"
                                b"<user_directed_id>U</user_directed_id><user_device_name>Shelfy</user_device_name></response>")

    def fake(req):
        seen.update(url=req.full_url, body=req.data.decode())
        return Resp()
    monkeypatch.setattr(m.amazon.urllib.request, "urlopen", fake)
    info = m.amazon.register("tok<en", "A" * 32)
    assert info.adp_token == "T" and info.name == "Teszt Elek"
    assert seen["url"].endswith("/FirsProxy/registerDeviceWithToken")
    assert f"<deviceSerialNumber>{'A' * 32}</deviceSerialNumber>" in seen["body"] and "<authToken>tok&lt;en</authToken>" in seen["body"]
    assert "ZYSQ37GQ" not in seen["body"]
