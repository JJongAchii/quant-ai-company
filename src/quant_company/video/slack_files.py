"""Owner file delivery in the brief's Slack thread (Slack external upload API).

files.getUploadURLExternal (one per file) → POST the bytes to the returned URL → files.completeUploadExternal with
channel_id + thread_ts. Runs in the Slack dispatcher, the only process with the bot token; the video volume is
mounted read-only there. Each file ID is stored before its bytes are sent. Nothing is shared until the completion
call, so a failure before it is definitive and leaves no partial post; a lost completion response is uncertain and
is never replayed automatically. Slack offers no exactly-once delivery and this code claims none."""

import asyncio
import json
from pathlib import Path

import httpx

from .render import verify_artifacts
from .store import VideoStore

API = 'https://slack.com/api/'
CHUNK = 1024 * 1024
# Published name suffix per manifest file; the owner gets exactly these four.
FILES = (('video.mp4', '.mp4', '영상'), ('thumbnail.png', '_썸네일.png', '썸네일'), ('subtitles.srt', '.srt', '자막'),
         ('upload.txt', '_업로드문안.txt', '업로드 문안'))


class Definitive(Exception):
    """Slack rejected the upload before anything was shared."""


def names(job):
    day = str(job['source']['day']).replace('-', '')
    stem = ('마감브리핑_' if job['source'].get('edition_kind') == 'close' else '아침브리핑_') + day
    return [(source, stem + suffix, title) for source, suffix, title in FILES]


async def stream(path, touch):
    with path.open('rb') as handle:
        sent = 0
        while data := await asyncio.to_thread(handle.read, CHUNK):
            sent += len(data)
            if sent % (32 * CHUNK) < CHUNK:
                await asyncio.to_thread(touch)
            yield data


async def deliver(outbox, row, token):
    store = VideoStore(outbox.company)
    job = await asyncio.to_thread(store.delivery_job, row['id'])
    receipt = {'files': [], 'attempt_message': row['id']}

    def touch():
        # Keep the dispatcher's sending lease fresh during a long upload.
        with outbox.company.db.transaction() as conn:
            conn.execute("UPDATE outbox SET started_at=now() WHERE id=%s AND status='sending'", (row['id'],))

    async def finish(state, error=None, outbox_status=None):
        await asyncio.to_thread(store.delivery_result, row['id'], state, receipt, error)
        await asyncio.to_thread(outbox.settle, row, outbox_status or {'delivered': 'delivered', 'failed': 'blocked',
                                                                         'uncertain': 'uncertain'}[state], error=error)

    try:
        directory = verify_artifacts(job['artifacts'], outbox.company.settings.video_artifact_dir)
    except (ValueError, OSError, TypeError, KeyError):
        await finish('failed', 'artifact_check_failed')
        return True
    headers = {'Authorization': 'Bearer ' + token}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, write=600, read=120), transport=outbox.transport) as client:
            if not await asyncio.to_thread(outbox.before_send, row):
                return False
            for source, filename, _title in names(job):
                path = Path(directory) / source
                size = path.stat().st_size
                response = await client.post(API + 'files.getUploadURLExternal', headers=headers,
                                             data={'filename': filename, 'length': str(size)})
                if response.status_code == 429:
                    raise RateLimited(response.headers.get('retry-after', '30'))
                if response.status_code >= 500:
                    raise Definitive('slack_server_error_before_share')
                result = response.json()
                if not result.get('ok') or not result.get('file_id') or not result.get('upload_url'):
                    raise Definitive(str(result.get('error', 'upload_url_rejected'))[:80])
                receipt['files'].append({'id': result['file_id'], 'name': filename, 'bytes': size, 'uploaded': False})
                await asyncio.to_thread(store.delivery_receipt, row['id'], receipt)
                sent = await client.post(result['upload_url'], content=stream(path, touch),
                                         headers={'Content-Length': str(size), 'Content-Type': 'application/octet-stream'})
                if sent.status_code != 200:
                    raise Definitive(f'file_upload_http_{sent.status_code}')
                receipt['files'][-1]['uploaded'] = True
                await asyncio.to_thread(store.delivery_receipt, row['id'], receipt)
                await asyncio.to_thread(touch)
            files = [{'id': f['id'], 'title': title} for f, (_, _, title) in zip(receipt['files'], names(job), strict=True)]
            receipt['complete_requested'] = True
            await asyncio.to_thread(store.delivery_receipt, row['id'], receipt)
            try:
                response = await client.post(API + 'files.completeUploadExternal', headers=headers, data={
                    'files': json.dumps(files, ensure_ascii=False), 'channel_id': row['channel'],
                    'thread_ts': row['thread_ts'], 'initial_comment': row['text']})
            except httpx.HTTPError:
                await finish('uncertain', 'complete_outcome_unknown')
                return True
            if response.status_code == 429 or response.status_code >= 500:
                await finish('uncertain', 'complete_outcome_unknown')
                return True
            result = response.json()
            shared = [f.get('id') for f in result.get('files', [])] if isinstance(result.get('files'), list) else []
            if result.get('ok') and sorted(shared) == sorted(f['id'] for f in files):
                receipt['completed'] = {'file_ids': shared}
                await finish('delivered')
            elif result.get('ok'):
                await finish('uncertain', 'complete_receipt_mismatch')
            else:
                await finish('failed', str(result.get('error', 'complete_rejected'))[:80])
    except RateLimited as limited:
        # Nothing is shared yet; reserved upload URLs simply expire. The next attempt starts over.
        try:
            delay = max(1, min(int(limited.args[0]), 3600))
        except ValueError:
            delay = 30
        receipt['rate_limited'] = True
        await asyncio.to_thread(store.delivery_receipt, row['id'], receipt)
        await asyncio.to_thread(outbox.settle, row, 'pending', error='rate_limited', delay=delay)
    except Definitive as error:
        await finish('failed', str(error))
    except (httpx.HTTPError, OSError, ValueError):
        # Before completion nothing is visible in Slack, so a transport failure here is a definitive non-delivery.
        await finish('failed', 'transfer_failed_before_share')
    return True


class RateLimited(Exception):
    pass
