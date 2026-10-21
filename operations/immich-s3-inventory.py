#!/usr/bin/env python3
"""Build an original-file inventory from read-only Immich database queries."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from immich_s3_common import STATE, CONFIG, BackupBusy, backup_lock


def build_inventory(source, bucket):
    os.umask(0o077)
    source = source.resolve(strict=True)
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
    manifest = {'source': str(source), 'bucket': bucket,
                'objects': [{'path': p, 'bytes': size} for p, size in objects.items()]}
    tmp = STATE / 'manifest.tmp'
    tmp.write_text(json.dumps(manifest))
    tmp.chmod(0o600)
    tmp.replace(STATE / 'manifest.json')
    print(json.dumps({'files': len(objects), 'bytes': sum(objects.values()),
                      'manifest': str(STATE / 'manifest.json')}))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(CONFIG['source']))
    parser.add_argument('--bucket', default=CONFIG['bucket'])
    args = parser.parse_args()
    with backup_lock():
        return build_inventory(args.source, args.bucket)


def entrypoint():
    try:
        return main()
    except BackupBusy as exc:
        print(f'Skipped: {exc}')
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(entrypoint())
