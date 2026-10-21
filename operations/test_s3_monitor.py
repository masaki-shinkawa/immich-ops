#!/usr/bin/env python3
"""Validate stale-success alerts and restore integrity with isolated fixtures."""
import datetime
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

fake_boto = types.ModuleType('boto3')
fake_boto.client = Mock(return_value=Mock())
fake_config = types.ModuleType('botocore.config')
fake_config.Config = Mock()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


with patch.dict(sys.modules, {'boto3': fake_boto, 'botocore': types.ModuleType('botocore'),
                             'botocore.config': fake_config}):
    watchdog = load('backup_watchdog', Path(__file__).parent / 'cloud/backup_watchdog.py')
restore = load('restore_test', Path(__file__).with_name('immich-s3-restore-test.py'))
monitor = load('local_monitor', Path(__file__).with_name('immich_s3_monitor.py'))


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime.now(datetime.timezone.utc)
        self.start = (self.now - datetime.timedelta(hours=1)).isoformat()

    def test_fresh_success_is_healthy(self):
        self.assertIsNone(watchdog.assess({'state':'complete','last_success_at':self.start},
                                         self.now,self.start,36))

    def test_old_success_alerts_even_with_fresh_heartbeat(self):
        old = (self.now - datetime.timedelta(hours=37)).isoformat()
        self.assertIsNotNone(watchdog.assess({'state':'complete','last_success_at':old,
                                             'heartbeat_at':self.now.isoformat()},
                                            self.now,self.start,36))

    def test_initial_grace_period(self):
        self.assertIsNone(watchdog.assess({'state':'uploading','last_success_at':None},
                                         self.now,self.start,36))

    def test_failed_backup_or_restore_alerts_immediately(self):
        for status in ({'state':'failed'}, {'restore_test_state':'failed'}):
            self.assertIsNotNone(watchdog.assess(status,self.now,self.start,36))

    def test_missing_heartbeat_still_sends_alert(self):
        with patch.object(watchdog.s3,'get_object',side_effect=RuntimeError('missing')), \
             patch.object(watchdog.sns,'publish') as publish, \
             patch.dict('os.environ',{'BUCKET':'fixture','HEARTBEAT_KEY':'fixture','TOPIC_ARN':'fixture'}):
            self.assertTrue(watchdog.handler({},None)['alert_sent'])
            publish.assert_called_once()

    def test_future_timestamp_is_not_accepted_as_success(self):
        future=(self.now+datetime.timedelta(days=1)).isoformat()
        self.assertIsNotNone(watchdog.assess({'last_success_at':future},self.now,self.start,36))


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.patch=patch.object(restore,'STATE',self.root);self.patch.start()
        import hashlib
        self.raw=b'dummy-restored-image'
        self.state={'state':'restoring','requested_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'bucket':'fixture','key':'fixture.jpg','profile':'fixture','region':'us-east-1',
                    'bytes':len(self.raw),'sha256':hashlib.sha256(self.raw).hexdigest()}
        (self.root/'restore-test.json').write_text(json.dumps(self.state))

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def test_waiting_does_not_download(self):
        head=json.dumps({'Restore':'ongoing-request="true"'})
        with patch.object(restore.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout=head)) as run:
            self.assertEqual(restore.check(),0)
            self.assertEqual(run.call_count,1)
        self.assertEqual(json.loads((self.root/'restore-test.json').read_text())['state'],'restoring')

    def test_ready_verifies_hash_and_removes_download(self):
        def aws(command,**kwargs):
            if 'head-object' in command:
                return subprocess.CompletedProcess([],0,stdout=json.dumps({'Restore':'ongoing-request="false"'}))
            Path(command[-1]).write_bytes(self.raw)
            return subprocess.CompletedProcess([],0,stdout='{}')
        with patch.object(restore.subprocess,'run',side_effect=aws):
            self.assertEqual(restore.check(),0)
        self.assertTrue(json.loads((self.root/'restore-test.json').read_text())['sha256_matches'])
        self.assertEqual(list(self.root.glob('restore-test-*')),[])

    def test_mismatched_download_fails(self):
        def aws(command,**kwargs):
            if 'head-object' in command:
                return subprocess.CompletedProcess([],0,stdout=json.dumps({'Restore':'ongoing-request="false"'}))
            Path(command[-1]).write_bytes(b'corrupt')
            return subprocess.CompletedProcess([],0,stdout='{}')
        with patch.object(restore.subprocess,'run',side_effect=aws):
            with self.assertRaises(RuntimeError): restore.check()


class LocalMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_patch = patch.object(monitor, 'STATE', self.root)
        self.state_patch.start()
        self.cloud_patch = patch.object(monitor, 'cloud')
        self.cloud = self.cloud_patch.start()

    def tearDown(self):
        self.cloud_patch.stop()
        self.state_patch.stop()
        self.temp.cleanup()

    def test_failure_is_not_repeated_each_hour(self):
        (self.root / 'status.json').write_text(json.dumps({'state':'failed','updated_at':'fixture'}))
        with contextlib.redirect_stdout(io.StringIO()):
            monitor.report()
            monitor.report()
        publishes = [c for c in self.cloud.call_args_list if c.args[:2] == ('sns','publish')]
        self.assertEqual(len(publishes), 1)

    def test_heartbeat_does_not_falsely_refresh_success(self):
        timestamp = '2026-01-01T00:00:00+00:00'
        (self.root / 'status.json').write_text(json.dumps({'state':'complete','updated_at':timestamp}))
        with contextlib.redirect_stdout(io.StringIO()):
            monitor.report()
        status = json.loads((self.root / 'heartbeat.json').read_text())
        self.assertEqual(status['last_success_at'], timestamp)
        self.assertNotEqual(status['last_success_at'], status['heartbeat_at'])

    def test_restore_success_is_reported_once(self):
        (self.root / 'status.json').write_text(json.dumps({'state':'uploading'}))
        (self.root / 'restore-test.json').write_text(json.dumps({'state':'complete','verified_at':'fixture'}))
        with contextlib.redirect_stdout(io.StringIO()):
            monitor.report()
            monitor.report()
        publishes = [c for c in self.cloud.call_args_list if c.args[:2] == ('sns','publish')]
        self.assertEqual(len(publishes), 1)


if __name__=='__main__':
    unittest.main()
