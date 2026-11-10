import hmac
import json
import secrets
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db, discovery, goals, worker
from .config import ADMIN_PASSWORD, COOKIES_FILE, DOWNLOADS, PLATFORMS, SECRET_KEY
from .publishers import REGISTRY

app = FastAPI(title="HypeCut")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["fromjson"] = lambda s: json.loads(s or "[]")
templates.env.filters["fmt"] = lambda n: f"{int(n or 0):,}".replace(",", ".")

PUBLIC = ("/login", "/media/", "/healthz", "/static/")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not path.startswith(PUBLIC) and not path.startswith("/oauth/") and not request.session.get("auth"):
        return RedirectResponse("/login", 303)
    return await call_next(request)


# Added after require_login so the session is available inside it (last added = outermost).
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60 * 60 * 24 * 30, https_only=False)


@app.on_event("startup")
def _startup():
    db.init()
    worker.start()


def page(request, name, **ctx):
    ctx.update(request=request, active=name, pending=db.one("SELECT COUNT(*) n FROM clips WHERE status='pending'")["n"])
    return templates.TemplateResponse(request, f"{name}.html", ctx)


def back(request, default="/"):
    return RedirectResponse(request.headers.get("referer") or default, 303)


# ---------- auth ----------
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if hmac.compare_digest(password, ADMIN_PASSWORD):
        request.session["auth"] = True
        return RedirectResponse("/", 303)
    return templates.TemplateResponse(request, "login.html", {"error": "Senha incorreta"}, status_code=401)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@app.get("/healthz")
def health():
    return {"ok": True}


# ---------- dashboard / sources ----------
@app.get("/")
def dashboard(request: Request):
    totals = db.one("SELECT COUNT(*) posts, COALESCE(SUM(views),0) views, COALESCE(SUM(likes),0) likes "
                    "FROM posts WHERE status='posted'")
    return page(request, "dashboard",
                sources=db.q("SELECT s.*, (SELECT COUNT(*) FROM clips c WHERE c.source_id=s.id) n "
                             "FROM sources s ORDER BY id DESC LIMIT 15"),
                goals=goals.progress()[:4], totals=totals,
                upcoming=db.q("SELECT p.*, c.title FROM posts p JOIN clips c ON c.id=p.clip_id "
                              "WHERE p.status='scheduled' ORDER BY scheduled_at LIMIT 8"),
                accounts={p: db.account(p) for p in PLATFORMS},
                top=db.q("SELECT d.* FROM discoveries d WHERE status='new' ORDER BY COALESCE(score,0)*velocity DESC LIMIT 5"))


@app.post("/sources")
def add_source(request: Request, urls: str = Form(...), notes: str = Form("")):
    for u in urls.split():
        if u.startswith("http"):
            worker.enqueue_source(u, notes=notes)
    return RedirectResponse("/", 303)


@app.post("/sources/upload")
def upload_source(request: Request, files: list[UploadFile] = File(...), notes: str = Form("")):
    for f in files:
        if not f.filename:
            continue
        safe = "".join(ch for ch in Path(f.filename).name if ch.isalnum() or ch in "._- ")[:80] or "video.mp4"
        dest = DOWNLOADS / f"upload_{secrets.token_hex(4)}__{safe}"
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out, 1024 * 1024)
        worker.enqueue_source(f"file://{dest}", origin="upload", notes=notes)
    return RedirectResponse("/", 303)


@app.post("/sources/{sid}/retry")
def retry_source(request: Request, sid: int):
    db.update("sources", sid, status="queued", step="na fila", error=None)
    worker._wake.set()
    return back(request)


@app.get("/sources/{sid}")
def source_detail(request: Request, sid: int):
    src = db.one("SELECT * FROM sources WHERE id=?", (sid,)) or {}
    return page(request, "clips", title=src.get("title") or src.get("url"), source=src,
                clips=db.q("SELECT * FROM clips WHERE source_id=? ORDER BY score DESC", (sid,)), status=None)


# ---------- clips ----------
@app.get("/clips")
def clips(request: Request, status: str = "pending"):
    where = "" if status == "all" else "WHERE c.status=?"
    rows = db.q(f"SELECT c.*, s.title src_title FROM clips c JOIN sources s ON s.id=c.source_id {where} "
                f"ORDER BY c.id DESC LIMIT 100", () if status == "all" else (status,))
    return page(request, "clips", title="Cortes", clips=rows, status=status, source=None)


