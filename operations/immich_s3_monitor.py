#!/usr/bin/env python3
"""Publish a tiny status heartbeat and operational alerts, without media or secrets."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from immich_s3_common import STATE, CONFIG

AWS = str(Path.home() / '.local/bin/aws')


def cloud(service, operation, *args):
    return subprocess.run([AWS, service, operation, *args, '--profile', CONFIG['profile'],
                           '--region', CONFIG['region'], '--no-cli-pager'],
                          check=True, capture_output=True, text=True)


def report():
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'monitor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        cache_path = STATE / 'monitor-state.json'
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        status_path = STATE / 'status.json'
        status = json.loads(status_path.read_text()) if status_path.exists() else {'state': 'unknown'}
        restore_path = STATE / 'restore-test.json'
        restore = json.loads(restore_path.read_text()) if restore_path.exists() else {}
        if status.get('state') == 'complete':
            cache['last_success_at'] = status['updated_at']
        if restore.get('state') == 'failed':
            failure_id = 'restore:' + restore.get('checked_at', 'unknown')
            subject = 'Immich backup: restore verification failed'
        elif status.get('state') == 'failed':
            failure_id = 'backup:' + status.get('updated_at', 'unknown')
            subject = 'Immich backup failed'
        else:
            failure_id = None
            subject = ''
        heartbeat = {'heartbeat_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     'last_success_at': cache.get('last_success_at'),
                     'state': status.get('state', 'unknown'),
                     'restore_test_state': restore.get('state', 'not_started')}
        heartbeat_path = STATE / 'heartbeat.json'
        heartbeat_path.write_text(json.dumps(heartbeat))
        heartbeat_path.chmod(0o600)
        cloud('s3api', 'put-object', '--bucket', CONFIG['bucket'],
              '--key', CONFIG['monitor']['heartbeat_key'], '--body', str(heartbeat_path),
              '--storage-class', 'STANDARD', '--content-type', 'application/json')
        if failure_id and cache.get('last_failure_notified') != failure_id:
            cloud('sns', 'publish', '--topic-arn', CONFIG['monitor']['topic_arn'],
                  '--subject', subject, '--message',
                  'An Immich backup or restore verification failed. Check the private logs in '
                  '~/.local/state/immich-s3-backup/ on the NAS. No credentials or media are included.')
            cache['last_failure_notified'] = failure_id
        if restore.get('state') == 'complete' and cache.get('restore_success_notified_at') != restore.get('verified_at'):
            cloud('sns', 'publish', '--topic-arn', CONFIG['monitor']['topic_arn'],
                  '--subject', 'Immich restore verification succeeded', '--message',
                  'A sample original image was restored from Deep Archive and its byte length '
                  'and SHA-256 matched the original. The temporary local download was removed.')
            cache['restore_success_notified_at'] = restore['verified_at']
        temp = STATE / 'monitor-state.tmp'
        temp.write_text(json.dumps(cache)); temp.chmod(0o600); temp.replace(cache_path)
        print(json.dumps({'heartbeat_updated': True, 'failure_alert': bool(failure_id)}), flush=True)
        return 0


def main():
    try:
        return report()
    except Exception:
        print('Backup monitoring failed; inspect local AWS access and private logs.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
