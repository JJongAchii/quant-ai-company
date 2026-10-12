"""Rendering on an ECS Fargate task that exists only while it renders (owner decision 2026-10-12).

The always-on host keeps the database, Slack and the video worker; a render rents 16 vCPU for about ten minutes.
Everything for one render lives under s3://<bucket>/render/<job>/<render key>/: in/ holds the frozen inputs and out/
the resumable progress (scene segments, alignment, layout) plus the artifacts, with manifest.json written last as the
completion marker. The task renders into the same /state/video/<job>/<render key> path as the worker, so the manifest
it writes verifies unchanged after download. Tasks hold only S3 access to that prefix: no database, Slack or model keys.
"""

import io
import json
import os
import signal
import tarfile
import threading
import time
from pathlib import Path

RUNNING = frozenset({'PROVISIONING', 'PENDING', 'ACTIVATING', 'RUNNING'})
# After the container exits ECS keeps the task for a while; its exit code may still be unknown in these states.
STOPPING = frozenset({'DEACTIVATING', 'STOPPING', 'DEPROVISIONING'})
# Resumable progress and the delivered artifact set; inputs (speech) and the rebuilt page are never synced back.
RESUMABLE = ('seg-', 'align-', 'layout', 'review/', 'thumbnail.png')
SYNCED = RESUMABLE + ('video.mp4', 'subtitles.srt', 'script.txt', 'plan.json', 'alignment.json', 'upload.txt',
                      'sources.json', 'assets.json')


class RemoteRenderFailed(RuntimeError):
    """ECS refused the task (capacity or permissions); counted as one failed attempt."""


def ambiguous(error):
    """True when AWS may have acted without answering (connection lost, timeout): ask again with the same token.
    A refusal (RemoteRenderFailed) or any AWS error response (botocore ClientError) is definitive."""
    return not isinstance(error, RemoteRenderFailed) and not hasattr(error, 'response')


def split_uri(uri):
    if not uri.startswith('s3://'):
        raise ValueError('Render prefix must be an s3:// URI')
    bucket, _, prefix = uri[5:].partition('/')
    return bucket, prefix.rstrip('/')


class Storage:
    """The few S3 calls a render needs; tests pass a fake client with the same methods."""

    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    def put(self, key, body):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body)

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)['Body'].read()

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as error:  # noqa: BLE001 - botocore ClientError 404 or a fake KeyError
            if getattr(error, 'response', {}).get('Error', {}).get('Code') in {'404', 'NoSuchKey', 'NotFound'} \
                    or isinstance(error, KeyError):
                return False
            raise

    def keys(self, prefix):
        token, found = None, []
        while True:
            page = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix,
                                               **({'ContinuationToken': token} if token else {}))
            found += [item['Key'] for item in page.get('Contents', [])]
            if not page.get('IsTruncated'):
                return found
            token = page['NextContinuationToken']


def boto3_clients(settings):
    import boto3

    session = boto3.session.Session(region_name=settings.video_render_region)
    return session.client('s3'), session.client('ecs')


