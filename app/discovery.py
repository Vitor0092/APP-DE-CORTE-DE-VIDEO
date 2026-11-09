"""Finds trending videos of the niche on YouTube and (optionally) turns the best ones into clips."""
from datetime import datetime, timedelta, timezone

import httpx

from . import db
from .config import YOUTUBE_API_KEY

YT = "https://www.googleapis.com/youtube/v3"


def scan(hours: int = 48, per_keyword: int = 10) -> int:
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY não configurada (necessária para descobrir vídeos)")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = set()
    for kw in [k.strip() for k in db.setting("discovery_keywords", "").split(",") if k.strip()]:
        r = httpx.get(f"{YT}/search", timeout=30, params={
            "key": YOUTUBE_API_KEY, "part": "id", "q": kw, "type": "video", "order": "viewCount",
            "publishedAfter": since, "maxResults": per_keyword, "relevanceLanguage": "pt",
        }).json()
        ids.update(i["id"]["videoId"] for i in r.get("items", []))
    found = []
    ids = list(ids)
    for i in range(0, len(ids), 50):
        r = httpx.get(f"{YT}/videos", timeout=30, params={
            "key": YOUTUBE_API_KEY, "part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50])}).json()
        for v in r.get("items", []):
            pub = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
            age_h = max(1.0, (datetime.now(timezone.utc) - pub).total_seconds() / 3600)
            views = int(v["statistics"].get("viewCount", 0))
            found.append({"video_id": v["id"], "title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
                          "views": views, "published_at": v["snippet"]["publishedAt"], "velocity": views / age_h})
    new = [f for f in found if not db.one("SELECT 1 FROM discoveries WHERE video_id=?", (f["video_id"],))]
    scores = {}
    if new:
        try:
            from .pipeline.analyze import rate_trending
            scores = rate_trending(new[:60])
        except Exception:
            scores = {}
    for f in found:
        score = scores.get(f["video_id"])
        db.run("""INSERT INTO discoveries(video_id,title,channel,views,published_at,velocity,score,found_at)
                  VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET views=excluded.views,
                  velocity=excluded.velocity, score=COALESCE(excluded.score, discoveries.score)""",
               (f["video_id"], f["title"], f["channel"], f["views"], f["published_at"], f["velocity"], score, db.now()))
    if db.setting("discovery_auto_create") == "1":
        auto_create()
    return len(new)


def auto_create(limit: int = 3):
    min_vel = float(db.setting("discovery_min_velocity", 5000))
    rows = db.q("""SELECT * FROM discoveries WHERE status='new' AND velocity>=? AND COALESCE(score,60)>=60
                   ORDER BY COALESCE(score,0)*velocity DESC LIMIT ?""", (min_vel, limit))
    for r in rows:
        create_source(r["video_id"], origin="descoberta")


def create_source(video_id: str, origin="descoberta"):
    from .worker import enqueue_source
    sid = enqueue_source(f"https://www.youtube.com/watch?v={video_id}", origin=origin)
    db.update("discoveries", video_id, key="video_id", status="usado", source_id=sid)
    return sid
