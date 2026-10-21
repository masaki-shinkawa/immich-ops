#!/usr/bin/env python3
"""Upload the validated original-file inventory; verify all remote sizes and classes."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

STATE = Path.home() / '.local/state/immich-s3-backup'
AWS = str(Path.home() / '.local/bin/aws')
PROFILE = 'immich-backup'

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def status(**values):
    tmp = STATE / 'status.tmp'
    tmp.write_text(json.dumps({'updated_at': now(), **values}, indent=2))
    tmp.chmod(0o600)
    tmp.replace(STATE / 'status.json')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    lock = (STATE / 'backup.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    data = json.loads((STATE / 'manifest.json').read_text())
    source = Path(data['source']).resolve()
    stage = STATE / 'originals'
    stage.mkdir(exist_ok=True)
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
        expected['originals/' + str(rel)] = item['bytes']
    staged = {str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_symlink()}
    if staged != {item['path'] for item in data['objects']}:
        raise ValueError('Staging inventory mismatch')
    command = [AWS, 's3', 'sync', str(stage), 's3://' + data['bucket'] + '/originals/',
               '--profile', PROFILE, '--region', 'us-east-1', '--storage-class',
               'DEEP_ARCHIVE', '--follow-symlinks', '--no-progress', '--no-cli-pager']
    if args.dry_run:
        command.append('--dryrun')
        subprocess.run(command, check=True)
        return 0
    status(state='uploading', files=len(expected), bytes=sum(expected.values()), started_at=now())
    subprocess.run(command, check=True)
    status(state='verifying', files=len(expected), bytes=sum(expected.values()))
    response = subprocess.run([AWS, 's3api', 'list-objects-v2', '--bucket', data['bucket'],
                               '--prefix', 'originals/', '--profile', PROFILE,
                               '--region', 'us-east-1', '--output', 'json', '--no-cli-pager'],
                              check=True, capture_output=True, text=True)
    objects = {o['Key']: o for o in json.loads(response.stdout).get('Contents', [])}
    mismatches = [key for key, size in expected.items() if key not in objects
                  or objects[key]['Size'] != size
                  or objects[key].get('StorageClass') != 'DEEP_ARCHIVE']
    if mismatches:
        raise ValueError(f'Remote verification failed for {len(mismatches)} files')
    status(state='complete', verified_files=len(expected), verified_bytes=sum(expected.values()),
           verification='S3 key, size and DEEP_ARCHIVE class; no download performed')
    print('Backup complete: all original files verified by S3 key, size and storage class', flush=True)
    return 0

def entrypoint():
    try:
        return main()
    except Exception as exc:
        status(state='failed', error=str(exc))
        print(str(exc), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(entrypoint())
