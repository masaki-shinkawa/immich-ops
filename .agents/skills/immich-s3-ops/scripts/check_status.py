#!/usr/bin/env python3
"""Read local backup state without exposing credentials, media paths, or raw logs."""
import datetime
import json
from pathlib import Path
import subprocess

STATE = Path.home() / '.local/state/immich-s3-backup'


def read_json(path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {'read_error': True}
    except (OSError, ValueError):
        return {'read_error': True}


def select(value, fields):
    if value is None:
        return {'exists': False}
    if value.get('read_error'):
        return {'exists': True, 'read_error': True}
    result = {key: value[key] for key in fields if key in value}
    result['exists'] = True
    if value.get('error'):
        result['has_error'] = True
    return result


def command(args):
    try:
        response = subprocess.run(args, text=True, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable'
    return response.stdout.strip() or ('unavailable' if response.returncode else 'unknown')


def collect(state_dir=STATE):
    now = datetime.datetime.now(datetime.timezone.utc)
    backup = read_json(state_dir / 'status.json')
    restore = read_json(state_dir / 'restore-test.json')
    monitor = read_json(state_dir / 'monitor-state.json')
    result = {'checked_at_utc': now.isoformat(),
              'backup': select(backup, ('state', 'updated_at', 'started_at', 'verified_files', 'verified_bytes')),
              'restore_test': select(restore, ('state', 'requested_at', 'checked_at', 'verified_at', 'sha256_matches'))}
    if backup and backup.get('verified_bytes') is not None:
        result['backup']['verified_GiB'] = round(backup['verified_bytes'] / 2**30, 2)
    last_success = (monitor or {}).get('last_success_at')
    if backup and backup.get('state') == 'complete':
        last_success = backup.get('updated_at', last_success)
    if last_success:
        try:
            timestamp = datetime.datetime.fromisoformat(last_success)
            result['last_success_jst'] = timestamp.astimezone(
                datetime.timezone(datetime.timedelta(hours=9))).isoformat()
            result['hours_since_success'] = round((now - timestamp).total_seconds() / 3600, 1)
        except (ValueError, TypeError):
            result['success_timestamp_invalid'] = True
    initial_log = state_dir / 'upload.log'
    if initial_log.exists():
        result['historical_initial_upload_errors'] = sum(
            line.startswith('upload failed:') for line in initial_log.read_text(errors='replace').splitlines())
    # Select structured run summaries only. Never display media keys or raw errors.
    runs = []
    for logfile in sorted((state_dir / 'logs').glob('*.log'))[-2:]:
        for line in logfile.read_text(errors='replace').splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            fields = ('started_at_utc', 'finished_at_utc', 'exit_code', 'dry_run', 'state')
            summary = {k: entry[k] for k in fields if k in entry}
            if summary:
                runs.append(summary)
    result['recent_run_events'] = runs[-8:]
    return result


def main():
    summary = collect()
    summary['cron'] = command(['systemctl', 'is-active', 'cron'])
    summary['legacy_initial_service'] = command(
        ['systemctl', '--user', 'show', 'immich-s3-backup', '--property=ActiveState', '--value'])
    summary['one_time_recovery_service'] = command(
        ['systemctl', '--user', 'show', 'immich-s3-recover', '--property=ActiveState', '--value'])
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
