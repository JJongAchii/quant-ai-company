"""Remote (ECS Fargate) rendering against in-memory S3/ECS fakes: no AWS account, no Chromium."""

import io
import json

import pytest

from quant_company.video import remote as remote_module
from quant_company.video.contracts import VisualReview
from quant_company.video.remote import Storage, Syncer, render_task

from .test_briefing import brief  # noqa: F401
from .test_video import SimulatedSpeech, new_job, video  # noqa: F401
from .test_video_motion import InspectedRenderer, InspectingProvider, episode


class FakeS3:
    def __init__(self):
        self.objects, self.puts = {}, []

    def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3 keyword names
        self.objects[(Bucket, Key)] = bytes(Body)
        self.puts.append(Key)

    def get_object(self, Bucket, Key):  # noqa: N803
        return {'Body': io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
        return {}

    def list_objects_v2(self, Bucket, Prefix, **_):  # noqa: N803
        return {'Contents': [{'Key': k} for b, k in sorted(self.objects) if b == Bucket and k.startswith(Prefix)],
                'IsTruncated': False}


class FakeECS:
    """run_task records the call and, like ECS, starts one task per client token; the test decides how it stops."""

    def __init__(self):
        self.runs, self.states, self.tokens, self.stopped, self.fail = [], {}, {}, [], []

    def run_task(self, **request):
        self.runs.append(request)
        if request['clientToken'] not in self.tokens:
            self.tokens[request['clientToken']] = arn = f"arn:aws:ecs:task/{len(self.tokens) + 1}"
            self.states[arn] = {'lastStatus': 'PENDING', 'containers': [{}]}
        if self.fail:
            raise self.fail.pop(0)  # the call may have reached ECS without an answer
        return {'tasks': [{'taskArn': self.tokens[request['clientToken']]}], 'failures': []}

    def describe_tasks(self, cluster, tasks):
        if self.fail:
            raise self.fail.pop(0)
        return {'tasks': [self.states[arn] for arn in tasks if arn in self.states]}

    def stop_task(self, cluster, task, reason):
        self.stopped.append((task, reason))
        self.finish(task, code=137, reason=reason)

    def finish(self, arn, code=0, reason='', status='STOPPED'):
        self.states[arn] = {'lastStatus': status, 'stoppedReason': reason, 'containers': [{'exitCode': code}]}


def remote_settings(store, tmp_path):
    settings = store.settings
    settings.video_template = 'motion-v2'
    settings.video_render_backend = 'remote'
    settings.video_render_bucket = 'render-bucket'
    settings.video_render_cluster = 'render-cluster'
    settings.video_render_task_definition = 'render-task:1'
    settings.video_render_subnets = ['subnet-a']
    settings.video_render_security_group = 'sg-render'
    settings.video_asset_dir = tmp_path / 'assets'
    (tmp_path / 'assets').mkdir(exist_ok=True)
    (tmp_path / 'assets' / 'manifest.json').write_text(json.dumps({'fixture-photo': {'tags': [], 'kind': '자료사진'},
                                                                   'fixture-illustration': {'tags': [], 'kind': '일러스트'}}))
    return settings


def simulate_task(s3, settings, run, renderer):
    """What the Fargate container does: render_task against the same S3 objects."""
    uri = run['overrides']['containerOverrides'][0]['command'][-1]
    return render_task(settings, uri, s3=s3, renderer=renderer)


async def ticks(store, provider, speech, renderer, until, limit=40):
    from quant_company.video.runner import VideoRunner

    for _ in range(limit):
        runner = VideoRunner(store.company, provider, speech, renderer)
        await runner.tick()
        current = store.get(until['job'])
        with store.db.transaction() as conn:
            conn.execute('UPDATE video_jobs SET lease_until=NULL WHERE id=%s', (until['job'],))
        if until['stop'](current):
            return current
    return store.get(until['job'])


async def test_remote_render_runs_on_fargate_and_is_collected(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, _ = video
    settings = remote_settings(store, tmp_path)
    s3, ecs = FakeS3(), FakeECS()
    monkeypatch.setattr(remote_module, 'boto3_clients', lambda s: (s3, ecs))
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'speech'))
    job = new_job(brief, video)
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    speech, renderer = SimulatedSpeech(), InspectedRenderer()
    current = await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: ecs.runs})
    assert current['state'] == 'rendering' and current['error'] == 'render_remote' and renderer.renders == 0
    run = ecs.runs[0]
    assert run['capacityProviderStrategy'] == [{'capacityProvider': 'FARGATE_SPOT', 'weight': 1}]
    assert run['clientToken'].endswith('-1') and run['networkConfiguration']['awsvpcConfiguration']['assignPublicIp'] == 'ENABLED'
    prefix = run['overrides']['containerOverrides'][0]['command'][-1].split('render-bucket/', 1)[1]
    assert ('render-bucket', prefix + '/in/ready.json') in s3.objects
    # The task renders with the shared code and writes the manifest last; the worker collects and inspects.
    result = simulate_task(s3, settings, run, InspectedRenderer())
    assert result['manifest'].endswith('/out/manifest.json')
    arn = current['qa']['render']['attempts'][-1]['task']
    ecs.states[arn] = {'lastStatus': 'STOPPING', 'containers': [{}]}  # exited, exit code not reported yet: wait
    current = await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: True})
    assert current['error'] == 'render_remote_stopping' and 'outcome' not in current['qa']['render']['attempts'][-1]
    ecs.finish(arn, status='DEPROVISIONING')  # observed live: exit code 0 arrives before STOPPED
    current = await ticks(store, provider, speech, renderer, {'job': job['id'],
                                                              'stop': lambda j: j['state'] not in {'rendering', 'inspecting'}})
    assert current['state'] == 'uploading', current['error']
    assert renderer.renders == 0 and current['qa']['render']['attempts'][0]['outcome'] == 'completed'


