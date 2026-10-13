"""Story pictures from the separate Codex image runtime: plan contract, generation, fallback, rendering inputs.
The image runtime is simulated here; tests/test_video_image_runtime.py covers the runtime against a simulated CLI."""

import hashlib
import json

import pytest

from quant_company.contracts import ProviderFault
from quant_company.video.episode import EpisodePlan, episode_prompt, episode_violations
from quant_company.video.illustrations import GENERATED, library_entries, make_next, missing, request_id

from .test_briefing import brief  # noqa: F401
from .test_video import body_source, video  # noqa: F401
from .test_video_image_runtime import png
from .test_video_motion import InspectedRenderer, InspectingProvider, episode, produce_inspected

SHELF = {'fixture-photo': {'tags': [], 'kind': '자료사진'}, 'fixture-illustration': {'tags': [], 'kind': '일러스트'}}
BRIEF = 'A generic semiconductor fabrication floor with glowing wafer handling robots, seen at night.'


def illustrated(**item):
    raw = episode().model_dump(mode='json')
    raw['illustrations'] = [{'id': 'gen-1', 'subject': '반도체 공장', 'brief': BRIEF, 'fallback': 'fixture-illustration', **item}]
    next(s for s in raw['scenes'] if s['id'] == 'head')['data']['image'] = {'asset': 'gen-1'}
    return EpisodePlan.model_validate(raw)


class FakeImages:
    def __init__(self, faults=()):
        self.calls, self.faults, self.data = [], list(faults), {}

    async def generate(self, rid, model, effort, text):
        self.calls.append((rid, model, effort, text))
        if self.faults:
            raise self.faults.pop(0)
        self.data[rid] = png()
        return {'request_id': rid, 'sha256': hashlib.sha256(self.data[rid]).hexdigest(), 'bytes': len(self.data[rid]),
                'width': 64, 'height': 36, 'media_type': 'image/png'}

    async def download(self, rid, sha256):
        return self.data[rid]


def test_generated_ids_are_checked_like_library_images():
    def image_findings(plan):
        return [f for f in episode_violations(plan, body_source(), SHELF)
                if 'Illustration' in f or 'asset library' in f]

    assert image_findings(illustrated()) == []
    raw = illustrated().model_dump(mode='json')
    raw['illustrations'].append({'id': 'gen-2', 'subject': '항구', 'brief': BRIEF, 'fallback': 'missing-asset'})
    found = image_findings(EpisodePlan.model_validate(raw))
    assert any('gen-2 is defined but not used' in f for f in found) and any('fallback is not a listed' in f for f in found)
    raw = episode().model_dump(mode='json')
    next(s for s in raw['scenes'] if s['id'] == 'head')['data']['image'] = {'asset': 'gen-3'}  # never defined
    assert any('asset library' in f for f in image_findings(EpisodePlan.model_validate(raw)))
    with pytest.raises(ValueError, match='unique'):
        EpisodePlan.model_validate({**illustrated().model_dump(mode='json'),
                                    'illustrations': [illustrated().illustrations[0].model_dump()] * 2})


def test_pictures_are_offered_only_when_the_image_runtime_is_configured():
    assert 'Story pictures' not in episode_prompt(body_source(), '', SHELF)
    assert 'Story pictures' in episode_prompt(body_source(), '', SHELF, illustrations=True)


def test_offered_pictures_must_ask_for_a_fresh_photo_of_the_main_subject_as_the_thumbnail():
    # Owner (2026-10-12): photos are found for each episode's content, not rotated from pre-registered ones. The
    # 2026-10-13 AM plan used only reserve library photos because the prompt preferred them.
    def photo_findings(plan, photos=True):
        return [f for f in episode_violations(plan, body_source(), SHELF, photos=photos)
                if f.startswith(('Story photo', 'Thumbnail is not'))]

    assert [f[:19] for f in photo_findings(illustrated())] == ['Story photo missing']
    photo = illustrated(brief='PHOTO: Korea Exchange headquarters Seoul || SCENE: ' + BRIEF)
    assert [f[:30] for f in photo_findings(photo)] == ['Thumbnail is not a story photo']
    raw = photo.model_dump(mode='json')
    raw['thumbnail_image'] = 'gen-1'
    assert photo_findings(EpisodePlan.model_validate(raw)) == []
    assert photo_findings(illustrated(), photos=False) == []