@app.get("/clips/{cid}/video")
def clip_video(cid: int):
    c = db.one("SELECT file FROM clips WHERE id=?", (cid,))
    if not c or not c["file"]:
        raise HTTPException(404)
    return FileResponse(c["file"], media_type="video/mp4")


@app.get("/media/{token}/final.mp4")
def public_media(token: str):
    """Public link used only by Instagram to fetch the Reel. Token is random per clip."""
    c = db.one("SELECT id, file FROM clips WHERE public_token=?", (token,))
    if not c or not c["file"]:
        raise HTTPException(404)
    return FileResponse(c["file"], media_type="video/mp4")


@app.post("/clips/{cid}/approve")
def approve(request: Request, cid: int, platforms: list[str] = Form(default=[])):
    worker.schedule_posts(cid, platforms or None)
    return back(request)


@app.post("/clips/{cid}/post-now")
def post_now(request: Request, cid: int, platforms: list[str] = Form(default=[])):
    worker.schedule_posts(cid, platforms or None, now=True)
    return back(request)


@app.post("/clips/{cid}/reject")
def reject(request: Request, cid: int):
    db.update("clips", cid, status="rejected")
    return back(request)


@app.post("/clips/{cid}/edit")
def edit_clip(request: Request, cid: int, title: str = Form(...), hook: str = Form(""), voiceover: str = Form(""),
              caption_youtube: str = Form(""), caption_tiktok: str = Form(""), caption_instagram: str = Form(""),
              hashtags: str = Form(""), start: float = Form(...), end: float = Form(...)):
    old = db.one("SELECT * FROM clips WHERE id=?", (cid,))
    tags = [t if t.startswith("#") else f"#{t}" for t in hashtags.replace(",", " ").split()]
    db.update("clips", cid, title=title, hook=hook, voiceover=voiceover, caption_youtube=caption_youtube,
              caption_tiktok=caption_tiktok, caption_instagram=caption_instagram,
              hashtags=json.dumps(tags, ensure_ascii=False), start=start, end=end)
    if (old["start"], old["end"], old["hook"], old["voiceover"]) != (start, end, hook, voiceover):
        db.update("clips", cid, status="rendering")
        import threading
        threading.Thread(target=worker.render, args=(cid,), daemon=True).start()
    return back(request)


# ---------- posts ----------
@app.get("/posts")
def posts(request: Request):
    return page(request, "posts", posts=db.q(
        "SELECT p.*, c.title FROM posts p JOIN clips c ON c.id=p.clip_id ORDER BY COALESCE(posted_at, scheduled_at) DESC LIMIT 200"))


@app.post("/posts/{pid}/retry")
def retry_post(request: Request, pid: int):
    db.update("posts", pid, status="scheduled", scheduled_at=db.now(), error=None)
    worker._wake.set()
    return back(request)


@app.post("/posts/{pid}/cancel")
def cancel_post(request: Request, pid: int):
    db.update("posts", pid, status="cancelled")
    return back(request)


@app.post("/posts/refresh-stats")
def refresh(request: Request):
    worker.refresh_stats()
    return back(request)


# ---------- discovery ----------
@app.get("/discover")
def discover(request: Request):
    return page(request, "discover", items=db.q(
        "SELECT * FROM discoveries ORDER BY status='new' DESC, COALESCE(score,0)*velocity DESC LIMIT 100"), error=None)


@app.post("/discover/scan")
def discover_scan(request: Request):
    try:
        discovery.scan()
    except Exception as e:
        return page(request, "discover", items=db.q("SELECT * FROM discoveries ORDER BY velocity DESC LIMIT 100"),
                    error=str(e))
    return RedirectResponse("/discover", 303)


@app.post("/discover/{vid}/create")
def discover_create(request: Request, vid: str):
    discovery.create_source(vid, origin="descoberta")
    return back(request)


@app.post("/discover/{vid}/ignore")
def discover_ignore(request: Request, vid: str):
    db.update("discoveries", vid, key="video_id", status="ignorado")
    return back(request)