async def test_spot_reclaims_resume_and_the_episode_falls_back_to_a_local_render(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, _ = video
    remote_settings(store, tmp_path)
    store.settings.video_render_attempts = 2
    s3, ecs = FakeS3(), FakeECS()
    monkeypatch.setattr(remote_module, 'boto3_clients', lambda s: (s3, ecs))
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'speech'))
    job = new_job(brief, video)
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    speech, renderer = SimulatedSpeech(), InspectedRenderer()
    await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: ecs.runs})
    ecs.finish('arn:aws:ecs:task/1', code=143, reason='Your Spot Task was interrupted.')
    current = await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: len(ecs.runs) > 1})
    assert ecs.runs[1]['capacityProviderStrategy'] == [{'capacityProvider': 'FARGATE', 'weight': 1}]  # last attempt
    assert ecs.runs[1]['clientToken'].endswith('-2') and current['qa']['render']['attempts'][0]['outcome'] == 'failed'
    ecs.finish('arn:aws:ecs:task/2', code=1, reason='Essential container exited')
    current = await ticks(store, provider, speech, renderer, {'job': job['id'],
                                                              'stop': lambda j: j['state'] not in {'rendering', 'inspecting'}})
    assert current['state'] == 'uploading', current['error']
    assert current['qa']['render_local'] is True and renderer.renders == 1  # always produced, here


async def test_an_unanswered_launch_is_asked_again_with_the_same_token_and_status_errors_do_not_relaunch(
        brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, _ = video
    remote_settings(store, tmp_path)
    s3, ecs = FakeS3(), FakeECS()
    monkeypatch.setattr(remote_module, 'boto3_clients', lambda s: (s3, ecs))
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'speech'))
    job = new_job(brief, video)
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    speech, renderer = SimulatedSpeech(), InspectedRenderer()
    ecs.fail.append(ConnectionError('read timed out'))  # RunTask reached ECS; the answer was lost
    current = await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: len(ecs.runs) > 1})
    assert ecs.runs[0]['clientToken'] == ecs.runs[1]['clientToken'] and len(ecs.tokens) == 1  # one paid task
    assert current['qa']['render']['attempts'] == [{**current['qa']['render']['attempts'][0], 'task': 'arn:aws:ecs:task/1'}]
    ecs.fail += [ConnectionError('unreachable')] * 3  # describe_tasks fails: wait, never launch a second task
    current = await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: not ecs.fail})
    assert len(ecs.tokens) == 1 and current['qa']['render']['attempts'][0]['errors'] == 4
    assert current['state'] == 'rendering' and 'outcome' not in current['qa']['render']['attempts'][0]


async def test_a_task_past_its_time_limit_is_stopped_and_the_next_attempt_resumes(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from datetime import UTC, datetime, timedelta

    store, _ = video
    remote_settings(store, tmp_path)
    s3, ecs = FakeS3(), FakeECS()
    monkeypatch.setattr(remote_module, 'boto3_clients', lambda s: (s3, ecs))
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'speech'))
    job = new_job(brief, video)
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    speech, renderer = SimulatedSpeech(), InspectedRenderer()
    current = await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: ecs.runs})
    ecs.states['arn:aws:ecs:task/1']['lastStatus'] = 'RUNNING'
    qa = current['qa']
    qa['render']['attempts'][0]['launched_at'] = (datetime.now(UTC) - timedelta(minutes=101)).isoformat()
    store.update_qa(current, qa)
    current = await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: len(ecs.runs) > 1})
    assert ecs.stopped == [('arn:aws:ecs:task/1', 'render time limit')]
    assert current['qa']['render']['attempts'][0]['outcome'] == 'timeout' and ecs.runs[1]['clientToken'].endswith('-2')


