#!/usr/bin/env python3
"""Exercise opaque transport with dummy files only."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).with_name('secret_files.py')
spec = importlib.util.spec_from_file_location('secret_files', HELPER)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SecretFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.env = self.repo / '.env'
        self.original = b'# preserve formatting\nDUMMY_TOKEN="dummy-roundtrip-sentinel"\n'
        self.env.write_bytes(self.original)
        self.transport = self.root / 'private/payload.json'
        self.targets = {'immich-env': (self.env, '.env', 0, 2)}
        self.patches = [patch.object(mod, 'REPO', self.repo), patch.object(mod, 'TARGETS', self.targets)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['secret_files.py', *args]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.entrypoint()
        logs = out.getvalue() + err.getvalue()
        self.assertNotIn('dummy-roundtrip-sentinel', logs)
        self.assertNotIn(base64.b64encode(self.original).decode(), logs)
        return rc

    def test_roundtrip_preserves_bytes_and_permissions(self):
        self.assertEqual(self.call('backup', '--output', str(self.transport)), 0)
        self.env.write_bytes(b'changed\n')
        self.assertEqual(self.call('restore', '--input', str(self.transport), '--file', 'immich-env'), 0)
        self.assertEqual(self.env.read_bytes(), self.original)
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.transport.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.call('verify', '--input', str(self.transport)), 0)

    def test_tampered_payload_never_changes_destination(self):
        self.assertEqual(self.call('backup', '--output', str(self.transport)), 0)
        payload = json.loads(self.transport.read_text())
        payload['files'][0]['content'] = base64.b64encode(b'tampered').decode()
        self.transport.write_text(json.dumps(payload))
        self.assertEqual(self.call('restore', '--input', str(self.transport), '--file', 'immich-env'), 1)
        self.assertEqual(self.env.read_bytes(), self.original)

    def test_unknown_id_never_changes_destination(self):
        self.assertEqual(self.call('backup', '--output', str(self.transport)), 0)
        payload = json.loads(self.transport.read_text())
        payload['files'][0]['id'] = 'unapproved'
        self.transport.write_text(json.dumps(payload))
        self.assertEqual(self.call('verify', '--input', str(self.transport)), 1)
        self.assertEqual(self.env.read_bytes(), self.original)

    def test_repository_transport_is_refused(self):
        target = self.repo / 'leaked.json'
        self.assertEqual(self.call('backup', '--output', str(target)), 1)
        self.assertFalse(target.exists())

    def test_verify_detects_local_change(self):
        self.assertEqual(self.call('backup', '--output', str(self.transport)), 0)
        self.env.write_bytes(b'changed\n')
        self.assertEqual(self.call('verify', '--input', str(self.transport)), 1)

    def test_receiver_handles_large_payload_without_echo(self):
        raw = b'dummy-large-value-' * 700
        content = base64.b64encode(raw).decode()
        payload = {'schema': mod.SCHEMA, 'spreadsheet_id': mod.SPREADSHEET_ID, 'files': [
            {'id': 'immich-env', 'tab': '.env', 'sheet_id': 0, 'row': 2,
             'encoding': 'base64', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
             'updated_at': 'fixture', 'content': content}]}
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, str(HELPER), 'receive', '--output', str(self.transport)],
                                   stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(slave)
        try:
            self.assertEqual(process.stdout.readline(), b'Opaque receiver ready\n')
            buffer = (json.dumps(payload) + '\n').encode()
            while buffer:
                sent = os.write(master, buffer)
                buffer = buffer[sent:]
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0)
            self.assertNotIn(content.encode(), stdout + stderr)
            self.assertEqual(json.loads(self.transport.read_text()), payload)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            os.close(master)


if __name__ == '__main__':
    unittest.main()
