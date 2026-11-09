"""Download a video with yt-dlp (YouTube, TikTok, Instagram, X, Twitch clips...)."""
from pathlib import Path

import yt_dlp

from ..config import DOWNLOADS


def download(url: str, source_id: int) -> dict:
    out = DOWNLOADS / f"src_{source_id}.%(ext)s"
    opts = {
        "outtmpl": str(out),
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    path = next(DOWNLOADS.glob(f"src_{source_id}.*"))
    return {
        "path": str(path),
        "title": info.get("title") or url,
        "duration": info.get("duration") or 0,
        "channel": info.get("uploader") or "",
        "description": (info.get("description") or "")[:2000],
    }


def fetch_info(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
        return ydl.extract_info(url, download=False)
