---
name: immich-s3-ops
description: Check, troubleshoot, or run this repository's Immich originals backup to S3, including daily cron, timeout recovery, alerts, and the pending restore test. Use for backup status requests such as "バックアップチェック" or "check" in this context. Secret-file backup and restore belong to immich-secret-backup.
---

# Immich S3 operations

Read `operations/HANDOFF.md` for the dated handoff and `operations/S3_BACKUP.md` for operating procedures. Resolve these from the repository root, four parents above this skill's `scripts/` helpers. Read `operations/s3-backup.json` for current nonsecret settings; do not copy credentials into commands, tools, responses, or Git.

## Status checks

Run this read-only helper first, from the repository root:

```bash
python3 .agents/skills/immich-s3-ops/scripts/check_status.py
```

- Report the current backup state, verified count/bytes and last-success time, restore-test state, and any unresolved failure. Historical log errors and a failed legacy service are separate from current status.
- `status.json` with `state=complete` records successful comparison of all inventory keys, byte sizes, and Deep Archive storage class. It is not proof that every file was downloaded or hashed. Report its timestamp so old success is not mistaken for current progress.
- `restore-test.json` is a separate, one-image content check. Only `state=complete` plus `sha256_matches=true` is a completed restore test. `restoring` means pending.
- The old initial `immich-s3-backup.service` may remain failed after its two timed-out uploads were successfully recovered. Judge current health from the latest daily result and status, not that historical service alone. Do not restart that old script.
- More than 36 hours since the last verified success needs investigation. A fresh heartbeat alone does not mean the backup succeeded.
- Avoid enumerating the whole bucket for a routine local status check. If the user requests live S3 verification or local evidence conflicts, use the connected AWS tools and check pagination/completeness. A first page of 1,000 objects is not a total count; return the full aggregate without displaying media filenames.

## Requested execution or recovery

Use `python3 operations/immich-s3-daily.py [--dry-run]` for a user-requested run. It refreshes the read-only DB inventory and holds one lock across upload and verification. A status question alone should not start another backup or generate notifications.

- Original images/videos only, selected from DB `originalPath`. Some originals are in `encoded-video`; do not exclude them by directory name. DB dumps and derivatives are excluded.
- Preserve `~/.local/state/immich-s3-backup/`, its lock, inventory, and symlinks while a transfer is active. A busy job skips rather than starting concurrently.
- Sync uploads new/changed files; it never deletes remote originals. Never add `--delete` or copy restored files into a running media tree.
- For `AWS_ERROR_HTTP_RESPONSE_FIRST_BYTE_TIMEOUT`, inspect affected objects before retrying: an error line alone does not establish whether S3 committed the object. Re-run only differences using the wrapper after the active job exits. See the documented classic-client fallback; do not alter private AWS configuration or replay all uploads.
- Confirm retry results with the wrapper's verification. Stop after one requested retry if it fails, preserve evidence, and report the failure rather than looping indefinitely.
- Do not claim a skipped run or an empty error log proves success. Errors during verification also matter.

## Schedules, notifications, and restore

- Backup: daily 05:00 JST (20:00 UTC); local monitoring at minute 35 hourly; restore checker at minute 15 hourly. Preserve other users' cron entries and the existing Minecraft job.
- AWS watchdog: daily 07:00 JST, checks 36-hour success age independently of NAS uptime. Settings and scoped IAM resources are in `operations/cloud/resources.json`.
- SNS email delivery requires a confirmed subscription. Verify live subscription state if asked; do not recreate subscriptions or send test emails during a routine status check. No recipient address belongs in this public repository.
- An already-requested one-image Bulk restore has an hourly checker. Do not submit another paid restore while it is pending. A requested status check may inspect or run the existing checker without requesting a new restore.
- Seven-day multipart cleanup removes only unfinished parts, not completed archives or local originals. Missing objects can be retried on a later sync.
- For `.env` or AWS credential backup/recovery, use the sibling `immich-secret-backup` skill. This skill never reads their values.

When changing scripts or schedules, update the relevant operations guide, validate the affected behavior, and keep the repository and installed cron block consistent. Do not redeploy cloud resources as a side effect of checking status.
