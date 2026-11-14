"""Amazon Send to Kindle az Amazon saját (nem nyilvános) protokollján, az stkclient könyvtárral.

E-mail nélkül küld, 200 MB-ig. Nem hivatalos kapcsolat: a Shelfy eszközként jelenik meg a felhasználó
Amazon-fiókjában, és ha az Amazon változtat a szolgáltatáson, leállhat – ezért az e-mail tartalék marad.
A hívások blokkolnak (urllib), szálból kell futtatni őket."""
from __future__ import annotations

import json
from pathlib import Path

import stkclient


def signin() -> tuple[str, str]:
    """(bejelentkezési cím, ellenőrző kód) – a kód a belépés utáni cím beváltásához kell."""
    a = stkclient.OAuth2()
    return a.get_signin_url(), a._verifier


def connect(verifier: str, redirect_url: str) -> str:
    """A belépés utáni oldal címéből eszközt regisztrál; a visszaadott JSON a tárolandó (titkos) kapcsolat."""
    a = stkclient.OAuth2()
    a._verifier = verifier
    return a.create_client(redirect_url.strip()).dumps()


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
