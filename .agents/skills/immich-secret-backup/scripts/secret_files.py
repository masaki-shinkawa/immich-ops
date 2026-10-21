#!/usr/bin/env python3
"""Opaque transport for three allowlisted secret files; never print their values."""
import argparse
import base64
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile

REPO = Path(__file__).resolve().parents[4]
SPREADSHEET_ID = '1dclAy3Y_6RFP1VyFlS3NIM-fOhppjQa7rGSDbWso_ZU'
TARGETS = {
    'immich-env': (REPO / '.env', '.env', 0, 2),
    'aws-config': (Path.home() / '.aws/config', 'AWS', 1370232211, 2),
    'aws-credentials': (Path.home() / '.aws/credentials', 'AWS', 1370232211, 3),
}
SCHEMA = 'immich-secret-files-v1'


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Symlink target refused')
    fd, name = tempfile.mkstemp(prefix='.secret-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_payload(path):
    payload = json.loads(Path(path).read_bytes())
    if payload.get('schema') != SCHEMA or payload.get('spreadsheet_id') != SPREADSHEET_ID:
        raise ValueError('Unexpected transport schema or spreadsheet')
    files = payload.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('Empty transport')
    seen = set()
    validated = []
    for entry in files:
        key = entry.get('id')
        if key not in TARGETS or key in seen:
            raise ValueError('Unexpected or duplicate file identifier')
        seen.add(key)
        _, tab, sheet_id, row = TARGETS[key]
        if (entry.get('tab'), entry.get('sheet_id'), entry.get('row')) != (tab, sheet_id, row):
            raise ValueError('Unexpected destination')
        if entry.get('encoding') != 'base64':
            raise ValueError('Unsupported encoding')
        content = entry.get('content')
        if not isinstance(content, str) or len(content) > 40000:
            raise ValueError('Invalid payload size')
        raw = base64.b64decode(content, validate=True)
        if len(raw) != entry.get('bytes') or not hmac.compare_digest(
                hashlib.sha256(raw).hexdigest(), entry.get('sha256', '')):
            raise ValueError('Payload integrity mismatch')
        validated.append((key, raw))
    return payload, validated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--output', type=Path, required=True)
    backup.add_argument('--file', choices=TARGETS, action='append')
    verify = commands.add_parser('verify')
    verify.add_argument('--input', type=Path, required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--input', type=Path, required=True)
    restore.add_argument('--file', choices=TARGETS, action='append', required=True)
    receive = commands.add_parser('receive')
    receive.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'receive':
        if args.output.resolve().is_relative_to(REPO):
            raise ValueError('Transport must stay outside the repository')
        # TTY echo must be disabled before sending a transport from functions.exec.
        import termios
        descriptor = sys.stdin.fileno()
        old = termios.tcgetattr(descriptor)
        quiet = termios.tcgetattr(descriptor)
        quiet[3] &= ~(termios.ECHO | termios.ICANON)
        quiet[6][termios.VMIN] = 1
        quiet[6][termios.VTIME] = 0
        termios.tcsetattr(descriptor, termios.TCSANOW, quiet)
        print('Opaque receiver ready', flush=True)
        try:
            line = sys.stdin.readline(200000)
            payload = json.loads(line)
            encoded = json.dumps(payload).encode()
            # Verify before installing the received transport.
            with tempfile.TemporaryDirectory() as tmp:
                check = Path(tmp) / 'check.json'
                check.write_bytes(encoded)
                read_payload(check)
            atomic_write(args.output, encoded)
        finally:
            termios.tcsetattr(descriptor, termios.TCSANOW, old)
        print(json.dumps({'received': True}))
        return 0
    if args.command == 'backup':
        if args.output.resolve().is_relative_to(REPO):
            raise ValueError('Transport must stay outside the repository')
        entries = []
        for key in args.file or TARGETS:
            target, tab, sheet_id, row = TARGETS[key]
            if target.is_symlink():
                raise ValueError('Symlink source refused')
            raw = target.read_bytes()
            content = base64.b64encode(raw).decode('ascii')
            if len(content) > 40000:
                raise ValueError('Source exceeds bounded cell size')
            entries.append({'id': key, 'tab': tab, 'sheet_id': sheet_id, 'row': row,
                            'encoding': 'base64', 'bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest(),
                            'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            'content': content})
        payload = {'schema': SCHEMA, 'spreadsheet_id': SPREADSHEET_ID, 'files': entries}
        atomic_write(args.output, json.dumps(payload).encode())
        print(json.dumps({'prepared_files': [e['id'] for e in entries]}))
        return 0
    _, entries = read_payload(args.input)
    selected = set(getattr(args, 'file', None) or [key for key, _ in entries])
    if not selected.issubset({key for key, _ in entries}):
        raise ValueError('Requested file absent from transport')
    if args.command == 'restore':
        for key, _ in entries:
            if key in selected and TARGETS[key][0].is_symlink():
                raise ValueError('Symlink destination refused')
        for key, raw in entries:
            if key in selected:
                target = TARGETS[key][0]
                atomic_write(target, raw)
        print(json.dumps({'restored_files': sorted(selected), 'mode': '0600'}))
    else:
        matches = []
        for key, raw in entries:
            target = TARGETS[key][0]
            matches.append({'id': key, 'matches_local': target.is_file() and
                            hmac.compare_digest(target.read_bytes(), raw)})
        print(json.dumps({'verified_files': matches}))
        if not all(m['matches_local'] for m in matches):
            return 1
    return 0


def entrypoint():
    try:
        return main()
    except Exception:
        # Exception messages from parsers or SDKs can contain values; suppress them.
        print('Secret-file operation failed; contents withheld.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(entrypoint())
