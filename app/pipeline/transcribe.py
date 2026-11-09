"""Speech-to-text with word timestamps (faster-whisper, runs locally, free)."""
from functools import lru_cache

from ..config import WHISPER_MODEL


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8")


def transcribe(path: str) -> list[dict]:
    """Returns segments: [{start, end, text, words:[{start,end,word}]}]."""
    segments, _info = _model().transcribe(path, word_timestamps=True, vad_filter=True)
    out = []
    for s in segments:
        out.append({
            "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
            "words": [{"start": w.start, "end": w.end, "word": w.word} for w in (s.words or [])],
        })
    return out