def test_the_prompt_asks_for_fresh_photos_first_and_keeps_listed_photos_in_reserve():
    offered = episode_prompt(body_source(), '', SHELF, illustrations=True)
    assert 'found fresh for each episode' in offered and 'only a reserve' in offered
    assert 'use an illustration only when no listed photo fits' not in offered
    assert 'found fresh for each episode' not in episode_prompt(body_source(), '', SHELF)


async def test_pictures_are_kept_checked_and_unmade_slots_fall_back(tmp_path):
    from quant_company.config import Settings

    settings = Settings(_env_file=None, video_artifact_dir=tmp_path)
    job = {'id': '02a27a7c-1929-580f-b444-160bca078dc1', 'qa': {}}
    plan = illustrated()
    images = FakeImages()
    made, waiting = await make_next(job, plan, images, settings, tmp_path / str(job['id']) / 'illustrations')
    entry = made['gen-1']
    assert not waiting and entry['request_id'] == request_id(job, plan.illustrations[0]) and entry['width'] == 64
    assert images.calls[0][1:3] == ('gpt-6-astra', 'low') and 'No text, letters' in images.calls[0][3]
    picture = tmp_path / str(job['id']) / 'illustrations' / entry['file']
    assert hashlib.sha256(picture.read_bytes()).hexdigest() == entry['sha256']
    job['qa']['illustrations'] = made
    assert missing(job, plan) == []  # an unchanged brief is never generated again
    library = {'fixture-illustration': {'file': 'illu.png', 'kind': '일러스트'}}
    assert library_entries(job, plan, settings, library)['gen-1']['kind'] == GENERATED
    picture.write_bytes(b'tampered')  # a changed file is not shown: the library fallback is
    assert library_entries(job, plan, settings, library)['gen-1'] == library['fixture-illustration']
    revised = illustrated(brief=BRIEF + ' Wider view.')
    assert [i.id for i in missing(job, revised)] == ['gen-1']  # a revised brief is a new picture
    busy, _ = await make_next(job, revised, FakeImages([ProviderFault('busy', 'later', 30)]), settings, tmp_path)
    assert busy == job['qa']['illustrations']  # nothing recorded: asked again with the same request ID
    quota, waiting = await make_next(job, revised, FakeImages([ProviderFault('quota', 'limit', 900)]), settings, tmp_path)
    assert not waiting and quota['gen-1']['failed'] == 'quota'
    assert library_entries({**job, 'qa': {'illustrations': quota}}, revised, settings, library)['gen-1'] == \
        library['fixture-illustration']


