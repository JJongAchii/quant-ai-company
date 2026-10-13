import asyncio
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from .auth import write_secret
from .render import verify_artifacts

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"


def session_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "www.googleapis.com" or parsed.username
            or parsed.port not in (443, None) or parsed.path != "/upload/youtube/v3/videos"):
        raise ValueError("Unexpected YouTube upload session")
    return url


class YouTube:
    def __init__(self, settings, token=None, transport=None):
        self.settings, self.test_token, self.transport = settings, token, transport

    async def token(self):
        if self.test_token is not None:
            return self.test_token

        def load():
            import json

            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials

            path = self.settings.video_credentials_dir / "youtube-tokens.json"
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise ValueError("YouTube credentials must be private regular files")
            credentials = Credentials.from_authorized_user_file(str(path))
            if not credentials.valid:
                credentials.refresh(Request())
                write_secret(path, json.loads(credentials.to_json()))
            return credentials.token

        return await asyncio.to_thread(load)

    async def request(self, method, url, **kwargs):
        token = await self.token()
        async with httpx.AsyncClient(timeout=90, transport=self.transport, trust_env=False,
                                     follow_redirects=False) as client:
            return await client.request(method, url, headers={"Authorization": "Bearer " + token,
                                        **kwargs.pop("headers", {})}, **kwargs)

    async def account(self, channel):
        response = await self.request("GET", API + "/channels", params={"part": "id", "mine": "true"})
        response.raise_for_status()
        if channel not in {x["id"] for x in response.json()["items"]}:
            raise ValueError("YouTube account does not own configured channel")

    async def initiate(self, job):
        path = verify_artifacts(job["artifacts"], self.settings.video_artifact_dir) / "video.mp4"
        await self.account(job["policy"]["youtube_channel"])
        description = job["artifacts"]["description"]
        if len(description.encode()) > 5000:
            raise ValueError("YouTube description exceeds limit")
        response = await self.request("POST", UPLOAD + "/videos", params={"uploadType": "resumable", "part": "snippet,status"},
            headers={"X-Upload-Content-Length": str(path.stat().st_size), "X-Upload-Content-Type": "video/mp4"},
            json={"snippet": {"title": job["artifacts"].get("title", job["plan"]["title"]), "description": description,
                              "categoryId": "25", "defaultLanguage": "ko", **({"tags": job["artifacts"]["tags"]} if job["artifacts"].get("tags") else {})},
                  "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}})
        response.raise_for_status()
        return session_url(response.headers["location"])

    async def upload(self, job):
        directory = verify_artifacts(job["artifacts"], self.settings.video_artifact_dir)
        path = directory / "video.mp4"
        size, url = path.stat().st_size, session_url(job["upload_session"])
        # Query existing session after every restart; never create a replacement session here.
        response = await self.request("PUT", url, headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"}, content=b"")
        with path.open("rb") as stream:
            last_offset = -1
            while response.status_code == 308:
                previous = response.headers.get("range")
                if previous and not re.fullmatch(r"bytes=0-\d+", previous):
                    raise ValueError("Invalid upload offset")
                offset = int(previous.split("-")[1]) + 1 if previous else 0
                if offset >= size or offset <= last_offset:
                    raise ValueError("Upload made no progress or lacks a final receipt; retain session")
                last_offset = offset
                stream.seek(offset)
                data = stream.read(8 * 1024 * 1024)
                response = await self.request("PUT", url, headers={"Content-Type": "video/mp4",
                    "Content-Range": f"bytes {offset}-{offset+len(data)-1}/{size}"}, content=data)
        response.raise_for_status()
        identity = response.json().get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{11}", identity):
            raise ValueError("Upload result lacks a video ID; reconcile existing session")
        return identity

    async def video(self, identity):
        response = await self.request("GET", API + "/videos", params={"part": "status,snippet,processingDetails", "id": identity})
        response.raise_for_status()
        rows = response.json()["items"]
        if len(rows) != 1:
            raise ValueError("Uploaded video unavailable")
        return rows[0]

    async def thumbnail(self, job):
        path = verify_artifacts(job["artifacts"], self.settings.video_artifact_dir) / "thumbnail.png"
        response = await self.request("POST", UPLOAD + "/thumbnails/set",
                                      params={"videoId": job["youtube_id"], "uploadType": "media"},
                                      headers={"Content-Type": "image/png"}, content=path.read_bytes())
        response.raise_for_status()
        return {"video_id": job["youtube_id"], "thumbnail_set": True}

    async def captions(self, job):
        path = verify_artifacts(job["artifacts"], self.settings.video_artifact_dir) / "subtitles.srt"
        import json

        metadata = {"snippet": {"videoId": job["youtube_id"], "language": "ko", "name": "한국어", "isDraft": False}}
        boundary = "quantcompany-caption-boundary"
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                + json.dumps(metadata, ensure_ascii=False) + f"\r\n--{boundary}\r\nContent-Type: application/x-subrip\r\n\r\n").encode()
        body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        response = await self.request("POST", UPLOAD + "/captions", params={"part": "snippet", "uploadType": "multipart"},
                                      headers={"Content-Type": "multipart/related; boundary=" + boundary}, content=body)
        response.raise_for_status()
        return {"caption_id": response.json()["id"]}

    async def publish(self, job):
        await self.account(job["policy"]["youtube_channel"])
        current = await self.video(job["youtube_id"])
        if current["snippet"]["channelId"] != job["policy"]["youtube_channel"]:
            raise ValueError("Video belongs to another channel")
        if current["status"]["privacyStatus"] != "private":
            raise ValueError("Unexpected video visibility before public release")
        if current.get("processingDetails", {}).get("processingStatus") != "succeeded":
            raise ValueError("YouTube processing has not succeeded")
        status = {k: v for k, v in current["status"].items()
                  if k in {"license", "embeddable", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia"}}
        if datetime.now(UTC) >= job['publish_deadline']:
            raise ValueError('Public release deadline expired; keep private')
        response = await self.request("PUT", API + "/videos", params={"part": "status"},
                                      json={"id": job["youtube_id"], "status": {**status, "privacyStatus": "public"}})
        response.raise_for_status()
        if response.json().get("status", {}).get("privacyStatus") != "public":
            raise ValueError("YouTube did not confirm public visibility")
        return {"video_id": job["youtube_id"], "privacy": "public"}
