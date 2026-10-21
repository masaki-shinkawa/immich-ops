#!/usr/bin/env python3
"""Build an original-file inventory from read-only Immich database queries."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

STATE = Path.home() / '.local/state/immich-s3-backup'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path('/mnt/photo-backup/immich'))
    parser.add_argument('--bucket', default='immich-backup-362919012960-us-east-1-an')
    args = parser.parse_args()
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet',
                             'immich-s3-backup.service'], check=False)
    if active.returncode == 0:
        raise RuntimeError('Backup is running; do not replace its inventory')
    lock = (STATE / 'backup.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    source = args.source.resolve(strict=True)
    sql = '''SELECT coalesce(json_agg(t), '[]'::json) FROM (
        SELECT a."originalPath" AS path, e."fileSizeInByte" AS bytes
        FROM asset a LEFT JOIN asset_exif e ON e."assetId" = a.id
        WHERE a."deletedAt" IS NULL ORDER BY a."originalPath"
    ) t;'''
    query = subprocess.run(
        ['docker', 'exec', '-i', 'immich_postgres', 'sh', '-c',
         'PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=30000" '
         'psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
        input=sql, capture_output=True, text=True, check=True)
    rows = json.loads(query.stdout)
    if not rows:
        raise ValueError('No original files found')
    objects = {}
    for row in rows:
        rel = Path(row['path']).relative_to('/data')
        if '..' in rel.parts or row['bytes'] is None:
            raise ValueError('Invalid path or missing file size')
        local = source / rel
        if not local.resolve(strict=True).is_relative_to(source):
            raise ValueError('Original file outside media root')
        if not local.is_file() or local.stat().st_size != row['bytes']:
            raise ValueError('Original file missing or size does not match DB')
        if str(rel) in objects and objects[str(rel)] != row['bytes']:
            raise ValueError('Conflicting duplicate source path')
        objects[str(rel)] = row['bytes']
    # The upload runner checks that staging links exactly match this inventory.
    stage = STATE / 'originals'
    if stage.exists():
        staged = {str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_symlink()}
        if staged - objects.keys():
            raise RuntimeError('Staging contains old paths; archive the staging directory before refreshing')
    manifest = {'source': str(source), 'bucket': args.bucket,
                'objects': [{'path': p, 'bytes': size} for p, size in objects.items()]}
    tmp = STATE / 'manifest.tmp'
    tmp.write_text(json.dumps(manifest))
    tmp.chmod(0o600)
    tmp.replace(STATE / 'manifest.json')
    print(json.dumps({'files': len(objects), 'bytes': sum(objects.values()),
                      'manifest': str(STATE / 'manifest.json')}))
    return 0


def entrypoint():
    try:
        return main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(entrypoint())