async def test_pictures_are_made_before_speech_and_the_episode_is_delivered(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.video.contracts import VisualReview
    from quant_company.video.runner import VideoRunner

    store, _ = video
    store.settings.video_image_runtime_url = 'http://video-image-runtime:8080'
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    provider.queues['video_episode_v1'] = [illustrated()]
    images = FakeImages()
    original = VideoRunner.__init__

    def with_images(self, company, provider=None, speech=None, renderer=None, youtube=None, images_client=images):
        original(self, company, provider, speech, renderer, youtube, images=images_client)

    monkeypatch.setattr(VideoRunner, '__init__', with_images)
    job, speech = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'uploading', job['error']
    assert len(images.calls) == 1 and job['qa']['illustrations']['gen-1']['sha256']
    assert job['qa']['illustrations_offered'] is True
    plan_request = next(c for c in provider.calls if c.output_contract == 'video_episode_v1')
    assert 'Story pictures' in plan_request.prompt and len(speech.calls) == len(episode().scenes)


def test_fargate_inputs_carry_the_pictures_to_the_same_job_path(tmp_path):
    import io
    import tarfile

    from quant_company.config import Settings
    from quant_company.video.remote import RemoteRender, Storage, render_task

    from .test_video_remote import FakeS3

    settings = Settings(_env_file=None, video_artifact_dir=tmp_path / 'worker', video_asset_dir=tmp_path / 'assets',
                        video_render_bucket='render-bucket')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'manifest.json').write_text('{}')
    job_id = '02a27a7c-1929-580f-b444-160bca078dc1'
    folder = tmp_path / 'worker' / job_id / 'illustrations'
    folder.mkdir(parents=True)
    (folder / 'gen-1-abc.png').write_bytes(png())
    made = {'gen-1': {'request_id': 'r', 'file': 'gen-1-abc.png', 'sha256': hashlib.sha256(png()).hexdigest()}}
    job = {'id': job_id, 'source': {}, 'policy': {'template': 'motion-v2'}, 'qa': {'illustrations': made}}
    s3 = FakeS3()
    plan = illustrated()
    audio = []
    for n in range(len(plan.scenes)):
        audio.append(tmp_path / f'speech-{n:02}.mp3')
        audio[-1].write_bytes(b'speech')
    RemoteRender(settings, s3=s3, ecs=object()).upload_inputs(job, plan, audio, None, 'render-x')
    # Owner (2026-10-13): no 'AI 생성 이미지' text on screen. The render image labels any episode file it is given
    # directly as generated, so a generated picture travels as an unlabelled picture of the upload's library copy.
    frozen = json.loads(Storage(s3, 'render-bucket').get(f'render/{job_id}/render-x/in/job.json'))
    assert frozen['illustrations'] == {}
    uploaded = json.loads(Storage(s3, 'render-bucket').get(f'render/{job_id}/render-x/in/plan.json'))
    assert uploaded['illustrations'][0]['fallback'] == 'episode-gen-1'
    task = Settings(_env_file=None, video_artifact_dir=tmp_path / 'task', video_asset_dir=tmp_path / 'task-assets')
    seen = {}

    class Renderer:
        def render(self, job, plan, directory, audio, texts):
            library = json.loads((tmp_path / 'task-assets' / 'manifest.json').read_text())
            seen['entries'] = library_entries(job, plan, task, library)
            (directory / 'video.mp4').write_bytes(b'video')
            return {'directory': str(directory), 'files': {}, 'duration': 1.0}

    render_task(task, f's3://render-bucket/render/{job_id}/render-x', s3=s3, renderer=Renderer())
    library = json.loads((tmp_path / 'task-assets' / 'manifest.json').read_text())
    assert library['episode-gen-1'] == {'file': f'pictures/episode/{job_id}/gen-1-abc.png', 'kind': '일러스트',
                                        'tags': ['반도체 공장']}
    assert (tmp_path / 'task-assets' / library['episode-gen-1']['file']).read_bytes() == png()
    assert seen['entries']['gen-1']['kind'] == '일러스트'
    assert tarfile.open(fileobj=io.BytesIO(Storage(s3, 'render-bucket').get(
        f'render/{job_id}/render-x/in/assets.tar.gz')), mode='r:gz')


def test_delivery_names_generated_pictures_and_the_youtube_disclosure():
    from quant_company.video.store import delivery_text

    job = {'policy': {}, 'source': {'edition_kind': 'close'}, 'qa': {}}
    plain = delivery_text(job, {'title': 't', 'duration': 290})
    marked = delivery_text(job, {'title': 't', 'duration': 290, 'generated_images': ['gen-1', 'gen-2']})
    assert '생성 그림' not in plain and '생성 그림 2장 포함(화면 표기 없음)' in marked and '합성된 콘텐츠' in marked
    # Remote renders receive generated pictures as library pictures; the job's own pictures give the count.
    job['qa'] = {'illustrations': {'gen-1': {'file': 'gen-1.png'}, 'gen-2': {'file': 'p.jpg', 'kind': '자료사진'},
                                   'gen-3': {'failed': 'quota'}}}
    remote = delivery_text(job, {'title': 't', 'duration': 290})
    assert '생성 그림 1장 포함' in remote and 'AI 생성 이미지' not in remote + marked


