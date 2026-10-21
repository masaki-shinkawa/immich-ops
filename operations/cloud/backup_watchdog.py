"""Scheduled outside the NAS so missing successful backups are detectable."""
import datetime
import json
import os

import boto3
from botocore.config import Config

SDK_CONFIG = Config(retries={'mode': 'standard', 'total_max_attempts': 3},
                    connect_timeout=5, read_timeout=10)
s3 = boto3.client('s3', config=SDK_CONFIG)
sns = boto3.client('sns', config=SDK_CONFIG)


def assess(status, now, watch_started_at, stale_hours):
    if status.get('state') == 'failed':
        return 'The most recent Immich backup failed.'
    if status.get('restore_test_state') == 'failed':
        return 'The Immich restore verification failed.'
    reference = status.get('last_success_at') or watch_started_at
    last_success = datetime.datetime.fromisoformat(reference)
    if last_success.tzinfo is None or last_success > now:
        return 'Invalid backup success timestamp; check the NAS clock and status.'
    if (now - last_success).total_seconds() > stale_hours * 3600:
        return f'No successful Immich backup has been recorded for more than {stale_hours} hours.'
    return None


def handler(event, context):
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        with s3.get_object(Bucket=os.environ['BUCKET'], Key=os.environ['HEARTBEAT_KEY'])['Body'] as body:
            status = json.loads(body.read())
        reason = assess(status, now, os.environ['WATCH_STARTED_AT'],
                        int(os.environ['STALE_HOURS']))
    except Exception:
        # Still notify if the heartbeat is missing, unreadable, or malformed.
        reason = 'The Immich backup heartbeat could not be read or validated.'
    if reason:
        sns.publish(TopicArn=os.environ['TOPIC_ARN'], Subject='Immich backup needs attention',
                    Message=reason + '\nCheck the NAS backup status and private local logs. '
                    'This scheduled check runs in AWS independently of the NAS.')
    return {'alert_sent': bool(reason), 'checked_at': now.isoformat()}
