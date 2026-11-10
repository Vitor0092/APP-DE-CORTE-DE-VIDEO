"""Download a video with yt-dlp (YouTube, TikTok, Instagram, X, Twitch clips...) or use an uploaded file."""
import re
from pathlib import Path

import yt_dlp

from ..config import COOKIES_FILE, DOWNLOADS
from .media import probe_duration

BOT_HINT = ("O YouTube bloqueou o download pelo servidor. Envie seu cookies.txt em Ajustes "
            "(ou envie o arquivo do vídeo direto no Painel) e clique em Tentar de novo.")

COOKIES_EXPIRED = ("O YouTube recusou os cookies enviados (eles foram trocados pelo navegador). Exporte de novo "
                   "usando uma janela anônima, como explicado em Ajustes, e clique em Tentar de novo.")


def _opts(extra=None, cookies=True):
    o = {"quiet": True, "noplaylist": True}
    if cookies and COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        o["cookiefile"] = str(COOKIES_FILE)
    o.update(extra or {})
    return o




def download(url: str, source_id: int) -> dict:
    if url.startswith("file://"):
        path = url[len("file://"):]
        if not Path(path).exists():
            raise RuntimeError("Arquivo enviado não encontrado no servidor")
        name = Path(path).stem.split("__", 1)[-1]
        return {"path": path, "title": name, "duration": probe_duration(path), "channel": "", "description": ""}

    # Leftovers from an interrupted run (e.g. a video-only "src_1.f137.mp4" part) must not be picked up later.
    for old in DOWNLOADS.glob(f"src_{source_id}.*"):
        if not old.name.endswith(".segments.json"):
            old.unlink(missing_ok=True)
    out = DOWNLOADS / f"src_{source_id}.%(ext)s"
    extra = {
        "outtmpl": str(out),
        # Prefer mp4 up to 1080p, then anything up to 1080p, then whatever exists.
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
    }
    info, last = None, None
    # Cookies sometimes limit the formats YouTube offers; if so, retry without them.
    for use_cookies in (True, False):
        try:
            with yt_dlp.YoutubeDL(_opts(extra, cookies=use_cookies)) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Exception as e:
            last = e
            if "Requested format is not available" not in str(e) or not COOKIES_FILE.exists():
                break
    if info is None:
        msg = str(last)
        if "not a bot" in msg or "Sign in to confirm" in msg:
            if COOKIES_FILE.exists():
                raise RuntimeError(COOKIES_EXPIRED) from last
            raise RuntimeError(BOT_HINT) from last
        if "Requested format is not available" in msg:
            raise RuntimeError("O YouTube não liberou nenhum formato de vídeo para o servidor. Tente de novo em alguns "
                               "minutos, atualize os cookies em Ajustes ou envie o arquivo do vídeo direto.") from last
        raise last
    path = _final_path(info, source_id)
    return {
        "path": str(path),
        "title": info.get("title") or url,
        "duration": info.get("duration") or probe_duration(str(path)),
        "channel": info.get("uploader") or "",
        "description": (info.get("description") or "")[:2000],
    }


def _final_path(info: dict, source_id: int) -> Path:
    """The merged file yt-dlp produced, never an intermediate video-only/audio-only part."""
    for d in info.get("requested_downloads") or []:
        fp = d.get("filepath")
        if fp and Path(fp).exists():
            return Path(fp)
    final = re.compile(rf"^src_{source_id}\.(mp4|mkv|webm|mov)$")
    candidates = sorted((p for p in DOWNLOADS.glob(f"src_{source_id}.*") if final.match(p.name)),
                        key=lambda p: p.suffix != ".mp4")
    if not candidates:
        raise RuntimeError("Download terminou mas o arquivo final do vídeo não foi encontrado")
    return candidates[0]


def fetch_info(url: str) -> dict:
    with yt_dlp.YoutubeDL(_opts({"skip_download": True})) as ydl:
        return ydl.extract_info(url, download=False)