def test_the_page_shows_a_generated_picture_without_any_label(tmp_path):
    from quant_company.config import Settings
    from quant_company.video.motion import MotionRenderer

    settings = Settings(_env_file=None, video_artifact_dir=tmp_path / 'video', video_asset_dir=tmp_path / 'assets')
    (tmp_path / 'assets').mkdir()
    for name in ('photo.png', 'illu.png'):
        (tmp_path / 'assets' / name).write_bytes(png())
    shelf = {'fixture-photo': {'file': 'photo.png', 'kind': '자료사진', 'author': 'A', 'license': 'CC BY 4.0'},
             'fixture-illustration': {'file': 'illu.png', 'kind': '일러스트'}}
    job_id = '02a27a7c-1929-580f-b444-160bca078dc1'
    folder = tmp_path / 'video' / job_id / 'illustrations'
    folder.mkdir(parents=True)
    (folder / 'gen-1-abc.png').write_bytes(png())
    job = {'id': job_id, 'source': {}, 'qa': {'illustrations': {'gen-1': {
        'file': 'gen-1-abc.png', 'sha256': hashlib.sha256(png()).hexdigest()}}}}
    renderer = MotionRenderer(settings, library=shelf)
    plan = illustrated()
    renderer.episode_assets = library_entries(job, plan, settings, shelf)
    page, scenes, used = renderer._page(job, plan, tmp_path / 'render')
    head = next(s for s in scenes if s['id'] == 'head')['data']['image']
    # Owner (2026-10-13): no 'AI 생성 이미지' text on screen.
    assert head == {'file': 'img/gen-1-abc.png', 'credit': ''} and (page / 'img' / 'gen-1-abc.png').is_file()
    assert renderer.generated_used == {'gen-1'}
    photo = next(s for s in scenes if s['id'] == 'photo')['data']['image']
    assert photo['credit'] == '자료사진 · A · CC BY 4.0'
    failed = {**job, 'qa': {'illustrations': {'gen-1': {'failed': 'quota'}}}}
    renderer.episode_assets, renderer.generated_used = library_entries(failed, plan, settings, shelf), set()
    _, scenes, _ = renderer._page(failed, plan, tmp_path / 'render-fallback')
    assert next(s for s in scenes if s['id'] == 'head')['data']['image'] == {'file': 'img/illu.png', 'credit': ''}
    assert renderer.generated_used == set()


def test_a_photo_request_is_parsed_and_its_scene_is_the_generated_fallback():
    from quant_company.video import photos
    from quant_company.video.illustrations import prompt

    item = illustrated(brief='PHOTO: SK hynix Icheon || SCENE: ' + BRIEF).illustrations[0]
    assert photos.query(item) == 'SK hynix Icheon' and photos.scene(item) == BRIEF
    assert 'SK hynix' not in prompt(item) and BRIEF in prompt(item)
    plain = illustrated().illustrations[0]
    assert photos.query(plain) is None and photos.scene(plain) == BRIEF


