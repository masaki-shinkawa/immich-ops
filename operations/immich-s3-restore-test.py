#!/usr/bin/env python3
"""Check the one-time archive restore and compare SHA-256 without displaying media."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from immich_s3_common import STATE

AWS = str(Path.home() / '.local/bin/aws')


def save(data):
    tmp = STATE / 'restore-test.tmp'
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(STATE / 'restore-test.json')


def check():
    path = STATE / 'restore-test.json'
    if not path.exists():
        return 0
    state = json.loads(path.read_text())
    if state['state'] in ('complete', 'failed'):
        return 0
    now = datetime.datetime.now(datetime.timezone.utc)
    requested = datetime.datetime.fromisoformat(state['requested_at'])
    if (now - requested).total_seconds() > 72 * 3600:
        raise RuntimeError('Restore test did not complete within 72 hours')
    base = [AWS, 's3api']
    common = ['--bucket', state['bucket'], '--key', state['key'],
              '--profile', state['profile'], '--region', state['region'], '--no-cli-pager']
    response = subprocess.run(base + ['head-object'] + common,
                              check=True, capture_output=True, text=True)
    head = json.loads(response.stdout)
    state['checked_at'] = now.isoformat()
    if 'ongoing-request="false"' not in head.get('Restore', ''):
        save(state)
        print(json.dumps({'restore_test': 'waiting'}))
        return 0
    with tempfile.TemporaryDirectory(prefix='restore-test-', dir=STATE) as directory:
        output = Path(directory) / 'sample'
        subprocess.run(base + ['get-object'] + common + [str(output)],
                       check=True, capture_output=True, text=True)
        data = output.read_bytes()
        if len(data) != state['bytes'] or hashlib.sha256(data).hexdigest() != state['sha256']:
            raise RuntimeError('Restored sample failed byte-length or SHA-256 verification')
    state.update(state='complete', verified_at=now.isoformat(), sha256_matches=True)
    save(state)
    print(json.dumps({'restore_test': 'complete', 'sha256_matches': True}))
    return 0


def main():
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'restore-test.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            return check()
        except Exception:
            path = STATE / 'restore-test.json'
            state = json.loads(path.read_text()) if path.exists() else {}
            state.update(state='failed', error='Restore test failed; inspect private local logs',
                         checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            save(state)
            print(json.dumps({'restore_test': 'failed'}), file=sys.stderr)
            return 1


if __name__ == '__main__':
    sys.exit(main())