class RemoteRender:
    """Worker side: freeze inputs, start the task, watch it, collect and verify the result."""

    def __init__(self, settings, s3=None, ecs=None):
        self.settings = settings
        self._s3, self._ecs = s3, ecs

    def _clients(self):
        if self._s3 is None or self._ecs is None:
            self._s3, self._ecs = boto3_clients(self.settings)
        return self._s3, self._ecs

    @property
    def storage(self):
        return Storage(self._clients()[0], self.settings.video_render_bucket)

    def prefix(self, job, key):
        return f"render/{job['id']}/{key}"

    def inputs_ready(self, job, key):
        return self.storage.exists(self.prefix(job, key) + '/in/ready.json')

    def upload_inputs(self, job, plan, audio_paths, texts, key):
        storage, base = self.storage, self.prefix(job, key) + '/in/'
        if storage.exists(base + 'ready.json'):
            return False  # the same render key always has the same inputs
        # Story pictures made for this episode travel with the inputs and land at the same job path in the task.
        made = (job.get('qa') or {}).get('illustrations') or {}
        folder = Path(self.settings.video_artifact_dir) / str(job['id']) / 'illustrations'
        # Real photos found for this episode (video/photos.py) travel as library photos instead: a render image older
        # than the photo lookup labels every episode file as generated. Each becomes an asset of this upload's library
        # copy and its picture's fallback, so the task credits it like any library photo (2026-10-12).
        photos = {k: v for k, v in made.items() if v.get('kind') == '자료사진' and v.get('file')}
        generated = {k: v for k, v in made.items() if k not in photos}
        frozen = {'id': str(job['id']), 'source': job['source'], 'policy': job['policy'], 'illustrations': generated}
        for entry in generated.values():
            if entry.get('file'):
                storage.put(base + 'illustrations/' + entry['file'], (folder / entry['file']).read_bytes())
        raw_plan = plan.model_dump(mode='json')
        extra = {}
        for item in raw_plan.get('illustrations', []):
            entry = photos.get(item['id'])
            if entry:
                asset = f"episode-{item['id']}"
                extra[asset] = ({'file': f"photos/episode/{job['id']}/{entry['file']}", 'kind': '자료사진',
                                 'tags': [item['subject']]}
                                | {k: entry.get(k, '') for k in ('subject', 'author', 'license', 'license_url', 'source')},
                                folder / entry['file'])
                item['fallback'] = asset
        storage.put(base + 'job.json', json.dumps(frozen, ensure_ascii=False, default=str).encode())
        storage.put(base + 'plan.json', json.dumps(raw_plan, ensure_ascii=False).encode())
        storage.put(base + 'texts.json', json.dumps(texts, ensure_ascii=False).encode())
        for index, path in enumerate(audio_paths):
            storage.put(base + f'speech-{index:02}.mp3', Path(path).read_bytes())
        buffer = io.BytesIO()
        library_dir = Path(self.settings.video_asset_dir)
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            if not extra:
                archive.add(library_dir, arcname='.')
            else:
                archive.add(library_dir, arcname='.', filter=lambda info: None if info.name == './manifest.json' else info)
                manifest = json.loads((library_dir / 'manifest.json').read_text()) if (library_dir / 'manifest.json').exists() else {}
                manifest.update({asset: entry for asset, (entry, _) in extra.items()})
                for entry, path in extra.values():
                    archive.add(path, arcname='./' + entry['file'])
                data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                info = tarfile.TarInfo('./manifest.json')
                info.size, info.mtime = len(data), int(time.time())
                archive.addfile(info, io.BytesIO(data))
        storage.put(base + 'assets.tar.gz', buffer.getvalue())
        storage.put(base + 'ready.json', json.dumps({'scenes': len(audio_paths), 'at': time.time()}).encode())
        return True

    def launch(self, job, key, attempt):
        _, ecs = self._clients()
        uri = f"s3://{self.settings.video_render_bucket}/{self.prefix(job, key)}"
        # Spot first; the last attempt asks for regular Fargate capacity so a render is not starved by Spot reclaims.
        spot = attempt < self.settings.video_render_attempts
        response = ecs.run_task(
            cluster=self.settings.video_render_cluster, taskDefinition=self.settings.video_render_task_definition,
            count=1, clientToken=f"{job['id']}-{key}-{attempt}"[-64:], startedBy='quant-company-video',
            capacityProviderStrategy=[{'capacityProvider': 'FARGATE_SPOT' if spot else 'FARGATE', 'weight': 1}],
            networkConfiguration={'awsvpcConfiguration': {'subnets': list(self.settings.video_render_subnets),
                                                          'securityGroups': [self.settings.video_render_security_group],
                                                          'assignPublicIp': 'ENABLED'}},
            overrides={'containerOverrides': [{'name': 'render', 'command': [
                'quant-company', 'video', 'render-task', '--prefix', uri]}]})
        tasks = response.get('tasks') or []
        if not tasks:
            reasons = '; '.join(f.get('reason', '') for f in response.get('failures', []))
            raise RemoteRenderFailed(reasons[:300] or 'no task started')
        return tasks[0]['taskArn']

    def status(self, arn):
        _, ecs = self._clients()
        tasks = ecs.describe_tasks(cluster=self.settings.video_render_cluster, tasks=[arn]).get('tasks') or []
        if not tasks:
            return {'status': 'MISSING', 'exit': None, 'reason': 'task not found'}
        task = tasks[0]
        container = next(iter(task.get('containers') or []), {})
        return {'status': task.get('lastStatus'), 'exit': container.get('exitCode'), 'stop_code': task.get('stopCode'),
                'reason': (task.get('stoppedReason') or container.get('reason') or '')[:300]}

    def stop(self, arn, reason):
        _, ecs = self._clients()
        ecs.stop_task(cluster=self.settings.video_render_cluster, task=arn, reason=reason[:255])

    def collect(self, job, key, directory):
        """Download only what the manifest delivers (plus review frames) and verify every checksum."""
        from .render import verify_artifacts

        storage, out = self.storage, self.prefix(job, key) + '/out/'
        artifacts = json.loads(storage.get(out + 'manifest.json'))
        names = list(artifacts['files']) + [f['file'] for f in (artifacts.get('review') or {}).get('frames', [])]
        for name in dict.fromkeys(names):
            target = Path(directory) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(storage.get(out + name))
        (Path(directory) / 'manifest.json').write_text(json.dumps(artifacts, ensure_ascii=False, indent=2))
        verify_artifacts(artifacts, self.settings.video_artifact_dir)
        return artifacts


