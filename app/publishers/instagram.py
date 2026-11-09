"""Instagram Reels via Instagram API with Instagram Login (Business/Creator account).
Instagram downloads the video from a public URL, so PUBLIC_BASE_URL must be reachable from the internet."""
import time
from urllib.parse import urlencode

import httpx

from .. import db
from ..config import INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET, PUBLIC_BASE_URL

NAME = "Instagram"
GRAPH = "https://graph.instagram.com/v21.0"
REDIRECT = f"{PUBLIC_BASE_URL}/oauth/instagram/callback"


def configured():
    return bool(INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET)


def auth_url(state):
    return "https://www.instagram.com/oauth/authorize?" + urlencode({
        "client_id": INSTAGRAM_APP_ID, "redirect_uri": REDIRECT, "response_type": "code", "state": state,
        "scope": "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_insights",
    })


def exchange(code):
    r = httpx.post("https://api.instagram.com/oauth/access_token", timeout=30, data={
        "client_id": INSTAGRAM_APP_ID, "client_secret": INSTAGRAM_APP_SECRET,
        "grant_type": "authorization_code", "redirect_uri": REDIRECT, "code": code})
    r.raise_for_status()
    short = r.json()["access_token"]
    long = httpx.get("https://graph.instagram.com/access_token", timeout=30, params={
        "grant_type": "ig_exchange_token", "client_secret": INSTAGRAM_APP_SECRET, "access_token": short}).json()
    tok = long["access_token"]
    me = httpx.get(f"{GRAPH}/me", params={"fields": "user_id,username", "access_token": tok}, timeout=30).json()
    db.save_account("instagram", tok, None, time.time() + long.get("expires_in", 5184000) - 86400,
                    {"user_id": me.get("user_id") or me.get("id"), "name": me.get("username", "")})


def _access():
    a = db.account("instagram")
    if not a:
        raise RuntimeError("Instagram não conectado")
    if (a["expires_at"] or 0) < time.time() + 7 * 86400:  # refresh a week before expiry
        t = httpx.get("https://graph.instagram.com/refresh_access_token", timeout=30,
                      params={"grant_type": "ig_refresh_token", "access_token": a["access_token"]}).json()
        if "access_token" in t:
            db.save_account("instagram", t["access_token"], None, time.time() + t["expires_in"] - 86400)
            return t["access_token"], a["extra"]["user_id"]
    return a["access_token"], a["extra"]["user_id"]


def publish(clip, file_path):
    tok, uid = _access()
    video_url = f"{PUBLIC_BASE_URL}/media/{clip['public_token']}/final.mp4"
    caption = (clip["caption_instagram"] + "\n\n" + " ".join(clip["hashtags_list"]))[:2200]
    c = httpx.post(f"{GRAPH}/{uid}/media", timeout=60, data={
        "media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true",
        "access_token": tok}).json()
    if "id" not in c:
        raise RuntimeError(f"Instagram: {c}")
    for _ in range(60):
        st = httpx.get(f"{GRAPH}/{c['id']}", params={"fields": "status_code,status", "access_token": tok},
                       timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram processamento: {st}")
        time.sleep(5)
    p = httpx.post(f"{GRAPH}/{uid}/media_publish", timeout=60,
                   data={"creation_id": c["id"], "access_token": tok}).json()
    if "id" not in p:
        raise RuntimeError(f"Instagram publish: {p}")
    link = httpx.get(f"{GRAPH}/{p['id']}", params={"fields": "permalink", "access_token": tok}, timeout=30).json()
    return p["id"], link.get("permalink", "")


def stats(remote_ids):
    if not remote_ids:
        return {}
    tok, _ = _access()
    out = {}
    for mid in remote_ids:
        try:
            m = httpx.get(f"{GRAPH}/{mid}", params={"fields": "like_count,comments_count", "access_token": tok},
                          timeout=30).json()
            ins = httpx.get(f"{GRAPH}/{mid}/insights", params={"metric": "views", "access_token": tok},
                            timeout=30).json()
            views = next((d["values"][0]["value"] for d in ins.get("data", []) if d["name"] == "views"), 0)
            out[mid] = {"views": views, "likes": m.get("like_count", 0), "comments": m.get("comments_count", 0)}
        except Exception:
            continue
    return out