def test_commons_results_keep_only_free_large_photos_that_name_the_subject():
    import httpx

    from quant_company.video import photos

    def page(index, title, licence, width=2400, mime='image/jpeg'):
        return {'index': index, 'title': title, 'imageinfo': [{
            'width': width, 'height': 1600, 'mime': mime, 'thumburl': f'https://upload/{index}.jpg',
            'descriptionurl': f'https://commons/{index}', 'extmetadata': {
                'LicenseShortName': {'value': licence}, 'Artist': {'value': '<a href="x">Jane Doe</a>'},
                'ImageDescription': {'value': ''}, 'LicenseUrl': {'value': 'https://cc/'}}}]}

    pages = {str(i): p for i, p in enumerate([
        page(1, 'File:SK hynix campus Icheon.jpg', 'CC BY-SA 4.0'),
        page(2, 'File:SK hynix logo.svg.png', 'CC BY 4.0', mime='image/png', width=800),  # too small
        page(3, 'File:SK hynix fab.jpg', 'Fair use'),  # not free
        page(4, 'File:Unrelated building.jpg', 'CC0'),  # does not name the subject
        page(5, 'File:Hynix Icheon at night.jpg', 'Public domain'),
    ])}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'query': {'pages': pages}}))
    rows = photos.search('SK hynix Icheon', transport=transport)
    assert [r['title'] for r in rows] == ['File:SK hynix campus Icheon.jpg', 'File:Hynix Icheon at night.jpg']
    assert rows[0]['author'] == 'Jane Doe' and rows[0]['license'] == 'CC BY-SA 4.0'


def test_a_long_photo_request_is_shortened_until_commons_finds_it_and_the_closest_match_leads():
    # 2026-10-13: 'crude oil tanker Strait of Hormuz' and two other long requests found nothing (Commons ANDs words).
    import httpx

    from quant_company.video import photos

    def page(index, title):
        return {'index': index, 'title': title, 'imageinfo': [{
            'width': 2400, 'height': 1600, 'mime': 'image/jpeg', 'thumburl': f'https://upload/{index}.jpg',
            'descriptionurl': f'https://commons/{index}', 'extmetadata': {
                'LicenseShortName': {'value': 'CC BY 4.0'}, 'Artist': {'value': 'Jane Doe'},
                'ImageDescription': {'value': ''}, 'LicenseUrl': {'value': 'https://cc/'}}}]}

    asked = []

    def answer(request):
        query = request.url.params['gsrsearch'].removesuffix(' filetype:bitmap')
        asked.append(query)
        pages = ({'1': page(1, 'File:Crude oil tanker in Port Arthur.jpg'),
                  '2': page(2, 'File:Crude oil tanker in the Strait of Hormuz.jpg')}
                 if len(query.split()) <= 3 else {})
        return httpx.Response(200, json={'query': {'pages': pages}})

    rows = photos.search('crude oil tanker Strait of Hormuz', transport=httpx.MockTransport(answer))
    assert asked == ['crude oil tanker Strait of Hormuz', 'crude oil tanker Strait of', 'crude oil tanker Strait',
                     'crude oil tanker']
    assert [r['title'] for r in rows] == ['File:Crude oil tanker in the Strait of Hormuz.jpg',
                                          'File:Crude oil tanker in Port Arthur.jpg']
    assert 'tokens' not in rows[0]
    asked.clear()
    single = photos.search('Hormuz', transport=httpx.MockTransport(answer))
    assert asked == ['Hormuz'] and [r['title'] for r in single] == ['File:Crude oil tanker in the Strait of Hormuz.jpg']


def test_frame_keeps_a_portrait_whole_and_fills_16_by_9():
    pytest.importorskip('PIL')  # photo framing and checks need the video extra (Pillow)
    import io

    from PIL import Image

    from quant_company.video import photos

    tall = io.BytesIO()
    Image.new('RGB', (800, 1200), (200, 30, 30)).save(tall, format='JPEG')
    framed = Image.open(io.BytesIO(photos.frame(tall.getvalue())))
    assert framed.size == (1920, 1080)
    assert framed.getpixel((960, 540))[0] > 150 and framed.getpixel((40, 540))[0] < 150  # sharp centre, dim sides


