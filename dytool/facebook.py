"""Đăng video lên Facebook Page bằng Graph API chính thức (Reels hoặc video thường)."""

from pathlib import Path

import httpx


class FacebookError(RuntimeError):
    pass


class FacebookPage:
    def __init__(self, cfg: dict):
        self.page_id = cfg["page_id"]
        self.token = cfg["page_token"]
        self.ver = cfg.get("graph_version", "v23.0")
        self.mode = cfg.get("mode", "reels")
        if not self.page_id or not self.token:
            raise FacebookError("Thiếu facebook.page_id hoặc facebook.page_token trong config.yaml")
        self.http = httpx.Client(timeout=600)

    def _check(self, r: httpx.Response) -> dict:
        try:
            js = r.json()
        except ValueError:
            js = {}
        if r.status_code >= 400 or "error" in js:
            raise FacebookError(js.get("error", {}).get("message") or r.text[:300])
        return js

    def whoami(self) -> dict:
        r = self.http.get(f"https://graph.facebook.com/{self.ver}/{self.page_id}",
                          params={"fields": "id,name", "access_token": self.token})
        return self._check(r)

    def publish(self, video: Path, caption: str) -> str:
        return self._reel(video, caption) if self.mode == "reels" else self._video(video, caption)

    def _reel(self, video: Path, caption: str) -> str:
        base = f"https://graph.facebook.com/{self.ver}/{self.page_id}/video_reels"
        start = self._check(self.http.post(base, data={"upload_phase": "start", "access_token": self.token}))
        vid = start["video_id"]
        size = video.stat().st_size
        with video.open("rb") as f:
            self._check(self.http.post(
                start.get("upload_url") or f"https://rupload.facebook.com/video-upload/{self.ver}/{vid}",
                headers={"Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(size)},
                content=f.read(),
            ))
        self._check(self.http.post(base, data={
            "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
            "description": caption, "access_token": self.token,
        }))
        return vid

    def _video(self, video: Path, caption: str) -> str:
        with video.open("rb") as f:
            js = self._check(self.http.post(
                f"https://graph-video.facebook.com/{self.ver}/{self.page_id}/videos",
                data={"description": caption, "access_token": self.token},
                files={"source": (video.name, f, "video/mp4")},
            ))
        return js["id"]
