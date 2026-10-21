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

## Future manual runs

Run from the repository root, only after the current service has stopped:

```bash
python3 operations/immich-s3-inventory.py
python3 operations/immich-s3-backup.py --dry-run
python3 operations/immich-s3-backup.py
```

The inventory validates every local original against the DB's size. The upload runner rechecks files, follows only the prepared staging links, and syncs without deleting remote objects. Repeated runs skip unchanged files; path/size/timestamp changes may upload new objects or versions.

The repository scripts share a nonblocking lock. The initial deployed version predates this lock, so wait for its service to finish before using repository scripts.
If a refreshed inventory removes old local paths, the inventory command refuses stale staging. After the service stops, archive the local `originals` staging directory outside the repository before rebuilding it; it contains symlinks, not media copies. Do not delete media to resolve staging errors.

No recurring schedule is enabled. A fresh inventory is necessary to include newly added originals.

## Costs and recovery

Deep Archive has a 180-day minimum billable duration. Replacing/deleting an archive earlier incurs the remaining minimum-duration charge. Restores require an explicit restore request, a waiting period, retrieval requests/data charges, temporary restored-copy storage, and internet download charges.
Versioning retains overwritten versions and can increase storage usage.

Restore original objects to a separate local directory. Since the DB is excluded, original files can be reimported into Immich, but albums, favorites, face assignments, and other DB-only state are not restored. Do not write restored files directly into a running Immich media tree.

References: [S3 pricing](https://aws.amazon.com/s3/pricing/), [archival storage](https://docs.aws.amazon.com/AmazonS3/latest/userguide/archival-storage.html).
