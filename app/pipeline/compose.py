"""Assembles a created video: AI narration per scene over the chosen footage, karaoke subtitles and on-screen
text, then joins the scenes. Scene parts go to the container's temporary disk, only the final video to /data."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import db
from ..config import CLIPS, TTS_VOICE
from . import tts
from .media import has_audio, probe_duration
from .render import ASS_HEADER, H, W, _chunks, _clean, _even_words, _ts

FPS = 30
VIDEO = ["-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS)]
AUDIO = ["-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]
STEREO = "aformat=sample_rates=44100:channel_layouts=stereo"


def _scene_ass(words: list, top_text: str, dur: float) -> str:
    lines = []
    if top_text.strip():
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(dur)},Hook,,0,0,0,,{_clean(top_text)}")
    lines += _chunks(words, 0.0, 3)
    return ASS_HEADER + "\n".join(lines) + "\n"


def _render_scene(i: int, sc: dict, top_text: str, tmp: Path, voice: str, audio_cache: dict) -> tuple[Path, float]:
    vo = tmp / f"vo_{i:02d}.mp3"
    try:
        words = tts.speak(sc["narration"], str(vo), voice=voice) or []
    except Exception as e:
        print(f"[tts] cena {i + 1}: {e}")
        raise RuntimeError("A voz de IA não respondeu. Tente de novo em alguns minutos.") from None
    vo_len = probe_duration(str(vo))
    if vo_len <= 0:
        raise RuntimeError("A voz de IA não gerou áudio. Tente de novo em alguns minutos.")
    words = words or _even_words(sc["narration"], vo_len)
    dur = max(1.5, round((vo_len + 0.25) * FPS) / FPS)

    src = sc["path"]
    src_len = sc.get("duration") or probe_duration(src)
    loop = src_len < dur + 0.3  # footage shorter than the line: loop it
    start = 0.0 if loop else max(0.0, min(sc["t"], src_len - dur - 0.2))
    if src not in audio_cache:
        audio_cache[src] = has_audio(src)
    (tmp / f"s_{i:02d}.ass").write_text(_scene_ass(words, top_text, dur), encoding="utf-8")

    vf = (f"[0:v]split[a][b];"
          f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
          f"scale={W}:{H},eq=brightness=-0.15[bg];"
          f"[b]scale={W}:-2[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass=s_{i:02d}.ass,fps={FPS},setsar=1[v]")
    if audio_cache[src]:  # game sound stays low under the narration
        af = (f";[0:a]volume=0.12,{STEREO}[ga];[1:a]volume=1.6,{STEREO},apad[vo];"
              f"[ga][vo]amix=inputs=2:duration=longest:normalize=0[aout]")
    else:
        af = f";[1:a]volume=1.6,{STEREO},apad[aout]"
    out = tmp / f"s_{i:02d}.mp4"
    cmd = (["ffmpeg", "-nostdin", "-y", "-threads", "2"] + (["-stream_loop", "-1"] if loop else [])
           + ["-ss", f"{start:.2f}", "-t", f"{dur + 0.5:.2f}", "-i", src, "-i", str(vo),
              "-filter_complex", vf + af, "-map", "[v]", "-map", "[aout]", "-t", f"{dur:.3f}"]
           + VIDEO + AUDIO + [out.name])
    res = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou na cena {i + 1}: " + res.stderr[-600:])
    return out, dur


def render_creation(creation_id: int, scenes: list[dict], hook: str, voice: str = TTS_VOICE) -> dict:
    """scenes: [{narration, on_screen_text, path, t, duration}]. Returns {file, duration, narration}."""
    workdir = CLIPS / f"crt_{creation_id}"
    workdir.mkdir(parents=True, exist_ok=True)
    final = workdir / "final.mp4"
    tmp = Path(tempfile.mkdtemp(prefix=f"crt{creation_id}_"))
    try:
        parts, total, cache = [], 0.0, {}
        for i, sc in enumerate(scenes):
            db.update("creations", creation_id, step=f"narrando e montando a cena {i + 1}/{len(scenes)}")
            top = hook if i == 0 and hook.strip() else sc.get("on_screen_text", "")
            part, dur = _render_scene(i, sc, top, tmp, voice, cache)
            parts.append(part)
            total += dur
        db.update("creations", creation_id, step="juntando as cenas")
        (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
        res = subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
                              "-c:v", "copy"] + AUDIO + ["-movflags", "+faststart", str(final)],
                             cwd=tmp, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError("ffmpeg falhou ao juntar as cenas: " + res.stderr[-600:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"file": str(final), "duration": round(total, 2),
            "narration": " ".join(s["narration"].strip() for s in scenes)}
