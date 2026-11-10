"""Download a video with yt-dlp (YouTube, TikTok, Instagram, X, Twitch clips...) or use an uploaded file."""
import subprocess
from pathlib import Path

import yt_dlp

from ..config import COOKIES_FILE, DOWNLOADS

BOT_HINT = ("O YouTube bloqueou o download pelo servidor. Envie seu cookies.txt em Ajustes "
            "(ou envie o arquivo do vídeo direto no Painel) e clique em Tentar de novo.")


def _opts(extra=None):
    o = {"quiet": True, "noplaylist": True}
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        o["cookiefile"] = str(COOKIES_FILE)
    o.update(extra or {})
    return o


def _probe_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def download(url: str, source_id: int) -> dict:
    if url.startswith("file://"):
        path = url[len("file://"):]
        if not Path(path).exists():
            raise RuntimeError("Arquivo enviado não encontrado no servidor")
        name = Path(path).stem.split("__", 1)[-1]
        return {"path": path, "title": name, "duration": _probe_duration(path), "channel": "", "description": ""}

    out = DOWNLOADS / f"src_{source_id}.%(ext)s"
    opts = _opts({
        "outtmpl": str(out),
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
    })
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        msg = str(e)
        if "not a bot" in msg or "Sign in to confirm" in msg or "cookies" in msg:
            raise RuntimeError(BOT_HINT) from e
        raise
    path = next(DOWNLOADS.glob(f"src_{source_id}.*"))
    return {
        "path": str(path),
        "title": info.get("title") or url,
        "duration": info.get("duration") or _probe_duration(str(path)),
        "channel": info.get("uploader") or "",
        "description": (info.get("description") or "")[:2000],
    }


def fetch_info(url: str) -> dict:
    with yt_dlp.YoutubeDL(_opts({"skip_download": True})) as ydl:
        return ydl.extract_info(url, download=False)
