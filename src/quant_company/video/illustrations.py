"""Story-specific pictures for an episode (owner decision 2026-10-12).

The plan's illustration briefs go to the separate Codex image runtime (providers/video_image_runtime.py: subscription,
image tool only). Each picture is checked (PNG, size, checksum) and kept with the job; a slot that cannot be made uses
its library fallback, so the episode is always produced. One request per brief: an answer lost in transit is read back
from the runtime's receipt and never generated twice.
"""

import hashlib
from pathlib import Path

import httpx

from ..contracts import ProviderFault
from .contracts import digest

STYLE = ("A 16:9 landscape editorial image for a Korean stock-market briefing video. Realistic, calm and premium: a navy, "
         "gold and cream palette, natural light, high detail, one clear subject with space around it. No text, letters, "
         "numbers, logos, flags, watermarks, charts, readable screens or identifiable people. Do not depict a real event, "
         "a specific company's building or a real person.")
MAX_BYTES = 16 * 1024 * 1024
KNOWN = {"quota", "auth", "busy", "uncertain", "invalid_request", "timeout", "invalid_output", "unavailable"}
GENERATED = 'AI 생성 이미지'


def prompt(item):
    from .photos import scene

    # A real-photo request ('PHOTO: … || SCENE: …') falls back to a generated picture of its scene part.
    return f"{STYLE}\nSubject: {item.subject}\nScene: {scene(item) or item.subject}"


def request_id(job, item):
    # The brief is part of the ID: a revised brief is a new picture, an unchanged one is never generated again.
    return f"video-{job['id']}-illustration-{item.id}-{digest({'subject': item.subject, 'brief': item.brief})[:8]}"


def missing(job, plan):
    made = (job.get('qa') or {}).get('illustrations') or {}
    return [item for item in getattr(plan, 'illustrations', []) if (made.get(item.id) or {}).get('request_id')
            != request_id(job, item)]


class ImageClient:
    def __init__(self, base_url, token, *, transport=None, timeout_seconds=420.0):
        self.base_url, self.token = base_url.rstrip('/'), token
        self.transport, self.timeout = transport, timeout_seconds

    def _client(self):
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport, follow_redirects=False, trust_env=False)

    @staticmethod
    def _fault(response):
        try:
            data = response.json()
        except ValueError:
            data = {}
        code = data.get('code') if isinstance(data, dict) and data.get('code') in KNOWN else 'uncertain'
        retry = data.get('retry_after_seconds', 0) if isinstance(data, dict) else 0
        return ProviderFault(code, str(data.get('message', ''))[:300] if isinstance(data, dict) else '',
                             retry if type(retry) is int and 0 <= retry <= 86400 else 0)

    async def generate(self, rid, model, effort, text):
        try:
            async with self._client() as client:
                response = await client.post(f"{self.base_url}/v1/images", headers={"Authorization": f"Bearer {self.token}"},
                                             json={"request_id": rid, "model": model, "reasoning_effort": effort,
                                                   "prompt": text})
        except httpx.TimeoutException:
            # The runtime keeps generating and records the result: ask again with the same request ID.
            raise ProviderFault('pending', 'The image runtime is still generating.', 30) from None
        except httpx.HTTPError:
            raise ProviderFault('unavailable', 'The image runtime is unreachable.') from None
        if response.status_code != 200:
            raise self._fault(response)
        data = response.json()
        if (not isinstance(data, dict) or data.get('request_id') != rid or not isinstance(data.get('sha256'), str)
                or len(data['sha256']) != 64 or not all(type(data.get(k)) is int and data[k] > 0
                                                         for k in ('bytes', 'width', 'height'))):
            raise ProviderFault('invalid_output', 'The image runtime returned an unexpected answer.')
        return data

    async def download(self, rid, sha256):
        try:
            async with self._client() as client:
                response = await client.get(f"{self.base_url}/v1/images/{rid}",
                                            headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError:
            raise ProviderFault('unavailable', 'The image runtime is unreachable.') from None
        if response.status_code != 200:
            raise self._fault(response)
        data = response.content
        if not 0 < len(data) <= MAX_BYTES or hashlib.sha256(data).hexdigest() != sha256:
            raise ProviderFault('invalid_output', 'The downloaded image does not match its receipt.')
        return data


async def make_next(job, plan, client, settings, folder):
    """Makes the next missing picture. Returns (illustrations map, waiting): waiting means ask again later."""
    made = dict((job.get('qa') or {}).get('illustrations') or {})
    for item in missing(job, plan):
        rid = request_id(job, item)
        try:
            meta = await client.generate(rid, settings.video_image_model, settings.video_image_effort, prompt(item))
            data = await client.download(rid, meta['sha256'])
        except ProviderFault as fault:
            if fault.code in {'busy', 'pending'}:
                return made, True
            # Quota, login, refusal or an uncertain turn: this slot uses its library fallback; production continues.
            made[item.id] = {'request_id': rid, 'failed': fault.code}
            return made, False
        name = f"{item.id}-{meta['sha256'][:12]}.png"
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / (name + '.partial')
        partial.write_bytes(data)
        partial.replace(folder / name)
        made[item.id] = {'request_id': rid, 'file': name, 'sha256': meta['sha256'], 'width': meta['width'],
                         'height': meta['height']}
        return made, False
    return made, False


def library_entries(job, plan, settings, library):
    """gen-* IDs for the renderer: the checked picture (shown without a label) or its library fallback."""
    from .render import checksum

    made = job.get('illustrations') or (job.get('qa') or {}).get('illustrations') or {}
    folder = Path(settings.video_artifact_dir) / str(job['id']) / 'illustrations'
    entries = {}
    for item in getattr(plan, 'illustrations', []):
        entry = made.get(item.id) or {}
        path = folder / entry['file'] if entry.get('file') else None
        if path is not None and path.is_file() and checksum(path) == entry.get('sha256') and entry.get('kind') == '자료사진':
            # A real photo found for this episode (video/photos.py): credited like a library photo, never as generated.
            entries[item.id] = {'file': str(path), 'kind': '자료사진', 'subject': entry.get('subject') or item.subject,
                                'author': entry.get('author', ''), 'license': entry.get('license', ''),
                                'license_url': entry.get('license_url', ''), 'source': entry.get('source', ''),
                                'tags': [item.subject]}
        elif path is not None and path.is_file() and checksum(path) == entry.get('sha256'):
            entries[item.id] = {'file': str(path), 'kind': GENERATED, 'author': 'Codex', 'license': '',
                                'tags': [item.subject]}
        elif item.fallback in library:
            entries[item.id] = library[item.fallback]
    return entries
