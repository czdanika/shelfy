"""Amazon Send to Kindle az Amazon saját (nem nyilvános) protokollján, az stkclient könyvtárral.

E-mail nélkül küld, 200 MB-ig. Nem hivatalos kapcsolat: a Shelfy eszközként jelenik meg a felhasználó
Amazon-fiókjában, és ha az Amazon változtat a szolgáltatáson, leállhat – ezért az e-mail tartalék marad.
A hívások blokkolnak (urllib), szálból kell futtatni őket."""
from __future__ import annotations

import hashlib
import json
import string
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

import stkclient
from stkclient import api, model


def signin() -> tuple[str, str]:
    """(bejelentkezési cím, ellenőrző kód) – a kód a belépés utáni cím beváltásához kell."""
    a = stkclient.OAuth2()
    return a.get_signin_url(), a._verifier


def device_serial(seed: str) -> str:
    """Saját, állandó 32 jegyű eszközsorszám (A–Z, 0–9) a magból – Shelfy-példányonként és felhasználónként más.

    Az stkclient mindenkinek ugyanazzal a beégetett sorszámmal regisztrál. Az Amazon egy sorszámhoz egyetlen
    kulcsot tart érvényben, ezért bárki más regisztrációja (a világon bárhol) érvényteleníti a korábbit, és a
    küldés „Failed to validate DeviceInfoToken” hibával elbukik. Saját sorszámmal a kapcsolat megmarad;
    ugyanannak a felhasználónak az újbóli összekapcsolása csak a saját kulcsát cseréli."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(alphabet[b % len(alphabet)] for b in hashlib.sha256(seed.encode()).digest())


def register(access_token: str, serial: str) -> model.DeviceInfo:
    """Eszköz regisztrálása a megadott sorszámmal (az stkclient azonos hívása, de nem a közös sorszámmal)."""
    q = {"deviceType": "A1K6D1WRW0MALS", "deviceSerialNumber": serial, "pid": "D21NN3GG", "authToken": access_token,
         "authTokenType": "AccessToken", "softwareVersion": "253", "os_version": "MacOSX_10.14.6_x64",
         "device_model": "Shelfy"}
    body = "<?xml version='1.0' encoding='UTF-8'?>\n<request><parameters>" + \
           "".join(f"<{k}>{escape(v)}</{k}>" for k, v in q.items()) + "</parameters></request>"
    req = urllib.request.Request(
        url="https://firs-ta-g7g.amazon.com/FirsProxy/registerDeviceWithToken", data=body.encode(), method="POST",
        headers={"Content-Type": "text/xml", "Expect": "", "Accept-Language": "en-US,*", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req) as r:
            return model.DeviceInfo.from_xml(r.read())
    except urllib.error.HTTPError as e:
        raise api.APIError(str(e), e.read()) from e


def connect(verifier: str, redirect_url: str, serial: str) -> str:
    """A belépés utáni oldal címéből eszközt regisztrál; a visszaadott JSON a tárolandó (titkos) kapcsolat."""
    code = stkclient._parse_authorization_code(redirect_url.strip())
    token = api.token_exchange(code, verifier)
    return stkclient.Client(register(token, serial)).dumps()


def account_name(client_json: str) -> str:
    try:
        d = json.loads(client_json)["device_info"]
        return d.get("name") or d.get("given_name") or "Amazon"
    except (ValueError, KeyError, TypeError, AttributeError):
        return "Amazon"


def send(client_json: str, path: Path, title: str, author: str, fmt: str) -> int:
    """Feltölti a fájlt, és a fiók minden Kindle-eszközére elküldi. Visszaadja az eszközök számát."""
    c = stkclient.Client.loads(client_json)
    serials = [d.device_serial_number for d in c.get_owned_devices()]
    if not serials:
        raise RuntimeError("no Kindle device on the Amazon account")
    c.send_file(path, serials, author=author, title=title, format=fmt)
    return len(serials)


def disconnect(client_json: str) -> None:
    try:
        stkclient.Client.loads(client_json).logout()
    except Exception:  # a helyi kapcsolatot akkor is töröljük, ha az Amazon nem válaszol
        pass
