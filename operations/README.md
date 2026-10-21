# Immich HDD storage and automatic startup

Media: `/mnt/photo-backup/immich` on ext4 UUID
`b1ffb942-2296-412d-9b00-f7e5a03548d0` (Seagate ST1000DM010).
PostgreSQL remains at `/home/ms/packages/nas/immich-app/postgres` on the SSD.
The container media path remains `/data`; database asset paths do not need editing.

`/etc/fstab` mounts the HDD by UUID with `nofail` and a 15 second device timeout.
The operating system can boot when the HDD is absent. Immich requires the mounted
HDD and checks its UUID and Immich marker before startup. Docker's bind mount has
`create_host_path: false` so a missing media directory is an error.

`immich.service` manages all five containers, including Cloudflare Tunnel. It is
enabled for both normal boot and Docker startup, and stops with Docker or the
media mount. Compose runs in the foreground with `--abort-on-container-exit`;
systemd restarts the stack after an unexpected exit. Container restart policies
are disabled to avoid bypassing the disk check during Docker startup.

## Operation

```bash
sudo systemctl status immich.service
sudo systemctl restart immich.service
sudo systemctl stop immich.service
journalctl -u immich.service -n 100 --no-pager
findmnt /mnt/photo-backup
df -h / /mnt/photo-backup
curl --fail http://127.0.0.1:2283/api/server/ping
```

Use systemd for starting/stopping this deployment. After reconnecting an absent
HDD, run `sudo systemctl start immich.service`. To intentionally keep Immich
stopped across reboots, disable the service as well.

The service uses both `docker-compose.yml` and `docker-compose.cloudflare.yml`.
After a Compose file upgrade, preserve the disabled container restart policies
and the media bind mount's `create_host_path: false`. The startup command does
not pull images automatically; image updates should be performed separately.

## Migration records

The initial copy runs as `immich-hdd-copy.service`, followed by
`immich-hdd-cutover.service`. These are transient migration jobs, not boot jobs.
The cutover job stops writers, saves a compressed database dump, synchronizes
the final differences, checks for remaining file/metadata differences, and
verifies container health, media mount, API response, and database asset count.
If cutover fails before starting the new deployment, it attempts to restore the
previous Compose and environment configuration and start the SSD deployment.
Once the new deployment starts, its disk remains authoritative so that client
uploads cannot be orphaned by an automatic return to the old disk.

Pre-migration configuration is saved under `backups/`. Database and cutover
records are saved under `/mnt/photo-backup/migration-backups/<timestamp>/`,
restricted to root. `operations/cutover-complete` identifies the successful
record directory. After validating the restart, file metadata, sampled SHA-256
content hashes, and database asset count, the original SSD media was removed.
`library` is now a compatibility symlink to `/mnt/photo-backup/immich`.
`operations/cleanup-complete` records completion of this step. A copy of the
pre-migration database dump is also retained in `backups/` on the SSD.
The scripts are records of this migration; do not rerun them on a completed
deployment.

This HDD is the primary media store after migration. A database dump on this
same disk does not constitute an independent backup of photos.

References: [Immich backup and restore](https://docs.immich.app/administration/backup-and-restore),
[Docker restart policy behavior](https://docs.docker.com/engine/containers/start-containers-automatically/).
