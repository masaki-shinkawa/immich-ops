# S3 originals backup

## Scope

- Bucket: `immich-backup-362919012960-us-east-1-an`, region `us-east-1`.
- CLI profile: `immich-backup` (credentials outside this repository).
- Objects: `originals/<Immich path relative to /data>`.
- Class: `DEEP_ARCHIVE`, uploaded directly.
- Original images and videos only; no DB dumps, thumbnails, or generated derivatives.

The DB is queried read-only to find originals and their recorded sizes. Some originals are referenced from `encoded-video`; use DB paths rather than excluding a directory by name.
The inventory and symlink staging tree live in `~/.local/state/immich-s3-backup`, outside Git. The symlinks avoid duplicating the media files; the AWS CLI uploads their target contents.

## Status checks and initial transfer history

For routine checks, run the nonprinting status summary from the repository root:

```bash
python3 .agents/skills/immich-s3-ops/scripts/check_status.py
```

Read [HANDOFF.md](HANDOFF.md) for the dated operational snapshot. Always refresh live state before reporting it.

The first run used `~/.local/libexec/immich-s3-backup.py` in a transient user service. That historical service may remain failed after recovery succeeded. The latest `status.json` and daily-run exit code determine current backup status. Do not restart the old installed script. Never replace any active transfer's inventory or staging tree.

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

## Monitoring and alerts

- Every real daily run publishes a small Standard-class JSON object at `monitor/latest.json`; a separate hourly cron job also reports the initial transfer's state and detects failures outside the wrapper.
- The heartbeat contains only timestamps and backup/restore state. No file names, media, credentials, DB contents, or raw error messages are sent.
- Failures send an SNS alert immediately when observed locally. Repeated hourly reports of the same failure are suppressed.
- An independent Lambda watchdog, scheduled by EventBridge Scheduler at **07:00 JST daily**, reads the heartbeat. It alerts for failed backup/restore state, unreadable status, or **more than 36 hours without a successful backup**. It works even when the NAS is off. Because this check is daily, detection can occur up to a day after the 36-hour threshold.
- Heartbeat freshness alone is not success: the last completed verification time is preserved. A running monitor cannot conceal a broken daily backup.
- Notification delivery requires an email subscription to the `immich-backup-alerts` SNS topic and the recipient's confirmation. A topic with no confirmed subscriber does not deliver email. The recipient address is not stored in this public repository.
- Local monitoring logs: `~/.local/state/immich-s3-backup/monitor.log`. AWS Lambda logs are retained for 14 days.
- Source: `operations/cloud/backup_watchdog.py`; deployed resource names, scoped IAM policies, and settings: `operations/cloud/resources.json`. Credentials stay out of both.

The monitor adds Standard-class heartbeat PUTs (roughly hourly), a small amount of Standard storage, one daily Lambda invocation/Scheduler execution, SNS notifications when needed, and minimal execution logs. These are separate from media storage and the daily LIST cost. It does not retrieve archived images.

## One-time restore verification

A small already-uploaded image was requested with Bulk retrieval and a one-day temporary restore window. It runs independently of the full backup. An hourly cron job checks `restore-test.json`, waits up to 72 hours, then downloads the restored sample into a private temporary directory, verifies byte length and SHA-256, and removes the local download. This does not modify original media. The S3 restored copy expires automatically; the original archive stays intact.

```bash
python3 operations/immich-s3-restore-test.py
```

State is stored privately in `~/.local/state/immich-s3-backup/restore-test.json`. The checker exits without S3 calls after `complete` or `failed`. Successful verification sends a one-time SNS result message; failures are picked up by the monitor. A pending restore is not a completed test.

## Incomplete multipart cleanup

The bucket has the rule in `operations/s3-lifecycle.json`: abort incomplete uploads under `originals/` after seven days. Only unfinished parts are removed. Completed archives and local originals are unaffected. A subsequent daily sync retries missing files from the beginning when their local original is still present.


## Timeout recovery used during initial transfer

Two videos produced `AWS_ERROR_HTTP_RESPONSE_FIRST_BYTE_TIMEOUT`. `HeadObject` confirmed they were absent at that time. The one-time `immich-s3-recover.service` waited for the initial transfer to finish and invoked the daily wrapper with a private, nonsecret AWS configuration overlay selecting `preferred_transfer_client = classic`, standard retries (10 attempts), concurrency 4, and 64 MB multipart chunks. It completed at 2026-10-21T16:34:10Z. The subsequent daily check also passed.

The overlay was `~/.local/state/immich-s3-backup/recovery-aws-config`; credentials still came from the existing shared credentials file. This was a one-time environment override, not a permanent change to `~/.aws/config` or the daily cron job.

For a requested retry after a similar error, first check affected S3 keys and wait for the current transfer to release the lock. If the existing overlay remains appropriate, invoke:

```bash
AWS_CONFIG_FILE="$HOME/.local/state/immich-s3-backup/recovery-aws-config" \
  python3 operations/immich-s3-daily.py
```

Do not recreate the historical transient service to check status, blindly upload every object, or loop retries indefinitely. If the requested retry fails, preserve the current logs and report the unresolved error. The daily wrapper's final S3 verification, not the absence of new error lines, establishes success.