async def test_a_checked_real_photo_is_used_and_credited_instead_of_a_generated_picture(brief, video, tmp_path,  # noqa: F811
                                                                                         monkeypatch):
    pytest.importorskip('PIL')  # photo framing and checks need the video extra (Pillow)
    from quant_company.video import photos
    from quant_company.video.contracts import VisualReview
    from quant_company.video.runner import VideoRunner

    store, _ = video
    store.settings.video_image_runtime_url = 'http://video-image-runtime:8080'
    plan = illustrated(brief='PHOTO: SK hynix Icheon || SCENE: ' + BRIEF)
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[]), VisualReview(passed=True, issues=[])])
    provider.queues['video_episode_v1'] = [plan]
    row = {'title': 'File:SK hynix campus Icheon.jpg', 'page': 'https://commons/1', 'url': 'https://upload/1.jpg',
           'license': 'CC BY-SA 4.0', 'license_url': 'https://cc/', 'author': 'Jane Doe', 'width': 2400, 'height': 1600}
    monkeypatch.setattr(photos, 'search', lambda term, **kw: [row])
    monkeypatch.setattr(photos, 'download', lambda r, **kw: png())
    images = FakeImages()
    original = VideoRunner.__init__

    def with_images(self, company, provider=None, speech=None, renderer=None, youtube=None, images_client=images):
        original(self, company, provider, speech, renderer, youtube, images=images_client)

    monkeypatch.setattr(VideoRunner, '__init__', with_images)
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'uploading', (job['error'], {k: v for k, v in job['qa'].items() if 'photo' in k or k == 'illustrations'},
                                         [c.output_contract for c in provider.calls])
    made = job['qa']['illustrations']['gen-1']
    assert made['kind'] == '자료사진' and made['author'] == 'Jane Doe' and not images.calls  # nothing generated
    check = next(c for c in provider.calls if c.request_id.endswith(job['qa']['photo_round'][:8]))
    assert check.output_contract == 'video_visual_review_v1' and len(check.attachments) == 1
    entries = library_entries(job, EpisodePlan.model_validate(job['plan']), store.settings, {})
    assert entries['gen-1']['kind'] == '자료사진' and entries['gen-1']['license'] == 'CC BY-SA 4.0'


async def test_photos_are_looked_up_and_checked_once_per_plan_even_when_one_is_generated(brief, video, tmp_path,  # noqa: F811
                                                                                         monkeypatch):
    pytest.importorskip('PIL')  # photo framing and checks need the video extra (Pillow)
    # 2026-10-13 v2: every generated picture changed the set of missing photos, so Commons and Claude were asked again.
    from quant_company.video import photos
    from quant_company.video.contracts import VisualIssue, VisualReview
    from quant_company.video.runner import VideoRunner

    store, _ = video
    store.settings.video_image_runtime_url = 'http://video-image-runtime:8080'
    raw = illustrated(brief='PHOTO: SK hynix Icheon || SCENE: ' + BRIEF).model_dump(mode='json')
    raw['illustrations'].append({'id': 'gen-2', 'subject': '유조선', 'brief': 'PHOTO: crude oil tanker || SCENE: ' + BRIEF,
                                 'fallback': 'fixture-illustration'})
    raw['thumbnail_image'] = 'gen-2'
    wrong_place = VisualIssue(scene='gen-2-c1', kind='image', detail='다른 해협에서 찍은 사진', fix='다른 사진')
    provider = InspectingProvider(visual=[VisualReview(passed=False, issues=[wrong_place]),
                                          VisualReview(passed=True, issues=[]), VisualReview(passed=True, issues=[])])
    provider.queues['video_episode_v1'] = [EpisodePlan.model_validate(raw)]
    searched = []

    def search(term, **kw):
        searched.append(term)
        return [{'title': f'File:{term}.jpg', 'page': 'https://commons/1', 'url': 'https://upload/1.jpg',
                 'license': 'CC BY 4.0', 'license_url': 'https://cc/', 'author': 'Jane Doe', 'width': 2400, 'height': 1600}]

    monkeypatch.setattr(photos, 'search', search)
    monkeypatch.setattr(photos, 'download', lambda r, **kw: png())
    images = FakeImages()
    original = VideoRunner.__init__

    def with_images(self, company, provider=None, speech=None, renderer=None, youtube=None, images_client=images):
        original(self, company, provider, speech, renderer, youtube, images=images_client)

    monkeypatch.setattr(VideoRunner, '__init__', with_images)
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'uploading', job['error']
    assert searched == ['SK hynix Icheon', 'crude oil tanker']
    assert sum('visual-photos' in c.request_id for c in provider.calls) == 1
    made = job['qa']['illustrations']
    assert made['gen-1']['kind'] == '자료사진' and 'kind' not in made['gen-2'] and len(images.calls) == 1