def test_render_task_resumes_finished_segments_and_writes_the_manifest_last(tmp_path):
    import tarfile

    from quant_company.config import Settings

    s3 = FakeS3()
    storage = Storage(s3, 'render-bucket')
    prefix = 'render/job-1/render-abc'
    plan = episode()
    storage.put(prefix + '/in/job.json', json.dumps({'id': 'job-1', 'source': {}, 'policy': {'template': 'motion-v2'}}).encode())
    storage.put(prefix + '/in/plan.json', plan.model_dump_json().encode())
    storage.put(prefix + '/in/texts.json', b'null')
    for i in range(len(plan.scenes)):
        storage.put(prefix + f'/in/speech-{i:02}.mp3', b'speech')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        data = b'{}'
        info = tarfile.TarInfo('manifest.json')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    storage.put(prefix + '/in/assets.tar.gz', buffer.getvalue())
    storage.put(prefix + '/out/seg-00-00.mp4', b'finished-by-the-reclaimed-task')
    storage.put(prefix + '/out/seg-00-00.json', b'{"start": 0}')
    settings = Settings(_env_file=None, video_artifact_dir=tmp_path / 'video', video_asset_dir=tmp_path / 'assets')
    seen = {}

    class Renderer:
        def render(self, job, plan, directory, audio, texts):
            seen['resumed'] = (directory / 'seg-00-00.mp4').read_bytes()
            (directory / 'seg-00-01.mp4').write_bytes(b'new')
            (directory / 'video.mp4').write_bytes(b'video')
            return {'directory': str(directory), 'files': {'video.mp4': 'x'}, 'duration': 1.0}

    s3.puts.clear()
    result = render_task(settings, 's3://render-bucket/' + prefix, s3=s3, renderer=Renderer())
    assert seen['resumed'] == b'finished-by-the-reclaimed-task'
    assert prefix + '/out/seg-00-00.mp4' not in s3.puts  # resumed progress is not uploaded again
    assert (result['resumed_segments'], result['rendered_segments']) == (1, 1)
    assert storage.get(prefix + '/out/seg-00-01.mp4') == b'new' and storage.get(prefix + '/out/video.mp4') == b'video'
    assert json.loads(storage.get(prefix + '/out/manifest.json'))['duration'] == 1.0
    assert result['manifest'] == prefix + '/out/manifest.json'


def test_the_fixed_opening_and_closing_travel_with_the_render_inputs(tmp_path):
    from quant_company.config import Settings
    from quant_company.video.remote import RemoteRender

    s3 = FakeS3()
    settings = Settings(_env_file=None, video_artifact_dir=tmp_path / 'video', video_asset_dir=tmp_path / 'assets',
                        video_render_bucket='render-bucket')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'manifest.json').write_text('{}')
    plan = episode()
    audio = []
    for i in range(len(plan.scenes)):
        audio.append(tmp_path / f's{i}.mp3')
        audio[-1].write_bytes(b'speech')
    brand = {'sting': tmp_path / 'sting.mp3', 'endcard': tmp_path / 'endcard.mp3'}
    for name, path in brand.items():
        path.write_bytes(name.encode())
    job = {'id': 'job-1', 'source': {}, 'policy': {'template': 'motion-v2'}, 'qa': {}}
    remote = RemoteRender(settings, s3=s3, ecs=FakeECS())
    remote.upload_inputs(job, plan, audio, None, 'render-abc', brand)
    seen = {}

    class Renderer:
        def render(self, job, plan, directory, audio, texts, brand=None):
            seen['brand'] = {name: path.read_bytes() for name, path in brand.items()}
            (directory / 'video.mp4').write_bytes(b'video')
            return {'directory': str(directory), 'files': {'video.mp4': 'x'}, 'duration': 1.0}

    render_task(settings, 's3://render-bucket/render/job-1/render-abc', s3=s3, renderer=Renderer())
    assert seen['brand'] == {'sting': b'sting', 'endcard': b'endcard'}


