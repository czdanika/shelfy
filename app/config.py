"""Beállítások: környezeti változók + az adatbázisban tárolt (admin felületen módosítható) értékek."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "igen", "on")


@dataclass
class Config:
    data_dir: Path
    library_dir: Path
    ingest_dir: Path | None
    secret_key: str
    default_language: str = "en"
    ebook_convert: str = "ebook-convert"
    ebook_meta: str = "ebook-meta"
    convert_timeout: int = 300
    admin_user: str | None = None
    admin_password: str | None = None
    providers: tuple[str, ...] | None = None  # None: az alapnyelvhez illő kezdő lista (a felületen átállítható)
    online_lookup: bool = True


def load_config() -> Config:
    data = Path(os.environ.get("DATA_DIR", "/data"))
    data.mkdir(parents=True, exist_ok=True)
    lib = Path(os.environ.get("LIBRARY_DIR", str(data / "library")))
    lib.mkdir(parents=True, exist_ok=True)
    ingest = os.environ.get("INGEST_DIR")
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        kf = data / "secret.key"
        if kf.exists():
            secret = kf.read_text().strip()
        else:
            secret = secrets.token_urlsafe(48)
            kf.write_text(secret)
            try:
                kf.chmod(0o600)
            except OSError:
                pass
    env_prov = os.environ.get("METADATA_PROVIDERS")
    providers = tuple(p.strip() for p in env_prov.split(",") if p.strip()) if env_prov else None
    return Config(
        data_dir=data,
        library_dir=lib,
        ingest_dir=Path(ingest) if ingest else None,
        secret_key=secret,
        default_language=os.environ.get("DEFAULT_LANGUAGE", "en"),
        ebook_convert=os.environ.get("EBOOK_CONVERT", "ebook-convert"),
        ebook_meta=os.environ.get("EBOOK_META", "ebook-meta"),
        convert_timeout=int(os.environ.get("CONVERT_TIMEOUT", "300")),
        admin_user=os.environ.get("ADMIN_USER") or None,
        admin_password=os.environ.get("ADMIN_PASSWORD") or None,
        providers=providers,
        online_lookup=_bool(os.environ.get("ONLINE_LOOKUP"), True),
    )
