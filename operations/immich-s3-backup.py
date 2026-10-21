#!/usr/bin/env python3
"""Upload the validated original-file inventory; verify all remote sizes and classes."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from immich_s3_common import STATE, CONFIG, BackupBusy, backup_lock
AWS = str(Path.home() / '.local/bin/aws')
PROFILE = CONFIG['profile']

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def status(**values):
    tmp = STATE / 'status.tmp'
    tmp.write_text(json.dumps({'updated_at': now(), **values}, indent=2))
    tmp.chmod(0o600)
    tmp.replace(STATE / 'status.json')

def upload_backup(dry_run=False):
    os.umask(0o077)
    data = json.loads((STATE / 'manifest.json').read_text())
    source = Path(data['source']).resolve()
    stage = STATE / 'originals'
    if stage.is_symlink():
        raise ValueError('Staging root must not be a symlink')
    stage.mkdir(exist_ok=True)
    wanted = {item['path'] for item in data['objects']}
    for path in stage.rglob('*'):
        if path.is_symlink():
            if str(path.relative_to(stage)) not in wanted:
                path.unlink()  # Remove only the staging link; preserve the media target.
        elif not path.is_dir():
            raise ValueError('Unexpected regular file in staging')
    expected = {}
    for item in data['objects']:
        rel = Path(item['path'])
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Invalid inventory path')
        local = source / rel
        if not local.resolve().is_relative_to(source):
            raise ValueError('Source outside media root')
        if not local.is_file() or local.stat().st_size != item['bytes']:
            raise ValueError('Source missing or size changed; refresh inventory')
        link = stage / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if link.readlink() != local:
                raise ValueError('Unexpected staging link')
        else:
            link.symlink_to(local)
        expected[CONFIG['prefix'] + str(rel)] = item['bytes']
    staged = {str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_symlink()}
    if staged != {item['path'] for item in data['objects']}:
        raise ValueError('Staging inventory mismatch')
    command = [AWS, 's3', 'sync', str(stage), 's3://' + data['bucket'] + '/' + CONFIG['prefix'],
               '--profile', PROFILE, '--region', CONFIG['region'], '--storage-class',
               CONFIG['storage_class'], '--follow-symlinks', '--no-progress', '--no-cli-pager']
    if dry_run:
        command.append('--dryrun')
        subprocess.run(command, check=True)
        return 0
    status(state='uploading', files=len(expected), bytes=sum(expected.values()), started_at=now())
    subprocess.run(command, check=True)
    status(state='verifying', files=len(expected), bytes=sum(expected.values()))
    response = subprocess.run([AWS, 's3api', 'list-objects-v2', '--bucket', data['bucket'],
                               '--prefix', CONFIG['prefix'], '--profile', PROFILE,
                               '--region', CONFIG['region'], '--output', 'json', '--no-cli-pager'],
                              check=True, capture_output=True, text=True)
    objects = {o['Key']: o for o in json.loads(response.stdout).get('Contents', [])}
    mismatches = [key for key, size in expected.items() if key not in objects
                  or objects[key]['Size'] != size
                  or objects[key].get('StorageClass') != CONFIG['storage_class']]
    if mismatches:
        raise ValueError(f'Remote verification failed for {len(mismatches)} files')
    status(state='complete', verified_files=len(expected), verified_bytes=sum(expected.values()),
           verification='S3 key, size and DEEP_ARCHIVE class; no download performed')
    print('Backup complete: all original files verified by S3 key, size and storage class', flush=True)
    return 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    with backup_lock():
        return upload_backup(args.dry_run)


def entrypoint():
    try:
        return main()
    except BackupBusy as exc:
        print(f'Skipped: {exc}')
        return 0
    except Exception as exc:
        status(state='failed', error=str(exc))
        print(str(exc), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(entrypoint())