def test_the_music_bed_travels_with_the_render_inputs(tmp_path):
    """The render image has no music folder: the worker's chosen track goes in the inputs, alone (the library copy
    leaves the folder out), and the task renders with it; without one the task is given none."""
    import tarfile
    from datetime import date

    from quant_company.config import Settings
    from quant_company.video.remote import RemoteRender

    s3 = FakeS3()
    settings = Settings(_env_file=None, video_artifact_dir=tmp_path / 'video', video_asset_dir=tmp_path / 'assets',
                        video_render_bucket='render-bucket')
    (tmp_path / 'assets' / 'music').mkdir(parents=True)
    (tmp_path / 'assets' / 'manifest.json').write_text('{}')
    for name in ('a.mp3', 'b.mp3'):
        (tmp_path / 'assets' / 'music' / name).write_bytes(name.encode())
    plan = episode()
    audio = []
    for i in range(len(plan.scenes)):
        audio.append(tmp_path / f's{i}.mp3')
        audio[-1].write_bytes(b'speech')
    job = {'id': 'job-1', 'source': {'day': '2026-10-20'}, 'policy': {'template': 'motion-v2'}, 'qa': {}}
    remote = RemoteRender(settings, s3=s3, ecs=FakeECS())
    remote.upload_inputs(job, plan, audio, None, 'render-abc')
    chosen = ['a.mp3', 'b.mp3'][date(2026, 10, 20).toordinal() % 2]
    inputs = Storage(s3, 'render-bucket')
    assert json.loads(inputs.get('render/job-1/render-abc/in/music.json')) == chosen
    with tarfile.open(fileobj=io.BytesIO(inputs.get('render/job-1/render-abc/in/assets.tar.gz')), mode='r:gz') as archive:
        assert './manifest.json' in archive.getnames() and not any('music' in n for n in archive.getnames())
    # The task, with its own empty folders as in the render image.
    task = Settings(_env_file=None, video_artifact_dir=tmp_path / 'task', video_asset_dir=tmp_path / 'task-assets')
    seen = {}

    class Renderer:
        def render(self, job, plan, directory, audio, texts, music=None):
            seen['music'] = (music.name, music.read_bytes())
            (directory / 'video.mp4').write_bytes(b'video')
            return {'directory': str(directory), 'files': {'video.mp4': 'x'}, 'duration': 1.0}

    render_task(task, 's3://render-bucket/render/job-1/render-abc', s3=s3, renderer=Renderer())
    assert seen['music'] == (chosen, chosen.encode()) and not (tmp_path / 'task-assets' / 'music').exists()
    # No track on the worker: none in the inputs and none given (a renderer without the argument still renders).
    for name in ('a.mp3', 'b.mp3'):
        (tmp_path / 'assets' / 'music' / name).unlink()
    remote.upload_inputs(job, plan, audio, None, 'render-def')
    assert json.loads(inputs.get('render/job-1/render-def/in/music.json')) is None

    class Plain:
        def render(self, job, plan, directory, audio, texts):
            (directory / 'video.mp4').write_bytes(b'video')
            return {'directory': str(directory), 'files': {'video.mp4': 'x'}, 'duration': 1.0}

    render_task(task, 's3://render-bucket/render/job-1/render-def', s3=s3, renderer=Plain())


def test_syncer_uploads_only_progress_and_artifacts(tmp_path):
    s3 = FakeS3()
    (tmp_path / 'review').mkdir()
    for name in ('seg-01-00.mp4', 'seg-01-00.partial.mp4', 'speech-00.mp3', 'align-00.json', 'review/00-a.jpg', 'notes.txt'):
        (tmp_path / name).write_bytes(b'x')
    syncer = Syncer(Storage(s3, 'b'), tmp_path, 'out/')
    syncer.once()
    syncer.once()  # unchanged files are not uploaded twice
    assert sorted(k for _, k in s3.objects) == ['out/align-00.json', 'out/review/00-a.jpg', 'out/seg-01-00.mp4']
    assert len(syncer.sent) == 3


def test_remote_backend_requires_its_aws_settings():
    from quant_company.config import Settings

    with pytest.raises(ValueError, match='Remote rendering requires'):
        Settings(_env_file=None, video_render_backend='remote', video_render_bucket='b')


async def test_a_render_made_by_other_renderer_code_is_reported(brief, video, tmp_path, monkeypatch):  # noqa: F811
    """2026-10-13: the Fargate task ran the 10/12 render image while the worker had newer code; nothing said so."""
    from quant_company.video.motion import RENDERER_DIGEST
    from quant_company.video.store import delivery_text

    store, _ = video
    settings = remote_settings(store, tmp_path)
    s3, ecs = FakeS3(), FakeECS()
    monkeypatch.setattr(remote_module, 'boto3_clients', lambda s: (s3, ecs))
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'speech'))
    job = new_job(brief, video)
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    speech, renderer = SimulatedSpeech(), InspectedRenderer()
    current = await ticks(store, provider, speech, renderer, {'job': job['id'], 'stop': lambda j: ecs.runs})
    simulate_task(s3, settings, ecs.runs[0], InspectedRenderer(renderer_code='0' * 64))
    ecs.finish(current['qa']['render']['attempts'][-1]['task'])
    current = await ticks(store, provider, speech, renderer, {'job': job['id'],
                                                              'stop': lambda j: j['state'] not in {'rendering', 'inspecting'}})
    assert current['state'] == 'uploading', current['error']
    assert current['qa']['render_findings'] == [
        f"Render image code 000000000000 differs from the worker's {RENDERER_DIGEST[:12]}: rebuild the render image"]
    assert "렌더: Render image code" in delivery_text(current, current['artifacts'])
