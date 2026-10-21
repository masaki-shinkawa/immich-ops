# S3 originals backup

## Scope

- Bucket: `immich-backup-362919012960-us-east-1-an`, region `us-east-1`.
- CLI profile: `immich-backup` (credentials outside this repository).
- Objects: `originals/<Immich path relative to /data>`.
- Class: `DEEP_ARCHIVE`, uploaded directly.
- Original images and videos only; no DB dumps, thumbnails, or generated derivatives.

The DB is queried read-only to find originals and their recorded sizes. Some originals are referenced from `encoded-video`; use DB paths rather than excluding a directory by name.
The inventory and symlink staging tree live in `~/.local/state/immich-s3-backup`, outside Git. The symlinks avoid duplicating the media files; the AWS CLI uploads their target contents.

## Initial backup already running

The first run uses the installed script at `~/.local/libexec/immich-s3-backup.py`, launched as a transient user service. Do not replace its inventory, staging tree, or installed script while it runs.

```bash
systemctl --user status immich-s3-backup
cat ~/.local/state/immich-s3-backup/status.json
tail ~/.local/state/immich-s3-backup/upload.log
```

`state=complete` means all inventory keys, sizes, and Deep Archive classes passed S3 readback. It does not mean content was downloaded or compared with a full-file hash. `state=failed` requires investigation before retrying.

## Daily cron and manual runs

The user crontab checks once daily at **05:00 JST**, represented as `0 20 * * *` on this UTC host.
The tracked block is in `operations/immich-s3-backup.cron`; other crontab entries are preserved.
Cron must be running at the scheduled time. If the server is off then, the next check is the following day.

The wrapper refreshes the read-only DB inventory, reconciles the local symlink staging tree, runs `aws s3 sync`, and verifies S3 keys/sizes/storage classes. One exclusive lock spans the entire sequence. If the initial legacy transfer or another backup is running, the check is skipped without changing its state.

Each run compares local file sizes and modification times against S3's actual listing. Only new or changed files upload. No `--delete` is used: originals removed locally or moved to trash remain on S3. Stale staging symlinks are removed automatically without deleting their source files.

Run from the repository root:

```bash
python3 operations/immich-s3-daily.py --dry-run
python3 operations/immich-s3-daily.py
```

The daily wrapper writes dated UTC logs to `~/.local/state/immich-s3-backup/logs/YYYY-MM-DD.log` with private permissions. `status.json` contains the most recent actual upload/verification status; skipped checks are recorded in the log and leave the active status unchanged. Logs are not automatically deleted.

```bash
crontab -l
cat ~/.local/state/immich-s3-backup/status.json
tail ~/.local/state/immich-s3-backup/logs/*.log
```

For individual maintenance steps, `immich-s3-inventory.py` and `immich-s3-backup.py [--dry-run]` still work, each with the same lock. Use the daily wrapper for normal operation so inventory and upload cannot interleave.

## S3 settings and comparison costs

`operations/s3-backup.json` holds source path, bucket, region, AWS profile name, object prefix, and storage class. These values contain no access keys and are tracked in Git. Credentials remain in `~/.aws/credentials`; the separate secret-file skill backs them up to the user-designated Google Sheet.

Local manifests, staging links, and run status are private runtime files outside Git. They are not a substitute for the S3 listing. Every real run lists before syncing and again for verification, without downloading archived media.

For the initial 14,641 objects, each listing needs about 15 LIST requests. At the verified us-east-1 rate of $0.005/1,000 LIST requests, two listings daily cost about $0.0045 per 30 days, excluding retries and growth. Changed uploads, storage, and eventual restores are billed separately. See the current [S3 pricing](https://aws.amazon.com/s3/pricing/) before recalculating.

## Costs and recovery

Deep Archive has a 180-day minimum billable duration. Replacing/deleting an archive earlier incurs the remaining minimum-duration charge. Restores require an explicit restore request, a waiting period, retrieval requests/data charges, temporary restored-copy storage, and internet download charges.
Versioning retains overwritten versions and can increase storage usage.

Restore original objects to a separate local directory. Since the DB is excluded, original files can be reimported into Immich, but albums, favorites, face assignments, and other DB-only state are not restored. Do not write restored files directly into a running Immich media tree.

References: [S3 pricing](https://aws.amazon.com/s3/pricing/), [archival storage](https://docs.aws.amazon.com/AmazonS3/latest/userguide/archival-storage.html).
