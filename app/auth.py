"""Felhasználók, jelszavak, munkamenet, jogosultságok."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

ROLES = ("admin", "editor", "reader")
PERMS = ("can_upload", "can_edit", "can_download", "can_delete")
ROLE_DEFAULTS = {
    "admin": dict(can_upload=1, can_edit=1, can_download=1, can_delete=1),
    "editor": dict(can_upload=1, can_edit=1, can_download=1, can_delete=0),
    "reader": dict(can_upload=0, can_edit=0, can_download=1, can_delete=0),
}
PBKDF2_ITER = 260_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITER)
    return f"pbkdf2-{PBKDF2_ITER}-{salt.hex()}-{dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt, want = stored.split("-", 3)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


@dataclass
class User:
    id: int
    username: str
    display_name: str
    role: str
    can_upload: bool
    can_edit: bool
    can_download: bool
    can_delete: bool
    kindle_email: str | None
    language: str | None
    password_hash: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_guest(self) -> bool:
        return self.role == "guest"

    @property
    def initial(self) -> str:
        return (self.display_name or self.username or "?")[:1].upper()

    def can(self, perm: str) -> bool:
        return self.is_admin or (not self.is_guest and bool(getattr(self, perm, False)))

    @classmethod
    def guest(cls) -> "User":
        """Bejelentkezés nélküli látogató, ha a vendég-böngészés be van kapcsolva: csak nézelődhet."""
        return cls(id=0, username="", display_name="", role="guest", can_upload=False, can_edit=False,
                   can_download=False, can_delete=False, kindle_email=None, language=None, password_hash="")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(("shelfy|" + self.password_hash).encode()).hexdigest()[:16]

    @classmethod
    def from_row(cls, r) -> "User":
        return cls(id=r["id"], username=r["username"], display_name=r["display_name"], role=r["role"],
                   can_upload=bool(r["can_upload"]), can_edit=bool(r["can_edit"]),
                   can_download=bool(r["can_download"]), can_delete=bool(r["can_delete"]),
                   kindle_email=r["kindle_email"], language=r["language"], password_hash=r["password_hash"])


class Sessions:
    """Titkosított, aláírt süti (Fernet)."""

    def __init__(self, secret: str) -> None:
        self.f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))

    def dump(self, data: dict) -> str:
        return self.f.encrypt(json.dumps(data).encode()).decode()

    def seal(self, text: str | None) -> str | None:
        """Titok (pl. SMTP jelszó) titkosítva tárolásra; 'enc:' előtaggal."""
        return "enc:" + self.f.encrypt(text.encode()).decode() if text else None

    def unseal(self, stored: str | None) -> str | None:
        if not stored:
            return None
        if not stored.startswith("enc:"):  # régi, titkosítatlan érték
            return stored
        try:
            return self.f.decrypt(stored[4:].encode()).decode()
        except InvalidToken:
            return None

    def load(self, token: str | None, max_age: int = 60 * 60 * 24 * 365) -> dict:
        if not token:
            return {}
        try:
            return json.loads(self.f.decrypt(token.encode(), ttl=max_age))
        except (InvalidToken, ValueError):
            return {}


class Throttle:
    """Egyszerű brute-force védelem: 5 hiba / 10 perc / IP."""

    def __init__(self, limit: int = 5, window: int = 600) -> None:
        self.limit, self.window = limit, window
        self.fails: dict[str, list[float]] = {}

    def blocked(self, key: str) -> bool:
        now = time.time()
        self.fails[key] = [t for t in self.fails.get(key, []) if now - t < self.window]
        return len(self.fails[key]) >= self.limit

    def fail(self, key: str) -> None:
        self.fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        self.fails.pop(key, None)
