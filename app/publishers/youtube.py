"""YouTube Shorts via YouTube Data API v3 (official OAuth)."""
import os
import time
from urllib.parse import urlencode

import httpx

from .. import db
from ..config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, PUBLIC_BASE_URL

NAME = "YouTube"
REDIRECT = f"{PUBLIC_BASE_URL}/oauth/youtube/callback"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


def configured():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def auth_url(state):
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state,
    })


def _token(data):
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, **data}, timeout=30)
    r.raise_for_status()
    return r.json()


def exchange(code):
    t = _token({"code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    access = t["access_token"]
    ch = httpx.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                   headers={"Authorization": f"Bearer {access}"}, timeout=30).json()
    name = (ch.get("items") or [{}])[0].get("snippet", {}).get("title", "")
    db.save_account("youtube", access, t.get("refresh_token"), time.time() + t.get("expires_in", 3600) - 60,
                    {"name": name})


def _access():
    a = db.account("youtube")
    if not a:
        raise RuntimeError("YouTube não conectado")
    if (a["expires_at"] or 0) < time.time():
        t = _token({"refresh_token": a["refresh_token"], "grant_type": "refresh_token"})
        db.save_account("youtube", t["access_token"], a["refresh_token"], time.time() + t["expires_in"] - 60)
        return t["access_token"]
    return a["access_token"]


def publish(clip, file_path):
    tok = _access()
    tags = [h.lstrip("#") for h in clip["hashtags_list"]][:15]
    title = clip["title"][:90]
    if "#shorts" not in title.lower():
        title = (title + " #Shorts")[:100]
    meta = {
        "snippet": {"title": title, "categoryId": "20", "tags": tags,
                    "description": clip["caption_youtube"] + "\n\n" + " ".join(clip["hashtags_list"])},
        "status": {"privacyStatus": db.setting("youtube_privacy", "public"), "selfDeclaredMadeForKids": False},
    }
    size = os.path.getsize(file_path)
    r = httpx.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        json=meta, timeout=60,
        headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": "video/mp4",
                 "X-Upload-Content-Length": str(size)})
    r.raise_for_status()
    with open(file_path, "rb") as f:
        up = httpx.put(r.headers["Location"], content=f.read(), timeout=600,
                       headers={"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"})
    up.raise_for_status()
    vid = up.json()["id"]
    return vid, f"https://youtube.com/shorts/{vid}"


def stats(remote_ids):
    if not remote_ids:
        return {}
    tok = _access()
    out = {}
    for i in range(0, len(remote_ids), 50):
        r = httpx.get("https://www.googleapis.com/youtube/v3/videos", timeout=30,
                      params={"part": "statistics", "id": ",".join(remote_ids[i:i + 50])},
                      headers={"Authorization": f"Bearer {tok}"}).json()
        for it in r.get("items", []):
            s = it["statistics"]
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0))}
    return out
