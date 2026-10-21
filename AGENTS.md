# Immich operations

- Read `README.md` and the relevant operations guide before changing deployment behavior.
- Live media, PostgreSQL data, `.env`, AWS credentials, migration backups, logs, and runtime manifests stay outside Git.
- For backing up, retrieving, updating, or restoring `.env` or AWS configuration without exposing values, use `.agents/skills/immich-secret-backup/SKILL.md`. Never print, diff, or copy secrets into model context or commits.
- Do not interrupt an active S3 backup or replace its manifest/staging tree. Repository copies can be edited; deploy them after the current job completes.
- `operations/prepare-photo-disk.sh` and `operations/cutover-photo-disk.sh` are historical migration records. Do not rerun them on this completed deployment.
- No backup timer is configured. Do not enable a schedule without a user request.
