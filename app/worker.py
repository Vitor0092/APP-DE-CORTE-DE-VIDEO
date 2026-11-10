"""Background worker: processes videos, posts on schedule, scans trends, refreshes metrics."""
import json
import logging
import re
import secrets
import shutil
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, isolate
from .config import DATA_DIR, DOWNLOADS

log = logging.getLogger("hypecut.worker")
_wake = threading.Event()


def enqueue_source(url: str, origin="manual", notes="") -> int:
    sid = db.run("INSERT INTO sources(url,origin,status,step,notes,created_at) VALUES (?,?,?,?,?,?)",
                 (url.strip(), origin, "queued", "na fila", notes, db.now()))
    _wake.set()
    return sid


def _segments_path(sid):
    return DOWNLOADS / f"src_{sid}.segments.json"


def process_source(src):
    from .pipeline import analyze, download, transcribe
    sid = src["id"]
    step = lambda s: db.update("sources", sid, status="processing", step=s)  # noqa: E731

    step("baixando vídeo")
    # Little free disk: drop every older original video too, so the new download (and its merge) fits.
    cleanup_storage(current=sid, keep=1 if disk_free_mb() > 350 else 0)
    info = isolate.run(download.download, src["url"], sid)
    db.update("sources", sid, title=info["title"], video_path=info["path"], duration=info["duration"])

    step("transcrevendo áudio")
    segs = isolate.run(transcribe.transcribe, info["path"])
    _segments_path(sid).write_text(json.dumps(segs, ensure_ascii=False))
    if not segs:
        step("sem fala no vídeo: a IA vai narrar os cortes")

    step("IA escolhendo momentos virais")
    title = info["title"] + (f" | Observações: {src['notes']}" if src.get("notes") else "")
    ideas = analyze.pick_clips(title, segs, info["duration"], info.get("description", ""))

    for i, c in enumerate(ideas, 1):
        step(f"editando corte {i}/{len(ideas)}")
        cid = db.run("""INSERT INTO clips(source_id,start,end,content_type,title,hook,voiceover,reason,
                        caption_youtube,caption_tiktok,caption_instagram,hashtags,score,public_token,status,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (sid, c["start"], c["end"], c["content_type"], c["title"], c["hook"], c["voiceover"],
                      c["reason"], c["caption_youtube"], c["caption_tiktok"], c["caption_instagram"],
                      json.dumps(c["hashtags"], ensure_ascii=False), c["viral_score"],
                      secrets.token_urlsafe(16), "rendering", db.now()))
        render(cid)

    db.update("sources", sid, status="done", step=f"{len(ideas)} cortes gerados")


def render(clip_id):
    from .pipeline import download
    from .pipeline import render as r
    c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    src = db.one("SELECT * FROM sources WHERE id=?", (c["source_id"],))
    try:
        segs = json.loads(_segments_path(src["id"]).read_text())
        video = src["video_path"]
        if not video or not Path(video).exists():  # removed earlier to free disk space
            if not src["url"].startswith("http"):
                raise RuntimeError("O vídeo original foi apagado para liberar espaço. Envie o arquivo de novo.")
            video = isolate.run(download.download, src["url"], src["id"])["path"]
            db.update("sources", src["id"], video_path=video)
        path = isolate.run(r.render_clip, clip_id, video, segs, c["start"], c["end"], c["hook"], c["voiceover"],
                           use_voice=db.setting("voiceover") == "1")
        if db.setting("require_approval") == "1":
            db.update("clips", clip_id, file=path, status="pending", error=None)
        else:
            db.update("clips", clip_id, file=path, status="approved", error=None)
            schedule_posts(clip_id)
    except Exception as e:
        db.update("clips", clip_id, status="error", error=str(e)[:1000])


def _rm(path):
    if path:
        Path(path).unlink(missing_ok=True)


def cleanup_storage(current=None, keep=1):
    """The Railway trial volume has only 500 MB. Keep the newest finished source video (for re-editing),
    delete the rest, plus leftovers of interrupted downloads and clips that are done with."""
    kept = 0
    for r in db.q("SELECT id, status, video_path FROM sources WHERE video_path IS NOT NULL ORDER BY id DESC"):
        if r["id"] == current or r["status"] in ("queued", "processing"):
            continue
        if r["status"] == "done" and kept < keep:
            kept += 1
            continue
        _rm(r["video_path"])
        db.update("sources", r["id"], video_path=None)
    hour_ago = time.time() - 3600
    for p in DOWNLOADS.iterdir():
        if p.is_file() and re.search(r"\.(part|ytdl)$|\.f\d+\.|\.temp\.", p.name) and p.stat().st_mtime < hour_ago:
            p.unlink(missing_ok=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    for c in db.q("SELECT id, file FROM clips WHERE file IS NOT NULL AND "
                  "(status='rejected' OR (status='posted' AND created_at<?))", (cutoff,)):
        _rm(c["file"])
        db.update("clips", c["id"], file=None)


def storage_used_mb() -> int:
    return round(sum(p.stat().st_size for p in DATA_DIR.rglob("*") if p.is_file()) / 1e6)


def disk_free_mb() -> int:
    return round(shutil.disk_usage(DATA_DIR).free / 1e6)


def _next_slot(platform):
    times = [t.strip() for t in db.setting("post_times", "12:00").split(",") if t.strip()]
    now_local = datetime.now().astimezone()
    taken = {r["scheduled_at"] for r in db.q(
        "SELECT scheduled_at FROM posts WHERE platform=? AND status IN ('scheduled','posting','posted')", (platform,))}
    for day in range(0, 60):
        d = now_local + timedelta(days=day)
        for t in sorted(times):
            hh, mm = map(int, t.split(":"))
            slot = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
            iso = slot.astimezone(timezone.utc).isoformat(timespec="seconds")
            if slot > now_local and iso not in taken:
                return iso
    return db.now()


def schedule_posts(clip_id, platforms=None, now=False):
    platforms = platforms or [p for p in db.setting("auto_post_platforms", "").split(",") if p]
    for p in platforms:
        if not db.account(p):
            continue
        if db.one("SELECT 1 FROM posts WHERE clip_id=? AND platform=? AND status!='error'", (clip_id, p)):
            continue
        db.run("INSERT INTO posts(clip_id,platform,scheduled_at,status,created_at) VALUES (?,?,?,?,?)",
               (clip_id, p, db.now() if now else _next_slot(p), "scheduled", db.now()))
    db.update("clips", clip_id, status="scheduled")
    _wake.set()


def clip_for_publish(clip_id):
    c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    tags = json.loads(c["hashtags"] or "[]")
    c["hashtags_list"] = [t if t.startswith("#") else f"#{t}" for t in tags]
    return c


def publish_due():
    from .publishers import REGISTRY
    for p in db.q("SELECT * FROM posts WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at", (db.now(),)):
        db.update("posts", p["id"], status="posting")
        try:
            c = clip_for_publish(p["clip_id"])
            remote_id, url = REGISTRY[p["platform"]].publish(c, c["file"])
            db.update("posts", p["id"], status="posted", remote_id=remote_id, url=url, posted_at=db.now(), error=None)
            if not db.one("SELECT 1 FROM posts WHERE clip_id=? AND status IN ('scheduled','posting')", (c["id"],)):
                db.update("clips", c["id"], status="posted")
        except Exception as e:
            log.exception("publish failed")
            db.update("posts", p["id"], status="error", error=str(e)[:1000])


def refresh_stats():
    from .publishers import REGISTRY
    for name, mod in REGISTRY.items():
        rows = db.q("SELECT id, remote_id FROM posts WHERE platform=? AND status='posted' AND remote_id IS NOT NULL "
                    "ORDER BY posted_at DESC LIMIT 200", (name,))
        if not rows or not db.account(name):
            continue
        try:
            st = mod.stats([r["remote_id"] for r in rows])
        except Exception:
            log.exception("stats %s", name)
            continue
        for r in rows:
            if r["remote_id"] in st:
                db.update("posts", r["id"], **st[r["remote_id"]])


def _loop():
    from . import discovery
    last_disc, last_stats = 0.0, 0.0
    while True:
        try:
            src = db.one("SELECT * FROM sources WHERE status='queued' ORDER BY id LIMIT 1")
            if src:
                try:
                    process_source(src)
                except Exception as e:
                    traceback.print_exc()
                    db.update("sources", src["id"], status="error", error=str(e)[:1000])
            publish_due()
            interval = int(db.setting("discovery_interval_min", 60)) * 60
            if db.setting("discovery_enabled") == "1" and time.time() - last_disc > interval:
                last_disc = time.time()
                try:
                    discovery.scan()
                except Exception as e:
                    log.warning("discovery: %s", e)
            if time.time() - last_stats > 3 * 3600:
                last_stats = time.time()
                refresh_stats()
        except Exception:
            traceback.print_exc()
        _wake.wait(5 if db.one("SELECT 1 FROM sources WHERE status='queued'") else 30)
        _wake.clear()


def start():
    # A video still "processing" at startup means the server restarted mid-job (often out of memory).
    # Don't requeue it automatically, or a too-heavy video would crash the server in a loop.
    db.run("UPDATE sources SET status='error', error=? WHERE status='processing'",
           ("O processamento foi interrompido porque o servidor reiniciou (provavelmente faltou memória). "
            "Clique em Tentar de novo; se repetir, tente um vídeo mais curto.",))
    db.run("UPDATE clips SET status='error', error='Edição interrompida pelo reinício do servidor' WHERE status='rendering'")
    db.run("UPDATE posts SET status='scheduled' WHERE status='posting'")
    du = shutil.disk_usage(DATA_DIR)
    log.warning("disco %s: %d MB livres de %d MB; arquivos do app: %d MB",
                DATA_DIR, du.free // 10**6, du.total // 10**6, storage_used_mb())
    threading.Thread(target=_loop, daemon=True, name="hypecut-worker").start()
