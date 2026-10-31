"""Parancssori eszközök (docker exec … python -m app.…) rootként indulnak; a fájlokat viszont a PUID/PGID
felhasználó nevében kell írniuk, különben a NAS-mappában root tulajdonú fájlok keletkeznének."""
from __future__ import annotations

import os


def drop_root() -> None:
    if os.name != "posix" or os.getuid() != 0 or not os.environ.get("PUID"):
        return
    try:
        gid, uid = int(os.environ.get("PGID", "1000")), int(os.environ["PUID"])
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    except (ValueError, OSError):
        pass
