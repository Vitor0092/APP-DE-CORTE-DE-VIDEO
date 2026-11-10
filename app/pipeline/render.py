"""ffmpeg editor: vertical 9:16, blurred background, karaoke subtitles, hook text, voice-over."""
import subprocess

from ..config import CLIPS
from . import tts
from .media import has_audio, probe_duration

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


def _chunks(words, offset, words_per_line):
    lines = []
    for i in range(0, len(words), words_per_line):
        chunk = words[i:i + words_per_line]
        cs, ce = chunk[0]["start"] - offset, chunk[-1]["end"] - offset
        text = ""
        for w in chunk:
            dur = max(1, int((w["end"] - w["start"]) * 100))
            text += f"{{\\k{dur}}}{_clean(w['word']).upper()} "
        lines.append(f"Dialogue: 0,{_ts(cs)},{_ts(ce + 0.05)},Sub,,0,0,0,,{text.strip()}")
    return lines


def _even_words(text, total):
    """Fallback timings when the TTS gives no word boundaries: spread words evenly over the narration."""
    words = text.split()
    if not words or total <= 0:
        return []
    step = total / len(words)
    return [{"start": i * step, "end": (i + 1) * step, "word": w} for i, w in enumerate(words)]


def build_ass(segments, start, end, hook, hook_until, vo_words=None, vo_len=0.0, words_per_line=3) -> str:
    lines = []
    if hook:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(hook_until)},Hook,,0,0,0,,{_clean(hook)}")
    lines += _chunks(vo_words or [], 0.0, words_per_line)
    # Original speech is ducked under the narration, so its subtitles start after the narration ends.
    speech = [w for s in segments for w in s.get("words", [])
              if w["end"] > start and w["start"] < end and w["start"] - start >= vo_len - 0.05]
    lines += _chunks(speech, start, words_per_line)
    return ASS_HEADER + "\n".join(lines) + "\n"


def render_clip(clip_id: int, src: str, segments: list, start: float, end: float,
                hook: str = "", voiceover: str = "", use_voice: bool = True) -> str:
    workdir = CLIPS / f"clip_{clip_id}"
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "final.mp4"

    vo_path, vo_len, vo_words = None, 0.0, []
    if use_voice and voiceover.strip():
        vo_path = str(workdir / "vo.mp3")
        try:
            vo_words = tts.speak(voiceover, vo_path) or []
            vo_len = probe_duration(vo_path)
            if not vo_words:
                vo_words = _even_words(voiceover, vo_len)
        except Exception as e:  # no voice is better than no clip
            print(f"[tts] falhou, seguindo sem narração: {e}")
            vo_path, vo_len, vo_words = None, 0.0, []

    dur = max(0.5, end - start)
    hook_until = max(3.0, vo_len)
    (workdir / "subs.ass").write_text(build_ass(segments, start, end, hook, hook_until, vo_words, vo_len),
                                      encoding="utf-8")

    vf = (
        f"[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:4,eq=brightness=-0.15[bg];"
        f"[b]scale={W}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass=subs.ass[v]"
    )
    src_audio = has_audio(src)
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", src]
    if vo_path:
        cmd += ["-i", vo_path]
    if vo_path and src_audio:
        af = (f";[0:a]volume='if(lt(t,{vo_len:.2f}),0.2,1)':eval=frame[oa];"
              f"[1:a]volume=1.6[vo];[oa][vo]amix=inputs=2:duration=first:normalize=0[aout]")
    elif vo_path:  # silent video: narration only, padded to the clip length
        af = f";[1:a]volume=1.6,apad=whole_dur={dur:.2f}[aout]"
    elif src_audio:
        af = ";[0:a]anull[aout]"
    else:  # no audio at all: silent track, since the platforms expect one
        af = f";anullsrc=r=44100:cl=stereo,atrim=duration={dur:.2f}[aout]"
    cmd += [
        "-filter_complex", vf + af, "-map", "[v]", "-map", "[aout]", "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-r", "30", "-movflags", "+faststart", "final.mp4",
    ]
    res = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg falhou: " + res.stderr[-800:])
    return str(out)
