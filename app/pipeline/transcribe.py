"""Speech-to-text with word timestamps (faster-whisper, runs locally, free)."""
import gc
import subprocess

import numpy as np

from ..config import WHISPER_MODEL
from .media import has_audio, probe_duration

SAMPLE_RATE = 16000
CHUNK_SECONDS = 600


def _model():
    # Loaded per video and freed afterwards so rendering has the RAM back.
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=2)


def load_audio(path: str, start: float = 0.0, length: float | None = None) -> np.ndarray:
    """Decode with ffmpeg (avoids PyAV version issues inside faster-whisper)."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.2f}", "-i", path]
    if length:
        cmd += ["-t", f"{length:.2f}"]
    res = subprocess.run(cmd + ["-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"], capture_output=True)
    if res.returncode != 0 or (not res.stdout and start == 0):
        raise RuntimeError("Não consegui extrair o áudio do vídeo: " + res.stderr.decode(errors="ignore")[-300:])
    return np.frombuffer(res.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe(path: str) -> list[dict]:
    """Returns segments: [{start, end, text, words:[{start,end,word}]}]. Empty if the video has no audio.
    Audio is decoded in 10-minute pieces so long videos fit in little RAM."""
    if not has_audio(path):
        return []
    total = probe_duration(path) or CHUNK_SECONDS
    model = _model()
    try:
        out, t = [], 0.0
        while t < total:
            audio = load_audio(path, t, CHUNK_SECONDS)
            if not audio.size:
                break
            segments, _info = model.transcribe(audio, word_timestamps=True, vad_filter=True)
            for s in segments:
                out.append({
                    "start": round(t + s.start, 2), "end": round(t + s.end, 2), "text": s.text.strip(),
                    "words": [{"start": t + w.start, "end": t + w.end, "word": w.word} for w in (s.words or [])],
                })
            del audio
            t += CHUNK_SECONDS
        return out
    finally:
        del model
        gc.collect()
