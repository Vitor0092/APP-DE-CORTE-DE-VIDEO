"""TikTok Content Posting API (official OAuth).
Note: until TikTok audits your app, direct posts are forced to private (SELF_ONLY)."""
import math
import os
import time
from urllib.parse import urlencode

import httpx

from .. import db
from ..config import PUBLIC_BASE_URL, TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET

NAME = "TikTok"
API = "https://open.tiktokapis.com"
REDIRECT = f"{PUBLIC_BASE_URL}/oauth/tiktok/callback"


def configured():
    return bool(TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET)


def auth_url(state):
    return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key": TIKTOK_CLIENT_KEY, "response_type": "code", "redirect_uri": REDIRECT, "state": state,
        "scope": "user.info.basic,video.publish,video.upload,video.list",
    })


def _token(data):
    r = httpx.post(f"{API}/v2/oauth/token/", timeout=30, data={
        "client_key": TIKTOK_CLIENT_KEY, "client_secret": TIKTOK_CLIENT_SECRET, **data})
    r.raise_for_status()
    t = r.json()
    if "access_token" not in t:
        raise RuntimeError(f"TikTok OAuth: {t}")
    db.save_account("tiktok", t["access_token"], t.get("refresh_token"),
                    time.time() + t.get("expires_in", 86400) - 60, {"open_id": t.get("open_id")})
    return t["access_token"]


def exchange(code):
    tok = _token({"code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    u = httpx.get(f"{API}/v2/user/info/?fields=display_name", headers={"Authorization": f"Bearer {tok}"},
                  timeout=30).json()
    db.save_account("tiktok", tok, extra={"name": u.get("data", {}).get("user", {}).get("display_name", "")})


def _access():
    a = db.account("tiktok")
    if not a:
        raise RuntimeError("TikTok não conectado")
    if (a["expires_at"] or 0) < time.time():
        return _token({"grant_type": "refresh_token", "refresh_token": a["refresh_token"]})
    return a["access_token"]


def _post(path, tok, payload):
    r = httpx.post(f"{API}{path}", json=payload, timeout=60,
                   headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"})
    j = r.json()
    if j.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok: {j['error']}")
    return j.get("data", {})


def publish(clip, file_path):
    tok = _access()
    info = _post("/v2/post/publish/creator_info/query/", tok, {})
    wanted = db.setting("tiktok_privacy", "PUBLIC_TO_EVERYONE")
    options = info.get("privacy_level_options") or ["SELF_ONLY"]
    privacy = wanted if wanted in options else options[0]
    size = os.path.getsize(file_path)
    chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
    total = max(1, math.floor(size / chunk))
    caption = (clip["caption_tiktok"] + " " + " ".join(clip["hashtags_list"]))[:2200]
    data = _post("/v2/post/publish/video/init/", tok, {
        "post_info": {"title": caption, "privacy_level": privacy, "disable_comment": False,
                      "disable_duet": False, "disable_stitch": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                        "total_chunk_count": total},
    })
    with open(file_path, "rb") as f:
        for i in range(total):
            begin = i * chunk
            length = size - begin if i == total - 1 else chunk
            f.seek(begin)
            httpx.put(data["upload_url"], content=f.read(length), timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(length),
                "Content-Range": f"bytes {begin}-{begin + length - 1}/{size}"}).raise_for_status()
    publish_id = data["publish_id"]
    for _ in range(60):
        st = _post("/v2/post/publish/status/fetch/", tok, {"publish_id": publish_id})
        if st.get("status") == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or []
            vid = str(ids[0]) if ids else publish_id
            return vid, (f"https://www.tiktok.com/video/{ids[0]}" if ids else "")
        if st.get("status") == "FAILED":
            raise RuntimeError(f"TikTok falhou: {st.get('fail_reason')}")
        time.sleep(5)
    return publish_id, ""


def stats(remote_ids):
    ids = [i for i in remote_ids if i.isdigit()]
    if not ids:
        return {}
    tok = _access()
    out = {}
    for i in range(0, len(ids), 20):
        r = httpx.post(f"{API}/v2/video/query/?fields=id,view_count,like_count,comment_count", timeout=30,
                       headers={"Authorization": f"Bearer {tok}"},
                       json={"filters": {"video_ids": ids[i:i + 20]}}).json()
        for v in r.get("data", {}).get("videos", []):
            out[str(v["id"])] = {"views": v.get("view_count", 0), "likes": v.get("like_count", 0),
                                 "comments": v.get("comment_count", 0)}
    return out
