#!/bin/bash
# Run as root on the host after immich-hdd-copy.service has started.
set -Eeuo pipefail
umask 077
cd /home/ms/packages/nas/immich-app
exec 9>/run/immich-hdd-cutover.lock
flock -n 9
uuid=b1ffb942-2296-412d-9b00-f7e5a03548d0
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/mnt/photo-backup/migration-backups/$stamp
compose=(/usr/bin/docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml)
stopped=0
rollback() {
    result=$?
    trap - ERR
    echo "Migration failed (exit $result). Original media has been retained."
    if (( stopped )); then
        systemctl stop immich.service || true
        systemctl disable immich.service || true
        cp -a "$backup/env" .env
        cp -a "$backup/docker-compose.yml" docker-compose.yml
        cp -a "$backup/docker-compose.cloudflare.yml" docker-compose.cloudflare.yml
        "${compose[@]}" up -d --pull never --no-build || true
    fi
    exit "$result"
}
trap rollback ERR
while systemctl is-active --quiet immich-hdd-copy.service; do sleep 10; done
status=$(systemctl show immich-hdd-copy.service -p ExecMainStatus --value)
[[ "$status" = 0 || "$status" = 24 ]]
mountpoint -q /mnt/photo-backup
test "$(findmnt -n -o UUID --mountpoint /mnt/photo-backup)" = "$uuid"
test -d library/upload
test -d /mnt/photo-backup/immich/library
test "$(readlink -f library)" = /home/ms/packages/nas/immich-app/library
test ! -e operations/cutover-complete
mkdir -m 0700 "$backup"
cp -a .env "$backup/env"
cp -a docker-compose.yml docker-compose.cloudflare.yml "$backup/"
cp -a /etc/fstab "$backup/fstab"
echo "Stopping media writers for final synchronization. Backup: $backup"
stopped=1
"${compose[@]}" stop --timeout 120 cloudflared immich-server immich-machine-learning
docker exec immich_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM asset;"' > "$backup/asset-count.before"
docker exec immich_postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' | gzip > "$backup/database.sql.gz"
gzip -t "$backup/database.sql.gz"
"${compose[@]}" stop --timeout 120
rsync -aHAX --numeric-ids --delete-delay --stats library/ /mnt/photo-backup/immich/
rsync -aHAXn --numeric-ids --delete --itemize-changes library/ /mnt/photo-backup/immich/ > "$backup/rsync-verification.txt"
test ! -s "$backup/rsync-verification.txt"
sync -f /mnt/photo-backup
python3 - <<'PY'
from pathlib import Path
import re
p = Path('.env')
s = p.read_text()
s, count = re.subn(r'^UPLOAD_LOCATION=.*$', 'UPLOAD_LOCATION=/mnt/photo-backup/immich', s, flags=re.M)
assert count == 1
p.write_text(s)
for name in ('docker-compose.yml', 'docker-compose.cloudflare.yml'):
    p = Path(name)
    s = p.read_text()
    s = re.sub(r'^    restart: .+$', '    restart: "no" # Restart and mount ordering are managed by immich.service', s, flags=re.M)
    if name == 'docker-compose.yml':
        s = s.replace('      - ${UPLOAD_LOCATION}:/data', '''      - type: bind
        source: ${UPLOAD_LOCATION}
        target: /data
        bind:
          create_host_path: false''')
    p.write_text(s)
PY
"${compose[@]}" config --quiet
install -D -m 0755 operations/immich-storage-check /usr/local/libexec/immich-storage-check
install -m 0644 operations/immich.service /etc/systemd/system/immich.service
/usr/local/libexec/immich-storage-check
systemd-analyze verify --generators=yes /etc/systemd/system/immich.service
systemctl daemon-reload
# Once clients can write to the new disk, keep that disk authoritative even
# if a subsequent health check fails. Reverting could orphan new uploads.
stopped=0
systemctl enable --now immich.service
healthy=0
for attempt in {1..90}; do
    if systemctl is-active --quiet immich.service &&
       docker inspect immich_server immich_machine_learning immich_postgres immich_redis --format '{{.State.Health.Status}}' | awk 'BEGIN{ok=1; n=0} {n++; if ($0 != "healthy") ok=0} END{exit !(ok && n==4)}' &&
       curl --fail --silent http://127.0.0.1:2283/api/server/ping > "$backup/ping.json"; then
        healthy=1
        break
    fi
    sleep 5
done
test "$healthy" = 1
test "$(docker inspect immich_server --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')" = /mnt/photo-backup/immich
docker exec immich_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM asset;"' > "$backup/asset-count.after"
# New client uploads may arrive as soon as the server becomes healthy.
test "$(cat "$backup/asset-count.after")" -ge "$(cat "$backup/asset-count.before")"
"${compose[@]}" ps
printf '%s\n' "$backup" > operations/cutover-complete
stopped=0
echo 'Cutover verified. Original SSD media retained pending final validation and cleanup.'
