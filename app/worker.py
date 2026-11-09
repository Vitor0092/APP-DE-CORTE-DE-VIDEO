"""Background worker: processes videos, posts on schedule, scans trends, refreshes metrics."""
import json
import logging
import secrets
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import db
from .config import DOWNLOADS

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
    info = download.download(src["url"], sid)
    db.update("sources", sid, title=info["title"], video_path=info["path"], duration=info["duration"])

    step("transcrevendo áudio")
    segs = transcribe.transcribe(info["path"])
    _segments_path(sid).write_text(json.dumps(segs, ensure_ascii=False))

    step("IA escolhendo momentos virais")
    title = info["title"] + (f" | Observações: {src['notes']}" if src.get("notes") else "")
    ideas = analyze.pick_clips(title, segs, info["duration"])

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
    from .pipeline import render as r
    c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    src = db.one("SELECT * FROM sources WHERE id=?", (c["source_id"],))
    try:
        segs = json.loads(_segments_path(src["id"]).read_text())
        path = r.render_clip(clip_id, src["video_path"], segs, c["start"], c["end"], c["hook"], c["voiceover"],
                             use_voice=db.setting("voiceover") == "1")
        if db.setting("require_approval") == "1":
            db.update("clips", clip_id, file=path, status="pending", error=None)
        else:
            db.update("clips", clip_id, file=path, status="approved", error=None)
            schedule_posts(clip_id)
    except Exception as e:
        db.update("clips", clip_id, status="error", error=str(e)[:1000])


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
    # Anything left "processing"/"posting" after a restart goes back to the queue.
    db.run("UPDATE sources SET status='queued' WHERE status='processing'")
    db.run("UPDATE posts SET status='scheduled' WHERE status='posting'")
    threading.Thread(target=_loop, daemon=True, name="hypecut-worker").start()