def test_an_episode_photo_reaches_fargate_as_a_credited_library_photo_not_a_generated_file(tmp_path):
    """The render image predates the photo lookup and labels every episode file as generated: the photo travels as
    a library asset of the upload and becomes its picture's fallback."""
    import io
    import tarfile

    from quant_company.config import Settings
    from quant_company.video.remote import RemoteRender, Storage

    from .test_video_remote import FakeS3

    settings = Settings(_env_file=None, video_artifact_dir=tmp_path / 'worker', video_asset_dir=tmp_path / 'assets',
                        video_render_bucket='render-bucket')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'manifest.json').write_text('{"fixture-illustration": {"file": "illu.png", "kind": "일러스트"}}')
    job_id = '02a27a7c-1929-580f-b444-160bca078dc1'
    folder = tmp_path / 'worker' / job_id / 'illustrations'
    folder.mkdir(parents=True)
    (folder / 'gen-1-photo1-abc.jpg').write_bytes(png())
    made = {'gen-1': {'request_id': 'r', 'file': 'gen-1-photo1-abc.jpg', 'sha256': hashlib.sha256(png()).hexdigest(),
                      'kind': '자료사진', 'subject': '반도체 공장', 'author': 'Jane Doe', 'license': 'CC BY-SA 4.0',
                      'license_url': 'https://cc/', 'source': 'https://commons/1'}}
    job = {'id': job_id, 'source': {}, 'policy': {'template': 'motion-v2'}, 'qa': {'illustrations': made}}
    s3 = FakeS3()
    plan = illustrated()
    audio = []
    for n in range(len(plan.scenes)):
        audio.append(tmp_path / f'speech-{n:02}.mp3')
        audio[-1].write_bytes(b'speech')
    RemoteRender(settings, s3=s3, ecs=object()).upload_inputs(job, plan, audio, None, 'render-x')
    store = Storage(s3, 'render-bucket')
    frozen = json.loads(store.get(f'render/{job_id}/render-x/in/job.json'))
    sent = EpisodePlan.model_validate_json(store.get(f'render/{job_id}/render-x/in/plan.json'))
    assert frozen['illustrations'] == {} and sent.illustrations[0].fallback == 'episode-gen-1'
    with tarfile.open(fileobj=io.BytesIO(store.get(f'render/{job_id}/render-x/in/assets.tar.gz')), mode='r:gz') as tar:
        manifest = json.loads(tar.extractfile('./manifest.json').read())
        assert tar.extractfile(f'./photos/episode/{job_id}/gen-1-photo1-abc.jpg').read() == png()
    assert manifest['episode-gen-1']['kind'] == '자료사진' and manifest['episode-gen-1']['author'] == 'Jane Doe'
    assert 'fixture-illustration' in manifest
    task = Settings(_env_file=None, video_artifact_dir=tmp_path / 'task', video_asset_dir=tmp_path / 'task-assets')
    entries = library_entries({'id': job_id, **frozen}, sent, task, manifest)
    assert entries['gen-1']['kind'] == '자료사진' and entries['gen-1']['license'] == 'CC BY-SA 4.0'
