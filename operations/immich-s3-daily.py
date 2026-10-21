#!/usr/bin/env python3
"""Refresh the original inventory and sync changes once, under a single lock."""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import sys

from immich_s3_common import STATE, CONFIG, BackupBusy, backup_lock


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory = load_script('immich-s3-inventory')
backup = load_script('immich-s3-backup')


def run(dry_run=False):
    try:
        with backup_lock():
            inventory.build_inventory(Path(CONFIG['source']), CONFIG['bucket'])
            return backup.upload_backup(dry_run)
    except BackupBusy as exc:
        # Keep the status/manifest of the active transfer untouched.
        print(json.dumps({'state': 'skipped', 'reason': str(exc)}), flush=True)
        return 0
    except Exception as exc:
        if not dry_run:
            backup.status(state='failed', error=str(exc))
        print(json.dumps({'state': 'failed', 'error': str(exc)}), file=sys.stderr, flush=True)
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    logs = STATE / 'logs'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = datetime.datetime.now(datetime.timezone.utc)
    logfile = logs / (now.strftime('%Y-%m-%d') + '.log')
    with logfile.open('a', buffering=1) as stream:
        os.dup2(stream.fileno(), 1)
        os.dup2(stream.fileno(), 2)
        print(json.dumps({'started_at_utc': now.isoformat(), 'dry_run': args.dry_run}), flush=True)
        result = run(args.dry_run)
        print(json.dumps({'finished_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'exit_code': result}), flush=True)
        return result


if __name__ == '__main__':
    sys.exit(main())
