"""Lấy danh sách video Douyin qua Web API của TikTokDownloader (DouK-Downloader) và tải file về."""

import re
import time
from pathlib import Path

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")


class DouyinError(RuntimeError):
    pass


def sec_uid_from_url(url: str) -> str:
    m = re.search(r"/user/([\w-]+)", url)
    if not m:
        raise DouyinError(f"Link kênh không hợp lệ (cần dạng douyin.com/user/<sec_uid>): {url}")
    return m.group(1)


def _seconds(hms: str | int | float | None) -> float:
    if isinstance(hms, (int, float)):
        return float(hms)
    if not hms:
        return 0.0
    total = 0.0
    for part in str(hms).split(":"):
        total = total * 60 + float(part)
    return total


def normalize(raw: dict) -> dict | None:
    """Chuẩn hóa 1 item dữ liệu gốc của Douyin (source=True). Bỏ qua ảnh/album (chỉ lấy video).
    Tự phân tích vì bộ extractor của TikTokDownloader trả rỗng với kết quả tìm kiếm."""
    a = raw.get("aweme_info", raw)
    v = a.get("video") or {}
    if not a.get("aweme_id") or a.get("images") or not v:
        return None
    duration = (v.get("duration") or a.get("duration") or 0) / 1000
    # chọn luồng bitrate cao nhất, nếu không có thì dùng play_addr
    streams = [b for b in v.get("bit_rate") or [] if (b.get("play_addr") or {}).get("url_list")]
    best = max(streams, key=lambda b: b.get("bit_rate", 0))["play_addr"] if streams else v.get("play_addr") or {}
    urls = best.get("url_list") or []
    if not urls or duration <= 0:
        return None
    stats = a.get("statistics") or {}
    return {
        "id": str(a["aweme_id"]),
        "desc": a.get("desc") or "",
        "create_ts": a.get("create_time"),
        "likes": stats.get("digg_count") or 0,
        "duration": duration,
        "url": urls[0],
        "meta": {"nickname": (a.get("author") or {}).get("nickname"),
                 "share_url": f"https://www.douyin.com/video/{a['aweme_id']}",
                 "width": best.get("width"), "height": best.get("height")},
    }


class DouyinClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.api = cfg["api"].rstrip("/")
        self.http = httpx.Client(timeout=120, follow_redirects=True)

    def _post(self, path: str, body: dict) -> list[dict]:
        body = {"cookie": self.cfg.get("cookie", ""), "proxy": self.cfg.get("proxy", ""), "source": True, **body}
        try:
            r = self.http.post(self.api + path, json=body)
        except httpx.ConnectError as e:
            raise DouyinError(f"Không kết nối được {self.api}. Đã chạy `dytool server` chưa?") from e
        if r.status_code != 200:
            raise DouyinError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
        js = r.json()
        data = js.get("data")
        if data is None:
            raise DouyinError(f"{path}: {js.get('message')} (cookie hết hạn hoặc bị chặn?)")
        return data if isinstance(data, list) else [data]

    def channel_videos(self, url: str, max_age_days: float = 3) -> list[dict]:
        data = self._post("/douyin/account", {
            "sec_user_id": sec_uid_from_url(url),
            "tab": "post",
            "earliest": max_age_days,   # số ngày tính ngược từ hôm nay
            "count": 18,
        })
        return [v for v in map(normalize, data) if v]

    def search_videos(self, keyword: str, max_results: int = 20,
                      publish_time: int = 0, sort_type: int = 0) -> list[dict]:
        pages = max(1, -(-max_results // 10))
        body = {"keyword": keyword, "pages": pages, "count": 10, "publish_time": publish_time, "sort_type": sort_type}
        try:
            data = self._post("/douyin/search/video", body)
        except DouyinError:
            # Douyin hay trả rỗng ở kiểu "tìm video" (nhất là lọc 1 ngày) -> thử "tìm tổng hợp" rồi lọc lấy video
            data = self._post("/douyin/search/general", body)
        return [v for v in map(normalize, data) if v][:max_results]

    def fresh_url(self, video_id: str) -> str:
        """Lấy lại link tải mới cho 1 video (link CDN của Douyin hết hạn sau vài giờ)."""
        data = self._post("/douyin/detail", {"detail_id": video_id})
        v = normalize(data[0]) if data else None
        if not v:
            raise DouyinError(f"Không lấy lại được link video {video_id}")
        return v["url"]

    def download(self, url: str, dest: Path) -> Path:
        tmp = dest.with_suffix(".part")
        headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/"}
        for attempt in range(3):
            try:
                with self.http.stream("GET", url, headers=headers) as r:
                    r.raise_for_status()
                    with tmp.open("wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                if tmp.stat().st_size < 50_000:
                    raise DouyinError("File tải về quá nhỏ, có thể link đã hết hạn")
                tmp.rename(dest)
                return dest
            except (httpx.HTTPError, DouyinError):
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))
        return dest
