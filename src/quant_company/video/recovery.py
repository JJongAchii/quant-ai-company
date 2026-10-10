"""Operator-supplied, already verified receipts. Recovery never makes an external write."""

import re

from psycopg.types.json import Jsonb

from ..company import PolicyError
from . import store as video_store
from .contracts import VideoReview
from .episode import check_plan, plan_class
from .render import verify_artifacts
from .store import LOCK
from .youtube import session_url


def receipt_stage(job, key, receipt):
    if key in {'plan', 'review'}:
        cls = plan_class(job) if key == 'plan' else VideoReview
        result = cls.model_validate(receipt['output'])
        if receipt['request_id'] != f"video-{job['id']}-{key}":
            raise ValueError('Model receipt belongs to another request')
        if key == 'plan':
            check_plan(job, result)
        return 'queued' if key == 'plan' else 'reviewing'
    if re.fullmatch(r'speech-\d+', key):
        if not isinstance(receipt.get('taskId'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', receipt['taskId']):
            raise ValueError('Runway reconciliation requires the original task ID')
        return 'synthesizing'
    if key == 'upload-session':
        session_url(receipt['session'])
        return 'uploading'
    if key == 'captions' and isinstance(receipt.get('caption_id'), str) and receipt['caption_id']:
        return 'uploading'
    if key == 'thumbnail' and receipt.get('thumbnail_set') is True and receipt.get('video_id') == job['youtube_id']:
        return 'uploading'
    if key == 'publish' and receipt.get('privacy') == 'public' and receipt.get('video_id') == job['youtube_id']:
        if not job['approved_at'] or not job['approved_by'] or job['approved_at'] >= job['publish_deadline']:
            raise ValueError('Public receipt has no prior valid human approval')
        return 'published'
    raise ValueError('Unknown or mismatched effect receipt')


def reconcile(store, identity, key, receipt, note):
    if len(note.strip()) < 20 or len(note) > 2000 or not isinstance(receipt, dict):
        raise ValueError('Reconciliation requires a verified receipt and a meaningful audit note')
    with store.db.transaction() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
        job = conn.execute('SELECT * FROM video_jobs WHERE id=%s FOR UPDATE', (identity,)).fetchone()
        if not job or job['state'] != 'uncertain' or not store.allowed(job):
            raise PolicyError('Only an uncertain, authorized video can be reconciled')
        stage = receipt_stage(job, key, receipt)
        effect = conn.execute("SELECT * FROM video_effects WHERE id=%s AND state='running' FOR UPDATE",
                              (str(job['id'])+':'+key,)).fetchone()
        if not effect:
            raise PolicyError('No unresolved original request to reconcile')
        conn.execute("UPDATE video_effects SET state='completed',receipt=%s,completed_at=now() WHERE id=%s",
                     (Jsonb(receipt), effect['id']))
        conn.execute('INSERT INTO video_reconciliations(job_id,effect_key,note,receipt) VALUES(%s,%s,%s,%s)',
                     (job['id'], key, note, Jsonb(receipt)))
        if key == 'upload-session':
            conn.execute('UPDATE video_jobs SET upload_session=%s WHERE id=%s', (receipt['session'], job['id']))
        unresolved = conn.execute("SELECT 1 FROM video_effects WHERE job_id=%s AND state='running' LIMIT 1",
                                  (job['id'],)).fetchone()
        state = 'uncertain' if unresolved else stage
        if video_store.utcnow() >= job['publish_deadline'] and state not in {'uncertain', 'published'}:
            state = 'expired'
        conn.execute('UPDATE video_jobs SET state=%s,error=NULL,lease_until=NULL,updated_at=now() WHERE id=%s',
                     (state, job['id']))
        return {'state': state, 'reconciled_effect': key}


RETRYABLE = {'video_stage_failed:rendering': 'rendering', 'video_stage_failed:uploading': 'uploading',
             # Runway was unreachable at the free account check: no speech request was sent.
             'runway_unavailable': 'synthesizing', 'runway_account': 'synthesizing'}


def retry(store, identity, note):
    if not 20 <= len(note.strip()) <= 2000:
        raise ValueError('Safe retry requires a meaningful audit note')
    job = store.get(identity)
    if job and job['state'] == 'delivery_failed':
        # Slack definitively rejected the previous attempt before sharing; a new attempt has its own receipts.
        if not store.allowed(job):
            raise PolicyError('Video production account or source policy changed')
        verify_artifacts(job['artifacts'], store.settings.video_artifact_dir)
        with store.db.transaction() as conn:
            conn.execute('INSERT INTO video_reconciliations(job_id,note) VALUES(%s,%s)', (identity, note))
        store.deliver(job, job['artifacts'])
        return {'state': 'delivering'}
    with store.db.transaction() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
        job = conn.execute('SELECT * FROM video_jobs WHERE id=%s FOR UPDATE', (identity,)).fetchone()
        if (not job or job['state'] != 'blocked' or not store.allowed(job) or video_store.utcnow() >= job['publish_deadline']
                or job['error'] not in RETRYABLE
                or conn.execute("SELECT 1 FROM video_effects WHERE job_id=%s AND state='running'", (identity,)).fetchone()):
            raise PolicyError('Only known tasks, local rendering or the same upload session may be retried')
        stage = RETRYABLE[job['error']]
        if stage == 'uploading' and not job['upload_session']:
            raise PolicyError('A new upload session requires separate reconciliation')
        conn.execute('INSERT INTO video_reconciliations(job_id,note) VALUES(%s,%s)', (identity, note))
        conn.execute('UPDATE video_jobs SET state=%s,error=NULL,lease_until=NULL,updated_at=now() WHERE id=%s',
                     (stage, identity))
        return {'state': stage}
