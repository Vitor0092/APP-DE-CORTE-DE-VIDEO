"""ffmpeg editor: vertical 9:16, blurred background, karaoke subtitles, hook text, voice-over."""
import subprocess
from pathlib import Path

from ..config import CLIPS
from . import tts

W, H = 1080, 1920

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,DejaVu Sans,78,&H0000F0FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,560,1
Style: Hook,DejaVu Sans,92,&H00FFFFFF,&H00FFFFFF,&H006E3DFF,&H006E3DFF,-1,0,0,0,100,100,0,0,3,18,0,8,60,60,230,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def build_ass(segments, start, end, hook, hook_until, words_per_line=3) -> str:
    words = [w for s in segments for w in s.get("words", []) if w["end"] > start and w["start"] < end]
    lines = []
    if hook:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(hook_until)},Hook,,0,0,0,,{_clean(hook)}")
    for i in range(0, len(words), words_per_line):
        chunk = words[i:i + words_per_line]
        cs, ce = chunk[0]["start"] - start, chunk[-1]["end"] - start
        text = ""
        for w in chunk:
            dur = max(1, int((w["end"] - w["start"]) * 100))
            text += f"{{\\k{dur}}}{_clean(w['word']).upper()} "
        lines.append(f"Dialogue: 0,{_ts(cs)},{_ts(ce + 0.05)},Sub,,0,0,0,,{text.strip()}")
    return ASS_HEADER + "\n".join(lines) + "\n"


def _probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def render_clip(clip_id: int, src: str, segments: list, start: float, end: float,
                hook: str = "", voiceover: str = "", use_voice: bool = True) -> str:
    workdir = CLIPS / f"clip_{clip_id}"
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "final.mp4"

    vo_path, vo_len = None, 0.0
    if use_voice and voiceover.strip():
        vo_path = str(workdir / "vo.mp3")
        try:
            tts.speak(voiceover, vo_path)
            vo_len = _probe_duration(vo_path)
        except Exception as e:  # no voice is better than no clip
            print(f"[tts] falhou, seguindo sem narração: {e}")
            vo_path = None

    hook_until = max(3.0, vo_len)
    (workdir / "subs.ass").write_text(build_ass(segments, start, end, hook, hook_until), encoding="utf-8")

    vf = (
        f"[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:4,eq=brightness=-0.15[bg];"
        f"[b]scale={W}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass=subs.ass[v]"
    )
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", src]
    if vo_path:
        cmd += ["-i", vo_path]
        af = (f";[0:a]volume='if(lt(t,{vo_len:.2f}),0.2,1)':eval=frame[oa];"
              f"[1:a]volume=1.6[vo];[oa][vo]amix=inputs=2:duration=first:normalize=0[aout]")
        amap = "[aout]"
    else:
        af, amap = "", "0:a?"
    cmd += [
        "-filter_complex", vf + af, "-map", "[v]", "-map", amap,
        "-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-r", "30", "-movflags", "+faststart", "final.mp4",
    ]
    res = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg falhou: " + res.stderr[-800:])
    return str(out)
