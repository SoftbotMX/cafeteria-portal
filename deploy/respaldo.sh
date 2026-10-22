#!/usr/bin/env bash
# Respaldo diario de la BD del portal (cron del usuario en el VPS). Guarda 14 días.
set -euo pipefail
umask 077  # datos de tutores y hashes de contraseñas: sólo el dueño (Codex)
AQUI="$(cd "$(dirname "$0")" && pwd)"
DIR="${RESPALDOS:-$HOME/respaldos/cafeteria-portal}"
mkdir -p "$DIR" && chmod 700 "$DIR"
archivo="$DIR/portal_$(date +%Y%m%d_%H%M%S).dump"
docker compose -p cafeteria-portal -f "$AQUI/docker-compose.yml" exec -T db \
    pg_dump -U portal -Fc portal > "$archivo.tmp"
mv "$archivo.tmp" "$archivo"
find "$DIR" -name 'portal_*.dump' -mtime +14 -delete
echo "respaldo: $archivo"
