"""Shared nonsecret settings and a lock spanning inventory through verification."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path

STATE = Path.home() / '.local/state/immich-s3-backup'
CONFIG = json.loads(Path(__file__).with_name('s3-backup.json').read_text())


class BackupBusy(Exception):
    """Another backup owns the inventory or the initial transfer is still active."""


def initial_backup_running():
    legacy = str(Path.home() / '.local/libexec/immich-s3-backup.py').encode()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            args = (process / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if legacy in args:
            return True
    return False


@contextmanager
def backup_lock():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if initial_backup_running():
        raise BackupBusy('Initial backup is still running')
    with (STATE / 'backup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupBusy('Another backup is running') from None
        if initial_backup_running():
            raise BackupBusy('Initial backup is still running')
        yield
