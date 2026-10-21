#!/usr/bin/env python3
"""Check locking and local staging using dummy files and mocked AWS calls."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import immich_s3_common as common


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = load('immich-s3-backup')
inventory = load('immich-s3-inventory')
daily = load('immich-s3-daily')


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.source = self.root / 'media'
        self.source.mkdir()
        self.patches = [patch.object(common, 'STATE', self.state),
                        patch.object(backup, 'STATE', self.state),
                        patch.object(inventory, 'STATE', self.state),
                        patch.object(common, 'initial_backup_running', return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def manifest(self, objects):
        (self.state / 'manifest.json').write_text(json.dumps(
            {'source': str(self.source), 'bucket': 'test-bucket', 'objects': objects}))

    def test_lock_prevents_overlap(self):
        with common.backup_lock():
            with self.assertRaises(common.BackupBusy):
                with common.backup_lock():
                    self.fail('Second job entered the lock')
        with common.backup_lock():
            pass

    def test_initial_backup_blocks_manifest_changes(self):
        with patch.object(common, 'initial_backup_running', return_value=True):
            with self.assertRaises(common.BackupBusy):
                with common.backup_lock():
                    self.fail('Initial transfer should block a new job')

    def test_stale_staging_link_removed_without_deleting_original(self):
        (self.source / 'old.jpg').write_bytes(b'keep-original')
        (self.source / 'new.jpg').write_bytes(b'new')
        stage = self.state / 'originals'
        stage.mkdir()
        (stage / 'old.jpg').symlink_to(self.source / 'old.jpg')
        self.manifest([{'path': 'new.jpg', 'bytes': 3}])
        with patch.object(backup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            backup.upload_backup(dry_run=True)
        self.assertEqual((self.source / 'old.jpg').read_bytes(), b'keep-original')
        self.assertFalse((stage / 'old.jpg').is_symlink())
        self.assertEqual((stage / 'new.jpg').read_bytes(), b'new')

    def test_invalid_local_size_prevents_aws_calls(self):
        (self.source / 'photo.jpg').write_bytes(b'abc')
        self.manifest([{'path': 'photo.jpg', 'bytes': 999}])
        with patch.object(backup.subprocess, 'run') as aws:
            with self.assertRaises(ValueError):
                backup.upload_backup(dry_run=True)
            aws.assert_not_called()

    def test_inventory_refresh_adds_originals_and_excludes_trashed_paths(self):
        (self.source / 'first.jpg').write_bytes(b'abc')
        (self.source / 'second.jpg').write_bytes(b'defg')
        rows = [{'path': '/data/first.jpg', 'bytes': 3}]
        def query(*args, **kwargs):
            return subprocess.CompletedProcess([], 0, stdout=json.dumps(rows))
        with patch.object(inventory.subprocess, 'run', side_effect=query), contextlib.redirect_stdout(io.StringIO()):
            inventory.build_inventory(self.source, 'test-bucket')
            rows[:] = [{'path': '/data/second.jpg', 'bytes': 4}]
            inventory.build_inventory(self.source, 'test-bucket')
        result = json.loads((self.state / 'manifest.json').read_text())
        self.assertEqual(result['objects'], [{'path': 'second.jpg', 'bytes': 4}])
        self.assertTrue((self.source / 'first.jpg').exists())

    def test_busy_daily_job_does_not_change_active_status(self):
        status = self.state / 'status.json'
        status.write_text('{"state":"uploading"}')
        with patch.object(common, 'initial_backup_running', return_value=True), \
             patch.object(daily.inventory, 'build_inventory') as build, \
             patch.object(daily.backup, 'upload_backup') as upload, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(daily.run(), 0)
            build.assert_not_called()
            upload.assert_not_called()
        self.assertEqual(status.read_text(), '{"state":"uploading"}')

    def test_daily_keeps_one_lock_across_inventory_and_upload(self):
        entered = []
        def step(*args):
            with self.assertRaises(common.BackupBusy):
                with common.backup_lock():
                    self.fail('Daily job released its lock too early')
            entered.append(True)
            return 0
        with patch.object(daily.inventory, 'build_inventory', side_effect=step), \
             patch.object(daily.backup, 'upload_backup', side_effect=step):
            self.assertEqual(daily.run(), 0)
        self.assertEqual(len(entered), 2)


if __name__ == '__main__':
    unittest.main()
