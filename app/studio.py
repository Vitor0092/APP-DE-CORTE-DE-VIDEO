"""Video studio: a footage library (gameplay etc.) and "create my own video from an example" jobs."""
import json
import secrets
import shutil
import traceback
from pathlib import Path

from . import db, isolate
from .config import LIBRARY, STUDIO, TTS_VOICE
from .pipeline.media import probe_duration

NO_FOOTAGE = ("Não há cenas para usar: envie jogatinas na Biblioteca ou marque \"Usar cenas do vídeo de exemplo\" "
              "e clique em Tentar de novo.")


def safe_name(filename: str) -> str:
    return "".join(ch for ch in Path(filename or "").name if ch.isalnum() or ch in "._- ")[:80] or "video.mp4"


# ---------- library ----------
def add_library_file(path: Path, title: str):
    d = probe_duration(str(path))
    db.run("INSERT INTO library(title,path,duration,size_mb,status,error,created_at) VALUES (?,?,?,?,?,?,?)",
           (title[:100], str(path), d, round(path.stat().st_size / 1e6, 1), "ready" if d > 0 else "error",
            None if d > 0 else "Não consegui ler este vídeo.", db.now()))


def add_library_link(url: str):
    db.run("INSERT INTO library(title,url,status,created_at) VALUES (?,?,'queued',?)", (url, url, db.now()))


def process_library(item):
    from .pipeline import download
    db.update("library", item["id"], status="processing", error=None)
    try:
        info = isolate.run(download.download, item["url"], f"lib{item['id']}", folder=str(LIBRARY))
        p = Path(info["path"])
        db.update("library", item["id"], title=(info["title"] or item["url"])[:100], path=str(p),
                  duration=info["duration"], size_mb=round(p.stat().st_size / 1e6, 1), status="ready")
    except Exception as e:
        db.update("library", item["id"], status="error", error=str(e)[:500])


def delete_library(lid: int):
    it = db.one("SELECT path FROM library WHERE id=?", (lid,))
    if it and it["path"]:
        Path(it["path"]).unlink(missing_ok=True)
    db.run("DELETE FROM library WHERE id=?", (lid,))


# ---------- creations ----------
def workdir(cid: int) -> Path:
    return STUDIO / f"crt_{cid}"


def tidy(cid: int, keep_upload: bool):
    """Drop working files; an uploaded example is kept while the job may still be retried."""
    w = workdir(cid)
    if not w.exists():
        return
    for p in w.iterdir():
        if keep_upload and p.name.startswith("upload__"):
            continue
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    if not any(w.iterdir()):
        w.rmdir()


def delete_creation(cid: int):
    tidy(cid, keep_upload=False)
    db.run("DELETE FROM creations WHERE id=?", (cid,))


def _pick_shots(plan_scenes: list, shots: list) -> list:
    by_id = {s["id"]: s for s in shots}
    uses: dict[int, int] = {}
    out = []
    for i, sc in enumerate(plan_scenes):
        shot = by_id.get(sc["shot_id"]) or next((s for s in shots if s["id"] not in uses), shots[i % len(shots)])
        n = uses.get(shot["id"], 0)
        uses[shot["id"]] = n + 1
        # A repeated image continues a few seconds later in the same video instead of showing the same frames.
        out.append({**sc, "path": shot["path"], "t": shot["t"] + 4 * n, "duration": shot["duration"]})
    return out


def process_creation(cr):
    from . import worker
    from .pipeline import compose, download, frames, script, transcribe
    cid = cr["id"]
    work = workdir(cid)
    work.mkdir(parents=True, exist_ok=True)

    def step(s):
        db.update("creations", cid, status="processing", step=s, error=None)

    ok = False
    try:
        step("baixando o vídeo de exemplo")
        worker.free_up_disk()
        ex = isolate.run(download.download, cr["example_url"], "ex", folder=str(work))
        db.update("creations", cid, example_title=ex["title"])

        step("ouvindo o vídeo de exemplo")
        segs = isolate.run(transcribe.transcribe, ex["path"])

        step("separando as cenas")
        footage = []
        if cr["use_example"]:
            footage.append({"name": "exemplo", "path": ex["path"], "duration": ex["duration"]})
        if cr["use_library"]:
            footage += [{"name": (r["title"] or "jogatina")[:40], "path": r["path"], "duration": r["duration"]}
                        for r in db.q("SELECT * FROM library WHERE status='ready' ORDER BY id DESC")]
        footage = [f for f in footage if f["path"] and Path(f["path"]).exists()]
        if not footage:
            raise RuntimeError(NO_FOOTAGE)
        shots, sheets = isolate.run(frames.candidates, footage, str(work / "shots"))
        style = "" if cr["use_example"] else isolate.run(frames.style_sheet, ex["path"], ex["duration"],
                                                            str(work / "style.jpg"))

        step("IA estudando o exemplo e escrevendo o roteiro")
        plan = script.write_video(ex, segs, shots, sheets, style, cr["topic"] or "", int(cr["duration"] or 60))
        db.update("creations", cid, analysis=plan["example_analysis"])

        step("narrando e montando o vídeo")
        out = isolate.run(compose.render_creation, cid, _pick_shots(plan["scenes"], shots), plan["hook"],
                          cr["voice"] or TTS_VOICE)

        approval = db.setting("require_approval") == "1"
        clip_id = db.run(
            """INSERT INTO clips(source_id,creation_id,start,end,content_type,title,hook,voiceover,reason,
               caption_youtube,caption_tiktok,caption_instagram,hashtags,score,file,public_token,status,created_at)
               VALUES (NULL,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, out["duration"], plan["content_type"], plan["title"], plan["hook"], out["narration"],
             plan["example_analysis"], plan["caption_youtube"], plan["caption_tiktok"], plan["caption_instagram"],
             json.dumps(plan["hashtags"], ensure_ascii=False), plan["viral_score"], out["file"],
             secrets.token_urlsafe(16), "pending" if approval else "approved", db.now()))
        if not approval:
            worker.schedule_posts(clip_id)
        db.update("creations", cid, status="done", step=f"pronto: {out['duration']:.0f}s", clip_id=clip_id)
        ok = True
    finally:
        tidy(cid, keep_upload=not ok)


def run_next() -> bool:
    """Does one queued library download or one creation. Returns True if there was work."""
    item = db.one("SELECT * FROM library WHERE status='queued' ORDER BY id LIMIT 1")
    if item:
        process_library(item)
        return True
    cr = db.one("SELECT * FROM creations WHERE status='queued' ORDER BY id LIMIT 1")
    if cr:
        try:
            process_creation(cr)
        except Exception as e:
            traceback.print_exc()
            db.update("creations", cr["id"], status="error", error=str(e)[:1000])
        return True
    return False


def on_startup():
    db.run("UPDATE creations SET status='error', error=? WHERE status IN ('processing','uploading')",
           ("A criação foi interrompida porque o servidor reiniciou. Clique em Tentar de novo.",))
    db.run("UPDATE library SET status='queued' WHERE status='processing'")
    for w in STUDIO.glob("crt_*"):
        r = db.one("SELECT status FROM creations WHERE id=?", (int(w.name[4:]) if w.name[4:].isdigit() else -1,))
        if not r:
            shutil.rmtree(w, ignore_errors=True)
        elif r["status"] != "queued":
            tidy(int(w.name[4:]), keep_upload=r["status"] == "error")
