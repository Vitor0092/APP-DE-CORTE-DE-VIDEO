"""Speech-to-text with word timestamps (faster-whisper, runs locally, free)."""
import gc
import subprocess

import numpy as np

from ..config import WHISPER_MODEL

SAMPLE_RATE = 16000


def _model():
    # Loaded per video and freed afterwards so rendering has the RAM back.
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=2)


def load_audio(path: str) -> np.ndarray:
    """Decode with ffmpeg (avoids PyAV version issues inside faster-whisper)."""
    res = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "s16le", "-"],
        capture_output=True,
    )
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError("Não consegui extrair o áudio do vídeo: " + res.stderr.decode(errors="ignore")[-300:])
    return np.frombuffer(res.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe(path: str) -> list[dict]:
    """Returns segments: [{start, end, text, words:[{start,end,word}]}]."""
    model = _model()
    try:
        segments, _info = model.transcribe(load_audio(path), word_timestamps=True, vad_filter=True)
        out = []
        for s in segments:
            out.append({
                "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                "words": [{"start": w.start, "end": w.end, "word": w.word} for w in (s.words or [])],
            })
        return out
    finally:
        del model
        gc.collect()