class Syncer:
    """In the task: upload progress every few seconds and once more on SIGTERM (Fargate Spot's two-minute warning)."""

    def __init__(self, storage, directory, out, interval=15):
        self.storage, self.directory, self.out, self.interval = storage, Path(directory), out, interval
        self.sent, self.lock, self.stop = {}, threading.RLock(), threading.Event()  # SIGTERM may re-enter once()

    def once(self):
        with self.lock:
            for path in sorted(self.directory.rglob('*')):
                name = path.relative_to(self.directory).as_posix()
                if not path.is_file() or not name.startswith(SYNCED) or name.endswith('.partial.mp4'):
                    continue
                stamp = (path.stat().st_size, path.stat().st_mtime_ns)
                if self.sent.get(name) != stamp:
                    self.storage.put(self.out + name, path.read_bytes())
                    self.sent[name] = stamp

    def run(self):
        while not self.stop.wait(self.interval):
            try:
                self.once()
            except Exception:  # noqa: BLE001 - progress upload is best effort; the final sync raises
                pass


def render_task(settings, uri, s3=None, renderer=None):
    """Entry point inside the Fargate task (quant-company video render-task --prefix s3://...)."""
    from .episode import plan_class
    from .motion import MotionRenderer

    bucket, prefix = split_uri(uri)
    if s3 is None:
        s3 = boto3_clients(settings)[0]
    storage = Storage(s3, bucket)
    key = prefix.rsplit('/', 1)[-1]
    inputs, out = prefix + '/in/', prefix + '/out/'
    job = json.loads(storage.get(inputs + 'job.json'))
    plan = plan_class(job).model_validate_json(storage.get(inputs + 'plan.json'))
    texts = json.loads(storage.get(inputs + 'texts.json'))
    directory = Path(settings.video_artifact_dir) / job['id'] / key
    directory.mkdir(parents=True, exist_ok=True)
    audio = []
    for index in range(len(plan.scenes)):
        path = directory / f'speech-{index:02}.mp3'
        path.write_bytes(storage.get(inputs + f'speech-{index:02}.mp3'))
        audio.append(path)
    for entry in (job.get('illustrations') or {}).values():
        if entry.get('file'):
            picture = directory.parent / 'illustrations' / Path(entry['file']).name
            picture.parent.mkdir(parents=True, exist_ok=True)
            picture.write_bytes(storage.get(inputs + 'illustrations/' + entry['file']))
    assets = Path(settings.video_asset_dir)
    assets.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(storage.get(inputs + 'assets.tar.gz')), mode='r:gz') as archive:
        archive.extractall(assets, filter='data')
    model = Path(settings.video_alignment_model)
    if model.is_absolute() and not model.exists():
        for name in storage.keys(f'models/{model.name}/'):
            target = model / name.split(f'models/{model.name}/', 1)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(storage.get(name))
    syncer, resumed = Syncer(storage, directory, out), 0
    # Resume: a previous (interrupted) task's scenes, alignment and layout are reused as they are (and not re-uploaded).
    for name in storage.keys(out):
        relative = name[len(out):]
        if relative.startswith(RESUMABLE):
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(storage.get(name))
            syncer.sent[relative] = (target.stat().st_size, target.stat().st_mtime_ns)
            resumed += relative.startswith('seg-') and relative.endswith('.mp4')

    def interrupted(signum=None, frame=None):
        # SIGTERM (Spot reclaim, StopTask) or the time limit: save the finished scenes for the next task, then exit.
        try:
            syncer.once()
        finally:
            os._exit(143 if signum else 124)

    signal.signal(signal.SIGTERM, interrupted)
    # A task never outlives its time limit, even when the worker that launched it is gone.
    watchdog = threading.Timer(settings.video_render_timeout_minutes * 60, interrupted)
    watchdog.daemon = True
    watchdog.start()
    thread = threading.Thread(target=syncer.run, daemon=True)
    thread.start()
    artifacts = (renderer or MotionRenderer(settings)).render(job, plan, directory, audio, texts)
    watchdog.cancel()
    syncer.stop.set()
    syncer.once()
    storage.put(out + 'manifest.json', json.dumps(artifacts, ensure_ascii=False, indent=2).encode())
    segments = len([n for n in syncer.sent if n.startswith('seg-') and n.endswith('.mp4')])
    return {'manifest': out + 'manifest.json', 'duration': artifacts.get('duration'), 'resumed_segments': resumed,
            'rendered_segments': segments - resumed}
