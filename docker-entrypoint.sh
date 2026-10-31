#!/bin/sh
# NAS-barát indítás: rootként indulva a PUID/PGID felhasználóra vált, és rendbe teszi a mappák jogosultságát,
# így a felcsatolt NAS-mappákba (pl. /Volume1/docker/shelfy) gond nélkül tud írni.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
if [ "$(id -u)" = "0" ]; then
  for d in "${DATA_DIR:-/data}" "${LIBRARY_DIR:-${DATA_DIR:-/data}/library}" ${INGEST_DIR:+"$INGEST_DIR"}; do
    mkdir -p "$d"
    # csak ha a mappa nem a megadott felhasználóé (egy nagy könyvtárnál nem járjuk be feleslegesen)
    if [ "$(stat -c %u "$d")" != "$PUID" ]; then
      chown -R "$PUID:$PGID" "$d" 2>/dev/null || echo "shelfy: cannot change owner of $d – check the folder permissions on the host" >&2
    fi
  done
  export HOME=/tmp
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
