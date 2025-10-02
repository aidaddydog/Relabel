
#!/usr/bin/env bash
set -euo pipefail
TS=$(date +"%Y%m%d-%H%M%S")
DEST=${1:-/srv/relabel/backups}
mkdir -p "$DEST"
echo "[*] Dumping Postgres..."
pg_dump "${DATABASE_URL}" > "$DEST/relabel-db-$TS.sql"
echo "[*] Archiving data dir..."
tar czf "$DEST/relabel-data-$TS.tar.gz" -C "${RELABEL_DATA:-/srv/relabel/data}" .
echo "[*] Done. Backups at $DEST"
