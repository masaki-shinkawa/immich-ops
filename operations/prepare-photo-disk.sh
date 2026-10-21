#!/bin/bash
set -euo pipefail
cd /home/ms/packages/nas/immich-app
uuid=b1ffb942-2296-412d-9b00-f7e5a03548d0
test "$(blkid -s UUID -o value /dev/sda1)" = "$uuid"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 0755 /mnt/photo-backup
cp -a /etc/fstab "backups/fstab.before-hdd.$stamp"
cp -a .env "backups/env.before-hdd.$stamp"
chmod 600 "backups/env.before-hdd.$stamp"
cp -a docker-compose.yml "backups/docker-compose.before-hdd.$stamp.yml"
cp -a docker-compose.cloudflare.yml "backups/docker-compose.cloudflare.before-hdd.$stamp.yml"
if ! grep -q "$uuid" /etc/fstab; then
    printf '\n# Immich media disk\nUUID=%s /mnt/photo-backup ext4 defaults,nofail,x-systemd.device-timeout=15s 0 2\n' "$uuid" >> /etc/fstab
fi
findmnt --verify --tab-file /etc/fstab
systemctl daemon-reload
systemctl start 'mnt-photo\x2dbackup.mount'
test "$(findmnt -n -o UUID --mountpoint /mnt/photo-backup)" = "$uuid"
install -d -o 1000 -g 1000 -m 0755 /mnt/photo-backup/immich /mnt/photo-backup/migration-backups
df -h / /mnt/photo-backup