# ---------- goals ----------
@app.get("/goals")
def goals_page(request: Request):
    mix_rows = {r["content_type"]: r["n"] for r in db.q(
        "SELECT c.content_type, COUNT(*) n FROM posts p JOIN clips c ON c.id=p.clip_id WHERE p.status='posted' "
        "GROUP BY c.content_type")}
    total = sum(mix_rows.values()) or 1
    mix = [{"key": k, "label": l, "target": t, "tip": tip, "actual": round(100 * mix_rows.get(k, 0) / total)}
           for k, l, t, tip in goals.CONTENT_MIX]
    return page(request, "goals", goals=goals.progress(), mix=mix, platforms=PLATFORMS)


@app.post("/goals")
def add_goal(request: Request, name: str = Form(...), metric: str = Form(...), platform: str = Form(...),
             target: float = Form(...), period: str = Form(...)):
    db.run("INSERT INTO goals(name,metric,platform,target,period) VALUES (?,?,?,?,?)",
           (name, metric, platform, target, period))
    return RedirectResponse("/goals", 303)


@app.post("/goals/{gid}/delete")
def del_goal(request: Request, gid: int):
    db.run("DELETE FROM goals WHERE id=?", (gid,))
    return RedirectResponse("/goals", 303)


# ---------- accounts / OAuth ----------
@app.get("/accounts")
def accounts(request: Request):
    return page(request, "accounts", items=[
        {"key": k, "name": m.NAME, "configured": m.configured(), "account": db.account(k), "redirect": m.REDIRECT}
        for k, m in REGISTRY.items()])


@app.get("/connect/{platform}")
def connect(request: Request, platform: str):
    mod = REGISTRY.get(platform)
    if not mod:
        raise HTTPException(404)
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(mod.auth_url(state), 303)


@app.get("/oauth/{platform}/callback")
def oauth_callback(request: Request, platform: str, code: str = "", state: str = "", error: str = ""):
    if not request.session.get("auth"):
        return RedirectResponse("/login", 303)
    if error or not code or state != request.session.get("oauth_state"):
        raise HTTPException(400, f"Falha ao conectar: {error or 'state inválido'}")
    REGISTRY[platform].exchange(code)
    return RedirectResponse("/accounts", 303)


@app.post("/accounts/{platform}/disconnect")
def disconnect(request: Request, platform: str):
    db.run("DELETE FROM accounts WHERE platform=?", (platform,))
    return RedirectResponse("/accounts", 303)


# ---------- settings ----------
SETTING_KEYS = list(db.DEFAULT_SETTINGS) + ["youtube_privacy", "tiktok_privacy"]
CHECKBOXES = {"discovery_enabled", "discovery_auto_create", "require_approval", "voiceover"}


@app.get("/settings")
def settings(request: Request):
    cookies = None
    if COOKIES_FILE.exists():
        from datetime import datetime
        cookies = datetime.fromtimestamp(COOKIES_FILE.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
    return page(request, "settings", s={k: db.setting(k, "") for k in SETTING_KEYS}, platforms=PLATFORMS,
                cookies=cookies, storage_mb=worker.storage_used_mb(), free_mb=worker.disk_free_mb())


@app.post("/settings/cleanup")
def cleanup(request: Request):
    worker.cleanup_storage(keep=0)
    return RedirectResponse("/settings?ok=1", 303)


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    for k in SETTING_KEYS:
        if k in CHECKBOXES:
            db.set_setting(k, "1" if form.get(k) else "0")
        elif k == "auto_post_platforms":
            db.set_setting(k, ",".join(form.getlist(k)))
        elif k in form:
            db.set_setting(k, form.get(k))
    return RedirectResponse("/settings?ok=1", 303)


@app.post("/settings/cookies")
async def save_cookies(request: Request, cookies: UploadFile = File(None), cookies_text: str = Form("")):
    data = (await cookies.read()).decode("utf-8", "ignore") if cookies and cookies.filename else cookies_text
    if data.strip():
        COOKIES_FILE.write_text(data if data.endswith("\n") else data + "\n", encoding="utf-8")
    return RedirectResponse("/settings?ok=1", 303)


@app.post("/settings/cookies/delete")
def delete_cookies(request: Request):
    COOKIES_FILE.unlink(missing_ok=True)
    return RedirectResponse("/settings?ok=1", 303)


@app.get("/api/status")
def api_status():
    return JSONResponse({
        "sources": db.q("SELECT id,status,step,title FROM sources WHERE status IN ('queued','processing')"),
        "pending": db.one("SELECT COUNT(*) n FROM clips WHERE status='pending'")["n"],
    })
