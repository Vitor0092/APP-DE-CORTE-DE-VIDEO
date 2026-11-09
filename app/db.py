"""SQLite storage. One small file, no server needed."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DATA_DIR

_DB = DATA_DIR / "hypecut.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, url TEXT, title TEXT, origin TEXT DEFAULT 'manual',
  status TEXT DEFAULT 'queued', step TEXT, error TEXT, video_path TEXT,
  duration REAL, notes TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY, source_id INTEGER, start REAL, end REAL,
  content_type TEXT, title TEXT, hook TEXT, voiceover TEXT, reason TEXT,
  caption_youtube TEXT, caption_tiktok TEXT, caption_instagram TEXT, hashtags TEXT,
  score REAL, file TEXT, public_token TEXT, status TEXT DEFAULT 'rendering', error TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY, clip_id INTEGER, platform TEXT, scheduled_at TEXT,
  status TEXT DEFAULT 'scheduled', remote_id TEXT, url TEXT, error TEXT,
  views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
  posted_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS accounts (
  platform TEXT PRIMARY KEY, access_token TEXT, refresh_token TEXT,
  expires_at REAL, extra TEXT, connected_at TEXT);
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY, name TEXT, metric TEXT, platform TEXT, target REAL, period TEXT);
CREATE TABLE IF NOT EXISTS discoveries (
  video_id TEXT PRIMARY KEY, title TEXT, channel TEXT, views INTEGER, published_at TEXT,
  velocity REAL, score REAL, status TEXT DEFAULT 'new', source_id INTEGER, found_at TEXT);
"""

DEFAULT_SETTINGS = {
    "niche": "GTA 6",
    "discovery_keywords": "GTA 6, GTA VI, GTA 6 trailer, GTA 6 gameplay, GTA 6 leak, GTA 6 novidades",
    "discovery_enabled": "1",
    "discovery_interval_min": "60",
    "discovery_auto_create": "0",
    "discovery_min_velocity": "5000",
    "require_approval": "1",
    "auto_post_platforms": "youtube,tiktok,instagram",
    "post_times": "11:00,15:00,19:00,22:00",
    "clips_per_video": "5",
    "min_clip_seconds": "20",
    "max_clip_seconds": "59",
    "voiceover": "1",
    "language": "pt-BR",
    "channel_style": "Canal brasileiro de notícias e curiosidades de GTA 6. Tom animado, direto, com opinião própria.",
}

DEFAULT_GOALS = [
    ("Shorts por dia no YouTube", "posts", "youtube", 4, "day"),
    ("Vídeos por dia no TikTok", "posts", "tiktok", 4, "day"),
    ("Reels por dia no Instagram", "posts", "instagram", 3, "day"),
    ("Visualizações na semana (todas)", "views", "all", 100000, "week"),
    ("Visualizações no mês (todas)", "views", "all", 1000000, "month"),
    ("Curtidas na semana (todas)", "likes", "all", 5000, "week"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn():
    c = sqlite3.connect(_DB, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, conn() as c:
        c.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))
        if not c.execute("SELECT 1 FROM goals LIMIT 1").fetchone():
            c.executemany("INSERT INTO goals(name,metric,platform,target,period) VALUES (?,?,?,?,?)", DEFAULT_GOALS)


def q(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def run(sql, args=()) -> int:
    with _lock, conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def update(table, row_id, key="id", **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    run(f"UPDATE {table} SET {cols} WHERE {key}=?", (*fields.values(), row_id))


def setting(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    run("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def account(platform):
    a = one("SELECT * FROM accounts WHERE platform=?", (platform,))
    if a:
        a["extra"] = json.loads(a["extra"] or "{}")
    return a


def save_account(platform, access_token, refresh_token=None, expires_at=None, extra=None):
    old = account(platform) or {}
    run(
        "INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?)",
        (platform, access_token, refresh_token or old.get("refresh_token"), expires_at,
         json.dumps({**old.get("extra", {}), **(extra or {})}), now()),
    )
