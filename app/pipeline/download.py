"""Download a video with yt-dlp (YouTube, TikTok, Instagram, X, Twitch clips...) or use an uploaded file."""
import re
import shutil
from pathlib import Path

import yt_dlp

from .. import db
from ..config import COOKIES_FILE, DOWNLOADS
from .media import probe_duration

BOT_HINT = ("O YouTube bloqueou o download pelo servidor. Envie seu cookies.txt em Ajustes "
            "(ou envie o arquivo do vídeo direto no Painel) e clique em Tentar de novo.")

COOKIES_EXPIRED = ("O YouTube recusou os cookies enviados (eles foram trocados pelo navegador). Exporte de novo "
                   "usando uma janela anônima, como explicado em Ajustes, e clique em Tentar de novo.")

DISK_FULL = ("Faltou espaço em disco no servidor para juntar o vídeo e o áudio. Escolha a qualidade 480p em "
             "Ajustes (ou use um vídeo mais curto, ou envie o arquivo direto) e clique em Tentar de novo.")


def _clear(source_id, folder=DOWNLOADS):
    for old in Path(folder).glob(f"src_{source_id}.*"):
        if not old.name.endswith(".segments.json"):
            old.unlink(missing_ok=True)


def _opts(extra=None, cookies=True):
    o = {"quiet": True, "noplaylist": True}
    if cookies and COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        o["cookiefile"] = str(COOKIES_FILE)
    o.update(extra or {})
    return o


def download(url: str, source_id, folder=None) -> dict:
    folder = Path(folder or DOWNLOADS)
    if url.startswith("file://"):
        path = url[len("file://"):]
        if not Path(path).exists():
            raise RuntimeError("O arquivo enviado não está mais no servidor (foi apagado para liberar espaço). "
                               "Envie o arquivo de novo pelo Painel.")
        name = Path(path).stem.split("__", 1)[-1]
        return {"path": path, "title": name, "duration": probe_duration(path), "channel": "", "description": ""}

    # Leftovers from an interrupted run (e.g. a video-only "src_1.f137.mp4" part) must not be picked up later.
    _clear(source_id, folder)
    out = folder / f"src_{source_id}.%(ext)s"
    h = int(db.setting("max_video_height", 720) or 720)
    # Joining video and audio briefly needs both parts plus the joined copy, so on a small disk pick the best
    # quality whose size fits (sizes unknown to the site pass: "<?").
    budget = max(20, int((shutil.disk_usage(folder).free / 1e6 - 60) / 2.4))
    fit = f"[filesize<?{budget}M][filesize_approx<?{budget}M]"
    extra = {
        "outtmpl": str(out),
        # Prefer H.264 at up to 30 fps (cheap to decode, small; clips are 30 fps anyway), then any codec.
        "format": "/".join([
            f"bv*[height<={h}][fps<=30][vcodec^=avc1]{fit}+ba[ext=m4a]",
            f"bv*[height<={h}][vcodec^=avc1]{fit}+ba[ext=m4a]",
            f"bv*[height<={h}]{fit}+ba",
            f"b[height<={h}]{fit}",
            f"bv*[height<={h}][fps<=30]+ba", f"bv*[height<={h}]+ba", f"b[height<={h}]", "bv*+ba", "b",
        ]),
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
        _clear(source_id, folder)  # a failed merge leaves big parts behind on a small disk
        msg = str(last)
        if "Conversion failed" in msg or "No space left" in msg:
            raise RuntimeError(DISK_FULL) from last
        if "not a bot" in msg or "Sign in to confirm" in msg:
            if COOKIES_FILE.exists():
                raise RuntimeError(COOKIES_EXPIRED) from last
            raise RuntimeError(BOT_HINT) from last
        if "Requested format is not available" in msg:
            raise RuntimeError("O YouTube não liberou nenhum formato de vídeo para o servidor. Tente de novo em alguns "
                               "minutos, atualize os cookies em Ajustes ou envie o arquivo do vídeo direto.") from last
        raise last
    path = _final_path(info, source_id, folder)
    return {
        "path": str(path),
        "title": info.get("title") or url,
        "duration": info.get("duration") or probe_duration(str(path)),
        "channel": info.get("uploader") or "",
        "description": (info.get("description") or "")[:2000],
    }


def _final_path(info: dict, source_id, folder=DOWNLOADS) -> Path:
    """The merged file yt-dlp produced, never an intermediate video-only/audio-only part."""
    for d in info.get("requested_downloads") or []:
        fp = d.get("filepath")
        if fp and Path(fp).exists():
            return Path(fp)
    final = re.compile(rf"^src_{source_id}\.(mp4|mkv|webm|mov)$")
    candidates = sorted((p for p in Path(folder).glob(f"src_{source_id}.*") if final.match(p.name)),
                        key=lambda p: p.suffix != ".mp4")
    if not candidates:
        raise RuntimeError("Download terminou mas o arquivo final do vídeo não foi encontrado")
    return candidates[0]


def fetch_info(url: str) -> dict:
    with yt_dlp.YoutubeDL(_opts({"skip_download": True})) as ydl:
        return ydl.extract_info(url, download=False)
